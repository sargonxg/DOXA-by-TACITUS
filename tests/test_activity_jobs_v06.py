"""Actual native durable workflows; synthetic embeddings do not measure quality."""
from io import BytesIO
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.model_registry import get_model_profile
from test_access_v05 import managed as managed, write_config


class TinyRuntime:
    def __init__(self):
        self.calls = 0

    def model_profile(self, key):
        return get_model_profile(key)

    def embed_windows(self, texts):
        self.calls += 1
        return {"windows": [{"input_index": i, "start": 0, "end": len(text), "text": text,
                             "embedding": [1.0] + [0.0] * 383} for i, text in enumerate(texts)]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("DOXA_ACCESS_MODE", "DOXA_ACCESS_CONFIG_FILE", "DOXA_INTERNAL_TOKEN", "DOXA_CLOUD_STATE_URL"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


def prepare(client, count=2):
    identity = client.post("/api/projects", json={"name": "Durable source collection"}).json()["id"]
    documents = []
    for ordinal in range(count):
        response = client.post(f"/api/projects/{identity}/upload", files={"files": (f"source-{ordinal}.txt", f"Please review the scoped source {ordinal} before Friday.".encode(), "text/plain")})
        assert response.status_code == 200, response.text
        documents.append(response.json()[0])
    return identity, documents


def submit(client, project_id, body, key="same-request"):
    response = client.post(f"/api/v3/projects/{project_id}/jobs", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def test_index_cancellation_restart_reuses_committed_document_bundle(client, monkeypatch):
    pytest.importorskip("numpy")
    from doxa.semantic import SemanticIndex
    project, docs = prepare(client)
    manager = client.app.state.activity_jobs
    runtime = TinyRuntime()
    manager.services["cpu_runtime"] = lambda: runtime
    job = submit(client, project, {"kind": "index", "document_ids": [doc["id"] for doc in docs]})
    build_document = SemanticIndex._build_document
    cancelled = False
    def interrupt(self, *args, **kwargs):
        nonlocal cancelled
        value = build_document(self, *args, **kwargs)
        if not cancelled:
            cancelled = True
            manager.cancel(job["id"])
        return value
    monkeypatch.setattr(SemanticIndex, "_build_document", interrupt)
    assert manager.work_once(job["id"])["status"] == "cancelled"
    assert runtime.calls == 1
    manager.restart(job["id"])
    result = manager.work_once(job["id"])
    assert result["status"] == "completed"
    assert result["result"]["documents"] == 2
    assert result["result"]["cached_documents"] == 1
    assert runtime.calls == 2
    assert manager.work_once(job["id"])["attempts"] == result["attempts"]
    activity = client.get(f"/api/v3/projects/{project}/activity")
    assert activity.status_code == 200
    assert activity.json()["jobs"]["items"][0]["id"] == job["id"]


def test_idempotency_and_source_revision_failure_do_not_broaden_scope(client):
    project, docs = prepare(client)
    manager = client.app.state.activity_jobs
    manager.services["cpu_runtime"] = lambda: TinyRuntime()
    body = {"kind": "index", "document_ids": [docs[0]["id"]]}
    job = submit(client, project, body)
    assert submit(client, project, body)["id"] == job["id"]
    conflict = client.post(f"/api/v3/projects/{project}/jobs", json={"kind": "index", "document_ids": [docs[1]["id"]]}, headers={"Idempotency-Key": "same-request"})
    assert conflict.status_code == 409
    manager.store.put_json(docs[0]["blocks_key"], [{"id": "different", "text": "Changed after submission"}])
    result = manager.work_once(job["id"])
    assert result["status"] == "failed" and result["error"]["code"] == "source_changed"
    assert not result["can_restart"]
    assert manager.store.one("SELECT COUNT(*) n FROM activity_jobs")["n"] == 1
    assert manager.store.one("SELECT COUNT(*) n FROM budget_reservations")["n"] == 0


def test_guided_report_reuses_real_report_worker_and_original_frozen_brief(client):
    project, docs = prepare(client, 1)
    original = client.put(f"/api/pilot/projects/{project}/brief", json={"profile_id": "company", "expected_revision": 0, "question": "Which explicit requests appear in this selected writing?"}).json()
    job = submit(client, project, {"kind": "guided_report", "document_ids": [docs[0]["id"]], "formats": ["html", "json"]})
    manager = client.app.state.activity_jobs
    # Rendering must use captured evidence even if the source catalog changes.
    manager.store.execute("UPDATE documents SET title='Changed later' WHERE id=?", [docs[0]["id"]])
    client.put(f"/api/pilot/projects/{project}/brief", json={"profile_id": "personal", "expected_revision": 1})
    pending = manager.work_once(job["id"])
    assert pending["status"] == "queued"
    children = json.loads(manager._row(job["id"])["state_json"])["report_job_ids"]
    reports = client.app.state.report_jobs
    for identity in children:
        assert reports.work_once(identity)["status"] == "completed"
    complete = manager.work_once(job["id"])
    assert complete["status"] == "completed"
    assert {item["format"] for item in complete["result"]["artifacts"]} == {"html", "json"}
    artifact = next(item for item in complete["result"]["artifacts"] if item["format"] == "json")
    response = client.get(artifact["download_url"])
    assert response.status_code == 200
    report = response.json()
    assert report["report_brief"]["brief"]["question"] == original["question"]
    assert report["documents"][0]["title"] != "Changed later"
    assert response.headers["etag"] == '"' + artifact["sha256"] + '"'
    assert reports.get(children[0])["publication_type"] == "guided"


def test_guided_cancel_restart_propagates_without_rereading_sources(client):
    project, docs = prepare(client, 1)
    job = submit(client, project, {"kind": "guided_report", "document_ids": [docs[0]["id"]], "formats": ["json"]})
    manager = client.app.state.activity_jobs
    manager.work_once(job["id"])
    children = json.loads(manager._row(job["id"])["state_json"])["report_job_ids"]
    assert manager.cancel(job["id"])["status"] == "cancelled"
    assert all(client.app.state.report_jobs.get(child)["status"] == "cancelled" for child in children)
    before = manager.store.get_json(manager._row(job["id"])["config_key"])
    manager.restart(job["id"])
    assert client.app.state.report_jobs.work_once(children[0])["status"] == "completed"
    assert manager.work_once(job["id"])["status"] == "completed"
    assert manager.store.get_json(manager._row(job["id"])["config_key"]) == before


def test_policy_change_pauses_pending_publication_before_any_child_work(client):
    project, docs = prepare(client, 1)
    job = submit(client, project, {"kind": "guided_report", "document_ids": [docs[0]["id"]], "formats": ["json"]})
    policy = client.get(f"/api/pilot/projects/{project}/privacy-policy").json()
    policy["policy"]["allow_exports"] = False
    assert client.put(f"/api/pilot/projects/{project}/privacy-policy", json={"policy": policy["policy"], "expected_revision": policy["revision"]}).status_code == 200
    manager = client.app.state.activity_jobs
    result = manager.work_once(job["id"])
    assert result["status"] == "paused_access"
    assert result["error"]["code"] == "authorization_required"
    assert not manager.store.query("SELECT id FROM report_jobs WHERE project_id=?", [project])


def archive_bytes(entries):
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, text in entries:
            bundle.writestr(name, text)
    return output.getvalue()


def import_archive(client, project, content, *, key="archive"):
    session = client.post(f"/api/v3/projects/{project}/imports", json={"name": "Saved archive", "total_files": 3}).json()
    response = client.post(f"/api/v3/projects/{project}/jobs/import-archive", data={"import_id": session["id"]}, files={"file": ("sources.zip", content, "application/zip")}, headers={"Idempotency-Key": key})
    return response, session


def test_durable_import_resumes_after_committed_file_and_discloses_failures(client, monkeypatch):
    project, _docs = prepare(client, 1)
    data = archive_bytes([("one.txt", "First source"), ("broken.pdf", b"This is not a PDF"), ("two.txt", "Second source")])
    response, session = import_archive(client, project, data)
    assert response.status_code == 202, response.text
    job = response.json()
    manager = client.app.state.activity_jobs
    update = manager._update
    stopped = False
    def interrupt(identity, lease, **kwargs):
        nonlocal stopped
        update(identity, lease, **kwargs)
        if kwargs.get("state", {}).get("cursor") == 1 and not stopped:
            stopped = True
            manager.cancel(identity)
    monkeypatch.setattr(manager, "_update", interrupt)
    assert manager.work_once(job["id"])["status"] == "cancelled"
    first = manager.store.query("SELECT document_id FROM v3_import_files WHERE import_id=?", [session["id"]])
    assert len(first) == 1
    manager.restart(job["id"])
    result = manager.work_once(job["id"])
    assert result["status"] == "partial"
    assert result["result"]["ready_documents"] == 2
    assert result["result"]["failed_documents"] == 1
    assert manager.store.one("SELECT COUNT(*) n FROM v3_import_files WHERE import_id=?", [session["id"]])["n"] == 3
    assert manager.store.one("SELECT COUNT(*) n FROM documents WHERE id=?", [first[0]["document_id"]])["n"] == 1
    assert manager.store.one("SELECT COUNT(*) n FROM privacy_project_activity")["n"] == 0


def test_unsafe_archive_is_rejected_before_durable_job_creation(client):
    project, _docs = prepare(client, 1)
    response, _session = import_archive(client, project, archive_bytes([("../escape.txt", "Unsafe")]))
    assert response.status_code == 422
    assert client.app.state.store.one("SELECT COUNT(*) n FROM activity_jobs")["n"] == 0


def test_changed_frozen_job_input_is_not_executed(client):
    project, docs = prepare(client, 1)
    manager = client.app.state.activity_jobs
    manager.services["cpu_runtime"] = lambda: TinyRuntime()
    job = submit(client, project, {"kind": "index", "document_ids": [docs[0]["id"]]})
    row = manager._row(job["id"])
    config = manager.store.get_json(row["config_key"])
    config["document_ids"] = []
    manager.store.put_json(row["config_key"], config)
    result = manager.work_once(job["id"])
    assert result["status"] == "failed" and result["error"]["code"] == "source_changed"


def test_managed_job_ids_are_project_scoped_and_revocation_pauses_execution(managed):
    client = managed.client
    managed.app.state.activity_jobs.services["cpu_runtime"] = lambda: TinyRuntime()
    response = client.post("/api/v3/projects/allowed/jobs", json={"kind": "index", "document_ids": ["doc-allowed"]}, headers={**managed.headers("analyst"), "Idempotency-Key": "managed"})
    assert response.status_code == 202, response.text
    job = response.json()
    # Another project account cannot infer whether a supplied job ID exists.
    private = client.post("/api/v3/projects/private/jobs", json={"kind": "index", "document_ids": ["doc-private"]}, headers={**managed.headers("owner"), "Idempotency-Key": "private"})
    assert private.status_code == 202, private.text
    assert client.get(f"/api/v3/jobs/{private.json()['id']}", headers=managed.headers("analyst")).status_code == 404
    denied = client.post("/api/v3/projects/allowed/jobs", json={"kind": "index", "document_ids": ["doc-allowed"]}, headers={**managed.headers("viewer"), "Idempotency-Key": "viewer"})
    assert denied.status_code == 403
    cancel = client.post(f"/api/v3/jobs/{job['id']}/cancel", headers=managed.headers("viewer"))
    assert cancel.status_code == 403
    assert managed.app.state.activity_jobs.get(job["id"])["status"] == "queued"
    write_config(managed.config, [user for user in managed.users if user["id"] != "analyst"])
    result = managed.app.state.activity_jobs.work_once(job["id"])
    assert result["status"] == "paused_access"
    assert result["error"]["code"] == "authorization_required"


def test_missing_local_model_is_explained_before_any_job_is_scheduled(client):
    project, docs = prepare(client, 1)
    runtime = TinyRuntime()
    runtime.health = lambda *_args, **_kwargs: {"ok": False}
    manager = client.app.state.activity_jobs
    manager.services["cpu_runtime"] = lambda: runtime
    response = client.post(f"/api/v3/projects/{project}/jobs", json={"kind": "index", "document_ids": [docs[0]["id"]]}, headers={"Idempotency-Key": "no-model"})
    assert response.status_code == 422
    assert "nothing was downloaded or scheduled" in response.text
    assert manager.store.one("SELECT COUNT(*) n FROM activity_jobs")["n"] == 0
    assert runtime.calls == 0


def test_source_corruption_during_index_build_never_publishes_a_complete_manifest(client, monkeypatch):
    pytest.importorskip("numpy")
    from doxa.semantic import SemanticIndex
    project, docs = prepare(client)
    manager = client.app.state.activity_jobs
    runtime = TinyRuntime()
    manager.services["cpu_runtime"] = lambda: runtime
    job = submit(client, project, {"kind": "index", "document_ids": [doc["id"] for doc in docs]})
    later = sorted(docs, key=lambda document: document["id"])[-1]
    build_document = SemanticIndex._build_document
    changed = False
    def corrupt_after_first(self, *args, **kwargs):
        nonlocal changed
        value = build_document(self, *args, **kwargs)
        if not changed:
            changed = True
            manager.store.put_json(later["blocks_key"], [{"id": "replaced", "text": "Changed during indexing"}])
        return value
    monkeypatch.setattr(SemanticIndex, "_build_document", corrupt_after_first)
    result = manager.work_once(job["id"])
    assert result["status"] == "failed" and result["error"]["code"] == "source_changed"
    assert runtime.calls == 1
    prefix = SemanticIndex(manager.store, runtime)._prefix(project)
    assert not manager.store.object_path(prefix + "/manifest.json").exists()


def test_concurrent_same_key_submissions_publish_one_logical_job(client):
    from concurrent.futures import ThreadPoolExecutor
    project, docs = prepare(client, 1)
    manager = client.app.state.activity_jobs
    manager.services["cpu_runtime"] = lambda: TinyRuntime()
    body = {"kind": "index", "document_ids": [docs[0]["id"]]}
    def submit_once(_):
        return submit(client, project, body, key="concurrent")["id"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        identities = list(pool.map(submit_once, range(3)))
    assert len(set(identities)) == 1
    assert manager.store.one("SELECT COUNT(*) n FROM activity_jobs")["n"] == 1


def test_slow_source_commits_before_soft_deadline_so_restart_can_finish(client):
    pytest.importorskip("numpy")
    import time
    project, docs = prepare(client)
    manager = client.app.state.activity_jobs
    class SlowRuntime(TinyRuntime):
        def embed_windows(self, texts):
            time.sleep(1.05)
            return super().embed_windows(texts)
    runtime = SlowRuntime()
    manager.services["cpu_runtime"] = lambda: runtime
    job = submit(client, project, {"kind": "index", "document_ids": [doc["id"] for doc in docs]})
    # Each source is slower than the requested scheduling checkpoint. Its
    # accepted bundle must still commit; otherwise retries can loop forever.
    assert manager.work_once(job["id"], deadline_seconds=1)["status"] == "queued"
    assert manager.work_once(job["id"], deadline_seconds=1)["status"] == "queued"
    result = manager.work_once(job["id"], deadline_seconds=1)
    assert result["status"] == "completed"
    assert runtime.calls == 2
    assert result["result"]["cached_documents"] == 2


def accepted_quote(client, project, *, profile="public", scope=None, key="quote"):
    from doxa.pilot_commerce import create_quote, accept_quote
    store = client.app.state.store
    offer = create_quote(store, project, {"idempotency_key": key, "profile_id": profile,
        "fixed_price_cents": 5000, "processing_cap_microusd": 100000,
        "scope": scope or {"documents": 10, "characters": 1000000, "criteria": 1000, "ocr_pages": 100}},
        operator_id="test-operator")
    return accept_quote(store, project, offer["id"], actor_id="test-customer", scope_sha256=offer["scope_sha256"])


def test_required_quote_guards_both_report_routes_and_actual_saved_scope(client, monkeypatch):
    project, docs = prepare(client, 1)
    monkeypatch.setenv("DOXA_REQUIRE_ACCEPTED_QUOTE", "true")
    body = {"document_ids": [docs[0]["id"]], "formats": ["json"]}
    for route, data in ((f"/api/pilot/projects/{project}/reports", body),
                        (f"/api/v3/projects/{project}/jobs", {"kind": "guided_report", **body})):
        missing = client.post(route, json=data, headers={"Idempotency-Key": "required"})
        assert missing.status_code == 409 and "accepted" in missing.text
    wrong_profile = accepted_quote(client, project, profile="company", key="wrong-profile")
    wrong_scope = accepted_quote(client, project, scope={"documents": 10, "characters": 1, "criteria": 1000, "ocr_pages": 0}, key="too-small")
    for quote in (wrong_profile, wrong_scope):
        denied = client.post(f"/api/v3/projects/{project}/jobs", json={"kind": "guided_report", **body, "quote_id": quote["id"]}, headers={"Idempotency-Key": quote["id"]})
        assert denied.status_code == 409
    quote = accepted_quote(client, project)
    allowed = client.post(f"/api/v3/projects/{project}/jobs", json={"kind": "guided_report", **body, "quote_id": quote["id"]}, headers={"Idempotency-Key": "authorized"})
    assert allowed.status_code == 202, allowed.text
    manager = client.app.state.activity_jobs
    assert manager.work_once(allowed.json()["id"])["status"] == "queued"
    children = json.loads(manager._row(allowed.json()["id"])["state_json"])["report_job_ids"]
    assert client.app.state.report_jobs.work_once(children[0])["status"] == "completed"
    assert manager.work_once(allowed.json()["id"])["status"] == "completed"


@pytest.mark.parametrize("authority_change", ["revoked", "cap_exceeded"])
def test_quote_authority_is_rechecked_by_parent_and_renderer_before_publication(client, monkeypatch, authority_change):
    project, docs = prepare(client, 1)
    monkeypatch.setenv("DOXA_REQUIRE_ACCEPTED_QUOTE", "true")
    quote = accepted_quote(client, project)
    job = submit(client, project, {"kind": "guided_report", "document_ids": [docs[0]["id"]], "formats": ["json"], "quote_id": quote["id"]})
    manager = client.app.state.activity_jobs
    manager.work_once(job["id"])
    children = json.loads(manager._row(job["id"])["state_json"])["report_job_ids"]
    if authority_change == "revoked":
        revoked = client.post(f"/api/pilot/projects/{project}/quotes/{quote['id']}/revoke", json={"reason_code": "security_review"})
        assert revoked.status_code == 200, revoked.text
    else:
        from doxa.pilot_commerce import reserve_usage, settle_usage
        reserve_usage(manager.store, project, quote["id"], event_key="actual-service-work", category="compute", reserved_microusd=100000)
        settle_usage(manager.store, project, "actual-service-work", actual_microusd=110000)
    child = client.app.state.report_jobs.work_once(children[0])
    assert child["status"] == "failed"
    assert client.app.state.report_jobs.get(children[0])["artifacts"] == []
    result = manager.work_once(job["id"])
    assert result["status"] == "paused_access"
    assert result["error"]["code"] == "delivery_authority_changed"
    assert not result["can_restart"]
    assert not client.app.state.report_jobs.get(children[0])["can_restart"]


def test_legacy_report_rechecks_quote_after_render_before_saving_outputs(client, monkeypatch):
    import doxa.pilot_reports as reports
    project, docs = prepare(client, 1)
    monkeypatch.setenv("DOXA_REQUIRE_ACCEPTED_QUOTE", "true")
    quote = accepted_quote(client, project)
    original = reports.build_pilot_report
    def revoke_after_render(*args, **kwargs):
        result = original(*args, **kwargs)
        from doxa.pilot_commerce import revoke_quote
        revoke_quote(client.app.state.store, project, quote["id"], actor_id="test-operator", reason_code="scope_changed")
        return result
    monkeypatch.setattr(reports, "build_pilot_report", revoke_after_render)
    response = client.post(f"/api/pilot/projects/{project}/reports", json={"document_ids": [docs[0]["id"]], "formats": ["json"], "quote_id": quote["id"]})
    assert response.status_code == 409
    assert client.app.state.store.one("SELECT COUNT(*) n FROM pilot_reports WHERE project_id=?", [project])["n"] == 0


def test_failed_input_write_keeps_precommitted_artifact_ownership(client, monkeypatch):
    project, docs = prepare(client, 1)
    manager = client.app.state.activity_jobs
    manager.services["cpu_runtime"] = lambda: TinyRuntime()
    original = manager.store.put_json
    def fail_after_write(key, value):
        original(key, value)
        if key.startswith("activity-jobs/"):
            raise OSError("Synthetic failure after private input write")
    monkeypatch.setattr(manager.store, "put_json", fail_after_write)
    with pytest.raises(OSError):
        manager.create(project, {"kind": "index", "document_ids": [docs[0]["id"]]}, idempotency_key="failed-write")
    assert manager.store.one("SELECT COUNT(*) n FROM activity_jobs")["n"] == 0
    owned = manager.store.query("SELECT path FROM privacy_artifacts WHERE project_id=? AND kind='activity_job'", [project])
    assert len(owned) == 1
    assert (manager.store.root / owned[0]["path"] / "config.json").is_file()
