#!/usr/bin/env python3
"""Prepare small public-site facts from audited, frozen local artifacts; no deploy."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_walkthrough import audit_snapshot  # noqa: E402


def prepare(institutional_snapshot: Path, output: Path, news_summary: Path | None = None) -> dict:
    with gzip.open(institutional_snapshot, "rt", encoding="utf-8") as stream:
        frozen = json.load(stream)
    audit_snapshot(frozen)
    run, scope = frozen["run"], frozen["run_coverage"]
    package = json.loads((ROOT / "benchmarks/v3-institutions/showcase.json").read_text())
    if package["run"]["id"] != run["id"] or package["counts"]["assessments"] != scope["retained_criterion_decisions"]:
        raise ValueError("Institutional package and annotated guide must describe the same final recorded run")
    counts = frozen["decision_counts"]
    total_units = sum(row["count"] for row in frozen["unit_states"])
    accepted = sum(row["count"] for row in frozen["unit_states"] if row["status"] == "accepted")
    if sum(row["count"] for row in counts) != scope["retained_criterion_decisions"]:
        raise ValueError("Recorded assessment counts do not match the frozen scope")
    item = frozen["assessments"][0]
    source = next(doc for doc in frozen["documents"] if doc["id"] == item["document_id"])
    institutional = {"generated_at": frozen["generated_at"], "package_generated_at": package["generated_at"],
        "run_id": run["id"], "status": run["status"], "documents": scope["selected_documents"],
        "fully_assessed_documents": scope["fully_assessed_documents"], "units_done": accepted, "units_total": total_units,
        "coverage_percent": round(100 * accepted / total_units, 1), "criterion_decisions_retained": scope["retained_criterion_decisions"],
        "criterion_decisions_requested": scope["requested_criterion_decisions"], "criterion_decisions_unresolved": scope["unresolved_criterion_decisions"],
        "decision_counts": counts, "model": run["config"]["model"], "provider": run["config"]["provider"],
        "prompt_version": run["model_profile"].get("prompt_version"), "methodology_version": run["config"]["methodology_version"],
        "citation_audit": package["citation_audit"], "independent_human_labels": False, "interpretation_accuracy": None,
        "provider_cost_usd": run.get("usage", {}).get("cost_usd"), "budget_accounted_usd": run.get("spent_usd"),
        "source_summary": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/benchmarks/v3-institutions/showcase.json",
        "example": {"id": item["id"], "quote": item["quote"], "title": item.get("title"), "criterion_id": item["criterion_id"],
            "criterion_kind": item["criterion_kind"], "result": item["result"], "attribution": item.get("attribution"),
            "document_stance": item.get("document_stance"), "review_status": item.get("review_status", "unreviewed"),
            "source_title": source["title"], "source_url": source["source_url"], "author": source["author"],
            "locator": item.get("locator"), "block_id": item["block_id"], "model_reading": item.get("explanation")}}
    collection = json.loads((ROOT / "corpus/news/collection.json").read_text())
    audit = json.loads((ROOT / "corpus/news/audit.json").read_text())
    source_audit = json.loads((ROOT / "corpus/news/source-audit.json").read_text())
    if collection["status"] != "complete" or audit["status"] != "pass" or source_audit["status"] != "pass":
        raise ValueError("Publish collection facts only after all source audits pass")
    authors, translated, actual_count = set(), 0, 0
    with (ROOT / "corpus/news/manifest.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            actual_count += 1
            authors.update(person["name"] for person in row.get("authors", []) if person.get("name"))
            translated += bool(row.get("translators"))
    if actual_count != collection["article_count"] or actual_count != audit["article_count"]:
        raise ValueError("Frozen news manifest count differs from its audited collection")
    news_summary = news_summary or ROOT / "benchmarks/v3-news-final-summary.json"
    news_bytes = news_summary.read_bytes()
    saved_news = json.loads(news_bytes)
    if saved_news["collection"]["documents"] != actual_count:
        raise ValueError("News analysis capture must describe the audited source count")
    units_total = sum(row["count"] for row in saved_news["unit_states"])
    units_done = sum(row["count"] for row in saved_news["unit_states"] if row["status"] == "accepted")
    news = {"publisher": collection["publisher"], "documents": actual_count, "words": audit["word_count"],
            "credited_author_names": len(authors), "translated_articles": translated, "publication_months": len(collection["monthly_allocation"]),
            "frame_start": collection["frame_start"], "frame_end": collection["frame_end"], "frozen_at": collection["frozen_at"],
            "holdout_groups": collection["holdout_count"], "source_audit_checked": source_audit["checked"],
            "source_audit_matched": source_audit["matched"], "license": "CC BY 3.0", "selection_notice": collection["selection_notice"],
            "source_manifest": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/docs/news-corpus.md",
            "analysis_snapshot": {"generated_at": saved_news["generated_at"], "run_id": saved_news["run_id"], "status": saved_news["status"],
                "summary_sha256": hashlib.sha256(news_bytes).hexdigest(),
                "selected_documents": saved_news["collection"]["documents"], "units_done": units_done, "units_total": units_total,
                "coverage_percent": round(100 * units_done / units_total, 1), "assessments_retained": sum(row["count"] for row in saved_news["decision_counts"]),
                "fully_assessed_documents": saved_news["progress"].get("documents_complete"),
                "criterion_decisions_requested": units_total * sum(len(pack.get("criteria", [])) for pack in saved_news.get("config", {}).get("lens_snapshots", [])),
                "budget_accounted_usd": saved_news.get("budget_accounted_usd"),
                "provider_cost_usd": saved_news.get("usage", {}).get("cost_usd"),
                "independent_human_labels": False, "interpretation_accuracy": None,
                "source_summary": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/" + news_summary.resolve().relative_to(ROOT).as_posix()}}
    captured = news["analysis_snapshot"]
    captured["criterion_decisions_unresolved"] = max(0, captured["criterion_decisions_requested"] - captured["assessments_retained"])
    dashboard_path = ROOT / "examples/news-corpus-dashboard.manifest.json"
    if dashboard_path.exists():
        dashboard = json.loads(dashboard_path.read_text())
        if dashboard["run_id"] != captured["run_id"] or dashboard["counts"]["documents"] != actual_count or dashboard["counts"]["assessments"] != captured["assessments_retained"]:
            raise ValueError("The dashboard and dated news summary must describe the same final recorded selection")
        files = []
        for row in dashboard["files"]:
            if Path(row["file"]).name != row["file"]:
                raise ValueError("Dashboard artifact names must be simple local files")
            path = dashboard_path.parent / row["file"]
            value = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(chunk)
            if path.stat().st_size != row["bytes"] or value.hexdigest() != row["sha256"]:
                raise ValueError("Dashboard artifact checksum differs from its manifest")
            files.append({**row, "url": "https://github.com/sargonxg/DOXA-by-TACITUS/raw/refs/heads/main/examples/" + row["file"]})
        news["dashboard_exports"] = {"run_id": dashboard["run_id"], "run_status": dashboard["run_status"],
            "counts": dashboard["counts"], "human_appendix": dashboard["human_appendix"], "files": files,
            "manifest_url": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/examples/news-corpus-dashboard.manifest.json",
            "receipt_url": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/examples/news-corpus-dashboard.receipt.json"}
    result = {"schema_version": "3.0", "institutional": institutional, "news": news,
              "legacy_reference": {"method_version": "legacy-v1.2", "findings_retained": 1071, "coverage_percent": 64.3,
                "source_summary": "https://github.com/sargonxg/DOXA-by-TACITUS/blob/main/benchmarks/cloud-gemma4-summary.json"},
              "notice": "Frozen recorded artifacts, not live telemetry. Processing/source-anchor checks do not establish interpretation accuracy. News source collection and model-analysis coverage are different facts."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--institutional-snapshot", type=Path, default=ROOT / "examples/institutional-context-commentary.snapshot.json.gz")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--news-summary", type=Path, default=ROOT / "benchmarks/v3-news-final-summary.json")
    args = parser.parse_args()
    facts = prepare(args.institutional_snapshot, args.output, args.news_summary)
    print(json.dumps({"schema": facts["schema_version"], "institutional_units": [facts["institutional"]["units_done"], facts["institutional"]["units_total"]],
                      "news_sources": facts["news"]["documents"], "saved_news_units": [facts["news"]["analysis_snapshot"]["units_done"], facts["news"]["analysis_snapshot"]["units_total"]]}))
