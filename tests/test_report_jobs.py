"""Durable publication correctness; synthetic records are not accuracy evidence."""
import gzip
import hashlib
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from doxa.report_jobs import ReportJobs, register
from doxa import report_jobs, report_source
from doxa.workspace import Workspace, WorkspaceQuery
from test_recorded_export import recorded_workspace as recorded_workspace


@pytest.fixture
def prepared(recorded_workspace):
    store, root = recorded_workspace
    query = Workspace(store).create("project", WorkspaceQuery(run_id="run", duplicate_policy="sources", results=["meets_criterion", "does_not_meet", "uncertain", "not_applicable"]))
    return store, root, query


def make_job(prepared, format="html"):
    store, _, query = prepared
    manager = ReportJobs(store)
    return manager, manager.create("project", {"query_id": query["query_id"], "format": format, "profile": "briefing", "publication_type": "briefing"})


def test_capture_checkpoint_restart_and_original_frozen_scope(prepared):
    manager, job = make_job(prepared)
    store = manager.store
    assert job["status"] == "queued" and job["artifacts"] == []
    assert manager.work_once(job["id"], deadline_seconds=80)["pending"]
    frozen_state = json.loads(store.one("SELECT state_json FROM report_jobs WHERE id=?", [job["id"]])["state_json"])
    assert frozen_state["frozen"]
    # A later library edit cannot change the already-materialized source selection.
    store.execute("UPDATE documents SET title='Changed after freeze' WHERE id='source-a'")
    Workspace(store).touch("project")
    manager.cancel(job["id"])
    assert manager.get(job["id"])["status"] == "cancelled"
    manager.restart(job["id"])
    assert manager.work_once(job["id"])["status"] == "completed"
    completed = manager.get(job["id"])
    assert {x["format"] for x in completed["artifacts"]} == {"html", "snapshot", "manifest", "inputs"}
    row = store.one("SELECT object_key FROM report_job_artifacts WHERE job_id=? AND format='snapshot'", [job["id"]])
    snapshot = json.loads(gzip.decompress(store.get_bytes(row["object_key"])))
    assert len(snapshot["documents"]) == 2 and len(snapshot["findings"]) == 5
    assert snapshot["documents"][0]["title"] != "Changed after freeze"
    assert {x["result"] for x in snapshot["findings"]} == {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
    assert store.one("SELECT COUNT(*) n FROM report_job_parts WHERE job_id=? AND stage='assessments'", [job["id"]])["n"] == 1


def test_source_revision_failure_is_explicit_and_not_restartable(prepared):
    manager, job = make_job(prepared)
    Workspace(manager.store).touch("project")
    assert manager.work_once(job["id"])["status"] == "failed"
    value = manager.get(job["id"])
    assert value["error"]["code"] == "source_changed" and not value["can_restart"]
    assert value["artifacts"] == []


def test_queued_cancel_and_exclusive_lease(prepared):
    manager, job = make_job(prepared)
    manager.cancel(job["id"])
    assert manager.work_once(job["id"])["status"] == "cancelled"
    manager.restart(job["id"])
    manager.store.execute("UPDATE report_jobs SET status='running',lease_token='another',lease_until=? WHERE id=?", [time.time() + 300, job["id"]])
    assert manager.work_once(job["id"])["pending"]
    assert manager.store.one("SELECT COUNT(*) n FROM report_job_parts WHERE job_id=?", [job["id"]])["n"] == 0


def test_artifacts_are_not_ready_when_upload_fails(prepared, monkeypatch):
    manager, job = make_job(prepared)
    def fail(*_args, **_kwargs):
        raise OSError("Synthetic object storage outage")
    monkeypatch.setattr(manager.store, "put_path", fail)
    assert manager.work_once(job["id"])["status"] == "failed"
    assert manager.get(job["id"])["artifacts"] == []
    assert manager.store.one("SELECT COUNT(*) n FROM report_job_artifacts WHERE job_id=?", [job["id"]])["n"] == 0


def test_cancel_between_part_upload_and_commit_does_not_advance_checkpoint(prepared, monkeypatch):
    manager, job = make_job(prepared)
    original = manager.store.put_bytes
    def cancel_after_upload(*args, **kwargs):
        value = original(*args, **kwargs)
        manager.cancel(job["id"])
        return value
    monkeypatch.setattr(manager.store, "put_bytes", cancel_after_upload)
    assert manager.work_once(job["id"])["status"] == "cancelled"
    assert manager.store.one("SELECT COUNT(*) n FROM report_job_parts WHERE job_id=?", [job["id"]])["n"] == 0
    assert not manager.get(job["id"])["artifacts"]


def test_expired_lease_recovers_and_terminal_redelivery_does_no_work(prepared, monkeypatch):
    manager, job = make_job(prepared)
    manager.store.execute("UPDATE report_jobs SET status='running',lease_token='crashed',lease_until=0 WHERE id=?", [job["id"]])
    assert manager.work_once(job["id"])["status"] == "completed"
    original = manager.get(job["id"])
    monkeypatch.setattr(manager.store, "get_json", lambda *_: pytest.fail("Terminal redelivery must not reread or render inputs"))
    assert manager.work_once(job["id"])["status"] == "completed"
    assert manager.get(job["id"])["artifacts"] == original["artifacts"]


def test_reclaimed_lease_never_reuses_obsolete_renderer_paths(prepared):
    manager, job = make_job(prepared)
    obsolete = manager.store.root / "report-work" / job["id"] / "crashed" / "output"
    obsolete.mkdir(parents=True)
    (obsolete / "report.html").write_text("Obsolete renderer must not become the ready artifact")
    manager.store.execute("UPDATE report_jobs SET status='running',lease_token='crashed',lease_until=0 WHERE id=?", [job["id"]])
    assert manager.work_once(job["id"])["status"] == "completed"
    assert (obsolete / "report.html").read_text() == "Obsolete renderer must not become the ready artifact"
    assert list((manager.store.root / "report-work" / job["id"]).iterdir()) == [obsolete.parent]
    item = manager.store.one("SELECT object_key FROM report_job_artifacts WHERE job_id=? AND format='html'", [job["id"]])
    assert "Obsolete renderer" not in manager.store.get_bytes(item["object_key"]).decode()


@pytest.mark.parametrize("always_slow", [False, True])
def test_render_budget_is_reserved_after_materialization_without_infinite_retry(prepared, monkeypatch, always_slow):
    manager, job = make_job(prepared, "xlsx")
    clock = [0]
    monkeypatch.setattr(report_jobs, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    original = report_source.materialize
    calls = [0]
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        if always_slow or calls[0] == 0:
            clock[0] += 180
        calls[0] += 1
        return result
    monkeypatch.setattr(report_source, "materialize", delayed)
    assert manager.work_once(job["id"])["status"] == "queued"
    state = json.loads(manager.store.one("SELECT state_json FROM report_jobs WHERE id=?", [job["id"]])["state_json"])
    assert state["frozen"] and state["render_deferred"]
    assert not manager.get(job["id"])["artifacts"]
    result = manager.work_once(job["id"])
    if always_slow:
        assert result["status"] == "failed"
        assert manager.get(job["id"])["error"]["code"] == "render_timeout"
    else:
        assert result["status"] == "completed"


def test_remote_queue_delivery_failure_retains_durable_job_and_never_starts_loop(prepared, monkeypatch):
    manager, _ = make_job(prepared)
    config = manager.store.get_json(manager.store.one("SELECT config_key FROM report_jobs LIMIT 1")["config_key"])
    monkeypatch.setattr("doxa.report_source.source_config", lambda *_: config)
    local = manager.store
    class RemoteView:
        remote = True
        def __getattr__(self, name):
            return getattr(local, name)
    manager.store = RemoteView()
    delivered = []
    def fail(identity):
        delivered.append(identity)
        raise OSError("Synthetic queue outage")
    monkeypatch.setattr(manager.store, "enqueue_report", fail, raising=False)
    job = manager.create("project", {"query_id": config["query_id"], "format": "html"})
    assert delivered == [job["id"]] and job["status"] == "queued"
    assert manager._loop_task is None


def test_real_endpoint_range_etag_and_pending_visibility(prepared):
    manager, job = make_job(prepared)
    app = FastAPI()
    register(app, {"db": lambda: manager.store, "report_jobs": lambda: manager, "require_project": lambda _identity: None})
    with TestClient(app) as client:
        path = f'/api/v3/report-jobs/{job["id"]}/artifacts/html'
        assert client.get(path).status_code == 409
        assert manager.work_once(job["id"])["status"] == "completed"
        full = client.get(path)
        assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
        assert full.headers["etag"] == '"' + hashlib.sha256(full.content).hexdigest() + '"'
        assert client.get(path, headers={"If-None-Match": full.headers["etag"]}).status_code == 304
        partial = client.get(path, headers={"Range": "bytes=5-20"})
        assert partial.status_code == 206 and partial.content == full.content[5:21]
        assert client.head(path).content == b""
        assert client.get(path, headers={"Range": "bytes=9999999999-"}).status_code == 416
        assert client.get(path, headers={"Range": "bytes=5-20", "If-Range": '"old"'}).status_code == 200
        assert client.get("/api/v3/projects/project/report-jobs").json()["total"] == 1


def test_cursor_cannot_be_reused_for_another_project(prepared):
    manager, _ = make_job(prepared)
    manager.create("project", {"query_id": prepared[2]["query_id"], "format": "html"})
    page = manager.list("project", limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert len(manager.list("project", limit=1, cursor=page["next_cursor"])["items"]) == 1
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        manager.list("other", cursor=page["next_cursor"])
