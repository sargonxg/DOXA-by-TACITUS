import json

from fastapi.testclient import TestClient

from doxa.api import create_app
from test_evaluation_v04 import labels, reviewed_store  # noqa: F401


def test_scoped_blinded_review_api_and_optional_overlays(reviewed_store, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DOXA_REVIEWER_ID", "test-reviewer-A")
    app = create_app(reviewed_store.root, start_workers=False, use_saved_showcase=False)
    with TestClient(app) as client:
        created = client.post("/api/v3/projects/p/evaluations", json={"run_id": "r", "development_count": 2, "heldout_count": 2})
        assert created.status_code == 200, created.text
        identity = created.json()["id"]
        base = f"/api/v3/projects/p/evaluations/{identity}"
        form = client.get(base + "/form").json()
        assert form["model_blinded"] and "SECRET_MODEL_EXPLANATION" not in json.dumps(form)
        case = form["cases"][0]
        payload = {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
                   "independent_blinded_review": True, "labels": labels()}
        response = client.post(base + "/reviews", json={"rows": [payload], "reviewer_id": "browser-spoofed-reviewer"})
        assert response.status_code == 200, response.text
        stored = app.state.store.one("SELECT reviewer_id FROM v4_reference_reviews WHERE case_id=?", [case["id"]])
        assert stored["reviewer_id"] == "test-reviewer-A"
        assert client.post(base + "/adjudications", json={"case_id": case["id"], "labels": labels()}).status_code == 422
        monkeypatch.setenv("DOXA_REVIEWER_ID", "test-reviewer-B")
        assert client.post(base + "/reviews", json={"rows": [payload]}).status_code == 200
        assert client.post(base + "/adjudications", json={"case_id": case["id"], "labels": labels()}).status_code == 200
        report = client.get(base + "/report").json()
        assert report["adjudicated_cases"] == 1 and report["interpretation_accuracy"] is None
        assert report["heldout_metrics_locked"]
        assert client.get(base + "/form?split=heldout&include_model=true").status_code == 422
        assert client.get(f"/api/v3/projects/other/evaluations/{identity}/form").status_code == 404
        overlay_path = "/api/v3/projects/p/assessments/a0/overlays"
        assert client.get(overlay_path).json()["default"]["status"] == "NOT_ASSESSED"
        overlay = client.post(overlay_path, json={"fields": {"evidence_function": "policy_argument"}, "actor_kind": "agent", "actor_id": "browser"})
        assert overlay.status_code == 200, overlay.text
        assert overlay.json()["actor_id"] == "test-reviewer-B" and overlay.json()["actor_kind"] == "human"
        assert app.state.store.one("SELECT result FROM v3_assessments WHERE id='a0'")["result"] == "does_not_meet"
