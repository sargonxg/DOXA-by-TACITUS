"""Offline, read-only exploration of a frozen twenty-source research subset.

This never initializes a Store, installs a run, remaps historical identities or
requests inference. The full parent run remains partial and unvalidated.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_RAW_BYTES = 12 * 1024 * 1024
DATA_DIRECTORY = Path(__file__).with_name("data")
OUTCOMES = {"meets_criterion", "does_not_meet", "not_applicable", "uncertain"}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_example(payload):
    if (not isinstance(payload, dict) or payload.get("schema_version") != "doxa-recorded-starter-1"
            or payload.get("mode") != "read_only_recorded_exploration"
            or payload.get("fresh_inference_requests") != 0
            or payload.get("independent_human_reference_labels") is not False):
        raise ValueError("Recorded example has an invalid origin/interpretation contract")
    documents, records = payload.get("documents"), payload.get("records")
    if not isinstance(documents, list) or not isinstance(records, list) or len(documents) != 20 or len(records) != 20:
        raise ValueError("Recorded example must retain exactly twenty source records")
    by_document = {document["id"]: document for document in documents}
    if len(by_document) != 20:
        raise ValueError("Recorded example repeats a historical document identity")
    counts, states, seen = Counter(), Counter(), set()
    references = 0
    for record in records:
        identity = record.get("document_id")
        if identity in seen or identity not in by_document:
            raise ValueError("Recorded example has a missing or repeated parent source")
        seen.add(identity)
        document = by_document[identity]
        metadata, blocks = document["metadata"], document["blocks"]
        if (document.get("source_id") != metadata.get("id") or document.get("sha256") != record.get("sha256")
                or any(document.get(key) != metadata.get(key) for key in
                       ("title", "author", "authors", "translators", "source_url", "published_at", "license", "license_url", "attribution", "word_count"))):
            raise ValueError("Recorded display metadata differs from its canonical source credits")
        if (metadata.get("split") != "development" or metadata.get("synthetic") is not False
                or metadata.get("license") != "CC-BY-3.0"
                or metadata.get("license_url") != "https://creativecommons.org/licenses/by/3.0/"
                or not metadata.get("attribution") or not metadata.get("authors")):
            raise ValueError("Recorded example requires licensed, credited development sources")
        if (canonical_hash(blocks) != record.get("blocks_sha256")
                or record.get("blocks_sha256") != record.get("sha256")
                or record.get("sha256") != metadata.get("content_sha256")
                or canonical_hash({**metadata, "blocks": blocks}) != document.get("source_snapshot_sha256")
                or canonical_hash(record) != document.get("archive_record_sha256")):
            raise ValueError("Recorded source or assessment snapshot hash changed")
        by_block = {block["id"]: block["text"] for block in blocks}
        if len(by_block) != len(blocks):
            raise ValueError("Recorded example has duplicate source blocks")
        for assessment in record["assessments"]:
            if (assessment.get("document_id") != identity or assessment.get("run_id") != payload["parent"]["run_id"]
                    or assessment.get("source_hash") != record["sha256"]
                    or assessment.get("source_version_id") != record["source_version_id"]
                    or assessment.get("result") not in OUTCOMES):
                raise ValueError("Recorded assessment scope or outcome changed")
            refs = assessment.get("evidence_refs")
            if not isinstance(refs, list) or not refs:
                raise ValueError("Recorded assessment requires original exact references")
            for ref in refs:
                text = by_block.get(ref.get("block_id"))
                start, end = ref.get("start"), ref.get("end")
                if (text is None or type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(text) or text[start:end] != ref.get("quote")):
                    raise ValueError("Recorded assessment quotation does not resolve exactly")
                references += 1
            counts[assessment["result"]] += 1
        states.update(status["status"] for status in record["unit_statuses"])
    summary = payload["selection"]["counts"]
    if (set(counts) != OUTCOMES or dict(counts) != summary["outcomes"]
            or dict(states) != summary["unit_states"]
            or sum(counts.values()) != summary["assessments"]
            or references != summary["exact_references"]):
        raise ValueError("Recorded example coverage totals differ from retained records")
    if payload["parent"]["status"] != "partial" or payload["parent"]["independent_human_reference_labels"] is not False:
        raise ValueError("Recorded example must retain its partial, unvalidated parent status")
    if any(payload["parent"].get(key) != payload["method"].get(key) for key in ("model", "provider")):
        raise ValueError("Recorded example display model differs from the frozen method")
    return {"documents": len(documents), "assessments": sum(counts.values()),
            "exact_references": references, "outcomes": dict(counts), "unit_states": dict(states)}


def load_recorded_example(directory=None):
    root = DATA_DIRECTORY if directory is None else Path(directory)
    manifest_path = root / "recorded-starter.manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
        raise ValueError("The packaged recorded example manifest is missing or oversized")
    manifest = json.loads(manifest_path.read_text())
    artifact = manifest.get("artifact", {})
    if manifest.get("schema_version") != "doxa-recorded-starter-manifest-1" or artifact.get("file") != "recorded-starter.json.gz":
        raise ValueError("Unsupported recorded example package")
    path = root / artifact["file"]
    if not path.is_file() or path.stat().st_size > MAX_COMPRESSED_BYTES:
        raise ValueError("The packaged recorded example is missing or oversized")
    compressed = path.read_bytes()
    if len(compressed) != artifact.get("bytes") or hashlib.sha256(compressed).hexdigest() != artifact.get("sha256"):
        raise ValueError("Recorded example compressed checksum differs from its manifest")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES or len(raw) != artifact.get("uncompressed_bytes") or hashlib.sha256(raw).hexdigest() != artifact.get("uncompressed_sha256"):
        raise ValueError("Recorded example uncompressed checksum or bounds differ from its manifest")
    payload = json.loads(raw)
    validate_example(payload)
    return payload


def recorded_artifact(format, directory=None):
    root = DATA_DIRECTORY if directory is None else Path(directory)
    filenames = {"pdf": ("recorded-example.pdf", "application/pdf"),
                 "xlsx": ("recorded-example.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                 "html": ("recorded-example.html", "text/html; charset=utf-8"),
                 "json.gz": ("recorded-starter.json.gz", "application/gzip")}
    if format not in filenames:
        raise ValueError("Recorded example formats are pdf, xlsx, html and json.gz")
    filename, media_type = filenames[format]
    manifest_path = root / "recorded-example.manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
        raise ValueError("Recorded example publication manifest is missing or oversized")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "doxa-recorded-example-publication-1":
        raise ValueError("Unsupported recorded example publication manifest")
    entry = next((item for item in manifest["files"] if item["packaged_file"] == filename), None)
    path = root / filename
    if entry is None or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Recorded example artifact is missing or oversized")
    raw = path.read_bytes()
    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("Recorded example artifact failed its release checksum")
    return path, media_type, f"v06-recorded-example.{format}"


def register_recorded_api(app):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    @app.get("/api/system/recorded-example")
    def recorded_example():
        try:
            return load_recorded_example()
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, "The packaged recorded example is unavailable or failed its integrity check") from None

    @app.get("/api/system/recorded-example/artifacts/{format}")
    def recorded_example_artifact(format: str):
        if format not in {"pdf", "xlsx", "html", "json.gz"}:
            raise HTTPException(404, "Recorded example format not found")
        try:
            path, media_type, filename = recorded_artifact(format)
            return FileResponse(path, media_type=media_type, filename=filename,
                                headers={"Cache-Control": "private, no-store"})
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "The packaged recorded report is unavailable or failed its integrity check") from None
