"""Prospectively registered alert-review campaigns, separate from inference.

Historical snapshots are baselines, not prospective observations. The existing
trajectory engine generates every candidate; reviewers cannot submit alerts.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math

from fastapi import Request

from .lineage import content_sha256
from .method_config import cache_method_fingerprint
from .store import utcnow
from .trajectories import detect_spikes, monthly_trajectories

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v06_alert_campaigns(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,run_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v06_alert_snapshots(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,campaign_id TEXT NOT NULL,run_id TEXT NOT NULL,
 look_index INTEGER NOT NULL,payload_json TEXT NOT NULL,captured_at TEXT NOT NULL,
 UNIQUE(campaign_id,look_index)
);
CREATE TABLE IF NOT EXISTS v06_alert_reviews(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,campaign_id TEXT NOT NULL,alert_id TEXT NOT NULL,
 reviewer_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(campaign_id,alert_id,reviewer_id)
);
"""


def ensure_schema(store):
    if store.remote:
        raise ValueError("Prospective alert evaluation requires native storage")
    if not getattr(store, "_v06_alert_schema_ready", False):
        store.batch([{"sql": value.strip(), "params": []} for value in SCHEMA_SQL.split(";") if value.strip()])
        store._v06_alert_schema_ready = True


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Campaign dates require an explicit ISO timestamp and timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Campaign dates require an explicit ISO timestamp and timezone") from None
    if result.tzinfo is None:
        raise ValueError("Campaign dates require a timezone")
    return result.astimezone(timezone.utc)


def _run(store, project_id, run_id):
    row = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
    if not row:
        raise ValueError("Campaign run is outside this project")
    config, model = json.loads(row["config_json"]), json.loads(row.get("model_profile_json") or "{}")
    model_keys = ("provider", "model", "mode", "revision", "quantization", "context_limit", "prompt_version",
                  "response_schema_version", "unit_builder_version", "rubric_versions", "methodology_version")
    identity = {key: model.get(key) for key in model_keys}
    return {"run_id": run_id, "run_status": row["status"],
            "method_sha256": cache_method_fingerprint(config, {"id": "campaign-method-only", "sha256": "not-a-source"}),
            "model_identity": identity, "model_identity_sha256": content_sha256(identity),
            "source_selection_sha256": content_sha256(config.get("source_snapshot", [])),
            "revision_resolved": str(identity.get("revision") or config.get("model_revision") or "unresolved") != "unresolved"}


def _campaign(store, project_id, campaign_id):
    ensure_schema(store)
    row = store.one("SELECT payload_json FROM v06_alert_campaigns WHERE project_id=? AND id=?", [project_id, campaign_id])
    if not row:
        raise ValueError("Alert-review campaign not found in this project")
    return json.loads(row["payload_json"])


def _scenarios(value):
    if not isinstance(value, list) or len(value) > 5:
        raise ValueError("Choose at most five explicitly hypothetical classification-error scenarios")
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"label", "positive_reclassification_fraction", "negative_reclassification_fraction"}:
            raise ValueError("Sensitivity scenarios require a label and positive/negative reclassification fractions")
        if not isinstance(row["label"], str) or not row["label"].strip() or len(row["label"]) > 150 or row["label"] in seen:
            raise ValueError("Sensitivity scenario labels must be unique bounded text")
        seen.add(row["label"])
        for field in ("positive_reclassification_fraction", "negative_reclassification_fraction"):
            number = row[field]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError("Sensitivity fractions must lie in [0,1]")
    return value


def create_campaign(store, project_id, body, *, actor_id):
    ensure_schema(store)
    fields = {"run_id", "lens_id", "criterion_id", "start_at", "end_at", "look_times", "review_question", "classification_scenarios"}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError("Supply only the declared run, criterion, future window, look times, review question and sensitivity scenarios")
    now = _time(utcnow())
    start, end = _time(body["start_at"]), _time(body["end_at"])
    if end <= start or (end - start).days > 366:
        raise ValueError("Preregister a future observation window of at most one year")
    if any(not isinstance(body[field], str) or not body[field] or len(body[field]) > 200 for field in ("run_id", "lens_id", "criterion_id")):
        raise ValueError("Supply bounded run, lens and criterion identifiers")
    looks = body["look_times"]
    if not isinstance(looks, list) or not 1 <= len(looks) <= 24:
        raise ValueError("Preregister 1–24 future look timestamps")
    timestamps = [_time(value) for value in looks]
    if timestamps != sorted(set(timestamps)) or any(not start <= timestamp <= end for timestamp in timestamps):
        raise ValueError("Look times must be unique, increasing and inside the observation window")
    if not isinstance(body["review_question"], str) or not body["review_question"].strip() or len(body["review_question"]) > 4000:
        raise ValueError("A bounded explicit alert-relevance question is required")
    scenarios = _scenarios(body["classification_scenarios"])
    from .lenses import get_lens
    pack = get_lens(body["lens_id"])
    criterion = next((row for row in pack["criteria"] if row["id"] == body["criterion_id"]), None)
    if not criterion or criterion["kind"] != "indicator":
        raise ValueError("Inferential alert review requires an explicit indicator criterion; descriptive frames are not prejudice rates")
    with store.transaction(write=True):
        method = _run(store, project_id, body["run_id"])
        identity = content_sha256(["alert-campaign-0.6", project_id, body, method])[:32]
        existing = store.one("SELECT payload_json FROM v06_alert_campaigns WHERE project_id=? AND id=?", [project_id, identity])
        if existing:
            return {**{key: value for key, value in json.loads(existing["payload_json"]).items() if key != "baseline_snapshot"}, "idempotent": True}
        if start < now:
            raise ValueError("A new prospective campaign cannot be registered after its window begins")
        baseline = monthly_trajectories(store, project_id, body["run_id"], lens_id=pack["id"], criterion_id=criterion["id"])
        policy = detect_spikes([], alpha=0.01 / len(looks), as_of_month=now.strftime("%Y-%m"))["policy"]
        content = {"version": "doxa-prospective-alert-campaign-0.6", "project_id": project_id,
                   "run_id": body["run_id"], "lens_id": pack["id"], "criterion_id": criterion["id"],
                   "rubric_sha256": content_sha256(pack), "start_at": start.isoformat(), "end_at": end.isoformat(),
                   "look_times": [value.isoformat() for value in timestamps], "review_question": body["review_question"],
                   "classification_scenarios": scenarios, "frozen_method": method,
                   "sequential_policy": {"name": "Bonferroni across fixed registered looks; Holm within each look", "family_alpha": 0.01,
                                         "per_look_alpha": 0.01 / len(looks), "look_count": len(looks), "trajectory_policy": policy},
                   "baseline_snapshot": baseline, "baseline_sha256": content_sha256(baseline),
                   "baseline_role": "Historical baseline only; not a prospective observation or relevance label.",
                   "registered_by": actor_id, "registered_at": utcnow(), "status": "prospective_observations_pending"}
        content["id"] = identity
        store.execute("INSERT INTO v06_alert_campaigns(id,project_id,run_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                      [identity, project_id, body["run_id"], json.dumps(content, ensure_ascii=False, allow_nan=False), content["registered_at"]])
        return {key: value for key, value in content.items() if key != "baseline_snapshot"}


def sensitivity_series(monthly, scenarios):
    result = []
    for scenario in scenarios:
        rows = []
        for month in monthly:
            k, n = month.get("positive_documents", 0), month.get("eligible_documents", 0)
            if n:
                rows.append({"date": month["date"], "observed_detected_rate": k / n,
                             "assumed_reclassified_rate": (k * (1 - scenario["positive_reclassification_fraction"]) + (n - k) * scenario["negative_reclassification_fraction"]) / n})
        result.append({"assumption": scenario, "monthly": rows})
    return {"scenarios": result, "calibrated_classification_error": False, "sensitivity_only": True,
            "notice": "Fractions are hypothetical reclassifications of observed positive/negative groups, not measured false-positive/negative rates, corrected prevalence or causal estimates."}


def capture_look(store, project_id, campaign_id, *, run_id, look_index):
    campaign = _campaign(store, project_id, campaign_id)
    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ValueError("Choose a bounded follow-up run identity")
    if type(look_index) is not int or not 0 <= look_index < len(campaign["look_times"]):
        raise ValueError("Choose one preregistered look index")
    now = _time(utcnow())
    if not _time(campaign["look_times"][look_index]) <= now <= _time(campaign["end_at"]):
        raise ValueError("This scheduled observation is not open or the registered window has ended")
    with store.transaction(write=True):
        prior = store.one("SELECT payload_json FROM v06_alert_snapshots WHERE project_id=? AND campaign_id=? AND look_index=?", [project_id, campaign_id, look_index])
        if prior:
            content = json.loads(prior["payload_json"])
            if content["run_id"] != run_id:
                raise ValueError("A captured look is immutable; a retry must use the same run")
            return {**content, "idempotent": True}
        method, frozen = _run(store, project_id, run_id), campaign["frozen_method"]
        if any(method[key] != frozen[key] for key in ("method_sha256", "model_identity_sha256")):
            raise ValueError("Follow-up run changed the registered analytical method or model identity")
        from .lenses import get_lens
        if content_sha256(get_lens(campaign["lens_id"])) != campaign["rubric_sha256"]:
            raise ValueError("Registered rubric changed; create a separate campaign")
        trajectory = monthly_trajectories(store, project_id, run_id, lens_id=campaign["lens_id"], criterion_id=campaign["criterion_id"])
        if not trajectory["source_snapshot_available"]:
            raise ValueError("A prospective observation requires a frozen run source selection")
        detection = detect_spikes(trajectory["monthly"], alpha=campaign["sequential_policy"]["per_look_alpha"], as_of_month=now.strftime("%Y-%m"))
        candidates = [{**row, "alert_id": content_sha256([campaign_id, row["date"], campaign["lens_id"], campaign["criterion_id"]])[:32]}
                      for row in detection["candidates"] if campaign["start_at"][:7] <= row["date"] <= campaign["end_at"][:7]]
        content = {"id": content_sha256([campaign_id, look_index])[:32], "campaign_id": campaign_id,
                   "run_id": run_id, "look_index": look_index, "captured_at": utcnow(), "frozen_method": method,
                   "trajectory_sha256": content_sha256(trajectory), "trajectory": trajectory,
                   "detection": detection, "prospective_candidates": candidates,
                   "sensitivity": sensitivity_series(trajectory["monthly"], campaign["classification_scenarios"])}
        store.execute("INSERT INTO v06_alert_snapshots(id,project_id,campaign_id,run_id,look_index,payload_json,captured_at) VALUES(?,?,?,?,?,?,?)",
                      [content["id"], project_id, campaign_id, run_id, look_index, json.dumps(content, ensure_ascii=False, allow_nan=False), content["captured_at"]])
        return content


def review_alert(store, project_id, campaign_id, body, *, reviewer_id):
    ensure_schema(store)
    with store.transaction(write=True):
        return _review_alert(store, project_id, campaign_id, body, reviewer_id=reviewer_id)


def _review_alert(store, project_id, campaign_id, body, *, reviewer_id):
    _campaign(store, project_id, campaign_id)
    if not isinstance(reviewer_id, str) or not reviewer_id.strip() or len(reviewer_id) > 200:
        raise ValueError("An identified reviewer is required")
    if not isinstance(body, dict) or set(body) != {"alert_id", "relevance", "context_sufficient", "note"}:
        raise ValueError("Alert review accepts only a frozen alert ID, relevance, context sufficiency and note")
    if not isinstance(body["alert_id"], str) or len(body["alert_id"]) > 200:
        raise ValueError("A bounded frozen alert ID is required")
    if not isinstance(body["relevance"], str) or body["relevance"] not in {"relevant", "not_relevant", "uncertain"} or type(body["context_sufficient"]) is not bool:
        raise ValueError("Review requires an explicit relevance reading and Boolean context sufficiency")
    if not isinstance(body["note"], str) or not body["note"].strip() or len(body["note"]) > 4000:
        raise ValueError("Review needs a bounded human explanation")
    snapshots = store.query("SELECT payload_json FROM v06_alert_snapshots WHERE project_id=? AND campaign_id=?", [project_id, campaign_id])
    if not any(body["alert_id"] == candidate["alert_id"] for row in snapshots for candidate in json.loads(row["payload_json"])["prospective_candidates"]):
        raise ValueError("Review must resolve to a server-generated prospective alert in this campaign")
    existing = store.one("SELECT payload_json FROM v06_alert_reviews WHERE project_id=? AND campaign_id=? AND alert_id=? AND reviewer_id=?", [project_id, campaign_id, body["alert_id"], reviewer_id])
    if existing:
        record = json.loads(existing["payload_json"])
        if any(record[key] != value for key, value in body.items()):
            raise ValueError("A prior alert review is immutable")
        return {**record, "idempotent": True}
    record = {**body, "reviewer_id": reviewer_id, "reviewed_at": utcnow(), "origin": "authenticated_operator_relevance_review",
              "reviewer_qualifications_independently_verified": False}
    identity = content_sha256([campaign_id, body["alert_id"], reviewer_id])[:32]
    store.execute("INSERT INTO v06_alert_reviews(id,project_id,campaign_id,alert_id,reviewer_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  [identity, project_id, campaign_id, body["alert_id"], reviewer_id, json.dumps(record, ensure_ascii=False), record["reviewed_at"]])
    return record


def campaign_report(store, project_id, campaign_id):
    campaign = _campaign(store, project_id, campaign_id)
    snapshots = [json.loads(row["payload_json"]) for row in store.query("SELECT payload_json FROM v06_alert_snapshots WHERE project_id=? AND campaign_id=? ORDER BY look_index", [project_id, campaign_id])]
    alerts = {candidate["alert_id"]: candidate for snapshot in snapshots for candidate in snapshot["prospective_candidates"]}
    reviews = [json.loads(row["payload_json"]) for row in store.query("SELECT payload_json FROM v06_alert_reviews WHERE project_id=? AND campaign_id=? ORDER BY created_at,id", [project_id, campaign_id])]
    by_alert = {}
    for review in reviews:
        by_alert.setdefault(review["alert_id"], []).append(review)
    resolved = Counter()
    for items in by_alert.values():
        readings = {item["relevance"] if item["context_sufficient"] else "uncertain" for item in items}
        resolved[next(iter(readings)) if len(readings) == 1 else "disagreement"] += 1
    clear = resolved["relevant"] + resolved["not_relevant"]
    return {"campaign": {key: value for key, value in campaign.items() if key != "baseline_snapshot"},
            "completed_looks": len(snapshots), "registered_looks": len(campaign["look_times"]),
            "unique_prospective_alerts": len(alerts), "alerts": list(alerts.values()), "reviews": reviews,
            "status": "not_measured" if not reviews else "human_observations_recorded_external_validation_pending",
            "reviewed_unique_alerts": len(by_alert), "readings": dict(resolved),
            "operator_assessed_relevance_fraction": resolved["relevant"] / clear if clear else None,
            "review_coverage": len(by_alert) / len(alerts) if alerts else None,
            "independently_validated_alert_quality": None, "causal_claims_enabled": False,
            "notice": "Repeated looks at the same alert month count once. Operator relevance readings are not classifier accuracy or independently validated alert utility; missing/uncertain reviews remain explicit."}


def register_alert_review_api(app, store_factory):
    from fastapi import HTTPException
    from .access import require_project_access, trusted_actor

    def db(project_id):
        require_project_access(project_id)
        store = store_factory()
        if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
            raise HTTPException(404, "Project not found")
        if store.remote:
            raise HTTPException(501, "Alert-review campaigns require native pilot storage")
        return store

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/alert-review-campaigns", status_code=201)
    def create(project_id: str, body: dict, request: Request):
        store = db(project_id)
        return call(create_campaign, store, project_id, body, actor_id=trusted_actor(request, store)["actor_id"])

    @app.get("/api/v3/projects/{project_id}/alert-review-campaigns")
    def campaigns(project_id: str, offset: int = 0):
        store = db(project_id)
        ensure_schema(store)
        if not 0 <= offset <= 100000:
            raise HTTPException(422, "Campaign offset is outside its bound")
        rows = store.query("SELECT payload_json FROM v06_alert_campaigns WHERE project_id=? ORDER BY created_at,id LIMIT 101 OFFSET ?", [project_id, offset])
        return {"campaigns": [{key: value for key, value in json.loads(row["payload_json"]).items() if key != "baseline_snapshot"}
                              for row in rows[:100]], "next_offset": offset + 100 if len(rows) > 100 else None}

    @app.get("/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}")
    def report(project_id: str, campaign_id: str):
        return call(campaign_report, db(project_id), project_id, campaign_id)

    @app.post("/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}/snapshots", status_code=201)
    def capture(project_id: str, campaign_id: str, body: dict):
        if set(body) != {"run_id", "look_index"}:
            raise HTTPException(422, "Capture accepts run_id and registered look_index only")
        return call(capture_look, db(project_id), project_id, campaign_id, run_id=body["run_id"], look_index=body["look_index"])

    @app.post("/api/v3/projects/{project_id}/alert-review-campaigns/{campaign_id}/reviews", status_code=201)
    def review(project_id: str, campaign_id: str, body: dict, request: Request):
        store = db(project_id)
        return call(review_alert, store, project_id, campaign_id, body, reviewer_id=trusted_actor(request, store)["actor_id"])
