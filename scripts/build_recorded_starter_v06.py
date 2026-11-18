#!/usr/bin/env python3
"""Build/check twenty real development sources and unchanged recorded outputs.

Streams one parent archive record at a time; never opens a mutable Store or
requests network/model access. Selection is deliberately diagnostic, not a
representative sample or an independent interpretation evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.contextual import _criteria  # noqa: E402
from doxa.recorded_starter import MAX_COMPRESSED_BYTES, MAX_RAW_BYTES, canonical_hash, validate_example  # noqa: E402
from doxa.showcase_v3 import _parts, _validate_record, read_manifest  # noqa: E402

EXPECTED_PARENT_SHA256 = "783432b8430aeadc0bcce665e897bba33d8b9a14f718f70f0c5a988c1e884827"
EXPECTED_CORPUS_SHA256 = "6ea29f063abcdda5425d91277c9137cbb66f461fa2fff26f1f3543c51fb02ff3"
SEED = "doxa-v06-real-development-starter-20"


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_records(manifest, path, *, wanted_parts=None):
    for part in manifest["parts"]:
        if wanted_parts is not None and part["file"] not in wanted_parts:
            continue
        count = total = 0
        with gzip.open(path.parent / part["file"], "rb") as stream:
            while True:
                raw = stream.readline(32 * 1024 * 1024 + 1)
                if not raw:
                    break
                if len(raw) > 32 * 1024 * 1024:
                    raise ValueError("A parent archive record exceeded the bounded reader")
                count += 1
                total += len(raw)
                yield json.loads(raw), {"part": part["file"], "line": count,
                                        "part_sha256": part["sha256"], "raw_line_sha256": hashlib.sha256(raw).hexdigest()}
        if count != part["documents"] or total != part["uncompressed_bytes"]:
            raise ValueError("A frozen parent archive part changed size or source count")


def build(root=ROOT):
    root = Path(root)
    corpus = root / "corpus/news"
    manifest, path, parent_hash = read_manifest(root / "benchmarks/v3-news/showcase.json")
    if parent_hash != EXPECTED_PARENT_SHA256 or file_hash(corpus / "manifest.jsonl") != EXPECTED_CORPUS_SHA256:
        raise ValueError("The original recorded parent/canonical corpus identity changed")
    _parts(manifest, path)  # SHA-256 every compressed part before using it.
    excluded_sources, excluded_stories = set(), set()
    with (corpus / "holdout.jsonl").open() as stream:
        for line in stream:
            item = json.loads(line)
            excluded_sources.add(item["id"])
            excluded_stories.add(item["story_group_id"])
    scaffold_path = root / "benchmarks/evaluation-v04/scaffold.json"
    scaffold = json.loads(scaffold_path.read_text())
    for case in scaffold["cases"]:
        if case["split"] == "heldout":
            excluded_sources.add(case["source"]["source_id"])
            excluded_stories.add(case["story_group"])
    eligible = {}
    with (corpus / "manifest.jsonl").open() as stream:
        for line in stream:
            item = json.loads(line)
            if (item["split"] == "development" and item["id"] not in excluded_sources
                    and item["story_group_id"] not in excluded_stories and item["license"] == "CC-BY-3.0"
                    and item["synthetic"] is False and item["body_characters"] <= 6000):
                eligible[item["id"]] = {key: item[key] for key in ("id", "story_group_id", "published_at", "content_sha256", "metadata_path", "text_path", "txt_sha256")}
    candidates = []
    for record, locator in archive_records(manifest, path):
        source = eligible.get(record["source_id"])
        if not source or not record["assessments"] or len(record["assessments"]) > 57:
            continue
        outcomes = {item["result"] for item in record["assessments"]}
        features = {"year:" + source["published_at"][:4]} | {"outcome:" + outcome for outcome in outcomes}
        for assessment in record["assessments"]:
            if assessment["criterion_kind"] in {"indicator", "boundary"} and assessment["result"] in {"meets_criterion", "uncertain"}:
                features.add("kind_outcome:" + assessment["criterion_kind"] + "/" + assessment["result"])
        if any(status["status"] != "accepted" for status in record["unit_statuses"]):
            features.add("partial_source")
        candidates.append({"source_id": record["source_id"], "document_id": record["document_id"],
                           "story_group_id": source["story_group_id"], "features": features,
                           "priority": canonical_hash([SEED, record["source_id"]]), "locator": locator,
                           "record_sha256": canonical_hash(record)})
    if len(candidates) < 20:
        raise ValueError("Too few licensed development sources for the recorded starter")
    desired = {"outcome:" + item for item in ("meets_criterion", "does_not_meet", "not_applicable", "uncertain")}
    desired |= {"year:" + str(year) for year in range(2020, 2026)}
    desired |= {feature for candidate in candidates for feature in candidate["features"] if feature.startswith("kind_outcome:")}
    if any("partial_source" in candidate["features"] for candidate in candidates):
        desired.add("partial_source")
    candidates.sort(key=lambda item: item["priority"])
    selected, selected_stories, covered = [], set(), set()
    while len(selected) < 20:
        available = [item for item in candidates if item["story_group_id"] not in selected_stories]
        choice = min(available, key=lambda item: (-len(item["features"] & (desired - covered)), item["priority"]))
        selected.append(choice)
        selected_stories.add(choice["story_group_id"])
        covered |= choice["features"]
    if not desired <= covered:
        raise ValueError("Recorded starter cannot preserve every declared diagnostic feature")
    selected_by_source = {item["source_id"]: item for item in selected}
    records = {}
    for record, locator in archive_records(manifest, path, wanted_parts={item["locator"]["part"] for item in selected}):
        choice = selected_by_source.get(record["source_id"])
        if choice:
            if choice["locator"] != locator or canonical_hash(record) != choice["record_sha256"]:
                raise ValueError("The selected original record changed between read-only passes")
            records[record["source_id"]] = record
    _, criteria = _criteria(manifest["run"]["config"]["lens_snapshots"], manifest["run"]["config"])
    documents, audits = [], []
    for choice in selected:
        record = records[choice["source_id"]]
        entry = eligible[choice["source_id"]]
        source = json.loads((corpus / entry["metadata_path"]).read_text())
        if file_hash(corpus / entry["text_path"]) != source["txt_sha256"]:
            raise ValueError("A selected attributed text snapshot changed")
        if source["content_sha256"] != record["sha256"]:
            raise ValueError("Source and original model record hashes differ")
        normalized = {**source, "id": record["document_id"], "sha256": record["sha256"],
                      "metadata": {**source, "source_id": choice["source_id"]}, "blocks_key": "read-only-memory"}
        class SourceReader:
            def get_json(self, _key):
                return source["blocks"]
        audit = _validate_record(SourceReader(), record, normalized,
                                 {"id": record["document_id"], "sha256": record["sha256"]}, manifest["run"], criteria)
        audits.append(audit)
        metadata = {key: value for key, value in source.items() if key != "blocks"}
        documents.append({"id": record["document_id"], "source_id": source["id"], "sha256": record["sha256"],
                          **{key: source.get(key) for key in ("title", "author", "authors", "translators", "source_url", "published_at", "license", "license_url", "attribution", "word_count")},
                          "blocks": source["blocks"], "metadata": metadata,
                          "source_snapshot_sha256": canonical_hash(source), "archive_record_sha256": canonical_hash(record),
                          "parent_locator": choice["locator"]})
    run = manifest["run"]
    config = run["config"]
    counts, states = Counter(), Counter()
    for audit in audits:
        counts.update(audit["outcomes"])
        states.update(audit["unit_states"])
    payload = {"schema_version": "doxa-recorded-starter-1", "id": "globalvoices-development-recorded-20-v06",
               "mode": "read_only_recorded_exploration", "title": "Twenty real Global Voices sources · recorded model readings",
               "data_origin": "licensed_real_public_sources_with_recorded_model_outputs",
               "fresh_inference_requests": 0, "network_requests": 0, "mutable_workspace_installed": False,
               "independent_human_reference_labels": False, "interpretation_accuracy": None,
               "parent": {"run_id": run["id"], "project_id": run["project_id"], "status": run["status"],
                          "provider": config["provider"], "model": config["model"], "generated_at": manifest["generated_at"],
                          "package_sha256": parent_hash, "canonical_manifest_sha256": EXPECTED_CORPUS_SHA256,
                          "config_sha256": canonical_hash(config), "progress": run["progress"],
                          "counts": manifest["counts"], "unit_states": manifest["unit_states"], "outcomes": manifest["outcomes"],
                          "independent_human_reference_labels": False, "interpretation_accuracy": None},
               "method": {key: config.get(key) for key in ("provider", "model", "question", "persona", "model_revision", "provider_preflight", "methodology_version", "pipeline_version", "unit_builder_version", "unit_max_chars", "context_sentences", "lens_ids", "lens_snapshots", "lens_snapshots_sha256", "quantization")},
               "selection": {"seed": SEED, "count": 20, "purpose": "diagnostic exploration, not prevalence or accuracy estimation",
                             "eligibility": "development split, licensed CC BY 3.0, unique story groups, at most 6,000 body characters and 57 retained decisions per source; excludes canonical holdout and locked evaluation sources/story groups",
                             "selection_uses_recorded_outcomes": True, "eligible_candidate_sources": len(candidates),
                             "years": sorted(feature.removeprefix("year:") for feature in covered if feature.startswith("year:")),
                             "retained_partial_source": "partial_source" in covered,
                             "locked_scaffold_sha256": file_hash(scaffold_path), "holdout_sha256": file_hash(corpus / "holdout.jsonl"),
                             "counts": {"documents": 20, "assessments": sum(counts.values()),
                                        "exact_references": sum(audit["audited_references"] for audit in audits),
                                        "source_characters": sum(audit["source_characters"] for audit in audits),
                                        "words": sum(audit["words"] for audit in audits), "outcomes": dict(counts), "unit_states": dict(states)}},
               "documents": documents, "records": [records[item["source_id"]] for item in selected],
               "notice": "Previously computed interpretations, not new analysis or independent human labels. The 5,000-source parent is partial with 79 missing decisions. This deliberately selected development subset illustrates outcomes and incomplete work; it is not representative. Source credits, translations, CC BY 3.0 notices, exact text and original model records are retained. No mutable workspace or new run is created."}
    validate_example(payload)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    # GzipFile fixes the header OS byte across Python versions; gzip.compress
    # used a platform-dependent OS byte for mtime=0 on Python 3.11/3.12.
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", compresslevel=9, mtime=0) as stream:
        stream.write(raw)
    compressed = buffer.getvalue()
    if len(raw) > MAX_RAW_BYTES or len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValueError("The recorded starter exceeded its packaged bounds")
    package = {"schema_version": "doxa-recorded-starter-manifest-1", "artifact": {"file": "recorded-starter.json.gz", "bytes": len(compressed),
               "sha256": hashlib.sha256(compressed).hexdigest(), "uncompressed_bytes": len(raw), "uncompressed_sha256": hashlib.sha256(raw).hexdigest()},
               "builder": "scripts/build_recorded_starter_v06.py", "builder_sha256": file_hash(Path(__file__)),
               "parent_package_sha256": parent_hash, "canonical_manifest_sha256": EXPECTED_CORPUS_SHA256,
               "selection": payload["selection"], "fresh_inference_requests": 0, "network_requests": 0}
    return compressed, package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the reproducible packaged assets; default only compares current assets")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    compressed, manifest = build(args.root)
    directory = args.root / "doxa/data"
    manifest_raw = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    if args.write:
        directory.mkdir(exist_ok=True)
        (directory / "recorded-starter.json.gz").write_bytes(compressed)
        (directory / "recorded-starter.manifest.json").write_bytes(manifest_raw)
    else:
        if (directory / "recorded-starter.json.gz").read_bytes() != compressed or (directory / "recorded-starter.manifest.json").read_bytes() != manifest_raw:
            raise ValueError("Recorded starter differs from reproducible source inputs")
    print(json.dumps({"status": "written" if args.write else "verified", **manifest["artifact"],
                      "counts": manifest["selection"]["counts"], "fresh_inference_requests": 0, "network_requests": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
