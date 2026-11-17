"""Bounded archive intake and logical import sessions across HTTP file groups."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
import stat
import tempfile
import uuid
import zipfile

from fastapi import File, HTTPException, UploadFile

from .store import utcnow
from .workspace import Workspace


def commit_document(store, values: dict, blocks: list[dict], source_identity: str) -> str:
    """Publish readiness only after every derived record is durable.

    Local SQLite commits the complete document atomically. Large remote imports
    use bounded batches while their durable commit stays non-ready; retries
    rebuild the complete index before publishing a final atomic ready marker.
    """
    from .news import _index_statements
    existing = store.one("SELECT document_id FROM import_commits WHERE project_id=? AND source_identity=? AND sha256=?",
                         [values["project_id"], source_identity, values["sha256"]])
    if existing:
        values = {**values, "id": existing["document_id"]}
    document_id = values["id"]
    state = values["status"]
    staged = {**values, "status": "importing"}
    columns = list(staged)
    updates = ",".join(f"{key}=excluded.{key}" for key in columns if key not in {"id", "created_at"})
    statements = [{"sql": "INSERT INTO documents(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ") ON CONFLICT(id) DO UPDATE SET " + updates, "params": list(staged.values())},
        {"sql": "INSERT INTO import_commits(project_id,source_identity,sha256,document_id,status,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,source_identity,sha256) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at", "params": [values["project_id"], source_identity, values["sha256"], document_id, "importing", utcnow()]},
        Workspace.touch_statement(values["project_id"])]
    if store.fts:
        statements.extend(_index_statements(document_id, values["project_id"], values["title"], blocks))
    final = [
        {"sql": "INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", "params": [values["batch_id"], document_id]},
        {"sql": "INSERT INTO v3_sources(document_id,project_id,source_identity,content_hash,canonical_url,relative_path,version,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET source_identity=excluded.source_identity,content_hash=excluded.content_hash,canonical_url=excluded.canonical_url,relative_path=excluded.relative_path,version=excluded.version", "params": [document_id, values["project_id"], source_identity, values["sha256"], values["source_url"], json.loads(values["metadata_json"]).get("relative_path") or values["filename"], json.loads(values["metadata_json"]).get("extraction_manifest", {}).get("extraction_version_id", ""), utcnow()]},
        {"sql": "UPDATE documents SET status=? WHERE id=?", "params": [state, document_id]},
        {"sql": "UPDATE import_commits SET status=?,updated_at=? WHERE project_id=? AND source_identity=? AND sha256=?", "params": [state, utcnow(), values["project_id"], source_identity, values["sha256"]]},
    ]
    session = store.one("SELECT id FROM v3_imports WHERE batch_id=? AND project_id=?", [values["batch_id"], values["project_id"]])
    if session:
        relative = json.loads(values["metadata_json"]).get("relative_path") or values["filename"]
        final.extend([
            {"sql": "INSERT INTO v3_import_files(import_id,relative_path,sha256,document_id,status,error,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(import_id,relative_path,sha256) DO UPDATE SET document_id=excluded.document_id,status=excluded.status,error=excluded.error", "params": [session["id"], relative, values["sha256"], document_id, state, values.get("error"), utcnow()]},
            {"sql": "UPDATE v3_imports SET completed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='ready'),failed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='failed'),status=CASE WHEN total_files<=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=?) THEN 'completed' ELSE 'running' END WHERE id=?", "params": [session["id"], session["id"], session["id"], session["id"]]},
        ])
    final.append(Workspace.touch_statement(values["project_id"]))
    if any(len(json.dumps(statement, ensure_ascii=False).encode()) > 768 * 1024 for statement in [*statements, *final]):
        raise ValueError("Document metadata exceeds the bounded import statement size")
    if len(json.dumps(statements[:3], ensure_ascii=False).encode()) > 768 * 1024 or len(json.dumps(final, ensure_ascii=False).encode()) > 768 * 1024:
        raise ValueError("Document publication metadata exceeds the bounded import size")
    with store.transaction(write=True) if not store.remote else nullcontext():
        # Keep each D1 bridge payload bounded; local batches remain nested in one
        # SQLite transaction and therefore roll back together on any failure.
        store.batch(statements[:3])
        batch, size = [], 0
        for statement in statements[3:]:
            amount = len(json.dumps(statement, ensure_ascii=False).encode())
            if amount > 768 * 1024:
                raise ValueError("Document metadata exceeds the bounded import statement size")
            if batch and (len(batch) >= 40 or size + amount > 768 * 1024):
                store.batch(batch)
                batch, size = [], 0
            batch.append(statement)
            size += amount
        store.batch(batch)
        # Readiness, provenance, membership and invalidation publish together.
        store.batch(final)
    return document_id


def register(app, services):
    def db():
        return services["db"]()

    @app.post("/api/v3/projects/{project_id}/imports")
    def initialize(project_id: str, body: dict):
        services["require_project"](project_id)
        total = body.get("total_files", 0)
        if not isinstance(total, int) or not 0 <= total <= 10000:
            raise HTTPException(422, "An import supports at most 10,000 files")
        identity = uuid.uuid4().hex
        name = str(body.get("name") or "Document collection")[:180]
        batch = services["new_batch"](project_id, name)
        db().execute("INSERT INTO v3_imports(id,project_id,batch_id,name,total_files,created_at) VALUES(?,?,?,?,?,?)", [identity, project_id, batch["id"], name, total, utcnow()])
        return {"id": identity, "batch_id": batch["id"], "status": "pending", "total_files": total}

    @app.get("/api/v3/projects/{project_id}/imports/{import_id}")
    def status(project_id: str, import_id: str):
        result = db().one("SELECT * FROM v3_imports WHERE id=? AND project_id=?", [import_id, project_id])
        if not result:
            raise HTTPException(404, "Import not found")
        result["errors"] = db().query("SELECT relative_path,error FROM v3_import_files WHERE import_id=? AND status='failed' LIMIT 100", [import_id])
        return result

    @app.post("/api/v3/projects/{project_id}/imports/{import_id}/archive")
    async def archive(project_id: str, import_id: str, file: UploadFile = File(...)):
        session = status(project_id, import_id)
        batch = {"id": session["batch_id"], "project_id": project_id}
        maximum = 1024 * 1024 * 1024
        with tempfile.TemporaryDirectory(dir=db().root) as working:
            source = Path(working) / "source.zip"
            size = 0
            with source.open("wb") as target:
                while part := await file.read(1024 * 1024):
                    size += len(part)
                    if size > 500 * 1024 * 1024:
                        raise HTTPException(413, "Archive transport exceeds 500 MiB")
                    target.write(part)
            try:
                bundle = zipfile.ZipFile(source)
            except zipfile.BadZipFile as error:
                raise HTTPException(422, "Upload a valid ZIP archive") from error
            with bundle:
                entries = [item for item in bundle.infolist() if not item.is_dir()]
                if len(entries) > 10000 or sum(item.file_size for item in entries) > maximum:
                    raise HTTPException(413, "Archive exceeds 10,000 files or 1 GiB expanded size")
                identities = set()
                for entry in entries:
                    name = entry.filename.replace("\\", "/")
                    relative = PurePosixPath(name)
                    if not name or relative.is_absolute() or ".." in relative.parts or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                        raise HTTPException(422, "Unsafe archive path, symlink or encrypted member")
                    if str(relative) in identities:
                        raise HTTPException(422, "Archive contains duplicate source paths")
                    identities.add(str(relative))
                    if entry.file_size > 500 * 1024 * 1024:
                        raise HTTPException(413, "An archive member exceeds 500 MiB")
                db().execute("UPDATE v3_imports SET status='running',total_files=? WHERE id=?", [len(entries), import_id])
                for number, entry in enumerate(entries):
                    path = Path(working) / (str(number) + Path(entry.filename).suffix)
                    digest = hashlib.sha256()
                    with bundle.open(entry) as member, path.open("wb") as target:
                        while chunk := member.read(1024 * 1024):
                            digest.update(chunk)
                            target.write(chunk)
                    old = db().one("SELECT f.status,c.status AS commit_status FROM v3_import_files f LEFT JOIN import_commits c ON c.document_id=f.document_id WHERE f.import_id=? AND f.relative_path=? AND f.sha256=?", [import_id, entry.filename, digest.hexdigest()])
                    if old and old["status"] == old["commit_status"] == "ready":
                        path.unlink()
                        continue
                    try:
                        document, _ = await services["ingest"](project_id, batch, path, Path(entry.filename).name, source_identity=entry.filename)
                        state, error = document["status"], document.get("error")
                        document_id = document["id"]
                    except (ValueError, OSError) as failure:
                        state, error, document_id = "failed", str(failure)[:600], None
                    db().execute("INSERT INTO v3_import_files(import_id,relative_path,sha256,document_id,status,error,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(import_id,relative_path,sha256) DO UPDATE SET document_id=excluded.document_id,status=excluded.status,error=excluded.error", [import_id, entry.filename, digest.hexdigest(), document_id, state, error, utcnow()])
                    path.unlink(missing_ok=True)
                    await asyncio.sleep(0)
        db().execute("UPDATE v3_imports SET completed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='ready'),failed_files=(SELECT COUNT(*) FROM v3_import_files WHERE import_id=? AND status='failed'),status='completed' WHERE id=?", [import_id, import_id, import_id])
        Workspace(db()).touch(project_id)
        return status(project_id, import_id)
