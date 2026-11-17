"""Real HTTP import retries and simultaneous multipart writers, no inference."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import threading

from fastapi.testclient import TestClient
import pytest

from doxa.api import create_app
from test_api import project


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    monkeypatch.delenv("DOXA_INTERNAL_TOKEN", raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as result:
        yield result


def begin(client, pid, content):
    response = client.post(f"/api/projects/{pid}/uploads/init", json={"filename": "source.txt", "size": len(content)})
    assert response.status_code == 200, response.text
    upload_id = response.json()["id"]
    part = client.put(f"/api/uploads/{upload_id}/parts/1", content=content)
    assert part.status_code == 200, part.text
    return upload_id, {"parts": [part.json()]}


def test_completed_upload_replays_same_result_after_cleanup_and_restart(client, tmp_path):
    pid = project(client)
    content = b"An exact source with no remote inference."
    upload_id, body = begin(client, pid, content)
    first = client.post(f"/api/uploads/{upload_id}/complete", json=body)
    assert first.status_code == 200, first.text
    # A second process has independent Store state and no original HTTP session.
    with TestClient(create_app(tmp_path, start_workers=False)) as second:
        replay = second.post(f"/api/uploads/{upload_id}/complete", json=body)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert len(second.get(f"/api/projects/{pid}/documents").json()) == 1
        assert second.put(f"/api/uploads/{upload_id}/parts/1", content=content).status_code == 409


def test_lost_completion_commit_is_repaired_without_new_source_or_batch(client, monkeypatch):
    pid = project(client)
    upload_id, body = begin(client, pid, b"Durable original source.")
    store = client.app.state.store
    execute = store.execute
    def fail_once(sql, params=()):
        if "UPDATE upload_sessions SET status='completed'" in sql:
            raise OSError(errno.ENOSPC, "injected after document commit")
        return execute(sql, params)
    monkeypatch.setattr(store, "execute", fail_once)
    assert client.post(f"/api/uploads/{upload_id}/complete", json=body).status_code == 507
    original_id = store.one("SELECT id FROM documents WHERE project_id=?", [pid])["id"]
    monkeypatch.setattr(store, "execute", execute)
    repaired = client.post(f"/api/uploads/{upload_id}/complete", json=body)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["document"]["id"] == original_id
    assert store.one("SELECT COUNT(*) n FROM batch_members WHERE document_id=?", [original_id])["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM documents WHERE project_id=?", [pid])["n"] == 1


def test_simultaneous_identical_parts_are_idempotent_and_conflicts_do_not_overwrite(client):
    pid = project(client)
    source = b"Concurrent source content."
    upload_id = client.post(f"/api/projects/{pid}/uploads/init", json={"filename": "source.txt", "size": len(source)}).json()["id"]
    url = f"/api/uploads/{upload_id}/parts/1"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.put(url, content=source), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["etag"] for response in responses}) == 1
    different = b"X" * len(source)
    assert client.put(url, content=different).status_code == 409
    rejected_key = f"uploads/{upload_id}/parts/1/{hashlib.sha256(different).hexdigest()}"
    assert not client.app.state.store.object_path(rejected_key).exists()
    body = {"parts": [responses[0].json()]}
    result = client.post(f"/api/uploads/{upload_id}/complete", json=body)
    assert result.status_code == 200, result.text
    doc = result.json()["document"]
    assert client.get(f"/api/documents/{doc['id']}/source").content == source
    assert doc["sha256"] == hashlib.sha256(source).hexdigest()


def test_failed_extraction_can_retry_same_original_after_environment_repair(client, monkeypatch):
    from doxa import extraction
    pid = project(client)
    parser = extraction.extract_text_document
    def unavailable(*_):
        raise ValueError("Parser temporarily unavailable")
    monkeypatch.setattr(extraction, "extract_text_document", unavailable)
    source = b"The parser can now read this exact original."
    first = client.post(f"/api/projects/{pid}/upload", files={"files": ("source.txt", source, "text/plain")}).json()[0]
    assert first["status"] == "failed"
    monkeypatch.setattr(extraction, "extract_text_document", parser)
    second = client.post(f"/api/projects/{pid}/upload", files={"files": ("source.txt", source, "text/plain")}).json()[0]
    assert second["status"] == "ready" and second["id"] == first["id"]
    store = client.app.state.store
    assert store.one("SELECT COUNT(*) n FROM documents WHERE project_id=?", [pid])["n"] == 1
    assert second["metadata"]["extraction_manifest"]["raw_sha256"] == hashlib.sha256(source).hexdigest()


def test_two_different_writers_racing_for_a_part_publish_one_whole_value(client, monkeypatch):
    pid = project(client)
    values = [b"First complete value.", b"Other complete value."]
    assert len(values[0]) == len(values[1])
    upload_id = client.post(f"/api/projects/{pid}/uploads/init", json={"filename": "source.txt", "size": len(values[0])}).json()["id"]
    store = client.app.state.store
    original = store.put_path
    barrier = threading.Barrier(2)
    def synchronized(key, path, *args):
        if key.startswith(f"uploads/{upload_id}/parts/"):
            barrier.wait(timeout=5)
        return original(key, path, *args)
    monkeypatch.setattr(store, "put_path", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda value: client.put(f"/api/uploads/{upload_id}/parts/1", content=value), values))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    expected = next(value for value in values if hashlib.sha256(value).hexdigest() == winner["etag"])
    assert len(list(store.object_path(f"uploads/{upload_id}/parts/1").glob("*"))) == 1
    complete = client.post(f"/api/uploads/{upload_id}/complete", json={"parts": [winner]})
    assert complete.status_code == 200, complete.text
    identity = complete.json()["document"]["id"]
    assert client.get(f"/api/documents/{identity}/source").content == expected


def test_new_run_freezes_prompt_metadata_when_library_metadata_changes(client, monkeypatch):
    from test_v3_api import start_mock, upload
    captured = []
    pid = project(client)
    document = upload(client, pid, "A frozen author is part of the interpretation context.")
    run, _ = start_mock(client, pid, monkeypatch)
    current = client.patch(f"/api/documents/{document['id']}", json={"author": "Later correction", "title": "New title"}).json()
    assert current["author"] == "Later correction"
    async def analyze(source, *_args, **_kwargs):
        captured.append(source)
        return {"findings": [], "assessments": [], "unit_statuses": [], "coverage": {"complete": False}, "usage": {}, "errors": []}
    monkeypatch.setattr("doxa.contextual.analyze_units", analyze)
    client.portal.call(client.app.state.work_once, run["id"])
    assert captured and captured[0]["author"] == document["author"]
    assert captured[0]["title"] == document["title"]
    stored = client.app.state.store.one("SELECT config_json FROM runs WHERE id=?", [run["id"]])
    config = json.loads(stored["config_json"])
    assert config["method_config_version"] == "0.4" and len(config["source_metadata_hash"]) == 64


def test_final_unit_invalidates_cached_coverage_and_counts_all_decisions(client, monkeypatch):
    from test_v3_api import finish, query, start_mock, upload
    pid = project(client)
    upload(client, pid, "COUNTER. The writer rejects collective blame.")
    run, _ = start_mock(client, pid, monkeypatch)
    initial = query(client, pid, run_id=run["id"])
    before = initial["metrics"]["coverage_detail"]
    assert before["sources_total"] == 1 and before["sources_done"] == 0
    assert before["decisions_total"] > 0 and before["decisions_done"] == 0
    assert finish(client, run["id"])["status"] == "completed"
    current = query(client, pid, run_id=run["id"], results=["meets_criterion"])
    detail = current["metrics"]["coverage_detail"]
    assert detail["sources_done"] == 1 and detail["units_done"] == detail["units_total"]
    assert detail["decisions_done"] == detail["decisions_total"] == before["decisions_total"]
    assert current["metrics"]["findings"] == 0  # display filters do not erase processing coverage
    assert current["snapshot_version"] != initial["snapshot_version"]
    events = client.app.state.store.query("SELECT payload_json FROM request_attempt_events WHERE run_id=?", [run["id"]])
    assert {json.loads(row["payload_json"])["status"] for row in events} >= {"started", "completed"}
    # A corrupted/missing row cannot be made complete by its historical unit
    # status or by deriving expectations from the remaining observed outcomes.
    store = client.app.state.store
    missing = store.one("SELECT id FROM v3_assessments WHERE run_id=? LIMIT 1", [run["id"]])["id"]
    from doxa.workspace import Workspace
    store.batch([{"sql": "DELETE FROM v3_assessments WHERE id=?", "params": [missing]}, Workspace.touch_statement(pid)])
    incomplete = query(client, pid, run_id=run["id"])["metrics"]
    assert incomplete["coverage_detail"]["sources_done"] == 0
    assert incomplete["coverage_detail"]["decisions_done"] + 1 == incomplete["coverage_detail"]["decisions_total"]
    assert incomplete["coverage"] == 0


def test_legacy_run_uses_existing_frozen_manifest_without_rewriting_config(client, monkeypatch):
    from test_v3_api import start_mock, upload
    pid = project(client)
    document = upload(client, pid, "A historical bibliographic record.")
    run, _ = start_mock(client, pid, monkeypatch)
    store = client.app.state.store
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id=?", [run["id"]])["config_json"])
    for key in ("method_config_version", "method_fingerprint", "method_config", "source_metadata_hash"):
        config.pop(key, None)
    original = json.dumps(config)
    store.execute("UPDATE runs SET config_json=? WHERE id=?", [original, run["id"]])
    store.execute("DELETE FROM run_source_metadata WHERE run_id=?", [run["id"]])
    client.patch(f"/api/documents/{document['id']}", json={"author": "Later edited author"})
    observed = []
    async def analyze(source, *_args, **_kwargs):
        observed.append(source["author"])
        return {"coverage": {"complete": False}, "usage": {}, "errors": []}
    monkeypatch.setattr("doxa.contextual.analyze_units", analyze)
    client.portal.call(client.app.state.work_once, run["id"])
    assert observed == [document["author"]]
    assert store.one("SELECT config_json FROM runs WHERE id=?", [run["id"]])["config_json"] == original
