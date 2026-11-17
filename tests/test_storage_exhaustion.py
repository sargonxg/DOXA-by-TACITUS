"""Disk exhaustion is actionable; unrelated failures retain their semantics."""
import errno
import sqlite3

import pytest
from fastapi.testclient import TestClient
from xlsxwriter.exceptions import FileCreateError

from doxa.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


def assert_storage_response(response):
    assert response.status_code == 507, response.text
    assert response.json()["code"] == "storage_exhausted"
    assert "Free space" in response.json()["detail"] and "checkpoints" in response.json()["detail"]
    assert "private-provider-secret" not in response.text and "/private/" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("wrapped", [False, True])
def test_report_export_disk_full_returns_safe_507_and_security_headers(client, monkeypatch, wrapped):
    project = client.post("/api/projects", json={"name": "Synthetic export storage fixture"}).json()["id"]

    def build_report(*_args):
        try:
            raise OSError(errno.ENOSPC, "No space left on device", "/private/private-provider-secret/export.xlsx")
        except OSError as error:
            if wrapped:
                raise FileCreateError(error)
            raise

    with monkeypatch.context() as patch:
        patch.setattr("doxa.reports.build_report", build_report)
        response = client.post(f"/api/projects/{project}/reports", json={"format": "xlsx"})
        assert_storage_response(response)
    assert client.get("/api/health").status_code == 200
    assert client.app.state.store.one("SELECT COUNT(*) n FROM reports")["n"] == 0


def test_sqlite_storage_full_before_project_creation_returns_safe_507(client, monkeypatch):
    before = client.app.state.store.one("SELECT COUNT(*) n FROM projects")["n"]
    def full_database(*_args, **_kwargs):
        raise sqlite3.OperationalError("database or disk is full: /private/private-provider-secret")
    monkeypatch.setattr(client.app.state.store, "execute", full_database)
    response = client.post("/api/projects", json={"name": "Uncommitted storage fixture"})
    assert_storage_response(response)
    assert client.app.state.store.one("SELECT COUNT(*) n FROM projects")["n"] == before


@pytest.mark.parametrize("error", [PermissionError(errno.EACCES, "permission denied"),
                                  sqlite3.OperationalError("database is locked"), ValueError("unrelated application error")])
def test_non_storage_exhaustion_errors_are_not_reclassified_or_swallowed(client, monkeypatch, error):
    def unrelated_failure(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(client.app.state.store, "execute", unrelated_failure)
    with pytest.raises(type(error)) as raised:
        client.post("/api/projects", json={"name": "Synthetic failure propagation"})
    assert raised.value is error
