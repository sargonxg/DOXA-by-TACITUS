#!/usr/bin/env python3
"""Stream a persisted v3 run into source-checked, bounded gzip JSONL parts."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.store import Store, utcnow  # noqa: E402

MAX_PART_BYTES = 49 * 1024 * 1024


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_run(row: dict) -> dict:
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(result.pop(key) or "{}")
    return result


def _record(store: Store, run: dict, document: dict) -> tuple[dict, dict]:
    metadata = json.loads(document.get("metadata_json") or "{}")
    blocks = {block["id"]: block["text"] for block in store.get_json(document["blocks_key"])}
    assessments = []
    audited = 0
    for row in store.query("SELECT * FROM v3_assessments WHERE run_id=? AND document_id=? ORDER BY unit_id,lens_id,criterion_id,id", [run["id"], document["id"]]):
        item = json.loads(row["payload_json"])
        refs = item.get("evidence_refs") or []
        if not refs:
            raise ValueError("A contextual assessment has no source evidence references")
        for reference in refs:
            text = blocks.get(reference["block_id"])
            start, end = reference.get("start"), reference.get("end")
            if text is None or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text) or (reference.get("quote") is not None and text[start:end] != reference["quote"]):
                raise ValueError("A contextual assessment failed exact source quotation validation")
            # Compact negative rows persist identifiers/offsets instead of
            # duplicating source prose. Rehydrate quotations from originals.
            reference["quote"] = text[start:end]
            audited += 1
        item["evidence_refs"] = refs
        if not item.get("quote"):
            item["quote"] = refs[0]["quote"]
        for key in ["block_id", "start", "end"]:
            item.setdefault(key, refs[0][key])
        if item.get("result") not in {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}:
            raise ValueError("Legacy or malformed outcomes cannot be packaged as contextual assessments")
        if item.get("criterion_kind") not in {"indicator", "boundary", "descriptive"}:
            raise ValueError("Assessment has no canonical criterion kind")
        original_id = item.get("id")
        # These are identity fields, not a reinterpretation of model outcomes.
        for key in ["id", "project_id", "run_id", "document_id", "unit_id", "lens_id", "criterion_id"]:
            item[key] = row[key]
        if original_id != item["id"]:
            item["source_assessment_id"] = original_id
        correction = json.loads(row.get("review_json") or "{}")
        if correction:
            item["review_overlay"] = correction
        item["review_status"] = row.get("review_status", "unreviewed")
        item["review_note"] = row.get("review_note", "")
        assessments.append(item)
    statuses = []
    for row in store.query("SELECT * FROM v3_unit_statuses WHERE run_id=? AND document_id=? ORDER BY unit_id", [run["id"], document["id"]]):
        status = json.loads(row["payload_json"] or "{}")
        status.update({"unit_id": row["unit_id"], "status": row["status"], "characters": row["characters"]})
        statuses.append(status)
    source_id = metadata.get("source_id") or document["id"]
    return {"source_id": source_id, "sha256": document["sha256"], "assessments": assessments, "unit_statuses": statuses}, {
        "source_characters": sum(len(text) for text in blocks.values()), "words": document.get("word_count", 0),
        "assessments": len(assessments), "audited_references": audited,
        "unit_states": dict(Counter(status["status"] for status in statuses)),
        "outcomes": dict(Counter(item["result"] for item in assessments)),
    }


def package_run(store: Store, run_id: str, output_dir: Path, *, part_documents: int = 100,
                max_part_bytes: int = MAX_PART_BYTES, benchmark: dict | None = None) -> dict:
    """Keep only one document's assessment payloads in memory at a time.

    The manifest is published last. Selected but unprocessed documents remain in
    the archive with empty results; an incomplete run never becomes completed.
    """
    if not 1 <= part_documents <= 100 or not 1024 <= max_part_bytes < 50 * 1024 * 1024:
        raise ValueError("Use 1–100 documents per part and a part limit below 50 MiB")
    row = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    if row is None:
        raise ValueError("Run not found")
    run = unpack_run(row)
    if str(run.get("config", {}).get("methodology_version")) != "3.0":
        raise ValueError("Only methodology version 3.0 runs can use this archive format")
    if run["status"] in {"queued", "running", "importing"}:
        raise ValueError("Wait for leased work to settle before packaging a run")
    snapshots = run.get("config", {}).get("source_snapshot") or []
    if not snapshots:
        raise ValueError("Run has no frozen source selection")
    if len({item["id"] for item in snapshots}) != len(snapshots):
        raise ValueError("Run source selection contains duplicates")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {"documents": 0, "assessments": 0, "audited_references": 0, "source_characters": 0, "words": 0}
    unit_states, outcomes, parts = Counter(), Counter(), []
    with tempfile.TemporaryDirectory(prefix=".v3-package-", dir=output_dir) as staging:
        staging_dir = Path(staging)
        raw = compressor = path = None
        part_count = part_raw_bytes = 0

        def close_part():
            nonlocal raw, compressor, path, part_count, part_raw_bytes
            if compressor is None:
                return
            compressor.close()
            raw.close()
            size = path.stat().st_size
            if size >= max_part_bytes:
                raise ValueError("Compressed part exceeds the configured size limit; reduce part_documents")
            parts.append({"file": path.name, "sha256": file_hash(path), "bytes": size,
                          "documents": part_count, "uncompressed_bytes": part_raw_bytes})
            raw = compressor = path = None
            part_count = part_raw_bytes = 0

        for source in snapshots:
            document = store.one("SELECT * FROM documents WHERE id=? AND project_id=?", [source["id"], run["project_id"]])
            if document is None or document["sha256"] != source["sha256"]:
                raise ValueError("A selected source changed or is unavailable")
            record, stats = _record(store, run, document)
            line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            # Raw-size rotation bounds incompressible input before the hard
            # compressed-size check. A large single record gets its own part.
            if compressor is not None and (part_count >= part_documents or part_raw_bytes + len(line) > max_part_bytes * 0.8):
                close_part()
            if compressor is None:
                path = staging_dir / f"assessments-part-{len(parts) + 1:04d}.jsonl.gz"
                raw = path.open("wb")
                compressor = gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0)
            compressor.write(line)
            part_count += 1
            part_raw_bytes += len(line)
            counts["documents"] += 1
            for key in ["assessments", "audited_references", "source_characters", "words"]:
                counts[key] += stats[key]
            unit_states.update(stats["unit_states"])
            outcomes.update(stats["outcomes"])
        close_part()
        manifest = {"schema_version": "3.0", "format": "doxa-contextual-jsonl-parts", "generated_at": utcnow(),
                    "run": run, "parts": parts, "counts": counts, "unit_states": dict(unit_states), "outcomes": dict(outcomes),
                    "benchmark": benchmark or {}, "citation_audit": {"valid": True, "references": counts["audited_references"]},
                    "interpretation_accuracy": None, "independent_human_labels": False,
                    "notice": "Exact source anchoring is a software check, not interpretation accuracy. Run state, failed/pending units, historical usage and selection scope are preserved. Whole selected documents are archived; no passage sampling is performed."}
        for part in parts:
            shutil.move(str(staging_dir / part["file"]), output_dir / part["file"])
        temporary = output_dir / ".showcase.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output_dir / "showcase.json")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--part-documents", type=int, default=100)
    args = parser.parse_args(argv)
    store = Store(args.data_dir)
    try:
        if store.remote:
            raise ValueError("Packaging expects a local persisted benchmark workspace")
        store.init()
        manifest = package_run(store, args.run_id, args.output_dir, part_documents=args.part_documents)
        print(json.dumps({"status": manifest["run"]["status"], "documents": manifest["counts"]["documents"], "assessments": manifest["counts"]["assessments"], "parts": len(manifest["parts"]), "citation_audit": manifest["citation_audit"]}))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
