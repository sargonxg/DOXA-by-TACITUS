"""Recoverable native import/index/publication jobs over existing execution engines.

Jobs freeze their inputs, preserve original run/report identities, and recheck
current account authority inside the real worker. No model downloads or remote
inference are initiated by this scheduler.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
import time
import uuid
import zipfile

from fastapi import File, Form, HTTPException, Request, UploadFile

from .access import AccessControl, WorkerAuthorizationError, current_principal
from .privacy_lifecycle import activity, require_active_project, track_artifact
from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS activity_jobs (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
 phase TEXT NOT NULL,config_key TEXT NOT NULL,config_sha256 TEXT NOT NULL,request_sha256 TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,state_json TEXT NOT NULL DEFAULT '{}',
 progress_json TEXT NOT NULL DEFAULT '{}',error_json TEXT,result_json TEXT,
 actor_id TEXT NOT NULL,managed_access INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0,lease_token TEXT,lease_until REAL,
 attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(project_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS activity_jobs_pending ON activity_jobs(status,lease_until,created_at);
CREATE INDEX IF NOT EXISTS activity_jobs_project ON activity_jobs(project_id,created_at);
"""
TERMINAL = {"completed", "partial", "failed", "cancelled", "paused_access"}
PERMISSION = {"index": "analyze", "guided_report": "export", "import": "write"}
MAX_ARCHIVE = 500 * 1024 * 1024


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(compact(value).encode()).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ensure_schema(store):
    if store.remote:
        return
    if not getattr(store, "_activity_jobs_ready", False):
        store.batch([{"sql": part.strip(), "params": []} for part in SCHEMA_SQL.split(";") if part.strip()])
        store._activity_jobs_ready = True


class Cancelled(Exception):
    pass


class Checkpoint(Exception):
    pass


class SourceChanged(ValueError):
    pass


class ActivityJobs:
    def __init__(self, store, services, *, access_control=None):
        self.store, self.services = store, services
        self.access_control = access_control or AccessControl.from_environment()
        self.access_control.ensure_native(store)
        self._stop = threading.Event()
        self._loop_task = None
        self._worker_task = None
        try:
            self._application_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._application_loop = None
        ensure_schema(store)

    async def start(self):
        if not self.store.remote and self._loop_task is None:
            self._application_loop = asyncio.get_running_loop()
            self._stop.clear()
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        if self._worker_task:
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None

    async def _loop(self):
        while not self._stop.is_set():
            if self._worker_task is None or self._worker_task.done():
                if self._worker_task and not self._worker_task.cancelled():
                    self._worker_task.exception()
                rows = self.store.query("SELECT id FROM activity_jobs WHERE status='queued' OR(status='running' AND COALESCE(lease_until,0)<?) ORDER BY updated_at,id LIMIT 1", [time.time()])
                self._worker_task = asyncio.create_task(asyncio.to_thread(self.work_once, rows[0]["id"])) if rows else None
            await asyncio.sleep(0.5)

    def _row(self, identity):
        row = self.store.one("SELECT * FROM activity_jobs WHERE id=?", [identity])
        if not row:
            raise HTTPException(404, "Activity job not found")
        return row

    def get(self, identity):
        row = self._row(identity)
        return {key: row[key] for key in ("id", "project_id", "kind", "status", "phase", "attempts", "created_at", "updated_at")} | {
            "summary": json.loads(row["state_json"]).get("summary", {}),
            "progress": json.loads(row["progress_json"]),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "result": json.loads(row["result_json"]) if row["result_json"] and row["status"] in {"completed", "partial"} else None,
            "cancel_requested": bool(row["cancel_requested"]),
            "can_restart": row["status"] in {"cancelled", "failed", "paused_access"} and (json.loads(row["error_json"] or "{}").get("code") not in {"source_changed", "delivery_authority_changed"}),
            "execution": {"managed_access": bool(row["managed_access"]), "actor_id": row["actor_id"]},
        }

    def list(self, project_id, *, limit=50):
        limit = max(1, min(int(limit), 100))
        rows = self.store.query("SELECT id FROM activity_jobs WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT ?", [project_id, limit])
        return {"items": [self.get(row["id"]) for row in rows],
                "total": self.store.one("SELECT COUNT(*) n FROM activity_jobs WHERE project_id=?", [project_id])["n"]}

    def _submission(self, project_id, kind):
        if self.store.remote:
            raise HTTPException(501, "Recoverable pilot activities require dedicated native storage")
        require_active_project(self.store, project_id)
        principal = current_principal()
        if self.access_control.managed:
            if not principal.managed:
                raise HTTPException(403, "Submit through an authenticated project account")
            try:
                self.access_control.require_worker(principal.id, project_id, PERMISSION[kind])
            except WorkerAuthorizationError as error:
                raise HTTPException(403, str(error)) from None
        return principal

    def _existing(self, project_id, key, request_hash):
        if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ValueError("Use an explicit 1–128 character idempotency key")
        row = self.store.one("SELECT * FROM activity_jobs WHERE project_id=? AND idempotency_key=?", [project_id, key])
        if row:
            if row["request_sha256"] != request_hash:
                raise HTTPException(409, "This idempotency key belongs to different submitted inputs")
            return self.get(row["id"])
        return None

    def create(self, project_id, body, *, idempotency_key):
        kind = body.get("kind")
        if kind not in {"index", "guided_report"}:
            raise ValueError("Choose index or guided_report; ZIP intake uses the import-archive upload route")
        principal = self._submission(project_id, kind)
        request_hash = digest({"body": body, "actor_id": principal.id})
        previous = self._existing(project_id, idempotency_key, request_hash)
        if previous:
            return previous
        with activity(self.store, project_id, "activity_submit"):
            if kind == "index":
                if set(body) - {"kind", "document_ids", "query_id"}:
                    raise ValueError("Index jobs accept only kind, document_ids and query_id")
                ids = body.get("document_ids")
                if body.get("query_id"):
                    from .workspace import Workspace
                    service = Workspace(self.store)
                    query = service.require_query(body["query_id"])
                    if query["project_id"] != project_id:
                        raise HTTPException(404, "Query does not belong to this project")
                    ids = service.document_ids(query)
                if ids is None:
                    ids = [row["id"] for row in self.store.query("SELECT id FROM documents WHERE project_id=? AND status='ready' ORDER BY id LIMIT 10001", [project_id])]
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 10000
                        or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)):
                    raise ValueError("Select between 1 and 10,000 distinct ready source IDs")
                with self.store.transaction():
                    sources = []
                    for identity in sorted(ids):
                        row = self.store.one("SELECT id,sha256,blocks_key,status FROM documents WHERE id=? AND project_id=?", [identity, project_id])
                        if not row or row["status"] != "ready" or not row["blocks_key"]:
                            raise ValueError("Selected sources must be ready documents in this project")
                        sources.append({"id": identity, "sha256": row["sha256"], "blocks_key": row["blocks_key"],
                                        "blocks_sha256": file_digest(self.store.object_path(row["blocks_key"])),
                                        "blocks_fingerprint": digest(self.store.get_json(row["blocks_key"]))})
                runtime = self._runtime()
                if hasattr(runtime, "health"):
                    health = runtime.health("minilm", verify_artifacts=False)
                    if not health.get("ok"):
                        raise ValueError("The pinned MiniLM runtime is not installed or ready. Open Models & providers and follow the explicit CPU installation guide before submitting an index; nothing was downloaded or scheduled")
                config = {"kind": kind, "document_ids": sorted(ids), "sources": sources,
                          "model_profile": runtime.model_profile("minilm")}
            else:
                from .pilot_api import capture_guided_report
                if set(body) - {"kind", "document_ids", "run_id", "formats", "expert_commentary", "review_ack", "quote_id"}:
                    raise ValueError("Unsupported guided report fields")
                snapshot, formats, trusted = capture_guided_report(self.store, project_id, body, self.services,
                                                                  actor_id=principal.id, permissions=principal.permissions)
                config = {"kind": kind, "snapshot": snapshot, "formats": formats, "trusted_reviews": trusted,
                          "quote_id": body.get("quote_id")}
            return self._persist(project_id, kind, config, principal, idempotency_key, request_hash)

    def _persist(self, project_id, kind, config, principal, key, request_hash, *, identity=None):
        identity, now = identity or uuid.uuid4().hex, utcnow()
        config_key = f"activity-jobs/{identity}/config.json"
        if len(compact(config).encode()) > 20 * 1024 * 1024:
            raise ValueError("Frozen job inputs exceed their 20 MiB bound")
        # Commit ownership before writing: a later insert/storage failure must
        # not roll its registry entry back and leave private unowned inputs.
        track_artifact(self.store, project_id, f"objects/activity-jobs/{identity}", "activity_job")
        self.store.put_json(config_key, config)
        with self.store.transaction(write=True):
            previous = self._existing(project_id, key, request_hash)
            if previous:
                return previous
            return self._persist_locked(project_id, kind, config, principal, key, request_hash,
                                        identity=identity, config_key=config_key, now=now)

    def _persist_locked(self, project_id, kind, config, principal, key, request_hash, *, identity, config_key, now):
        state = {}
        if kind == "guided_report":
            snapshot = config["snapshot"]
            state["summary"] = {"profile_id": snapshot["brief"]["profile_id"],
                "brief_revision": snapshot["brief"].get("revision"),
                "source_ids": [doc["id"] for doc in snapshot["documents"]],
                "review_status": "operator_signed" if config["trusted_reviews"] and snapshot.get("expert_commentary") else "provisional",
                "requested_formats": config["formats"]}
        self.store.execute("INSERT INTO activity_jobs(id,project_id,kind,status,phase,config_key,config_sha256,request_sha256,idempotency_key,state_json,progress_json,actor_id,managed_access,created_at,updated_at) VALUES(?,?,?,'queued','queued',?,?,?,?,?,?,?,?,?,?)", [
            identity, project_id, kind, config_key, digest(config), request_hash, key, compact(state),
            compact({"completed": 0, "total": len(config.get("document_ids", config.get("entries", []))), "percent": 0,
                     "message": "Submitted inputs are saved; waiting for a worker"}),
            principal.id, int(principal.managed), now, now])
        return self.get(identity)

    def _runtime(self):
        if self.services.get("cpu_runtime"):
            return self.services["cpu_runtime"]()
        if not hasattr(self, "_cpu_runtime"):
            import os
            from .cpu_models import CPUModels
            self._cpu_runtime = CPUModels(threads=int(os.environ.get("DOXA_CPU_THREADS", "2")),
                                          batch_size=int(os.environ.get("DOXA_CPU_BATCH_SIZE", "2")))
        return self._cpu_runtime

    def _report_manager(self):
        if self.services.get("report_jobs"):
            return self.services["report_jobs"]()
        from .report_jobs import ReportJobs
        if not hasattr(self, "_reports"):
            self._reports = ReportJobs(self.store, access_control=self.access_control)
        return self._reports

    def _authorize(self, row):
        require_active_project(self.store, row["project_id"])
        if self.access_control.managed:
            if not row["managed_access"]:
                raise WorkerAuthorizationError("An authenticated account must explicitly restart this historical activity")
            self.access_control.require_worker(row["actor_id"], row["project_id"], PERMISSION[row["kind"]])
        if row["kind"] == "guided_report":
            from .privacy_lifecycle import require_exports
            try:
                require_exports(self.store, row["project_id"])
            except ValueError:
                raise WorkerAuthorizationError("Export is no longer permitted by project policy") from None

    def _check(self, identity, lease, deadline=None):
        row = self._row(identity)
        self._authorize(row)
        if row["cancel_requested"]:
            raise Cancelled()
        if row["status"] != "running" or row["lease_token"] != lease:
            raise Checkpoint()
        if self._stop.is_set() or deadline and time.monotonic() > deadline:
            raise Checkpoint()
        self.store.execute("UPDATE activity_jobs SET lease_until=?,updated_at=? WHERE id=? AND lease_token=?", [time.time() + 120, utcnow(), identity, lease])

    def _update(self, identity, lease, *, state=None, progress=None, phase=None):
        fields, args = [], []
        for column, value in (("state_json", compact(state) if state is not None else None),
                              ("progress_json", compact(progress) if progress is not None else None), ("phase", phase)):
            if value is not None:
                fields.append(column + "=?")
                args.append(value)
        if fields:
            self.store.execute("UPDATE activity_jobs SET " + ",".join(fields) + ",updated_at=? WHERE id=? AND lease_token=?", [*args, utcnow(), identity, lease])

    def cancel(self, identity):
        row = self._row(identity)
        self._submission(row["project_id"], row["kind"])
        if row["status"] in {"completed", "partial", "failed", "cancelled"}:
            return self.get(identity)
        self.store.execute("UPDATE activity_jobs SET cancel_requested=1,status=CASE WHEN status IN('queued','paused_access') THEN 'cancelled' ELSE status END,phase=CASE WHEN status IN('queued','paused_access') THEN 'cancelled' ELSE phase END,updated_at=? WHERE id=?", [utcnow(), identity])
        for child in json.loads(row["state_json"]).get("report_job_ids", []):
            self._report_manager().cancel(child)
        return self.get(identity)

    def restart(self, identity):
        row = self._row(identity)
        principal = self._submission(row["project_id"], row["kind"])
        if not self.get(identity)["can_restart"]:
            raise HTTPException(409, "This activity cannot restart; create a new activity after reviewing its inputs")
        with activity(self.store, row["project_id"], "activity_restart"):
            children = json.loads(row["state_json"]).get("report_job_ids", [])
            # Report restart rechecks current caller authority and preserves
            # immutable captured evidence rather than rereading changed sources.
            for child in children:
                child_job = self._report_manager().get(child)
                if child_job["can_restart"]:
                    self._report_manager().restart(child)
            self.store.execute("UPDATE activity_jobs SET status='queued',phase='queued',cancel_requested=0,error_json=NULL,lease_token=NULL,lease_until=NULL,actor_id=?,managed_access=?,updated_at=? WHERE id=? AND status IN('failed','cancelled','paused_access')", [principal.id, int(principal.managed), utcnow(), identity])
        return self.get(identity)

    def work_once(self, identity, *, deadline_seconds=45):
        from .privacy_lifecycle import ProjectErasingError
        row = self._row(identity)
        if row["status"] in TERMINAL:
            return self.get(identity)
        lease = uuid.uuid4().hex
        acquired = self.store.execute("UPDATE activity_jobs SET status='running',phase=CASE WHEN phase='queued' THEN 'starting' ELSE phase END,lease_token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=? AND cancel_requested=0 AND(status='queued' OR(status='running' AND COALESCE(lease_until,0)<?))", [lease, time.time() + 120, utcnow(), identity, time.time()])
        if not acquired:
            return self.get(identity)
        try:
            with activity(self.store, row["project_id"], "activity_worker"):
                self._authorize(row)
                config = self.store.get_json(row["config_key"])
                if digest(config) != row["config_sha256"]:
                    raise SourceChanged()
                state = json.loads(row["state_json"])
                deadline = time.monotonic() + max(1, min(float(deadline_seconds), 420))
                if row["kind"] == "index":
                    from .local_profile import heavy_operation
                    with heavy_operation():
                        self._work_index(row, config, lease, deadline)
                elif row["kind"] == "import":
                    # Ingest acquires the heavy slot in its actual extraction
                    # thread. Holding it here across asyncio.run would deadlock
                    # that separate worker when the local profile has one slot.
                    self._work_import(row, config, state, lease, deadline)
                else:
                    self._work_guided(row, config, state, lease, deadline)
        except Cancelled:
            self.store.execute("UPDATE activity_jobs SET status='cancelled',phase='cancelled',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_token=?", [utcnow(), identity, lease])
        except Checkpoint:
            self.store.execute("UPDATE activity_jobs SET status='queued',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_token=? AND cancel_requested=0", [utcnow(), identity, lease])
        except WorkerAuthorizationError:
            for child in json.loads(self._row(identity)["state_json"]).get("report_job_ids", []):
                self._report_manager().cancel(child)
            self._fail(identity, lease, "authorization_required", "Current account authority or export policy changed; an authorized account must restart this activity", status="paused_access")
        except ProjectErasingError:
            self._fail(identity, lease, "project_erasing", "Project erasure has stopped this activity", status="cancelled")
        except SourceChanged:
            self._fail(identity, lease, "source_changed", "Submitted sources or model identity changed; create a new activity")
        except HTTPException as error:
            if error.status_code in {403, 404, 409}:
                self._fail(identity, lease, "delivery_authority_changed", "The accepted quote no longer permits this frozen report; contact the pilot operator before restarting", status="paused_access")
            else:
                self._fail(identity, lease, "operation_failed", "The requested operation could not finish; completed work is retained")
        except Exception:
            self._fail(identity, lease, "operation_failed", "The operation could not finish. Check source readiness, local model availability and storage, then restart; completed work is retained")
        return self.get(identity)

    def _fail(self, identity, lease, code, message, *, status="failed"):
        self.store.execute("UPDATE activity_jobs SET status=?,phase=?,error_json=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_token=?", [status, status, compact({"code": code, "message": message}), utcnow(), identity, lease])

    def _finish(self, row, lease, result, *, partial=False):
        self._check(row["id"], lease)
        status = "partial" if partial else "completed"
        self.store.execute("UPDATE activity_jobs SET status=?,phase=?,result_json=?,progress_json=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_token=? AND cancel_requested=0", [status, status, compact(result), compact({"completed": result.get("formats_completed", result.get("processed_documents", result.get("documents", 1))), "total": result.get("formats_completed", result.get("processed_documents", result.get("documents", 1))), "percent": 100, "message": "Operation finished with explicit source failures" if partial else "Committed results are ready"}), utcnow(), row["id"], lease])

    def _work_index(self, row, config, lease, deadline):
        from .semantic import SemanticIndex
        runtime = self._runtime()
        if runtime.model_profile("minilm") != config["model_profile"]:
            raise SourceChanged()
        for expected in config["sources"]:
            actual = self.store.one("SELECT sha256,blocks_key,status FROM documents WHERE id=? AND project_id=?", [expected["id"], row["project_id"]])
            if (not actual or actual["status"] != "ready" or actual["sha256"] != expected["sha256"]
                    or actual["blocks_key"] != expected["blocks_key"]
                    or file_digest(self.store.object_path(expected["blocks_key"])) != expected["blocks_sha256"]):
                raise SourceChanged()
        service = SemanticIndex(self.store, runtime)
        track_artifact(self.store, row["project_id"], "objects/" + service._prefix(row["project_id"]), "semantic_index")
        def checkpoint():
            # Cancellation/access are checked within embedding batches. A soft
            # scheduling deadline yields after a complete document bundle, so
            # a slow single source cannot restart forever before committing.
            self._check(row["id"], lease)
        def progress(completed, total, cached):
            self._update(row["id"], lease, phase="indexing", progress={"completed": completed, "total": total,
                "cached_documents": cached, "percent": 100 * completed / max(total, 1), "message": "Indexing exact-source passages; accepted document bundles survive restart"})
            if time.monotonic() > deadline and completed > cached:
                raise Checkpoint()
        expected_sources = {source["id"]: source for source in config["sources"]}
        def validate_document(document, blocks):
            expected = expected_sources[document["id"]]
            if (document["sha256"] != expected["sha256"] or document["blocks_key"] != expected["blocks_key"]
                    or digest(blocks) != expected["blocks_fingerprint"]):
                raise SourceChanged()
        result = service.build(row["project_id"], config["document_ids"], checkpoint=checkpoint,
                               progress=progress, validate_document=validate_document)
        self._finish(row, lease, result)

    def _work_guided(self, row, config, state, lease, deadline):
        children = state.get("report_job_ids") or [hashlib.sha256((row["id"] + ":" + fmt).encode()).hexdigest()[:32] for fmt in config["formats"]]
        self._update(row["id"], lease, state={**state, "report_job_ids": children}, phase="rendering")
        reports = self._report_manager()
        for identity, fmt in zip(children, config["formats"], strict=True):
            self._check(row["id"], lease, deadline)
            from .pilot_api import admit_guided_quote
            admit_guided_quote(self.store, row["project_id"], config.get("quote_id"), config["snapshot"])
            reports.create_guided(row["project_id"], config["snapshot"], fmt, identity=identity,
                                  quote_id=config.get("quote_id"),
                                  actor_id=row["actor_id"], managed_access=bool(row["managed_access"]),
                                  trusted_reviews=config["trusted_reviews"])
        jobs = [reports.get(identity) for identity in children]
        if any(job["status"] in {"failed", "cancelled"} for job in jobs):
            raise ValueError("A guided render failed or was cancelled")
        completed = sum(job["status"] == "completed" for job in jobs)
        self._update(row["id"], lease, progress={"completed": completed, "total": len(jobs),
            "percent": completed * 100 / len(jobs), "message": "Rendering through the persistent report engine"})
        if completed != len(jobs):
            raise Checkpoint()
        from .pilot_api import admit_guided_quote
        admit_guided_quote(self.store, row["project_id"], config.get("quote_id"), config["snapshot"])
        artifacts = [artifact for job in jobs for artifact in job["artifacts"] if artifact["format"] != "manifest"]
        result = {**state.get("summary", {}), "report_job_ids": children, "artifacts": artifacts,
                  "formats_completed": len(jobs),
                  "source_ids": [doc["id"] for doc in config["snapshot"]["documents"]],
                  "reference_status": "reference_pending", "frozen_snapshot_sha256": digest(config["snapshot"])}
        self._finish(row, lease, result)

    def create_archive(self, project_id, import_id, path, *, idempotency_key):
        principal = self._submission(project_id, "import")
        session = self.store.one("SELECT * FROM v3_imports WHERE id=? AND project_id=?", [import_id, project_id])
        if not session:
            raise HTTPException(404, "Import session does not belong to this project")
        archive_hash = file_digest(path)
        request_hash = digest({"kind": "import", "import_id": import_id, "archive_sha256": archive_hash, "actor_id": principal.id})
        previous = self._existing(project_id, idempotency_key, request_hash)
        if previous:
            return previous
        with activity(self.store, project_id, "archive_submit"):
            with zipfile.ZipFile(path) as bundle:
                infos = [item for item in bundle.infolist() if not item.is_dir()]
                if not infos or len(infos) > 10000 or sum(info.file_size for info in infos) > 1024 * 1024 * 1024:
                    raise ValueError("Archive supports 1–10,000 files and at most 1 GiB expanded content")
                entries, names = [], set()
                for info in infos:
                    normalized = info.filename.replace("\\", "/")
                    relative = PurePosixPath(normalized)
                    if (not normalized or relative.is_absolute() or ".." in relative.parts or "\x00" in normalized
                            or ":" in relative.parts[0] or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                            or normalized in names or info.file_size > MAX_ARCHIVE):
                        raise ValueError("Unsafe, repeated, encrypted or oversized archive member")
                    names.add(normalized)
                    entries.append({"name": info.filename, "bytes": info.file_size, "crc": info.CRC})
            identity = uuid.uuid4().hex
            key = f"activity-jobs/{identity}/archive.zip"
            track_artifact(self.store, project_id, f"objects/activity-jobs/{identity}", "import_archive")
            self.store.put_path(key, Path(path), "application/zip")
            config = {"kind": "import", "import_id": import_id, "batch_id": session["batch_id"],
                      "archive_key": key, "archive_sha256": archive_hash, "entries": entries}
            return self._persist(project_id, "import", config, principal, idempotency_key, request_hash, identity=identity)

    def _work_import(self, row, config, state, lease, deadline):
        path = self.store.object_path(config["archive_key"])
        if file_digest(path) != config["archive_sha256"]:
            raise SourceChanged()
        cursor = state.get("cursor", 0)
        items = list(state.get("items", []))
        if len(items) != cursor:
            raise SourceChanged()
        folder = self.store.root / "project-work" / row["project_id"] / row["id"] / lease
        track_artifact(self.store, row["project_id"], f"project-work/{row['project_id']}/{row['id']}", "import_work")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(path) as bundle:
                for ordinal in range(cursor, len(config["entries"])):
                    self._check(row["id"], lease, deadline)
                    entry = config["entries"][ordinal]
                    info = bundle.getinfo(entry["name"])
                    if info.file_size != entry["bytes"] or info.CRC != entry["crc"]:
                        raise SourceChanged()
                    target = folder / uuid.uuid4().hex
                    with bundle.open(info) as source, target.open("wb") as destination:
                        extracted_bytes = 0
                        while chunk := source.read(1024 * 1024):
                            self._check(row["id"], lease)
                            extracted_bytes += len(chunk)
                            if extracted_bytes > entry["bytes"] or extracted_bytes > MAX_ARCHIVE:
                                raise SourceChanged()
                            destination.write(chunk)
                        if extracted_bytes != entry["bytes"]:
                            raise SourceChanged()
                    sha = file_digest(target)
                    coroutine = self.services["ingest"](
                        row["project_id"], {"id": config["batch_id"], "project_id": row["project_id"]},
                        target, Path(entry["name"]).name, source_identity=entry["name"])
                    # Existing intake has event-loop-owned extraction slots.
                    # Run it on the application loop, retaining this actual
                    # worker's erasure lease until its committed result returns.
                    if self._application_loop is not None and self._application_loop.is_running():
                        document, _duplicate = asyncio.run_coroutine_threadsafe(coroutine, self._application_loop).result()
                    else:
                        document, _duplicate = asyncio.run(coroutine)
                    target.unlink(missing_ok=True)
                    self._check(row["id"], lease)
                    self.store.execute("INSERT INTO v3_import_files(import_id,relative_path,sha256,document_id,status,error,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(import_id,relative_path,sha256) DO UPDATE SET document_id=excluded.document_id,status=excluded.status,error=excluded.error", [config["import_id"], entry["name"], sha, document["id"], document["status"], document.get("error"), utcnow()])
                    items.append({"name": entry["name"], "sha256": sha, "document_id": document["id"], "status": document["status"]})
                    self._update(row["id"], lease, state={"cursor": ordinal + 1, "items": items}, phase="extracting",
                        progress={"completed": ordinal + 1, "total": len(config["entries"]),
                                  "percent": 100 * (ordinal + 1) / len(config["entries"]),
                                  "message": "Extracted source status is committed before advancing"})
            failed = sum(item["status"] == "failed" for item in items)
            ready = sum(item["status"] == "ready" for item in items)
            session = self.store.one("SELECT COUNT(*) total,COALESCE(SUM(status='ready'),0) ready,COALESCE(SUM(status='failed'),0) failed FROM v3_import_files WHERE import_id=?", [config["import_id"]])
            self.store.execute("UPDATE v3_imports SET completed_files=?,failed_files=?,status=CASE WHEN total_files<=? THEN 'completed' ELSE 'running' END WHERE id=?", [session["ready"], session["failed"], session["total"], config["import_id"]])
            self._finish(row, lease, {"import_id": config["import_id"], "documents": len(config["entries"]),
                                     "ready_documents": ready, "failed_documents": failed}, partial=failed > 0)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def register_activity_api(app, services):
    """Register before installing the managed route allowlist wrappers."""
    def manager():
        if services["db"]().remote:
            raise HTTPException(501, "Recoverable pilot activities require dedicated native storage; use the existing Cloudflare research workflow")
        if not hasattr(app.state, "activity_jobs"):
            app.state.activity_jobs = ActivityJobs(services["db"](), services,
                access_control=getattr(app.state, "access_control", None))
        return app.state.activity_jobs

    def project(project_id):
        services["require_project"](project_id)
        return manager()

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, zipfile.BadZipFile) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/v3/projects/{project_id}/jobs", status_code=202)
    def submit(project_id: str, body: dict, request: Request):
        return call(project(project_id).create, project_id, body,
                    idempotency_key=request.headers.get("idempotency-key", ""))

    @app.get("/api/v3/projects/{project_id}/jobs")
    def jobs(project_id: str, limit: int = 50):
        return project(project_id).list(project_id, limit=limit)

    @app.get("/api/v3/jobs/{job_id}")
    def detail(job_id: str):
        return manager().get(job_id)

    @app.post("/api/v3/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return manager().cancel(job_id)

    @app.post("/api/v3/jobs/{job_id}/restart")
    def restart(job_id: str):
        return manager().restart(job_id)

    @app.post("/api/v3/projects/{project_id}/jobs/import-archive", status_code=202)
    async def archive(project_id: str, request: Request, import_id: str = Form(...), file: UploadFile = File(...)):
        service = project(project_id)
        from .pilot_api import project_work_directory
        import tempfile
        with activity(service.store, project_id, "archive_upload"):
            with tempfile.TemporaryDirectory(dir=project_work_directory(service.store, project_id)) as working:
                path, total = Path(working) / "archive.zip", 0
                with path.open("wb") as target:
                    while part := await file.read(1024 * 1024):
                        total += len(part)
                        if total > MAX_ARCHIVE:
                            raise HTTPException(413, "Archive transport exceeds 500 MiB")
                        target.write(part)
                return call(service.create_archive, project_id, import_id, path,
                            idempotency_key=request.headers.get("idempotency-key", ""))

    @app.get("/api/v3/projects/{project_id}/activity")
    def activity_center(project_id: str):
        service = project(project_id)
        imports = service.store.query("SELECT id,name,status,total_files,completed_files,failed_files,created_at FROM v3_imports WHERE project_id=? ORDER BY created_at DESC LIMIT 50", [project_id])
        runs = service.store.query("SELECT id,status,created_at FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 50", [project_id])
        report_manager = service._report_manager()
        return {"project_id": project_id, "jobs": service.list(project_id), "imports": imports,
                "runs": runs, "report_jobs": report_manager.list(project_id),
                "notice": "Processing coverage, model judgments and human review are separate measures. Completed indexing or rendering does not validate interpretations."}

    app.state.activity_services = services
