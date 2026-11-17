"""Human-adjudicated project memory and explicit, auditable candidate activation."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import get_args

from .models import AssessmentResult, PropositionStance
from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v3_review_events (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,assessment_id TEXT NOT NULL,
 actor_kind TEXT NOT NULL,actor_id TEXT NOT NULL,adjudicated INTEGER NOT NULL DEFAULT 0,
 corrections_json TEXT NOT NULL,resolved_json TEXT NOT NULL,previous_review_id TEXT,
 note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v3_review_events_assessment ON v3_review_events(assessment_id,created_at);
CREATE TABLE IF NOT EXISTS v3_learning_examples (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,review_id TEXT NOT NULL,assessment_id TEXT NOT NULL,
 document_id TEXT NOT NULL,criterion_id TEXT NOT NULL,language TEXT NOT NULL,story_group TEXT NOT NULL,
 payload_json TEXT NOT NULL,content_sha256 TEXT NOT NULL,approved_by TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(project_id,review_id)
);
CREATE INDEX IF NOT EXISTS v3_learning_examples_lookup ON v3_learning_examples(project_id,criterion_id,language);
CREATE TABLE IF NOT EXISTS v3_learning_snapshots (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,payload_json TEXT NOT NULL,
 created_by TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_learning_snapshot_items (
 snapshot_id TEXT NOT NULL,example_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id,example_id)
);
CREATE TABLE IF NOT EXISTS v3_learning_candidates (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,baseline_snapshot_id TEXT,
 status TEXT NOT NULL DEFAULT 'draft',created_by TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_learning_reports (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,candidate_id TEXT NOT NULL,
 baseline_run_id TEXT NOT NULL,candidate_run_id TEXT NOT NULL,payload_json TEXT NOT NULL,
 eligible INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_learning_activations (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,candidate_id TEXT,previous_candidate_id TEXT,
 snapshot_id TEXT,actor_id TEXT NOT NULL,action TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_learning_active (
 project_id TEXT PRIMARY KEY,candidate_id TEXT,snapshot_id TEXT,activation_id TEXT,updated_at TEXT NOT NULL
);
"""


RESULTS = set(get_args(AssessmentResult))
STANCES = set(get_args(PropositionStance))
ATTRIBUTIONS = {"author", "quoted_speaker", "reported_speaker", "unknown"}
ROLE_FOR_ATTRIBUTION = {"author": "author_assertion", "quoted_speaker": "attributed_quotation",
                        "reported_speaker": "reported_speech", "unknown": "unknown"}
MAX_EXAMPLE_CHARS = 6000
MAX_SNAPSHOT_EXAMPLES = 1000
MAX_REQUEST_BYTES = 4800


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _row(store, table, identity, project_id):
    if table not in {"v3_assessments", "v3_review_events", "v3_learning_snapshots",
                     "v3_learning_candidates", "v3_learning_reports", "runs"}:
        raise ValueError("Unsupported scoped record")
    row = store.one(f"SELECT * FROM {table} WHERE id=? AND project_id=?", [identity, project_id])
    if not row:
        raise ValueError("Record not found in this project")
    return row


def _human(actor_kind, actor_id):
    if actor_kind != "human" or not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("An explicitly identified human operator must approve this action")


def _metadata(document):
    value = document.get("metadata") or document.get("metadata_json") or {}
    return json.loads(value) if isinstance(value, str) else value


def _group(document):
    metadata = _metadata(document)
    for key in ("story_group_id", "story_group", "duplicate_group", "duplicate_cluster", "body_sha256"):
        if metadata.get(key):
            return str(metadata[key])
    return str(document.get("sha256") or document["id"])


def _holdout(document):
    metadata = _metadata(document)
    split = str(metadata.get("split") or metadata.get("dataset_split") or metadata.get("corpus_split") or "").lower()
    return metadata.get("holdout") is True or split in {"holdout", "held_out", "test", "validation"}


def _all_documents(store, project_id):
    offset = 0
    while True:
        rows = store.query("SELECT id,sha256,metadata_json FROM documents WHERE project_id=? ORDER BY id LIMIT 500 OFFSET ?",
                           [project_id, offset])
        yield from rows
        if len(rows) < 500:
            break
        offset += len(rows)


def _validate_evidence(store, assessment, resolved):
    document = store.one("SELECT blocks_key FROM documents WHERE id=? AND project_id=?",
                         [assessment["document_id"], assessment["project_id"]])
    if not document or not document.get("blocks_key"):
        raise ValueError("Source blocks are required to review evidence")
    blocks = {block["id"]: block["text"] for block in store.get_json(document["blocks_key"])}
    refs = resolved.get("evidence_refs") or []
    if not isinstance(refs, list) or len(refs) > 8:
        raise ValueError("Evidence references must be a list with no more than eight spans")
    check = list(refs)
    if resolved.get("quote"):
        check.append({"block_id": resolved.get("block_id"), "start": resolved.get("start"),
                      "end": resolved.get("end"), "quote": resolved["quote"]})
    for ref in check:
        if ref.get("document_id") not in {None, assessment["document_id"]}:
            raise ValueError("Review evidence must remain in the assessed source document")
        text = blocks.get(ref.get("block_id"))
        start, end = ref.get("start"), ref.get("end")
        quote = ref.get("quote", ref.get("text"))
        if not isinstance(text, str) or not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(text)) or text[start:end] != quote:
            raise ValueError("Review evidence does not match an exact persisted source span")


def record_review(store, project_id, assessment_id, corrections, *, actor_kind, actor_id, note="", adjudicated=False):
    """Append an immutable event; only trusted human context can apply corrections.

    The HTTP adapter must derive actor_kind/id from its operator/authentication
    context, never from untrusted model output or a request body's claimed role.
    """
    if actor_kind not in {"human", "agent"} or not isinstance(actor_id, str) or not actor_id.strip():
        raise ValueError("A reviewer identity and kind are required")
    if adjudicated and actor_kind != "human":
        raise ValueError("Agents cannot mark a review human-adjudicated")
    if not isinstance(corrections, dict) or len(_json(corrections)) > 32000:
        raise ValueError("Structured corrections must be a bounded object")
    allowed = {"result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical",
               "speaker", "target", "proposition", "title", "explanation", "alternative",
               "quote", "block_id", "start", "end", "context", "evidence_refs"}
    if set(corrections) - allowed:
        raise ValueError("Corrections cannot change source identity, criterion or run scope")
    if len(note) > 12000:
        raise ValueError("Review note exceeds 12000 characters")
    assessment = _row(store, "v3_assessments", assessment_id, project_id)
    original = json.loads(assessment["payload_json"])
    current_review = json.loads(assessment.get("review_json") or "{}")
    current = dict(current_review.get("resolved") or original)
    resolved = {**current, **corrections}
    if resolved.get("result", assessment["result"]) not in RESULTS:
        raise ValueError("Unknown explicit assessment result")
    for name in ("speaker_stance", "document_stance"):
        if resolved.get(name, "unclear") not in STANCES:
            raise ValueError("Unknown proposition stance")
    attribution = resolved.get("attribution", "unknown")
    if attribution not in ATTRIBUTIONS:
        raise ValueError("Unknown attribution")
    if "speech_role" in corrections and "attribution" not in corrections:
        inverse = {role: key for key, role in ROLE_FOR_ATTRIBUTION.items()}
        if corrections["speech_role"] not in inverse:
            raise ValueError("Unknown speech role")
        resolved["attribution"] = attribution = inverse[corrections["speech_role"]]
    expected_role = ROLE_FOR_ATTRIBUTION[attribution]
    if "speech_role" in corrections and resolved["speech_role"] != expected_role:
        raise ValueError("Speech role and attribution disagree")
    resolved["speech_role"] = expected_role
    if not isinstance(resolved.get("hypothetical", False), bool):
        raise ValueError("Hypothetical must be an explicit boolean")
    _validate_evidence(store, assessment, resolved)
    review_id = uuid.uuid4().hex
    timestamp = utcnow()
    event = {"id": review_id, "project_id": project_id, "assessment_id": assessment_id,
             "actor_kind": actor_kind, "actor_id": actor_id, "adjudicated": bool(adjudicated),
             "corrections": corrections, "resolved": resolved,
             "previous_review_id": current_review.get("review_id"), "note": note, "created_at": timestamp}
    statements = [{"sql": "INSERT INTO v3_review_events(id,project_id,assessment_id,actor_kind,actor_id,adjudicated,corrections_json,resolved_json,previous_review_id,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   "params": [review_id, project_id, assessment_id, actor_kind, actor_id, int(adjudicated),
                              _json(corrections), _json(resolved), event["previous_review_id"], note, timestamp]}]
    if actor_kind == "human":
        status = "adjudicated" if adjudicated else "reviewed"
        projection = {"review_id": review_id, "actor_kind": "human", "actor_id": actor_id,
                      "adjudicated": bool(adjudicated), "corrections": corrections,
                      "resolved": resolved, "created_at": timestamp}
        statements.append({"sql": "UPDATE v3_assessments SET result=?,attribution=?,speech_role=?,speaker_stance=?,document_stance=?,hypothetical=?,review_status=?,review_note=?,review_json=? WHERE id=? AND project_id=?",
                           "params": [resolved.get("result", assessment["result"]), resolved.get("attribution", "unknown"),
                                      resolved["speech_role"], resolved.get("speaker_stance", "unclear"),
                                      resolved.get("document_stance", "unclear"), int(resolved.get("hypothetical", False)),
                                      status, note, _json(projection), assessment_id, project_id]})
    store.batch(statements)
    return {**event, "state_applied": actor_kind == "human"}


def approve_example(store, project_id, review_id, *, actor_kind, actor_id):
    _human(actor_kind, actor_id)
    review = _row(store, "v3_review_events", review_id, project_id)
    if review["actor_kind"] != "human" or not review["adjudicated"]:
        raise ValueError("Only a human-adjudicated review can become an approved example")
    assessment = _row(store, "v3_assessments", review["assessment_id"], project_id)
    current = json.loads(assessment.get("review_json") or "{}")
    if current.get("review_id") != review_id:
        raise ValueError("Approve the latest human review, not an obsolete correction")
    document = store.one("SELECT * FROM documents WHERE id=? AND project_id=?", [assessment["document_id"], project_id])
    if not document:
        raise ValueError("Source document is unavailable")
    group = _group(document)
    if _holdout(document) or any(_holdout(d) and _group(d) == group for d in _all_documents(store, project_id)):
        raise ValueError("Holdout documents and their story/duplicate groups cannot enter learning memory")
    resolved = json.loads(review["resolved_json"])
    if not resolved.get("quote") and not resolved.get("evidence_refs"):
        raise ValueError("An approved learning example needs an exact reviewed source span")
    criterion = resolved.get("criterion_key") or f"{assessment['lens_id']}/{assessment['criterion_id']}"
    metadata = _metadata(document)
    language = str(metadata.get("language") or "en")
    example = {key: resolved[key] for key in (
        "result", "quote", "block_id", "start", "end", "context", "evidence_refs", "target", "proposition",
        "speaker", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical",
        "explanation", "alternative", "criterion_kind") if key in resolved}
    example.update({"document_id": document["id"], "story_group_id": group, "criterion_key": criterion,
                    "language": language, "source_url": document.get("source_url", ""), "review_id": review_id,
                    "source_hash": metadata.get("body_sha256") or metadata.get("source_sha256") or document.get("sha256")})
    if len(_json(example)) > MAX_EXAMPLE_CHARS:
        raise ValueError("Approved example exceeds the bounded prompt budget; select shorter exact evidence in a new human review")
    identity = _hash([project_id, review_id])[:32]
    payload_hash = _hash(example)
    store.execute("INSERT INTO v3_learning_examples(id,project_id,review_id,assessment_id,document_id,criterion_id,language,story_group,payload_json,content_sha256,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,review_id) DO NOTHING",
                  [identity, project_id, review_id, assessment["id"], document["id"], criterion, language,
                   group, _json(example), payload_hash, actor_id, utcnow()])
    return {"id": identity, **example, "content_sha256": payload_hash}


def create_snapshot(store, project_id, *, actor_kind, actor_id):
    _human(actor_kind, actor_id)
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise ValueError("Project not found")
    approved = store.query("SELECT e.id,e.content_sha256,e.review_id,a.review_json FROM v3_learning_examples e JOIN v3_assessments a ON a.id=e.assessment_id WHERE e.project_id=? ORDER BY e.id", [project_id])
    examples = []
    for example in approved:
        latest = json.loads(example["review_json"] or "{}")
        if latest.get("review_id") == example["review_id"] and latest.get("adjudicated"):
            examples.append({"id": example["id"], "content_sha256": example["content_sha256"]})
    if len(examples) > MAX_SNAPSHOT_EXAMPLES:
        raise ValueError("Snapshot exceeds the 1000-example memory bound")
    payload = {"project_id": project_id, "examples": examples, "format_version": "3.0"}
    digest = _hash(payload)
    identity = digest[:32]
    timestamp = utcnow()
    statements = [{"sql": "INSERT INTO v3_learning_snapshots(id,project_id,content_sha256,payload_json,created_by,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   "params": [identity, project_id, digest, _json(payload), actor_id, timestamp]}]
    statements.extend({"sql": "INSERT INTO v3_learning_snapshot_items(snapshot_id,example_id,ordinal) VALUES(?,?,?) ON CONFLICT(snapshot_id,example_id) DO NOTHING",
                       "params": [identity, example["id"], index]} for index, example in enumerate(examples))
    for offset in range(0, len(statements), 50):
        store.batch(statements[offset:offset + 50])
    return {"id": identity, "project_id": project_id, "content_sha256": digest, "example_count": len(examples),
            "created_by": actor_id, "created_at": timestamp}


def select_examples(store, project_id, snapshot_id, criterion_id, language="en", exclude_document_ids=None,
                    exclude_story_groups=None, limit=4, source_text=""):
    if not isinstance(limit, int) or not 0 <= limit <= 4:
        raise ValueError("A request can retrieve no more than four approved examples")
    if not snapshot_id or limit == 0:
        return []
    snapshot = _row(store, "v3_learning_snapshots", snapshot_id, project_id)
    manifest = json.loads(snapshot["payload_json"])
    if _hash(manifest) != snapshot["content_sha256"]:
        raise ValueError("Learning snapshot failed its immutable content checksum")
    expected = {item["id"]: item["content_sha256"] for item in manifest["examples"]}
    membership = store.query("SELECT e.id,e.content_sha256 FROM v3_learning_snapshot_items s JOIN v3_learning_examples e ON e.id=s.example_id WHERE s.snapshot_id=? AND e.project_id=?", [snapshot_id, project_id])
    if {item["id"]: item["content_sha256"] for item in membership} != expected:
        raise ValueError("Learning snapshot membership is incomplete or changed")
    excluded_documents = set(exclude_document_ids or [])
    excluded_groups = set(exclude_story_groups or [])
    for document_id in excluded_documents:
        document = store.one("SELECT id,sha256,metadata_json FROM documents WHERE id=? AND project_id=?", [document_id, project_id])
        if document:
            excluded_groups.add(_group(document))
    rows = store.query("SELECT e.* FROM v3_learning_snapshot_items s JOIN v3_learning_examples e ON e.id=s.example_id WHERE s.snapshot_id=? AND e.project_id=? AND e.criterion_id=? AND e.language=? ORDER BY e.content_sha256,e.id",
                       [snapshot_id, project_id, criterion_id, language])
    terms = set(re.findall(r"\b[\w-]{3,}\b", source_text.lower()))
    if terms:
        def relevance(row):
            payload = json.loads(row["payload_json"])
            text = " ".join(str(payload.get(key, "")) for key in ("quote", "context", "proposition", "explanation"))
            words = set(re.findall(r"\b[\w-]{3,}\b", text.lower()))
            overlap = len(terms & words) / math.sqrt(max(1, len(terms) * len(words)))
            return (-overlap, row["content_sha256"], row["id"])
        rows.sort(key=relevance)
    selected, seen_groups = [], set(excluded_groups)
    for row in rows:
        if row["document_id"] in excluded_documents or row["story_group"] in seen_groups:
            continue
        payload = json.loads(row["payload_json"])
        if _hash(payload) != row["content_sha256"]:
            raise ValueError("Approved memory example failed its immutable content checksum")
        prompt_fields = {key: payload[key] for key in (
            "criterion_key", "language", "document_id", "source_hash", "story_group_id", "result", "quote", "context",
            "proposition", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical",
            "explanation", "alternative") if key in payload}
        example = {"example_id": row["id"], "snapshot_id": snapshot_id, **prompt_fields}
        if len(_json([*selected, example]).encode()) > MAX_REQUEST_BYTES:
            continue
        selected.append(example)
        seen_groups.add(row["story_group"])
        if len(selected) == limit:
            break
    return selected


def create_candidate(store, project_id, snapshot_id, *, actor_kind, actor_id):
    _human(actor_kind, actor_id)
    _row(store, "v3_learning_snapshots", snapshot_id, project_id)
    active = store.one("SELECT * FROM v3_learning_active WHERE project_id=?", [project_id]) or {}
    candidate = {"id": uuid.uuid4().hex, "project_id": project_id, "snapshot_id": snapshot_id,
                 "baseline_snapshot_id": active.get("snapshot_id"), "status": "draft",
                 "created_by": actor_id, "created_at": utcnow()}
    store.execute("INSERT INTO v3_learning_candidates(id,project_id,snapshot_id,baseline_snapshot_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  list(candidate.values()))
    return candidate


def candidate_report(store, project_id, candidate_id, baseline_run_id, candidate_run_id):
    candidate = _row(store, "v3_learning_candidates", candidate_id, project_id)
    baseline = _row(store, "runs", baseline_run_id, project_id)
    challenger = _row(store, "runs", candidate_run_id, project_id)
    before, after = json.loads(baseline["config_json"]), json.loads(challenger["config_json"])
    before_sources, after_sources = before.get("source_snapshot"), after.get("source_snapshot")
    reasons = []
    if baseline_run_id == candidate_run_id:
        reasons.append("A paired comparison requires two distinct runs")
    if not before_sources or before_sources != after_sources:
        reasons.append("Runs do not contain the same frozen source snapshot")
    if before.get("learning_snapshot_id") != candidate.get("baseline_snapshot_id"):
        reasons.append("Baseline run did not pin the candidate's baseline memory")
    if after.get("learning_snapshot_id") != candidate["snapshot_id"]:
        reasons.append("Candidate run did not pin the candidate memory")
    operational = {"learning_snapshot_id", "approved_examples", "approved_example_ids", "profile_hash", "analysis_profile_hash",
                   "document_ids", "source_snapshot", "budget_usd", "monthly_budget_usd"}
    if {key: value for key, value in before.items() if key not in operational} != {key: value for key, value in after.items() if key not in operational}:
        reasons.append("Model or analytical settings differ beyond memory")
    if baseline["status"] != "completed" or challenger["status"] != "completed":
        reasons.append("Both paired runs must finish completely")
    for selected in (baseline_run_id, candidate_run_id):
        coverage = store.one("SELECT COUNT(*) AS total,SUM(CASE WHEN status='accepted' THEN 0 ELSE 1 END) AS incomplete FROM v3_unit_statuses WHERE run_id=?", [selected])
        if not coverage["total"] or coverage["incomplete"]:
            reasons.append("Both paired runs need complete accepted-unit coverage")
            break
    before_profile, after_profile = json.loads(baseline.get("model_profile_json") or "{}"), json.loads(challenger.get("model_profile_json") or "{}")
    for key in ("revision", "system_fingerprint"):
        if before_profile.get(key) not in {None, "unresolved"} and after_profile.get(key) != before_profile[key]:
            reasons.append("Provider model revision or fingerprint changed")
            break
    source_ids = {item.get("id") for item in (after_sources or []) if isinstance(item, dict)}
    groups = {_group(doc) for doc in _all_documents(store, project_id) if doc["id"] in source_ids}
    examples = store.query("SELECT e.document_id,e.story_group FROM v3_learning_snapshot_items s JOIN v3_learning_examples e ON e.id=s.example_id WHERE s.snapshot_id=?", [candidate["snapshot_id"]])
    if any(example["document_id"] in source_ids or example["story_group"] in groups for example in examples):
        reasons.append("Evaluation sources overlap a learning example or story group")
    left = store.query("SELECT unit_id,document_id,lens_id,criterion_id,payload_json FROM v3_assessments WHERE project_id=? AND run_id=?", [project_id, baseline_run_id])
    right = store.query("SELECT unit_id,document_id,lens_id,criterion_id,payload_json FROM v3_assessments WHERE project_id=? AND run_id=?", [project_id, candidate_run_id])
    def decisions(rows):
        return {(r["document_id"], r["unit_id"], r["lens_id"], r["criterion_id"]): json.loads(r["payload_json"]) for r in rows}
    first, second = decisions(left), decisions(right)
    shared = sorted(set(first) & set(second))
    if not shared or set(first) != set(second):
        reasons.append("Paired assessment scopes differ or are empty")
    changed = sum(first[key].get("result") != second[key].get("result") for key in shared)
    report = {"candidate_id": candidate_id, "baseline_run_id": baseline_run_id, "candidate_run_id": candidate_run_id,
              "dataset_sha256": _hash(after_sources), "paired_assessments": len(shared), "changed_results": changed,
              "agreement": (len(shared) - changed) / len(shared) if shared else None,
              "interpretation_accuracy": None, "independent_reference_labels": False,
              "eligible_for_activation": not reasons, "blocking_reasons": reasons,
              "notice": "Agreement and complete coverage do not establish better interpretation. Activation requires explicit human approval.",
              "created_at": utcnow()}
    identity = _hash(report)[:32]
    store.execute("INSERT INTO v3_learning_reports(id,project_id,candidate_id,baseline_run_id,candidate_run_id,payload_json,eligible,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  [identity, project_id, candidate_id, baseline_run_id, candidate_run_id, _json(report), int(not reasons), report["created_at"]])
    store.execute("UPDATE v3_learning_candidates SET status='evaluated' WHERE id=? AND project_id=?", [candidate_id, project_id])
    return {"id": identity, **report}


def _activate(store, project_id, candidate_id, snapshot_id, actor_id, action, note):
    previous = store.one("SELECT * FROM v3_learning_active WHERE project_id=?", [project_id]) or {}
    event = {"id": uuid.uuid4().hex, "project_id": project_id, "candidate_id": candidate_id,
             "previous_candidate_id": previous.get("candidate_id"), "snapshot_id": snapshot_id,
             "actor_id": actor_id, "action": action, "note": note, "created_at": utcnow()}
    store.batch([
        {"sql": "INSERT INTO v3_learning_activations(id,project_id,candidate_id,previous_candidate_id,snapshot_id,actor_id,action,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)", "params": list(event.values())},
        {"sql": "INSERT INTO v3_learning_active(project_id,candidate_id,snapshot_id,activation_id,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET candidate_id=excluded.candidate_id,snapshot_id=excluded.snapshot_id,activation_id=excluded.activation_id,updated_at=excluded.updated_at",
         "params": [project_id, candidate_id, snapshot_id, event["id"], event["created_at"]]},
    ])
    return event


def activate_candidate(store, project_id, candidate_id, report_id, *, actor_kind, actor_id, note=""):
    _human(actor_kind, actor_id)
    candidate = _row(store, "v3_learning_candidates", candidate_id, project_id)
    report = _row(store, "v3_learning_reports", report_id, project_id)
    if report["candidate_id"] != candidate_id or not report["eligible"]:
        raise ValueError("A complete, scope-matched paired report is required before activation")
    active = store.one("SELECT snapshot_id FROM v3_learning_active WHERE project_id=?", [project_id]) or {}
    if active.get("snapshot_id") != candidate.get("baseline_snapshot_id"):
        raise ValueError("Active memory changed after this candidate was created; create a new candidate")
    event = _activate(store, project_id, candidate_id, candidate["snapshot_id"], actor_id, "activate", note)
    store.execute("UPDATE v3_learning_candidates SET status='activated' WHERE id=? AND project_id=?", [candidate_id, project_id])
    return event


def rollback_candidate(store, project_id, target_candidate_id=None, *, actor_kind, actor_id, note=""):
    _human(actor_kind, actor_id)
    if target_candidate_id:
        candidate = _row(store, "v3_learning_candidates", target_candidate_id, project_id)
        prior = store.one("SELECT id FROM v3_learning_activations WHERE project_id=? AND candidate_id=? AND action='activate' LIMIT 1", [project_id, target_candidate_id])
        if not prior:
            raise ValueError("Rollback target must have been explicitly activated previously")
        snapshot_id = candidate["snapshot_id"]
    else:
        snapshot_id = None
    return _activate(store, project_id, target_candidate_id, snapshot_id, actor_id, "rollback", note)


def learning_overview(store, project_id):
    active = store.one("SELECT * FROM v3_learning_active WHERE project_id=?", [project_id])
    snapshots = store.query("SELECT id,content_sha256,created_by,created_at FROM v3_learning_snapshots WHERE project_id=? ORDER BY created_at,id", [project_id])
    candidates = store.query("SELECT * FROM v3_learning_candidates WHERE project_id=? ORDER BY created_at,id", [project_id])
    reports = []
    for row in store.query("SELECT id,payload_json FROM v3_learning_reports WHERE project_id=? ORDER BY created_at DESC,id LIMIT 200", [project_id]):
        reports.append({"id": row["id"], **json.loads(row["payload_json"])})
    activations = store.query("SELECT * FROM v3_learning_activations WHERE project_id=? ORDER BY created_at DESC,id LIMIT 200", [project_id])
    counts = store.one("SELECT COUNT(*) AS total FROM v3_learning_examples WHERE project_id=?", [project_id]) or {"total": 0}
    return {"project_id": project_id, "active": active, "snapshots": snapshots, "candidates": candidates,
            "reports": reports, "activations": activations,
            "approved_examples": counts["total"], "max_examples_per_request": 4,
            "actor_provenance": "Reviewer identity is supplied by the server. A local-operator identity is self-attested, not independent proof of a person's identity.",
            "notice": "Approved retrieval examples affect newly pinned runs only. No model weights are automatically trained."}


get_learning_overview = learning_overview
build_candidate_report = candidate_report
