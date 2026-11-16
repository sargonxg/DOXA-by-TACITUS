import json

import pytest

from doxa.batches import compare_batches
from doxa.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    result = Store(tmp_path)
    result.init()
    result.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Research','2026-01-01')")
    yield result
    result.close()


def batch(store, batch_id, date):
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,'p',?,?)", [batch_id, batch_id, date])


def document(store, doc_id, batch_id, *, filename="source.txt", source_url="", digest="hash-one", date="2026-01-01", metadata=None):
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,filename,mime_type,sha256,status,created_at,source_url,metadata_json) VALUES(?,'p',?,?,?,'text/plain',?,'ready',?,?,?)",
                  [doc_id, batch_id, doc_id, filename, digest, date, source_url, json.dumps(metadata or {})])
    store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?)", [batch_id, doc_id])


def test_no_batches_and_first_batch_have_explicit_empty_baseline(store):
    empty = compare_batches(store, "p")
    assert empty["before_batch"] is None and empty["after_batch"] is None
    assert all(count == 0 for count in empty["totals"].values())
    batch(store, "first", "2026-01-01")
    document(store, "a", "first", metadata={"attribution": "Source attribution"})
    result = compare_batches(store, "p")
    assert result["before_batch"] is None
    assert result["after_batch"]["id"] == "first"
    assert result["totals"] == {"added": 1, "changed": 0, "unchanged": 0, "not_in_later_batch": 0}
    assert result["added"][0]["metadata"]["attribution"] == "Source attribution"
    assert "metadata_json" not in result["added"][0]


def test_latest_two_batches_compare_source_hashes_and_absence(store):
    batch(store, "old", "2026-01-01")
    batch(store, "new", "2026-02-01")
    document(store, "old-changed", "old", filename="policy.txt", digest="old")
    document(store, "new-changed", "new", filename="policy.txt", digest="new")
    document(store, "same", "old", filename="same.txt")
    store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES('new','same')")
    document(store, "absent", "old", filename="absent.txt")
    document(store, "added", "new", filename="added.txt")
    result = compare_batches(store, "p")
    assert result["before_batch"]["id"] == "old" and result["after_batch"]["id"] == "new"
    assert result["totals"] == {"added": 1, "changed": 1, "unchanged": 1, "not_in_later_batch": 1}
    assert result["changed"][0]["before"]["sha256"] == "old"
    assert result["changed"][0]["after"]["sha256"] == "new"
    assert result["unchanged"][0]["id"] == "same"
    assert result["not_in_later_batch"][0]["id"] == "absent"
    assert "does not imply deletion" in result["note"]


def test_source_url_takes_precedence_and_filename_matching_casefolds(store):
    batch(store, "old", "2026-01-01")
    batch(store, "new", "2026-02-01")
    document(store, "url-old", "old", filename="first.txt", source_url="https://example.org/policy")
    document(store, "url-new", "new", filename="renamed.txt", source_url="https://example.org/policy", digest="changed")
    document(store, "case-old", "old", filename="POLICY.TXT")
    document(store, "case-new", "new", filename="policy.txt")
    result = compare_batches(store, "p")
    assert result["totals"] == {"added": 0, "changed": 1, "unchanged": 1, "not_in_later_batch": 0}


def test_url_paths_are_case_sensitive_and_not_guessed_equivalent(store):
    batch(store, "old", "2026-01-01")
    batch(store, "new", "2026-02-01")
    document(store, "a", "old", source_url="https://example.org/Policy")
    document(store, "b", "new", source_url="https://example.org/policy")
    result = compare_batches(store, "p")
    assert result["totals"]["added"] == 1 and result["totals"]["not_in_later_batch"] == 1


def test_latest_source_version_within_batch_is_deterministic(store):
    batch(store, "old", "2026-01-01")
    batch(store, "new", "2026-02-01")
    document(store, "original", "old", digest="original")
    document(store, "new-a", "new", digest="earlier", date="2026-02-01")
    document(store, "new-z", "new", digest="latest", date="2026-02-01")
    document(store, "older-import", "new", digest="older", date="2026-01-20")
    result = compare_batches(store, "p")
    assert len(result["changed"]) == 1
    assert result["changed"][0]["after"]["id"] == "new-z"


def test_explicit_older_after_uses_its_previous_batch_not_future(store):
    for name, date in [("a", "2026-01-01"), ("b", "2026-02-01"), ("c", "2026-03-01")]:
        batch(store, name, date)
        document(store, f"doc-{name}", name, digest=name)
    result = compare_batches(store, "p", after_batch="b")
    assert result["before_batch"]["id"] == "a" and result["after_batch"]["id"] == "b"
    assert compare_batches(store, "p", after_batch="a")["before_batch"] is None
    assert compare_batches(store, "p", before_batch="a")["after_batch"]["id"] == "c"


def test_batch_and_project_isolation(store):
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('other','Other','2026-01-01')")
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES('foreign','other','Foreign','2026-01-01')")
    with pytest.raises(LookupError):
        compare_batches(store, "p", after_batch="foreign")
    with pytest.raises(LookupError):
        compare_batches(store, "p", before_batch="missing")
    with pytest.raises(LookupError):
        compare_batches(store, "missing")


def test_replaying_same_batch_is_unchanged(store):
    batch(store, "a", "2026-01-01")
    document(store, "source", "a")
    result = compare_batches(store, "p", "a", "a")
    assert result["totals"] == {"added": 0, "changed": 0, "unchanged": 1, "not_in_later_batch": 0}
