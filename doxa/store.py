"""Durable SQLite/D1 metadata and filesystem/R2 artifacts behind narrow interfaces."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


def utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("DOXA_DATA_DIR", "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.remote = os.environ.get("DOXA_CLOUD_STATE_URL", "").rstrip("/")
        self.token = os.environ.get("DOXA_SERVICE_TOKEN", "")
        self.lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self._transaction_depth = 0
        self._transaction_write = False
        if not self.remote:
            self._db = sqlite3.connect(self.root / "doxa.sqlite3", check_same_thread=False, timeout=30)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA busy_timeout=30000")
        elif not self.token:
            raise RuntimeError("DOXA_SERVICE_TOKEN is required for cloud persistence")

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "x-doxa-service-token": self.token}

    def init(self) -> None:
        from .migrations import migrate
        migrate(self)
        if self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
        self.execute("INSERT INTO batch_members(batch_id,document_id) SELECT batch_id,id FROM documents WHERE 1 ON CONFLICT(batch_id,document_id) DO NOTHING")
        # D1 and SQLite both support FTS5. Failure is explicit in capabilities, never a hidden search claim.
        self.fts = True
        try:
            self.execute("CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(id UNINDEXED, project_id UNINDEXED, title, body)")
        except Exception:
            self.fts = False

    @contextmanager
    def transaction(self, *, write: bool = False):
        """One local SQLite snapshot across nested Store calls.

        A D1 transaction cannot span HTTP requests. Remote callers must use one
        batch for writes and verify their project revision around compound reads.
        """
        if self.remote:
            raise RuntimeError("Remote transactions require a single Store.batch request")
        assert self._db is not None
        with self.lock:
            outer = self._transaction_depth == 0
            if not outer and write and not self._transaction_write:
                raise RuntimeError("Cannot upgrade a read snapshot to a write transaction")
            query_only = None
            if outer:
                if not write:
                    query_only = self._db.execute("PRAGMA query_only").fetchone()[0]
                    self._db.execute("PRAGMA query_only=ON")
                try:
                    self._db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                except BaseException:
                    if query_only is not None:
                        self._db.execute(f"PRAGMA query_only={int(query_only)}")
                    raise
                self._transaction_write = write
            self._transaction_depth += 1
            try:
                yield self
                if outer:
                    self._db.commit()
            except BaseException:
                if outer:
                    self._db.rollback()
                raise
            finally:
                self._transaction_depth -= 1
                if outer:
                    self._transaction_write = False
                    if query_only is not None:
                        self._db.execute(f"PRAGMA query_only={int(query_only)}")

    def batch(self, statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not statements:
            return []
        if self.remote:
            response = httpx.post(self.remote + "/internal/storage/sql", json={"statements": statements},
                                  headers=self.headers(), timeout=60)
            response.raise_for_status()
            return response.json()["results"]
        assert self._db is not None
        with self.lock:
            if self._transaction_depth:
                if not self._transaction_write:
                    raise RuntimeError("Writes are forbidden inside a read snapshot")
                results = []
                for statement in statements:
                    cur = self._db.execute(statement["sql"], statement.get("params", []))
                    results.append({"rows": [dict(row) for row in cur.fetchall()] if cur.description else [], "changes": max(cur.rowcount, 0)})
                return results
            try:
                self._db.execute("BEGIN IMMEDIATE")
                results = []
                for statement in statements:
                    cur = self._db.execute(statement["sql"], statement.get("params", []))
                    rows = [dict(row) for row in cur.fetchall()] if cur.description else []
                    results.append({"rows": rows, "changes": max(cur.rowcount, 0)})
                self._db.commit()
                return results
            except Exception:
                self._db.rollback()
                raise

    def query(self, sql: str, params: list | tuple = ()) -> list[dict]:
        # Read-only queries do not acquire SQLite's RESERVED writer lock.
        if not self.remote and sql.lstrip().upper().startswith(("SELECT", "WITH", "EXPLAIN", "PRAGMA")):
            assert self._db is not None
            with self.lock:
                return [dict(row) for row in self._db.execute(sql, params).fetchall()]
        return self.batch([{"sql": sql, "params": list(params)}])[0]["rows"]

    def one(self, sql: str, params: list | tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: list | tuple = ()) -> int:
        return self.batch([{"sql": sql, "params": list(params)}])[0]["changes"]

    def index_document(self, document_id: str, project_id: str, title: str, blocks: list[dict]) -> None:
        """Bound each FTS row/bridge request, including for very long documents.

        Search is indexed in fragments; the API intersects document identities
        for multi-term queries so terms may occur on different pages.
        """
        if not self.fts:
            return
        self.execute("DELETE FROM document_search WHERE id=?", [document_id])
        buffer = ""

        def write(value: str) -> None:
            self.execute("INSERT INTO document_search(id,project_id,title,body) VALUES(?,?,?,?)",
                         [document_id, project_id, title, value])

        for block in blocks:
            text = block["text"]
            start = 0
            while start < len(text):
                end = min(start + 6000, len(text))
                if end < len(text):
                    boundary = text.rfind(" ", start + 3000, end)
                    if boundary > start:
                        end = boundary + 1
                fragment = text[start:end]
                if buffer and len(buffer) + len(fragment) + 1 > 6000:
                    write(buffer)
                    buffer = ""
                buffer += ("\n" if buffer else "") + fragment
                start = end
        if buffer or not blocks:
            write(buffer)

    def object_path(self, key: str) -> Path:
        path = (self.root / "objects" / key).resolve()
        if not path.is_relative_to(self.root / "objects"):
            raise ValueError("Invalid artifact key")
        return path

    def put_bytes(self, key: str, value: bytes, content_type: str = "application/octet-stream") -> None:
        if self.remote:
            if len(value) > 32 * 1024 * 1024:
                with tempfile.TemporaryDirectory(dir=self.root) as folder:
                    path = Path(folder) / "artifact"
                    path.write_bytes(value)
                    self.put_path(key, path, content_type)
                return
            response = httpx.put(self.remote + "/internal/storage/objects/" + quote(key, safe="/"),
                                 content=value, headers={**self.headers(), "content-type": content_type}, timeout=120)
            response.raise_for_status()
            return
        path = self.object_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as output:
            temp = Path(output.name)
            output.write(value)
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def put_json(self, key: str, value: Any) -> None:
        self.put_bytes(key, json.dumps(value, ensure_ascii=False).encode(), "application/json")

    def get_bytes(self, key: str) -> bytes:
        if self.remote:
            response = httpx.get(self.remote + "/internal/storage/objects/" + quote(key, safe="/"),
                                 headers=self.headers(), timeout=120)
            response.raise_for_status()
            return response.content
        return self.object_path(key).read_bytes()

    def get_json(self, key: str) -> Any:
        return json.loads(self.get_bytes(key))

    def put_path(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        if self.remote:
            if path.stat().st_size > 32 * 1024 * 1024:
                self._put_multipart(key, path, content_type)
                return
            def chunks():
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        yield chunk
            response = httpx.put(self.remote + "/internal/storage/objects/" + quote(key, safe="/"),
                                 content=chunks(), headers={**self.headers(), "content-type": content_type,
                                                          "content-length": str(path.stat().st_size)}, timeout=300)
            response.raise_for_status()
            return
        import shutil
        destination = self.object_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as output:
            temp = Path(output.name)
        try:
            shutil.copyfile(path, temp)
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)

    def _put_multipart(self, key: str, path: Path, content_type: str) -> None:
        """Keep large generated artifacts below the gateway's request-body cap."""
        base = self.remote + "/internal/storage/multipart"
        with httpx.Client(headers=self.headers(), timeout=180) as client:
            response = client.post(base + "/init", json={"key": key, "content_type": content_type})
            response.raise_for_status()
            upload_id = response.json()["upload_id"]
            parts = []
            try:
                with path.open("rb") as source:
                    number = 1
                    while chunk := source.read(8 * 1024 * 1024):
                        part = client.put(base + "/" + quote(upload_id, safe="") + f"/parts/{number}",
                                          params={"key": key}, content=chunk,
                                          headers={"content-type": "application/octet-stream"})
                        part.raise_for_status()
                        parts.append(part.json())
                        number += 1
                complete = client.post(base + "/complete", json={"key": key, "upload_id": upload_id, "parts": parts})
                complete.raise_for_status()
            except BaseException:
                try:
                    client.post(base + "/abort", json={"key": key, "upload_id": upload_id}).raise_for_status()
                except httpx.HTTPError:
                    pass
                raise
    def download(self, key: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.remote:
            import shutil
            shutil.copyfile(self.object_path(key), target)
            return target
        with httpx.stream("GET", self.remote + "/internal/storage/objects/" + quote(key, safe="/"),
                          headers=self.headers(), timeout=300) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
        return target

    def delete_object(self, key: str) -> None:
        if self.remote:
            response = httpx.delete(self.remote + "/internal/storage/objects/" + quote(key, safe="/"),
                                    headers=self.headers(), timeout=60)
            response.raise_for_status()
        else:
            self.object_path(key).unlink(missing_ok=True)

    def enqueue(self, run_id: str) -> None:
        response = httpx.post(self.remote + "/internal/storage/enqueue", json={"run_id": run_id},
                              headers=self.headers(), timeout=30)
        response.raise_for_status()

    def enqueue_report(self, job_id: str) -> None:
        response = httpx.post(self.remote + "/internal/storage/enqueue", json={"kind": "report", "job_id": job_id},
                              headers=self.headers(), timeout=30)
        response.raise_for_status()

    def close(self) -> None:
        if self._db:
            self._db.close()
