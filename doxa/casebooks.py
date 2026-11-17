"""Operator-curated casebooks with immutable source/model captures and drafts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
import uuid

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .store import utcnow
from .snapshot_reads import scope_snapshot, validate_source_blocks
from .workspace import Workspace, unpack_assessment, unpack_document

MAX_CASES = 100
MAX_CASE_BYTES = 768 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def initialize_casebooks(store):
    schema = Path(__file__).with_name("v4_casebooks.sql").read_text()
    store.batch([{"sql": part, "params": []} for part in schema.split(";") if part.strip()])


def _book(store, identity):
    row = store.one("SELECT * FROM v4_casebooks WHERE id=?", [identity])
    if not row or not store.one("SELECT id FROM projects WHERE id=?", [row["project_id"]]):
        raise HTTPException(404, "Casebook not found")
    return row


def _scope(store, book):
    value = store.get_json(book["scope_key"])
    if digest(value) != book["scope_sha256"]:
        raise HTTPException(409, "Frozen casebook scope failed its integrity check")
    return value


def _case(store, row):
    frozen = store.get_json(row["snapshot_key"])
    if digest(frozen) != row["snapshot_sha256"]:
        raise HTTPException(409, "Frozen case source failed its integrity check")
    snapshot = {**frozen, "editorial_note": row["editorial_note"], "selection_rationale": row["selection_rationale"],
                "editorial_authorship": "Operator-supplied guidance; not independent human adjudication."}
    assessment = frozen.get("normalized_assessment") or frozen.get("assessment") or {}
    return {**row, "source_snapshot": frozen["source"], "assessment_snapshot": frozen.get("assessment"),
            "unit_snapshot": frozen.get("unit"), "frozen_snapshot": snapshot,
            "criterion_kind": assessment.get("criterion_kind"), "result": assessment.get("result"),
            "title": assessment.get("title") or "Unresolved source unit",
            "block_id": assessment.get("block_id") or (frozen.get("unit") or {}).get("rebuilt_unit", {}).get("block_id"),
            "capture_sha256": row["snapshot_sha256"]}


def get_casebook(store, identity):
    book = _book(store, identity)
    with scope_snapshot(store, book["project_id"]):
        book = _book(store, identity)
        frozen_run = _scope(store, book)["run"]
        config = json.loads(frozen_run.get("config_json") or "{}")
        profile = json.loads(frozen_run.get("model_profile_json") or "{}")
        method = {key: config.get(key) for key in ("methodology_version", "pipeline_version", "schema_version", "provider", "model", "model_revision", "quantization", "method_fingerprint", "analysis_profile_hash", "source_metadata_hash", "lens_snapshots_sha256", "unit_builder_version", "unit_max_chars", "context_sentences")}
        method["profile"] = {key: profile.get(key) for key in ("prompt_version", "revision", "response_model", "response_schema_version", "structured_output", "criterion_registry_version", "temperature", "context_limit", "citation_offset_unit", "batching_version")}
        method.update(run_id=frozen_run["id"], run_status=frozen_run.get("status"),
                      source_cohort_count=len(config.get("source_snapshot", [])),
                      source_cohort_sha256=digest(config.get("source_snapshot", [])))
        cases = store.query("SELECT * FROM v4_casebook_cases WHERE casebook_id=? ORDER BY ordinal,id", [identity])
        result = {**book, "cases": [_case(store, row) for row in cases], "case_limit": MAX_CASES,
                  "method_provenance": method,
                  "interpretation_accuracy": None, "notice": "Curated cases are illustrative, not a representative quality sample. Original model records and review state remain separate."}
        if _book(store, identity)["revision"] != book["revision"]:
            raise HTTPException(409, "Casebook changed while loading; refresh the draft")
        return result


def casebook_preview(book):
    """Bound browser responses; complete captures remain in export inputs."""
    result = {key: value for key, value in book.items() if key not in {"scope_key", "scope_sha256", "cases"}}
    cases = []
    for item in book["cases"]:
        source = item["source_snapshot"]
        raw = item.get("assessment_snapshot") or {}
        preview = {key: value for key, value in item.items() if key not in {"snapshot_key", "frozen_snapshot", "source_snapshot", "assessment_snapshot", "unit_snapshot"}}
        preview["source_snapshot"] = {key: source.get(key) for key in ("id", "title", "author", "published_at", "sha256", "source_url", "license", "source_version_id")}
        preview["assessment_snapshot"] = {key: raw.get(key) for key in ("id", "unit_id", "title", "block_id", "criterion_id", "criterion_kind", "result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical", "detail_supplied", "certainty")}
        for key, cap in (("quote", 1000), ("explanation", 1000), ("alternative", 1000)):
            preview["assessment_snapshot"][key] = str(raw.get(key) or "")[:cap]
        capture = item.get("frozen_snapshot") or {}
        condition = capture.get("criterion") or {}
        preview["framework_condition"] = {key: condition.get(key) for key in ("lens_name", "lens_version", "id", "label", "kind", "source_url")}
        preview["framework_condition"]["description"] = str(condition.get("description") or "")[:1000]
        preview["criterion_capture_status"] = capture.get("criterion_capture_status", "not_recorded")
        preview["review_status"] = (capture.get("normalized_assessment") or {}).get("review_status", "unreviewed")
        preview["origin_labels"] = capture.get("origin_labels", {})
        unit = item.get("unit_snapshot") or {}
        preview["unit_snapshot"] = {key: unit.get(key) for key in ("unit_id", "status", "characters")}
        preview["bounded_preview"] = True
        cases.append(preview)
    result.update(cases=cases, case_count=len(cases))
    return result


def create_casebook(store, project_id, query_id, title, rationale="", publication_type="casebook"):
    service = Workspace(store)
    with scope_snapshot(store, project_id):
        saved = service.require_query(query_id)
        if saved["project_id"] != project_id:
            raise HTTPException(404, "Query does not belong to this project")
        run = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [saved["scope"].get("run_id"), project_id])
        if not run:
            raise HTTPException(422, "Choose a recorded contextual run for a casebook")
        config = json.loads(run["config_json"])
        if not str(config.get("methodology_version", "")).startswith("3") or config.get("provider") == "lexical":
            raise HTTPException(422, "Casebooks require typed contextual model results")
        where, params = service._document_scope(project_id, saved["scope"])
        documents = [unpack_document(row) for row in store.query("SELECT d.* FROM documents d WHERE " + " AND ".join(where) + " ORDER BY d.id", params)]
        for document in documents:
            metadata = document.get("metadata") or {}
            source_identity = metadata.get("source_id") or metadata.get("source_identity") or document.get("source_url") or document["id"]
            document["source_version_id"] = hashlib.sha256(f"{source_identity}:{document['sha256']}".encode()).hexdigest()[:32]
        expected = {row["id"]: row["sha256"] for row in config.get("source_snapshot", [])}
        if not documents or any(expected.get(row["id"]) != row["sha256"] for row in documents):
            raise HTTPException(409, "The selected source versions do not match the frozen run")
        bibliography = {}
        if config.get("source_manifest_key"):
            manifest = store.get_json(config["source_manifest_key"])
            if isinstance(manifest, list):
                bibliography = {row["id"]: row for row in manifest}
        frozen = {"schema_version": "casebook-scope-1", "scope": saved["scope"], "documents": documents,
                  "run": run, "analysis_metadata": bibliography,
                  "captured_at": utcnow(), "query_id": query_id,
                  "scope_hash": saved["scope_hash"], "snapshot_version": saved["snapshot_version"]}
    identity, timestamp = uuid.uuid4().hex, utcnow()
    key = f"casebooks/{identity}/scope.json"
    store.put_json(key, frozen)
    store.execute("INSERT INTO v4_casebooks(id,project_id,query_id,run_id,scope_hash,snapshot_version,scope_key,scope_sha256,title,selection_rationale,publication_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, project_id, query_id, run["id"], saved["scope_hash"], saved["snapshot_version"], key, digest(frozen), title, rationale, publication_type, timestamp, timestamp])
    return get_casebook(store, identity)


def _freeze_case(store, book, document_id, assessment_id=None, unit_id=None):
    scope = _scope(store, book)
    source = next((row for row in scope["documents"] if row["id"] == document_id), None)
    current = store.one("SELECT sha256 FROM documents WHERE id=? AND project_id=?", [document_id, book["project_id"]])
    if source is None or current is None or current["sha256"] != source["sha256"]:
        raise HTTPException(422, "The case must belong to the casebook's frozen source cohort")
    config = json.loads(scope["run"]["config_json"])
    assessment = normalized = overlay = criterion = original_payload = None
    events = []
    if assessment_id:
        row = store.one("SELECT * FROM v3_assessments WHERE id=? AND project_id=? AND run_id=? AND document_id=?",
                        [assessment_id, book["project_id"], book["run_id"], document_id])
        if not row or (unit_id and row["unit_id"] != unit_id):
            raise HTTPException(422, "Assessment does not belong to this source and frozen run")
        filters = scope["scope"]
        for name, column in (("lens_ids", "lens_id"), ("criterion_ids", "criterion_id"), ("speech_roles", "speech_role")):
            if filters.get(name) and row[column] not in filters[name]:
                raise HTTPException(422, "Assessment differs from the casebook's frozen framework or speaker filters")
        for name, column in (("review_status", "review_status"), ("stance", "speaker_stance")):
            if filters.get(name) and row[column] != filters[name]:
                raise HTTPException(422, "Assessment differs from the casebook's frozen review or stance filter")
        unit_id = row["unit_id"]
        original_payload = json.loads(row["payload_json"])
        assessment = json.loads(row["payload_json"])
        # Database identity fields disambiguate reused original assessment IDs.
        for key in ("id", "project_id", "run_id", "document_id", "unit_id", "lens_id", "criterion_id"):
            assessment[key] = row[key]
        if original_payload.get("id") != assessment["id"]:
            assessment["source_assessment_id"] = original_payload.get("id")
        normalized = unpack_assessment(row)
        overlay = json.loads(row.get("review_json") or "{}")
        events = store.query("SELECT * FROM v3_review_events WHERE assessment_id=? AND project_id=? ORDER BY created_at,id", [assessment_id, book["project_id"]])
        for pack in config.get("lens_snapshots", []):
            if pack.get("id") == row["lens_id"]:
                condition = next((item for item in pack.get("criteria", []) if item["id"] == row["criterion_id"]), None)
                if condition:
                    criterion = {"lens_id": pack["id"], "lens_name": pack.get("name"), "lens_version": pack.get("version"),
                                 "source_url": pack.get("source_url"), "scope": pack.get("scope"), "context_rules": pack.get("context_rules"), **condition}
    if not unit_id:
        raise HTTPException(422, "An assessment or recorded source-unit ID is required")
    unit = store.one("SELECT * FROM v3_unit_statuses WHERE run_id=? AND document_id=? AND unit_id=?", [book["run_id"], document_id, unit_id])
    if not unit or (not assessment and unit["status"] == "accepted"):
        raise HTTPException(422, "An unresolved case needs an actual unfinished source unit")
    blocks = store.get_json(source["blocks_key"])
    block_integrity = validate_source_blocks(source, blocks)
    by_id = {row["id"]: row for row in blocks}
    refs = (assessment or {}).get("evidence_refs") or []
    if assessment and not refs:
        raise HTTPException(422, "Assessment lacks source evidence references")
    selected = set()
    for ref in refs:
        block = by_id.get(ref.get("block_id"))
        start, end = ref.get("start"), ref.get("end")
        if (not block or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(block["text"])
                or (ref.get("quote") is not None and block["text"][start:end] != ref["quote"])):
            raise HTTPException(409, "Assessment source anchors failed exact validation")
        selected.add(block["id"])
    if not assessment:
        from .contextual import build_units
        char_limit = config.get("unit_max_chars") or (1200 if config.get("provider") == "ollama" else 4000)
        rebuilt = build_units({**source, "blocks": blocks}, max_unit_chars=char_limit,
                              context_sentences=config.get("context_sentences", 2))
        match = next((item for item in rebuilt if item["id"] == unit_id), None)
        if match is None:
            raise HTTPException(409, "Recorded source unit cannot be rebuilt from its frozen source")
        selected.update(ref["block_id"] for sentence in match.get("sentences", match.get("source_sentences", [])) for ref in sentence.get("segments", [sentence]) if ref.get("block_id"))
        if not selected:
            selected.update(ref["block_id"] for ref in match.get("segments", []) if ref.get("block_id"))
        unit["rebuilt_unit"] = match
    chosen = [block for block in blocks if block["id"] in selected]
    snapshot = {"schema_version": "casebook-case-1", "kind": "assessment" if assessment else "unresolved",
                "source": source, "source_sha256": source["sha256"], "blocks": chosen,
                "source_block_integrity": block_integrity,
                "analysis_metadata": scope.get("analysis_metadata", {}).get(document_id),
                "assessment": assessment, "original_assessment_payload": original_payload,
                "original_payload_sha256": hashlib.sha256(row["payload_json"].encode()).hexdigest() if assessment else None,
                "normalized_assessment": normalized, "review_overlay": overlay, "review_events": events,
                "criterion": criterion, "criterion_capture_status": "frozen_run_snapshot" if criterion else "not_recorded",
                "unit": unit, "captured_at": utcnow(),
                "notice": "Source, frozen framework, original model output, review and operator guidance are separate. Missing detail is not invented."}
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_CASE_BYTES:
        raise HTTPException(422, "Case capture exceeds 768 KiB; choose a smaller source unit")
    return snapshot


def pin_case(store, identity, document_id, assessment_id=None, unit_id=None):
    book = _book(store, identity)
    with scope_snapshot(store, book["project_id"]):
        book = _book(store, identity)
        existing = store.one("SELECT id FROM v4_casebook_cases WHERE casebook_id=? AND document_id=? AND COALESCE(assessment_id,'')=? AND (?='' OR unit_id=?)",
                             [identity, document_id, assessment_id or "", unit_id or "", unit_id or ""])
        if existing:
            return get_casebook(store, identity)
        position = store.one("SELECT COUNT(*) n,COALESCE(MAX(ordinal),-1)+1 next_ordinal FROM v4_casebook_cases WHERE casebook_id=?", [identity])
        if position["n"] >= MAX_CASES:
            raise HTTPException(422, "A casebook contains at most 100 selected cases")
        snapshot = _freeze_case(store, book, document_id, assessment_id, unit_id)
    unit_id = snapshot["unit"]["unit_id"]
    case_id = digest([identity, document_id, assessment_id, unit_id])[:32]
    snapshot_hash = digest(snapshot)
    key, timestamp = f"casebooks/{identity}/cases/{case_id}/{snapshot_hash}.json", utcnow()
    store.put_json(key, snapshot)
    # The conditional insert makes duplicate pins and concurrent stale requests
    # safe without changing an already captured case's original source record.
    changed = store.batch([
        {"sql": "UPDATE v4_casebooks SET revision=revision+1,updated_at=? WHERE id=? AND revision=? RETURNING id", "params": [timestamp, identity, book["revision"]]},
        {"sql": "INSERT INTO v4_casebook_cases(id,casebook_id,ordinal,kind,document_id,unit_id,assessment_id,snapshot_key,snapshot_sha256,created_at,updated_at) SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE changes()=1 ON CONFLICT(id) DO NOTHING",
         "params": [case_id, identity, position["next_ordinal"], snapshot["kind"], document_id, unit_id, assessment_id, key, snapshot_hash, timestamp, timestamp]},
    ])
    if not changed[0]["rows"]:
        raise HTTPException(409, "Casebook changed; reload before pinning another case")
    return get_casebook(store, identity)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="Source-linked casebook", min_length=1, max_length=180)
    selection_rationale: str = Field(default="", max_length=4000)
    publication_type: Literal["casebook", "teaching"] = "casebook"


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=180)
    selection_rationale: str | None = Field(default=None, max_length=4000)


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=128)
    assessment_id: str | None = Field(default=None, min_length=1, max_length=128)
    unit_id: str | None = Field(default=None, min_length=1, max_length=128)


class CasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_rationale: str | None = Field(default=None, max_length=4000)
    editorial_note: str | None = Field(default=None, max_length=8000)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_ids: list[str] = Field(min_length=1, max_length=MAX_CASES)


def register_casebooks(app, services):
    if getattr(app.state, "casebooks_registered", False):
        return
    app.state.casebooks_registered = True

    def db():
        return services["db"]()

    @app.post("/api/v3/projects/{project_id}/casebooks", status_code=201)
    def create(project_id: str, body: DraftInput):
        services["require_project"](project_id)
        return casebook_preview(create_casebook(db(), project_id, body.query_id, body.title, body.selection_rationale, body.publication_type))

    @app.get("/api/v3/projects/{project_id}/casebooks")
    def listing(project_id: str, limit: int = Query(default=30, ge=1, le=100), cursor: str = ""):
        services["require_project"](project_id)
        rows = db().query("SELECT b.id,b.project_id,b.title,b.revision,b.query_id,b.run_id,b.scope_hash,b.snapshot_version,b.publication_type,b.created_at,b.updated_at,(SELECT COUNT(*) FROM v4_casebook_cases c WHERE c.casebook_id=b.id) case_count FROM v4_casebooks b WHERE b.project_id=? AND b.id>? ORDER BY b.id LIMIT ?", [project_id, cursor, limit + 1])
        return {"items": [{**row, "cases": []} for row in rows[:limit]],
                "total": db().one("SELECT COUNT(*) n FROM v4_casebooks WHERE project_id=?", [project_id])["n"],
                "next_cursor": rows[limit-1]["id"] if len(rows) > limit else None}

    @app.get("/api/v3/casebooks/{identity}")
    def get(identity: str):
        return casebook_preview(get_casebook(db(), identity))

    @app.patch("/api/v3/casebooks/{identity}")
    def patch(identity: str, body: DraftPatch):
        _book(db(), identity)
        changes = body.model_dump(exclude_none=True)
        if changes:
            db().execute("UPDATE v4_casebooks SET " + ",".join(key + "=?" for key in changes) + ",revision=revision+1,updated_at=? WHERE id=?", [*changes.values(), utcnow(), identity])
        return casebook_preview(get_casebook(db(), identity))

    @app.post("/api/v3/casebooks/{identity}/cases")
    def pin(identity: str, body: CaseInput):
        return casebook_preview(pin_case(db(), identity, body.document_id, body.assessment_id, body.unit_id))

    @app.patch("/api/v3/casebooks/{identity}/cases/{case_id}")
    def edit_case(identity: str, case_id: str, body: CasePatch):
        _book(db(), identity)
        if not db().one("SELECT id FROM v4_casebook_cases WHERE id=? AND casebook_id=?", [case_id, identity]):
            raise HTTPException(404, "Pinned case not found")
        changes = body.model_dump(exclude_none=True)
        if changes:
            db().batch([
                {"sql": "UPDATE v4_casebook_cases SET " + ",".join(key + "=?" for key in changes) + ",updated_at=? WHERE id=? AND casebook_id=?", "params": [*changes.values(), utcnow(), case_id, identity]},
                {"sql": "UPDATE v4_casebooks SET revision=revision+1,updated_at=? WHERE id=?", "params": [utcnow(), identity]},
            ])
        return casebook_preview(get_casebook(db(), identity))

    @app.delete("/api/v3/casebooks/{identity}/cases/{case_id}")
    def remove(identity: str, case_id: str):
        _book(db(), identity)
        if not db().one("SELECT id FROM v4_casebook_cases WHERE id=? AND casebook_id=?", [case_id, identity]):
            return casebook_preview(get_casebook(db(), identity))
        db().batch([
            {"sql": "DELETE FROM v4_casebook_cases WHERE id=? AND casebook_id=?", "params": [case_id, identity]},
            {"sql": "UPDATE v4_casebooks SET revision=revision+1,updated_at=? WHERE id=?", "params": [utcnow(), identity]},
        ])
        return casebook_preview(get_casebook(db(), identity))

    @app.put("/api/v3/casebooks/{identity}/order")
    def reorder(identity: str, body: OrderInput):
        book = _book(db(), identity)
        current = {row["id"] for row in db().query("SELECT id FROM v4_casebook_cases WHERE casebook_id=?", [identity])}
        if len(set(body.case_ids)) != len(body.case_ids) or set(body.case_ids) != current:
            raise HTTPException(422, "Supply every pinned case exactly once")
        result = db().batch([
            {"sql": "UPDATE v4_casebooks SET revision=revision+1,updated_at=? WHERE id=? AND revision=? RETURNING id", "params": [utcnow(), identity, book["revision"]]},
            {"sql": "UPDATE v4_casebook_cases SET ordinal=(SELECT CAST(key AS INTEGER) FROM json_each(?) ordering WHERE ordering.value=v4_casebook_cases.id) WHERE casebook_id=? AND changes()=1", "params": [json.dumps(body.case_ids), identity]},
        ])
        if not result[0]["rows"]:
            raise HTTPException(409, "Casebook changed; reload before reordering")
        return casebook_preview(get_casebook(db(), identity))
