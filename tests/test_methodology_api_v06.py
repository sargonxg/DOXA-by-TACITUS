"""Actual HTTP interface checks use isolated synthetic storage, no inference."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from doxa.methodology_api import register_methodology_api
from test_evaluation_v04 import reviewed_store  # noqa: F401
from test_reference_panels_v06 import manifest_for, protocol_for


def test_method_cards_study_and_scoped_panel_http_workflow(reviewed_store, monkeypatch):  # noqa: F811
    app = FastAPI()
    register_methodology_api(app, lambda: reviewed_store)
    monkeypatch.setenv('DOXA_REVIEWER_ID', 'trusted-test-operator')
    with TestClient(app) as client:
        snapshot = client.get('/api/v3/method-cards').json()
        assert snapshot['cards'] and snapshot['snapshot_sha256']
        assert client.get('/api/v3/method-cards/discourse/stance').json()['id'] == 'discourse/stance'
        assert client.get('/api/v3/method-cards/nonexistent/criterion').status_code == 404
        assert client.get('/api/v3/study-protocol').json()['template_not_live_project_counters']
        manifest = manifest_for(reviewed_store)
        response = client.post('/api/v3/projects/p/evaluation-panels', json={'run_id': 'r', 'manifest': manifest})
        assert response.status_code == 201, response.text
        evaluation = response.json()['id']
        registered = client.post(f'/api/v3/projects/p/evaluations/{evaluation}/preregistration', json=protocol_for(manifest))
        assert registered.status_code == 201, registered.text
        assert registered.json()['registered_by'] == 'trusted-test-operator'
        assert client.get(f'/api/v3/projects/p/evaluations/{evaluation}/readiness').json()['reference_status'] == 'reference_pending'
        assert client.post('/api/v3/projects/outside/evaluation-panels', json={'run_id': 'r', 'manifest': manifest}).status_code == 404
