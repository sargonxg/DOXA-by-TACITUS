"""Crash boundaries, concurrent snapshots and ordered schema upgrades."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from doxa.imports import commit_document
from doxa.store import Store, utcnow
from doxa.workspace import Workspace


def legacy_workspace(path):
    connection = sqlite3.connect(path / "doxa.sqlite3")
    root = Path(__file__).parents[1] / "doxa"
    connection.executescript((root / "schema.sql").read_text() + (root / "v3_schema.sql").read_text())
    connection.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Retain me','2026-01-01')")
    connection.commit()
    connection.close()


def test_upgrade_has_checked_backup_is_idempotent_and_preserves_original(tmp_path):
    legacy_workspace(tmp_path)
    store = Store(tmp_path)
    store.init()
    assert store.one("SELECT MAX(version) n FROM schema_versions")["n"] == 4
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    original = sqlite3.connect(backups[0])
    assert original.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == 3
    assert original.execute("SELECT name FROM projects WHERE id='p'").fetchone()[0] == "Retain me"
    assert original.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert backups[0].stat().st_mode & 0o077 == 0
    original.close()
    store.init()
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    store.close()


def test_future_schema_is_rejected_before_any_schema_write(tmp_path):
    legacy_workspace(tmp_path)
    connection = sqlite3.connect(tmp_path / "doxa.sqlite3")
    connection.execute("INSERT INTO schema_versions VALUES(99,'future')")
    connection.commit()
    connection.close()
    before = (tmp_path / "doxa.sqlite3").read_bytes()
    store = Store(tmp_path)
    with pytest.raises(RuntimeError, match="newer"):
        store.init()
    assert store.one("SELECT name FROM sqlite_master WHERE name='upload_sessions'") is None
    assert not (tmp_path / "backups").exists()
    store.close()
    assert (tmp_path / "doxa.sqlite3").read_bytes() == before


def test_failed_upgrade_rolls_back_version_and_retries_from_backup(tmp_path, monkeypatch):
    import doxa.migrations as migrations
    legacy_workspace(tmp_path)
    store = Store(tmp_path)
    original = migrations.V4_SQL
    monkeypatch.setattr(migrations, "V4_SQL", original + ";INSERT INTO missing_table VALUES('fail');")
    with pytest.raises(sqlite3.OperationalError):
        store.init()
    assert store.one("SELECT MAX(version) n FROM schema_versions")["n"] == 3
    assert store.one("SELECT name FROM sqlite_master WHERE name='upload_sessions'") is None
    monkeypatch.setattr(migrations, "V4_SQL", original)
    store.init()
    assert store.one("SELECT name FROM sqlite_master WHERE name='upload_sessions'")
    store.close()


def test_read_transaction_stays_coherent_across_an_independent_writer(tmp_path):
    reader = Store(tmp_path)
    reader.init()
    reader.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Before',?)", [utcnow()])
    Workspace(reader).touch("p")
    with reader.transaction():
        before = Workspace(reader).revision("p")
        subprocess.run([sys.executable, "-c", "from doxa.store import Store; from doxa.workspace import Workspace; import sys; s=Store(sys.argv[1]); s.init(); s.batch([{'sql':\"UPDATE projects SET name='After' WHERE id='p'\",'params':[]},Workspace.touch_statement('p')]); s.close()", str(tmp_path)], check=True, timeout=10)
        assert reader.one("SELECT name FROM projects WHERE id='p'")["name"] == "Before"
        assert Workspace(reader).revision("p") == before
    assert reader.one("SELECT name FROM projects WHERE id='p'")["name"] == "After"
    assert Workspace(reader).revision("p") != before
    reader.close()


def test_read_snapshot_forbids_cte_writes_and_restores_connection_afterwards(tmp_path):
    store = Store(tmp_path)
    store.init()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with store.transaction():
            store.query("WITH chosen AS (SELECT 'p' id) INSERT INTO projects(id,name,created_at) SELECT id,'Forbidden','now' FROM chosen")
    assert store.one("SELECT COUNT(*) n FROM projects")["n"] == 0
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Allowed','now')")
    assert store.one("SELECT name FROM projects")["name"] == "Allowed"
    store.close()


def document_values():
    return dict(id="d", project_id="p", batch_id="b", title="Title", author="Author", institution="", document_type="document", published_at=None,
        filename="source.txt", mime_type="text/plain", sha256="abc", status="ready", word_count=2, page_count=0,
        source_url="", license="", object_key="original", blocks_key="blocks", created_at=utcnow(), error=None, metadata_json=json.dumps({"source_identity": "source.txt"}))


def test_local_import_late_failure_rolls_back_document_index_and_revision(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.init()
    original = store.batch
    def fail_publication(statements):
        if any("UPDATE documents SET status=" in item["sql"] for item in statements):
            raise OSError("simulated storage failure after index")
        return original(statements)
    monkeypatch.setattr(store, "batch", fail_publication)
    with pytest.raises(OSError):
        commit_document(store, document_values(), [{"id": "b1", "text": "Exact text"}], "source.txt")
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM document_search")["n"] == 0
    assert Workspace(store).revision("p") == "0"
    monkeypatch.setattr(store, "batch", original)
    assert commit_document(store, document_values(), [{"id": "b1", "text": "Exact text"}], "source.txt") == "d"
    assert store.one("SELECT status FROM documents WHERE id='d'")["status"] == "ready"
    assert store.one("SELECT source_identity FROM v3_sources WHERE document_id='d'")["source_identity"] == "source.txt"
    store.close()


def test_bounded_remote_style_import_repairs_partial_index_before_ready(tmp_path):
    store = Store(tmp_path)
    store.init()
    class Bridge:
        remote, fts = True, True
        calls, fail = 0, True
        def one(self, *args):
            return store.one(*args)
        def batch(self, statements):
            self.calls += 1
            if self.fail and self.calls == 3:
                raise ConnectionError("lost bridge response")
            return store.batch(statements)
    bridge = Bridge()
    blocks = [{"id": "b1", "text": "source words " * 26000}]
    with pytest.raises(ConnectionError):
        commit_document(bridge, document_values(), blocks, "source.txt")
    assert store.one("SELECT status FROM documents WHERE id='d'")["status"] == "importing"
    assert store.one("SELECT COUNT(*) n FROM document_search")["n"] > 0
    clause, params = Workspace(store)._document_scope("p", {})
    assert store.one("SELECT COUNT(*) n FROM documents d WHERE " + " AND ".join(clause), params)["n"] == 0
    bridge.fail = False
    commit_document(bridge, document_values(), blocks, "source.txt")
    assert store.one("SELECT status FROM documents WHERE id='d'")["status"] == "ready"
    assert store.one("SELECT COUNT(*) n FROM batch_members")["n"] == 1
    count = store.one("SELECT COUNT(*) n FROM document_search")["n"]
    commit_document(bridge, document_values(), blocks, "source.txt")
    assert store.one("SELECT COUNT(*) n FROM document_search")["n"] == count
    store.close()
