#!/usr/bin/env python3
"""Audit retained CPU-study inputs/citations offline; never calls a model."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doxa.contextual import RESULT_CODES, SYSTEM, _criteria, _wire_unit, build_units  # noqa: E402
from doxa.lineage import content_sha256  # noqa: E402
from doxa.models import assessment_schema  # noqa: E402


def audit(summary_path: Path):
    summary = json.loads(summary_path.read_text())
    if summary["status"] == "running" or len(summary["profiles"]) != 2:
        raise ValueError("The two-profile study is not terminal; no final audit was written")
    sources = {}
    units = {}
    for source in summary["sources"]:
        document = json.loads((ROOT / "corpus/news/metadata" / (source["source_id"] + ".json")).read_text())
        if content_sha256(document["blocks"]) != source["source_hash"]:
            raise ValueError("Canonical study source blocks changed")
        document.update({"sha256": document["content_sha256"],
            "source_version_id": hashlib.sha256(f"{document['id']}:{document['content_sha256']}".encode()).hexdigest()[:32]})
        sources[document["id"]] = document
        units[document["id"]] = {unit["id"]: unit for unit in build_units(document, max_unit_chars=1200, context_sentences=2)}
    profiles, all_decisions = [], []
    for profile in summary["profiles"]:
        path = ROOT / profile["results_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != profile["results_sha256"]:
            raise ValueError("Retained study result checksum differs")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            full = json.load(stream)
        checked_requests, references, assessments = set(), 0, 0
        unit_statuses, errors, decisions = {}, {}, {}
        config = profile["method"]["settings"]
        _, criteria = _criteria(config["lens_ids"], config)
        for article in full["articles"]:
            document = sources[article["source_id"]]
            blocks = {block["id"]: block["text"] for block in document["blocks"]}
            for result in article["unit_results"]:
                for status in result["unit_statuses"]:
                    unit_statuses[status["unit_id"]] = status["status"]
                for error in result["errors"]:
                    errors[error["code"]] = errors.get(error["code"], 0) + 1
                for assessment in result["assessments"]:
                    assessments += 1
                    if blocks[assessment["block_id"]][assessment["start"]:assessment["end"]] != assessment["quote"]:
                        raise ValueError("Retained assessment primary quote mismatch")
                    for ref in assessment["evidence_refs"]:
                        if blocks[ref["block_id"]][ref["start"]:ref["end"]] != ref["quote"]:
                            raise ValueError("Retained sentence reference mismatch")
                        references += 1
                    decisions[(document["id"], assessment["unit_id"], assessment["criterion_key"])] = assessment
                for entry in result["attempt_journal"]:
                    batch = [units[document["id"]][identity] for identity in entry["unit_ids"]]
                    keys = entry["criterion_keys"]
                    payload = {"document": {key: document.get(key) for key in ("id", "title", "author", "institution", "document_type", "published_at")},
                        "phase": entry["phase"], "question": config["question"], "reviewer_perspective": config["persona"],
                        "criteria": [criteria[key] for key in keys], "criterion_order": keys, "result_codes": RESULT_CODES,
                        "units": [_wire_unit(unit) for unit in batch], "approved_examples": []}
                    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
                    ids = list(dict.fromkeys(sentence["id"] for unit in batch for sentence in [*unit["context_before"], *unit["sentences"], *unit["context_after"]]))
                    schema = assessment_schema([unit["id"] for unit in batch], keys, ids, phase=entry["phase"])
                    if content_sha256(messages) != entry["messages_sha256"] or content_sha256(schema) != entry["schema_sha256"]:
                        raise ValueError("Final source no longer reproduces retained observed message/schema hashes")
                    checked_requests.add(entry["request_id"])
        counts = {status: sum(value == status for value in unit_statuses.values()) for status in ("accepted", "partial", "failed", "pending")}
        counts["unprocessed_or_unretained"] = summary["selected_source_units"] - len(unit_statuses)
        profiles.append({"model": profile["model"], "status": profile["status"],
            "selected_articles": summary["selected_articles"], "whole_articles_complete": profile["complete_articles"],
            "selected_units": summary["selected_source_units"], "unit_counts": counts,
            "retained_assessments": assessments, "exact_sentence_references_checked": references,
            "retained_request_messages_and_schemas_reproduced": len(checked_requests), "source_anchor_integrity_passed": True,
            "error_codes": errors, "interrupted_inflight_observations": profile.get("interrupted_inflight_observations"),
            "operating_cap_minutes": 30, "elapsed_seconds": profile["elapsed_seconds"],
            "peak_sampled_driver_plus_ollama_rss_bytes": profile["peak_sampled_combined_rss_bytes"],
            "runtime_gpu_bytes": [item.get("size_vram") for item in profile.get("runtime_ps", {}).get("models", [])],
            "model_digest": profile["model_profile"]["revision"], "quantization": profile["model_profile"]["quantization"],
            "upstream_reference": profile["upstream_reference"]})
        all_decisions.append(decisions)
    left, right = all_decisions
    paired = set(left) & set(right)
    result = {"schema_version": "0.4", "kind": "offline_retained_input_and_anchor_audit",
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(), "model_calls": 0,
        "profiles": profiles, "shared_retained_decisions": len(paired),
        "shared_retained_result_differences": sum(left[key]["result"] != right[key]["result"] for key in paired),
        "shared_retained_attribution_differences": sum(left[key]["attribution"] != right[key]["attribution"] for key in paired),
        "interpretation_accuracy": None, "independent_reference_labels": False, "winner": None,
        "quality_promotion_recommended": False,
        "notice": "Exact retained source citations and request hashes establish integrity, not semantic correctness. Paired cells exclude missing/invalid outcomes. Shared system load and bounded partial processing prevent causal model-speed or representative accuracy claims. In-flight observations absent at interruption remain unrecorded.",
        "reproduce": ".venv/bin/python -B scripts/audit_local_study_v04.py"}
    return result


if __name__ == "__main__":
    result = audit(ROOT / "benchmarks/v04-cpu-study.json")
    target = ROOT / "benchmarks/v04-cpu-study-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"profiles": len(result["profiles"]), "shared_retained_decisions": result["shared_retained_decisions"], "model_calls": 0}))
