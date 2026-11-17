"""Review candidates preserve raw comparisons, scope and unknown decisions."""
import json

import pytest
from fastapi import HTTPException

from doxa.lenses import get_lens
from doxa.review_queue import review_queue, unit_context
from doxa.store import utcnow
from doxa.workspace import Workspace, WorkspaceQuery
from test_workspace import seed_assessment, seed_document, seed_run, store as store


def setup(store):
    for identity in ("a", "b"):
        seed_document(store, identity, text="Source statement.")
    seed_run(store, sources=[{"id": identity, "sha256": identity + "-hash"} for identity in ("a", "b")])
    seed_assessment(store, "uncertain-a", "a", result="uncertain")
    seed_assessment(store, "unknown-b", "b", attribution="unknown")
    seed_assessment(store, "ordinary-b", "b", result="does_not_meet", unit="unit-negative")
    return Workspace(store).create("p", WorkspaceQuery(run_id="run"))["query_id"]


def test_uncertainty_queue_pages_real_candidates_without_treating_negatives_as_uncertain(store):
    query = setup(store)
    page = review_queue(store, "p", query, "uncertainty", limit=1)
    assert page["total"] == 2 and len(page["items"]) == 1 and page["next_cursor"]
    assert page["items"][0]["assessment_id"] == page["items"][0]["id"]
    other = review_queue(store, "p", query, "uncertainty", page["next_cursor"], 1)
    assert not other["next_cursor"]
    assert {page["items"][0]["id"], other["items"][0]["id"]} == {"uncertain-a", "unknown-b"}
    assert page["interpretation_accuracy"] is None


def test_queue_rejects_foreign_stale_and_wrong_kind_cursors(store):
    query = setup(store)
    cursor = review_queue(store, "p", query, "uncertainty", limit=1)["next_cursor"]
    with pytest.raises(HTTPException) as error:
        review_queue(store, "other", query, "uncertainty")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        review_queue(store, "p", query, "disagreement", cursor)
    assert error.value.status_code == 422
    Workspace(store).touch("p")
    with pytest.raises(HTTPException) as error:
        review_queue(store, "p", query, "uncertainty")
    assert error.value.status_code == 409


def test_disagreements_compare_original_models_even_after_human_projection_changes(store):
    query = setup(store)
    seed_run(store, "verifier", sources=[{"id": x, "sha256": x + "-hash"} for x in ("a", "b")])
    row = store.one("SELECT config_json FROM runs WHERE id='verifier'")
    config = json.loads(row["config_json"])
    config["verification_of"] = "run"
    store.execute("UPDATE runs SET config_json=? WHERE id='verifier'", [json.dumps(config)])
    seed_assessment(store, "second-a", "a", run="verifier", result="does_not_meet")
    store.execute("UPDATE v3_assessments SET result='does_not_meet',review_status='revised' WHERE id='uncertain-a'")
    page = review_queue(store, "p", query, "disagreement")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["result"] == "does_not_meet"
    assert item["primary_model"]["result"] == "uncertain"
    assert item["verifier_model"]["result"] == "does_not_meet"
    assert item["comparison_basis"] == "original_model_outputs" and "result" in item["changed_fields"]


def test_unfinished_queue_preserves_absence_and_filters_frozen_criteria(store):
    setup(store)
    pack = get_lens("discourse")
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='run'")["config_json"])
    config["lens_snapshots"] = [pack]
    store.execute("UPDATE runs SET config_json=? WHERE id='run'", [json.dumps(config)])
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "a", "unfinished", "partial", 17, json.dumps({"errors": [{"code": "output_limit", "message": "Incomplete response"}]}), utcnow()])
    query = Workspace(store).create("p", WorkspaceQuery(run_id="run", lens_ids=["discourse"], stance="supports"))["query_id"]
    page = review_queue(store, "p", query, "unfinished")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["assessment_id"] is None and item["result"] is None
    assert set(item["missing_criterion_keys"]) == {"discourse/" + c["id"] for c in pack["criteria"]}
    assert item["errors"][0]["code"] == "output_limit"
    assert page["unapplied_filters"] == ["stance"]
    unsupported = Workspace(store).create("p", WorkspaceQuery(run_id="run", lens_ids=["language-norms"]))["query_id"]
    assert review_queue(store, "p", unsupported, "unfinished")["total"] == 0


def test_queue_cannot_read_another_projects_verifier(store):
    query = setup(store)
    seed_run(store, "foreign", "other")
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='foreign'")["config_json"])
    config["verification_of"] = "run"
    store.execute("UPDATE runs SET config_json=? WHERE id='foreign'", [json.dumps(config)])
    page = review_queue(store, "p", query, "disagreement")
    assert page["total"] == 0 and "verifier_run_id" not in page


def test_unfinished_unit_context_resolves_exact_original_text_without_a_model_verdict(store):
    import hashlib
    from doxa.contextual import build_units
    setup(store)
    version = hashlib.sha256(b"a:a-hash").hexdigest()[:32]
    unit = build_units({"id": "a", "sha256": "a-hash", "source_version_id": version,
                        "blocks": [{"id": "b1", "text": "Source statement.", "paragraph": 1}]}, max_unit_chars=4000)[0]
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "a", unit["id"], "partial", 17, "{}", utcnow()])
    query = Workspace(store).create("p", WorkspaceQuery(run_id="run"))["query_id"]
    result = unit_context(store, "p", query, "a", unit["id"])
    assert result["unit"]["text"] == "Source statement."
    assert result["blocks"][0]["id"] == "b1" and result["finding"] is None
    assert result["unit_status"] == "partial"
    with pytest.raises(HTTPException) as error:
        unit_context(store, "p", query, "b", unit["id"])
    assert error.value.status_code == 404
