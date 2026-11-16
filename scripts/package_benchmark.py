#!/usr/bin/env python3
"""Package a finished real run and its honest summary; never invent scores."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def package(source: Path, output_dir: Path, name: str, showcase: Path | None = None) -> dict:
    snapshot = json.loads(source.read_text())
    runs = snapshot.get("runs", [])
    if len(runs) != 1 or runs[0]["status"] not in {"completed", "partial"}:
        raise ValueError("Only completed/partial finished runs may be packaged")
    documents = snapshot["documents"]
    source_map = {doc["id"]: {block["id"]: block["text"] for block in doc["blocks"]} for doc in documents}
    for finding in snapshot["findings"]:
        text = source_map[finding["document_id"]][finding["block_id"]]
        if text[finding["start"]:finding["end"]] != finding["quote"]:
            raise ValueError("A finding does not resolve exactly to the source")
    detail = snapshot["metrics"]["coverage_detail"]
    if showcase and (len(documents) != 20 or detail.get("sampled")):
        raise ValueError("Only the complete twenty-source selection can become the showcase")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"{name}.json.gz"
    artifact.write_bytes(compressed)
    run = runs[0]
    summary = {
        "schema_version": "1.0", "name": name, "generated_at": snapshot.get("generated_at"),
        "status": run["status"], "provider": run["config"]["provider"],
        "model": run.get("model_profile", {}).get("model") or run["config"].get("model"),
        "model_profile": run.get("model_profile", {}), "documents": len(documents),
        "words_in_selected_input": sum(doc["word_count"] for doc in documents),
        "findings_retained": len(snapshot["findings"]), "source_coverage": detail,
        "usage": run.get("usage", {}), "conservative_budget_accounted_usd": run.get("spent_usd", 0),
        "wall_seconds": snapshot["metrics"].get("wall_seconds"),
        "benchmark_driver_peak_rss_bytes": snapshot["metrics"].get("peak_process_rss_bytes"),
        "hardware": snapshot.get("benchmark", {}).get("hardware"),
        "error_count": len(snapshot.get("benchmark", {}).get("errors", [])),
        "citation_audit": snapshot.get("benchmark", {}).get("citation_audit"),
        "interpretation_accuracy": None, "independent_human_labels": False,
        "artifact": artifact.name, "uncompressed_sha256": hashlib.sha256(payload).hexdigest(),
        "notice": "Exact citation validation is a software gate, not an interpretation-accuracy score. Incomplete/sampled coverage remains explicit. Local compute and cloud platform charges are distinct from token-cost estimates.",
    }
    (output_dir / f"{name}-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    if showcase:
        showcase.parent.mkdir(parents=True, exist_ok=True)
        showcase.write_bytes(compressed)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks"))
    parser.add_argument("--showcase", type=Path)
    args = parser.parse_args()
    if not args.name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in args.name):
        parser.error("Use a simple artifact name")
    summary = package(args.source, args.output_dir, args.name, args.showcase)
    print(json.dumps({key: summary[key] for key in ["status", "provider", "documents", "findings_retained", "artifact", "error_count"]}, indent=2))


if __name__ == "__main__":
    main()
