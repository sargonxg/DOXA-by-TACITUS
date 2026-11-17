"""Optional append-only semantic annotations, separate from model assessments."""
from __future__ import annotations

import json
import uuid

from .lineage import content_sha256, lineage_record
from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v4_semantic_overlays(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,assessment_id TEXT NOT NULL,
 actor_id TEXT NOT NULL,actor_kind TEXT NOT NULL,version TEXT NOT NULL,
 payload_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v4_overlay_scope ON v4_semantic_overlays(project_id,assessment_id,created_at);
"""
EVIDENCE_FUNCTIONS = {"NOT_ASSESSED", "definition", "reported_incident", "example_of_claim", "counter_speech", "policy_argument", "other", "uncertain"}
PROPOSITION_TYPES = {"NOT_ASSESSED", "empirical_claim", "normative_claim", "policy_proposal", "definition", "interpretation", "uncertain"}
FIELDS = {"evidence_function", "proposition_type", "attribution_chain", "missing_context", "note"}


def ensure_schema(store):
    if getattr(store, "_v4_overlay_schema_ready", False):
        return
    if store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='v4_semantic_overlays'"):
        store._v4_overlay_schema_ready = True
        return
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            store.execute(statement)
    store._v4_overlay_schema_ready = True


def default_overlay():
    return {"status": "NOT_ASSESSED", "evidence_function": "NOT_ASSESSED", "proposition_type": "NOT_ASSESSED",
            "attribution_chain": None, "missing_context": None, "note": "", "origin": "not_assessed"}


def record_overlay(store, project_id, assessment_id, fields, *, actor_id, actor_kind, version="0.4"):
    ensure_schema(store)
    row = store.one("SELECT payload_json FROM v3_assessments WHERE id=? AND project_id=?", [assessment_id, project_id])
    if not row:
        raise ValueError("Assessment not found in this project")
    if actor_kind not in {"human", "agent"} or not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("An identified human or agent annotation actor is required")
    if not isinstance(version, str) or not 1 <= len(version) <= 80:
        raise ValueError("A bounded overlay version is required")
    if not isinstance(fields, dict) or not fields or set(fields) - FIELDS:
        raise ValueError("Only the optional semantic overlay fields can be annotated")
    payload = {**default_overlay(), **fields, "status": "annotated", "origin": "editorial_annotation"}
    if payload["evidence_function"] not in EVIDENCE_FUNCTIONS or payload["proposition_type"] not in PROPOSITION_TYPES:
        raise ValueError("Unknown evidence function or proposition type")
    chain = payload["attribution_chain"]
    if chain is not None:
        if not isinstance(chain, list) or len(chain) > 8:
            raise ValueError("Attribution chain must contain at most eight explicit source-grounded hops")
        for hop in chain:
            if not isinstance(hop, dict) or set(hop) - {"speaker", "role", "sentence_ids", "uncertain"}:
                raise ValueError("Attribution hops require explicit speaker/role/source sentence IDs")
            if not isinstance(hop.get("speaker"), str) or len(hop["speaker"]) > 240 or hop.get("role") not in {"author", "quoted_speaker", "reported_speaker", "unknown"}:
                raise ValueError("Invalid attribution hop")
            if not isinstance(hop.get("sentence_ids"), list) or len(hop["sentence_ids"]) > 16 or any(not isinstance(item, str) or len(item) > 128 for item in hop["sentence_ids"]):
                raise ValueError("Attribution hops need bounded sentence identifiers")
            if "uncertain" in hop and type(hop["uncertain"]) is not bool:
                raise ValueError("Attribution uncertainty must be Boolean")
    missing = payload["missing_context"]
    if missing is not None and (not isinstance(missing, list) or len(missing) > 12 or any(not isinstance(item, str) or len(item) > 500 for item in missing)):
        raise ValueError("Missing context must be a bounded explicit list")
    if not isinstance(payload["note"], str) or len(payload["note"]) > 8000:
        raise ValueError("Overlay note exceeds 8000 characters")
    original = json.loads(row["payload_json"])
    # Chains are optional interpretations, but their supplied evidence IDs must
    # still resolve to the original assessment's frozen citation references.
    allowed_ids = {ref.get("sentence_id") for ref in original.get("evidence_refs", [])}
    if chain and any(identity not in allowed_ids for hop in chain for identity in hop["sentence_ids"]):
        raise ValueError("Attribution chain refers outside the frozen assessment evidence")
    now, identity = utcnow(), uuid.uuid4().hex
    payload.update({"id": identity, "assessment_id": assessment_id, "actor_id": actor_id,
                    "actor_kind": actor_kind, "version": version, "created_at": now,
                    "original_assessment_sha256": content_sha256(original)})
    payload["lineage"] = lineage_record("editorial_annotation", payload_sha256=content_sha256(payload),
        parents=[{"kind": "persisted_assessment", "id": assessment_id, "sha256": content_sha256(original)}],
        actor={"id": actor_id, "kind": actor_kind}, recorded_at=now, metadata={"overlay_version": version})
    store.execute("INSERT INTO v4_semantic_overlays(id,project_id,assessment_id,actor_id,actor_kind,version,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  [identity, project_id, assessment_id, actor_id, actor_kind, version,
                   json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False), now])
    return payload


def assessment_overlays(store, project_id, assessment_id):
    ensure_schema(store)
    if not store.one("SELECT id FROM v3_assessments WHERE id=? AND project_id=?", [assessment_id, project_id]):
        raise ValueError("Assessment not found in this project")
    rows = store.query("SELECT payload_json FROM v4_semantic_overlays WHERE project_id=? AND assessment_id=? ORDER BY created_at,id", [project_id, assessment_id])
    return {"assessment_id": assessment_id, "default": default_overlay(),
            "annotations": [json.loads(row["payload_json"]) for row in rows],
            "original_assessment_unchanged": True}
