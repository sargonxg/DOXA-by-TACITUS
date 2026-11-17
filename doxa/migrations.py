"""Ordered additive upgrades with a checked, immutable pre-upgrade backup."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .store import utcnow

SCHEMA_VERSION = 5
REMOTE_SCHEMA_VERSION = 4
V4_SQL = """
CREATE TABLE IF NOT EXISTS import_commits (
 project_id TEXT NOT NULL, source_identity TEXT NOT NULL, sha256 TEXT NOT NULL,
 document_id TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(project_id,source_identity,sha256)
);
CREATE TABLE IF NOT EXISTS upload_sessions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', result_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_parts (
 upload_id TEXT NOT NULL, part_number INTEGER NOT NULL, sha256 TEXT NOT NULL,
 object_key TEXT NOT NULL, size INTEGER NOT NULL,
 PRIMARY KEY(upload_id,part_number)
);
CREATE TABLE IF NOT EXISTS run_source_metadata (
 run_id TEXT NOT NULL, document_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 metadata_sha256 TEXT NOT NULL, PRIMARY KEY(run_id,document_id)
);
CREATE TABLE IF NOT EXISTS request_attempt_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, task_id TEXT NOT NULL,
 request_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_attempts_run ON request_attempt_events(run_id,task_id,created_at,id);
CREATE TABLE IF NOT EXISTS private_journal_usage (
 run_id TEXT PRIMARY KEY, reserved_bytes INTEGER NOT NULL DEFAULT 0
);
"""


def _statements(sql):
    return [{"sql": part.strip(), "params": []} for part in sql.split(";") if part.strip()]


def _version(store):
    exists = store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'")
    return (store.one("SELECT MAX(version) version FROM schema_versions")["version"] or 0) if exists else 0


def _backup(store, version):
    folder = store.root / "backups"
    folder.mkdir(exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    path = folder / f"schema-{version}-before-{SCHEMA_VERSION}-{uuid.uuid4().hex}.sqlite3"
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = sqlite3.connect(f"file:{store.root / 'doxa.sqlite3'}?mode=ro", uri=True)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Pre-upgrade backup failed its integrity check")
        target.close()
        temporary.replace(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        path.with_suffix(".json").write_text(json.dumps({"from_version": version, "to_version": SCHEMA_VERSION,
            "created_at": utcnow(), "sha256": digest.hexdigest(), "backup": path.name}))
    finally:
        source.close()
        target.close()
        temporary.unlink(missing_ok=True)


def migrate(store):
    """Never mark a version applied ahead of its schema or modify future data."""
    current = _version(store)
    target = REMOTE_SCHEMA_VERSION if store.remote else SCHEMA_VERSION
    if current > target:
        raise RuntimeError(f"Workspace schema {current} is newer than supported schema {target}; use a matching application version")
    if current == target:
        _extensions(store)
        return
    existing = bool(store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"))
    if store.remote:
        if existing and not os.environ.get("DOXA_MIGRATION_BACKUP_RECEIPT", "").strip():
            raise RuntimeError("Back up D1 before upgrading this workspace and set DOXA_MIGRATION_BACKUP_RECEIPT to the operator's backup receipt")
        _apply(store, current)
        return
    with store.transaction(write=True):
        # BEGIN IMMEDIATE serializes concurrent application initializations.
        current = _version(store)
        if current > SCHEMA_VERSION:
            raise RuntimeError("Workspace schema changed to an unsupported future version")
        if current == SCHEMA_VERSION:
            _extensions(store)
            return
        if existing:
            _backup(store, current)
        _apply(store, current)


def _apply(store, current):
    statements = []
    if current < 3:
        from .learning import SCHEMA_SQL
        sql = Path(__file__).with_name("schema.sql").read_text() + "\n" + Path(__file__).with_name("v3_schema.sql").read_text()
        statements.extend(_statements(sql))
        statements.extend(_statements(SCHEMA_SQL))
    if current < 4:
        statements.extend(_statements(V4_SQL))
        statements.extend(_extension_statements())
        statements.append({"sql": "INSERT INTO schema_versions(version,applied_at) VALUES(?,?) ON CONFLICT(version) DO NOTHING", "params": [4, utcnow()]})
    if current < 5 and not store.remote:
        statements.extend(_native_extension_statements())
        statements.append({"sql": "INSERT INTO schema_versions(version,applied_at) VALUES(?,?) ON CONFLICT(version) DO NOTHING", "params": [5, utcnow()]})
    store.batch(statements)


def _extension_statements():
    from .learning import SCHEMA_SQL
    from .evaluation import SCHEMA_SQL as evaluation_schema
    from .semantic_overlays import SCHEMA_SQL as overlay_schema
    return [*_statements(SCHEMA_SQL), *_statements(evaluation_schema), *_statements(overlay_schema), *[statement for filename in ("report_jobs_schema.sql", "v4_casebooks.sql")
            if (path := Path(__file__).with_name(filename)).exists()
            for statement in _statements(path.read_text())]]


def _extensions(store):
    # Additive companion tables are idempotent within this release; this also
    # repairs an initialization interrupted between service registrations.
    store.batch([*_statements(V4_SQL), *_extension_statements(),
                 *(_native_extension_statements() if not store.remote else [])])


def _native_extension_statements():
    from .pilot_api import SCHEMA_SQL as pilot_schema
    from .privacy_lifecycle import SCHEMA_SQL as privacy_schema
    return [*_statements(pilot_schema), *_statements(privacy_schema)]
