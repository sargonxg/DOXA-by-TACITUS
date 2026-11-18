"""Real expiry, signed-ledger failure and restored-data resurrection checks."""
from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from doxa import privacy_lifecycle as privacy
from doxa import retention
from doxa.store import Store, utcnow


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for key in ("DOXA_CLOUD_STATE_URL", "DOXA_AUTOMATIC_RETENTION", "DOXA_DELETION_LEDGER_PATH",
                "DOXA_DELETION_LEDGER_KEY", "DOXA_INSTALLATION_ID", "DOXA_DELETION_LEDGER_PUBLISH_ARGV"):
        monkeypatch.delenv(key, raising=False)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PATH", str(control / "ledger.json"))
    monkeypatch.setenv("DOXA_DELETION_LEDGER_KEY", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setenv("DOXA_INSTALLATION_ID", "test-installation-a")
    store = Store(tmp_path / "data")
    store.init()
    for identity in ("p", "q"):
        store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [identity, "PRIVATE CUSTOMER " + identity, utcnow()])
        key = f"fixtures/{identity}/source.txt"
        privacy.track_artifact(store, identity, "objects/" + key, "test_source")
        store.put_bytes(key, ("PRIVATE SOURCE " + identity).encode())
    retention.initialize_deletion_ledger(store)
    yield store
    store.close()


def finish(store, identity):
    for _ in range(20):
        result = privacy.process_erase_job(store, identity, max_objects=2)
        if result["status"] == "completed":
            return result
    raise AssertionError("Erasure did not complete")


def snapshot(store, target):
    target.mkdir()
    destination = sqlite3.connect(target / "doxa.sqlite3")
    try:
        store._db.backup(destination)
    finally:
        destination.close()
    shutil.copytree(store.root / "objects", target / "objects")


def test_default_retention_remains_manual_and_no_ledger_needed(tmp_path, monkeypatch):
    for key in ("DOXA_AUTOMATIC_RETENTION", "DOXA_DELETION_LEDGER_PATH", "DOXA_DELETION_LEDGER_KEY", "DOXA_INSTALLATION_ID"):
        monkeypatch.delenv(key, raising=False)
    store = Store(tmp_path / "manual")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Example',?)", [utcnow()])
    privacy.set_policy(store, "p", {"retention_enabled": True, "raw_retention_days": 1}, "operator")
    store.execute("UPDATE privacy_policies SET updated_at=?", [(datetime.now(timezone.utc) - timedelta(days=2)).isoformat()])
    assert privacy.retention_due(store)[0]["automatic_erasure"] is False
    assert retention.retention_tick(store) == {"enabled": False, "scheduled": 0, "advanced": 0}
    assert retention.restore_guard(store)["ready"] is True
    assert store.one("SELECT id FROM projects WHERE id='p'")
    store.close()


def test_automatic_retention_requires_both_server_and_project_opt_in(installation, monkeypatch):
    store = installation
    monkeypatch.setenv("DOXA_AUTOMATIC_RETENTION", "true")
    privacy.set_policy(store, "p", {"retention_enabled": True, "raw_retention_days": 1, "derived_retention_days": 2}, "operator")
    privacy.set_policy(store, "q", {"retention_enabled": False, "raw_retention_days": 1}, "operator")
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.execute("UPDATE privacy_policies SET updated_at=?", [old])
    assert privacy.get_policy(store, "p")["enforcement"]["automatic_retention"] is True
    result = retention.retention_tick(store)
    assert result["scheduled"] == 1 and result["statuses"] == {"completed": 1}
    assert not store.one("SELECT id FROM projects WHERE id='p'")
    assert store.one("SELECT id FROM projects WHERE id='q'")
    assert (store.root / "objects/fixtures/q/source.txt").read_text() == "PRIVATE SOURCE q"
    payload = retention.DeletionLedger(store).read()
    assert [event["kind"] for event in payload["events"]] == ["requested", "completed"]
    assert "PRIVATE" not in json.dumps(payload)
    assert result["backup_expiry_verified"] is False


def test_expiry_drains_active_writer_then_resumes(installation, monkeypatch):
    store = installation
    monkeypatch.setenv("DOXA_AUTOMATIC_RETENTION", "true")
    privacy.set_policy(store, "p", {"retention_enabled": True, "raw_retention_days": 1}, "operator")
    token = privacy.begin_activity(store, "p", "analysis")
    future = datetime.now(timezone.utc) + timedelta(days=2)
    result = retention.retention_tick(store, now=future)
    assert result["statuses"] == {"waiting": 1}
    assert (store.root / "objects/fixtures/p/source.txt").exists()
    with pytest.raises(privacy.ProjectErasingError):
        privacy.begin_activity(store, "p", "late_upload")
    privacy.end_activity(store, token)
    result = retention.retention_tick(store, now=future)
    assert result["scheduled"] == 0 and result["statuses"] == {"completed": 1}
    assert not store.one("SELECT id FROM projects WHERE id='p'")


def test_restore_before_deletion_reapplies_external_obligation(installation, tmp_path):
    store = installation
    saved = tmp_path / "ordinary-backup"
    snapshot(store, saved)
    job = privacy.create_erase_job(store, "p", "operator", "original-request-0001")
    finish(store, job["id"])
    data_root = store.root
    store.close()
    shutil.rmtree(data_root)
    shutil.copytree(saved, data_root)
    restored = Store(data_root)
    restored.init()
    try:
        assert restored.one("SELECT id FROM projects WHERE id='p'")
        result = retention.restore_guard(restored)
        assert result["ready"] is True and result["replayed_projects"] == 1
        assert not restored.one("SELECT id FROM projects WHERE id='p'")
        assert not (data_root / "objects/fixtures/p/source.txt").exists()
        assert restored.one("SELECT id FROM projects WHERE id='q'")
        assert (data_root / "objects/fixtures/q/source.txt").read_text() == "PRIVATE SOURCE q"
        assert retention.restore_guard(restored)["replayed_projects"] == 0
        # Ordinary backups still retain data until their separate expiry.
        assert (saved / "objects/fixtures/p/source.txt").exists()
    finally:
        restored.close()


def test_intent_survives_crash_before_database_job(installation, monkeypatch):
    store = installation
    original = privacy._create_erase_job

    def crash(*args, **kwargs):
        raise RuntimeError("Simulated process death before DB commit")

    monkeypatch.setattr(privacy, "_create_erase_job", crash)
    with pytest.raises(RuntimeError):
        privacy.create_erase_job(store, "p", "operator", "never-persisted-request")
    assert not store.one("SELECT id FROM privacy_erase_jobs")
    assert retention.DeletionLedger(store).read()["events"][0]["kind"] == "requested"
    monkeypatch.setattr(privacy, "_create_erase_job", original)
    assert retention.restore_guard(store)["replayed_projects"] == 1
    assert not store.one("SELECT id FROM projects WHERE id='p'")


def test_completion_ledger_failure_is_not_reported_as_success(installation, monkeypatch):
    store = installation
    job = privacy.create_erase_job(store, "p", "operator", "completion-failure-request")
    original = retention.DeletionLedger.complete

    def unavailable(*args, **kwargs):
        raise OSError("External control storage unavailable")

    monkeypatch.setattr(retention.DeletionLedger, "complete", unavailable)
    with pytest.raises(OSError):
        privacy.process_erase_job(store, job["id"])
    failed = privacy.get_erase_job(store, job["id"])
    assert failed["status"] == "failed" and failed["receipt"]
    assert not store.one("SELECT id FROM projects WHERE id='p'")
    monkeypatch.setattr(retention.DeletionLedger, "complete", original)
    assert retention.restore_guard(store)["ready"] is True
    assert privacy.get_erase_job(store, job["id"])["status"] == "completed"
    assert not store.one("SELECT id FROM projects WHERE id='p'")


def test_missing_configured_ledger_blocks_erase_and_startup(installation):
    store = installation
    retention.DeletionLedger(store).path.unlink()
    with pytest.raises(OSError):
        privacy.create_erase_job(store, "p", "operator", "missing-ledger-request")
    with pytest.raises(retention.RestoreBlockedError):
        retention.restore_guard(store)
    assert store.one("SELECT id FROM projects WHERE id='p'")
    assert (store.root / "objects/fixtures/p/source.txt").exists()


@pytest.mark.parametrize("change", ["signature", "installation", "key", "permissions", "rollback"])
def test_corrupt_wrong_or_rolled_back_ledger_fails_closed(installation, monkeypatch, change):
    store = installation
    ledger = retention.DeletionLedger(store)
    initial = ledger.path.read_bytes()
    privacy.create_erase_job(store, "p", "operator", "verified-ledger-request")
    if change == "signature":
        envelope = json.loads(ledger.path.read_text())
        envelope["payload"]["events"][0]["project_id"] = "q"
        ledger.path.write_text(json.dumps(envelope))
    elif change == "installation":
        monkeypatch.setenv("DOXA_INSTALLATION_ID", "other-installation")
    elif change == "key":
        monkeypatch.setenv("DOXA_DELETION_LEDGER_KEY", base64.b64encode(b"x" * 32).decode())
    elif change == "permissions":
        ledger.path.chmod(0o644)
    else:
        ledger.path.write_bytes(initial)
    with pytest.raises(retention.RestoreBlockedError):
        retention.restore_guard(store)
    assert store.one("SELECT id FROM projects WHERE id='p'")


def test_restore_guard_waits_for_writer_and_blocks_incoherent_side_files(installation):
    store = installation
    token = privacy.begin_activity(store, "p", "real_writer")
    job = privacy.create_erase_job(store, "p", "operator", "writer-restore-request")
    with pytest.raises(retention.RestoreBlockedError, match="waiting"):
        retention.restore_guard(store)
    privacy.end_activity(store, token)
    finish(store, job["id"])
    orphan = store.root / "private-identity-maps/p/old-map.bin"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"PRIVATE OLD MAP")
    with pytest.raises(retention.RestoreBlockedError):
        retention.restore_guard(store)
    assert orphan.exists()  # never guess ownership or claim an incoherent restore safe


def test_explicit_initialization_refuses_replacement_and_internal_path(installation, monkeypatch):
    store = installation
    with pytest.raises(ValueError, match="already exists"):
        retention.initialize_deletion_ledger(store)
    inside = store.root / "control"
    inside.mkdir(mode=0o700)
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PATH", str(inside / "ledger.json"))
    with pytest.raises(ValueError, match="outside"):
        retention.initialize_deletion_ledger(store)


def test_external_expiry_is_separate_content_free_operator_record(installation):
    store = installation
    job = privacy.create_erase_job(store, "p", "operator", "external-retention-request")
    receipt = finish(store, job["id"])["receipt"]
    assert receipt["backups"]["expiry_verified"] is False
    result = retention.record_external_retention(store, "p", target="backup", reference="PRIVATE BUCKET AND CUSTOMER",
                                               expires_at=(datetime.now(timezone.utc) + timedelta(days=35)).isoformat())
    assert result["expiry_independently_verified"] is False
    event = retention.DeletionLedger(store).read()["events"][-1]
    assert event["target"] == "backup" and event["status"] == "expected"
    assert "PRIVATE" not in json.dumps(event)
    with pytest.raises(ValueError, match="future"):
        retention.record_external_retention(store, "p", target="provider", reference="provider-record",
                                           expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), status="expiry_attested")


def test_automatic_retention_without_external_ledger_is_rejected(installation, monkeypatch):
    monkeypatch.setenv("DOXA_AUTOMATIC_RETENTION", "true")
    for key in ("DOXA_DELETION_LEDGER_PATH", "DOXA_DELETION_LEDGER_KEY", "DOXA_INSTALLATION_ID"):
        monkeypatch.delenv(key)
    with pytest.raises(retention.RestoreBlockedError):
        retention.restore_guard(installation)


def test_external_ledger_rejects_symlink_and_permissive_control_directory(installation, tmp_path, monkeypatch):
    ledger = retention.DeletionLedger(installation)
    alias = tmp_path / "ledger-alias.json"
    alias.symlink_to(ledger.path)
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PATH", str(alias))
    with pytest.raises(ValueError, match="non-symlink"):
        retention.DeletionLedger(installation)
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PATH", str(ledger.path))
    ledger.path.parent.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        retention.DeletionLedger(installation)


def test_new_activity_jobs_cancel_and_drain_before_erasure(installation):
    store = installation
    store.execute("CREATE TABLE IF NOT EXISTS activity_jobs(id TEXT PRIMARY KEY,project_id TEXT,status TEXT,cancel_requested INTEGER,lease_until REAL)")
    columns = {row["name"]: row for row in store.query("PRAGMA table_info(activity_jobs)")}
    values = {"id": "active", "project_id": "p", "status": "running", "cancel_requested": 0,
              "lease_until": datetime.now(timezone.utc).timestamp() + 60}
    for name, column in columns.items():
        if name not in values and column["notnull"] and column["dflt_value"] is None:
            values[name] = 0 if "INT" in column["type"] or "REAL" in column["type"] else "{}" if name.endswith("_json") else "fixture"
    store.execute(f'INSERT INTO activity_jobs({",".join(values)}) VALUES({",".join("?" for _ in values)})', list(values.values()))
    job = privacy.create_erase_job(store, "p", "operator", "activity-job-erase-request")
    assert store.one("SELECT cancel_requested FROM activity_jobs WHERE id='active'")["cancel_requested"] == 1
    assert privacy.process_erase_job(store, job["id"])["waiting_for"] == ["activity_job_lease"]
    store.execute("UPDATE activity_jobs SET status='cancelled',lease_until=NULL WHERE id='active'")
    finish(store, job["id"])
    assert not store.one("SELECT id FROM activity_jobs WHERE id='active'")


def test_synchronous_publisher_failure_leaves_recoverable_intent_and_never_acknowledges_delete(installation, tmp_path, monkeypatch):
    store = installation
    publisher = tmp_path / "publisher.py"
    mirror, blocked = tmp_path / "external-ledger.json", tmp_path / "fail-request"
    publisher.write_text("""import json,sys
from pathlib import Path
raw=sys.stdin.buffer.read()
payload=json.loads(raw)['payload']
if payload['events'] and Path(sys.argv[2]).exists():
    raise SystemExit(7)
Path(sys.argv[1]).write_bytes(raw)
""")
    blocked.touch()
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PUBLISH_ARGV", json.dumps([sys.executable, str(publisher), str(mirror), str(blocked)]))
    with pytest.raises(OSError, match="publication"):
        privacy.create_erase_job(store, "p", "operator", "externally-committed-request")
    assert not store.one("SELECT id FROM privacy_erase_jobs")
    assert (store.root / "objects/fixtures/p/source.txt").exists()
    assert json.loads(retention.DeletionLedger(store).path.read_text())["payload"]["sequence"] == 1
    assert json.loads(mirror.read_text())["payload"]["sequence"] == 0
    blocked.unlink()
    result = retention.restore_guard(store)
    assert result["replayed_projects"] == 1
    assert not store.one("SELECT id FROM projects WHERE id='p'")
    assert json.loads(mirror.read_text())["payload"]["events"][-1]["kind"] == "completed"


def test_publisher_environment_excludes_ledger_key_and_provider_credentials(installation, tmp_path, monkeypatch):
    publisher, environment = tmp_path / "publisher.py", tmp_path / "environment.json"
    publisher.write_text("import json,os,sys\nfrom pathlib import Path\nsys.stdin.buffer.read()\nPath(sys.argv[1]).write_text(json.dumps(sorted(os.environ)))\n")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE PROVIDER KEY")
    monkeypatch.setenv("DOXA_DELETION_LEDGER_PUBLISH_ARGV", json.dumps([sys.executable, str(publisher), str(environment)]))
    retention.DeletionLedger(installation).read()
    keys = json.loads(environment.read_text())
    assert "OPENAI_API_KEY" not in keys and "DOXA_DELETION_LEDGER_KEY" not in keys


def test_operator_cli_status_exposes_counts_without_keys_paths_or_content(installation):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts/privacy_lifecycle_v06.py"), "status",
                             "--data-dir", str(installation.root)], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["deletion_obligations"] == 0 and payload["backup_expiry_independently_verified"] is False
    assert "PRIVATE" not in result.stdout and str(installation.root) not in result.stdout
    assert base64.b64encode(bytes(range(32))).decode() not in result.stdout


def test_completed_request_drops_actor_and_raw_key_but_preserves_unicode_idempotent_retry(installation):
    store = installation
    key = "PRIVATE CUSTOMER KEY ñ 0001"
    job = privacy.create_erase_job(store, "p", "PRIVATE REVIEWER NAME", key)
    result = finish(store, job["id"])
    row = store.one("SELECT * FROM privacy_erase_jobs WHERE id=?", [job["id"]])
    assert row["actor_id"] == "erased-actor"
    assert row["idempotency_key"].startswith("erasure-key-sha256:")
    assert "PRIVATE" not in json.dumps(row)
    assert privacy.create_erase_job(store, "p", "new operator", key)["id"] == job["id"]
    assert privacy.matches_erasure_key(row["idempotency_key"], key)
    assert not privacy.matches_erasure_key(row["idempotency_key"], "wrong request")
    assert result["receipt"]["retained_request_metadata"]["actor_identity"] == "removed"
