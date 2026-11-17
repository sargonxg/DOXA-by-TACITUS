"""Dedicated pilot authorization, real resources and browser session revocation.

Fixtures are synthetic software records; no inference or independent human
interpretation accuracy is claimed by these tests.
"""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from doxa.access import AccessControl, COOKIE, WorkerAuthorizationError, issue_token, permission_for, token_hash
from doxa.api import create_app
from doxa.store import utcnow
from test_workspace import seed_assessment, seed_document, seed_run


def write_config(path, users, *, secret="a-separate-random-session-secret-for-tests"):
    path.write_text(json.dumps({"version": 1, "session_secret": secret, "users": users}))
    path.chmod(0o600)


def user(identity, role, token, projects=(), **extra):
    return {"id": identity, "role": role, "token_sha256": token_hash(token),
            "project_ids": list(projects), **extra}


@pytest.fixture
def managed(tmp_path, monkeypatch):
    for key in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(key, raising=False)
    credentials = {name: issue_token() for name in ("owner", "analyst", "reviewer", "viewer", "reveal")}
    users = [user("owner", "owner", credentials["owner"], all_projects=True),
             user("analyst", "analyst", credentials["analyst"], ["allowed"]),
             user("reviewer", "reviewer", credentials["reviewer"], ["allowed"]),
             user("viewer", "viewer", credentials["viewer"], ["allowed"]),
             user("reveal", "viewer", credentials["reveal"], ["allowed"], permissions=["identity_reveal"])]
    config = tmp_path / "access.json"
    write_config(config, users)
    monkeypatch.setenv("DOXA_ACCESS_MODE", "managed")
    monkeypatch.setenv("DOXA_ACCESS_CONFIG_FILE", str(config))
    app = create_app(tmp_path / "data", start_workers=False, use_saved_showcase=False)
    with TestClient(app, base_url="https://testserver") as client:
        store = app.state.store
        for project in ("allowed", "private"):
            store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [project, project, utcnow()])
            store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", ["batch-" + project, project, project, utcnow()])
            seed_document(store, "doc-" + project, project, batch="batch-" + project, text="Source statement.")
            seed_run(store, "run-" + project, project, sources=[{"id": "doc-" + project, "sha256": "doc-" + project + "-hash"}])
            seed_assessment(store, "finding-" + project, "doc-" + project, "run-" + project, project)
        yield SimpleNamespace(client=client, app=app, store=store, config=config, users=users,
                              credentials=credentials, headers=lambda identity: {"Authorization": "Bearer " + credentials[identity]})


def test_local_session_is_compatible_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_ACCESS_MODE", raising=False)
    monkeypatch.delenv("DOXA_ACCESS_CONFIG_FILE", raising=False)
    with TestClient(create_app(tmp_path, start_workers=False)) as client:
        response = client.get("/api/access/session")
        assert response.status_code == 200
        value = response.json()
        assert value["mode"] == "local" and value["authenticated"]
        assert value["principal"]["all_projects"]
        assert "identity_reveal" in value["principal"]["permissions"]
        assert client.get("/api/projects").status_code == 200


def test_managed_authentication_has_no_header_or_body_identity_bypass(managed):
    client = managed.client
    session = client.get("/api/access/session")
    assert session.status_code == 200 and not session.json()["authenticated"]
    assert "no-store" in session.headers["cache-control"]
    assert client.get("/api/health").status_code == 200
    for headers in ({}, {"x-doxa-user": "owner", "x-doxa-role": "owner"},
                    {"cf-access-authenticated-user-email": "owner"}, {"x-doxa-service-token": managed.credentials["owner"]}):
        assert client.get("/api/projects", headers=headers).status_code == 401
    assert client.post("/api/projects", json={"name": "Spoof", "role": "owner"}).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    assert client.get("/api/projects", headers=managed.headers("owner")).status_code == 200


def test_project_list_and_all_resource_variants_enforce_grants(managed):
    client, store = managed.client, managed.store
    headers = managed.headers("analyst")
    assert [item["id"] for item in client.get("/api/projects", headers=headers).json()] == ["allowed"]
    own_query = client.post("/api/v3/projects/allowed/query", json={"run_id": "run-allowed"}, headers=headers).json()
    other_query = client.post("/api/v3/projects/private/query", json={"run_id": "run-private"}, headers=managed.headers("owner")).json()
    for project, query in (("allowed", own_query), ("private", other_query)):
        report = "report-" + project
        key = f"reports/{report}/report.html"
        store.put_bytes(key, ("SECRET-" + project).encode())
        store.execute("INSERT INTO reports(id,project_id,format,profile,object_key,snapshot_key,created_at) VALUES(?,?,?,?,?,?,?)",
                      [report, project, "html", "briefing", key, key, utcnow()])
        book = client.post(f"/api/v3/projects/{project}/casebooks", json={"query_id": query["query_id"], "title": project}, headers=managed.headers("owner"))
        assert book.status_code == 201, book.text
        job = client.post(f"/api/v3/projects/{project}/report-jobs", json={"query_id": query["query_id"], "format": "html"}, headers=managed.headers("owner"))
        assert job.status_code == 202, job.text
        if project == "private":
            book_id, job_id = book.json()["id"], job.json()["id"]
    blocked = [
        "/api/projects/private/documents", "/api/documents/doc-private", "/api/documents/doc-private/source",
        "/api/documents/doc-private/extraction", "/api/runs/run-private", "/api/v3/runs/run-private/status",
        "/api/v3/documents/doc-private/context", "/api/projects/private/findings", "/api/projects/private/dashboard",
        f"/api/v3/queries/{other_query['query_id']}", f"/api/v3/queries/{other_query['query_id']}/findings",
        f"/api/v3/casebooks/{book_id}", f"/api/v3/report-jobs/{job_id}",
        f"/api/v3/report-jobs/{job_id}/artifacts/html", "/api/reports/report-private/download",
    ]
    for path in blocked:
        result = client.get(path, headers=headers)
        assert result.status_code == 404, (path, result.status_code, result.text)
        assert "SECRET-private" not in result.text
    assert client.get("/api/documents/doc-allowed", headers=headers).status_code == 200
    assert client.get("/api/reports/report-allowed/download", headers=headers).content == b"SECRET-allowed"
    assert client.get("/api/projects/missing/documents", headers=managed.headers("owner")).status_code == 404


def test_roles_separate_analysis_review_export_and_global_administration(managed):
    client = managed.client
    viewer, analyst, reviewer = (managed.headers(name) for name in ("viewer", "analyst", "reviewer"))
    assert client.get("/api/settings", headers=viewer).status_code == 403
    assert client.post("/api/projects", json={"name": "Forbidden"}, headers=analyst).status_code == 403
    assert client.post("/api/v3/models/health", json={"model_key": "minilm"}, headers=analyst).status_code == 403
    assert client.post("/api/projects/allowed/upload", files={"files": ("new.txt", b"Text")}, headers=viewer).status_code == 403
    assert client.post("/api/projects/allowed/runs", json={"provider": "lexical"}, headers=reviewer).status_code == 403
    assert client.patch("/api/v3/findings/finding-allowed/review", json={}, headers=analyst).status_code == 403
    assert client.get("/api/documents/doc-allowed/source", headers=viewer).status_code == 403
    assert client.delete("/api/documents/doc-allowed", headers=analyst).status_code == 403
    assert client.post("/api/projects/allowed/upload", files={"files": ("new.txt", b"Text")}, headers=analyst).status_code == 200
    response = client.patch("/api/v3/findings/finding-allowed/review", json={"review_status": "confirmed", "actor_id": "spoof"},
                            headers={**reviewer, "x-doxa-reviewer": "spoof", "x-doxa-role": "owner"})
    assert response.status_code == 200, response.text
    assert response.json()["review_event"]["actor_id"] == "reviewer"
    assert managed.store.one("SELECT actor_id FROM v3_review_events WHERE assessment_id='finding-allowed'")["actor_id"] == "reviewer"
    managed.store.execute("INSERT INTO findings(id,project_id,run_id,document_id,task_id,lens_id,category,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          ["legacy", "allowed", "run-allowed", "doc-allowed", "", "discourse", "fixture", "{}", utcnow()])
    legacy = client.patch("/api/findings/legacy/review", json={"review_status": "confirmed"}, headers=reviewer)
    assert legacy.status_code == 409 and "individual reviewer provenance" in legacy.text
    assert managed.store.one("SELECT COUNT(*) n FROM reviews WHERE finding_id='legacy'")["n"] == 0


def test_session_cookie_supports_downloads_logout_and_credential_rotation(managed):
    client = managed.client
    response = client.post("/api/access/session", json={"token": managed.credentials["analyst"]})
    assert response.status_code == 200 and response.json()["principal"]["id"] == "analyst"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
    assert client.get("/api/documents/doc-allowed").status_code == 200
    uploaded = client.post("/api/projects/allowed/upload", files={"files": ("cookie.txt", b"Private cookie source")})
    assert uploaded.status_code == 200
    original = client.get("/api/documents/" + uploaded.json()[0]["id"] + "/source")
    assert original.status_code == 200 and original.content == b"Private cookie source"
    assert "no-store" in original.headers["cache-control"]
    assert client.get("/api/access/session").json()["authenticated"]
    # Cookie integrity and credentials are independent from browser identity fields.
    saved = client.cookies.get(COOKIE)
    client.cookies.set(COOKIE, saved + "tampered")
    assert client.get("/api/projects").status_code == 401
    client.cookies.clear()
    assert client.post("/api/access/session", json={"token": managed.credentials["analyst"]}).status_code == 200
    managed.users[1]["token_sha256"] = token_hash(issue_token())
    write_config(managed.config, managed.users)
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/access/session", json={"token": managed.credentials["analyst"]}).status_code == 401
    assert client.post("/api/access/session", json={"token": managed.credentials["owner"]}).status_code == 200
    assert client.delete("/api/access/session").status_code == 200
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/access/session", content=b"x" * 2049).status_code == 413
    assert client.post("/api/access/session", json={"token": managed.credentials["owner"]}, headers={"origin": "https://evil.example"}).status_code == 403


def test_project_grants_and_role_changes_revoke_existing_sessions(managed):
    client = managed.client
    assert client.post("/api/access/session", json={"token": managed.credentials["analyst"]}).status_code == 200
    assert client.get("/api/documents/doc-allowed").status_code == 200
    managed.users[1]["project_ids"] = []
    write_config(managed.config, managed.users)
    assert client.get("/api/documents/doc-allowed").status_code == 404
    assert client.get("/api/projects").json() == []
    managed.users[1]["project_ids"] = ["allowed"]
    managed.users[1]["role"] = "viewer"
    write_config(managed.config, managed.users)
    assert client.get("/api/documents/doc-allowed").status_code == 200
    assert client.post("/api/projects/allowed/upload", files={"files": ("x.txt", b"blocked")}).status_code == 403
    managed.users.pop(1)
    write_config(managed.config, managed.users)
    assert client.get("/api/projects").status_code == 401


def test_saved_privacy_export_policy_and_streaming_activity_lease(managed):
    from doxa.privacy_lifecycle import set_policy
    from fastapi.responses import StreamingResponse
    client, store = managed.client, managed.store
    # The body is consumed after the endpoint returns. Its lease must still exist.
    @managed.app.get("/api/projects/{project_id}/documents")
    def streamed(project_id: str):
        def chunks():
            assert store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id=?", [project_id])["n"] == 1
            yield b"first "
            assert store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id=?", [project_id])["n"] == 1
            yield b"second"
        return StreamingResponse(chunks(), media_type="text/plain")
    route = managed.app.routes.pop()
    managed.app.routes.insert(0, route)
    streamed_result = client.get("/api/projects/allowed/documents", headers=managed.headers("analyst"))
    assert streamed_result.content == b"first second"
    assert store.one("SELECT COUNT(*) n FROM privacy_project_activity")["n"] == 0
    set_policy(store, "allowed", {"allow_exports": False}, actor_id="owner")
    assert client.get("/api/documents/doc-allowed/source", headers=managed.headers("analyst")).status_code == 403
    assert client.post("/api/projects/allowed/reports", json={"format": "html"}, headers=managed.headers("analyst")).status_code == 403
    assert client.get("/api/documents/doc-allowed", headers=managed.headers("analyst")).status_code == 200


def test_bad_rotated_config_fails_closed_and_identity_reveal_is_explicit(managed):
    owner = managed.client.get("/api/access/session", headers=managed.headers("owner")).json()
    assert "identity_reveal" not in owner["principal"]["permissions"]
    reveal = managed.client.get("/api/access/session", headers=managed.headers("reveal")).json()
    assert "identity_reveal" in reveal["principal"]["permissions"]
    managed.config.write_text("{broken-json")
    response = managed.client.get("/api/projects", headers=managed.headers("owner"))
    assert response.status_code == 503
    assert str(managed.config) not in response.text
    assert managed.credentials["owner"] not in response.text


def test_external_http_cannot_carry_managed_credentials(managed):
    application = create_app(managed.store.root, start_workers=False, use_saved_showcase=False)
    with TestClient(application, base_url="http://pilot.example", client=("198.51.100.24", 5000)) as external:
        response = external.post("/api/access/session", json={"token": managed.credentials["owner"]})
        assert response.status_code == 403 and "HTTPS" in response.text
        assert "set-cookie" not in response.headers
        assert external.get("/api/projects", headers=managed.headers("owner")).status_code == 403
        assert "no-store" in response.headers["cache-control"]


def test_unreviewed_api_routes_and_internal_loopback_do_not_bypass_auth(managed, monkeypatch):
    @managed.app.get("/api/new-unreviewed-endpoint")
    def accidental_new_endpoint():
        return {"secret": "not authorized by default"}
    route = managed.app.routes.pop()
    managed.app.routes.insert(0, route)
    response = managed.client.get("/api/new-unreviewed-endpoint", headers=managed.headers("owner"))
    assert response.status_code == 403 and "not authorized by default" not in response.text
    @managed.app.delete("/api/projects/{project_id}/upload")
    def accidental_new_method(project_id: str):
        return {"secret": "different method was not reviewed"}
    response = managed.client.delete("/api/projects/allowed/upload", headers=managed.headers("owner"))
    assert response.status_code == 403 and "different method" not in response.text
    assert managed.client.post("/internal/work/run-allowed", headers=managed.headers("owner")).status_code == 401
    monkeypatch.setenv("DOXA_INTERNAL_TOKEN", "internal-" + "x" * 40)
    assert managed.client.get("/api/projects", headers=managed.headers("analyst")).status_code == 200
    assert managed.client.post("/internal/work/run-allowed", headers={"x-doxa-service-token": "internal-" + "x" * 40}).status_code == 200


def test_every_registered_released_api_has_an_explicit_managed_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_ACCESS_MODE", raising=False)
    monkeypatch.delenv("DOXA_ACCESS_CONFIG_FILE", raising=False)
    app = create_app(tmp_path, start_workers=False)
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in {"/api/health", "/api/access/session"}:
            continue
        for method in route.methods:
            if permission_for(path, method) is None:
                missing.append((method, path))
    assert missing == [], missing


def test_configuration_is_private_explicit_and_native_only(tmp_path):
    with pytest.raises(ValueError):
        AccessControl("managed")
    token = issue_token()
    path = tmp_path / "access.json"
    write_config(path, [user("owner", "owner", token, all_projects=True)])
    control = AccessControl("managed", str(path))
    with pytest.raises(RuntimeError, match="dedicated native"):
        control.ensure_native(SimpleNamespace(remote="https://example.invalid"))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private permissions"):
        AccessControl("managed", str(path))
    write_config(path, [user("owner", "owner", token, all_projects=True), user("viewer", "viewer", token)])
    with pytest.raises(ValueError, match="duplicated"):
        AccessControl("managed", str(path))
    write_config(path, [user("viewer", "viewer", token, all_projects=True)])
    with pytest.raises(ValueError):
        AccessControl("managed", str(path))


def test_durable_workers_recheck_current_role_grants_and_removed_users(tmp_path):
    path = tmp_path / "access.json"
    users = [user("owner", "owner", issue_token(), all_projects=True),
             user("analyst", "analyst", issue_token(), ["p"])]
    write_config(path, users)
    control = AccessControl("managed", str(path))
    assert control.require_worker("analyst", "p").id == "analyst"
    assert control.require_worker("analyst", "p", "export").id == "analyst"
    for actor, project, permission in ((None, "p", "analyze"), ("missing", "p", "analyze"),
                                       ("analyst", "private", "analyze"), ("analyst", "p", "admin")):
        with pytest.raises(WorkerAuthorizationError):
            control.require_worker(actor, project, permission)
    users[1]["project_ids"] = []
    write_config(path, users)
    with pytest.raises(WorkerAuthorizationError):
        control.require_worker("analyst", "p")
    users[1]["project_ids"] = ["p"]
    users[1]["role"] = "viewer"
    write_config(path, users)
    with pytest.raises(WorkerAuthorizationError):
        control.require_worker("analyst", "p")
    assert control.require_worker("analyst", "p", "read").role == "viewer"
    users.pop()
    write_config(path, users)
    with pytest.raises(WorkerAuthorizationError):
        control.require_worker("analyst", "p")
    path.write_text("{invalid")
    with pytest.raises(WorkerAuthorizationError, match="configuration is unavailable"):
        control.require_worker("owner", "p")
    assert AccessControl().require_worker(None, "legacy").managed is False


def test_managed_reports_pause_after_revocation_and_require_authorized_restart(managed):
    client, store = managed.client, managed.store
    query = client.post("/api/v3/projects/allowed/query", json={"run_id": "run-allowed"}, headers=managed.headers("analyst")).json()
    created = client.post("/api/v3/projects/allowed/report-jobs", json={"query_id": query["query_id"], "format": "html"}, headers=managed.headers("analyst"))
    assert created.status_code == 202, created.text
    identity = created.json()["id"]
    assert created.json()["execution"]["actor_id"] == "analyst"
    manager = managed.app.state.report_jobs
    config_key = store.one("SELECT config_key FROM report_jobs WHERE id=?", [identity])["config_key"]
    frozen_config = store.get_bytes(config_key)
    managed.users[1]["role"] = "viewer"
    write_config(managed.config, managed.users)
    assert manager.work_once(identity)["status"] == "failed"
    assert manager.get(identity)["error"]["code"] == "authorization_required"
    assert not store.query("SELECT * FROM report_job_parts WHERE job_id=?", [identity])
    assert client.post(f"/api/v3/report-jobs/{identity}/restart", headers=managed.headers("analyst")).status_code == 403
    resumed = client.post(f"/api/v3/report-jobs/{identity}/restart", headers=managed.headers("reviewer"))
    assert resumed.status_code == 200 and resumed.json()["execution"]["actor_id"] == "reviewer"
    assert store.get_bytes(config_key) == frozen_config
    # Historical job state gets no implicit local-operator authority in managed mode.
    state = json.loads(store.one("SELECT state_json FROM report_jobs WHERE id=?", [identity])["state_json"])
    state.pop("execution_actor_id")
    state.pop("managed_access")
    store.execute("UPDATE report_jobs SET state_json=? WHERE id=?", [json.dumps(state), identity])
    assert manager.work_once(identity)["status"] == "failed"
    assert manager.get(identity)["error"]["code"] == "authorization_required"
    assert client.post(f"/api/v3/report-jobs/{identity}/restart", headers=managed.headers("owner")).status_code == 200
    assert manager.get(identity)["execution"]["actor_id"] == "owner"
    assert store.get_bytes(config_key) == frozen_config


def test_report_checkpoints_stop_when_authorization_changes_during_capture(managed, monkeypatch):
    from doxa import report_source
    client = managed.client
    query = client.post("/api/v3/projects/allowed/query", json={"run_id": "run-allowed"}, headers=managed.headers("analyst")).json()
    created = client.post("/api/v3/projects/allowed/report-jobs", json={"query_id": query["query_id"], "format": "html"}, headers=managed.headers("analyst"))
    identity = created.json()["id"]
    original = report_source.capture_page
    called = []
    def revoke_after_first_capture(*args, **kwargs):
        result = original(*args, **kwargs)
        called.append(True)
        managed.users[1]["project_ids"] = []
        write_config(managed.config, managed.users)
        return result
    monkeypatch.setattr(report_source, "capture_page", revoke_after_first_capture)
    manager = managed.app.state.report_jobs
    monkeypatch.setattr(manager, "_run_renderer", lambda *_args: pytest.fail("Revoked export must not render"))
    assert manager.work_once(identity)["status"] == "failed"
    assert len(called) == 1
    assert manager.get(identity)["error"]["code"] == "authorization_required"
    assert manager.get(identity)["artifacts"] == []


def test_cancelled_asgi_request_retains_sync_worker_lease_until_late_write_finishes(tmp_path):
    import asyncio
    import threading

    from fastapi import FastAPI

    from doxa.access import install
    from doxa import privacy_lifecycle as privacy
    from doxa.store import Store

    store = Store(tmp_path)
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Cancellation fixture',?)", [utcnow()])
    application = FastAPI()
    application.state.store = store
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []

    @application.post("/api/pilot/projects/{project_id}/drafts")
    def late_writer(project_id: str):
        entered.set()
        try:
            assert store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id=? AND kind='api_thread'", [project_id])["n"] == 1
            assert release.wait(10), "Test did not release the blocked endpoint"
            key = "late/p/source.txt"
            store.put_bytes(key, b"Private late write after HTTP cancellation")
            store.execute("INSERT INTO documents(id,project_id,batch_id,title,filename,mime_type,sha256,status,object_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          ["late", project_id, "b", "Private late source", "source.txt", "text/plain", "fixture", "ready", key, utcnow()])
            return {"ok": True}
        except BaseException as error:
            errors.append(error)
            raise
        finally:
            finished.set()

    install(application, AccessControl())

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})
        messages = []

        async def receive():
            return await incoming.get()

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/pilot/projects/p/drafts", "raw_path": b"/api/pilot/projects/p/drafts",
                 "root_path": "", "query_string": b"", "headers": [(b"host", b"localhost")],
                 "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}
        request = asyncio.create_task(application(scope, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 5), "Synchronous endpoint never started"
            request.cancel()
            await asyncio.sleep(0.05)
            assert not finished.is_set(), "Endpoint must remain blocked after HTTP cancellation"
            assert store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id='p' AND kind='api_thread'")["n"] == 1
            job = privacy.create_erase_job(store, "p", "operator", "cancelled-api-request")
            waiting = privacy.process_erase_job(store, job["id"])
            assert waiting["status"] == "waiting" and "api_thread" in waiting["waiting_for"]
            assert store.one("SELECT id FROM projects WHERE id='p'")
            release.set()
            assert await asyncio.to_thread(finished.wait, 5), "Detached endpoint did not finish"
            try:
                await request
            except asyncio.CancelledError:
                pass
            for _ in range(100):
                result = privacy.process_erase_job(store, job["id"])
                if result["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert result["status"] == "completed"
            assert errors == []
            assert store.one("SELECT id FROM documents WHERE id='late'") is None
            assert not store.object_path("late/p/source.txt").exists()
            assert not store.query("SELECT * FROM privacy_project_activity")
        finally:
            release.set()
            if not request.done():
                request.cancel()
            try:
                await request
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        finished.wait(5)
        store.close()
