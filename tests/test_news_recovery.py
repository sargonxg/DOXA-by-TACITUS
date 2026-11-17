"""SQLite interruption/recovery checks using synthetic source transport fixtures."""

import copy
import hashlib
import json
import sqlite3

import pytest

from doxa.news import import_news
from doxa.store import Store, utcnow


PROJECT = "news-recovery-project"


def block_hash(blocks):
    raw = json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def synthetic_source():
    # No spaces near the 6K fragment boundaries: the expected fragments are
    # explicit, while the complete source also exercises Unicode indexing.
    blocks = [
        {"id": "recovery-source-b1", "text": "opening keyword " + "α" * 12900 + " endingmarker", "paragraph": 1, "page": None},
        {"id": "recovery-source-b2", "text": "Second paragraph closes this synthetic transport source.", "paragraph": 2, "page": None},
    ]
    return {
        "id": "synthetic-recovery-source", "title": "Synthetic upstream title", "author": "Fixture author",
        "institution": "Synthetic transport fixtures", "published_at": "2024-01-01", "modified_at": "2024-01-02",
        "source_url": "https://example.invalid/synthetic-news", "license": "TEST-FIXTURE-ONLY", "synthetic": True,
        "blocks": blocks, "content_sha256": block_hash(blocks), "word_count": sum(len(b["text"].split()) for b in blocks),
    }


def write_source(root, source):
    base = root / "corpus" / "news"
    metadata = base / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    sidecar = metadata / f"{source['id']}.json"
    sidecar.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    entry = {"id": source["id"], "metadata_path": f"metadata/{source['id']}.json", "content_sha256": source["content_sha256"]}
    (base / "manifest.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")


def original_identity(source):
    return hashlib.sha256(f"{PROJECT}:{source['id']}".encode()).hexdigest()[:32]


def expected_fragments(source):
    first, second = [block["text"] for block in source["blocks"]]
    return [first[:6000], first[6000:12000], first[12000:] + "\n" + second]


def search_rows(store, identity, *, rowids=False):
    columns = "rowid,id,project_id,title,body" if rowids else "id,project_id,title,body"
    return store.query(f"SELECT {columns} FROM document_search WHERE id=? ORDER BY rowid", [identity])


def assert_complete_index(store, identity, title, source):
    rows = search_rows(store, identity)
    assert [row["body"] for row in rows] == expected_fragments(source)
    assert all(row["id"] == identity and row["project_id"] == PROJECT and row["title"] == title for row in rows)
    assert len(rows) == len({row["body"] for row in rows}) == 3
    assert all(0 < len(row["body"]) <= 6000 for row in rows)


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path / "store")
    store.init()
    if not store.fts:
        store.close()
        pytest.skip("SQLite FTS5 is required for source-index recovery checks")
    store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [PROJECT, "Recovery fixture", utcnow()])
    root, source = tmp_path / "corpus-root", synthetic_source()
    write_source(root, source)
    try:
        yield store, root, source
    finally:
        store.close()


def test_source_link_failure_rolls_back_document_index_and_membership_then_retry_succeeds(recovery):
    store, root, source = recovery
    identity = original_identity(source)
    store.execute("""CREATE TRIGGER interrupt_source_link BEFORE INSERT ON v3_sources BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM documents WHERE id=NEW.document_id)
                    OR (SELECT COUNT(*) FROM document_search WHERE id=NEW.document_id)!=3
                    THEN RAISE(ABORT,'source link did not follow document and FTS writes') END;
        SELECT RAISE(ABORT,'simulated source-link interruption');
        END""")

    with pytest.raises(sqlite3.IntegrityError, match="simulated source-link interruption"):
        import_news(store, PROJECT, root, use_saved_showcase=False)

    assert store.one("SELECT id FROM documents WHERE id=?", [identity]) is None
    assert search_rows(store, identity) == []
    assert store.query("SELECT * FROM v3_sources") == []
    assert store.query("SELECT * FROM batch_members") == []
    store.execute("DROP TRIGGER interrupt_source_link")

    imported = import_news(store, PROJECT, root, use_saved_showcase=False)
    assert imported["documents"] == 1 and imported["saved_results"] is False
    assert_complete_index(store, identity, source["title"], source)
    assert store.one("SELECT content_hash FROM v3_sources WHERE document_id=?", [identity])["content_hash"] == source["content_sha256"]
    assert store.query("SELECT document_id FROM batch_members WHERE batch_id=?", [imported["batch"]["id"]]) == [{"document_id": identity}]
    assert store.query("SELECT * FROM runs") == store.query("SELECT * FROM budget_reservations") == []


@pytest.mark.parametrize("legacy_index", ["missing", "partial_and_duplicate"])
def test_legacy_document_repairs_source_link_and_index_without_rewriting_stored_source(recovery, legacy_index):
    store, root, source = recovery
    identity, key = original_identity(source), "legacy/source-blocks.json"
    stored_title = "Locally annotated title must survive recovery"
    metadata = {key: value for key, value in source.items() if key != "blocks"}
    metadata.update(source_id=source["id"], local_annotation={"reviewer": "Fixture reviewer", "preserve": True})
    store.put_json(key, source["blocks"])
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", ["legacy-batch", PROJECT, "Interrupted legacy fixture", utcnow()])
    store.execute("""INSERT INTO documents(id,project_id,batch_id,title,author,filename,mime_type,sha256,status,
                    word_count,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  [identity, PROJECT, "legacy-batch", stored_title, "Locally credited author", source["id"] + ".txt", "text/plain",
                   source["content_sha256"], "ready", source["word_count"], key, utcnow(), json.dumps(metadata, ensure_ascii=False)])
    if legacy_index == "partial_and_duplicate":
        for body in [source["blocks"][0]["text"][:300], source["blocks"][0]["text"][:300], "stale fragment must disappear"]:
            store.execute("INSERT INTO document_search(id,project_id,title,body) VALUES(?,?,?,?)", [identity, PROJECT, "Stale index title", body])
    original_document = store.one("SELECT * FROM documents WHERE id=?", [identity])
    original_bytes = store.object_path(key).read_bytes()

    first = import_news(store, PROJECT, root, use_saved_showcase=False)
    assert first["documents"] == 1
    assert store.one("SELECT * FROM documents WHERE id=?", [identity]) == original_document
    assert store.object_path(key).read_bytes() == original_bytes
    assert_complete_index(store, identity, stored_title, source)
    link = store.one("SELECT * FROM v3_sources WHERE document_id=?", [identity])
    assert link["source_identity"] == source["id"] and link["content_hash"] == source["content_sha256"]
    first_index = search_rows(store, identity)

    assert import_news(store, PROJECT, root, use_saved_showcase=False) == first
    assert search_rows(store, identity) == first_index
    assert store.one("SELECT * FROM documents WHERE id=?", [identity]) == original_document
    assert store.one("SELECT * FROM v3_sources WHERE document_id=?", [identity]) == link
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM v3_sources")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM batch_members WHERE batch_id=?", [first["batch"]["id"]])["n"] == 1


def test_revised_current_source_preserves_previous_immutable_version_and_search_rows(recovery):
    store, root, source = recovery
    first = import_news(store, PROJECT, root, use_saved_showcase=False)
    old_id = original_identity(source)
    old_document = store.one("SELECT * FROM documents WHERE id=?", [old_id])
    old_link = store.one("SELECT * FROM v3_sources WHERE document_id=?", [old_id])
    old_index = search_rows(store, old_id, rowids=True)
    old_blocks = store.object_path(old_document["blocks_key"]).read_bytes()

    revised = copy.deepcopy(source)
    revised["title"] = "A revised upstream title"
    revised["modified_at"] = "2024-02-01"
    revised["blocks"][1]["text"] += " This revision has its own immutable source version."
    revised["content_sha256"] = block_hash(revised["blocks"])
    revised["word_count"] = sum(len(block["text"].split()) for block in revised["blocks"])
    write_source(root, revised)
    second = import_news(store, PROJECT, root, use_saved_showcase=False)
    current_id = hashlib.sha256(f"{PROJECT}:{source['id']}:{revised['content_sha256']}".encode()).hexdigest()[:32]

    assert second["documents"] == 1 and second["batch"]["id"] != first["batch"]["id"]
    assert current_id != old_id
    assert store.one("SELECT * FROM documents WHERE id=?", [old_id]) == old_document
    assert store.one("SELECT * FROM v3_sources WHERE document_id=?", [old_id]) == old_link
    assert search_rows(store, old_id, rowids=True) == old_index
    assert store.object_path(old_document["blocks_key"]).read_bytes() == old_blocks
    assert_complete_index(store, current_id, revised["title"], revised)
    assert store.query("SELECT document_id FROM batch_members WHERE batch_id=?", [second["batch"]["id"]]) == [{"document_id": current_id}]
    assert store.query("SELECT document_id FROM batch_members WHERE batch_id=?", [first["batch"]["id"]]) == [{"document_id": old_id}]
    assert import_news(store, PROJECT, root, use_saved_showcase=False) == second
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 2
    assert store.one("SELECT COUNT(*) n FROM v3_sources")["n"] == 2
    assert search_rows(store, old_id, rowids=True) == old_index
    assert_complete_index(store, current_id, revised["title"], revised)


def test_staged_source_interruption_repairs_committed_prefix_without_duplicate_fragments(recovery, monkeypatch):
    import doxa.news as news

    store, root, source = recovery
    identity = original_identity(source)
    monkeypatch.setattr(news, "MAX_IMPORT_BATCH_STATEMENTS", 2)
    original_batch, batch_lengths = store.batch, []
    interrupted = False
    final_fragment = expected_fragments(source)[-1]

    def interrupt_later_fts_batch(statements):
        nonlocal interrupted
        batch_lengths.append(len(statements))
        if not interrupted and any(statement["sql"].startswith("INSERT INTO document_search")
                                   and statement["params"][-1] == final_fragment for statement in statements):
            interrupted = True
            raise sqlite3.OperationalError("simulated later FTS batch interruption")
        return original_batch(statements)

    monkeypatch.setattr(store, "batch", interrupt_later_fts_batch)
    with pytest.raises(sqlite3.OperationalError, match="simulated later FTS batch interruption"):
        import_news(store, PROJECT, root, use_saved_showcase=False)

    assert interrupted
    saved_document = store.one("SELECT * FROM documents WHERE id=?", [identity])
    assert saved_document is not None
    assert [row["body"] for row in search_rows(store, identity)] == expected_fragments(source)[:2]
    assert store.query("SELECT * FROM v3_sources") == store.query("SELECT * FROM batch_members") == []

    imported = import_news(store, PROJECT, root, use_saved_showcase=False)
    assert_complete_index(store, identity, source["title"], source)
    assert store.one("SELECT * FROM documents WHERE id=?", [identity]) == saved_document
    assert store.one("SELECT content_hash FROM v3_sources WHERE document_id=?", [identity])["content_hash"] == source["content_sha256"]
    assert store.query("SELECT document_id FROM batch_members WHERE batch_id=?", [imported["batch"]["id"]]) == [{"document_id": identity}]
    assert import_news(store, PROJECT, root, use_saved_showcase=False) == imported
    assert_complete_index(store, identity, source["title"], source)
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM v3_sources")["n"] == 1
    assert batch_lengths and max(batch_lengths) <= 2
