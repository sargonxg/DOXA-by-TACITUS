"""Opt-in retention and signed deletion obligations outside customer backups.

The ledger is an operator-controlled, content-free recovery dependency. Its
signature detects corruption; it is not a claim of protection from an operator
who controls both the signing key and all ledger copies. Backup/provider expiry
is recorded separately from live erasure and is never inferred from a timer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import uuid

from .store import utcnow

LEDGER_VERSION = "doxa-deletion-ledger-1"
MAX_LEDGER_BYTES = 16 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class RestoreBlockedError(RuntimeError):
    """The installation must not serve traffic until recovery succeeds."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("A timezone-aware timestamp is required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required")
    return parsed.astimezone(timezone.utc).isoformat()


def automatic_retention_enabled():
    value = os.environ.get("DOXA_AUTOMATIC_RETENTION", "false").strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError("DOXA_AUTOMATIC_RETENTION must be true or false")
    return value in {"true", "1"}


def ledger_configured():
    return any(os.environ.get(key) for key in (
        "DOXA_DELETION_LEDGER_PATH", "DOXA_DELETION_LEDGER_KEY", "DOXA_INSTALLATION_ID", "DOXA_DELETION_LEDGER_PUBLISH_ARGV"))


def _private_regular(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Deletion ledger files must be private regular files")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("Deletion ledger files must belong to the application operator")


class DeletionLedger:
    def __init__(self, store):
        if store.remote:
            raise ValueError("Deletion recovery requires native persistent storage")
        path = os.environ.get("DOXA_DELETION_LEDGER_PATH", "")
        identity = os.environ.get("DOXA_INSTALLATION_ID", "")
        encoded_key = os.environ.get("DOXA_DELETION_LEDGER_KEY", "")
        if not path or not _ID.fullmatch(identity) or len(identity) < 8 or not encoded_key:
            raise ValueError("Configure an external deletion ledger, installation identity and signing key")
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (ValueError, TypeError):
            raise ValueError("The deletion ledger signing key must encode 32 random bytes") from None
        if len(key) != 32:
            raise ValueError("The deletion ledger signing key must encode 32 random bytes")
        self.path = Path(path).expanduser()
        if not self.path.is_absolute() or self.path != self.path.resolve():
            raise ValueError("The deletion ledger needs an absolute, non-symlink path")
        root = store.root.resolve()
        if self.path.is_relative_to(root):
            raise ValueError("The deletion ledger must be outside the customer-data directory and its backups")
        parent = self.path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise ValueError("Provision a private external deletion ledger directory first")
        info = parent.stat()
        if info.st_mode & 0o077 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise ValueError("The deletion ledger directory must be private and operator-owned")
        if not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("Deletion recovery requires POSIX no-follow file operations")
        self.identity, self.key = identity, key
        self.store = store

    @contextmanager
    def locked(self):
        import fcntl
        lock = self.path.with_name(self.path.name + ".lock")
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            _private_regular(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _read(self):
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            _private_regular(descriptor)
            if os.fstat(descriptor).st_size > MAX_LEDGER_BYTES:
                raise ValueError("Deletion ledger exceeds the supported verification bound")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                value = json.loads(stream.read(MAX_LEDGER_BYTES + 1))
        finally:
            os.close(descriptor)
        if not isinstance(value, dict) or set(value) != {"payload", "signature"}:
            raise ValueError("Invalid deletion ledger envelope")
        payload = value["payload"]
        signature = hmac.new(self.key, _canonical(payload), hashlib.sha256).hexdigest()
        if not isinstance(value["signature"], str) or not hmac.compare_digest(signature, value["signature"]):
            raise ValueError("Deletion ledger signature verification failed")
        self._validate(payload)
        self._check_checkpoint(payload)
        self._publish(_canonical(value) + b"\n")
        return payload

    def _publish(self, raw):
        supplied = os.environ.get("DOXA_DELETION_LEDGER_PUBLISH_ARGV")
        if not supplied:
            return
        try:
            argv = json.loads(supplied)
        except (TypeError, ValueError):
            raise ValueError("The deletion ledger publisher must be a JSON argument array") from None
        if not isinstance(argv, list) or not 1 <= len(argv) <= 32 or any(not isinstance(arg, str) or not arg or len(arg) > 4000 or "\x00" in arg for arg in argv):
            raise ValueError("The deletion ledger publisher must be a bounded JSON argument array")
        if not Path(argv[0]).is_absolute():
            raise ValueError("The deletion ledger publisher executable must have an absolute path")
        cache_key = hashlib.sha256(raw + _canonical(argv)).hexdigest()
        if getattr(self.store, "_privacy_published_ledger", None) == cache_key:
            return
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
                   "GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_CONFIG", "CLOUDSDK_PYTHON", "DOXA_INSTALLATION_ID", "DOXA_LEDGER_BUCKET"}
        environment = {key: value for key, value in os.environ.items()
                       if key in allowed or key.startswith("DOXA_LEDGER_PUBLISH_")}
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
            try:
                process.communicate(raw, timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise OSError("External deletion ledger publication timed out") from None
            if process.returncode:
                raise OSError("External deletion ledger publication failed")
        except (OSError, ValueError):
            # Commands, credentials, filesystem locations and provider output
            # are deliberately absent from application-facing error messages.
            raise OSError("External deletion ledger publication could not be verified") from None
        self.store._privacy_published_ledger = cache_key

    def _check_checkpoint(self, payload):
        row = self.store.one("SELECT value_json FROM settings WHERE key='privacy_deletion_ledger_checkpoint'")
        if row:
            previous = json.loads(row["value_json"])
            if previous.get("installation_id") != self.identity or previous.get("sequence", 0) > payload["sequence"]:
                raise ValueError("Deletion ledger is older than this workspace or belongs to another installation")

    def _validate(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "installation_id", "sequence", "events"}:
            raise ValueError("Invalid deletion ledger payload")
        if payload["schema_version"] != LEDGER_VERSION or payload["installation_id"] != self.identity:
            raise ValueError("Deletion ledger belongs to another version or installation")
        events = payload["events"]
        if not isinstance(events, list) or type(payload["sequence"]) is not int or payload["sequence"] != len(events):
            raise ValueError("Invalid deletion ledger sequence")
        requested = {}
        for index, event in enumerate(events, 1):
            if not isinstance(event, dict) or event.get("sequence") != index:
                raise ValueError("Invalid deletion ledger event")
            base = {"sequence", "kind", "project_id", "request_digest", "created_at"}
            kind = event.get("kind")
            extras = {"receipt_sha256"} if kind == "completed" else (
                {"target", "reference_sha256", "expires_at", "status"} if kind == "external_retention" else set())
            if kind not in {"requested", "completed", "external_retention"} or set(event) != base | extras:
                raise ValueError("Invalid deletion ledger event fields")
            if not isinstance(event["project_id"], str) or not _ID.fullmatch(event["project_id"]):
                raise ValueError("Invalid ledger project identity")
            if not isinstance(event["request_digest"], str) or not _HEX.fullmatch(event["request_digest"]):
                raise ValueError("Invalid ledger request digest")
            _timestamp(event["created_at"])
            if kind == "requested":
                if event["project_id"] in requested:
                    raise ValueError("Duplicate deletion obligation")
                requested[event["project_id"]] = event["request_digest"]
            elif requested.get(event["project_id"]) != event["request_digest"]:
                raise ValueError("Deletion event has no matching request")
            if kind == "completed" and not _HEX.fullmatch(str(event["receipt_sha256"])):
                raise ValueError("Invalid erasure receipt digest")
            if kind == "external_retention":
                if event["target"] not in {"backup", "provider"} or event["status"] not in {"expected", "expiry_attested"}:
                    raise ValueError("Invalid external retention status")
                if not _HEX.fullmatch(str(event["reference_sha256"])):
                    raise ValueError("Invalid external retention reference")
                _timestamp(event["expires_at"])

    def _write(self, payload):
        self._validate(payload)
        self._check_checkpoint(payload)
        value = {"payload": payload, "signature": hmac.new(self.key, _canonical(payload), hashlib.sha256).hexdigest()}
        raw = _canonical(value) + b"\n"
        if len(raw) > MAX_LEDGER_BYTES:
            raise ValueError("Deletion ledger requires an operator-managed archival upgrade")
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(descriptor)
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._publish(raw)
            self.store.execute("INSERT INTO settings(key,value_json) VALUES('privacy_deletion_ledger_checkpoint',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                               [_canonical({"installation_id": self.identity, "sequence": payload["sequence"]}).decode()])
        finally:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def initialize(self):
        with self.locked():
            if self.path.exists():
                raise ValueError("A deletion ledger already exists; initialization never replaces it")
            self._write({"schema_version": LEDGER_VERSION, "installation_id": self.identity, "sequence": 0, "events": []})

    def read(self):
        with self.locked():
            return self._read()

    def digest(self, project_id, idempotency_key):
        return hashlib.sha256(_canonical([self.identity, project_id, idempotency_key])).hexdigest()

    def request(self, project_id, idempotency_key, *, created_at=None):
        digest = self.digest(project_id, idempotency_key)
        with self.locked():
            payload = self._read()
            existing = next((event for event in payload["events"] if event["kind"] == "requested" and event["project_id"] == project_id), None)
            if existing:
                if existing["request_digest"] != digest:
                    raise ValueError("This project already has a different deletion obligation")
                return digest
            payload["events"].append({"sequence": payload["sequence"] + 1, "kind": "requested", "project_id": project_id,
                                      "request_digest": digest, "created_at": _timestamp(created_at or utcnow())})
            payload["sequence"] += 1
            self._write(payload)
        return digest

    def complete(self, project_id, receipt):
        digest = hashlib.sha256(_canonical(receipt)).hexdigest()
        with self.locked():
            payload = self._read()
            request = next((event for event in payload["events"] if event["kind"] == "requested" and event["project_id"] == project_id), None)
            if request is None:
                raise ValueError("Deletion completion has no durable external obligation")
            if any(event["kind"] == "completed" and event["project_id"] == project_id and event["receipt_sha256"] == digest for event in payload["events"]):
                return
            payload["events"].append({"sequence": payload["sequence"] + 1, "kind": "completed", "project_id": project_id,
                                      "request_digest": request["request_digest"], "created_at": utcnow(), "receipt_sha256": digest})
            payload["sequence"] += 1
            self._write(payload)


def initialize_deletion_ledger(store):
    """Explicit operator action; missing configured ledgers never auto-recreate."""
    ledger = DeletionLedger(store)
    ledger.initialize()
    # Bootstrap only history actually present in this database. This cannot
    # reconstruct deletion history absent from both it and the external ledger.
    from .privacy_lifecycle import ensure_schema
    ensure_schema(store)
    for row in store.query("SELECT * FROM privacy_erase_jobs ORDER BY created_at,id"):
        ledger.request(row["project_id"], row["idempotency_key"], created_at=row["created_at"])
        if row["status"] == "completed" and row["receipt_json"]:
            ledger.complete(row["project_id"], json.loads(row["receipt_json"]))
    return {"initialized": True, "schema_version": LEDGER_VERSION, "sequence": ledger.read()["sequence"]}


def record_deletion_request(store, project_id, idempotency_key):
    if ledger_configured():
        return DeletionLedger(store).request(project_id, idempotency_key)
    if automatic_retention_enabled():
        raise ValueError("Automatic retention requires a separately provisioned deletion ledger")
    return None


def record_deletion_completion(store, project_id, receipt):
    if ledger_configured():
        DeletionLedger(store).complete(project_id, receipt)


def ensure_deletion_request(store, row):
    """Validate the ledger before deleting, including pre-upgrade queued jobs."""
    if ledger_configured():
        ledger = DeletionLedger(store)
        payload = ledger.read()
        if not any(event["kind"] == "requested" and event["project_id"] == row["project_id"] for event in payload["events"]):
            ledger.request(row["project_id"], row["idempotency_key"], created_at=row["created_at"])
    elif automatic_retention_enabled():
        raise ValueError("Automatic retention requires a separately provisioned deletion ledger")


def _check_private_ownership(store):
    """Reject incoherent restores that reintroduce unowned private side files."""
    from . import privacy_lifecycle as privacy
    tables = privacy._tables(store)
    namespaces = {"private-identity-maps": ("projects",), "project-work": ("projects",),
                  "private-request-journals": ("runs",), "report-work": ("report_jobs",),
                  "report-downloads": ("report_jobs",), "uploads": ("upload_sessions",),
                  "downloads": ("documents", "reports"), "exports": ("reports",)}
    for namespace, parents in namespaces.items():
        base = store.root / namespace
        if base.is_symlink():
            raise ValueError("Symlinked private artifacts require operator review")
        if not base.is_dir():
            continue
        if any(path.is_symlink() for path in base.rglob("*")):
            raise ValueError("Symlinked private artifacts require operator review")
        known = {row["id"] for table in parents if table in tables for row in store.query(f'SELECT id FROM "{table}"')}
        for child in base.iterdir():
            if child.is_symlink():
                raise ValueError("Symlinked private artifacts require operator review")
            if child.name not in known and (child.is_file() or any(path.is_file() for path in child.rglob("*"))):
                raise ValueError("Unattributed private artifacts require a coherent restore or ownership review")
    if any(path.is_dir() and any(path.iterdir()) for path in store.root.glob("tmp*")):
        raise ValueError("Unattributed temporary artifacts require ownership review")


def restore_guard(store, *, max_steps=1000, max_objects=100):
    """Reapply every external deletion obligation before opening the server.

    A restored database's old completion flags are not trusted while its project
    exists. A temporary tombstone project lets existing ownership inventory
    inspect project-scoped derivatives even if the restored parent is absent.
    Unknown artifacts, live writers and exhausted work bounds block startup.
    """
    if store.remote:
        if ledger_configured() or automatic_retention_enabled():
            raise RestoreBlockedError("Native deletion recovery cannot protect remote persistence")
        return {"configured": False, "ready": True, "replayed_projects": 0}
    if not ledger_configured():
        if automatic_retention_enabled():
            raise RestoreBlockedError("Automatic retention requires an external deletion ledger")
        return {"configured": False, "ready": True, "replayed_projects": 0}
    from . import privacy_lifecycle as privacy
    try:
        ledger = DeletionLedger(store)
        payload = ledger.read()
        privacy.ensure_schema(store)
        replayed = 0
        for event in (item for item in payload["events"] if item["kind"] == "requested"):
            project_id = event["project_id"]
            row = store.one("SELECT * FROM privacy_erase_jobs WHERE project_id=? ORDER BY created_at LIMIT 1", [project_id])
            present = store.one("SELECT id FROM projects WHERE id=?", [project_id])
            if row and row["phase"] == "completed" and row["receipt_json"] and not present:
                # A coherent post-erasure workspace needs no temporary project.
                # Verify ownership predicates and reject unexplained objects.
                tables = privacy._tables(store)
                outstanding = any(store.one(f'SELECT 1 hit FROM "{table}" WHERE project_id=? LIMIT 1', [project_id])
                                  for table, columns in tables.items() if "project_id" in columns and table not in privacy.PRESERVED_TABLES)
                if not outstanding:
                    privacy._check_unattributed_objects(store, tables)
                    _check_private_ownership(store)
                    privacy.process_erase_job(store, row["id"], max_objects=max_objects)
                    continue
            with store.transaction(write=True):
                if not present:
                    store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [project_id, "Pending deletion obligation", utcnow()])
                if row:
                    if row["phase"] == "completed":
                        store.execute("UPDATE privacy_erase_jobs SET status='waiting',phase='draining',inventory_json='{}',receipt_json=NULL,error=NULL,updated_at=? WHERE id=?", [utcnow(), row["id"]])
                    identity = row["id"]
                else:
                    identity = privacy._create_erase_job(store, project_id, "restore-guard", "restore-" + event["request_digest"])["id"]
            for _ in range(max_steps):
                result = privacy.process_erase_job(store, identity, max_objects=max_objects)
                if result["status"] == "completed":
                    replayed += 1
                    break
                if result["status"] == "waiting":
                    raise RestoreBlockedError("Deletion recovery is waiting for active work; stop writers and retry")
            else:
                raise RestoreBlockedError("Deletion recovery exceeded its work bound; resume before serving traffic")
        return {"configured": True, "ready": True, "replayed_projects": replayed,
                "ledger_sequence": ledger.read()["sequence"], "backup_expiry_verified": False}
    except RestoreBlockedError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        raise RestoreBlockedError("Deletion recovery verification failed; restore the current external ledger and retry") from error


def retention_tick(store, *, now=None, max_projects=20, max_objects=100):
    """An explicitly enabled scheduler advances whole-project expiry in bounds."""
    enabled = automatic_retention_enabled()
    if not enabled:
        return {"enabled": False, "scheduled": 0, "advanced": 0}
    if type(max_projects) is not int or not 1 <= max_projects <= 100:
        raise ValueError("Process between 1 and 100 retention projects per tick")
    DeletionLedger(store).read()  # fail closed before creating any automatic job
    from . import privacy_lifecycle as privacy
    privacy.ensure_schema(store)
    scheduled = 0
    due = privacy.retention_due(store, now=now)
    for candidate in due[:max_projects]:
        project_id = candidate["project_id"]
        if store.one("SELECT id FROM privacy_erase_jobs WHERE project_id=?", [project_id]):
            continue
        key = "retention-" + hashlib.sha256(_canonical([project_id, candidate["due_at"]])).hexdigest()
        privacy.create_erase_job(store, project_id, "retention-scheduler", key)
        scheduled += 1
    advanced = 0
    statuses = {}
    for row in store.query("SELECT id FROM privacy_erase_jobs WHERE status IN ('waiting','running','failed') ORDER BY created_at,id LIMIT ?", [max_projects]):
        result = privacy.process_erase_job(store, row["id"], max_objects=max_objects)
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
        advanced += 1
    return {"enabled": True, "scheduled": scheduled, "advanced": advanced, "statuses": statuses,
            "scope": "whole_project_at_earliest_raw_or_derived_deadline", "backup_expiry_verified": False}


def record_external_retention(store, project_id, *, target, reference, expires_at, status="expected"):
    """Track expiry separately; an operator attestation is not provider evidence."""
    if target not in {"backup", "provider"} or status not in {"expected", "expiry_attested"}:
        raise ValueError("Choose a backup/provider target and expected/expiry_attested status")
    if not isinstance(reference, str) or not 1 <= len(reference) <= 2000:
        raise ValueError("An external retention reference is required")
    expiry = _timestamp(expires_at)
    if status == "expiry_attested" and datetime.fromisoformat(expiry) > datetime.now(timezone.utc):
        raise ValueError("A future expiry cannot be attested as complete")
    ledger = DeletionLedger(store)
    with ledger.locked():
        payload = ledger._read()
        request = next((event for event in payload["events"] if event["kind"] == "requested" and event["project_id"] == project_id), None)
        if request is None:
            raise ValueError("External retention tracking requires a deletion obligation")
        event = {"sequence": payload["sequence"] + 1, "kind": "external_retention", "project_id": project_id,
                 "request_digest": request["request_digest"], "created_at": utcnow(), "target": target,
                 "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(), "expires_at": expiry, "status": status}
        payload["events"].append(event)
        payload["sequence"] += 1
        ledger._write(payload)
    return {"recorded": True, "target": target, "status": status, "expiry_independently_verified": False,
            "notice": "This is an operator record; live erasure does not delete external backups or provider copies."}
