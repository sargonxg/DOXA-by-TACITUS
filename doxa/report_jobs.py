"""Persistent report jobs: immutable inputs, bounded capture and atomic readiness."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import threading
import time
import uuid

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse

from . import report_source
from .access import WorkerAuthorizationError
from .recorded_export import compact, file_hash
from .store import utcnow

FORMATS = {"pdf", "html", "xlsx", "csv", "jsonl"}
TERMINAL = {"completed", "failed", "cancelled"}


class Cancelled(Exception):
    pass


class Stopped(Exception):
    pass


class DeliveryAuthorityError(WorkerAuthorizationError):
    """A frozen publication needs a new accepted service agreement."""


def _render_child(database, metadata, output, format):
    path = Path(output)
    try:
        result = report_source.render(Path(database), Path(metadata), path, format)
        (path / "result.json").write_text(compact({"artifacts": result}))
    except BaseException as error:
        path.mkdir(parents=True, exist_ok=True)
        (path / "result.json").write_text(compact({"error": str(error)[:1000]}))
        raise


def _casebook_child(config_path, output, format, profile):
    from .casebook_reports import render_casebook
    path = Path(output)
    try:
        config = json.loads(Path(config_path).read_text())
        artifacts = render_casebook(config["casebook"], path, format, profile)
        (path / "result.json").write_text(compact({"artifacts": artifacts}))
    except BaseException as error:
        path.mkdir(parents=True, exist_ok=True)
        (path / "result.json").write_text(compact({"error": str(error)[:1000]}))
        raise


def _guided_child(config_path, output, format):
    """Render a bounded guided snapshot in the existing cancellable child."""
    from .pilot_reports import build_pilot_report
    path = Path(output)
    try:
        config = json.loads(Path(config_path).read_text())
        result = build_pilot_report(config["guided_pilot"], format,
                                    trusted_reviews=config.get("trusted_reviews", False))
        filename = "doxa-guided-report." + result["extension"]
        artifact = path / filename
        artifact.write_bytes(result["content"])
        manifest = path / "manifest.json"
        manifest.write_text(compact(result["manifest"]), encoding="utf-8")
        artifacts = [{"format": format, "filename": filename, "path": str(artifact),
                      "bytes": artifact.stat().st_size, "sha256": file_hash(artifact),
                      "content_type": result["media_type"]},
                     {"format": "manifest", "filename": manifest.name, "path": str(manifest),
                      "bytes": manifest.stat().st_size, "sha256": file_hash(manifest),
                      "content_type": "application/json"}]
        (path / "result.json").write_text(compact({"artifacts": artifacts}))
    except BaseException as error:
        path.mkdir(parents=True, exist_ok=True)
        (path / "result.json").write_text(compact({"error": str(error)[:1000]}))
        raise


class ReportJobs:
    def __init__(self, store, *, access_control=None):
        from .access import AccessControl
        self.store = store
        self.access_control = access_control or AccessControl.from_environment()
        self.access_control.ensure_native(store)
        self._stop = threading.Event()
        self._loop_task = None
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self):
        if not self.store.remote and self._loop_task is None:
            self._stop.clear()
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        if self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def _loop(self):
        while not self._stop.is_set():
            if not self._tasks:
                rows = self.store.query("SELECT id FROM report_jobs WHERE status='queued' OR(status='running' AND lease_until<?) ORDER BY created_at LIMIT 1", [time.time()])
                for row in rows:
                    identity = row["id"]
                    task = asyncio.create_task(asyncio.to_thread(self.work_once, identity, deadline_seconds=420))
                    self._tasks[identity] = task
                    def finished(completed, identity=identity):
                        self._tasks.pop(identity, None)
                        if not completed.cancelled():
                            completed.exception()
                    task.add_done_callback(finished)
            await asyncio.sleep(0.2)

    def _row(self, identity):
        row = self.store.one("SELECT * FROM report_jobs WHERE id=?", [identity])
        if not row:
            raise HTTPException(404, "Report job not found")
        return row

    def get(self, identity):
        row = self._row(identity)
        result = {key: value for key, value in row.items() if key not in {"config_key", "state_json", "error_json", "progress_json", "lease_token", "lease_until"}}
        result["progress"] = json.loads(row["progress_json"] or "{}")
        result["error"] = json.loads(row["error_json"]) if row["error_json"] else None
        result["cancel_requested"] = bool(row["cancel_requested"])
        execution = json.loads(row["state_json"] or "{}")
        if execution.get("managed_access"):
            result["execution"] = {"managed_access": True, "actor_id": execution.get("execution_actor_id")}
        result["can_restart"] = row["status"] in {"failed", "cancelled"} and (result["error"] or {}).get("code") not in {"source_changed", "delivery_authority_changed"}
        result["artifacts"] = [{"format": item["format"], "filename": item["filename"], "bytes": item["bytes"], "sha256": item["sha256"],
                                "download_url": f'/api/v3/report-jobs/{identity}/artifacts/{item["format"]}'} for item in self.store.query("SELECT * FROM report_job_artifacts WHERE job_id=? ORDER BY format", [identity])] if row["status"] == "completed" else []
        return result

    def list(self, project_id, limit=20, cursor=None):
        limit = max(1, min(int(limit), 50))
        params, clause = [project_id], "project_id=?"
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if value["project_id"] != project_id or value["kind"] != "report_jobs":
                    raise ValueError()
                clause += " AND(created_at<? OR(created_at=? AND id<?))"
                params += [value["created_at"], value["created_at"], value["id"]]
            except (ValueError, KeyError, TypeError):
                raise HTTPException(422, "Cursor does not belong to this report list") from None
        rows = self.store.query("SELECT id,created_at FROM report_jobs WHERE " + clause + " ORDER BY created_at DESC,id DESC LIMIT ?", [*params, limit+1])
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit-1]
            next_cursor = base64.urlsafe_b64encode(compact({"project_id": project_id, "kind": "report_jobs", **last}).encode()).decode().rstrip("=")
        return {"items": [self.get(row["id"]) for row in rows[:limit]], "next_cursor": next_cursor,
                "total": self.store.one("SELECT COUNT(*) n FROM report_jobs WHERE project_id=?", [project_id])["n"]}

    def create(self, project_id, body):
        from .privacy_lifecycle import activity
        with activity(self.store, project_id, "report_submit"):
            return self._create(project_id, body)

    def _create(self, project_id, body):
        execution = self._submission_actor(project_id)
        unknown = set(body) - {"query_id", "format", "profile", "publication_type", "casebook_id"}
        if unknown:
            raise ValueError("Unsupported report-job fields: " + ", ".join(sorted(unknown)))
        format, profile = body.get("format", "pdf"), body.get("profile", "briefing")
        publication = body.get("publication_type", "briefing")
        if format not in FORMATS or profile not in {"research", "briefing", "teaching"} or publication not in {"briefing", "casebook", "teaching", "archive"}:
            raise ValueError("Unsupported report format, profile or publication type")
        if body.get("casebook_id"):
            from .casebooks import get_casebook
            book = get_casebook(self.store, str(body["casebook_id"]))
            if book["project_id"] != project_id:
                raise ValueError("Casebook does not belong to this project")
            config = {"project_id": project_id, "query_id": book["query_id"], "scope_hash": book["scope_hash"], "snapshot_version": book["snapshot_version"],
                      "casebook": book, "metadata": {"profile": profile}}
        elif publication == "casebook":
            raise ValueError("Choose a casebook draft for a casebook publication")
        else:
            config = report_source.source_config(self.store, project_id, str(body.get("query_id", "")), profile)
        identity, now = uuid.uuid4().hex, utcnow()
        key = f"report-jobs/{identity}/config.json"
        if not self.store.remote:
            from .privacy_lifecycle import track_artifact
            track_artifact(self.store, project_id, f"objects/report-jobs/{identity}", "report_job")
        self.store.put_json(key, config)
        self.store.execute("INSERT INTO report_jobs(id,project_id,query_id,casebook_id,casebook_revision,publication_type,format,profile,status,phase,scope_hash,snapshot_version,config_key,state_json,progress_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'queued','queued',?,?,?,?,?,?,?)", [
            identity, project_id, config["query_id"], body.get("casebook_id"), config.get("casebook", {}).get("revision"), publication, format, profile,
            config["scope_hash"], config["snapshot_version"], key, compact({"stage": 0, "cursor": "", "ordinal": 0, **execution}),
            compact({"phase": "queued", "completed": 0, "total": 0, "percent": 0, "message": "Waiting to freeze report inputs"}), now, now])
        if self.store.remote:
            try:
                self.store.enqueue_report(identity)
            except Exception:
                # The durable queued row remains discoverable by dispatch recovery.
                pass
        return self.get(identity)

    def create_guided(self, project_id, snapshot, format, *, identity, actor_id=None,
                      managed_access=False, trusted_reviews=False, quote_id=None):
        """Idempotently submit a frozen guided snapshot to this report engine.

        Only trusted server code calls this; actor authority is independently
        revalidated here and on every worker/render/publication checkpoint.
        """
        from .privacy_lifecycle import activity, require_exports, track_artifact
        if self.store.remote:
            raise ValueError("Guided publication requires dedicated native storage")
        if format not in {"pdf", "html", "xlsx", "json"}:
            raise ValueError("Unsupported guided report format")
        if len(identity) != 32 or any(char not in "0123456789abcdef" for char in identity):
            raise ValueError("Invalid deterministic guided report identity")
        with activity(self.store, project_id, "guided_report_submit"):
            require_exports(self.store, project_id)
            if self.access_control.managed:
                self.access_control.require_worker(actor_id, project_id, "export")
                if not managed_access:
                    raise ValueError("Managed report execution requires an authenticated owner")
            from .pilot_api import admit_guided_quote, guided_quote_scope
            admit_guided_quote(self.store, project_id, quote_id, snapshot)
            delivery_quote = {"quote_id": quote_id, "scope": guided_quote_scope(snapshot)}
            config = {"project_id": project_id, "guided_pilot": snapshot,
                      "trusted_reviews": bool(trusted_reviews), "delivery_quote": delivery_quote}
            encoded = compact(config)
            if len(encoded.encode()) > 16 * 1024 * 1024:
                raise ValueError("Frozen guided report exceeds its 16 MiB input limit")
            scope = hashlib.sha256(encoded.encode()).hexdigest()
            existing = self.store.one("SELECT * FROM report_jobs WHERE id=?", [identity])
            if existing:
                if existing["project_id"] != project_id or existing["scope_hash"] != scope or existing["format"] != format:
                    raise ValueError("Guided report identity conflicts with immutable inputs")
                return self.get(identity)
            key = f"report-jobs/{identity}/config.json"
            track_artifact(self.store, project_id, f"objects/report-jobs/{identity}", "guided_report_job")
            self.store.put_json(key, config)
            now = utcnow()
            state = {"stage": 0, "execution_actor_id": actor_id,
                     "managed_access": bool(managed_access), "delivery_quote": delivery_quote}
            self.store.execute("INSERT INTO report_jobs(id,project_id,query_id,publication_type,format,profile,status,phase,scope_hash,snapshot_version,config_key,state_json,progress_json,created_at,updated_at) VALUES(?,?,?,'guided',?,'briefing','queued','queued',?,'guided-pilot-0.6',?,?,?,?,?) ON CONFLICT(id) DO NOTHING", [
                identity, project_id, "guided:" + identity, format, scope, key,
                compact(state), compact({"phase": "queued", "completed": 0, "total": 1,
                                        "percent": 0, "message": "Frozen guided inputs are ready for rendering"}), now, now])
            return self.get(identity)

    def cancel(self, identity):
        row = self._row(identity)
        if row["status"] in TERMINAL:
            return self.get(identity)
        now = utcnow()
        self.store.execute("UPDATE report_jobs SET cancel_requested=1,phase=CASE WHEN status='queued' THEN 'cancelled' ELSE phase END,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,completed_at=CASE WHEN status='queued' THEN ? ELSE completed_at END,updated_at=? WHERE id=? AND status IN ('queued','running')", [now, now, identity])
        return self.get(identity)

    def restart(self, identity):
        from .privacy_lifecycle import activity
        row = self._row(identity)
        with activity(self.store, row["project_id"], "report_restart"):
            return self._restart(identity)

    def _restart(self, identity):
        if not self.get(identity)["can_restart"]:
            raise HTTPException(409, "This job cannot be restarted; refresh its source query and create a new report")
        row = self._row(identity)
        state = {**json.loads(row["state_json"] or "{}"), **self._submission_actor(row["project_id"])}
        self.store.execute("UPDATE report_jobs SET status='queued',phase='queued',cancel_requested=0,error_json=NULL,lease_token=NULL,lease_until=NULL,completed_at=NULL,state_json=?,updated_at=? WHERE id=? AND status IN ('failed','cancelled')", [compact(state), utcnow(), identity])
        if self.store.remote:
            self.store.enqueue_report(identity)
        return self.get(identity)

    def _check(self, identity, lease):
        row = self._row(identity)
        if not self.store.remote:
            from .privacy_lifecycle import ProjectErasingError, require_active_project
            try:
                require_active_project(self.store, row["project_id"])
            except ProjectErasingError:
                raise Cancelled() from None
        self._require_authorized(row)
        if self._stop.is_set():
            raise Stopped()
        if row["cancel_requested"]:
            raise Cancelled()
        if row["lease_token"] != lease or row["status"] != "running":
            raise Stopped()
        self.store.execute("UPDATE report_jobs SET lease_until=?,updated_at=? WHERE id=? AND lease_token=? AND status='running'", [time.time()+480, utcnow(), identity, lease])

    def work_once(self, identity, *, deadline_seconds=420):
        from .access import WorkerAuthorizationError
        from .privacy_lifecycle import ProjectErasingError, activity
        row = self._row(identity)
        if row["status"] in TERMINAL:
            return {"pending": False, "status": row["status"], "job_id": identity}
        try:
            from .local_profile import heavy_operation
            with heavy_operation(), activity(self.store, row["project_id"], "report_worker"):
                self._require_authorized(row)
                return self._work_once(identity, deadline_seconds=deadline_seconds)
        except WorkerAuthorizationError as error:
            self._authorization_failed(row, error)
            current = self._row(identity)
            return {"pending": current["status"] not in TERMINAL, "status": current["status"], "job_id": identity}
        except ProjectErasingError:
            return {"pending": False, "status": "cancelled", "job_id": identity}

    def _submission_actor(self, project_id):
        from .access import WorkerAuthorizationError, current_principal
        if not self.store.remote:
            from .privacy_lifecycle import require_exports
            try:
                require_exports(self.store, project_id)
            except ValueError:
                raise HTTPException(403, "Exports are disabled by this project's privacy policy") from None
        if not self.access_control.managed:
            return {}
        principal = current_principal()
        try:
            if not principal.managed:
                raise WorkerAuthorizationError("Submit or restart this report through an authenticated project account")
            self.access_control.require_worker(principal.id, project_id, "export")
        except WorkerAuthorizationError as error:
            raise HTTPException(403, str(error)) from None
        return {"execution_actor_id": principal.id, "managed_access": True}

    def _require_authorized(self, row):
        from .access import WorkerAuthorizationError
        if self.access_control.managed:
            state = json.loads(row["state_json"] or "{}")
            self.access_control.require_worker(state.get("execution_actor_id"), row["project_id"], "export")
        if not self.store.remote:
            from .privacy_lifecycle import require_exports
            try:
                require_exports(self.store, row["project_id"])
            except ValueError:
                raise WorkerAuthorizationError("Export is no longer permitted by this project's privacy policy") from None
            if row["publication_type"] == "guided":
                from .pilot_commerce import CommerceError, admit_quote_work
                delivery = json.loads(row["state_json"] or "{}").get("delivery_quote", {})
                try:
                    admit_quote_work(self.store, row["project_id"], delivery.get("quote_id"), **delivery["scope"])
                except (CommerceError, KeyError):
                    raise DeliveryAuthorityError("The accepted quote no longer permits this frozen report; submit a new report under a new accepted quote") from None

    def _authorization_failed(self, row, error, lease=None):
        match, value = ("lease_token=?", lease) if lease else ("state_json=?", row["state_json"])
        self.store.execute("UPDATE report_jobs SET status='failed',phase='failed',error_json=?,lease_token=NULL,lease_until=NULL,completed_at=?,updated_at=? WHERE id=? AND " + match + " AND status IN ('queued','running')",
                           [compact({"code": "delivery_authority_changed" if isinstance(error, DeliveryAuthorityError) else "authorization_required", "message": str(error)[:1000]}), utcnow(), utcnow(), row["id"], value])

    def _work_once(self, identity, *, deadline_seconds=420):
        row = self._row(identity)
        if row["status"] in TERMINAL:
            return {"pending": False, "status": row["status"], "job_id": identity}
        work_seconds = max(1, min(420, float(deadline_seconds)))
        lease, deadline = uuid.uuid4().hex, time.monotonic()+work_seconds
        acquired = self.store.execute("UPDATE report_jobs SET status='running',lease_token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=? AND cancel_requested=0 AND(status='queued' OR(status='running' AND COALESCE(lease_until,0)<?))", [lease, time.time()+480, utcnow(), identity, time.time()])
        if not acquired:
            return {"pending": True, "status": row["status"], "job_id": identity}
        # A killed parent can leave a renderer alive. A reclaimed lease must
        # never share its database/output paths with that obsolete process.
        folder = self.store.root / "report-work" / identity / lease
        folder.mkdir(parents=True, exist_ok=True)
        try:
            config = self.store.get_json(row["config_key"])
            state = json.loads(row["state_json"] or "{}")
            if config.get("guided_pilot"):
                if hashlib.sha256(compact(config).encode()).hexdigest() != row["scope_hash"]:
                    raise report_source.SourceChanged("Frozen guided inputs changed after submission")
                self._check(identity, lease)
                self.store.execute("UPDATE report_jobs SET phase='render',progress_json=?,updated_at=? WHERE id=? AND lease_token=?", [compact({"phase": "render", "completed": 0, "total": 1, "percent": 75, "message": "Rendering the frozen guided briefing"}), utcnow(), identity, lease])
                output = folder / "output"
                output.mkdir(exist_ok=True)
                config_path = folder / "guided.json"
                config_path.write_text(compact(config), encoding="utf-8")
                result = self._run_renderer(identity, lease, deadline, output, _guided_child,
                                            (str(config_path), str(output), row["format"]))
                return self._publish(identity, lease, result["artifacts"])
            if config.get("casebook"):
                self._check(identity, lease)
                self.store.execute("UPDATE report_jobs SET phase='render',progress_json=?,updated_at=? WHERE id=? AND lease_token=?", [compact({"phase": "render", "completed": 0, "total": 1, "percent": 75, "message": "Rendering the submitted frozen casebook revision"}), utcnow(), identity, lease])
                output = folder / "output"
                output.mkdir(exist_ok=True)
                config_path = folder / "casebook.json"
                config_path.write_text(compact(config), encoding="utf-8")
                result = self._run_renderer(identity, lease, deadline, output, _casebook_child, (str(config_path), str(output), row["format"], row["profile"]))
                return self._publish(identity, lease, result["artifacts"])
            if not state.get("frozen"):
                with report_source.source_reader(self.store) as reader:
                    report_source.revision_guard(reader, config)
                    while state.get("stage", 0) < len(report_source.STAGES):
                        self._check(identity, lease)
                        if time.monotonic() > deadline - 10:
                            return self._pause(identity, lease, state)
                        stage = report_source.STAGES[state["stage"]]
                        rows, cursor = report_source.capture_page(reader, config, stage, state.get("cursor", ""))
                        if not rows:
                            state.update({"stage": state["stage"]+1, "cursor": "", "ordinal": 0})
                            self.store.execute("UPDATE report_jobs SET state_json=?,phase=?,updated_at=? WHERE id=? AND lease_token=?", [compact(state), "capture_"+stage, utcnow(), identity, lease])
                            continue
                        raw = report_source.encode_part(stage, rows)
                        key = f"report-jobs/{identity}/parts/{lease}/{stage}-{state['ordinal']}.json.gz"
                        self.store.put_bytes(key, raw, "application/gzip")
                        digest = hashlib.sha256(raw).hexdigest()
                        next_state = {**state, "cursor": cursor, "ordinal": state.get("ordinal", 0)+1}
                        total = config["expected_documents"] + config["expected_assessments"] + int(config["metadata"]["metrics"].get("coverage_detail", {}).get("units_total", 0))
                        completed = self.store.one("SELECT COALESCE(SUM(row_count),0) n FROM report_job_parts WHERE job_id=?", [identity])["n"] + len(rows)
                        progress = {"phase": "capture_"+stage, "completed": completed, "total": total, "percent": min(65, 65*completed/max(total, 1)), "message": "Freezing exact report records"}
                        results = self.store.batch([
                            {"sql": "INSERT INTO report_job_parts(job_id,stage,ordinal,object_key,bytes,sha256,row_count,last_key) SELECT ?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM report_jobs WHERE id=? AND lease_token=? AND status='running' AND cancel_requested=0)", "params": [identity, stage, state.get("ordinal", 0), key, len(raw), digest, len(rows), cursor, identity, lease]},
                            {"sql": "UPDATE report_jobs SET state_json=?,progress_json=?,phase=?,updated_at=? WHERE id=? AND lease_token=? AND changes()=1", "params": [compact(next_state), compact(progress), progress["phase"], utcnow(), identity, lease]},
                        ])
                        if not results[0]["changes"]:
                            raise Cancelled()
                        state = next_state
                    report_source.revision_guard(reader, config)
                counts = {item["stage"]: item["n"] for item in self.store.query("SELECT stage,SUM(row_count) n FROM report_job_parts WHERE job_id=? GROUP BY stage", [identity])}
                if counts.get("documents", 0) != config["expected_documents"] or counts.get("assessments", 0) != config["expected_assessments"]:
                    raise report_source.SourceChanged("Frozen report counts differ from the selected query")
                state["frozen"] = True
                self.store.execute("UPDATE report_jobs SET state_json=?,phase='materialize',updated_at=? WHERE id=? AND lease_token=?", [compact(state), utcnow(), identity, lease])
            if time.monotonic() > deadline - 90:
                return self._pause(identity, lease, state)
            self._check(identity, lease)
            parts = self.store.query("SELECT * FROM report_job_parts WHERE job_id=? ORDER BY stage,ordinal", [identity])
            self.store.execute("UPDATE report_jobs SET phase='materialize',progress_json=?,updated_at=? WHERE id=? AND lease_token=?", [compact({"phase": "materialize", "completed": 0, "total": 1, "percent": 70, "message": "Preparing verified frozen inputs"}), utcnow(), identity, lease])
            database, metadata = report_source.materialize(self.store, config, parts, folder / "inputs", lambda: self._check(identity, lease))
            reserve = (300 if work_seconds >= 420 else 180) if row["format"] == "xlsx" else 90
            if time.monotonic() > deadline - reserve:
                if state.get("render_deferred"):
                    raise TimeoutError("Frozen input preparation leaves insufficient rendering time; narrow the report selection")
                state["render_deferred"] = True
                return self._pause(identity, lease, state)
            self.store.execute("UPDATE report_jobs SET phase='render',progress_json=?,updated_at=? WHERE id=? AND lease_token=?", [compact({"phase": "render", "completed": 0, "total": 1, "percent": 75, "message": "Rendering from immutable inputs"}), utcnow(), identity, lease])
            output = folder / "output"
            output.mkdir(exist_ok=True)
            result = self._run_renderer(identity, lease, deadline, output, _render_child, (str(database), str(metadata), str(output), row["format"]))
            inputs = output / "frozen-inputs.zip"
            report_source.input_zip(config, parts, folder / "inputs", inputs)
            artifacts = result["artifacts"] + [{"format": "inputs", "filename": inputs.name, "path": str(inputs), "bytes": inputs.stat().st_size, "sha256": file_hash(inputs), "content_type": "application/zip"}]
            return self._publish(identity, lease, artifacts)
        except Cancelled:
            self.store.execute("UPDATE report_jobs SET status='cancelled',phase='cancelled',lease_token=NULL,lease_until=NULL,completed_at=?,updated_at=? WHERE id=? AND lease_token=?", [utcnow(), utcnow(), identity, lease])
        except Stopped:
            self._pause(identity, lease, json.loads(self._row(identity)["state_json"]))
        except Exception as error:
            from .access import WorkerAuthorizationError
            if isinstance(error, WorkerAuthorizationError):
                self._authorization_failed(self._row(identity), error, lease)
                current = self._row(identity)
                return {"pending": current["status"] not in TERMINAL, "status": current["status"], "job_id": identity}
            code = "source_changed" if isinstance(error, report_source.SourceChanged) else "render_timeout" if isinstance(error, TimeoutError) else "report_failed"
            self.store.execute("UPDATE report_jobs SET status='failed',phase='failed',error_json=?,lease_token=NULL,lease_until=NULL,completed_at=?,updated_at=? WHERE id=? AND lease_token=?", [compact({"code": code, "message": str(error)[:1000]}), utcnow(), utcnow(), identity, lease])
        finally:
            # Checkpoints and ready artifacts live in Store, not this disposable
            # attempt directory. Retries reconstruct from verified input parts.
            shutil.rmtree(folder, ignore_errors=True)
        current = self._row(identity)
        return {"pending": current["status"] not in TERMINAL, "status": current["status"], "job_id": identity}

    def _run_renderer(self, identity, lease, deadline, output, target, args):
        result_path = output / "result.json"
        result_path.unlink(missing_ok=True)
        process = multiprocessing.get_context("spawn").Process(target=target, args=args)
        process.start()
        try:
            while process.is_alive():
                self._check(identity, lease)
                if time.monotonic() > deadline - 10:
                    raise TimeoutError("Rendering exceeded its bounded work time; narrow the report selection or restart")
                process.join(0.5)
            result = json.loads(result_path.read_text()) if result_path.exists() else {"error": "Renderer stopped without a verified result"}
            if process.exitcode or result.get("error"):
                raise ValueError(result.get("error") or "Renderer failed")
            return result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)

    def _pause(self, identity, lease, state):
        self.store.execute("UPDATE report_jobs SET status='queued',state_json=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_token=? AND cancel_requested=0", [compact(state), utcnow(), identity, lease])
        return {"pending": True, "status": "queued", "job_id": identity}

    def _publish(self, identity, lease, artifacts):
        for artifact in artifacts:
            self._check(identity, lease)
            path = Path(artifact["path"])
            if path.stat().st_size != artifact["bytes"] or file_hash(path) != artifact["sha256"]:
                raise ValueError("Rendered artifact verification failed")
            artifact["object_key"] = f"report-jobs/{identity}/artifacts/{lease}/{artifact['filename']}"
            self.store.put_path(artifact["object_key"], path, artifact["content_type"])
        self._check(identity, lease)
        statements = [{"sql": "INSERT INTO report_job_artifacts(job_id,format,object_key,filename,bytes,sha256,content_type,created_at) SELECT ?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM report_jobs WHERE id=? AND lease_token=? AND cancel_requested=0 AND status='running') ON CONFLICT(job_id,format) DO UPDATE SET object_key=excluded.object_key,filename=excluded.filename,bytes=excluded.bytes,sha256=excluded.sha256,content_type=excluded.content_type,created_at=excluded.created_at", "params": [identity, item["format"], item["object_key"], item["filename"], item["bytes"], item["sha256"], item["content_type"], utcnow(), identity, lease]} for item in artifacts]
        statements.append({"sql": "UPDATE report_jobs SET status='completed',phase='completed',progress_json=?,lease_token=NULL,lease_until=NULL,completed_at=?,updated_at=? WHERE id=? AND lease_token=? AND cancel_requested=0 AND status='running'", "params": [compact({"phase": "completed", "completed": 1, "total": 1, "percent": 100, "message": "Verified downloads are ready"}), utcnow(), utcnow(), identity, lease]})
        result = self.store.batch(statements)
        if not result[-1]["changes"]:
            raise Cancelled()
        return {"pending": False, "status": "completed", "job_id": identity}

    def artifact(self, identity, format, request):
        job = self._row(identity)
        if job["status"] != "completed":
            raise HTTPException(409, "Report artifacts are not ready")
        item = self.store.one("SELECT * FROM report_job_artifacts WHERE job_id=? AND format=?", [identity, format])
        if not item:
            raise HTTPException(404, "Report artifact not found")
        etag = '"' + item["sha256"] + '"'
        headers = {"ETag": etag, "Accept-Ranges": "bytes", "X-DOXA-Scope": job["scope_hash"], "X-DOXA-Snapshot": job["snapshot_version"], "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") in {etag, "*"}:
            return Response(status_code=304, headers=headers)
        if self.store.remote:
            path = self.store.root / "report-downloads" / identity / item["filename"]
            if not path.is_file() or path.stat().st_size != item["bytes"] or file_hash(path) != item["sha256"]:
                self.store.download(item["object_key"], path)
        else:
            path = self.store.object_path(item["object_key"])
        if not path.is_file() or path.stat().st_size != item["bytes"] or file_hash(path) != item["sha256"]:
            raise HTTPException(503, "Artifact integrity verification failed")
        return FileResponse(path, filename=item["filename"], media_type=item["content_type"], headers=headers)


def register(app, services):
    def manager():
        if "report_jobs" in services:
            return services["report_jobs"]()
        if not hasattr(app.state, "report_jobs"):
            app.state.report_jobs = ReportJobs(services["db"]())
        return app.state.report_jobs

    @app.post("/api/v3/projects/{project_id}/report-jobs", status_code=202)
    def create(project_id: str, body: dict):
        services["require_project"](project_id)
        try:
            return manager().create(project_id, body)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/v3/projects/{project_id}/report-jobs")
    def listing(project_id: str, limit: int = 20, cursor: str | None = None):
        services["require_project"](project_id)
        return manager().list(project_id, limit, cursor)

    @app.get("/api/v3/report-jobs/{job_id}")
    def get(job_id: str):
        return manager().get(job_id)

    @app.post("/api/v3/report-jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return manager().cancel(job_id)

    @app.post("/api/v3/report-jobs/{job_id}/restart")
    def restart(job_id: str):
        return manager().restart(job_id)

    @app.api_route("/api/v3/report-jobs/{job_id}/artifacts/{format}", methods=["GET", "HEAD"])
    def artifact(job_id: str, format: str, request: Request):
        return manager().artifact(job_id, format, request)
