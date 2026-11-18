from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from doxa.activity_jobs import register_activity_api
from doxa.readiness import register_readiness_api


def test_shared_remote_profile_reports_capability_without_creating_native_jobs(tmp_path):
    store = SimpleNamespace(root=tmp_path, remote="https://example.invalid/private-storage")
    app = FastAPI()
    register_readiness_api(app, lambda: store)
    register_activity_api(app, {"db": lambda: store, "require_project": lambda _: {"id": "p"}})
    with TestClient(app) as client:
        readiness = client.get("/api/system/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["storage_backend"] == "remote"
        result = client.get("/api/v3/projects/p/activity")
        assert result.status_code == 501
        assert "native" in result.json()["detail"]
        assert not hasattr(app.state, "activity_jobs")
