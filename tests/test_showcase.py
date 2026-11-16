"""Saved-showcase restoration logic, with real sources and a test-only result.

The twenty source documents are the shipped public corpus. The single lexical
result below is a deterministic fixture for import mechanics, not a model run,
human annotation, scientific benchmark, or production showcase artifact.
"""
import copy
import gzip
import json
from pathlib import Path
import re

import pytest

from doxa.showcase import restore_showcase
from doxa.store import Store, utcnow

CORPUS = Path(__file__).resolve().parent.parent / "corpus" / "documents"


@pytest.fixture
def restoration(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path / "workspace")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('project','Showcase import regression',?)", [utcnow()])
    originals = [json.loads(path.read_text()) for path in sorted(CORPUS.glob("*.json"))]
    assert len(originals) == 20
    documents, saved_documents = [], []
    for original in originals:
        doc_id = "current-" + original["id"]
        blocks_key = f"documents/{doc_id}/blocks.json"
        store.put_json(blocks_key, original["blocks"])
        current = {"id": doc_id, "title": original["title"], "author": original["author"], "sha256": original["content_sha256"], "blocks_key": blocks_key, "metadata": {"source_id": original["id"]}}
        documents.append(current)
        store.execute("INSERT INTO documents(id,project_id,batch_id,title,author,filename,mime_type,sha256,status,word_count,page_count,blocks_key,created_at,metadata_json) VALUES(?, 'project','batch',?,?,?,'text/plain',?,'ready',?,?,?,?,?)",
                      [doc_id, current["title"], current["author"], original["id"]+".txt", current["sha256"], original["word_count"], original.get("page_count") or 0, blocks_key, utcnow(), json.dumps(current["metadata"])])
        saved_documents.append({**copy.deepcopy(original), "id": "recorded-"+original["id"], "metadata": {"source_id": original["id"]}})
    first = saved_documents[0]
    block, match = next((block, found) for block in first["blocks"] if (found := re.search(r"\b(?:must|should|because|therefore|freedom|equality|security|tradition|rights)\b", block["text"], re.IGNORECASE)))
    finding = {"id": "test-only-lexical-observation", "document_id": first["id"], "block_id": block["id"], "start": match.start(), "end": match.end(), "quote": match.group(), "lens_id": "discourse", "criterion": "lexical_mention", "category": "lexical_mention", "title": "Test-only literal mention", "explanation": "A literal source match used to test restoration mechanics.", "alternative": "This fixture makes no interpretive or endorsement claim.", "stance": "not_applicable", "speaker": "Unknown", "attribution": "unknown", "certainty": "insufficient_context", "evidence_kind": "lexical_match", "review_status": "unreviewed"}
    total_blocks = sum(len(doc["blocks"]) for doc in saved_documents)
    snapshot = {"schema_version": "test-fixture", "generated_at": "2026-11-16T00:00:00Z", "documents": saved_documents, "findings": [finding],
                "runs": [{"id": "recorded-test-only-run", "status": "completed", "config": {"provider": "lexical", "lens_ids": ["discourse"], "budget_usd": 1}, "progress": {"documents_total": 20, "blocks_total": total_blocks, "blocks_done": total_blocks, "complete": True}, "usage": {"input_tokens": 70, "output_tokens": 42, "cost_usd": 1.23, "requests": 3}, "errors": [], "model_profile": {"provider": "lexical", "model": "test-only-import-fixture", "semantic_analysis": False}, "started_at": "2026-11-16T00:00:00Z", "completed_at": "2026-11-16T00:01:00Z"}],
                "metrics": {"coverage_detail": {"sampled": False, "full_corpus_scan_complete": True, "selected_input_complete": True}},
                "benchmark": {"test_fixture_only": True, "task_results": [{"document_id": doc["id"], "coverage": {"processed_blocks": len(doc["blocks"]), "processed_characters": sum(len(b["text"]) for b in doc["blocks"])}} for doc in saved_documents]}}
    yield store, documents, snapshot, tmp_path
    store.close()


def saved_path(directory, snapshot, *, compressed=False):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
    path = directory / ("test-showcase.json.gz" if compressed else "test-showcase.json")
    path.write_bytes(gzip.compress(raw) if compressed else raw)
    return path


def counts(store):
    return {table: store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] for table in ("runs", "tasks", "findings", "reviews", "budget_ledger", "budget_reservations")}


def test_restore_real_source_mapping_quotes_and_historical_usage_without_budget_debit(restoration, monkeypatch):
    store, documents, snapshot, directory = restoration
    month = utcnow()[:7]
    store.execute("INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES(?,0.2,0.3)", [month])
    ledger_before = store.one("SELECT * FROM budget_ledger WHERE month=?", [month])
    def forbidden_provider(*args, **kwargs):
        pytest.fail("Restoring saved results must never request inference")
    monkeypatch.setattr("doxa.providers.get_provider", forbidden_provider)
    path = saved_path(directory, snapshot, compressed=True)
    run_id = restore_showcase(store, "project", documents, path)
    run = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    config = json.loads(run["config_json"])
    assert run["status"] == "completed"
    assert config["saved_snapshot"]["historical_usage"] is True
    assert config["saved_snapshot"]["import_complete"] is True
    assert config["saved_snapshot"]["original_run_id"] == "recorded-test-only-run"
    assert len(config["source_snapshot"]) == 20
    assert run["spent_usd"] == 0 and run["reserved_usd"] == 0
    assert json.loads(run["usage_json"])["cost_usd"] == 1.23
    assert store.one("SELECT * FROM budget_ledger WHERE month=?", [month]) == ledger_before
    assert counts(store) == {"runs": 1, "tasks": 20, "findings": 1, "reviews": 0, "budget_ledger": 1, "budget_reservations": 0}
    restored = json.loads(store.one("SELECT payload_json FROM findings")["payload_json"])
    assert restored["document_id"] == documents[0]["id"]
    assert restored["run_id"] == run_id and restored["project_id"] == "project"
    assert restored["quote"] == snapshot["findings"][0]["quote"]
    block = next(b for b in store.get_json(documents[0]["blocks_key"]) if b["id"] == restored["block_id"])
    assert block["text"][restored["start"]:restored["end"]] == restored["quote"]
    assert all(json.loads(t["coverage_json"])["complete"] for t in store.query("SELECT coverage_json FROM tasks"))


def test_reimport_is_idempotent_and_preserves_local_review_audit(restoration):
    store, documents, snapshot, directory = restoration
    path = saved_path(directory, snapshot)
    first = restore_showcase(store, "project", documents, path)
    finding = store.one("SELECT id FROM findings")
    store.execute("UPDATE findings SET review_status='confirmed',review_note='Human review survives reimport' WHERE id=?", [finding["id"]])
    store.execute("INSERT INTO reviews(id,finding_id,review_status,review_note,previous_status,created_at) VALUES('review',?,'confirmed','Human review survives reimport','unreviewed',?)", [finding["id"], utcnow()])
    before = counts(store)
    # Compression packaging is immaterial: identity uses the decompressed snapshot.
    compressed = saved_path(directory, snapshot, compressed=True)
    second = restore_showcase(store, "project", documents, compressed)
    assert first == second
    assert counts(store) == before
    reviewed = store.one("SELECT review_status,review_note FROM findings WHERE id=?", [finding["id"]])
    assert reviewed == {"review_status": "confirmed", "review_note": "Human review survives reimport"}
    assert store.one("SELECT COUNT(*) AS n FROM reviews")["n"] == 1


@pytest.mark.parametrize("tamper", ["quote", "offset", "source"])
def test_tampered_evidence_or_source_fails_before_any_restore_writes(restoration, tamper):
    store, documents, snapshot, directory = restoration
    if tamper == "quote":
        snapshot["findings"][0]["quote"] = "Words absent from the cited source"
    elif tamper == "offset":
        snapshot["findings"][0]["start"] += 1
    else:
        snapshot["documents"][0]["blocks"][0]["text"] += " Tampered source text."
    before = counts(store)
    objects_before = {str(p.relative_to(store.root)) for p in (store.root / "objects").rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        restore_showcase(store, "project", documents, saved_path(directory, snapshot))
    assert counts(store) == before
    assert {str(p.relative_to(store.root)) for p in (store.root / "objects").rglob("*") if p.is_file()} == objects_before


@pytest.mark.parametrize("status,sampled", [("running", False), ("queued", False), ("importing", False), ("completed", True)])
def test_in_progress_or_sampled_snapshots_cannot_become_a_complete_showcase(restoration, status, sampled):
    store, documents, snapshot, directory = restoration
    snapshot["runs"][0]["status"] = status
    snapshot["metrics"]["coverage_detail"]["sampled"] = sampled
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase(store, "project", documents, saved_path(directory, snapshot))
    assert counts(store) == before


def test_interrupted_restore_retries_deterministic_ids_without_losing_review(restoration, monkeypatch):
    store, documents, snapshot, directory = restoration
    path = saved_path(directory, snapshot)
    original_execute = store.execute
    with monkeypatch.context() as context:
        def interrupt(sql, params=()):
            if sql.startswith("UPDATE runs SET config_json="):
                raise RuntimeError("Simulated interruption before import-complete marker")
            return original_execute(sql, params)
        context.setattr(store, "execute", interrupt)
        with pytest.raises(RuntimeError, match="Simulated interruption"):
            restore_showcase(store, "project", documents, path)
    interrupted = store.one("SELECT * FROM runs")
    assert interrupted["status"] == "importing"
    assert json.loads(interrupted["config_json"])["saved_snapshot"]["import_complete"] is False
    finding = store.one("SELECT id FROM findings")
    assert finding
    task_ids = {t["id"] for t in store.query("SELECT id FROM tasks")}
    store.execute("UPDATE findings SET review_status='revised',review_note='Keep this adjudication' WHERE id=?", [finding["id"]])
    store.execute("INSERT INTO reviews(id,finding_id,review_status,review_note,previous_status,created_at) VALUES('audit',?,'revised','Keep this adjudication','unreviewed',?)", [finding["id"], utcnow()])
    resumed = restore_showcase(store, "project", documents, path)
    assert resumed == interrupted["id"]
    assert {t["id"] for t in store.query("SELECT id FROM tasks")} == task_ids
    assert counts(store) == {"runs": 1, "tasks": 20, "findings": 1, "reviews": 1, "budget_ledger": 0, "budget_reservations": 0}
    complete = store.one("SELECT * FROM runs WHERE id=?", [resumed])
    assert complete["status"] == "completed"
    assert json.loads(complete["config_json"])["saved_snapshot"]["import_complete"] is True
    assert store.one("SELECT review_status,review_note FROM findings WHERE id=?", [finding["id"]]) == {"review_status": "revised", "review_note": "Keep this adjudication"}


def test_wrong_document_count_is_rejected_without_writing(restoration):
    store, documents, snapshot, directory = restoration
    snapshot["documents"].pop()
    with pytest.raises(ValueError, match="twenty"):
        restore_showcase(store, "project", documents, saved_path(directory, snapshot))
    assert counts(store)["runs"] == 0


def test_duplicate_manifest_source_ids_cannot_claim_twenty_distinct_documents(restoration):
    store, documents, snapshot, directory = restoration
    second_id = snapshot["documents"][1]["id"]
    snapshot["documents"][1] = copy.deepcopy(snapshot["documents"][0])
    snapshot["documents"][1]["id"] = second_id
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase(store, "project", documents, saved_path(directory, snapshot))
    assert counts(store) == before
