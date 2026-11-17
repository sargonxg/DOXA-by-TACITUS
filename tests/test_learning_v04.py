import json

from doxa import learning
from test_learning import approved, assessment, document, run, store  # noqa: F401


def test_memory_comparison_ignores_execution_history_and_reports_reference_pending(store):  # noqa: F811
    approved(store, "training")
    memory = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    candidate = learning.create_candidate(store, "p", memory["id"], actor_kind="human", actor_id="operator")
    document(store, "eval", split="holdout")
    run(store, "before")
    run(store, "after", snapshot=memory["id"])
    assessment(store, "before-a", "eval", "before")
    assessment(store, "after-a", "eval", "after")
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='after'")["config_json"])
    config.update({"concurrency": 8, "budget_usd": 2, "task_timing_history": [{"task_timeout_seconds": 1800}],
                   "source_manifest_key": "runs/after/source-manifest.json", "provider_preflight": {"checked_at": "later"}})
    store.execute("UPDATE runs SET config_json=? WHERE id='after'", [json.dumps(config)])
    report = learning.candidate_report(store, "p", candidate["id"], "before", "after")
    assert report["structural_compatibility_passed"]
    assert report["baseline_structural_fingerprint"] == report["candidate_structural_fingerprint"]
    assert report["quality_status"] == "reference_pending" and not report["quality_promotion_recommended"]
    config["new_analytical_setting"] = "changed"
    store.execute("UPDATE runs SET config_json=? WHERE id='after'", [json.dumps(config)])
    changed = learning.candidate_report(store, "p", candidate["id"], "before", "after")
    assert not changed["structural_compatibility_passed"]
    assert "Model or analytical settings differ beyond memory" in changed["blocking_reasons"]


def test_direct_review_batch_increments_workspace_revision_with_projection(store):  # noqa: F811
    from doxa.workspace import Workspace
    document(store, "training")
    assessment(store, "a", "training")
    before = int(Workspace(store).revision("p"))
    learning.record_review(store, "p", "a", {"result": "uncertain"}, actor_kind="human", actor_id="operator")
    assert int(Workspace(store).revision("p")) == before + 1
    assert store.one("SELECT result FROM v3_assessments WHERE id='a'")["result"] == "uncertain"
