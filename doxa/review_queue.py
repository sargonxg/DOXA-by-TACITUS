"""Bounded, query-scoped review queues; neither inference nor adjudication."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal
from urllib.parse import urlencode

from fastapi import HTTPException, Query

from .verification import COMPARE_FIELDS
from .snapshot_reads import scope_snapshot, validate_source_blocks
from .workspace import Workspace, unpack_assessment, unpack_document


def _cursor(query_id, kind, identity):
    return base64.urlsafe_b64encode(json.dumps({"query": query_id, "kind": "review-" + kind,
                                               "last": identity}).encode()).decode().rstrip("=")


def _summary(row):
    value = unpack_assessment(row)
    return {**{key: value.get(key) for key in ("id", "document_id", "unit_id", "lens_id", "criterion_id",
             "criterion_kind", "result", "review_status", "attribution", "speaker_stance", "document_stance")},
            "assessment_id": value["id"], "title": str(value.get("title") or "Criterion assessment")[:200],
            "source_title": row.get("source_title", ""), "source_author": row.get("source_author", ""),
            "quote": str(value.get("quote") or "")[:1000],
            "explanation": str(value.get("explanation") or "")[:1000]}


def _model_summary(payload):
    return {key: payload.get(key) for key in (*COMPARE_FIELDS, "certainty", "speaker", "proposition")}


def _criteria(config, scope):
    packs = config.get("lens_snapshots") or []
    if not isinstance(packs, list):
        return None
    keys = []
    for pack in packs:
        if scope.get("lens_ids") and pack.get("id") not in scope["lens_ids"]:
            continue
        for criterion in pack.get("criteria", []):
            if scope.get("criterion_ids") and criterion.get("id") not in scope["criterion_ids"]:
                continue
            keys.append(pack["id"] + "/" + criterion["id"])
    return keys if packs else None


def review_queue(store, project_id, query_id, kind, cursor=None, limit=50):
    if kind not in {"uncertainty", "disagreement", "unfinished"}:
        raise HTTPException(422, "Unknown review queue")
    limit = max(1, min(int(limit), 200))
    service = Workspace(store)
    # A SQLite snapshot protects all queue reads from independent writers.
    with scope_snapshot(store, project_id):
        saved = service.require_query(query_id)
        if saved["project_id"] != project_id:
            raise HTTPException(404, "Query does not belong to this project")
        last = service._cursor(cursor, query_id, "review-" + kind)
        scope = saved["scope"]
        run_id = scope.get("run_id")
        run = store.one("SELECT config_json FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
        config = json.loads(run["config_json"] or "{}") if run else {}
        envelope = {"kind": kind, "query_id": query_id, "query_hash": saved["scope_hash"],
                    "snapshot_version": saved["snapshot_version"], "items": [], "total": 0,
                    "next_cursor": None, "interpretation_accuracy": None,
                    "notice": "Review candidates are not independent human judgments or confirmed errors."}
        if not run or not str(config.get("methodology_version", "")).startswith("3"):
            return {**envelope, "scope_note": "Choose a contextual model run to review its recorded units."}
        if kind == "unfinished":
            return _unfinished(store, service, saved, config, last, limit, envelope)
        clause, params, modern = service._scope(project_id, scope, noteworthy=False)
        if not modern:
            return envelope
        table = service._table(True)
        if kind == "uncertainty":
            clause += (" AND (a.result='uncertain' OR (a.result='meets_criterion' AND a.criterion_kind='indicator'"
                       " AND (a.attribution='unknown' OR json_extract(a.payload_json,'$.certainty')"
                       " IN ('ambiguous','insufficient_context'))))")
            count = store.one("SELECT COUNT(*) n FROM " + table + " WHERE " + clause, params)["n"]
            rows = store.query("SELECT a.*,d.title source_title,d.author source_author FROM " + table
                               + " WHERE " + clause + " AND a.id>? ORDER BY a.id LIMIT ?", [*params, last, limit + 1])
            items = [{"type": "assessment", **_summary(row)} for row in rows[:limit]]
            note = "Explicit uncertain outcomes and indicator matches with unresolved attribution or context; all recorded outcomes remain distinct."
        else:
            verifier = store.one("SELECT id,status FROM runs WHERE project_id=? AND json_extract(config_json,'$.verification_of')=? ORDER BY created_at DESC,id DESC LIMIT 1",
                                 [project_id, run_id])
            if not verifier:
                return {**envelope, "scope_note": "No independent verifier run is recorded for this primary run."}
            envelope["verifier_run_id"] = verifier["id"]
            envelope["verifier_status"] = verifier["status"]
            table += (" JOIN v3_assessments v ON v.project_id=a.project_id AND v.document_id=a.document_id"
                      " AND v.unit_id=a.unit_id AND v.lens_id=a.lens_id AND v.criterion_id=a.criterion_id AND v.run_id=?")
            params = [verifier["id"], *params]
            differences = [f"json_extract(a.payload_json,'$.{field}') IS NOT json_extract(v.payload_json,'$.{field}')"
                           for field in COMPARE_FIELDS]
            clause += " AND (" + " OR ".join(differences) + ")"
            count = store.one("SELECT COUNT(*) n FROM " + table + " WHERE " + clause, params)["n"]
            rows = store.query("SELECT a.*,d.title source_title,d.author source_author,v.id verifier_assessment_id,v.payload_json verifier_payload FROM "
                               + table + " WHERE " + clause + " AND a.id>? ORDER BY a.id LIMIT ?", [*params, last, limit + 1])
            items = []
            for row in rows[:limit]:
                primary = json.loads(row["payload_json"])
                other = json.loads(row["verifier_payload"])
                items.append({"type": "disagreement", **_summary(row),
                              "verifier_assessment_id": row["verifier_assessment_id"],
                              "primary_model": _model_summary(primary), "verifier_model": _model_summary(other),
                              "changed_fields": [field for field in COMPARE_FIELDS if primary.get(field) != other.get(field)],
                              "comparison_basis": "original_model_outputs"})
            note = "Paired original-model differences in the most recent verifier subset. Human corrections are shown separately; disagreement does not establish which model is correct."
        return {**envelope, "items": items, "total": count, "scope_note": note,
                "next_cursor": _cursor(query_id, kind, items[-1]["id"]) if len(rows) > limit and items else None}


def _unfinished(store, service, saved, config, last, limit, envelope):
    scope, project_id = saved["scope"], saved["project_id"]
    where, params = service._document_scope(project_id, scope)
    clause = "u.run_id=? AND u.status!='accepted' AND " + " AND ".join(where)
    params = [scope.get("run_id"), *params]
    expected = _criteria(config, scope)
    if expected == []:
        return {**envelope, "scope_note": "The selected framework/criteria were not assessed in this run."}
    if expected is not None and (scope.get("lens_ids") or scope.get("criterion_ids")):
        clause += (" AND EXISTS(SELECT 1 FROM json_each(?) wanted WHERE NOT EXISTS(SELECT 1 FROM v3_assessments done"
                   " WHERE done.run_id=u.run_id AND done.document_id=u.document_id AND done.unit_id=u.unit_id"
                   " AND done.lens_id||'/'||done.criterion_id=wanted.value))")
        params.append(json.dumps(expected))
    table = "v3_unit_statuses u JOIN documents d ON d.id=u.document_id"
    count = store.one("SELECT COUNT(*) n FROM " + table + " WHERE " + clause, params)["n"]
    # Unit IDs are stable within a run. Include document ID in the cursor to
    # avoid relying on uniqueness imposed only by current unit builders.
    identity = "u.document_id||':'||u.unit_id"
    rows = store.query("SELECT u.*,d.title source_title,d.author source_author," + identity + " queue_key FROM "
                       + table + " WHERE " + clause + " AND " + identity + ">? ORDER BY " + identity + " LIMIT ?",
                       [*params, last, limit + 1])
    selected = rows[:limit]
    accepted = {}
    if selected:
        ids = [row["unit_id"] for row in selected]
        for row in store.query("SELECT document_id,unit_id,lens_id,criterion_id FROM v3_assessments WHERE project_id=? AND run_id=? AND unit_id IN (SELECT value FROM json_each(?))",
                               [project_id, scope.get("run_id"), json.dumps(ids)]):
            accepted.setdefault((row["document_id"], row["unit_id"]), set()).add(row["lens_id"] + "/" + row["criterion_id"])
    items = []
    for row in selected:
        payload = json.loads(row["payload_json"] or "{}")
        known = accepted.get((row["document_id"], row["unit_id"]), set())
        missing = [key for key in expected if key not in known] if expected is not None else None
        errors = [{key: str(error[key])[:500] for key in ("code", "criterion_key", "message") if key in error}
                  for error in payload.get("errors", [])[:12] if isinstance(error, dict)]
        items.append({"type": "unfinished", "id": hashlib.sha256(row["queue_key"].encode()).hexdigest()[:32],
                      "document_id": row["document_id"], "unit_id": row["unit_id"], "assessment_id": None,
                      "source_title": row["source_title"], "source_author": row["source_author"],
                      "status": row["status"], "missing_criterion_keys": missing,
                      "accepted_criteria": len(known), "errors": errors,
                      "title": "Unfinished source unit", "result": None, "review_status": "not_assessed",
                      "unit_context_available": True,
                      "unit_context_url": f"/api/v3/projects/{project_id}/unit-context?" + urlencode({
                          "query_id": saved["id"], "document_id": row["document_id"], "unit_id": row["unit_id"]})})
    ignored = [field for field in ("results", "review_status", "speech_roles", "stance") if scope.get(field)]
    note = "Unfinished units retain missing decisions, not negative results. Source, date, framework and criterion filters apply."
    if ignored:
        note += " Outcome, speaker-role, stance and review filters cannot describe unassessed decisions and are not applied here."
    return {**envelope, "items": items, "total": count, "scope_note": note, "unapplied_filters": ignored,
            "next_cursor": _cursor(saved["id"], "unfinished", selected[-1]["queue_key"]) if len(rows) > limit and selected else None}


def unit_context(store, project_id, query_id, document_id, unit_id):
    """Resolve one real frozen unit on demand, without fabricating a finding."""
    from .contextual import build_units
    service = Workspace(store)
    with scope_snapshot(store, project_id):
        saved = service.require_query(query_id)
        if saved["project_id"] != project_id:
            raise HTTPException(404, "Query does not belong to this project")
        where, params = service._document_scope(project_id, saved["scope"])
        row = store.one("SELECT d.* FROM documents d WHERE " + " AND ".join(where) + " AND d.id=?", [*params, document_id])
        run_id = saved["scope"].get("run_id")
        status = store.one("SELECT * FROM v3_unit_statuses WHERE run_id=? AND document_id=? AND unit_id=?", [run_id, document_id, unit_id])
        run = store.one("SELECT config_json FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
        if not row or not run or not status:
            raise HTTPException(404, "Source unit does not belong to this frozen query")
        config = json.loads(run["config_json"])
        expected = next((item.get("sha256") for item in config.get("source_snapshot", []) if item.get("id") == document_id), None)
        if expected != row["sha256"]:
            raise HTTPException(409, "Source version differs from the recorded run")
        document = unpack_document(row)
        metadata = document.get("metadata") or {}
        identity = metadata.get("source_id") or metadata.get("source_identity") or document.get("source_url") or document_id
        document["source_version_id"] = hashlib.sha256(f"{identity}:{row['sha256']}".encode()).hexdigest()[:32]
        blocks = store.get_json(document["blocks_key"])
        block_integrity = validate_source_blocks(document, blocks)
        maximum = config.get("unit_max_chars") or (1200 if config.get("provider") == "ollama" else 4000)
        units = build_units({**document, "blocks": blocks}, max_unit_chars=maximum,
                            context_sentences=config.get("context_sentences", 2), context_policy=config.get("context_policy"))
        unit = next((item for item in units if item["id"] == unit_id), None)
        if unit is None:
            raise HTTPException(409, "The recorded unit cannot be rebuilt from the frozen source and builder")
        wanted = set(unit["block_ids"])
        selected = [block for block in blocks if block["id"] in wanted][:11]
        omitted = len(json.dumps(selected, ensure_ascii=False).encode()) > 128 * 1024
        if omitted:
            selected = []
        return {"document": document, "unit": unit, "unit_status": status["status"],
                "finding": None, "blocks": selected, "total_blocks": len(blocks),
                "block_id": unit["block_id"], "query_id": query_id, "scope_hash": saved["scope_hash"],
                "context_scope": "Complete owned unit segments and bounded neighbouring sentences from the original source; no model interpretation.",
                "large_blocks_omitted": omitted, "source_block_integrity": block_integrity,
                "notice": "Unit text is source-derived. An unfinished unit is not a negative or uncertain model verdict."}


def register_review_queue(app, services):
    if getattr(app.state, "review_queue_registered", False):
        return
    app.state.review_queue_registered = True

    @app.get("/api/v3/projects/{project_id}/review-queue")
    def queue(project_id: str, query_id: str = Query(min_length=1, max_length=128),
              kind: Literal["uncertainty", "disagreement", "unfinished"] = "uncertainty",
              cursor: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
        services["require_project"](project_id)
        return review_queue(services["db"](), project_id, query_id, kind, cursor, limit)

    @app.get("/api/v3/projects/{project_id}/unit-context")
    def source_unit(project_id: str, query_id: str = Query(min_length=1, max_length=128),
                    document_id: str = Query(min_length=1, max_length=128),
                    unit_id: str = Query(min_length=1, max_length=128)):
        services["require_project"](project_id)
        return unit_context(services["db"](), project_id, query_id, document_id, unit_id)
