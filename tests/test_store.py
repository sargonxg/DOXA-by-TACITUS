"""Durability, isolation and transaction behavior at the storage boundary."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from doxa.store import Store, utcnow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    result = Store(tmp_path)
    result.init()
    yield result
    result.close()


def test_batch_rolls_back_every_statement_when_later_statement_fails(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.batch([
            {"sql": "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", "params": ["p", "First", utcnow()]},
            {"sql": "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", "params": ["p", "Duplicate", utcnow()]},
        ])
    assert store.one("SELECT id FROM projects WHERE id='p'") is None


def test_restart_retains_metadata_and_exact_unicode_objects(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    first = Store(tmp_path)
    first.init()
    first.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", ["p", "🧭 corpus café", utcnow()])
    blocks = [{"id": "b1", "text": "  Exact ‘quotation’.\nUnicode 🧭.  "}]
    first.put_json("documents/d1/blocks.json", blocks)
    first.close()
    second = Store(tmp_path)
    second.init()
    try:
        assert second.one("SELECT * FROM projects WHERE id=?", ["p"])["name"] == "🧭 corpus café"
        assert second.get_json("documents/d1/blocks.json") == blocks
    finally:
        second.close()


def test_object_keys_cannot_escape_artifact_root_including_symlinks(store, tmp_path):
    for key in ("../secret", "../../secret", "/etc/passwd", "a/../../../secret"):
        with pytest.raises(ValueError, match="artifact"):
            store.object_path(key)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "objects").mkdir(exist_ok=True)
    (tmp_path / "objects" / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        store.put_bytes("escape/file.txt", b"not allowed")
    assert not (outside / "file.txt").exists()


def test_object_roundtrip_download_and_delete(store, tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00exact source\xff" * 100)
    store.put_path("source/original.bin", source)
    target = store.download("source/original.bin", tmp_path / "download" / "copy.bin")
    assert target.read_bytes() == source.read_bytes()
    store.delete_object("source/original.bin")
    with pytest.raises(FileNotFoundError):
        store.get_bytes("source/original.bin")


def test_concurrent_budget_updates_never_oversubscribe_single_ledger(store):
    month = "2026-11"
    store.execute("INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES(?,0,0)", [month])

    def reserve(_):
        return bool(store.query("UPDATE budget_ledger SET reserved_usd=reserved_usd+0.125 WHERE month=? AND reserved_usd+spent_usd+0.125<=1 RETURNING month", [month]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        granted = list(pool.map(reserve, range(40)))
    assert sum(granted) == 8
    assert store.one("SELECT reserved_usd FROM budget_ledger WHERE month=?", [month])["reserved_usd"] == 1


def test_sql_parameters_retain_literal_strings(store):
    title = "Robert'); DROP TABLE projects; --"
    store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", ["p", title, utcnow()])
    assert store.one("SELECT name FROM projects WHERE id=?", ["p"])["name"] == title
    assert store.one("SELECT COUNT(*) AS n FROM projects")["n"] == 1


def test_concurrent_atomic_object_replacements_leave_one_complete_artifact(store):
    values = [(f"writer-{i:02d}|"*2000).encode() for i in range(32)]

    def write(value):
        store.put_bytes("runs/shared-result.json", value)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, values))
    assert store.get_bytes("runs/shared-result.json") in values
    assert not list((store.root / "objects" / "runs").glob("*.tmp"))
