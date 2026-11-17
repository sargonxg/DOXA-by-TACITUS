"""Bounded archive intake and logical import sessions across HTTP file groups."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path, PurePosixPath
import stat
import tempfile
import uuid
import zipfile

from fastapi import File, HTTPException, UploadFile

from .store import utcnow
from .workspace import Workspace


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
                for entry in entries:
                    name = entry.filename.replace("\\", "/")
                    relative = PurePosixPath(name)
                    if relative.is_absolute() or ".." in relative.parts or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                        raise HTTPException(422, "Unsafe archive path, symlink or encrypted member")
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
                    old = db().one("SELECT status FROM v3_import_files WHERE import_id=? AND relative_path=? AND sha256=?", [import_id, entry.filename, digest.hexdigest()])
                    if old and old["status"] == "ready":
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
