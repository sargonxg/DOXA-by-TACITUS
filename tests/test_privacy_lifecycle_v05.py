"""Whole-project erasure checks real derivative records, files and races."""
import asyncio
import json
import os
import threading
from types import SimpleNamespace

import pytest

from doxa import privacy_lifecycle as privacy
from doxa.store import Store, utcnow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    instance = Store(tmp_path)
    instance.init()
    privacy.ensure_schema(instance)
    for identity in ("p", "q"):
        instance.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [identity, "Private " + identity, utcnow()])
    yield instance
    instance.close()


def insert(store, table, **values):
    columns = store.query(f'PRAGMA table_info("{table}")')
    for column in columns:
        name = column["name"]
        if name in values or column["dflt_value"] is not None or not column["notnull"]:
            continue
        values[name] = (0 if "INT" in column["type"] or "REAL" in column["type"] else
                        '{"private":"SECRET ONLY P"}' if name.endswith("_json") else "private-" + name)
    store.execute(f'INSERT INTO "{table}"({",".join(values)}) VALUES({",".join("?" for _ in values)})', list(values.values()))


def populated(store, project):
    identities = {table: project + "-" + table for table in (
        "documents", "runs", "batches", "findings", "v3_review_events", "v3_learning_snapshots",
        "v3_learning_examples", "v3_learning_candidates", "v4_evaluations", "v4_casebooks",
        "upload_sessions", "v3_imports", "report_jobs", "reports", "v3_assessments")}
    selected = {}
    for table, columns in privacy._tables(store).items():
        if table in privacy.PRESERVED_TABLES or table.startswith("privacy_") or table in {"projects", "schema_versions", "budget_ledger", "settings"}:
            continue
        spec = privacy._scope_clause(table, columns, privacy._tables(store))
        if not spec:
            continue
        values = {}
        if "id" in columns:
            values["id"] = identities.get(table, project + "-" + table)
        if "project_id" in columns:
            values["project_id"] = project
        for field, parent in privacy.REFERENCES.items():
            if field in columns:
                values[field] = identities[parent]
        if "status" in columns:
            values["status"] = "completed"
        if "payload_json" in columns:
            values["payload_json"] = json.dumps({"private": "SECRET ONLY " + project.upper()})
        for key in set(columns) & privacy.OBJECT_COLUMNS:
            values[key] = f"custom/{project}/{table}-{key}.json"
            privacy.track_artifact(store, project, "objects/" + values[key], "fixture_source")
            store.put_bytes(values[key], ("SECRET ONLY " + project.upper()).encode())
        if table == "document_search":
            values.update(id=identities["documents"], title="private", body="SECRET ONLY " + project.upper())
        insert(store, table, **values)
        selected[table] = values
    store.execute("INSERT INTO settings(key,value_json) VALUES(?,?)", ["workspace_revision:" + project, '"private"'])
    store.execute("INSERT INTO settings(key,value_json) VALUES(?,?)", ["v3-showcase:" + identities["runs"], '"private"'])
    privacy.set_policy(store, project, {"allow_exports": False}, "operator")
    registered = f"failed-outputs/{project}/orphan.txt"
    privacy.track_artifact(store, project, registered)
    path = store.root / registered
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SECRET ONLY " + project.upper())
    for relative in (f"private-request-journals/{identities['runs']}/visible.json",
                     f"report-work/{identities['report_jobs']}/old-lease/input.sqlite3",
                     f"report-downloads/{identities['report_jobs']}/briefing.pdf",
                     f"exports/{identities['reports']}/briefing.html",
                     f"downloads/{identities['documents']}/original.txt",
                     f"uploads/{identities['upload_sessions']}/part-1",
                     f"project-work/{project}/temporary/original.txt",
                     f"private-identity-maps/{project}/vault.bin"):
        target = store.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SECRET ONLY " + project.upper())
    return identities, selected


def finish(store, job_id, maximum=100):
    for _ in range(maximum):
        result = privacy.process_erase_job(store, job_id, max_objects=3)
        if result["status"] == "completed":
            return result
    raise AssertionError("Erasure never completed")


def test_full_derivative_erasure_preserves_other_project_and_aggregate_costs(store):
    p, _ = populated(store, "p")
    _, qrows = populated(store, "q")
    other_before = {table: store.query(f'SELECT * FROM "{table}" WHERE project_id=? ORDER BY rowid', ["q"])
                    for table, cols in privacy._tables(store).items() if "project_id" in cols and table not in privacy.PRESERVED_TABLES}
    qfiles = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*")
              if path.is_file() and path.parent != store.root and "SECRET ONLY Q".encode() in path.read_bytes()}
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    completed = finish(store, job["id"])
    assert completed["status"] == "completed"
    assert store.one("SELECT id FROM projects WHERE id='p'") is None
    assert store.one("SELECT id FROM projects WHERE id='q'")
    assert completed["receipt"]["deleted_rows_by_table"]["v3_assessments"] == 1
    assert completed["receipt"]["deleted_rows_by_table"]["v4_casebook_cases"] == 1
    assert completed["receipt"]["deleted_rows_by_table"]["report_job_parts"] == 1
    assert completed["receipt"]["deleted_rows_by_table"]["v4_reference_reviews"] == 1
    assert "SECRET" not in json.dumps(completed)
    for table, before in other_before.items():
        assert store.query(f'SELECT * FROM "{table}" WHERE project_id=? ORDER BY rowid', ["q"]) == before
    for relative, value in qfiles.items():
        assert (store.root / relative).read_bytes() == value
    assert not (store.root / "private-identity-maps/p/vault.bin").exists()
    assert not (store.root / f"private-request-journals/{p['runs']}/visible.json").exists()
    assert store.one("SELECT COUNT(*) n FROM privacy_erasure_items")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM privacy_erasure_scope")["n"] == 0
    for table, cols in privacy._tables(store).items():
        if "project_id" in cols and table not in privacy.PRESERVED_TABLES:
            assert store.one(f'SELECT COUNT(*) n FROM "{table}" WHERE project_id=?', ["p"])["n"] == 0
    for table in ("tasks", "reviews", "v3_run_sources", "v3_unit_statuses", "v3_learning_snapshot_items", "v3_import_files", "upload_parts", "report_job_artifacts", "report_job_parts", "run_source_metadata", "private_journal_usage"):
        assert store.one(f'SELECT COUNT(*) n FROM "{table}"')["n"] == 1
    assert qrows


def test_waits_for_live_writer_then_inventories_late_artifact_and_blocks_new_work(store):
    identities, _ = populated(store, "p")
    token = privacy.begin_activity(store, "p", "upload")
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    waiting = privacy.process_erase_job(store, job["id"])
    assert waiting["status"] == "waiting" and "upload" in waiting["waiting_for"]
    assert store.one("SELECT id FROM documents WHERE project_id='p'")
    store.put_bytes(f"documents/{identities['documents']}/late.json", b"late private write")
    with pytest.raises(privacy.ProjectErasingError):
        privacy.begin_activity(store, "p", "new_export")
    privacy.end_activity(store, token)
    assert finish(store, job["id"])["status"] == "completed"
    assert not store.object_path(f"documents/{identities['documents']}/late.json").exists()


def test_alive_owner_never_expires_and_crashed_owner_can_drain(store, monkeypatch):
    token = privacy.begin_activity(store, "p")
    store.execute("UPDATE privacy_project_activity SET started_at='1900-01-01T00:00:00+00:00' WHERE token=?", [token])
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    assert privacy.process_erase_job(store, job["id"])["status"] == "waiting"
    monkeypatch.setattr(privacy, "_owner_alive", lambda _: False)
    assert finish(store, job["id"])["status"] == "completed"


def test_pending_report_analysis_and_upload_leases_prevent_early_erasure(store):
    identities, _ = populated(store, "p")
    store.execute("UPDATE tasks SET status='running',lease_until=? WHERE run_id=?", [privacy.time.time() + 300, identities["runs"]])
    store.execute("UPDATE report_jobs SET status='running',lease_until=? WHERE id=?", [privacy.time.time() + 300, identities["report_jobs"]])
    store.execute("UPDATE upload_sessions SET status='completing',metadata_json=? WHERE id=?", [json.dumps({"completion_lease_until": privacy.time.time() + 300}), identities["upload_sessions"]])
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    waiting = privacy.process_erase_job(store, job["id"])
    assert set(waiting["waiting_for"]) == {"analysis_lease", "report_lease", "upload_lease"}
    assert store.one("SELECT cancel_requested FROM runs WHERE id=?", [identities["runs"]])["cancel_requested"] == 1
    store.execute("UPDATE tasks SET lease_until=0")
    store.execute("UPDATE report_jobs SET lease_until=0")
    store.execute("UPDATE upload_sessions SET metadata_json='{}'")
    assert finish(store, job["id"])["status"] == "completed"


def test_erasure_is_resumable_idempotent_and_receipt_has_no_source_paths(store):
    populated(store, "p")
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    assert privacy.create_erase_job(store, "p", "operator", "erase-request-0001")["id"] == job["id"]
    with pytest.raises(privacy.ProjectErasingError):
        privacy.create_erase_job(store, "p", "operator", "different-key")
    progress = privacy.process_erase_job(store, job["id"], max_objects=1)
    assert progress["status"] == "running" and progress["progress"]["deleted"] == 1
    other = Store(store.root)
    other.init()
    try:
        completed = finish(other, job["id"])
        assert privacy.process_erase_job(other, job["id"]) == completed
        assert "custom/p" not in json.dumps(completed)
    finally:
        other.close()


def test_preserves_shared_object_and_discloses_backups_external_copies(store):
    populated(store, "p")
    populated(store, "q")
    store.put_bytes("shared/original.txt", b"shared authorized original")
    store.execute("UPDATE documents SET object_key='shared/original.txt'")
    backup = store.root / "backups" / "operator-copy.sqlite3"
    backup.parent.mkdir(exist_ok=True)
    backup.write_bytes(b"operator backup not automatically erased")
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    completed = finish(store, job["id"])
    assert completed["receipt"]["shared_files_preserved"] == 1
    assert store.get_bytes("shared/original.txt") == b"shared authorized original"
    assert backup.exists()
    assert completed["receipt"]["backups"]["erased"] is False
    assert completed["receipt"]["backups"]["files_detected"] >= 1
    assert completed["receipt"]["external_copies_erased"] is False
    assert completed["receipt"]["physical_media_secure_wipe"] is False


def test_symlink_cannot_delete_outside_workspace_and_failure_can_resume(store, tmp_path):
    populated(store, "p")
    outside = tmp_path.parent / ("outside-" + tmp_path.name)
    outside.mkdir(exist_ok=True)
    victim = outside / "private.txt"
    victim.write_text("must remain")
    link = store.root / "project-work/p/escape"
    link.symlink_to(outside, target_is_directory=True)
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    with pytest.raises(ValueError, match="path|Symlink"):
        privacy.process_erase_job(store, job["id"])
    assert victim.read_text() == "must remain"
    failed = privacy.get_erase_job(store, job["id"])
    assert failed["status"] == "failed" and "private.txt" not in failed["error"]
    link.unlink()
    assert finish(store, job["id"])["status"] == "completed"


def test_policy_validation_revision_enforcement_and_no_silent_retention_deletion(store):
    assert privacy.get_policy(store, "p")["policy_configured"] is False
    assert privacy.require_destination(store, "p", "cloudflare")["policy_configured"] is False
    with pytest.raises(ValueError, match="not permitted"):
        privacy.require_destination(store, "p", "cloudflare", managed=True)
    saved = privacy.set_policy(store, "p", {"allowed_inference_destinations": ["local", "cloudflare"], "allow_exports": False,
                                          "retention_enabled": True, "raw_retention_days": 1}, "operator", expected_revision=0)
    assert saved["revision"] == 1
    assert privacy.require_destination(store, "p", "cloudflare")["revision"] == 1
    with pytest.raises(ValueError, match="capture"):
        privacy.require_destination(store, "p", "cloudflare", journal_mode="visible_io")
    with pytest.raises(ValueError, match="disabled"):
        privacy.require_exports(store, "p")
    with pytest.raises(ValueError, match="revision changed"):
        privacy.set_policy(store, "p", {}, "operator", expected_revision=0)
    store.execute("UPDATE privacy_policies SET updated_at='2000-01-01T00:00:00+00:00'")
    assert privacy.retention_due(store)[0]["project_id"] == "p"
    assert store.one("SELECT id FROM projects WHERE id='p'")
    for invalid in ({"raw_retention_days": True}, {"allowed_inference_destinations": ["local", "local"]},
                    {"allowed_inference_destinations": []}, {"allow_exports": "false"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            privacy.set_policy(store, "p", invalid, "operator")


def test_activity_finally_and_remote_mode_are_explicit(store):
    with pytest.raises(RuntimeError):
        with privacy.activity(store, "p"):
            raise RuntimeError("operation failed")
    assert store.one("SELECT COUNT(*) n FROM privacy_project_activity")["n"] == 0
    remote = SimpleNamespace(remote="https://private.example")
    assert privacy.begin_activity(remote, "p") is None
    with pytest.raises(ValueError, match="native"):
        privacy.get_policy(remote, "p")


def test_reserved_budget_released_without_refunding_charged_costs(store):
    identities, _ = populated(store, "p")
    store.execute("DELETE FROM budget_reservations")
    store.execute("INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES('2026-11',2,3)")
    insert(store, "budget_reservations", id="reservation", run_id=identities["runs"], task_id="task", month="2026-11", amount=2, status="reserved")
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    finish(store, job["id"])
    ledger = store.one("SELECT * FROM budget_ledger WHERE month='2026-11'")
    assert ledger["reserved_usd"] == 0 and ledger["spent_usd"] == 3


def test_vacuum_between_batches_cannot_redirect_rowid_erasure_to_another_project(store):
    populated(store, "p")
    populated(store, "q")
    store.execute("CREATE TABLE legacy_context(project_id TEXT,payload_json TEXT)")
    store.execute("INSERT INTO legacy_context VALUES('obsolete','{}')")
    store.execute("INSERT INTO legacy_context VALUES('p','{\"private\":\"p\"}')")
    store.execute("INSERT INTO legacy_context VALUES('q','{\"private\":\"q\"}')")
    store.execute("DELETE FROM legacy_context WHERE project_id='obsolete'")
    before = store.one("SELECT rowid AS position FROM legacy_context WHERE project_id='p'")["position"]
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    assert privacy.process_erase_job(store, job["id"], max_objects=1)["status"] == "running"
    store._db.execute("VACUUM")
    after = store.one("SELECT rowid AS position FROM legacy_context WHERE project_id='p'")["position"]
    assert before != after
    assert finish(store, job["id"])["status"] == "completed"
    assert store.one("SELECT id FROM documents WHERE project_id='q'")
    assert not store.one("SELECT id FROM documents WHERE project_id='p'")
    assert store.one("SELECT payload_json FROM legacy_context WHERE project_id='q'")
    assert not store.one("SELECT payload_json FROM legacy_context WHERE project_id='p'")


def test_artifact_registry_rejects_cross_project_directory_overlap(store):
    privacy.track_artifact(store, "q", "generated/q")
    for candidate in ("generated/q", "generated/q/child.txt", "generated"):
        with pytest.raises(ValueError, match="owned|Unsafe"):
            privacy.track_artifact(store, "p", candidate)
    for candidate in ("../escape", "/etc/passwd", "a/../../outside", "doxa.sqlite3", "backups/snapshot.sqlite3"):
        with pytest.raises(ValueError, match="Unsafe"):
            privacy.track_artifact(store, "p", candidate)


def test_legacy_unattributed_temporary_data_blocks_complete_claim(store):
    unknown = store.root / "tmp-unattributed" / "original.txt"
    unknown.parent.mkdir()
    unknown.write_text("unknown project owner")
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    with pytest.raises(ValueError, match="Unattributed"):
        privacy.process_erase_job(store, job["id"])
    assert privacy.get_erase_job(store, job["id"])["status"] == "failed"
    assert unknown.read_text() == "unknown project owner"
    # An operator explicitly assigns the path rather than guessing ownership.
    unknown.unlink()
    unknown.parent.rmdir()
    assert finish(store, job["id"])["status"] == "completed"


def test_storage_failure_is_retryable_without_record_deletion_or_text_in_error(store, monkeypatch):
    populated(store, "p")
    original = privacy._safe_path
    fail = {"enabled": True}
    def fail_once(instance, relative):
        path = original(instance, relative)
        if fail["enabled"] and relative.endswith("orphan.txt"):
            fail["enabled"] = False
            raise PermissionError("private path and SECRET must not enter public error")
        return path
    monkeypatch.setattr(privacy, "_safe_path", fail_once)
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    with pytest.raises(PermissionError):
        privacy.process_erase_job(store, job["id"])
    failed = privacy.get_erase_job(store, job["id"])
    assert failed["status"] == "failed" and "SECRET" not in failed["error"]
    assert store.one("SELECT id FROM documents WHERE project_id='p'")
    assert finish(store, job["id"])["status"] == "completed"


def test_directory_handle_unlink_resists_parent_symlink_swap(store, tmp_path, monkeypatch):
    if os.unlink not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("POSIX no-follow directory handles are unavailable")
    parent = store.root / "project-work/p"
    parent.mkdir(parents=True)
    (parent / "source.txt").write_text("owned private source")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(exist_ok=True)
    (outside / "source.txt").write_text("unrelated file must survive")
    moved = parent.with_name("p-moved")
    original_open = privacy.os.open
    swapped = {"done": False}
    def swap_after_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == "p" and kwargs.get("dir_fd") is not None and not swapped["done"]:
            swapped["done"] = True
            parent.rename(moved)
            parent.symlink_to(outside, target_is_directory=True)
        return descriptor
    monkeypatch.setattr(privacy.os, "open", swap_after_open)
    privacy._unlink_artifact(store, "project-work/p/source.txt")
    assert swapped["done"]
    assert (outside / "source.txt").read_text() == "unrelated file must survive"
    assert not (moved / "source.txt").exists()


def test_failed_casebook_insert_leaves_owned_artifact_that_erasure_removes(store, monkeypatch):
    from doxa.casebooks import create_casebook
    from doxa.workspace import Workspace, WorkspaceQuery
    from test_workspace import seed_assessment, seed_document, seed_run
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES('batch-a','p','private',?)", [utcnow()])
    seed_document(store, "source", text="Private source statement.")
    seed_run(store, sources=[{"id": "source", "sha256": "source-hash"}])
    seed_assessment(store, "decision", "source", text="Private source statement.")
    query_id = Workspace(store).create("p", WorkspaceQuery(run_id="run"))["query_id"]
    original = store.execute
    def fail_book_insert(sql, params=()):
        if sql.startswith("INSERT INTO v4_casebooks("):
            raise OSError("simulated database outage after scope write")
        return original(sql, params)
    with monkeypatch.context() as patch:
        patch.setattr(store, "execute", fail_book_insert)
        with pytest.raises(OSError, match="simulated"):
            create_casebook(store, "p", query_id, "Private scope")
    assert not store.one("SELECT id FROM v4_casebooks")
    registered = store.one("SELECT path FROM privacy_artifacts WHERE project_id='p' AND kind='casebook'")
    assert registered
    scope_path = store.root / registered["path"] / "scope.json"
    assert scope_path.exists()
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    assert finish(store, job["id"])["status"] == "completed"
    assert not scope_path.exists()


def test_preupgrade_orphan_objects_require_explicit_ownership_before_completion(store):
    store.put_json("casebooks/old-failed-book/scope.json", {"private_source": "unknown owner"})
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    with pytest.raises(ValueError, match="Unattributed object"):
        privacy.process_erase_job(store, job["id"])
    assert privacy.get_erase_job(store, job["id"])["status"] == "failed"
    # The operator assigns this historical output to another authorized project.
    privacy.track_artifact(store, "q", "objects/casebooks/old-failed-book", "legacy_orphan")
    assert finish(store, job["id"])["status"] == "completed"
    assert store.get_json("casebooks/old-failed-book/scope.json") == {"private_source": "unknown owner"}


def test_cancelled_to_thread_awaiter_does_not_release_real_writer_ownership(store):
    started, release = threading.Event(), threading.Event()
    target = store.root / "project-work/p/late-source.txt"
    def writer():
        started.set()
        if not release.wait(5):
            raise AssertionError("Test did not release the blocked writer")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source written after HTTP cancellation")
    async def eventually(predicate):
        for _ in range(1000):
            if predicate():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("Concurrent worker did not reach its checkpoint")
    async def scenario():
        task = asyncio.create_task(asyncio.to_thread(privacy.run_activity, store, "p", "detached_writer", writer))
        try:
            await eventually(started.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id='p'")["n"] == 1
            job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
            waiting = privacy.process_erase_job(store, job["id"])
            assert waiting["status"] == "waiting" and "detached_writer" in waiting["waiting_for"]
            release.set()
            await eventually(lambda: store.one("SELECT COUNT(*) n FROM privacy_project_activity WHERE project_id='p'")["n"] == 0)
            assert target.exists()
            assert finish(store, job["id"])["status"] == "completed"
            assert not target.exists()
        finally:
            release.set()
    asyncio.run(scenario())


def test_worker_starting_after_erasure_barrier_never_calls_persistent_writer(store):
    called = []
    job = privacy.create_erase_job(store, "p", "operator", "erase-request-0001")
    async def scenario():
        with pytest.raises(privacy.ProjectErasingError):
            await asyncio.to_thread(privacy.run_activity, store, "p", "late_worker", lambda: called.append(True))
    asyncio.run(scenario())
    assert called == []
    assert finish(store, job["id"])["status"] == "completed"
