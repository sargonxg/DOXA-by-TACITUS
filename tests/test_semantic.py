import hashlib
import re

import pytest

pytest.importorskip("numpy", reason="Semantic retrieval belongs to the optional CPU extra")

from doxa.semantic import SemanticIndex, SemanticIndexError  # noqa: E402
from doxa.store import Store, utcnow  # noqa: E402


class TinyRuntime:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        return [[1.0, 0.0] + [0.0] * 382 if "threat" in text.lower() else [0.0, 1.0] + [0.0] * 382 for text in texts]

    def embed_windows(self, texts):
        self.calls += 1
        windows = []
        for index, text in enumerate(texts):
            offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
            for start in range(0, len(offsets), 200):
                left = offsets[start][0]
                right = offsets[min(start + 200, len(offsets)) - 1][1]
                quote = text[left:right]
                windows.append({"input_index": index, "start": left, "end": right, "text": quote,
                                "embedding": self.embed([quote])[0]})
        return {"windows": windows}


def document(store, identity, text, project="p"):
    key = f"documents/{identity}/blocks.json"
    store.put_json(key, [{"id": "block-" + identity, "text": text, "paragraph": 1}])
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,filename,mime_type,sha256,status,blocks_key,created_at,author) VALUES(?,?,? ,?,?,?,?,'ready',?,?,?)",
                  [identity, project, "batch", "Source " + identity, identity + ".txt", "text/plain", hashlib.sha256(text.encode()).hexdigest(), key, utcnow(), "Author " + identity])


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path)
    result.init()
    result.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Test',?)", [utcnow()])
    yield result
    result.close()


def test_persistent_index_ranks_and_cites_exact_sources_then_reuses_after_restart(store):
    document(store, "a", "The newspaper condemns threats against Jewish residents.")
    document(store, "b", "The annual education budget increases school funding.")
    runtime = TinyRuntime()
    index = SemanticIndex(store, runtime)
    built = index.build("p")
    assert built["documents"] == 2 and built["passages"] == 2 and runtime.calls == 2
    query = SemanticIndex(store, runtime).search("p", "threat", 2)
    assert query["hits"][0]["document_id"] == "a"
    for hit in query["hits"]:
        source = store.get_json(f"documents/{hit['document_id']}/blocks.json")[0]["text"]
        assert source[hit["start"]:hit["end"]] == hit["quote"]
    reloaded = SemanticIndex(store, runtime).build("p")
    assert reloaded["cached_documents"] == 2 and runtime.calls == 2
    assert reloaded["index_id"] == built["index_id"]
    filtered = index.search("p", "threat", 20, document_ids=["b"])
    assert filtered["documents_available"] == 1 and filtered["documents_indexed"] == 1
    assert {hit["document_id"] for hit in filtered["hits"]} == {"b"}


def test_partial_build_merges_and_search_discloses_new_or_changed_documents(store):
    document(store, "a", "Threats are condemned.")
    runtime = TinyRuntime()
    index = SemanticIndex(store, runtime)
    index.build("p")
    document(store, "b", "School budgets increase.")
    partial = index.search("p", "threat")
    assert partial["unindexed_documents"] == 1 and partial["documents_indexed"] == 1
    assert index.build("p", ["b"])["documents"] == 2
    store.execute("UPDATE documents SET sha256='changed' WHERE id='a'")
    stale = index.search("p", "threat")
    assert stale["stale_documents"] == 1 and all(hit["document_id"] != "a" for hit in stale["hits"])
    with pytest.raises(SemanticIndexError, match="ready documents"):
        index.build("p", ["another-project-source"])


def test_changed_source_quote_and_corrupt_vector_fail_then_explicit_build_repairs(store):
    document(store, "a", "Threats are condemned.")
    runtime = TinyRuntime()
    index = SemanticIndex(store, runtime)
    index.build("p")
    store.put_json("documents/a/blocks.json", [{"id": "block-a", "text": "A changed paragraph.", "paragraph": 1}])
    with pytest.raises(SemanticIndexError, match="citation differs"):
        index.search("p", "threat")
    index.build("p")
    manifest = store.get_json(index._prefix("p") + "/manifest.json")
    vector_key = manifest["documents"][0]["chunks"][0]["vectors_key"]
    store.put_bytes(vector_key, b"corrupt")
    with pytest.raises(SemanticIndexError, match="checksum"):
        SemanticIndex(store, runtime).search("p", "changed")
    repaired = SemanticIndex(store, runtime).build("p")
    assert repaired["cached_documents"] == 0
    assert SemanticIndex(store, runtime).search("p", "changed")["hits"]


def test_index_cannot_cross_projects_or_exceed_search_bounds(store):
    document(store, "a", "Threats are condemned.")
    runtime = TinyRuntime()
    index = SemanticIndex(store, runtime)
    with pytest.raises(SemanticIndexError, match="Build this project's"):
        index.search("p", "threat")
    with pytest.raises(SemanticIndexError, match="Project does not exist"):
        index.build("missing")
    index.build("p")
    with pytest.raises(SemanticIndexError, match="between 1 and 100"):
        index.search("p", "threat", 101)


def test_explicit_scope_replacement_keeps_history_and_excludes_obsolete_version(store):
    document(store, "old", "Threats are condemned.")
    document(store, "unchanged", "School funding increases.")
    index = SemanticIndex(store, TinyRuntime())
    original = index.build("p")
    old_manifest = store.get_json(index._prefix("p") + "/manifest.json")
    document(store, "new", "Threats are condemned in a new source version.")
    replaced = index.build("p", ["new", "unchanged"], replace_scope=True)
    active = store.get_json(index._prefix("p") + "/manifest.json")
    assert replaced["documents"] == 2 and replaced["cached_documents"] == 1
    assert {entry["document_id"] for entry in active["documents"]} == {"new", "unchanged"}
    assert replaced["index_id"] != original["index_id"]
    assert store.get_json(replaced["history_manifest_key"])["index_id"] == original["index_id"]
    assert store.get_bytes(old_manifest["documents"][0]["chunks"][0]["vectors_key"])
    with pytest.raises(SemanticIndexError, match="explicit source ID list"):
        index.build("p", replace_scope=True)
