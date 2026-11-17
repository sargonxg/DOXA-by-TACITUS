"""Native project privacy policies and resumable, content-free erasure receipts.

Erasure is deliberately whole-project. It drains application activities before
capturing the dependency inventory, removes live derived data, and discloses
backup/external-copy limitations. It never purports to securely wipe hardware.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS privacy_policies(
 project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,policy_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS privacy_erase_jobs(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,status TEXT NOT NULL,phase TEXT NOT NULL,
 inventory_json TEXT NOT NULL DEFAULT '{}',receipt_json TEXT,error TEXT,
 actor_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(project_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS privacy_erase_project ON privacy_erase_jobs(project_id,status);
CREATE TABLE IF NOT EXISTS privacy_project_activity(
 token TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,
 owner_pid INTEGER NOT NULL,owner_start TEXT NOT NULL,started_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS privacy_activity_project ON privacy_project_activity(project_id);
CREATE TABLE IF NOT EXISTS privacy_erasure_items(
 job_id TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
 PRIMARY KEY(job_id,path)
);
CREATE TABLE IF NOT EXISTS privacy_erasure_scope(
 job_id TEXT NOT NULL,table_name TEXT NOT NULL,row_identity INTEGER NOT NULL,
 PRIMARY KEY(job_id,table_name,row_identity)
);
CREATE TABLE IF NOT EXISTS privacy_artifacts(
 project_id TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(project_id,path)
);
"""

DEFAULT_POLICY = {
    "raw_retention_days": 30, "derived_retention_days": 30,
    "allowed_inference_destinations": ["local"], "identity_mode": "original",
    "allow_identity_reveal": False, "journal_mode": "metadata", "allow_exports": True,
    "retention_enabled": False,
}
DESTINATIONS = {"local", "cloudflare", "openai", "runpod"}
PRESERVED_TABLES = {"privacy_erase_jobs", "privacy_erasure_items", "privacy_erasure_scope"}
REFERENCES = {
    "run_id": "runs", "document_id": "documents", "batch_id": "batches",
    "upload_id": "upload_sessions", "import_id": "v3_imports", "finding_id": "findings",
    "casebook_id": "v4_casebooks", "job_id": "report_jobs", "snapshot_id": "v3_learning_snapshots",
    "example_id": "v3_learning_examples", "assessment_id": "v3_assessments",
    "review_id": "v3_review_events", "evaluation_id": "v4_evaluations", "candidate_id": "v3_learning_candidates",
}
OBJECT_COLUMNS = {"object_key", "blocks_key", "result_key", "config_key", "scope_key", "snapshot_key"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ProjectErasingError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ensure_schema(store):
    if store.remote:
        raise ValueError("Privacy lifecycle currently requires native persistent storage")
    if getattr(store, "_privacy_lifecycle_ready", False):
        return
    store.batch([{"sql": part.strip(), "params": []} for part in SCHEMA_SQL.split(";") if part.strip()])
    store._privacy_lifecycle_ready = True


def _project(store, project_id):
    if not isinstance(project_id, str) or not SAFE_ID.fullmatch(project_id):
        raise ValueError("Invalid project identity")
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise ValueError("Project not found")


def require_active_project(store, project_id):
    if store.remote:
        return
    ensure_schema(store)
    if store.one("SELECT id FROM privacy_erase_jobs WHERE project_id=? LIMIT 1", [project_id]):
        raise ProjectErasingError("Project is being erased or has been erased; create a different project")


def _process_start(pid):
    try:
        # Linux start ticks distinguish PID reuse after a restart.
        value = Path(f"/proc/{pid}/stat").read_text()
        return value[value.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        return "unknown"


def _owner_alive(row):
    try:
        os.kill(int(row["owner_pid"]), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True  # Uncertain ownership must not permit erasing under a writer.
    current = _process_start(row["owner_pid"])
    return current == "unknown" or row["owner_start"] == "unknown" or current == row["owner_start"]


def begin_activity(store, project_id, kind="api"):
    """Acquire an erasure barrier atomically; release after streaming ends.

    Activity has no unsafe timeout: an alive owner must finish or be stopped.
    A crashed Linux owner can be distinguished from a subsequently reused PID.
    """
    if store.remote:
        return None
    ensure_schema(store)
    if not isinstance(kind, str) or not 1 <= len(kind) <= 80:
        raise ValueError("Invalid activity kind")
    token = uuid.uuid4().hex
    with store.transaction(write=True):
        require_active_project(store, project_id)
        _project(store, project_id)
        store.execute("INSERT INTO privacy_project_activity(token,project_id,kind,owner_pid,owner_start,started_at) VALUES(?,?,?,?,?,?)",
                      [token, project_id, kind, os.getpid(), _process_start(os.getpid()), utcnow()])
    return token


def end_activity(store, token):
    if token is not None and not store.remote:
        store.execute("DELETE FROM privacy_project_activity WHERE token=?", [token])


@contextmanager
def activity(store, project_id, kind="worker"):
    token = begin_activity(store, project_id, kind)
    try:
        yield token
    finally:
        end_activity(store, token)


def run_activity(store, project_id, kind, function, *args, **kwargs):
    """Hold ownership inside the actual worker, not its cancellable awaiter.

    Cancelling ``asyncio.to_thread`` does not stop its running thread. Acquire
    here so erasure either prevents a not-yet-started writer or waits until the
    real reader/writer has finished, even after the HTTP request is gone.
    """
    with activity(store, project_id, kind):
        return function(*args, **kwargs)


def track_artifact(store, project_id, relative_path, kind="generated"):
    """Register output ownership BEFORE writing, including failed-job outputs."""
    ensure_schema(store)
    _safe_path(store, relative_path)
    with store.transaction(write=True):
        require_active_project(store, project_id)
        _project(store, project_id)
        for owner in store.query("SELECT path FROM privacy_artifacts WHERE project_id<>?", [project_id]):
            existing = owner["path"].rstrip("/")
            candidate = relative_path.rstrip("/")
            if candidate == existing or candidate.startswith(existing + "/") or existing.startswith(candidate + "/"):
                raise ValueError("Artifact path is already owned by another project")
        store.execute("INSERT INTO privacy_artifacts(project_id,path,kind,created_at) VALUES(?,?,?,?) ON CONFLICT(project_id,path) DO NOTHING",
                      [project_id, relative_path, kind, utcnow()])


def get_policy(store, project_id):
    ensure_schema(store)
    _project(store, project_id)
    row = store.one("SELECT * FROM privacy_policies WHERE project_id=?", [project_id])
    policy = json.loads(_json(DEFAULT_POLICY)) if row is None else json.loads(row["policy_json"])
    return {"project_id": project_id, "revision": row["revision"] if row else 0,
            "policy": policy, "policy_configured": row is not None,
            "updated_at": row["updated_at"] if row else None,
            "enforcement": {"native_only": True, "automatic_retention": False,
                "backup_erasure": "operator_managed", "hardware_secure_wipe": False,
                "notice": "Saved policies require destination/export checks at execution. Retention due dates are review candidates; no timer silently erases a project."}}


def set_policy(store, project_id, payload, actor_id, *, expected_revision=None):
    ensure_schema(store)
    if not isinstance(payload, dict) or set(payload) - set(DEFAULT_POLICY):
        raise ValueError("Unsupported privacy policy fields")
    merged = {**DEFAULT_POLICY, **payload}
    for field in ("raw_retention_days", "derived_retention_days"):
        if type(merged[field]) is not int or not 1 <= merged[field] <= 3650:
            raise ValueError("Retention days must be integers between 1 and 3650")
    allowed = merged["allowed_inference_destinations"]
    if not isinstance(allowed, list) or not allowed or len(allowed) > len(DESTINATIONS) or any(not isinstance(item, str) or item not in DESTINATIONS for item in allowed) or len(set(allowed)) != len(allowed):
        raise ValueError("Choose unique allowed inference destinations")
    if not isinstance(merged["identity_mode"], str) or merged["identity_mode"] not in {"original", "pseudonymized"}:
        raise ValueError("Unsupported identity mode")
    if not isinstance(merged["journal_mode"], str) or merged["journal_mode"] not in {"metadata", "visible_io"}:
        raise ValueError("Unsupported request journal mode")
    for field in ("allow_identity_reveal", "allow_exports", "retention_enabled"):
        if type(merged[field]) is not bool:
            raise ValueError("Privacy policy switches must be Boolean")
    if not isinstance(actor_id, str) or not 1 <= len(actor_id) <= 240:
        raise ValueError("An identified policy actor is required")
    with store.transaction(write=True):
        require_active_project(store, project_id)
        _project(store, project_id)
        row = store.one("SELECT revision FROM privacy_policies WHERE project_id=?", [project_id])
        revision = row["revision"] if row else 0
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
            raise ValueError("Privacy policy revision changed; reload before updating")
        store.execute("INSERT INTO privacy_policies(project_id,revision,policy_json,updated_at,updated_by) VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,policy_json=excluded.policy_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                      [project_id, revision + 1, _json(merged), utcnow(), actor_id])
    return get_policy(store, project_id)


def require_destination(store, project_id, provider, *, journal_mode="metadata", managed=False):
    value = get_policy(store, project_id)
    if not value["policy_configured"] and not managed:
        return value
    destination = "local" if provider in {"lexical", "ollama", "local"} else provider
    if destination not in value["policy"]["allowed_inference_destinations"]:
        raise ValueError("Inference destination is not permitted by this project's privacy policy")
    if journal_mode == "visible_io" and value["policy"]["journal_mode"] != "visible_io":
        raise ValueError("Visible request capture is not permitted by this project's privacy policy")
    return value


def require_exports(store, project_id):
    value = get_policy(store, project_id)
    if value["policy_configured"] and not value["policy"]["allow_exports"]:
        raise ValueError("Exports are disabled by this project's privacy policy")
    return value


def retention_due(store, *, now=None):
    ensure_schema(store)
    now = now or datetime.now(timezone.utc)
    result = []
    for row in store.query("SELECT project_id,policy_json,updated_at FROM privacy_policies ORDER BY project_id"):
        policy = json.loads(row["policy_json"])
        if policy.get("retention_enabled"):
            due = datetime.fromisoformat(row["updated_at"]) + timedelta(days=min(policy["raw_retention_days"], policy["derived_retention_days"]))
            if due <= now:
                result.append({"project_id": row["project_id"], "due_at": due.isoformat(), "automatic_erasure": False})
    return result


def create_erase_job(store, project_id, actor_id, idempotency_key):
    ensure_schema(store)
    if not isinstance(actor_id, str) or not 1 <= len(actor_id) <= 240:
        raise ValueError("An identified erasure actor is required")
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
        raise ValueError("An 8–128 character erasure idempotency key is required")
    with store.transaction(write=True):
        previous = store.one("SELECT id,idempotency_key FROM privacy_erase_jobs WHERE project_id=? ORDER BY created_at LIMIT 1", [project_id])
        if previous:
            if previous["idempotency_key"] != idempotency_key:
                raise ProjectErasingError("This project already has an erasure request")
            return get_erase_job(store, previous["id"])
        _project(store, project_id)
        identity, now = uuid.uuid4().hex, utcnow()
        store.execute("INSERT INTO privacy_erase_jobs(id,project_id,status,phase,actor_id,idempotency_key,created_at,updated_at) VALUES(?,?,'waiting','draining',?,?,?,?)",
                      [identity, project_id, actor_id, idempotency_key, now, now])
        store.execute("UPDATE runs SET cancel_requested=1,status=CASE WHEN status IN ('queued','creating','importing') THEN 'cancelled' ELSE status END WHERE project_id=?", [project_id])
        if _table_exists(store, "report_jobs"):
            store.execute("UPDATE report_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE project_id=?", [project_id])
        if _table_exists(store, "upload_sessions"):
            store.execute("UPDATE upload_sessions SET status='aborted' WHERE project_id=? AND status='pending'", [project_id])
    return get_erase_job(store, identity)


def get_erase_job(store, job_id):
    ensure_schema(store)
    row = store.one("SELECT * FROM privacy_erase_jobs WHERE id=?", [job_id])
    if not row:
        raise ValueError("Erasure job not found")
    inventory = json.loads(row["inventory_json"] or "{}")
    progress = store.one("SELECT COUNT(*) total,COALESCE(SUM(status='deleted'),0) deleted FROM privacy_erasure_items WHERE job_id=?", [job_id])
    return {key: row[key] for key in ("id", "project_id", "status", "phase", "created_at", "updated_at", "error")} | {
        "inventory": inventory, "progress": progress,
        "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
        "scope": "whole_project_live_application_data",
        "notice": "Backups, provider retention, downloaded copies and physical-media remanence are not erased by this job."}


def _table_exists(store, name):
    return bool(store.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]))


def _tables(store):
    rows = store.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return {row["name"]: [col["name"] for col in store.query(f'PRAGMA table_info("{row["name"]}")')]
            for row in rows if re.fullmatch(r"[A-Za-z0-9_]+", row["name"]) and not row["name"].startswith("document_search_")}


def _scope_clause(table, columns, tables):
    if table in PRESERVED_TABLES or table in {"schema_versions", "budget_ledger"}:
        return None
    if table == "projects":
        return "id=?", 1
    if "project_id" in columns:
        return "project_id=?", 1
    clauses = [f'"{field}" IN(SELECT id FROM "{parent}" WHERE project_id=?)'
               for field, parent in REFERENCES.items() if field in columns and parent in tables and "project_id" in tables[parent]]
    if table == "settings":
        return "key='workspace_revision:'||? OR key IN(SELECT 'v3-showcase:'||id FROM runs WHERE project_id=?)", 2
    return (" OR ".join(clauses), len(clauses)) if clauses else None


def _safe_path(store, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Unsafe erasure artifact path")
    if len(Path(relative).parts) < 2 or Path(relative).parts[0] in {"backups", "doxa.sqlite3", "doxa.sqlite3-wal", "doxa.sqlite3-shm"}:
        raise ValueError("Unsafe erasure artifact path: database and backups are not live artifacts")
    root = store.root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError("Erasure artifact path leaves the workspace")
    # A symlink can change between inventory and deletion; reject it rather than
    # following it or treating unrelated content as erased.
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("Symlinked erasure artifacts require operator review")
        current = current.parent
    return path


def _unlink_artifact(store, relative):
    """Unlink relative to no-follow directory handles on the pilot's POSIX host.

    Resolving a Path and then unlinking it leaves a parent-symlink swap window.
    Holding each parent descriptor prevents that race from reaching another tree.
    """
    _safe_path(store, relative)
    if os.unlink not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("Safe erasure needs POSIX no-follow directory operations on this host")
    descriptor = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for component in parts[:-1]:
            try:
                following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = following
        try:
            metadata = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Erasure encountered a non-regular artifact; operator review is required")
        try:
            os.unlink(parts[-1], dir_fd=descriptor)
        except FileNotFoundError:
            pass
    finally:
        os.close(descriptor)


def _waiting(store, project_id):
    active = []
    for row in store.query("SELECT * FROM privacy_project_activity WHERE project_id=?", [project_id]):
        if _owner_alive(row):
            active.append(row["kind"])
        else:
            store.execute("DELETE FROM privacy_project_activity WHERE token=?", [row["token"]])
    now = time.time()
    if store.one("SELECT t.id FROM tasks t JOIN runs r ON r.id=t.run_id WHERE r.project_id=? AND t.status='running' AND COALESCE(t.lease_until,0)>? LIMIT 1", [project_id, now]):
        active.append("analysis_lease")
    if _table_exists(store, "report_jobs") and store.one("SELECT id FROM report_jobs WHERE project_id=? AND status='running' AND COALESCE(lease_until,0)>? LIMIT 1", [project_id, now]):
        active.append("report_lease")
    if _table_exists(store, "upload_sessions") and store.one("SELECT id FROM upload_sessions WHERE project_id=? AND status='completing' AND COALESCE(json_extract(metadata_json,'$.completion_lease_until'),0)>? LIMIT 1", [project_id, now]):
        active.append("upload_lease")
    return sorted(set(active))


def _snapshot_scope(store, identity, project_id):
    # SQLite VACUUM can renumber rowids in tables without INTEGER PRIMARY KEY.
    # Refresh them from ownership predicates in the same transaction as delete.
    store.execute("DELETE FROM privacy_erasure_scope WHERE job_id=?", [identity])
    tables = _tables(store)
    counts = {}
    for table, columns in tables.items():
        spec = _scope_clause(table, columns, tables)
        if spec:
            clause, count = spec
            store.execute(f'INSERT INTO privacy_erasure_scope(job_id,table_name,row_identity) SELECT ?,?,rowid FROM "{table}" WHERE {clause} ON CONFLICT DO NOTHING',
                          [identity, table, *[project_id] * count])
            counts[table] = store.one("SELECT COUNT(*) n FROM privacy_erasure_scope WHERE job_id=? AND table_name=?", [identity, table])["n"]
    return tables, counts


def _check_unattributed_objects(store, tables):
    """Do not certify completion while older crashed writers have no owner.

    Registering future writes does not identify a pre-upgrade failed casebook or
    export. The operator must assign such a path rather than have us guess which
    project owns it or silently leave possible private source snapshots behind.
    """
    owned = {row["path"].rstrip("/") for row in store.query("SELECT path FROM privacy_artifacts")}
    namespaces = {"documents": "documents", "runs": "runs", "reports": "reports",
                  "casebooks": "v4_casebooks", "report-jobs": "report_jobs", "uploads": "upload_sessions"}
    for namespace, table in namespaces.items():
        if table in tables:
            owned.update("objects/" + namespace + "/" + row["id"] for row in store.query(f'SELECT id FROM "{table}"'))
    for row in store.query("SELECT id FROM projects"):
        project = row["id"]
        owned.update((f"objects/originals/{project}", f"objects/pilot-reports/{project}",
                      "objects/semantic/" + hashlib.sha256(project.encode()).hexdigest()[:32]))
    for table, columns in tables.items():
        for column in set(columns) & OBJECT_COLUMNS:
            owned.update("objects/" + row["value"] for row in store.query(
                f'SELECT DISTINCT "{column}" value FROM "{table}" WHERE "{column}" IS NOT NULL') if row["value"])
    base = store.root / "objects"
    if base.is_symlink():
        raise ValueError("Symlinked object storage requires operator review")
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlinked object storage requires operator review")
            if path.is_file():
                relative = path.relative_to(store.root)
                covered = any(str(Path(*relative.parts[:length])) in owned
                              for length in range(1, len(relative.parts) + 1))
                if not covered:
                    raise ValueError("Unattributed object artifacts require operator ownership review before erasure")


def _inventory(store, job):
    identity, project_id = job["id"], job["project_id"]
    if any(path.is_dir() and any(path.iterdir()) for path in store.root.glob("tmp*")):
        raise ValueError("Unattributed legacy temporary files require operator review before erasure")
    tables, counts = _snapshot_scope(store, identity, project_id)
    _check_unattributed_objects(store, tables)
    document_ids = [row["id"] for row in store.query("SELECT id FROM documents WHERE project_id=?", [project_id])]
    run_ids = [row["id"] for row in store.query("SELECT id FROM runs WHERE project_id=?", [project_id])]
    report_ids = [row["id"] for row in store.query("SELECT id FROM reports WHERE project_id=?", [project_id])]
    job_ids = [row["id"] for row in store.query("SELECT id FROM report_jobs WHERE project_id=?", [project_id])] if "report_jobs" in tables else []
    book_ids = [row["id"] for row in store.query("SELECT id FROM v4_casebooks WHERE project_id=?", [project_id])] if "v4_casebooks" in tables else []
    upload_ids = [row["id"] for row in store.query("SELECT id FROM upload_sessions WHERE project_id=?", [project_id])] if "upload_sessions" in tables else []
    for value in document_ids + run_ids + report_ids + job_ids + book_ids + upload_ids:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("Unsafe legacy artifact identity requires operator review")
    prefixes = [f"objects/originals/{project_id}", f"objects/semantic/{hashlib.sha256(project_id.encode()).hexdigest()[:32]}",
                f"project-work/{project_id}", f"private-identity-maps/{project_id}"]
    prefixes += [f"objects/documents/{value}" for value in document_ids]
    prefixes += [f"objects/runs/{value}" for value in run_ids]
    prefixes += [f"objects/reports/{value}" for value in report_ids]
    prefixes += [f"objects/report-jobs/{value}" for value in job_ids]
    prefixes += [f"objects/casebooks/{value}" for value in book_ids]
    prefixes += [f"objects/uploads/{value}" for value in upload_ids]
    prefixes += [f"downloads/{value}" for value in document_ids + report_ids]
    prefixes += [f"exports/{value}" for value in report_ids]
    prefixes += [f"report-work/{value}" for value in job_ids]
    prefixes += [f"report-downloads/{value}" for value in job_ids]
    prefixes += [f"private-request-journals/{value}" for value in run_ids]
    prefixes += [f"uploads/{value}" for value in upload_ids]
    registered = [row["path"] for row in store.query("SELECT path FROM privacy_artifacts WHERE project_id=?", [project_id])]
    paths, protected = set(), set()
    for table, columns in tables.items():
        for column in set(columns) & OBJECT_COLUMNS:
            scope = "rowid IN(SELECT row_identity FROM privacy_erasure_scope WHERE job_id=? AND table_name=?)"
            for row in store.query(f'SELECT DISTINCT "{column}" value FROM "{table}" WHERE "{column}" IS NOT NULL AND {scope}', [identity, table]):
                if row["value"]:
                    paths.add("objects/" + row["value"])
            for row in store.query(f'SELECT DISTINCT "{column}" value FROM "{table}" WHERE "{column}" IS NOT NULL AND NOT({scope})', [identity, table]):
                if row["value"]:
                    protected.add("objects/" + row["value"])
    for prefix in prefixes + registered:
        path = _safe_path(store, prefix)
        if path.is_dir():
            for candidate in path.rglob("*"):
                _safe_path(store, str(candidate.relative_to(store.root)))
                if candidate.is_file():
                    paths.add(str(candidate.relative_to(store.root)))
        elif path.exists():
            paths.add(prefix)
    for value in paths:
        _safe_path(store, value)
    paths = {value for value in paths if _safe_path(store, value).exists()}
    other_registrations = [row["path"].rstrip("/") for row in store.query("SELECT path FROM privacy_artifacts WHERE project_id<>?", [project_id])]
    shared = {value for value in paths if value in protected or any(value == prefix or value.startswith(prefix + "/") for prefix in other_registrations)}
    for path in sorted(paths - shared):
        store.execute("INSERT INTO privacy_erasure_items(job_id,path,kind) VALUES(?,?,'file') ON CONFLICT(job_id,path) DO UPDATE SET status='pending'", [identity, path])
    backups = store.root / "backups"
    file_count = store.one("SELECT COUNT(*) n FROM privacy_erasure_items WHERE job_id=?", [identity])["n"]
    return {"tables": counts, "live_files": file_count, "shared_files_preserved": len(shared),
            "backup_files_detected": sum(1 for path in backups.rglob("*") if path.is_file()) if backups.is_dir() else 0,
            "inventory_created_at": utcnow()}


def process_erase_job(store, job_id, *, max_objects=100):
    """Advance a confirmed request. Batches are durable and retry-safe.

    A root application worker can call repeatedly; this function never launches
    an untracked thread or silently expires an active writer's ownership.
    """
    ensure_schema(store)
    if type(max_objects) is not int or not 1 <= max_objects <= 1000:
        raise ValueError("Process between 1 and 1000 erasure objects per call")
    current = get_erase_job(store, job_id)
    if current["status"] == "completed":
        return current
    try:
        with store.transaction(write=True):
            row = store.one("SELECT * FROM privacy_erase_jobs WHERE id=?", [job_id])
            waiting = _waiting(store, row["project_id"])
            if waiting:
                store.execute("UPDATE privacy_erase_jobs SET status='waiting',phase='draining',error=NULL,updated_at=? WHERE id=?", [utcnow(), job_id])
                result = get_erase_job(store, job_id)
                result["waiting_for"] = waiting
                return result
            if row["phase"] in {"draining", "inventory"}:
                inventory = _inventory(store, row)
                store.execute("UPDATE privacy_erase_jobs SET status='running',phase='files',inventory_json=?,error=NULL,updated_at=? WHERE id=?", [_json(inventory), utcnow(), job_id])
        # Serialize claims and unlink with the same native lock. Separate Store
        # processes can retry an unlink safely; the barrier prevents new writers.
        for _ in range(max_objects):
            with store.transaction(write=True):
                item = store.one("SELECT path FROM privacy_erasure_items WHERE job_id=? AND status='pending' ORDER BY path LIMIT 1", [job_id])
                if not item:
                    break
                _unlink_artifact(store, item["path"])
                store.execute("UPDATE privacy_erasure_items SET status='deleted' WHERE job_id=? AND path=?", [job_id, item["path"]])
        if store.one("SELECT path FROM privacy_erasure_items WHERE job_id=? AND status='pending' LIMIT 1", [job_id]):
            return get_erase_job(store, job_id)
        with store.transaction(write=True):
            row = store.one("SELECT * FROM privacy_erase_jobs WHERE id=?", [job_id])
            if row["status"] == "completed":
                return get_erase_job(store, job_id)
            tables, refreshed_counts = _snapshot_scope(store, job_id, row["project_id"])
            inventory = json.loads(row["inventory_json"])
            if {key: value for key, value in refreshed_counts.items() if value} != {key: value for key, value in inventory.get("tables", {}).items() if value}:
                # Operator-side changes are outside the request barrier. Take a
                # fresh inventory rather than deleting a stale set of rowids.
                inventory = _inventory(store, row)
                store.execute("UPDATE privacy_erase_jobs SET phase='files',status='running',inventory_json=?,updated_at=? WHERE id=?", [_json(inventory), utcnow(), job_id])
                return get_erase_job(store, job_id)
            # Preserve only aggregate accounting: private reservation payloads
            # disappear, but charged amounts are not refunded or counted twice.
            for amount in store.query("SELECT month,SUM(amount) amount FROM budget_reservations WHERE status='reserved' AND rowid IN(SELECT row_identity FROM privacy_erasure_scope WHERE job_id=? AND table_name='budget_reservations') GROUP BY month", [job_id]):
                store.execute("UPDATE budget_ledger SET reserved_usd=MAX(0,reserved_usd-?) WHERE month=?", [amount["amount"], amount["month"]])
            store.execute("PRAGMA secure_delete=ON")
            for table in tables:
                if table not in PRESERVED_TABLES:
                    store.execute(f'DELETE FROM "{table}" WHERE rowid IN(SELECT row_identity FROM privacy_erasure_scope WHERE job_id=? AND table_name=?)', [job_id, table])
            receipt = {"schema_version": "doxa-live-project-erasure-1", "job_id": job_id,
                       "project_id": row["project_id"], "completed_at": utcnow(),
                       "deleted_rows_by_table": inventory.get("tables", {}), "deleted_live_files": inventory.get("live_files", 0),
                       "shared_files_preserved": inventory.get("shared_files_preserved", 0),
                       "backups": {"erased": False, "files_detected": inventory.get("backup_files_detected", 0), "expiry_verified": False},
                       "external_copies_erased": False, "physical_media_secure_wipe": False,
                       "scope": "live project application records and inventoried artifacts",
                       "notice": "Deletion of database records is logical erasure. Operator-managed backups, inference-provider retention and previously downloaded copies require separate handling."}
            store.execute("DELETE FROM privacy_erasure_items WHERE job_id=?", [job_id])
            store.execute("DELETE FROM privacy_erasure_scope WHERE job_id=?", [job_id])
            store.execute("UPDATE privacy_erase_jobs SET status='completed',phase='completed',receipt_json=?,error=NULL,updated_at=? WHERE id=?", [_json(receipt), utcnow(), job_id])
        return get_erase_job(store, job_id)
    except (OSError, ValueError, sqlite3.Error):
        # Do not persist parser messages, filenames or exception text as a
        # supposedly content-free receipt. The native operator can inspect logs.
        store.execute("UPDATE privacy_erase_jobs SET status='failed',error='Erasure could not complete; verify storage, ownership and schema, then resume.',updated_at=? WHERE id=?", [utcnow(), job_id])
        raise
