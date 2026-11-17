"""Restore finished contextual archives with exact source checks and no inference.

Archives are streamed twice: validation finishes before any run is written, then
bounded batches persist one document at a time. Interrupted writes are safe to
repeat. Historical usage is retained without charging the local budget ledger.
"""
from __future__ import annotations

from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re

from .contextual import UNIT_BUILDER_VERSION, _criteria, build_units
from .store import Store, utcnow

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PART_BYTES = 50 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024 * 1024
MAX_PART_RAW_BYTES = 512 * 1024 * 1024
MAX_BATCH_BYTES = 768 * 1024
MAX_BATCH_STATEMENTS = 50
RESULTS = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
STATUSES = {"accepted", "partial", "failed", "pending"}
ATTRIBUTIONS = {"author": "author_assertion", "quoted_speaker": "attributed_quotation", "reported_speaker": "reported_speech", "unknown": "unknown"}
STANCES = {"supports", "opposes", "neutral", "mixed", "unclear", "not_applicable"}


class ShowcaseNotFinished(ValueError):
    """An active, paused or failed archive must not become a saved demo run."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_manifest(path: Path) -> tuple[dict, Path, str]:
    path = Path(path)
    path = path / "showcase.json" if path.is_dir() else path
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Saved contextual manifest exceeds its size limit")
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("Saved contextual manifest must be an object")
    if manifest.get("format") != "doxa-contextual-jsonl-parts" or manifest.get("schema_version") != "3.0":
        raise ValueError("Unsupported saved contextual package format")
    run = manifest.get("run") or {}
    if not isinstance(run, dict) or not isinstance(run.get("config"), dict):
        raise ValueError("Saved contextual manifest requires a run/config object")
    if run.get("status") not in {"completed", "partial"}:
        raise ShowcaseNotFinished(f"Saved contextual package is {run.get('status', 'unknown')}; only finished completed/partial packages can be restored")
    if not run.get("id") or str((run.get("config") or {}).get("methodology_version")) != "3.0":
        raise ValueError("Saved package requires an identified methodology version 3.0 run")
    return manifest, path, _digest(raw)


def _parts(manifest, path):
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Saved contextual package has no archive parts")
    names = set()
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Saved contextual part entries must be objects")
        name = part.get("file")
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".jsonl.gz") or name in names:
            raise ValueError("Saved contextual part names must be unique local gzip files")
        names.add(name)
        file = (path.parent / name).resolve()
        if not file.is_relative_to(path.parent.resolve()):
            raise ValueError("Saved contextual part escapes its package directory")
        size = file.stat().st_size
        if not 0 < size < MAX_PART_BYTES or size != part.get("bytes") or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", ""))) or _file_digest(file) != part["sha256"]:
            raise ValueError("Saved contextual part failed its size/checksum validation")
        if not isinstance(part.get("documents"), int) or not 1 <= part["documents"] <= 100:
            raise ValueError("Saved contextual parts require 1–100 document records")
        if not isinstance(part.get("uncompressed_bytes"), int) or not 0 < part["uncompressed_bytes"] <= MAX_PART_RAW_BYTES:
            raise ValueError("Saved contextual part exceeds its uncompressed size limit")


def _records(manifest, path):
    for part in manifest["parts"]:
        count = raw_bytes = 0
        with gzip.open(path.parent / part["file"], "rb") as stream:
            while True:
                line = stream.readline(MAX_RECORD_BYTES + 1)
                if not line:
                    break
                raw_bytes += len(line)
                if len(line) > MAX_RECORD_BYTES or raw_bytes > MAX_PART_RAW_BYTES:
                    raise ValueError("Saved contextual record exceeds its decompression limit")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Saved contextual archive records must be objects")
                count += 1
                yield record
        if count != part["documents"] or raw_bytes != part["uncompressed_bytes"]:
            raise ValueError("Saved contextual part record count/length changed")


def _documents(store, project_id, documents, snapshots):
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise ValueError("Showcase project does not exist")
    if not isinstance(snapshots, list) or not snapshots or len(snapshots) > 50000:
        raise ValueError("Saved contextual run needs a bounded frozen source selection")
    if any(not isinstance(item, dict) or not item.get("id") or not item.get("sha256") for item in snapshots) or len({item["id"] for item in snapshots}) != len(snapshots):
        raise ValueError("Saved contextual source selection contains invalid or duplicate identities")
    documents = list(documents)
    identities = [document.get("id") for document in documents]
    if len(documents) != len(snapshots) or len(set(identities)) != len(documents) or None in identities:
        raise ValueError("Imported source identities must be distinct and match the complete frozen selection")
    supplied = {document["id"]: document for document in documents}
    mapping = {}
    for offset in range(0, len(identities), 80):
        part = identities[offset:offset + 80]
        rows = store.query("SELECT id,project_id,sha256,blocks_key,metadata_json,title,author,institution,document_type,published_at,word_count,source_url FROM documents WHERE project_id=? AND id IN (" + ",".join("?" for _ in part) + ")", [project_id, *part])
        if len(rows) != len(part):
            raise ValueError("Imported source does not belong to the showcase project")
        for row in rows:
            metadata = json.loads(row.pop("metadata_json") or "{}")
            source_id = metadata.get("source_id")
            if not source_id or source_id in mapping or supplied[row["id"]].get("sha256") != row["sha256"]:
                raise ValueError("Imported source identities/hashes are invalid or duplicated")
            row["metadata"] = metadata
            mapping[source_id] = row
    return mapping


def _validate_record(store, record, document, frozen, run, criteria):
    config = run["config"]
    if record.get("sha256") != document["sha256"] or frozen["sha256"] != record["sha256"]:
        raise ValueError("Saved source hash changed; recompute or restore the exact source version")
    old_id = record.get("document_id", frozen["id"])
    if old_id != frozen["id"]:
        raise ValueError("Saved source identity differs from its frozen source ordinal")
    blocks = store.get_json(document["blocks_key"])
    blocks_hash = _digest(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    if blocks_hash != record.get("blocks_sha256", record["sha256"]):
        raise ValueError("Saved extracted source blocks changed; repackage the exact source snapshot")
    metadata = document["metadata"]
    if metadata.get("content_sha256") == document["sha256"] and blocks_hash != document["sha256"]:
        raise ValueError("Canonical extracted source content differs from its frozen source hash")
    source_identity = metadata.get("source_id") or metadata.get("source_identity") or document.get("source_url") or old_id
    version = _digest(f"{source_identity}:{record['sha256']}".encode())[:32]
    if record.get("source_version_id", version) != version:
        raise ValueError("Saved source version identity is invalid")
    old_document = {**document, "id": old_id, "source_version_id": version, "blocks": blocks}
    units = build_units(old_document, max_unit_chars=config.get("unit_max_chars", 1200), context_sentences=config.get("context_sentences", 2))
    selection = config.get("verification_selection")
    if selection:
        selected = set(selection.get("unit_ids", {}).get(old_id, []))
        if not selected <= {unit["id"] for unit in units}:
            raise ValueError("Saved sampled unit selection has invalid source anchors")
        units = [unit for unit in units if unit["id"] in selected]
    by_unit = {unit["id"]: unit for unit in units}
    assessments, statuses = record.get("assessments"), record.get("unit_statuses")
    if not isinstance(assessments, list) or not isinstance(statuses, list) or any(not isinstance(item, dict) for item in [*assessments, *statuses]):
        raise ValueError("Saved source requires explicit assessment and unit status arrays")
    seen, ids, decisions, references = set(), set(), {}, 0
    for item in assessments:
        unit_id, key = item.get("unit_id"), f"{item.get('lens_id')}/{item.get('criterion_id')}"
        if not item.get("id") or item["id"] in ids or (unit_id, key) in seen or unit_id not in by_unit or key not in criteria:
            raise ValueError("Saved assessment has invalid/duplicate identity, unit or criterion")
        ids.add(item["id"])
        seen.add((unit_id, key))
        if item.get("criterion_kind") != criteria[key]["kind"] or item.get("result") not in RESULTS or item.get("criterion_key", key) != key:
            raise ValueError("Saved assessment outcome/criterion kind is invalid")
        if item.get("document_id") != old_id or item.get("run_id", run["id"]) != run["id"] or item.get("project_id", run.get("project_id")) != run.get("project_id"):
            raise ValueError("Saved assessment belongs to a different source/run")
        if item.get("source_hash", record["sha256"]) != record["sha256"] or item.get("source_version_id", version) != version:
            raise ValueError("Saved assessment source version changed")
        attribution = item.get("attribution", "unknown")
        if attribution not in ATTRIBUTIONS or item.get("speech_role", ATTRIBUTIONS[attribution]) != ATTRIBUTIONS[attribution] or any(item.get(field, "unclear") not in STANCES for field in ("speaker_stance", "document_stance")) or not isinstance(item.get("hypothetical", False), bool):
            raise ValueError("Saved assessment attribution/stance fields are invalid")
        unit = by_unit[unit_id]
        available = {sentence["id"]: sentence for sentence in [*unit["context_before"], *unit["sentences"], *unit["context_after"]]}
        owned = {sentence["id"] for sentence in unit["sentences"]}
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, dict) for ref in refs) or not owned.intersection(ref.get("sentence_id") for ref in refs):
            raise ValueError("Saved assessment evidence must include an exact scored-unit sentence")
        for ref in refs:
            sentence = available.get(ref.get("sentence_id"))
            if sentence is None or any(type(ref.get(field)) is not int for field in ("start", "end")) or any(ref.get(field) != sentence[field] for field in ("block_id", "start", "end")) or ref.get("quote") != sentence["text"]:
                raise ValueError("Saved assessment failed exact source sentence/quotation validation")
            for field in ("document_id", "source_version_id", "source_hash", "unit_id", "text", "paragraph_id", "page", "paragraph", "line_start", "line_end", "source_char_start", "source_char_end"):
                if field in ref and ref[field] is not None and ref[field] != sentence.get(field):
                    raise ValueError("Saved assessment evidence source locator changed")
            references += 1
        primary = refs[0]
        if any(item.get(field, primary[field]) != primary[field] for field in ("block_id", "start", "end", "quote")):
            raise ValueError("Saved assessment primary quotation differs from its source evidence")
        decisions.setdefault(unit_id, set()).add(key)
    for status in statuses:
        unit_id = status.get("unit_id")
        if unit_id in seen or unit_id not in by_unit or status.get("status") not in STATUSES:
            raise ValueError("Saved source unit status has an invalid/duplicate anchor")
        seen.add(unit_id)
        unit = by_unit[unit_id]
        if type(status.get("characters")) is not int or status["characters"] != unit["source_characters"]:
            raise ValueError("Saved unit character coverage differs from the frozen source unit")
        accepted, expected = decisions.get(unit_id, set()), set(criteria)
        state = status["status"]
        if state == "accepted" and accepted != expected:
            raise ValueError("Saved unit status overstates its explicit decision coverage")
        if any(field in status for field in ("accepted_criteria", "failed_criteria", "pending_criteria")):
            groups = [status.get(field, []) for field in ("accepted_criteria", "failed_criteria", "pending_criteria")]
            if any(not isinstance(group, list) or any(not isinstance(key, str) for key in group) or len(group) != len(set(group)) for group in groups) or not set(groups[0]) <= accepted or (state == "accepted" and set(groups[0]) != accepted) or len(set().union(*map(set, groups))) != sum(map(len, groups)) or set().union(*map(set, groups)) != expected:
                raise ValueError("Saved unit decision partition is invalid")
    if {status["unit_id"] for status in statuses} != set(by_unit):
        raise ValueError("Saved package omits frozen source units from its coverage denominator")
    if run["status"] == "completed" and any(status["status"] != "accepted" for status in statuses):
        raise ValueError("Completed saved run contains incomplete source units")
    return {"assessments": len(assessments), "audited_references": references,
            "source_characters": sum(len(block["text"]) for block in blocks), "words": document["word_count"],
            "unit_states": Counter(status["status"] for status in statuses), "outcomes": Counter(item["result"] for item in assessments)}


def _validate_archive(store, manifest, path, mapping, criteria):
    run, totals, states, outcomes, seen = manifest["run"], Counter(), Counter(), Counter(), set()
    ordered = []
    snapshots = run["config"]["source_snapshot"]
    for ordinal, record in enumerate(_records(manifest, path)):
        source_id = record.get("source_id")
        if ordinal >= len(snapshots) or source_id not in mapping or source_id in seen:
            raise ValueError("Saved source identities are unknown, duplicated or outside the frozen selection")
        seen.add(source_id)
        ordered.append(source_id)
        stats = _validate_record(store, record, mapping[source_id], snapshots[ordinal], run, criteria)
        states.update(stats.pop("unit_states"))
        outcomes.update(stats.pop("outcomes"))
        totals.update(stats)
        totals["documents"] += 1
    if seen != set(mapping) or any(manifest.get("counts", {}).get(key) != value for key, value in totals.items()):
        raise ValueError("Saved contextual archive counts/source scope do not match its manifest")
    if dict(states) != manifest.get("unit_states") or dict(outcomes) != manifest.get("outcomes"):
        raise ValueError("Saved contextual outcome/coverage counts changed")
    return ordered


def _batches(store, statements):
    batch, size = [], 0
    for statement in statements:
        amount = len(_json(statement).encode())
        if amount > MAX_BATCH_BYTES:
            raise ValueError("One saved assessment exceeds the bounded storage request size")
        if batch and (len(batch) >= MAX_BATCH_STATEMENTS or size + amount > MAX_BATCH_BYTES):
            store.batch(batch)
            batch, size = [], 0
        batch.append(statement)
        size += amount
    if batch:
        store.batch(batch)


def _remap_error_sources(value, mapping):
    if isinstance(value, list):
        return [_remap_error_sources(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: mapping.get(item, item) if key == "document_id" and isinstance(item, str) else _remap_error_sources(item, mapping) for key, item in value.items()}
    return value


def restore_showcase_v3(store: Store, project_id: str, documents: list[dict], package_path: Path) -> str:
    """Restore a source-checked completed/partial package; never contact a model."""
    manifest, path, digest = read_manifest(package_path)
    run, now = manifest["run"], utcnow()
    source_config = run["config"]
    builder_version = source_config.get("unit_builder_version") or run.get("model_profile", {}).get("unit_builder_version") or UNIT_BUILDER_VERSION
    if builder_version != UNIT_BUILDER_VERSION:
        raise ValueError("Saved source unit builder is unsupported; restore with its frozen application version")
    mapping = _documents(store, project_id, documents, source_config.get("source_snapshot"))
    _, criteria = _criteria(source_config.get("lens_snapshots") or source_config.get("lens_ids") or ["discourse"], source_config)
    _parts(manifest, path)
    ordered_ids = _validate_archive(store, manifest, path, mapping, criteria)
    run_id = _digest(f"{project_id}:v3-showcase:{digest}".encode())[:32]
    existing = store.one("SELECT project_id,config_json FROM runs WHERE id=?", [run_id])
    if existing:
        saved = json.loads(existing["config_json"]).get("saved_snapshot", {})
        if existing["project_id"] != project_id or saved.get("sha256") != digest:
            raise ValueError("Saved contextual run identity collides with another import")
        if saved.get("import_complete"):
            return run_id
    ordered = [mapping[source_id] for source_id in ordered_ids]
    id_mapping = {old["id"]: current["id"] for old, current in zip(source_config["source_snapshot"], ordered)}
    config = copy.deepcopy(source_config)
    config["source_snapshot"] = [{"id": document["id"], "sha256": document["sha256"]} for document in ordered]
    if "document_ids" in config:
        config["document_ids"] = [id_mapping[identity] for identity in config["document_ids"]]
    if config.get("verification_selection"):
        selection = config["verification_selection"]
        selection["document_ids"] = [id_mapping[identity] for identity in selection.get("document_ids", [])]
        selection["unit_ids"] = {id_mapping[identity]: values for identity, values in selection.get("unit_ids", {}).items()}
    config["saved_snapshot"] = {"sha256": digest, "format": manifest["format"], "generated_at": manifest.get("generated_at"),
                                "original_run_id": run["id"], "original_project_id": run.get("project_id"),
                                "historical_usage": True, "historical_spent_usd": run.get("spent_usd", 0),
                                "historical_reserved_usd": run.get("reserved_usd", 0), "import_complete": False,
                                "notice": "Previously computed contextual output. Importing makes no inference calls and does not debit this workspace budget. Historical reviews are provenance; local reviews start unreviewed."}
    if config.get("source_manifest_key"):
        config["source_manifest_key"] = f"runs/{run_id}/source-manifest.json"
        if not store.remote:
            from .privacy_lifecycle import track_artifact
            track_artifact(store, project_id, f"objects/runs/{run_id}", "recorded_run")
        store.put_json(config["source_manifest_key"], [{key: document.get(key) for key in ("id", "sha256", "title", "author", "institution", "document_type", "published_at")} for document in ordered])
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,started_at,completed_at,progress_json,usage_json,errors_json,model_profile_json) VALUES(?,?,'importing',?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                  [run_id, project_id, _json(config), now, run.get("started_at"), run.get("completed_at"), _json(run.get("progress", {})), _json(run.get("usage", {})), _json(_remap_error_sources(run.get("errors", []), id_mapping)), _json(run.get("model_profile", {}))])
    checkpoint_key = "v3-showcase:" + run_id
    checkpoint = store.one("SELECT value_json FROM settings WHERE key=?", [checkpoint_key])
    done = int(json.loads(checkpoint["value_json"]).get("documents", 0)) if checkpoint else 0
    for ordinal, record in enumerate(_records(manifest, path)):
        if ordinal < done:
            continue
        document = mapping[record["source_id"]]
        task_id = _digest(f"{run_id}:{record['source_id']}".encode())[:32]

        def statements():
            for assessment in record["assessments"]:
                item = copy.deepcopy(assessment)
                old_id = item["id"]
                item.update({"id": _digest(f"{run_id}:{record['source_id']}:{old_id}".encode())[:32], "project_id": project_id,
                             "run_id": run_id, "document_id": document["id"], "task_id": task_id, "source_assessment_id": old_id})
                item["evidence_refs"] = [{**ref, "document_id": document["id"]} for ref in item["evidence_refs"]]
                for key in ("review_status", "review_note", "review_overlay"):
                    if item.get(key):
                        item["historical_" + key] = item[key]
                item.update({"review_status": "unreviewed", "review_note": ""})
                item.pop("review_overlay", None)
                columns = ["id", "project_id", "run_id", "document_id", "task_id", "unit_id", "lens_id", "criterion_id", "criterion_kind", "result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical"]
                defaults = {"attribution": "unknown", "speech_role": "unknown", "speaker_stance": "unclear", "document_stance": "unclear", "hypothetical": False}
                values = [item.get(key, defaults.get(key)) for key in columns]
                values[-1] = int(values[-1])
                yield {"sql": "INSERT INTO v3_assessments(" + ",".join(columns) + ",payload_json,created_at) VALUES(" + ",".join("?" for _ in range(len(columns) + 2)) + ") ON CONFLICT(id) DO NOTHING", "params": [*values, _json(item), now]}
            for status in record["unit_statuses"]:
                payload = {**status, "document_id": document["id"], "run_id": run_id}
                yield {"sql": "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,task_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,unit_id) DO NOTHING", "params": [run_id, document["id"], status["unit_id"], task_id, status["status"], status["characters"], _json(payload), now]}
            accepted = sum(status["status"] == "accepted" for status in record["unit_statuses"])
            complete = accepted == len(record["unit_statuses"])
            coverage = {"total_units": len(record["unit_statuses"]), "accepted_units": accepted, "complete": complete, "saved_snapshot": True}
            yield {"sql": "INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block,status,coverage_json) VALUES(?,?,?,0,0,?,?,?) ON CONFLICT(id) DO NOTHING", "params": [task_id, run_id, document["id"], len(record["unit_statuses"]), "completed" if complete else "failed", _json(coverage)]}
            yield {"sql": "INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES(?,?,?) ON CONFLICT(run_id,document_id) DO NOTHING", "params": [run_id, document["id"], document["sha256"]]}
            yield {"sql": "INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", "params": [checkpoint_key, _json({"documents": ordinal + 1, "sha256": digest})]}

        _batches(store, statements())
    config["saved_snapshot"]["import_complete"] = True
    store.execute("UPDATE runs SET config_json=?,status=? WHERE id=? AND project_id=?", [_json(config), run["status"], run_id, project_id])
    return run_id


def import_institution_sources(store: Store, project_id: str, root: Path) -> tuple[list[dict], dict]:
    """Import the attributed twenty-source corpus without creating an analysis."""
    files = sorted((Path(root) / "corpus" / "documents").glob("*.json"))
    if len(files) != 20:
        raise ValueError("The twenty-document institutional corpus is unavailable")
    manifest_digest = _digest("".join(_file_digest(file) for file in files).encode())
    batch_id = _digest(f"{project_id}:institutions:{manifest_digest}".encode())[:32]
    batch = {"id": batch_id, "name": "Public institutions · 20 real documents"}
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", [batch_id, project_id, batch["name"], utcnow()])
    documents, seen = [], set()
    for file in files:
        source = json.loads(file.read_text(encoding="utf-8"))
        source_id = source.get("id")
        blocks = source.get("blocks")
        digest = _digest(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        if not source_id or source_id in seen or digest != source.get("content_sha256"):
            raise ValueError("Institutional source identity or extracted text checksum changed")
        seen.add(source_id)
        document_id = _digest(f"{project_id}:institutions:{source_id}".encode())[:32]
        existing = store.one("SELECT id,sha256 FROM documents WHERE id=? AND project_id=?", [document_id, project_id])
        if not existing:
            # A project may already contain this version from the historical
            # demo importer. Reuse its source identity without collapsing an
            # independent source that happens to have identical content.
            matches = store.query("SELECT id,sha256,metadata_json FROM documents WHERE project_id=? AND sha256=? ORDER BY id", [project_id, digest])
            existing = next((row for row in matches if json.loads(row["metadata_json"]).get("source_id") == source_id), None)
            if existing:
                document_id = existing["id"]
        if existing and existing["sha256"] != digest:
            raise ValueError("An imported institutional source changed")
        if not existing:
            metadata = {key: value for key, value in source.items() if key != "blocks"}
            metadata["source_id"] = source_id
            blocks_key = f"documents/{document_id}/blocks.json"
            if not store.remote:
                from .privacy_lifecycle import track_artifact
                track_artifact(store, project_id, f"objects/documents/{document_id}", "institutional_source")
            store.put_json(blocks_key, blocks)
            values = {"id": document_id, "project_id": project_id, "batch_id": batch_id, "title": source["title"],
                      "author": source.get("author") or "Unknown", "institution": source.get("institution") or "",
                      "document_type": source.get("document_type") or "document", "published_at": source.get("published_at"),
                      "filename": source_id + ".txt", "mime_type": "text/plain", "sha256": digest, "status": "ready",
                      "word_count": source.get("word_count") or sum(len(block["text"].split()) for block in blocks),
                      "page_count": source.get("page_count") or 0, "source_url": source.get("source_url") or "",
                      "license": source.get("license") or "", "blocks_key": blocks_key, "metadata_json": _json(metadata), "created_at": utcnow()}
            store.execute("INSERT INTO documents(" + ",".join(values) + ") VALUES(" + ",".join("?" for _ in values) + ")", list(values.values()))
            if store.fts:
                store.index_document(document_id, project_id, source["title"], blocks)
        store.execute("INSERT INTO v3_sources(document_id,project_id,source_identity,content_hash,canonical_url,relative_path,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(document_id) DO NOTHING", [document_id, project_id, source_id, digest, source.get("source_url") or "", source_id + ".txt", utcnow()])
        store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", [batch_id, document_id])
        documents.append({"id": document_id, "sha256": digest})
    return documents, batch
