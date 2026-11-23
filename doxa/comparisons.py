"""Frozen, operator-declared matched cohorts; no inferred motives or prevalence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException

from .store import utcnow

VERSION = "matched-cohorts-1.1"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matched_comparisons (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS matched_comparisons_project ON matched_comparisons(project_id,created_at);
"""
OUTCOMES = ("meets_criterion", "does_not_meet", "not_applicable", "uncertain")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def ensure_schema(store):
    if store.remote:
        raise ValueError("Matched comparisons currently require native storage")
    store.batch([{"sql": s.strip(), "params": []} for s in SCHEMA_SQL.split(";") if s.strip()])


def _selection(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 500
            or any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in value)
            or len(set(value)) != len(value)):
        raise ValueError("Each cohort needs 1–500 distinct document IDs")
    return value


def _maximum_pairs(candidates):
    """Start with nearest dates, then augment until no more sources can pair."""
    neighbors = defaultdict(list)
    by_left, by_right = {}, {}
    for distance, left, right in sorted(candidates):
        neighbors[left].append((distance, right))
        if left not in by_left and right not in by_right:
            by_left[left] = (distance, right)
            by_right[right] = left

    def augment(left, seen):
        for distance, right in neighbors[left]:
            if right in seen:
                continue
            seen.add(right)
            previous = by_right.get(right)
            if previous is None or augment(previous, seen):
                by_left[left] = (distance, right)
                by_right[right] = left
                return True
        return False

    for left in sorted(neighbors):
        if left not in by_left:
            augment(left, set())
    return sorted((distance, left, right) for left, (distance, right) in by_left.items())


def build_comparison(store, project_id, body, *, actor_id="local-operator (self-declared)"):
    from .privacy_lifecycle import activity
    with activity(store, project_id, "matched_comparison"):
        return _build_comparison(store, project_id, body, actor_id=actor_id)


def _build_comparison(store, project_id, body, *, actor_id):
    ensure_schema(store)
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise ValueError("Project does not exist")
    if not isinstance(body, dict):
        raise ValueError("A comparison specification is required")
    left, right = _selection(body.get("left_document_ids")), _selection(body.get("right_document_ids"))
    if set(left) & set(right):
        raise ValueError("A source cannot occur in both cohorts")
    fields = body.get("matching_fields", ["event", "topic", "genre"])
    if (not isinstance(fields, list) or not fields or len(set(fields)) != len(fields)
            or any(v not in {"event", "topic", "genre"} for v in fields)):
        raise ValueError("Match on a nonempty selection of event, topic and genre")
    gap = body.get("max_date_gap_days", 7)
    if type(gap) is not int or not 0 <= gap <= 366:
        raise ValueError("Maximum publication-date gap must be 0–366 days")
    question = body.get("question", "How do matched sources frame the declared issue?")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("A comparison question of at most 4000 characters is required")
    annotations = body.get("per_document", {})
    if not isinstance(annotations, dict) or set(annotations) - set(left + right):
        raise ValueError("Matching annotations must refer only to selected sources")
    allowed = {"event", "topic", "genre", "published_at", "source_family", "story_group"}
    for values in annotations.values():
        if (not isinstance(values, dict) or set(values) - allowed
                or any(not isinstance(v, str) or len(v) > 500 for v in values.values())):
            raise ValueError("Matching annotations must be bounded textual metadata")
    rows = store.query("SELECT id,title,sha256,published_at,document_type,institution,source_url,metadata_json,status "
                       "FROM documents WHERE project_id=? AND id IN (SELECT value FROM json_each(?))",
                       [project_id, json.dumps(left + right)])
    if {r["id"] for r in rows} != set(left + right):
        raise ValueError("Selection contains unavailable project sources")
    metadata, excluded = {}, []
    for row in rows:
        source = json.loads(row["metadata_json"] or "{}")
        declared = annotations.get(row["id"], {})
        values = {key: str(declared.get(key, source.get(key, "")) or "").strip() for key in allowed}
        values["genre"] = values["genre"] or row["document_type"]
        values["published_at"] = values["published_at"] or row["published_at"] or ""
        values["source_family"] = values["source_family"] or row["institution"] or urlsplit(row["source_url"]).hostname or ""
        missing = [key for key in fields if not values[key]]
        try:
            day = date.fromisoformat(values["published_at"][:10])
        except (ValueError, TypeError):
            day = None
        reason = "source_not_ready" if row["status"] != "ready" else "missing_matching_metadata" if missing else "missing_publication_date" if not day else "missing_source_family" if not values["source_family"] else None
        if reason:
            excluded.append({"document_id": row["id"], "reason": reason, "missing_fields": missing})
            continue
        metadata[row["id"]] = {"document_id": row["id"], "title": row["title"], "source_sha256": row["sha256"],
                               "matching_metadata": values, "metadata_origin": "operator_declared_or_source_metadata",
                               "original_metadata_sha256": _hash({k: row[k] for k in row if k != "status"}),
                               "day": day}
    left_set = set(left)
    by_digest = defaultdict(list)
    for identity in sorted(metadata):
        by_digest[metadata[identity]["source_sha256"]].append(identity)
    for identities in by_digest.values():
        both_sides = any(identity in left_set for identity in identities) and any(identity not in left_set for identity in identities)
        for identity in identities if both_sides else identities[1:]:
            excluded.append({"document_id": identity, "reason": "content_in_both_cohorts" if both_sides else "duplicate_content",
                             "missing_fields": []})
            del metadata[identity]
    candidates = []
    for a in left:
        for b in right:
            if a not in metadata or b not in metadata:
                continue
            ma, mb = metadata[a], metadata[b]
            va, vb = ma["matching_metadata"], mb["matching_metadata"]
            distance = abs((ma["day"] - mb["day"]).days)
            if (all(va[key].casefold() == vb[key].casefold() for key in fields)
                    and distance <= gap and va["source_family"].casefold() != vb["source_family"].casefold()):
                candidates.append((distance, a, b))
    pairs, used = [], set()
    for distance, a, b in _maximum_pairs(candidates):
        used.update((a, b))
        pairs.append({"left_document_id": a, "right_document_id": b, "date_gap_days": distance,
                      "matched_on": {key: metadata[a]["matching_metadata"][key] for key in fields}})
    excluded.extend({"document_id": identity, "reason": "no_eligible_one_to_one_match", "missing_fields": []}
                    for identity in sorted(set(metadata) - used))
    for value in metadata.values():
        value.pop("day")
    run_id, analysis = body.get("run_id"), None
    if run_id is not None:
        if not isinstance(run_id, str) or not store.one("SELECT id FROM runs WHERE id=? AND project_id=?", [run_id, project_id]):
            raise ValueError("The selected run is unavailable in this project")
        run = store.one("SELECT status,config_json,model_profile_json FROM runs WHERE id=?", [run_id])
        assessments = store.query("SELECT document_id,lens_id,criterion_id,result FROM v3_assessments "
                                  "WHERE project_id=? AND run_id=? AND document_id IN (SELECT value FROM json_each(?))",
                                  [project_id, run_id, json.dumps(sorted(used))])
        counts, assessed = defaultdict(lambda: {"left": Counter(), "right": Counter()}), {"left": set(), "right": set()}
        for item in assessments:
            side = "left" if item["document_id"] in left_set else "right"
            counts[item["lens_id"] + "/" + item["criterion_id"]][side][item["result"]] += 1
            assessed[side].add(item["document_id"])
        analysis = {"run_id": run_id, "run_status": run["status"], "frozen_config_sha256": _hash(json.loads(run["config_json"])),
                    "model_profile": json.loads(run["model_profile_json"] or "{}"),
                    "assessed_sources": {side: len(ids) for side, ids in assessed.items()},
                    "sources_without_retained_decisions": {side: len(pairs) - len(ids) for side, ids in assessed.items()},
                    "criteria": [{"criterion": key, **{side: {outcome: sides[side][outcome] for outcome in OUTCOMES} for side in ("left", "right")}}
                                 for key, sides in sorted(counts.items())],
                    "notice": "Counts describe retained model decisions, not population prevalence or adjudicated truth. Partial sources and missing decisions remain unresolved."}
    record = {"id": uuid.uuid4().hex, "project_id": project_id, "version": VERSION, "created_at": utcnow(), "created_by": actor_id,
              "question": question.strip(), "status": "matched_descriptive" if pairs else "comparison_not_established",
              "specification": {"matching_fields": fields, "max_date_gap_days": gap, "left_document_ids": left, "right_document_ids": right,
                                "pairing": "maximum_cardinality_date_ordered_augmentation"},
              "pairs": pairs, "sources": [metadata[key] for key in sorted(metadata)], "excluded": excluded,
              "summary": {"left_selected": len(left), "right_selected": len(right), "matched_pairs": len(pairs), "excluded_sources": len(excluded)},
              "analysis": analysis, "inference_statistics_enabled": False, "independence_status": "not_established",
              "limitations": ["Matching metadata and relevance are operator declarations, not independently established facts.",
                              "Pair count is maximized over eligible links using deterministic date-gap ordering. Review the achieved gaps and cohort composition.",
                              "Exact duplicates contribute one representative per cohort. Content shared across cohorts is excluded. Semantic duplicates and source dependence require review.",
                              "No unmatched-source omission, motive, factuality or individual political-identity verdict follows from this comparison."]}
    record["snapshot_sha256"] = _hash(record)
    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError("Comparison snapshot exceeds 2 MiB; narrow the selection")
    from .privacy_lifecycle import require_active_project
    with store.transaction(write=True):
        require_active_project(store, project_id)
        store.execute("INSERT INTO matched_comparisons(id,project_id,payload_json,created_at) VALUES(?,?,?,?)",
                      [record["id"], project_id, encoded, record["created_at"]])
    return record


def get_comparison(store, project_id, comparison_id):
    row = store.one("SELECT payload_json FROM matched_comparisons WHERE id=? AND project_id=?", [comparison_id, project_id])
    if not row:
        raise HTTPException(404, "Comparison not found")
    record = json.loads(row["payload_json"])
    actual = _hash({key: value for key, value in record.items() if key != "snapshot_sha256"})
    if actual != record["snapshot_sha256"]:
        raise HTTPException(409, "Comparison snapshot integrity check failed")
    return record


def register_comparison_api(app, store_factory):
    def db(project_id):
        from .access import require_project_access
        require_project_access(project_id)
        store = store_factory()
        if store.remote:
            raise HTTPException(501, "Matched comparisons currently require native storage")
        ensure_schema(store)
        return store

    @app.post("/api/v3/projects/{project_id}/comparisons", status_code=201)
    def create(project_id: str, body: dict):
        from .access import current_principal
        try:
            return build_comparison(db(project_id), project_id, body, actor_id=current_principal().id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/v3/projects/{project_id}/comparisons")
    def listing(project_id: str):
        rows = db(project_id).query("SELECT payload_json FROM matched_comparisons WHERE project_id=? ORDER BY created_at DESC LIMIT 50", [project_id])
        return {"items": [json.loads(r["payload_json"]) for r in rows]}

    @app.get("/api/v3/projects/{project_id}/comparisons/{comparison_id}")
    def get(project_id: str, comparison_id: str):
        return get_comparison(db(project_id), project_id, comparison_id)
