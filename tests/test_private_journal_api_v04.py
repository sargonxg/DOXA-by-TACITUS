"""Capture requires both local operator configuration and explicit run consent."""
import json

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from test_v3_api import CompactProvider, finish, project, start_mock, upload


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_ENABLE_PRIVATE_JOURNAL", "DOXA_PRIVATE_JOURNAL_MAX_BYTES"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


def test_private_capture_requires_operator_enablement_before_provider_or_run_creation(client, monkeypatch):
    pid = project(client)
    upload(client, pid, "Private text should not be captured without consent.")
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: pytest.fail("Rejected capture must not initialize a provider"))
    response = client.post(f"/api/v3/projects/{pid}/runs", json={"provider": "openai", "model": "test", "allow_remote_text": True, "request_journal_mode": "visible_io"})
    assert response.status_code == 422
    assert client.app.state.store.one("SELECT COUNT(*) n FROM runs WHERE project_id=?", [pid])["n"] == 0
    assert not (client.app.state.store.root / "private-request-journals").exists()


def test_operator_enabled_capture_stays_off_without_per_run_optin(client, monkeypatch):
    monkeypatch.setenv("DOXA_ENABLE_PRIVATE_JOURNAL", "true")
    pid = project(client)
    source = "UNIQUE PRIVATE SOURCE MARKER. The author opposes collective blame."
    upload(client, pid, source)
    run, _ = start_mock(client, pid, monkeypatch)
    assert finish(client, run["id"])["status"] == "completed"
    assert not (client.app.state.store.root / "private-request-journals").exists()
    events = client.app.state.store.query("SELECT payload_json FROM request_attempt_events WHERE run_id=?", [run["id"]])
    assert events and all("UNIQUE PRIVATE SOURCE MARKER" not in row["payload_json"] for row in events)


def test_explicit_capture_writes_private_entries_and_public_journal_contains_only_metadata(client, monkeypatch):
    monkeypatch.setenv("DOXA_ENABLE_PRIVATE_JOURNAL", "true")
    monkeypatch.setenv("DOXA_OPENAI_API_KEY", "DO_NOT_RECORD_THIS_CREDENTIAL")
    class ObservedProvider(CompactProvider):
        async def generate(self, messages, schema, **kwargs):
            result = await super().generate(messages, schema, **kwargs)
            result["provider_observation"] = {"_visible_response": json.dumps(result["content"]),
                "_visible_request": {"model": "compact-test", "messages": messages, "schema": schema}}
            return result
    pid = project(client)
    phrase = "UNIQUE PRIVATE SOURCE MARKER"
    upload(client, pid, phrase + ". The author opposes collective blame.")
    run, _ = start_mock(client, pid, monkeypatch, ObservedProvider(), request_journal_mode="visible_io")
    assert finish(client, run["id"])["status"] == "completed"
    folder = client.app.state.store.root / "private-request-journals" / run["id"]
    entries = list(folder.glob("*.json"))
    assert entries and folder.stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in entries)
    contents = "".join(path.read_text() for path in entries)
    assert phrase in contents and "DO_NOT_RECORD_THIS_CREDENTIAL" not in contents
    events = client.app.state.store.query("SELECT payload_json FROM request_attempt_events WHERE run_id=?", [run["id"]])
    assert events and all(phrase not in row["payload_json"] and "DO_NOT_RECORD_THIS_CREDENTIAL" not in row["payload_json"] for row in events)
    usage = client.app.state.store.one("SELECT reserved_bytes FROM private_journal_usage WHERE run_id=?", [run["id"]])["reserved_bytes"]
    assert usage == sum(path.stat().st_size for path in entries) <= 64 * 1024 * 1024


def test_capture_storage_quota_fails_explicitly_without_exceeding_its_bound(client, monkeypatch):
    monkeypatch.setenv("DOXA_ENABLE_PRIVATE_JOURNAL", "true")
    monkeypatch.setenv("DOXA_PRIVATE_JOURNAL_MAX_BYTES", "4096")
    pid = project(client)
    upload(client, pid, "A complete source for a bounded private capture test.")
    run, _ = start_mock(client, pid, monkeypatch, request_journal_mode="visible_io")
    settled = finish(client, run["id"])
    assert settled["status"] == "partial"
    assert "quota" in json.dumps(settled["errors"]).lower()
    usage = client.app.state.store.one("SELECT reserved_bytes FROM private_journal_usage WHERE run_id=?", [run["id"]])["reserved_bytes"]
    assert usage <= 4096
    files = list((client.app.state.store.root / "private-request-journals" / run["id"]).glob("*.json"))
    assert sum(path.stat().st_size for path in files) <= 4096
