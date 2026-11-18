"""Local and container HTTP adapter. Analysis semantics live in doxa.analysis."""
from __future__ import annotations

import asyncio
import errno
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from doxa.store import Store, utcnow
from doxa import __version__
from doxa.local_profile import apply_local_environment_defaults, configured_slots, heavy_operation, run_heavy

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
apply_local_environment_defaults()
MAX_FILE = 500 * 1024 * 1024
PART_SIZE = 8 * 1024 * 1024
TERMINAL = {"completed", "partial", "failed", "cancelled"}
PARSER_SLOTS = threading.BoundedSemaphore(configured_slots("parser"))


def task_execution_limits(provider: str, *, cloud_state: bool = False) -> dict:
    """Operational deadlines; these do not change the analytical method.

    The deployed container retains its shorter commit margin under the cloud
    HTTP limit. Only native/local-state Ollama work receives the longer budget.
    """
    if provider == "ollama" and not cloud_state:
        supplied = os.environ.get("DOXA_LOCAL_TASK_TIMEOUT_SECONDS")
        try:
            timeout = int(supplied) if supplied is not None else 1800
        except (TypeError, ValueError):
            raise ValueError("DOXA_LOCAL_TASK_TIMEOUT_SECONDS must be an integer between 480 and 7200") from None
        if not 480 <= timeout <= 7200:
            raise ValueError("DOXA_LOCAL_TASK_TIMEOUT_SECONDS must be an integer between 480 and 7200")
        source = "server_environment" if supplied is not None else "local_default"
    else:
        timeout = 420 if cloud_state else 480
        source = "cloud_container_limit" if cloud_state else "scheduler_default"
    return {"task_timeout_seconds": timeout, "task_lease_seconds": max(600, timeout + 120),
            "task_timeout_source": source}


def uid() -> str:
    return uuid.uuid4().hex


def parser_environment() -> dict[str, str]:
    """The extractor needs runtime paths and local OCR configuration, not API secrets."""
    allowed = {"PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME",
               "LANG", "LC_ALL", "LC_CTYPE", "TZ", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
               "XDG_CACHE_HOME", "XDG_DATA_HOME", "HF_HOME", "DOXA_ENABLE_OCR",
               "DOXA_DOCLING_ARTIFACTS", "DOXA_PARSER_MEMORY_MB", "DOXA_PDF_EXTRACTOR"}
    result = {key: value for key, value in os.environ.items() if key in allowed}
    result.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "OMP_NUM_THREADS": "2"})
    return result


def extract_path(path: Path, filename: str) -> dict:
    # An asyncio awaiter can be cancelled while its thread keeps working.
    # Hold the parser limit in the real worker, rather than only in the awaiter.
    with heavy_operation(), PARSER_SLOTS:
        return _extract_path(path, filename)


def _extract_path(path: Path, filename: str) -> dict:
    """One extraction policy for intake and explicit immutable re-extraction."""
    if Path(filename).suffix.lower() in {".txt", ".md", ".markdown"} and path.stat().st_size <= 20 * 1024 * 1024:
        from doxa.extraction import extract_text_document
        return extract_text_document(path, filename)
    output = path.parent / (uid() + ".json")
    try:
        result = subprocess.run([sys.executable, "-m", "doxa.extract_worker", str(path), str(output), filename],
                                capture_output=True, text=True, timeout=180, cwd=ROOT, env=parser_environment())
        if result.returncode:
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            raise ValueError((lines[-1] if lines else "Parser failed")[:600])
        return json.loads(output.read_text())
    finally:
        output.unlink(missing_ok=True)


def unpack(row: dict | None) -> dict | None:
    if row is None:
        return None
    row = dict(row)
    for key in list(row):
        if key.endswith("_json"):
            row[key[:-5]] = json.loads(row.pop(key) or "{}")
    return row


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=3000)


class RunInput(BaseModel):
    context_policy: Literal["structure-aware-1.0"] | None = None
    quote_id: str | None = Field(default=None, max_length=128)
    provider: str = "lexical"
    model: str = Field(default="", max_length=300)
    lens_ids: list[str] = Field(default_factory=lambda: ["discourse", "ihra", "jda", "nexus", "anti-muslim-hostility"])
    question: str = Field(default="What assumptions, frames, and competing interpretations appear in this corpus?", max_length=4000)
    persona: str = Field(default="research", max_length=2000)
    budget_usd: float = Field(default=2.0, ge=0, le=10000)
    custom_lens: dict | None = None
    document_ids: list[str] | None = None
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    methodology_version: str = "1.0"
    learning_snapshot_id: str | None = None
    allow_remote_text: bool = False
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    reasoning_token_allowance: int = Field(default=0, ge=0, le=8192)
    verification_of: str | None = None
    request_journal_mode: Literal["metadata", "visible_io"] = "metadata"
    service_profile_id: Literal["company", "public", "personal"] | None = None
    pilot_brief_revision: int | None = Field(default=None, ge=1)


class ReviewInput(BaseModel):
    review_status: str
    review_note: str = Field(default="", max_length=12000)


def create_app(data_dir: str | Path | None = None, *, start_workers: bool = True, use_saved_showcase: bool = True) -> FastAPI:
    from doxa.access import AccessControl, current_principal, install as install_access, require_project_access
    from doxa.resources import asset_root
    access = AccessControl.from_environment()
    assets_root = asset_root()
    tasks: dict[str, asyncio.Task] = {}
    extraction_slots = asyncio.Semaphore(configured_slots("parser"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(data_dir)
        access.ensure_native(app.state.store)
        app.state.store._managed_pilot = access.managed
        app.state.store.init()
        from doxa.pilot_api import ensure_schema as ensure_pilot_schema
        ensure_pilot_schema(app.state.store)
        from doxa.retention import restore_guard
        restore_guard(app.state.store)
        from doxa.report_jobs import ReportJobs
        app.state.report_jobs = ReportJobs(app.state.store, access_control=access)
        if not app.state.store.remote:
            from doxa.activity_jobs import ActivityJobs
            app.state.activity_jobs = ActivityJobs(app.state.store, app.state.activity_services, access_control=access)
        app.state.tasks = tasks
        if not app.state.store.one("SELECT id FROM projects LIMIT 1"):
            app.state.store.execute("INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)",
                                    [uid(), "My research workspace", "An evidence-centred discourse inquiry", utcnow()])
        if start_workers and not app.state.store.remote:
            await app.state.report_jobs.start()
            await app.state.activity_jobs.start()
            app.state.resume_pilot_erasure_jobs()
            async def maintain_retention():
                from doxa.retention import retention_tick
                while True:
                    try:
                        await asyncio.to_thread(retention_tick, app.state.store)
                    except Exception:
                        # Operational diagnostics contain no source text or secrets.
                        import logging
                        logging.getLogger("doxa.retention").error("Retention maintenance failed; no success receipt was issued")
                    await asyncio.sleep(60)
            tasks["_retention"] = asyncio.create_task(maintain_retention())
            for row in app.state.store.query("SELECT id FROM runs WHERE status IN ('queued','running')"):
                schedule(row["id"])
        yield
        for task in tasks.values():
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        if hasattr(app.state, "activity_jobs"):
            await app.state.activity_jobs.stop()
        await app.state.report_jobs.stop()
        app.state.store.close()

    app = FastAPI(title="DOXA by TACITUS · Bias Detection as a Service", version=__version__, lifespan=lifespan)

    def db() -> Store:
        return app.state.store

    async def project_thread(project_id, kind, function, *args, **kwargs):
        from functools import partial
        from doxa.privacy_lifecycle import run_activity
        callback = partial(function, *args, **kwargs)
        if kind in {"source_extract", "source_reextract", "legacy_report_render"}:
            callback = partial(run_heavy, callback)
        return await asyncio.to_thread(run_activity, db(), project_id, kind, callback)

    def require_project(project_id: str) -> dict:
        require_project_access(project_id)
        result = db().one("SELECT * FROM projects WHERE id=?", [project_id])
        if not result:
            raise HTTPException(404, "Project not found")
        return result

    def require_document(document_id: str, with_blocks: bool = False) -> dict:
        result = unpack(db().one("SELECT * FROM documents WHERE id=?", [document_id]))
        if not result:
            raise HTTPException(404, "Document not found")
        require_project_access(result["project_id"])
        if with_blocks:
            result["blocks"] = db().get_json(result["blocks_key"]) if result["blocks_key"] else []
        source = result.get("metadata", {}).get("source_id") or result.get("metadata", {}).get("source_identity") or result.get("source_url") or document_id
        result["source_version_id"] = hashlib.sha256(f"{source}:{result['sha256']}".encode()).hexdigest()[:32]
        return result

    def require_run(run_id: str, project_id: str | None = None) -> dict:
        run = unpack(db().one("SELECT * FROM runs WHERE id=?", [run_id]))
        if not run or (project_id is not None and run["project_id"] != project_id):
            raise HTTPException(404, "Run not found")
        require_project_access(run["project_id"])
        return run

    def settings() -> dict:
        saved = db().one("SELECT value_json FROM settings WHERE key='preferences'")
        result = {"monthly_budget_usd": 20.0, "default_provider": "cloudflare", "default_model": "@cf/google/gemma-4-26b-a4b-it"}
        if saved:
            result.update(json.loads(saved["value_json"]))
        result["cloudflare_configured"] = bool((os.environ.get("DOXA_CLOUDFLARE_BASE_URL") or any(os.environ.get(key) for key in ["DOXA_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID"])) and any(os.environ.get(key) for key in ["DOXA_CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"]))
        result["openai_configured"] = bool(os.environ.get("DOXA_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
        result["runpod_configured"] = bool(any(os.environ.get(key) for key in ["DOXA_RUNPOD_API_KEY", "RUNPOD_API_KEY"]) and any(os.environ.get(key) for key in ["DOXA_RUNPOD_ENDPOINT_ID", "RUNPOD_ENDPOINT_ID", "DOXA_RUNPOD_BASE_URL", "RUNPOD_BASE_URL"]))
        result["local_only"] = not db().remote
        result["private_request_journal_available"] = not db().remote and os.environ.get("DOXA_ENABLE_PRIVATE_JOURNAL", "").lower() in {"1", "true", "yes"}
        month = utcnow()[:7]
        result["budget"] = db().one("SELECT * FROM budget_ledger WHERE month=?", [month]) or {"month": month, "reserved_usd": 0, "spent_usd": 0}
        return result

    @app.middleware("http")
    async def protect(request: Request, call_next):
        token = os.environ.get("DOXA_INTERNAL_TOKEN", "")
        if token and (not access.managed or request.url.path.startswith("/internal/")):
            supplied = request.headers.get("x-doxa-service-token", "")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse({"detail": "Authenticated gateway required"}, status_code=401)
        elif request.url.path.startswith("/internal/"):
            # Internal work is permitted only by a configured service token, never from an arbitrary web page.
            if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                return JSONResponse({"detail": "Local internal endpoint"}, status_code=403)
        if not token or access.managed:
            host = request.headers.get("host", "").split(":")[0]
            allowed = {"localhost", "127.0.0.1", "[", "testserver"}
            if os.environ.get("DOXA_PUBLIC_ORIGIN"):
                allowed.add(urlsplit(os.environ["DOXA_PUBLIC_ORIGIN"]).hostname or "")
            if host not in allowed:
                return JSONResponse({"detail": "Untrusted host"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception as error:
            # Export libraries can wrap an underlying ENOSPC in their own
            # exception. Inspect its cause chain without exposing paths or
            # translating unrelated filesystem/database failures.
            cause, seen, exhausted = error, set(), False
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:
                    exhausted = True
                    break
                if isinstance(cause, sqlite3.OperationalError) and (
                    getattr(cause, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
                    or str(cause).casefold().startswith("database or disk is full")
                ):
                    exhausted = True
                    break
                cause = cause.__cause__ or cause.__context__
            if not exhausted:
                raise
            response = JSONResponse({
                "code": "storage_exhausted",
                "detail": "Workspace storage is full. Free space on the data and temporary volumes, then retry. Preserve original documents and checkpoints; review unused build cache and old export copies.",
            }, status_code=507)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "mode": "cloud" if db().remote else "local",
                "capabilities": {"multipart_uploads": True, "full_text_search": db().fts,
                                 "private_local": not bool(db().remote), "semantic_validation": "research_preview"}}

    @app.get("/api/projects")
    def projects():
        principal = current_principal()
        if principal.managed and not principal.all_projects:
            return db().query("SELECT * FROM projects WHERE id IN (SELECT value FROM json_each(?)) ORDER BY created_at", [json.dumps(principal.project_ids)])
        return db().query("SELECT * FROM projects ORDER BY created_at")

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        project_id = uid()
        db().execute("INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)",
                     [project_id, body.name.strip(), body.description, utcnow()])
        return require_project(project_id)

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str, q: str = "", offset: int = 0, limit: int = 2000):
        require_project(project_id)
        if not 0 <= offset or not 1 <= limit <= 2000:
            raise HTTPException(422, "Invalid pagination")
        if q and db().fts:
            # Quote user words: FTS syntax is not exposed as executable SQL or unchecked MATCH grammar.
            terms = re.findall(r"\w+", q)[:20]
            if not terms:
                return []
            clauses = ["d.project_id=?"]
            params = [project_id]
            for term in terms:
                clauses.append("d.id IN (SELECT id FROM document_search WHERE project_id=? AND document_search MATCH ?)")
                params.extend([project_id, '"' + term.replace('"', '""') + '"'])
            rows = db().query("SELECT d.* FROM documents d WHERE " + " AND ".join(clauses) + " ORDER BY d.created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset])
        else:
            rows = db().query("SELECT * FROM documents WHERE project_id=? AND (?='' OR title LIKE ? OR author LIKE ?) ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              [project_id, q, f"%{q}%", f"%{q}%", limit, offset])
        return [unpack(row) for row in rows]

    @app.get("/api/documents/{document_id}")
    def document_detail(document_id: str):
        return require_document(document_id, True)

    @app.get("/api/documents/{document_id}/source")
    def document_source(document_id: str):
        doc = require_document(document_id)
        if not doc["object_key"]:
            raise HTTPException(404, "This showcase provides attributed text snapshots; the original is linked by source_url")
        target = db().root / "downloads" / document_id / Path(doc["filename"]).name
        db().download(doc["object_key"], target)
        return FileResponse(target, filename=doc["filename"], media_type=doc["mime_type"])

    @app.get("/api/documents/{document_id}/extraction")
    def extraction_details(document_id: str, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 200:
            raise HTTPException(422, "Invalid extraction page range")
        doc = require_document(document_id)
        manifest = doc.get("metadata", {}).get("extraction_manifest")
        if not manifest:
            return {"document_id": document_id, "status": "not_recorded", "pages": [], "total_pages": None}
        if manifest.get("manifest_key"):
            raw = db().get_bytes(manifest["manifest_key"])
            if hashlib.sha256(raw).hexdigest() != manifest["manifest_sha256"]:
                raise HTTPException(409, "Extraction manifest integrity check failed")
            manifest = json.loads(raw)
        pages = manifest.get("pages", [])
        return {**{key: value for key, value in manifest.items() if key != "pages"}, "document_id": document_id,
                "pages": pages[offset:offset + limit], "total_pages": len(pages), "next_offset": offset + limit if offset + limit < len(pages) else None}

    @app.patch("/api/documents/{document_id}")
    def update_document(document_id: str, body: dict):
        document = require_document(document_id)
        if document.get("metadata", {}).get("privacy_transform_id"):
            raise HTTPException(409, "Transformed source metadata is frozen. Edit the authorized original and create a new privacy version instead.")
        editable = {"title", "author", "institution", "document_type", "published_at"}
        updates = {key: value for key, value in body.items() if key in editable}
        if any(value is not None and (not isinstance(value, str) or len(value) > 2000) for value in updates.values()):
            raise HTTPException(422, "Metadata must be text of at most 2000 characters")
        if updates:
            statements = [{"sql": "UPDATE documents SET " + ",".join(key + "=?" for key in updates) + " WHERE id=?", "params": [*updates.values(), document_id]}]
            if "title" in updates and db().fts:
                statements.append({"sql": "UPDATE document_search SET title=? WHERE id=?", "params": [updates["title"], document_id]})
            from doxa.workspace import Workspace
            db().batch([*statements, Workspace.touch_statement(document["project_id"])])
        return require_document(document_id)

    @app.post("/api/projects/{project_id}/metadata/import")
    async def import_metadata(project_id: str, file: UploadFile = File(...)):
        import csv
        import io
        require_project(project_id)
        raw = await file.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, "Metadata CSV must be at most 2 MiB")
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            if not reader.fieldnames or not {"id", "filename", "sha256"}.intersection(reader.fieldnames):
                raise ValueError("Include an id, filename, or sha256 column for matching documents")
            rows = list(reader)
            if len(rows) > 1000:
                raise ValueError("Import at most 1000 metadata rows at a time")
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        updated, unmatched, errors = 0, [], []
        for number, row in enumerate(rows, 2):
            key = next((key for key in ["id", "sha256", "filename"] if row.get(key)), None)
            matches = db().query(f"SELECT id FROM documents WHERE project_id=? AND {key}=?", [project_id, row[key]]) if key else []
            if len(matches) != 1:
                unmatched.append({"row": number, "reason": "No unique matching document"})
                continue
            values = {key: value for key, value in row.items() if key in {"title", "author", "institution", "document_type", "published_at"} and value is not None and value != ""}
            try:
                update_document(matches[0]["id"], values)
                updated += 1
            except HTTPException as exc:
                errors.append({"row": number, "reason": exc.detail})
        return {"updated": updated, "unmatched": unmatched, "errors": errors}

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: str):
        doc = require_document(document_id)
        active = db().one("SELECT t.id FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.document_id=? AND r.status IN ('queued','running') LIMIT 1", [document_id])
        if active:
            raise HTTPException(409, "Cancel active analysis before deleting its source")
        statements = [{"sql": "DELETE FROM reviews WHERE finding_id IN (SELECT id FROM findings WHERE document_id=?)", "params": [document_id]},
                       {"sql": "DELETE FROM findings WHERE document_id=?", "params": [document_id]},
                       {"sql": "DELETE FROM batch_members WHERE document_id=?", "params": [document_id]},
                       {"sql": "DELETE FROM tasks WHERE document_id=?", "params": [document_id]},
                       {"sql": "DELETE FROM import_commits WHERE document_id=?", "params": [document_id]},
                       {"sql": "DELETE FROM documents WHERE id=?", "params": [document_id]}]
        if db().fts:
            statements.append({"sql": "DELETE FROM document_search WHERE id=?", "params": [document_id]})
        from doxa.workspace import Workspace
        db().batch([*statements, Workspace.touch_statement(doc["project_id"])])
        for key in [doc.get("object_key"), doc.get("blocks_key"), doc.get("metadata", {}).get("extraction_manifest", {}).get("manifest_key")]:
            if key:
                shared = db().one("SELECT id FROM documents WHERE object_key=? OR blocks_key=? LIMIT 1", [key, key])
                if not shared:
                    db().delete_object(key)
        return {"ok": True, "historical_reports": "Saved report snapshots remain immutable until their project is deleted."}

    def new_batch(project_id: str, name: str, batch_id: str | None = None) -> dict:
        batch = {"id": batch_id or uid(), "project_id": project_id, "name": name, "created_at": utcnow()}
        db().execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", list(batch.values()))
        return db().one("SELECT * FROM batches WHERE id=? AND project_id=?", [batch["id"], project_id])

    def save_document(project_id: str, batch_id: str, data: dict, *, object_key: str | None = None, document_id: str | None = None) -> dict:
        metadata = dict(data.get("metadata") or {})
        digest = data.get("sha256") or data.get("content_sha256") or hashlib.sha256(json.dumps(data.get("blocks", []), ensure_ascii=False).encode()).hexdigest()
        identity = metadata.get("source_identity") or data.get("source_url") or data.get("filename") or data.get("id") or "@content"
        doc_id = document_id or hashlib.sha256(json.dumps([project_id, identity, digest]).encode()).hexdigest()[:32]
        blocks = data.get("blocks", [])
        blocks_key = f"documents/{doc_id}/blocks.json"
        if not db().remote:
            from doxa.privacy_lifecycle import track_artifact
            track_artifact(db(), project_id, f"objects/documents/{doc_id}", "source_units")
        db().put_json(blocks_key, blocks)
        for key in ["extraction_warnings", "extraction_manifest", "extractor", "extractor_version", "source_sha256", "content_sha256", "rights", "notices", "excluded_blocks", "source_id", "attribution", "license_url", "rights_source_url", "rights_notes", "rights_review", "coverage", "synthetic", "language"]:
            if key in data:
                metadata[key] = data[key]
        manifest = metadata.get("extraction_manifest")
        if isinstance(manifest, dict):
            encoded_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            manifest_key = f"documents/{doc_id}/extraction-manifest.json"
            db().put_bytes(manifest_key, encoded_manifest, "application/json")
            metadata["extraction_manifest"] = {**{key: value for key, value in manifest.items() if key not in {"pages", "exclusions"}},
                "pages_count": len(manifest.get("pages", [])), "manifest_key": manifest_key,
                "exclusions_preview": manifest.get("exclusions", [])[:20], "exclusions_count": len(manifest.get("exclusions", [])),
                "manifest_sha256": hashlib.sha256(encoded_manifest).hexdigest(), "page_details": "stored_in_manifest_artifact"}
            metadata.pop("page_manifest", None)
        values = {"id": doc_id, "project_id": project_id, "batch_id": batch_id,
                  "title": data.get("title") or data.get("filename") or "Untitled document",
                  "author": data.get("author") or "Unknown", "institution": data.get("institution") or "",
                  "document_type": data.get("document_type") or "document", "published_at": data.get("published_at"),
                  "filename": data.get("filename") or f"{data.get('id', doc_id)}.txt", "mime_type": data.get("mime_type") or "text/plain",
                  "sha256": digest, "status": data.get("status", "ready"), "word_count": data.get("word_count") or sum(len(b["text"].split()) for b in blocks),
                  "page_count": data.get("page_count") or 0, "source_url": data.get("source_url", ""),
                  "license": data.get("license", ""), "object_key": object_key, "blocks_key": blocks_key,
                  "created_at": utcnow(), "error": data.get("error"), "metadata_json": json.dumps(metadata, ensure_ascii=False)}
        from doxa.imports import commit_document
        doc_id = commit_document(db(), values, blocks, identity)
        return require_document(doc_id)

    async def ingest(project_id: str, batch: dict, path: Path, filename: str, existing_key: str | None = None, source_identity: str | None = None) -> tuple[dict, bool]:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        digest_string = digest.hexdigest()
        if source_identity:
            duplicate = db().one("SELECT d.* FROM documents d LEFT JOIN v3_sources s ON s.document_id=d.id WHERE d.project_id=? AND d.sha256=? AND (s.source_identity=? OR json_extract(d.metadata_json,'$.source_identity')=?)", [project_id, digest_string, source_identity, source_identity])
        else:
            duplicate = db().one("SELECT * FROM documents WHERE project_id=? AND sha256=?", [project_id, digest_string])
        commit_identity = source_identity or "@content"
        commit = db().one("SELECT * FROM import_commits WHERE project_id=? AND source_identity=? AND sha256=?", [project_id, commit_identity, digest_string])
        if duplicate and duplicate["status"] == "ready":
            from doxa.imports import commit_document
            from doxa.workspace import Workspace
            if not commit or commit["status"] != "ready":
                # Repair legacy/interrupted derived state using its original
                # extraction, preserving all historical evidence offsets.
                commit_document(db(), {**duplicate, "batch_id": batch["id"]}, db().get_json(duplicate["blocks_key"]), commit_identity)
            else:
                db().batch([{"sql": "INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", "params": [batch["id"], duplicate["id"]]}, Workspace.touch_statement(project_id)])
            if existing_key and existing_key != duplicate.get("object_key"):
                db().delete_object(existing_key)
            return require_document(duplicate["id"]), True
        key = (duplicate or {}).get("object_key") or existing_key or f"originals/{project_id}/{hashlib.sha256((commit_identity + digest_string).encode()).hexdigest()}/{Path(filename).name}"
        if not existing_key:
            if not db().remote:
                from doxa.privacy_lifecycle import track_artifact
                track_artifact(db(), project_id, "objects/" + key, "original")
            await project_thread(project_id, "source_copy", db().put_path, key, path)
        extracted: dict = {}
        error = None
        try:
            async with extraction_slots:
                extracted = await project_thread(project_id, "source_extract", extract_path, path, filename)
        except subprocess.TimeoutExpired:
            error = "Extraction exceeded the 180-second task limit. Install the OCR extra or split this document into smaller parts."
        except Exception as exc:
            error = str(exc)[:600]
        extracted.update({"filename": filename, "sha256": digest_string, "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                          "title": extracted.get("metadata", {}).get("title") or Path(filename).stem,
                          "author": extracted.get("metadata", {}).get("author") or "Unknown",
                          "status": "failed" if error else "ready", "error": error})
        extracted.setdefault("metadata", {}).update({"source_identity": commit_identity, "relative_path": source_identity or filename})
        return save_document(project_id, batch["id"], extracted, object_key=key, document_id=(duplicate or {}).get("id")), False

    @app.post("/api/documents/{document_id}/re-extract", status_code=201)
    async def reextract_document(document_id: str):
        original = require_document(document_id)
        if not original.get("object_key"):
            raise HTTPException(422, "This source has no retained original file; import its original before re-extracting")
        from doxa.pilot_api import project_work_directory
        with tempfile.TemporaryDirectory(dir=project_work_directory(db(), original["project_id"])) as working:
            path = Path(working) / ("source" + Path(original["filename"]).suffix)
            await project_thread(original["project_id"], "source_download", db().download, original["object_key"], path)
            raw_hash = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    raw_hash.update(chunk)
            raw_sha = raw_hash.hexdigest()
            metadata = original.get("metadata", {})
            expected_raw = metadata.get("raw_sha256") or metadata.get("extraction_manifest", {}).get("raw_sha256") or original["sha256"]
            if raw_sha != expected_raw:
                raise HTTPException(409, "Retained original failed its source hash check")
            try:
                async with extraction_slots:
                    extracted = await project_thread(original["project_id"], "source_reextract", extract_path, path, original["filename"])
            except (ValueError, subprocess.TimeoutExpired) as error:
                raise HTTPException(422, "Re-extraction did not succeed; the original version is unchanged. " + (str(error)[:400] if isinstance(error, ValueError) else "Parser exceeded its time limit.")) from error
        manifest = extracted["extraction_manifest"]
        if manifest["raw_sha256"] != raw_sha:
            raise HTTPException(409, "Extractor manifest does not match the retained original")
        derived_sha = hashlib.sha256(json.dumps({"raw_sha256": raw_sha, "blocks_sha256": manifest["blocks_sha256"],
            "extraction_version_id": manifest["extraction_version_id"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        identity = metadata.get("source_identity") or original.get("source_url") or original["filename"]
        existing = db().one("SELECT d.id FROM documents d JOIN v3_sources s ON s.document_id=d.id WHERE d.project_id=? AND s.source_identity=? AND d.sha256=? AND d.status='ready'", [original["project_id"], identity, derived_sha])
        if existing:
            return {"document": require_document(existing["id"]), "derived_from": original["id"], "duplicate": True, "original_preserved": True}
        derived_metadata = {**metadata, **extracted.get("metadata", {}), "source_identity": identity, "raw_sha256": raw_sha,
            "source_sha256": raw_sha, "derived_from_document_id": original["id"], "extraction_version_id": manifest["extraction_version_id"],
            "change_kind": "extraction_revision", "hash_basis": "raw source plus immutable extraction manifest and block digest"}
        extracted.update({key: original.get(key) for key in ("title", "author", "institution", "document_type", "published_at", "filename", "mime_type", "source_url", "license")})
        extracted.update({"sha256": derived_sha, "status": "ready", "metadata": derived_metadata})
        batch_id = hashlib.sha256(f"reextract:{original['project_id']}:{identity}:{derived_sha}".encode()).hexdigest()[:32]
        batch = new_batch(original["project_id"], "Re-extracted source version", batch_id)
        document = save_document(original["project_id"], batch["id"], extracted, object_key=original["object_key"])
        return {"document": document, "batch": batch, "derived_from": original["id"], "duplicate": False, "original_preserved": True}

    @app.post("/api/projects/{project_id}/upload")
    async def upload(project_id: str, files: list[UploadFile] = File(...), import_id: str | None = None):
        require_project(project_id)
        if len(files) > 100:
            raise HTTPException(422, "Import at most 100 files per batch")
        session = db().one("SELECT * FROM v3_imports WHERE id=? AND project_id=?", [import_id, project_id]) if import_id else None
        if import_id and not session:
            raise HTTPException(404, "Import does not belong to this project")
        batch = db().one("SELECT * FROM batches WHERE id=?", [session["batch_id"]]) if session else new_batch(project_id, "Uploaded documents")
        results = []
        from doxa.pilot_api import project_work_directory
        with tempfile.TemporaryDirectory(dir=project_work_directory(db(), project_id)) as working:
            for file in files:
                filename = Path(file.filename or "document.txt").name
                path = Path(working) / (uid() + Path(filename).suffix)
                size = 0
                with path.open("wb") as target:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise HTTPException(413, "Each file must be at most 500 MiB")
                        target.write(chunk)
                relative = str(file.filename or filename).replace("\\", "/")[:1000]
                doc, _ = await ingest(project_id, batch, path, filename, source_identity=relative if session else None)
                results.append(doc)
                if session:
                    db().execute("INSERT INTO v3_import_files(import_id,relative_path,sha256,document_id,status,error,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(import_id,relative_path,sha256) DO UPDATE SET document_id=excluded.document_id,status=excluded.status,error=excluded.error", [import_id, relative, doc["sha256"], doc["id"], doc["status"], doc.get("error"), utcnow()])
        if session:
            db().execute("UPDATE v3_imports SET completed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='ready'),failed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='failed'),status=CASE WHEN total_files<=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=?) THEN 'completed' ELSE 'running' END WHERE id=?", [import_id, import_id, import_id, import_id])
        return results

    def upload_meta(upload_id: str) -> tuple[Path, dict]:
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
            raise HTTPException(404, "Upload not found")
        folder = db().root / "uploads" / upload_id
        saved = db().one("SELECT * FROM upload_sessions WHERE id=?", [upload_id])
        if saved:
            if saved["status"] == "aborted":
                raise HTTPException(404, "Upload not found")
            return folder, {**json.loads(saved["metadata_json"]), "status": saved["status"]}
        if not (folder / "meta.json").exists():
            raise HTTPException(404, "Upload not found")
        # Upgrade an unfinished pre-v0.4 local upload without losing its parts.
        meta = json.loads((folder / "meta.json").read_text())
        meta.setdefault("part_size", PART_SIZE)
        db().execute("INSERT INTO upload_sessions(id,project_id,metadata_json,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", [upload_id, meta["project_id"], json.dumps(meta), meta["created_at"]])
        return folder, {**meta, "status": "pending"}

    @app.post("/api/projects/{project_id}/uploads/init")
    def init_upload(project_id: str, body: dict):
        require_project(project_id)
        import_id = body.get("import_id")
        if import_id and not db().one("SELECT id FROM v3_imports WHERE id=? AND project_id=?", [import_id, project_id]):
            raise HTTPException(404, "Import does not belong to this project")
        size = body.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE:
            raise HTTPException(422, "File size must be between 1 byte and 500 MiB")
        upload_id = uid()
        folder = db().root / "uploads" / upload_id
        folder.mkdir(parents=True)
        meta = {"id": upload_id, "project_id": project_id, "filename": Path(str(body.get("filename", "document.txt"))).name,
                "size": size, "created_at": utcnow(), "import_id": import_id, "part_size": PART_SIZE,
                "batch_id": (db().one("SELECT batch_id FROM v3_imports WHERE id=?", [import_id]) or {}).get("batch_id") if import_id else hashlib.sha256(f"upload:{upload_id}".encode()).hexdigest()[:32]}
        (folder / "meta.json").write_text(json.dumps(meta))
        db().execute("INSERT INTO upload_sessions(id,project_id,metadata_json,created_at) VALUES(?,?,?,?)", [upload_id, project_id, json.dumps(meta), meta["created_at"]])
        return {"id": upload_id, "upload_id": upload_id, "part_size": PART_SIZE}

    @app.put("/api/uploads/{upload_id}/parts/{part_number}")
    async def upload_part(upload_id: str, part_number: int, request: Request):
        folder, meta = upload_meta(upload_id)
        if meta["status"] != "pending":
            raise HTTPException(409, "Upload has already started completion")
        part_size = meta["part_size"]
        expected_parts = (meta["size"] + part_size - 1) // part_size
        if not 1 <= part_number <= expected_parts:
            raise HTTPException(422, "Invalid part number")
        digest = hashlib.sha256()
        size = 0
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=f"part-{part_number}-", delete=False) as output:
            path = Path(output.name)
            async for chunk in request.stream():
                size += len(chunk)
                if size > part_size:
                    path.unlink(missing_ok=True)
                    raise HTTPException(413, "Part exceeds 8 MiB")
                digest.update(chunk)
                output.write(chunk)
        expected_size = min(part_size, meta["size"] - (part_number - 1) * part_size)
        if size != expected_size:
            path.unlink(missing_ok=True)
            raise HTTPException(422, "Part size does not match the declared file size")
        etag = digest.hexdigest()
        key = f"uploads/{upload_id}/parts/{part_number}/{etag}"
        try:
            previous = db().one("SELECT sha256 FROM upload_parts WHERE upload_id=? AND part_number=?", [upload_id, part_number])
            if previous and previous["sha256"] != etag:
                raise HTTPException(409, "This part is already committed with different bytes")
            await project_thread(meta["project_id"], "upload_part_copy", db().put_path, key, path)
            db().execute("INSERT INTO upload_parts(upload_id,part_number,sha256,object_key,size) SELECT ?,?,?,?,? WHERE EXISTS(SELECT 1 FROM upload_sessions WHERE id=? AND status='pending') ON CONFLICT(upload_id,part_number) DO NOTHING", [upload_id, part_number, etag, key, size, upload_id])
            saved = db().one("SELECT sha256 FROM upload_parts WHERE upload_id=? AND part_number=?", [upload_id, part_number])
            if not saved or saved["sha256"] != etag:
                db().delete_object(key)
                raise HTTPException(409, "This part is already committed with different bytes or completion has started")
        finally:
            path.unlink(missing_ok=True)
        return {"partNumber": part_number, "etag": etag}

    @app.post("/api/uploads/{upload_id}/complete")
    async def finish_upload(upload_id: str, body: dict):
        folder, meta = upload_meta(upload_id)
        count = (meta["size"] + meta["part_size"] - 1) // meta["part_size"]
        parts = body.get("parts", [])
        if not isinstance(parts, list) or len(parts) != count:
            raise HTTPException(422, "All parts must be included")
        if any(not isinstance(part, dict) or isinstance(part.get("partNumber"), bool)
               or not isinstance(part.get("partNumber"), int) or not isinstance(part.get("etag"), str)
               or not re.fullmatch(r"[a-f0-9]{64}", part["etag"]) for part in parts):
            raise HTTPException(422, "Each part requires an integer number and SHA-256 checksum")
        provided = {p.get("partNumber"): p.get("etag") for p in parts if isinstance(p, dict)}
        if len(provided) != count or set(provided) != set(range(1, count + 1)):
            raise HTTPException(422, "All distinct parts must be included")
        parts_digest = hashlib.sha256(json.dumps(provided, sort_keys=True).encode()).hexdigest()
        terminal = db().one("SELECT result_json FROM upload_sessions WHERE id=? AND status='completed'", [upload_id])
        if terminal:
            saved = json.loads(terminal["result_json"])
            if saved["parts_sha256"] != parts_digest:
                raise HTTPException(422, "Completed upload has a different part manifest")
            return saved["result"]
        committed = []
        for number in range(1, count + 1):
            part = db().one("SELECT * FROM upload_parts WHERE upload_id=? AND part_number=?", [upload_id, number])
            if not part and (folder / f"part-{number}").exists() and (folder / f"etag-{number}").exists():
                digest = hashlib.sha256((folder / f"part-{number}").read_bytes()).hexdigest()
                key = f"uploads/{upload_id}/parts/{number}/{digest}"
                db().put_path(key, folder / f"part-{number}")
                db().execute("INSERT INTO upload_parts(upload_id,part_number,sha256,object_key,size) VALUES(?,?,?,?,?) ON CONFLICT(upload_id,part_number) DO NOTHING", [upload_id, number, digest, key, (folder / f"part-{number}").stat().st_size])
                part = db().one("SELECT * FROM upload_parts WHERE upload_id=? AND part_number=?", [upload_id, number])
            if not part or provided.get(number) != part["sha256"]:
                raise HTTPException(422, "Missing part or checksum mismatch")
            committed.append(part)
        completion_token = uid()
        claimed = db().query("UPDATE upload_sessions SET status='completing',metadata_json=json_set(metadata_json,'$.completion_lease_until',?,'$.completion_token',?) WHERE id=? AND (status='pending' OR (status='completing' AND COALESCE(json_extract(metadata_json,'$.completion_lease_until'),0)<?)) RETURNING id", [time.time() + 300, completion_token, upload_id, time.time()])
        if not claimed:
            raise HTTPException(409, "Upload completion is already in progress; retry after it settles")
        try:
            return await assemble_upload(upload_id, folder, meta, committed, parts_digest, completion_token)
        finally:
            # A handled error is retryable immediately; process death leaves a
            # bounded lease instead of allowing concurrent assembly/cleanup.
            db().execute("UPDATE upload_sessions SET metadata_json=json_set(metadata_json,'$.completion_lease_until',0) WHERE id=? AND status='completing' AND json_extract(metadata_json,'$.completion_token')=?", [upload_id, completion_token])

    async def assemble_upload(upload_id, folder, meta, committed, parts_digest, completion_token):
        import shutil
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, suffix=Path(meta["filename"]).suffix, delete=False) as output:
            assembled = Path(output.name)
            for part in committed:
                source_path = folder / f"read-{uid()}"
                db().download(part["object_key"], source_path)
                with source_path.open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                source_path.unlink()
        session = db().one("SELECT * FROM v3_imports WHERE id=? AND project_id=?", [meta.get("import_id"), meta["project_id"]]) if meta.get("import_id") else None
        batch = db().one("SELECT * FROM batches WHERE id=?", [session["batch_id"]]) if session else new_batch(meta["project_id"], "Resumable upload", meta.get("batch_id") or hashlib.sha256(f"upload:{upload_id}".encode()).hexdigest()[:32])
        doc, duplicate = await ingest(meta["project_id"], batch, assembled, meta["filename"], source_identity=meta["filename"] if session else None)
        if session:
            db().execute("INSERT INTO v3_import_files(import_id,relative_path,sha256,document_id,status,error,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(import_id,relative_path,sha256) DO UPDATE SET document_id=excluded.document_id,status=excluded.status,error=excluded.error", [session["id"], meta["filename"], doc["sha256"], doc["id"], doc["status"], doc.get("error"), utcnow()])
            db().execute("UPDATE v3_imports SET completed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='ready'),failed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='failed'),status=CASE WHEN total_files<=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=?) THEN 'completed' ELSE 'running' END WHERE id=?", [session["id"], session["id"], session["id"], session["id"]])
        result = {"document": doc, "batch": batch, "duplicate": duplicate}
        db().execute("UPDATE upload_sessions SET status='completed',result_json=? WHERE id=? AND status='completing' AND json_extract(metadata_json,'$.completion_token')=?", [json.dumps({"parts_sha256": parts_digest, "result": result}), upload_id, completion_token])
        saved = db().one("SELECT result_json FROM upload_sessions WHERE id=?", [upload_id])["result_json"]
        if not saved:
            raise HTTPException(409, "Upload completion lease changed; retry the persisted session")
        result = json.loads(saved)["result"]
        assembled.unlink(missing_ok=True)
        # Persisted result survives cleanup, restart and a lost HTTP response.
        for part in committed:
            try:
                db().delete_object(part["object_key"])
            except OSError:
                pass  # terminal result is durable; tracked parts can be cleaned later
        shutil.rmtree(folder, ignore_errors=True)
        return result

    @app.delete("/api/uploads/{upload_id}")
    def abort_upload(upload_id: str):
        import shutil
        folder, _ = upload_meta(upload_id)
        changed = db().query("UPDATE upload_sessions SET status='aborted' WHERE id=? AND status='pending' RETURNING id", [upload_id])
        if not changed:
            raise HTTPException(409, "Upload completion has already started")
        shutil.rmtree(folder, ignore_errors=True)
        return {"ok": True}

    @app.post("/internal/uploads/complete")
    async def cloud_upload(body: dict):
        require_project(body["project_id"])
        key = str(body["key"])
        if not key.startswith(("uploads/", "originals/")):
            raise HTTPException(422, "Invalid upload artifact")
        batch_id = hashlib.sha256(f"{body['project_id']}:{key}".encode()).hexdigest()[:32]
        batch = new_batch(body["project_id"], "Resumable cloud upload", batch_id)
        from doxa.pilot_api import project_work_directory
        with tempfile.TemporaryDirectory(dir=project_work_directory(db(), body["project_id"])) as working:
            path = Path(working) / (uid() + Path(body["filename"]).suffix)
            await project_thread(body["project_id"], "upload_source_download", db().download, key, path)
            if path.stat().st_size != body["size"] or path.stat().st_size > MAX_FILE:
                raise HTTPException(422, "Upload size mismatch")
            doc, duplicate = await ingest(body["project_id"], batch, path, body["filename"], existing_key=key)
        return {"document": doc, "batch": batch, "duplicate": duplicate}

    @app.get("/api/lenses")
    def lenses():
        from doxa.lenses import LENSES
        return list(LENSES.values()) if isinstance(LENSES, dict) else LENSES

    @app.get("/api/settings")
    def get_settings():
        return settings()

    @app.put("/api/settings")
    def save_settings(body: dict):
        allowed = {"monthly_budget_usd", "default_provider", "default_model"}
        values = {key: value for key, value in body.items() if key in allowed}
        if "monthly_budget_usd" in values and (not isinstance(values["monthly_budget_usd"], (int, float)) or not math.isfinite(values["monthly_budget_usd"]) or not 0 <= values["monthly_budget_usd"] <= 10000):
            raise HTTPException(422, "Monthly variable budget must be between $0 and $10,000")
        if "default_provider" in values and values["default_provider"] not in {"lexical", "ollama", "cloudflare", "openai", "runpod"}:
            raise HTTPException(422, "Unknown default provider")
        if "default_model" in values and (not isinstance(values["default_model"], str) or len(values["default_model"]) > 300):
            raise HTTPException(422, "Default model must be text of at most 300 characters")
        current = {key: settings()[key] for key in allowed}
        current.update(values)
        db().execute("INSERT INTO settings(key,value_json) VALUES('preferences',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", [json.dumps(current)])
        return settings()

    @app.post("/api/providers/health")
    async def provider_health(body: dict):
        from doxa.providers import get_provider
        try:
            # Endpoints and credentials are server configuration. A browser
            # health probe must not turn this server into an arbitrary fetcher.
            safe = RunInput.model_validate(body).model_dump(exclude_none=True)
            if not db().remote:
                from doxa.pilot_commerce import apply_server_pricing
                safe = apply_server_pricing(safe)
            provider = get_provider(safe)
            if asyncio.iscoroutine(provider):
                provider = await provider
            return await provider.health()
        except Exception as exc:
            return {"ok": False, "provider": body.get("provider"), "details": str(exc)[:500]}

    def settle_reservation(reservation_id: str, item_usage: dict) -> None:
        from contextlib import nullcontext
        from doxa.pilot_commerce import settle_usage, usd_to_microusd
        reservation = db().one("SELECT * FROM budget_reservations WHERE id=? AND status='reserved'", [reservation_id])
        if not reservation:
            return
        actual = item_usage.get("cost_usd")
        charged = float(actual) if actual is not None else reservation["amount"]
        if not math.isfinite(charged) or charged < 0:
            charged = reservation["amount"]
            item_usage = {**item_usage, "cost_usd": None, "estimated": True}
        # One transaction settles both ledgers exactly once. changes() refers
        # to the preceding statement in this SQLite/D1 transaction.
        # Dedicated-pilot usage and the provider ledger commit together locally.
        # Cloudflare's existing remote profile has no native pilot contracts.
        with db().transaction(write=True) if not db().remote else nullcontext():
            db().batch([
                {"sql": "UPDATE budget_reservations SET status='settled',charged_usd=?,usage_json=?,settled_at=? WHERE id=? AND status='reserved' RETURNING id", "params": [charged, json.dumps(item_usage), utcnow(), reservation_id]},
                {"sql": "UPDATE budget_ledger SET reserved_usd=MAX(0,reserved_usd-?),spent_usd=spent_usd+? WHERE month=? AND changes()=1 RETURNING month", "params": [reservation["amount"], charged, reservation["month"]]},
                {"sql": "UPDATE runs SET reserved_usd=MAX(0,reserved_usd-?),spent_usd=spent_usd+? WHERE id=? AND changes()=1", "params": [reservation["amount"], charged, reservation["run_id"]]},
            ])
            if not db().remote and db().one("SELECT name FROM sqlite_master WHERE type='table' AND name='pilot_usage_events'"):
                quote_event = db().one("SELECT event_key,project_id FROM pilot_usage_events WHERE event_key=? AND project_id=(SELECT project_id FROM runs WHERE id=?)", ["budget:" + reservation_id, reservation["run_id"]])
                if quote_event and not db().one("SELECT id FROM privacy_erase_jobs WHERE project_id=? LIMIT 1", [quote_event["project_id"]]):
                    settle_usage(db(), quote_event["project_id"], quote_event["event_key"], actual_microusd=usd_to_microusd(charged))

    def refresh_run(run_id: str) -> dict:
        run = require_run(run_id)
        rows = db().query("SELECT document_id,status,start_block,end_block,coverage_json,usage_json,error FROM tasks WHERE run_id=?", [run_id])
        docs = defaultdict(list)
        usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0, "estimated": False}
        errors = []
        blocks_total = sum(row["end_block"] - row["start_block"] for row in rows)
        blocks_done = 0
        for row in rows:
            docs[row["document_id"]].append(row["status"])
            if row["status"] in {"completed", "failed", "pending"}:
                coverage = json.loads(row["coverage_json"] or "{}")
                blocks_done += int(coverage.get("processed_blocks", row["end_block"] - row["start_block"] if row["status"] == "completed" else 0))
            item_usage = json.loads(row["usage_json"] or "{}")
            for key in ["input_tokens", "output_tokens", "requests"]:
                usage[key] += item_usage.get(key, 0) or 0
            if item_usage.get("cost_usd") is None and item_usage:
                usage["cost_usd"] = None
            elif usage["cost_usd"] is not None:
                usage["cost_usd"] += item_usage.get("cost_usd", 0) or 0
            usage["estimated"] = usage["estimated"] or bool(item_usage.get("estimated"))
            message = (row["error"] or "").strip()
            if message or row["status"] == "failed":
                errors.append({"document_id": row["document_id"],
                               "message": message or "Task failed without a stored error message"})
        reservations = db().query("SELECT status,usage_json FROM budget_reservations WHERE run_id=?", [run_id])
        if reservations:
            # Durable request usage includes earlier attempts and uncertain
            # requests; task result replacement on resume cannot erase spend.
            usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0, "estimated": False}
            for reservation in reservations:
                item_usage = json.loads(reservation["usage_json"] or "{}")
                usage["requests"] += 1
                for key in ["input_tokens", "output_tokens"]:
                    usage[key] += item_usage.get(key, 0) or 0
                if item_usage.get("cost_usd") is None:
                    usage["cost_usd"] = None
                elif usage["cost_usd"] is not None:
                    usage["cost_usd"] += item_usage["cost_usd"]
                usage["estimated"] = usage["estimated"] or bool(item_usage.get("estimated", True))
        config = run.get("config", {})
        lexical = config.get("provider") == "lexical"
        tasks_complete = all(row["status"] == "completed" for row in rows)
        unit_coverage_complete = True
        if str(config.get("methodology_version", "1")).startswith("3") and not lexical:
            units = db().one("SELECT COUNT(*) total,SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) accepted FROM v3_unit_statuses WHERE run_id=?", [run_id])
            unit_coverage_complete = bool(units["total"]) and units["accepted"] == units["total"]
            if config.get("task_index_basis") == "source_units_v3":
                unit_coverage_complete = unit_coverage_complete and units["total"] == blocks_total
        complete = tasks_complete and blocks_done == blocks_total and unit_coverage_complete and not errors
        progress = {"documents_total": len(docs), "documents_done": sum(all(s in {"completed", "failed", "cancelled"} for s in states) for states in docs.values()),
                    "documents_complete": sum(all(s == "completed" for s in states) for states in docs.values()),
                    "documents_settled": sum(all(s in {"completed", "failed", "cancelled"} for s in states) for states in docs.values()),
                    "blocks_total": blocks_total, "blocks_done": blocks_done,
                    "tasks_total": len(rows), "tasks_done": sum(row["status"] == "completed" for row in rows),
                    "tasks_failed": sum(row["status"] == "failed" for row in rows),
                    "current_document": run.get("progress", {}).get("current_document", ""),
                    "complete": complete,
                    "analysis_kind": "lexical_scan" if lexical else "model_analysis",
                    "semantic_analysis_performed": not lexical,
                    "semantic_blocks_done": 0 if lexical else blocks_done,
                    "lexical_blocks_done": blocks_done if lexical else 0}
        pending = any(row["status"] in {"pending", "running"} for row in rows)
        status = run["status"]
        if run["cancel_requested"]:
            status = "cancelled"
        elif not pending and not status.startswith("paused"):
            status = "completed" if complete else "partial"
        completed_at = (run.get("completed_at") or utcnow()) if status in TERMINAL else None
        if any([run.get("progress") != progress, run.get("usage") != usage, run.get("errors") != errors,
                run["status"] != status, run.get("completed_at") != completed_at]):
            from doxa.workspace import Workspace
            db().batch([{"sql": "UPDATE runs SET progress_json=?,usage_json=?,errors_json=?,status=?,completed_at=? WHERE id=?", "params": [json.dumps(progress), json.dumps(usage), json.dumps(errors), status, completed_at, run_id]}, Workspace.touch_statement(run["project_id"])])
        return require_run(run_id)

    async def _work_once(run_id: str) -> dict:
        from doxa.analysis import analyze_document
        from doxa.providers import get_provider
        run = require_run(run_id)
        if run["status"] in TERMINAL or run["status"].startswith("paused"):
            return {"pending": False, "run": run}
        if run["cancel_requested"]:
            return {"pending": False, "run": refresh_run(run_id)}
        limits = task_execution_limits(run["config"].get("provider", "lexical"), cloud_state=bool(db().remote))
        now = time.time()
        expired = db().query("SELECT id FROM tasks WHERE run_id=? AND status='running' AND (lease_until IS NULL OR lease_until<?)", [run_id, now])
        for expired_task in expired:
            for reservation in db().query("SELECT id FROM budget_reservations WHERE task_id=? AND status='reserved'", [expired_task["id"]]):
                settle_reservation(reservation["id"], {"cost_usd": None, "estimated": True, "recovered_after_crash": True})
        db().execute("UPDATE tasks SET status='pending',lease_token=NULL,lease_until=NULL WHERE run_id=? AND status='running' AND (lease_until IS NULL OR lease_until<?)", [run_id, now])
        task = db().one("SELECT * FROM tasks WHERE run_id=? AND status='pending' ORDER BY ordinal,id LIMIT 1", [run_id])
        if not task:
            running = db().one("SELECT id FROM tasks WHERE run_id=? AND status='running' LIMIT 1", [run_id])
            return {"pending": bool(running), "idle": True, "retry_after": 1, "run": refresh_run(run_id)}
        token = uid()
        claimed = db().query("UPDATE tasks SET status='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND status='pending' RETURNING id", [token, now+limits["task_lease_seconds"], task["id"]])
        if not claimed:
            return {"pending": True, "run": require_run(run_id)}
        document = require_document(task["document_id"], True)
        if str(run["config"].get("methodology_version", "1")).startswith("3") and run["config"].get("provider") != "lexical":
            from doxa.method_config import frozen_source_metadata
            try:
                document = {**document, **frozen_source_metadata(db(), run, document["id"])}
            except (ValueError, OSError) as error:
                db().execute("UPDATE tasks SET status='failed',error=? WHERE id=? AND lease_token=?", [str(error)[:1000], task["id"], token])
                return {"pending": True, "run": refresh_run(run_id)}
        complete_document = document
        document = {**document, "blocks": document["blocks"][task["start_block"]:task["end_block"]]} if run["config"].get("task_index_basis") != "source_units_v3" else document
        db().execute("UPDATE runs SET status='running',started_at=COALESCE(started_at,?),progress_json=? WHERE id=?", [utcnow(), json.dumps({**run["progress"], "current_document": document["title"]}), run_id])
        config = dict(run["config"])
        if not db().remote:
            from doxa.pilot_commerce import CommerceError, apply_server_pricing
            try:
                config = apply_server_pricing(config)
            except CommerceError as error:
                db().execute("UPDATE tasks SET status='pending',error=?,lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?", [str(error), task["id"], token])
                db().execute("UPDATE runs SET status='paused_budget' WHERE id=?", [run_id])
                return {"pending": False, "run": require_run(run_id)}
        operational_profile = {**run.get("model_profile", {}), **limits}
        if config.get("provider") == "ollama" and not db().remote:
            if any(config.get(key) != value for key, value in limits.items()):
                config.setdefault("task_timing_history", []).append({"applied_at": utcnow(), "task_id": task["id"],
                                                                     "reason": "worker_claim", **limits})
            config.update(limits)
            db().execute("UPDATE runs SET config_json=?,model_profile_json=? WHERE id=? AND cancel_requested=0",
                         [json.dumps(config), json.dumps(operational_profile), run_id])
        else:
            db().execute("UPDATE runs SET model_profile_json=? WHERE id=? AND cancel_requested=0",
                         [json.dumps(operational_profile), run_id])
        if str(config.get("methodology_version", "1")).startswith("3") and config.get("provider") != "lexical":
            from doxa.contextual import PROMPT_VERSION
            method_changed = bool(config.get("pipeline_version") and config["pipeline_version"] != PROMPT_VERSION)
            snapshots = config.get("lens_snapshots")
            if snapshots:
                from doxa.lenses import get_lens, validate_custom_lens
                try:
                    current_packs = [validate_custom_lens(config["custom_lens"]) if pack["id"] == "custom" else get_lens(pack["id"])
                                     for pack in snapshots]
                    snapshot_hash = hashlib.sha256(json.dumps(snapshots, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    method_changed = method_changed or snapshots != current_packs or (
                        bool(config.get("lens_snapshots_sha256")) and config["lens_snapshots_sha256"] != snapshot_hash)
                except (KeyError, TypeError, ValueError):
                    method_changed = True
            if method_changed:
                db().execute("UPDATE runs SET status='paused_method_changed' WHERE id=?", [run_id])
                db().execute("UPDATE tasks SET status='pending',lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?", [task["id"], token])
                return {"pending": False, "run": require_run(run_id)}
        reservations: list[str] = []
        month = utcnow()[:7]
        db().execute("INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES(?,0,0) ON CONFLICT(month) DO NOTHING", [month])

        async def reserve(amount: float) -> bool:
            from contextlib import nullcontext
            from doxa.pilot_api import enforce_analysis_policy
            from doxa.access import WorkerAuthorizationError
            from doxa.pilot_commerce import CommerceError, apply_server_pricing, quote_required, reserve_usage, usd_to_microusd
            try:
                access.require_worker(config.get("execution_actor_id"), run["project_id"], "analyze")
            except WorkerAuthorizationError:
                db().execute("UPDATE runs SET status='paused_access' WHERE id=?", [run_id])
                raise
            try:
                enforce_analysis_policy(db(), run["project_id"], config, [complete_document])
            except ValueError:
                db().execute("UPDATE runs SET status='paused_privacy' WHERE id=?", [run_id])
                raise
            try:
                apply_server_pricing(config)
            except CommerceError:
                return False
            amount = max(0, float(amount))
            if not math.isfinite(amount):
                return False
            maximum = float(settings()["monthly_budget_usd"])
            reservation_id = uid()
            try:
                with db().transaction(write=True) if not db().remote else nullcontext():
                    changes = db().batch([
                        {"sql": "UPDATE budget_ledger SET reserved_usd=reserved_usd+? WHERE month=? AND reserved_usd+spent_usd+?<=? AND EXISTS(SELECT 1 FROM runs WHERE id=? AND reserved_usd+spent_usd+?<=? AND cancel_requested=0) AND EXISTS(SELECT 1 FROM tasks WHERE id=? AND lease_token=? AND status='running') RETURNING month",
                         "params": [amount, month, amount, maximum, run_id, amount, config.get("budget_usd", 2), task["id"], token]},
                        {"sql": "UPDATE runs SET reserved_usd=reserved_usd+? WHERE id=? AND changes()=1 RETURNING id", "params": [amount, run_id]},
                        {"sql": "INSERT INTO budget_reservations(id,run_id,task_id,month,amount,created_at) SELECT ?,?,?,?,?,? WHERE changes()=1 RETURNING id", "params": [reservation_id, run_id, task["id"], month, amount, utcnow()]},
                    ])
                    if changes[2]["rows"] and (config.get("quote_id") or quote_required()):
                        if db().remote or not config.get("quote_id"):
                            raise CommerceError("Paid work requires an accepted native pilot quote", 409)
                        reserve_usage(db(), run["project_id"], config["quote_id"],
                                      event_key="budget:" + reservation_id, category="inference",
                                      reserved_microusd=usd_to_microusd(amount), reference_id=task["id"])
            except CommerceError:
                # Both native ledgers roll back, before any provider request.
                return False
            if not changes[2]["rows"]:
                return False
            reservations.append(reservation_id)
            return True

        async def record(usage: dict):
            if reservations:
                reservation_id = reservations[0]
                settle_reservation(reservation_id, usage)
                reservations.pop(0)

        config["_reserve_budget"] = reserve
        config["_record_usage"] = record
        result = None
        try:
            if config.get("request_journal_mode") == "visible_io":
                if db().remote or os.environ.get("DOXA_ENABLE_PRIVATE_JOURNAL", "").lower() not in {"1", "true", "yes"}:
                    raise ValueError("Private request capture is no longer enabled by the local operator")
                from doxa.lineage import PrivateJournal
                journal_root = db().root / "private-request-journals"
                journal_root.mkdir(mode=0o700, exist_ok=True)
                os.chmod(journal_root, 0o700)
                private_sink = PrivateJournal(journal_root / run_id)
                db().execute("INSERT INTO private_journal_usage(run_id,reserved_bytes) VALUES(?,0) ON CONFLICT(run_id) DO NOTHING", [run_id])
                def record_private_io(value):
                    size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
                    if size > 4 * 1024 * 1024:
                        raise ValueError("One private request entry exceeds the 4 MiB limit")
                    reserved = db().query("UPDATE private_journal_usage SET reserved_bytes=reserved_bytes+? WHERE run_id=? AND reserved_bytes+?<=? RETURNING run_id", [size, run_id, size, config["private_journal_limit_bytes"]])
                    if not reserved:
                        raise ValueError("Private request capture reached this run's local storage quota; no entry was written")
                    try:
                        return private_sink(value)
                    except Exception:
                        db().execute("UPDATE private_journal_usage SET reserved_bytes=MAX(0,reserved_bytes-?) WHERE run_id=?", [size, run_id])
                        raise
                config["_record_private_io"] = record_private_io
            provider = get_provider(config)
            if asyncio.iscoroutine(provider):
                provider = await provider
            if config.get("provider") == "ollama" and config.get("model_revision"):
                readiness = await provider.health()
                if not readiness.get("ok"):
                    raise RuntimeError("Local provider became unavailable; source coverage remains incomplete")
                if readiness.get("revision") != config["model_revision"]:
                    db().execute("UPDATE runs SET status='paused_model_changed' WHERE id=?", [run_id])
                    raise RuntimeError("Installed model revision changed. Start a new run rather than mixing model revisions.")
            if str(config.get("methodology_version", "1")).startswith("3") and config.get("provider") != "lexical":
                from doxa.contextual import analyze_units, build_units
                from doxa.learning import select_examples
                from doxa.workspace import Workspace
                full_units = build_units(complete_document, max_unit_chars=config.get("unit_max_chars", 1200),
                                         context_sentences=config.get("context_sentences", 2), context_policy=config.get("context_policy"))
                if config.get("verification_selection"):
                    selected_units = set(config["verification_selection"]["unit_ids"].get(document["id"], []))
                    full_units = [unit for unit in full_units if unit["id"] in selected_units]
                if config.get("task_index_basis") == "source_units_v3":
                    units = full_units[task["start_block"]:task["end_block"]]
                else:
                    unit_ids = {block["id"] for block in document["blocks"]}
                    units = [unit for unit in full_units if unit["block_id"] in unit_ids]
                cached = db().query("SELECT payload_json FROM v3_assessments WHERE run_id=? AND document_id=? AND task_id=?", [run_id, document["id"], task["id"]])
                config["accepted_assessments"] = [json.loads(row["payload_json"]) for row in cached]
                if config.get("learning_snapshot_id"):
                    selected = []
                    from doxa.lenses import get_lens
                    frozen_packs = {pack["id"]: pack for pack in config.get("lens_snapshots", [])}
                    for lens_id in config["lens_ids"]:
                        pack = frozen_packs.get(lens_id) or (config.get("custom_lens") if lens_id == "custom" else get_lens(lens_id))
                        for criterion in (pack or {}).get("criteria", []):
                            selected.extend(select_examples(db(), run["project_id"], config["learning_snapshot_id"], f"{lens_id}/{criterion['id']}", exclude_document_ids=[document["id"]], source_text=" ".join(b["text"] for b in document["blocks"])[:6000], limit=1))
                    terms = set(re.findall(r"\w+", " ".join(b["text"] for b in document["blocks"]).casefold()))
                    def relevance(example):
                        words = set(re.findall(r"\w+", str(example.get("quote", "")).casefold()))
                        return (-len(terms & words) / max(1, len(words)), str(example.get("example_id", "")))
                    chosen, groups, size = [], set(), 2
                    for example in sorted(selected, key=relevance):
                        group = example.get("story_group_id") or example.get("document_id")
                        encoded = len(json.dumps(example, ensure_ascii=False).encode())
                        if group in groups or size + encoded > 4800:
                            continue
                        chosen.append(example)
                        groups.add(group)
                        size += encoded
                        if len(chosen) == 4:
                            break
                    config["approved_examples"] = chosen
                buffered = []

                def flush():
                    if buffered:
                        for offset in range(0, len(buffered), 40):
                            db().batch([*buffered[offset:offset + 40], Workspace.touch_statement(run["project_id"])])
                        buffered.clear()

                async def checkpoint(event):
                    if event.get("stage") == "request_attempt":
                        attempt = event["attempt"]
                        payload = json.dumps(attempt, ensure_ascii=False, sort_keys=True)
                        identity = hashlib.sha256((run_id + task["id"] + payload).encode()).hexdigest()[:32]
                        db().execute("INSERT INTO request_attempt_events(id,project_id,run_id,task_id,request_id,status,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", [identity, run["project_id"], run_id, task["id"], attempt["request_id"], attempt["status"], payload, utcnow()])
                    elif event.get("stage") == "assessment_accepted":
                        assessment = dict(event["assessment"])
                        assessment["id"] = hashlib.sha256(f"{run_id}:{assessment['id']}".encode()).hexdigest()[:32]
                        assessment.update({"run_id": run_id, "project_id": run["project_id"], "document_id": document["id"]})
                        columns = ["id", "project_id", "run_id", "document_id", "task_id", "unit_id", "lens_id", "criterion_id", "criterion_kind", "result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical"]
                        assessment["task_id"] = task["id"]
                        values = [assessment.get(key, "unknown") for key in columns]
                        values[-1] = int(bool(assessment.get("hypothetical")))
                        # Negative decisions retain source anchors without repeated prose.
                        payload = dict(assessment)
                        if payload["result"] in {"does_not_meet", "not_applicable"}:
                            for key in ("context", "explanation", "alternative"):
                                payload.pop(key, None)
                            payload["evidence_refs"] = [{k: ref[k] for k in ("sentence_id", "block_id", "start", "end") if k in ref} for ref in payload.get("evidence_refs", [])]
                        buffered.append({"sql": "INSERT INTO v3_assessments(" + ",".join(columns) + ",payload_json,created_at) SELECT " + ",".join("?" for _ in range(len(columns) + 2)) + " WHERE EXISTS(SELECT 1 FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=? AND t.lease_token=? AND t.status='running' AND r.cancel_requested=0) ON CONFLICT(id) DO NOTHING", "params": [*values, json.dumps(payload, ensure_ascii=False), utcnow(), task["id"], token]})
                        if len(buffered) >= 40:
                            flush()
                    elif event.get("stage") == "unit_status":
                        flush()
                        status = dict(event)
                        unit = next(u for u in units if u["id"] == status["unit_id"])
                        status["characters"] = unit.get("source_characters", len(unit["text"]))
                        db().batch([{"sql": "UPDATE v3_unit_statuses SET status=?,payload_json=?,created_at=? WHERE run_id=? AND document_id=? AND unit_id=? AND EXISTS(SELECT 1 FROM tasks WHERE id=? AND lease_token=? AND status='running')", "params": [status["status"], json.dumps(status), utcnow(), run_id, document["id"], status["unit_id"], task["id"], token]}, Workspace.touch_statement(run["project_id"])])
                try:
                    # This adapter reserves and settles every request against
                    # durable run/monthly ledgers. An interrupted response keeps
                    # unknown invoice usage but charges its conservative reserve;
                    # it must not masquerade as an exhausted per-task budget.
                    analysis_config = {**config, "budget_usd": None}
                    result = await asyncio.wait_for(analyze_units(complete_document, config.get("lens_snapshots") or config["lens_ids"], provider, units=units, config=analysis_config, progress=checkpoint), timeout=limits["task_timeout_seconds"])
                finally:
                    flush()
                # Preserve legacy scheduler block denominators alongside unit coverage.
                result["coverage"]["total_blocks"] = len(units)
                result["coverage"]["processed_blocks"] = sum(s["status"] == "accepted" for s in result["unit_statuses"])
            else:
                result = await asyncio.wait_for(analyze_document(document, config, provider), timeout=limits["task_timeout_seconds"])
            result.setdefault("model_profile", {}).update(limits)
            # Only a current lease may commit. Replayed tasks cannot duplicate dashboard counts.
            lease = db().one("SELECT t.lease_token,r.cancel_requested FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=?", [task["id"]])
            if not lease or lease["lease_token"] != token or lease["cancel_requested"]:
                current = require_run(run_id)
                return {"pending": current["status"] not in TERMINAL, "run": current}
            result_key = f"runs/{run_id}/tasks/{task['id']}.json"
            db().put_json(result_key, result)
            statements = []
            for finding in result.get("findings", []):
                identity = json.dumps([run_id, document["id"], finding.get("block_id"), finding.get("start"), finding.get("end"), finding.get("lens_id"), finding.get("category"), finding.get("criterion")])
                finding_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
                finding.update({"id": finding_id, "document_id": document["id"], "run_id": run_id, "project_id": run["project_id"],
                                "document_title": document["title"], "author": document["author"], "review_status": "unreviewed", "review_note": ""})
                statements.append({"sql": "INSERT INTO findings(id,project_id,run_id,document_id,task_id,lens_id,category,payload_json,created_at) SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=? AND t.lease_token=? AND t.status='running' AND r.cancel_requested=0) ON CONFLICT(id) DO NOTHING",
                                   "params": [finding_id, run["project_id"], run_id, document["id"], task["id"], finding.get("lens_id", "discourse"), finding.get("category", "observation"), json.dumps(finding, ensure_ascii=False), utcnow(), task["id"], token]})
            for i in range(0, len(statements), 50):
                db().batch(statements[i:i+50])
            coverage = result.get("coverage", {})
            error_items = result.get("errors", [])
            error = json.dumps(error_items, ensure_ascii=False)[:12000] if error_items else None
            budget_error = any(item.get("code") in {"budget_exhausted", "pricing_required"} for item in error_items)
            status = "pending" if budget_error else ("completed" if coverage.get("complete", False) else "failed")
            db().execute("UPDATE tasks SET status=?,result_key=?,coverage_json=?,usage_json=?,error=?,lease_until=NULL WHERE id=? AND lease_token=? AND status='running' AND EXISTS(SELECT 1 FROM runs WHERE id=? AND cancel_requested=0)",
                         [status, result_key, json.dumps(coverage), json.dumps(result.get("usage", {})), error, task["id"], token, run_id])
            db().execute("UPDATE runs SET model_profile_json=? WHERE id=?", [json.dumps(result.get("model_profile", {})), run_id])
            if budget_error:
                db().execute("UPDATE runs SET status='paused_budget' WHERE id=? AND cancel_requested=0", [run_id])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            db().execute("UPDATE tasks SET status='failed',error=?,lease_until=NULL WHERE id=? AND lease_token=?", [message[:1200], task["id"], token])
        finally:
            # A failed remote request might still have been billed: preserve its conservative reservation as spend.
            while reservations:
                await record({"cost_usd": None})
        updated = refresh_run(run_id)
        pending = updated["status"] not in TERMINAL and not updated["status"].startswith("paused")
        return {"pending": pending, "run": updated}

    async def work_once(run_id: str) -> dict:
        if db().remote:
            return await _work_once(run_id)
        from doxa.privacy_lifecycle import activity, ProjectErasingError
        from doxa.access import WorkerAuthorizationError
        run = require_run(run_id)
        try:
            access.require_worker(run["config"].get("execution_actor_id"), run["project_id"], "analyze")
        except WorkerAuthorizationError:
            db().execute("UPDATE runs SET status='paused_access' WHERE id=?", [run_id])
            return {"pending": False, "run": require_run(run_id)}
        try:
            with activity(db(), run["project_id"], "analysis_worker"):
                return await _work_once(run_id)
        except ProjectErasingError:
            return {"pending": False, "run": {"id": run_id, "status": "cancelled", "reason": "project_erasure"}}

    async def run_loop(run_id: str):
        config = require_run(run_id)["config"]
        concurrency = 4 if config.get("provider") in {"cloudflare", "openai", "runpod"} else 1
        async def worker():
            while True:
                result = await work_once(run_id)
                if not result["pending"]:
                    break
                await asyncio.sleep(1 if result.get("idle") else 0.01)
        await asyncio.gather(*(worker() for _ in range(concurrency)))

    def schedule(run_id: str):
        if not start_workers:
            return
        if db().remote:
            db().enqueue(run_id)
        elif run_id not in tasks or tasks[run_id].done():
            tasks[run_id] = asyncio.create_task(run_loop(run_id))

    app.state.work_once = work_once

    async def create_run(project_id: str, body: RunInput, *, check_health: bool = True) -> dict:
        require_project(project_id)
        config = body.model_dump(exclude_none=True)
        if not db().remote:
            from doxa.pilot_commerce import CommerceError, apply_server_pricing
            try:
                config = apply_server_pricing(config, submitted_fields=body.model_fields_set)
            except CommerceError as error:
                raise HTTPException(error.status_code, str(error)) from error
        if access.managed:
            config["execution_actor_id"] = current_principal().id
            config["managed_access"] = True
        from doxa.pilot_api import enforce_analysis_policy, freeze_brief
        if body.service_profile_id:
            if body.verification_of:
                raise HTTPException(422, "Verification inherits its original run's service brief")
            try:
                frozen = freeze_brief(db(), project_id, body.service_profile_id, body.pilot_brief_revision)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            config["service_brief"] = frozen
            config["question"] = frozen["question"]
            config["persona"] = frozen["perspective"] + "\nReport audience: " + frozen["audience"]
            config["lens_ids"] = frozen["lens_ids"]
            body = body.model_copy(update={"question": config["question"], "persona": config["persona"],
                                           "lens_ids": config["lens_ids"]})
        elif body.pilot_brief_revision is not None:
            raise HTTPException(422, "A brief revision requires its service profile")
        try:
            enforce_analysis_policy(db(), project_id, config)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if body.request_journal_mode == "visible_io":
            if db().remote or os.environ.get("DOXA_ENABLE_PRIVATE_JOURNAL", "").lower() not in {"1", "true", "yes"}:
                raise HTTPException(422, "Private visible request capture requires operator-enabled local storage (DOXA_ENABLE_PRIVATE_JOURNAL)")
            if body.provider == "lexical" or not str(body.methodology_version).startswith("3"):
                raise HTTPException(422, "Private request capture applies to contextual v3 inference runs")
            try:
                limit = int(os.environ.get("DOXA_PRIVATE_JOURNAL_MAX_BYTES", str(64 * 1024 * 1024)))
                if not 4096 <= limit <= 1024 * 1024 * 1024:
                    raise ValueError
            except ValueError as error:
                raise HTTPException(422, "DOXA_PRIVATE_JOURNAL_MAX_BYTES must be an integer from 4096 through 1073741824") from error
            config["private_journal_limit_bytes"] = limit
        if body.provider == "cloudflare" and body.model in {"@cf/openai/gpt-oss-120b", "@cf/openai/gpt-oss-20b"}:
            # Apply the verified managed profile only to new runs. Explicit
            # operator choices, historical runs and compatible endpoints retain
            # their requested behavior.
            if "reasoning_effort" not in body.model_fields_set:
                config["reasoning_effort"] = "low"
            if "reasoning_token_allowance" not in body.model_fields_set:
                config["reasoning_token_allowance"] = 2048
        if body.provider == "ollama" and not db().remote:
            try:
                limits = task_execution_limits(body.provider)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            config.update(limits)
            config["task_timing_history"] = [{"applied_at": utcnow(), "reason": "run_created", **limits}]
        modern = str(body.methodology_version).startswith("3") and body.provider != "lexical"
        verification = None
        if body.verification_of:
            from doxa.verification import select_verification_units
            primary = require_run(body.verification_of, project_id)
            try:
                verification = select_verification_units(db(), project_id, body.verification_of)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            config.update({key: primary["config"][key] for key in ("lens_ids", "lens_snapshots", "lens_snapshots_sha256", "question", "persona", "custom_lens", "learning_snapshot_id", "service_brief", "service_profile_id", "pilot_brief_revision", "context_policy", "method_cards_snapshot") if key in primary["config"]})
            config["verification_selection"] = verification
        if modern:
            from doxa.contextual import PROMPT_VERSION, UNIT_BUILDER_VERSION
            config.update({"unit_max_chars": 1200 if body.provider == "ollama" else 4000,
                           "task_index_basis": "source_units_v3", "context_sentences": 2,
                           "pipeline_version": PROMPT_VERSION, "unit_builder_version": UNIT_BUILDER_VERSION})
            if not verification:
                from doxa.method_cards import freeze_method_cards
                config["method_cards_snapshot"] = freeze_method_cards(config["lens_ids"], custom_lens=config.get("custom_lens"))
            if verification:
                config["unit_max_chars"] = primary["config"].get("unit_max_chars", 4000)
            if not verification and "learning_snapshot_id" not in body.model_fields_set:
                active_memory = db().one("SELECT snapshot_id FROM v3_learning_active WHERE project_id=?", [project_id])
                if active_memory and active_memory["snapshot_id"]:
                    config["learning_snapshot_id"] = active_memory["snapshot_id"]
        if modern and body.provider not in {"ollama", "lexical"} and not body.allow_remote_text:
            raise HTTPException(422, "Enable remote text transfer for this run before sending source passages to the selected provider")
        if body.learning_snapshot_id and not db().one("SELECT id FROM v3_learning_snapshots WHERE id=? AND project_id=?", [body.learning_snapshot_id, project_id]):
            raise HTTPException(422, "Learning snapshot does not belong to this project")
        if body.provider not in {"lexical", "ollama", "cloudflare", "openai", "runpod"}:
            raise HTTPException(422, "Unknown inference provider")
        from doxa.lenses import get_lens, validate_custom_lens
        try:
            if len(body.lens_ids) > 12:
                raise ValueError("Choose no more than 12 analytical lenses")
            for lens_id in body.lens_ids:
                if lens_id != "custom":
                    get_lens(lens_id)
                elif not body.custom_lens:
                    raise ValueError("Custom lens selection requires explicit criteria")
            if body.custom_lens:
                config["custom_lens"] = validate_custom_lens(body.custom_lens)
            if modern and not config.get("lens_snapshots"):
                config["lens_snapshots"] = [validate_custom_lens(config["custom_lens"]) if identity == "custom" else get_lens(identity)
                                            for identity in config["lens_ids"]]
                config["lens_snapshots_sha256"] = hashlib.sha256(json.dumps(config["lens_snapshots"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not body.lens_ids and not body.custom_lens:
            raise HTTPException(422, "Select at least one analytical lens")
        if check_health:
            readiness = await provider_health(config)
            if not readiness.get("ok"):
                raise HTTPException(422, {"message": "Provider is not ready. Choose the lexical baseline or configure inference.", "health": readiness})
            for source, target in [("revision", "model_revision"), ("quantization", "quantization")]:
                if readiness.get(source) and readiness[source] != "unresolved":
                    config[target] = readiness[source]
            config["provider_preflight"] = readiness
        rows = db().query("SELECT * FROM documents WHERE project_id=? AND status='ready' ORDER BY created_at,id", [project_id])
        if body.document_ids is not None:
            selected_source_ids = set(body.document_ids)
            rows = [row for row in rows if row["id"] in selected_source_ids]
        if verification:
            rows = [row for row in rows if row["id"] in verification["document_ids"]]
        if not rows:
            raise HTTPException(422, "Import at least one successfully extracted document")
        if not db().remote:
            from doxa.pilot_commerce import CommerceError, admit_quote_work
            try:
                from doxa.lenses import get_lens
                criteria = sum(len((config.get("custom_lens") if lens == "custom" else get_lens(lens))["criteria"]) for lens in config["lens_ids"])
                characters = sum(sum(len(block["text"]) for block in db().get_json(row["blocks_key"])) for row in rows)
                admit_quote_work(db(), project_id, config.get("quote_id"), profile_id=config.get("service_profile_id", "public"),
                                 documents=len(rows), characters=characters, criteria=criteria,
                                 source_ids=[row["id"] for row in rows],
                                 ocr_pages=sum(row["page_count"] for row in rows if "ocr" in str(json.loads(row.get("metadata_json") or "{}").get("extractor", ""))))
            except CommerceError as error:
                raise HTTPException(error.status_code, str(error)) from error
        try:
            enforce_analysis_policy(db(), project_id, config, rows)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        run_id = uid()
        config["schema_version"] = "3.0" if modern else "1.0"
        from doxa.pilot_api import active_policy
        selected_policy = active_policy(db(), project_id, default_for_service=bool(config.get("service_profile_id") or config.get("service_brief")))
        if selected_policy:
            config["privacy_policy_snapshot"] = selected_policy
        manifest_key = f"runs/{run_id}/source-manifest.json"
        source_manifest = [{**{key: row.get(key) for key in ["id", "sha256", "title", "author", "institution", "document_type", "published_at", "source_url", "license"]}, "metadata": json.loads(row.get("metadata_json") or "{}")} for row in rows]
        frozen_by_id = {item["id"]: item for item in source_manifest}
        if not db().remote:
            from doxa.privacy_lifecycle import track_artifact
            track_artifact(db(), project_id, f"objects/runs/{run_id}", "run_inputs")
        db().put_json(manifest_key, source_manifest)
        config["source_manifest_key"] = manifest_key
        config["source_snapshot"] = [{"id": row["id"], "sha256": row["sha256"]} for row in rows]
        if modern:
            from doxa.method_config import method_config, source_metadata_hash
            config["method_config_version"] = "0.4"
            config["source_metadata_hash"] = source_metadata_hash(source_manifest)
            method = method_config(config)
            config["method_config"] = method.as_dict()
            config["method_fingerprint"] = method.fingerprint
        db().execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES(?,?,'creating',?,?)", [run_id, project_id, json.dumps(config), utcnow()])
        statements = []
        ordinal = 0
        for row in rows:
            statements.append({"sql": "INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES(?,?,?) ON CONFLICT(run_id,document_id) DO NOTHING", "params": [run_id, row["id"], row["sha256"]]})
            if modern:
                frozen = frozen_by_id[row["id"]]
                statements.append({"sql": "INSERT INTO run_source_metadata(run_id,document_id,payload_json,metadata_sha256) VALUES(?,?,?,?)", "params": [run_id, row["id"], json.dumps(frozen), source_metadata_hash(frozen)]})
            blocks = db().get_json(row["blocks_key"])
            if modern:
                from doxa.contextual import build_units
                doc = require_document(row["id"], True)
                units = build_units(doc, max_unit_chars=config["unit_max_chars"],
                                    context_sentences=config.get("context_sentences", 2), context_policy=config.get("context_policy"))
                if verification:
                    selected_units = set(verification["unit_ids"].get(row["id"], []))
                    units = [unit for unit in units if unit["id"] in selected_units]
                for unit in units:
                    statements.append({"sql": "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,'pending',?,?,?) ON CONFLICT(run_id,document_id,unit_id) DO NOTHING", "params": [run_id, row["id"], unit["id"], unit.get("source_characters", len(unit["text"])), json.dumps({"unit_id": unit["id"], "block_id": unit["block_id"], "start": unit["start"], "end": unit["end"], "block_ids": unit.get("block_ids", [unit["block_id"]])}), utcnow()]})
                first, characters = 0, 0
                for index, unit in enumerate(units):
                    size = unit.get("source_characters", len(unit["text"]))
                    if characters and characters + size > (1200 if body.provider == "ollama" else 16000):
                        statements.append({"sql": "INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block) VALUES(?,?,?,?,?,?)", "params": [uid(), run_id, row["id"], ordinal, first, index]})
                        ordinal += 1
                        first, characters = index, 0
                    characters += size
                if first < len(units):
                    statements.append({"sql": "INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block) VALUES(?,?,?,?,?,?)", "params": [uid(), run_id, row["id"], ordinal, first, len(units)]})
                    ordinal += 1
                continue
            start, chars = 0, 0
            for i, block in enumerate(blocks):
                if chars and chars + len(block["text"]) > 20000:
                    statements.append({"sql": "INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block) VALUES(?,?,?,?,?,?)", "params": [uid(), run_id, row["id"], ordinal, start, i]})
                    ordinal += 1
                    start, chars = i, 0
                chars += len(block["text"])
            if start < len(blocks):
                statements.append({"sql": "INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block) VALUES(?,?,?,?,?,?)", "params": [uid(), run_id, row["id"], ordinal, start, len(blocks)]})
                ordinal += 1
        for i in range(0, len(statements), 50):
            db().batch(statements[i:i+50])
        from doxa.workspace import Workspace
        db().batch([{"sql": "UPDATE runs SET status='queued' WHERE id=? AND status='creating'", "params": [run_id]}, Workspace.touch_statement(project_id)])
        result = refresh_run(run_id)
        schedule(run_id)
        return result

    @app.post("/api/projects/{project_id}/runs", status_code=201)
    async def start_run(project_id: str, body: RunInput):
        return await create_run(project_id, body)

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str):
        require_project(project_id)
        return [unpack(row) for row in db().query("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC", [project_id])]

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        return require_run(run_id)

    @app.post("/internal/work/{run_id}")
    async def cloud_work(run_id: str):
        result = await work_once(run_id)
        return {"pending": result["pending"], "run_id": run_id, "status": result["run"]["status"],
                "retry_after": result.get("retry_after", 0)}

    @app.post("/internal/report-work/{job_id}")
    async def cloud_report_work(job_id: str):
        return await asyncio.to_thread(app.state.report_jobs.work_once, job_id, deadline_seconds=240)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        require_run(run_id)
        db().batch([
            {"sql": "UPDATE runs SET cancel_requested=1,status='cancelled',completed_at=? WHERE id=?", "params": [utcnow(), run_id]},
            {"sql": "UPDATE tasks SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE run_id=? AND status IN ('pending','running')", "params": [run_id]},
        ])
        for reservation in db().query("SELECT id FROM budget_reservations WHERE run_id=? AND status='reserved'", [run_id]):
            settle_reservation(reservation["id"], {"cost_usd": None, "estimated": True, "cancelled_inflight_request": True})
        return refresh_run(run_id)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, body: dict | None = None):
        run = require_run(run_id)
        if run["status"] not in {"paused_budget", "paused_access", "paused_privacy", "failed", "partial", "cancelled"}:
            raise HTTPException(409, "Only a paused, partial, failed or cancelled run can be resumed")
        config = run["config"]
        if access.managed:
            config["execution_actor_id"] = current_principal().id
            config["managed_access"] = True
        from doxa.pilot_api import enforce_analysis_policy
        try:
            enforce_analysis_policy(db(), run["project_id"], config)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if str(config.get("methodology_version", "1")).startswith("3") and config.get("provider") != "lexical":
            from doxa.contextual import PROMPT_VERSION
            if config.get("pipeline_version") and config["pipeline_version"] != PROMPT_VERSION:
                raise HTTPException(409, "Analysis method changed. Start a new run to preserve comparable results.")
        if config.get("saved_snapshot"):
            raise HTTPException(409, "Saved showcase runs are fixed. Start a new analysis to compare a different model or retry the corpus.")
        if body and "budget_usd" in body:
            cap = body["budget_usd"]
            if not isinstance(cap, (float, int)) or not math.isfinite(cap) or not 0 <= cap <= 10000:
                raise HTTPException(422, "Run budget must be between $0 and $10,000")
            if config.get("verification_of") and cap > 2:
                raise HTTPException(422, "Independent verification has a $2 total run cap")
            config.setdefault("budget_changes", []).append({"previous": config.get("budget_usd"), "new": cap, "at": utcnow()})
            config["budget_usd"] = cap
        readiness = await provider_health(config)
        if not readiness.get("ok"):
            raise HTTPException(422, {"message": "Provider must be ready before resuming", "health": readiness})
        expected = config.get("model_revision")
        actual = readiness.get("revision")
        if expected and actual and expected != "unresolved" and actual != expected:
            raise HTTPException(409, "The installed model changed. Start a new run rather than mixing revisions.")
        db().execute("UPDATE runs SET config_json=? WHERE id=?", [json.dumps(config), run_id])
        db().execute("UPDATE runs SET status='queued',cancel_requested=0,completed_at=NULL WHERE id=?", [run_id])
        db().execute("UPDATE tasks SET status='pending',error=NULL,lease_token=NULL,lease_until=NULL WHERE run_id=? AND status IN ('failed','cancelled')", [run_id])
        result = refresh_run(run_id)
        schedule(run_id)
        return result

    def active_run_id(project_id: str) -> str | None:
        row = db().one("SELECT id FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", [project_id])
        return row["id"] if row else None

    def select_findings(project_id: str, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        run_id = filters.get("run_id") or active_run_id(project_id)
        if not run_id:
            return []
        require_run(run_id, project_id)
        clauses = ["f.project_id=?", "f.run_id=?", "t.result_key IS NOT NULL"]
        params = [project_id, run_id]
        for key in ["document_id", "lens_id", "category", "review_status"]:
            if filters.get(key):
                clauses.append(f"f.{key}=?")
                params.append(filters[key])
        rows = db().query("SELECT f.*,t.status AS source_task_status,d.author AS source_author,d.institution AS source_institution,d.title AS source_title FROM findings f JOIN tasks t ON t.id=f.task_id JOIN documents d ON d.id=f.document_id WHERE " + " AND ".join(clauses) + " ORDER BY f.created_at,f.id", params)
        results = []
        for row in rows:
            finding = json.loads(row["payload_json"])
            finding.update({"review_status": row["review_status"], "review_note": row["review_note"],
                            "source_task_status": row["source_task_status"], "source_task_complete": row["source_task_status"] == "completed"})
            if filters.get("author") and (row["source_author"] or row["source_institution"] or "Unknown author") != filters["author"]:
                continue
            query = str(filters.get("q") or "").lower()
            if query and not any(query in str(value or "").lower() for value in [finding.get("title"), finding.get("quote"), finding.get("explanation"), finding.get("target"), row["source_title"]]):
                continue
            results.append(finding)
        return results

    @app.get("/api/projects/{project_id}/findings")
    def findings(project_id: str, request: Request, offset: int = 0, limit: int = 10000):
        require_project(project_id)
        if offset < 0 or not 1 <= limit <= 10000:
            raise HTTPException(422, "Invalid pagination")
        return select_findings(project_id, dict(request.query_params))[offset:offset+limit]

    @app.patch("/api/findings/{finding_id}/review")
    def review(finding_id: str, body: ReviewInput):
        if current_principal().managed:
            raise HTTPException(409, "Managed reviews require a structured v3 assessment with individual reviewer provenance")
        if body.review_status not in {"unreviewed", "confirmed", "rejected", "revised"}:
            raise HTTPException(422, "Unknown review status")
        row = db().one("SELECT * FROM findings WHERE id=?", [finding_id])
        if not row:
            raise HTTPException(404, "Finding not found")
        db().batch([
            {"sql": "INSERT INTO reviews(id,finding_id,review_status,review_note,previous_status,created_at) VALUES(?,?,?,?,?,?)", "params": [uid(), finding_id, body.review_status, body.review_note, row["review_status"], utcnow()]},
            {"sql": "UPDATE findings SET review_status=?,review_note=? WHERE id=?", "params": [body.review_status, body.review_note, finding_id]}])
        return {**json.loads(row["payload_json"]), **body.model_dump()}

    @app.get("/api/findings/{finding_id}/reviews")
    def reviews(finding_id: str):
        return db().query("SELECT * FROM reviews WHERE finding_id=? ORDER BY created_at", [finding_id])

    @app.get("/api/projects/{project_id}/batches")
    def batches(project_id: str):
        require_project(project_id)
        return db().query("SELECT b.*,COUNT(d.id) AS documents FROM batches b LEFT JOIN batch_members m ON m.batch_id=b.id LEFT JOIN documents d ON d.id=m.document_id WHERE b.project_id=? GROUP BY b.id ORDER BY b.created_at DESC", [project_id])

    @app.get("/api/projects/{project_id}/batches/compare")
    def compare_imported_batches(project_id: str, before_batch: str | None = None, after_batch: str | None = None):
        from doxa.batches import compare_batches
        require_project(project_id)
        try:
            return compare_batches(db(), project_id, before_batch, after_batch)
        except (ValueError, LookupError) as exc:
            raise HTTPException(404, str(exc)) from exc

    def dashboard_data(project_id: str, filters: dict | None = None) -> dict:
        filters = filters or {}
        docs = [unpack(row) for row in db().query("SELECT * FROM documents WHERE project_id=?", [project_id])]
        items = select_findings(project_id, filters)
        run_id = filters.get("run_id") or active_run_id(project_id)
        run = require_run(run_id, project_id) if run_id else None
        source_snapshot = (run or {}).get("config", {}).get("source_snapshot", [])
        if source_snapshot:
            selected_ids = {item["id"] for item in source_snapshot}
            docs = [doc for doc in docs if doc["id"] in selected_ids]
        if filters.get("document_id"):
            docs = [doc for doc in docs if doc["id"] == filters["document_id"]]
        if filters.get("author"):
            docs = [doc for doc in docs if (doc["author"] or doc["institution"] or "Unknown author") == filters["author"]]
        categories = Counter(f.get("category", "observation") for f in items)
        types = Counter(d["document_type"] for d in docs)
        lenses = Counter(f.get("lens_id", "discourse") for f in items)
        authors = defaultdict(lambda: {"documents": 0, "findings": 0, "words": 0, "blocks_total": 0, "blocks_done": 0})
        doc_coverage = defaultdict(lambda: [0, 0])
        if run_id:
            for task in db().query("SELECT document_id,start_block,end_block,coverage_json FROM tasks WHERE run_id=?", [run_id]):
                doc_coverage[task["document_id"]][0] += task["end_block"] - task["start_block"]
                doc_coverage[task["document_id"]][1] += int(json.loads(task["coverage_json"] or "{}").get("processed_blocks", 0))
        by_id = {d["id"]: d for d in docs}
        timeline = defaultdict(lambda: {"documents": 0, "findings": 0})
        for doc in docs:
            author = doc["author"] or "Unknown"
            authors[author]["documents"] += 1
            authors[author]["words"] += doc["word_count"]
            authors[author]["blocks_total"] += doc_coverage[doc["id"]][0]
            authors[author]["blocks_done"] += doc_coverage[doc["id"]][1]
            if doc.get("published_at"):
                timeline[doc["published_at"][:4]]["documents"] += 1
        for finding in items:
            doc = by_id.get(finding["document_id"], {})
            authors[doc.get("author") or "Unknown"]["findings"] += 1
            if doc.get("published_at"):
                timeline[doc["published_at"][:4]]["findings"] += 1
        progress = dict((run or {}).get("progress", {}))
        if run_id and (filters.get("document_id") or filters.get("author")):
            selected_ids = {doc["id"] for doc in docs}
            selected_tasks = [task for task in db().query("SELECT document_id,start_block,end_block,status,coverage_json FROM tasks WHERE run_id=?", [run_id]) if task["document_id"] in selected_ids]
            progress["blocks_total"] = sum(task["end_block"] - task["start_block"] for task in selected_tasks)
            progress["blocks_done"] = sum(int(json.loads(task["coverage_json"] or "{}").get("processed_blocks", 0)) for task in selected_tasks)
            progress["documents_total"] = len(docs)
            progress["tasks_total"] = len(selected_tasks)
            progress["tasks_done"] = sum(task["status"] == "completed" for task in selected_tasks)
            progress["tasks_failed"] = sum(task["status"] == "failed" for task in selected_tasks)
        total = progress.get("blocks_total", 0)
        done = progress.get("blocks_done", 0)
        lexical = (run or {}).get("config", {}).get("provider") == "lexical"
        processing_coverage = round(100 * done / total, 1) if total else 0
        semantic_coverage = 0 if lexical else processing_coverage
        coverage_label = "Lexical scan coverage" if lexical else "Model analysis coverage"
        return {"documents": len(docs), "total_words": sum(d["word_count"] for d in docs), "total_pages": sum(d["page_count"] for d in docs),
                "findings": len(items), "reviewed": sum(f["review_status"] != "unreviewed" for f in items),
                "coverage": semantic_coverage, "semantic_coverage": semantic_coverage,
                "lexical_coverage": processing_coverage if lexical else 0, "processing_coverage": processing_coverage,
                "coverage_label": coverage_label, "analysis_kind": "lexical_scan" if lexical else "model_analysis",
                "coverage_detail": progress,
                "extraction": {"ready": sum(d["status"] == "ready" for d in docs), "failed": sum(d["status"] == "failed" for d in docs)},
                "by_category": [{"name": k, "count": v} for k, v in categories.most_common()],
                "by_author": [{"name": k, **v,
                               "analysis_coverage": round(100 * v["blocks_done"] / v["blocks_total"], 1) if v["blocks_total"] else 0,
                               "findings_per_10000_words": round(v["findings"] * 10000 / v["words"], 2) if v["words"] and v["blocks_total"] and v["blocks_done"] == v["blocks_total"] else None,
                               "rate_notice": "Word-normalized observation rates are withheld when source analysis is incomplete; they do not measure prejudice prevalence."}
                              for k, v in sorted(authors.items(), key=lambda item: -item[1]["findings"])],
                "by_type": [{"name": k, "count": v} for k, v in types.most_common()],
                "by_lens": [{"name": k, "count": v} for k, v in lenses.most_common()],
                "timeline": [{"date": k, **v} for k, v in sorted(timeline.items())],
                "recent_findings": items[:8], "runs": runs(project_id), "batches": batches(project_id),
                "cost_usd": (run or {}).get("usage", {}).get("cost_usd", 0), "active_run_id": run_id,
                "budget_accounted_usd": (run or {}).get("spent_usd", 0), "budget_reserved_usd": (run or {}).get("reserved_usd", 0),
                "method": (run or {}).get("config", {}).get("provider"),
                "methodology_notice": ("Literal word matches only; semantic coverage is zero and no prejudice or endorsement conclusions are produced. " if lexical else "Model interpretations require review; no interpretation accuracy is claimed. ") + "Counts and word-normalized rates describe the selected source corpus and run, not authors' private beliefs."}

    @app.get("/api/projects/{project_id}/dashboard")
    def dashboard(project_id: str, request: Request):
        require_project(project_id)
        return dashboard_data(project_id, dict(request.query_params))

    @app.post("/api/projects/{project_id}/demo")
    async def load_demo(project_id: str):
        require_project(project_id)
        source_dir = assets_root / "corpus" / "documents"
        source_files = sorted(source_dir.glob("*.json"))
        if len(source_files) != 20:
            raise HTTPException(503, "The 20-document corpus is unavailable. Run scripts/fetch_corpus.py.")
        batch = new_batch(project_id, "Public institutions · 20 real documents")
        imported = []
        for file in source_files:
            source = json.loads(file.read_text())
            digest = source.get("content_sha256") or hashlib.sha256(json.dumps(source["blocks"], ensure_ascii=False).encode()).hexdigest()
            existing = db().one("SELECT id FROM documents WHERE project_id=? AND sha256=?", [project_id, digest])
            if existing:
                imported.append(require_document(existing["id"]))
            else:
                source["sha256"] = digest
                source["source_id"] = source.get("id", file.stem)
                imported.append(save_document(project_id, batch["id"], source))
            db().execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", [batch["id"], imported[-1]["id"]])
        cached = assets_root / "corpus" / "showcase.json.gz"
        if use_saved_showcase and cached.exists():
            from doxa.showcase import restore_showcase
            try:
                run_id = restore_showcase(db(), project_id, imported, cached)
                run = require_run(run_id)
                return {"documents": imported, "run": run, "batch": batch, "method": run["config"].get("provider"),
                        "notice": "Real documents with previously computed model observations. All quotes were checked again; review interpretations and coverage. No inference was requested by this import."}
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise HTTPException(503, f"Saved showcase integrity check failed: {exc}") from exc
        # Baseline is genuinely computed over every imported block; it is never labeled AI interpretation.
        run = await create_run(project_id, RunInput(provider="lexical", model="lexical-baseline-v1", document_ids=[d["id"] for d in imported]), check_health=False)
        return {"documents": imported, "run": run, "batch": batch, "method": "lexical-baseline", "notice": "Real public documents. Lexical matches are navigation aids, not conclusions about prejudice or author endorsement."}

    @app.post("/api/projects/{project_id}/reports")
    async def export_report(project_id: str, body: dict):
        from doxa.reports import build_report
        project = require_project(project_id)
        fmt = str(body.get("format", "pdf")).lower()
        if fmt not in {"pdf", "html", "xlsx", "csv", "jsonl"}:
            raise HTTPException(422, "Supported formats: pdf, html, xlsx, csv, jsonl")
        profile = body.get("profile", "research")
        if profile not in {"research", "briefing", "teaching"}:
            raise HTTPException(422, "Unknown report profile")
        filters = body.get("filters") or {}
        items = select_findings(project_id, filters)
        all_docs = [require_document(row["id"], True) for row in db().query("SELECT id FROM documents WHERE project_id=?", [project_id])]
        if filters.get("document_id"):
            all_docs = [doc for doc in all_docs if doc["id"] == filters["document_id"]]
        run_id = filters.get("run_id") or active_run_id(project_id)
        selected_runs = [require_run(run_id)] if run_id else []
        if selected_runs and selected_runs[0].get("config", {}).get("source_snapshot"):
            selected_ids = {source["id"] for source in selected_runs[0]["config"]["source_snapshot"]}
            all_docs = [doc for doc in all_docs if doc["id"] in selected_ids]
        if filters.get("author"):
            all_docs = [doc for doc in all_docs if (doc["author"] or doc["institution"] or "Unknown author") == filters["author"]]
        snapshot = {"schema_version": "1.0", "project": project, "documents": all_docs, "findings": items,
                    "runs": selected_runs, "metrics": dashboard_data(project_id, filters), "profile": profile,
                    "filters": filters, "generated_at": utcnow(), "reviews": db().query("SELECT r.* FROM reviews r JOIN findings f ON f.id=r.finding_id WHERE f.project_id=? ORDER BY r.created_at", [project_id])}
        report_id = uid()
        output_dir = db().root / "exports" / report_id
        if not db().remote:
            from doxa.privacy_lifecycle import track_artifact
            track_artifact(db(), project_id, f"exports/{report_id}", "report_work")
            track_artifact(db(), project_id, f"objects/reports/{report_id}", "report_artifact")
        output_dir.mkdir(parents=True)
        path = await project_thread(project_id, "legacy_report_render", build_report, snapshot, fmt, output_dir)
        object_key = f"reports/{report_id}/{path.name}"
        snapshot_key = f"reports/{report_id}/snapshot.json"
        db().put_path(object_key, path)
        db().put_json(snapshot_key, snapshot)
        db().execute("INSERT INTO reports(id,project_id,format,profile,object_key,snapshot_key,created_at) VALUES(?,?,?,?,?,?,?)", [report_id, project_id, fmt, profile, object_key, snapshot_key, utcnow()])
        return FileResponse(path, filename=path.name, media_type={"pdf": "application/pdf", "html": "text/html", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv", "jsonl": "application/x-ndjson"}[fmt])

    @app.get("/api/projects/{project_id}/reports")
    def report_history(project_id: str):
        require_project(project_id)
        return db().query("SELECT id,project_id,format,profile,created_at FROM reports WHERE project_id=? ORDER BY created_at DESC", [project_id])

    @app.get("/api/reports/{report_id}/download")
    def download_report(report_id: str):
        report = db().one("SELECT * FROM reports WHERE id=?", [report_id])
        if not report:
            raise HTTPException(404, "Report not found")
        path = db().root / "downloads" / report_id / Path(report["object_key"]).name
        db().download(report["object_key"], path)
        return FileResponse(path, filename=path.name)

    from doxa.v3_api import register as register_v3
    from doxa.imports import register as register_imports
    services = {"db": db, "require_project": require_project, "require_document": require_document,
                "require_run": require_run, "create_run": create_run, "new_batch": new_batch,
                "ingest": ingest, "load_demo": load_demo, "provider_health": provider_health, "root": assets_root,
                "use_saved_showcase": use_saved_showcase, "save_document": save_document}
    services["report_jobs"] = lambda: app.state.report_jobs
    register_v3(app, services)
    register_imports(app, services)
    from doxa.pilot_api import register as register_pilot
    register_pilot(app, services)
    from doxa.readiness import register_readiness_api
    from doxa.comparisons import register_comparison_api
    from doxa.methodology_api import register_methodology_api
    from doxa.activity_jobs import register_activity_api
    from doxa.pilot_commerce import register_commerce_api
    register_readiness_api(app, db)
    from doxa.recorded_starter import register_recorded_api
    register_recorded_api(app)
    register_comparison_api(app, db)
    register_methodology_api(app, db)
    register_activity_api(app, services)
    register_commerce_api(app, db)
    install_access(app, access)

    from doxa.resources import frontend_distribution
    distribution = frontend_distribution()
    assets = distribution / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith(("api/", "internal/")):
            raise HTTPException(404, "Endpoint not found")
        public_file = (distribution / path).resolve()
        if path and public_file.is_relative_to(distribution.resolve()) and public_file.is_file():
            return FileResponse(public_file)
        index = distribution / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text())
        return HTMLResponse("<h1>DOXA by TACITUS</h1><p>Build the interface with <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p><p>The API is ready at <a href='/docs'>/docs</a>.</p>")

    return app


app = create_app()
