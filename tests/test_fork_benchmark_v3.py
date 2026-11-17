"""Source-cohort forks reuse exact checkpoints and never re-debit old requests."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.store import utcnow
from scripts.benchmark import checksum, import_document
from test_v3_api import CompactProvider, finish, project, start_mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fork_v3_fixture", ROOT / "scripts/fork_benchmark_v3.py")
forking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forking)


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as client:
        pid = project(client)
        store = client.app.state.store
        store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", ["old-batch", pid, "Synthetic original cohort", utcnow()])
        documents = []
        for index in range(4):
            source = {"id": f"s{index}", "title": f"Synthetic source {index}", "author": "Fixture author",
                      "institution": "Fixture publisher", "document_type": "news_article", "published_at": "2025-01-01",
                      "source_url": f"https://example.invalid/source-{index}", "license": "SYNTHETIC-TEST",
                      "blocks": [{"id": f"s{index}-b1", "text": f"QUOTED. Synthetic source {index} rejects a reported proposition."}]}
            imported = import_document(store, pid, "old-batch", source)
            documents.append(store.one("SELECT * FROM documents WHERE id=?", [imported["id"]]))
        old, provider = start_mock(client, pid, monkeypatch, provider=CompactProvider())
        assert finish(client, old["id"])["status"] == "completed"
        # Correct one source as a new immutable version; keep the old source.
        changed = deepcopy(documents[-1])
        blocks = [{"id": "s3-b1", "text": "QUOTED. Corrected synthetic source 3 retains its original counterpart."}]
        changed["sha256"] = checksum(blocks)
        changed["id"] = hashlib.sha256(f"{pid}:s3:{changed['sha256']}".encode()).hexdigest()[:32]
        changed["blocks_key"] = f"documents/{changed['id']}/blocks.json"
        changed["created_at"] = utcnow()
        store.put_json(changed["blocks_key"], blocks)
        columns = [key for key in changed if key != "blocks"]
        store.execute("INSERT INTO documents(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", [changed[key] for key in columns])
        assert client.post(f"/api/runs/{old['id']}/cancel").status_code == 200
        yield client, store, old["id"], documents, changed, provider


async def call_fork(client, store, old_id, selection, budget=1):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app, client=("127.0.0.1", 0)), base_url="http://127.0.0.1") as http:
        return await forking.fork_run(http, store, old_id, selection, budget_usd=budget, reason="Synthetic one-source immutable correction")


def invoke(cohort, *, budget=1):
    client, store, old_id, documents, changed, _provider = cohort
    selection = [document["id"] for document in documents[:-1]] + [changed["id"]]
    return client.portal.call(call_fork, client, store, old_id, selection, budget)


def original_snapshot(store, run_id):
    return {table: store.query(f"SELECT * FROM {table} WHERE " + ("id=?" if table == "runs" else "run_id=?") + " ORDER BY " + ("id" if table != "v3_unit_statuses" else "unit_id"), [run_id])
            for table in ("runs", "tasks", "v3_assessments", "v3_unit_statuses", "budget_reservations")}


def test_four_source_fork_reuses_only_three_exact_versions_and_preserves_billing(cohort):
    client, store, old_id, documents, changed, provider = cohort
    old = original_snapshot(store, old_id)
    ledger = store.query("SELECT * FROM budget_ledger")
    old_calls = len(provider.calls)
    result = invoke(cohort)
    new_id = result["run_id"]
    assert old == original_snapshot(store, old_id)
    assert store.query("SELECT * FROM budget_ledger") == ledger
    assert len(provider.calls) == old_calls, "Forking must not request model inference"
    assert result["changed_source_ids"] == ["s3"]
    assert result["reused"] == {"assessments": 18, "accepted_units": 3, "completed_tasks": 3, "documents": 3}
    assert result["new_monthly_debit_usd"] == 0
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=? AND document_id=?", [new_id, changed["id"]])["n"] == 0
    assert store.one("SELECT status FROM v3_unit_statuses WHERE run_id=? AND document_id=?", [new_id, changed["id"]])["status"] == "pending"
    assert store.one("SELECT COUNT(*) n FROM documents WHERE project_id=?", [result["project_id"]])["n"] == 5
    new = client.get(f"/api/runs/{new_id}").json()
    assert len(new["config"]["source_snapshot"]) == 4
    assert documents[-1]["id"] not in {row["id"] for row in new["config"]["source_snapshot"]}
    assert new["spent_usd"] == pytest.approx(old["runs"][0]["spent_usd"])
    assert new["progress"]["tasks_done"] == new["progress"]["blocks_done"] == 3
    copied = store.query("SELECT * FROM budget_reservations WHERE run_id=?", [new_id])
    assert len(copied) == len(old["budget_reservations"])
    assert all(json.loads(row["usage_json"])["historical_reused"] for row in copied)
    for row in store.query("SELECT * FROM v3_assessments WHERE run_id=?", [new_id]):
        payload = json.loads(row["payload_json"])
        assert payload["run_id"] == new_id and row["id"] != payload["reused_from_assessment_id"]
        blocks = {block["id"]: block["text"] for block in store.get_json(store.one("SELECT blocks_key FROM documents WHERE id=?", [row["document_id"]])["blocks_key"])}
        for ref in payload["evidence_refs"]:
            if "quote" in ref:
                assert blocks[ref["block_id"]][ref["start"]:ref["end"]] == ref["quote"]
    assert store.one("SELECT COUNT(*) n FROM findings WHERE run_id=?", [new_id])["n"] == 3

    completed = finish(client, new_id)
    assert completed["status"] == "completed", completed.get("errors")
    assert len(provider.calls) == old_calls + 2, "Only the changed source needs classification/details"
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [new_id])["n"] == 24
    actual_new = 0.002
    assert completed["spent_usd"] == pytest.approx(old["runs"][0]["spent_usd"] + actual_new)
    assert store.query("SELECT * FROM budget_ledger")[0]["spent_usd"] == pytest.approx(ledger[0]["spent_usd"] + actual_new)


def test_partial_peer_outcomes_are_cached_without_promoting_unit_coverage(cohort):
    client, store, old_id, documents, _changed, provider = cohort
    document = documents[0]
    negative = store.one("SELECT id FROM v3_assessments WHERE run_id=? AND document_id=? AND result='does_not_meet' LIMIT 1", [old_id, document["id"]])
    store.execute("DELETE FROM v3_assessments WHERE id=?", [negative["id"]])
    store.execute("UPDATE v3_unit_statuses SET status='partial',payload_json='{}' WHERE run_id=? AND document_id=?", [old_id, document["id"]])
    store.execute("UPDATE tasks SET status='failed',error='Synthetic lost criterion' WHERE run_id=? AND document_id=?", [old_id, document["id"]])
    original_calls = len(provider.calls)
    fork = invoke(cohort)
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [fork["run_id"]])["n"] == 17
    assert store.one("SELECT status FROM v3_unit_statuses WHERE run_id=? AND document_id=?", [fork["run_id"], document["id"]])["status"] == "partial"
    assert finish(client, fork["run_id"])["status"] == "completed"
    additional = provider.calls[original_calls:]
    unchanged_calls = [call for call in additional if call["document"]["id"] == document["id"]]
    assert unchanged_calls and all(len(call["criteria"]) == 1 for call in unchanged_calls)
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [fork["run_id"]])["n"] == 24


@pytest.mark.parametrize("unsafe", ["running", "reservation", "source_tampering"])
def test_unsafe_original_state_is_rejected_before_creating_a_run(cohort, unsafe):
    client, store, old_id, documents, _changed, provider = cohort
    if unsafe == "running":
        store.execute("UPDATE runs SET status='running' WHERE id=?", [old_id])
    elif unsafe == "reservation":
        store.execute("UPDATE runs SET reserved_usd=0.01 WHERE id=?", [old_id])
    else:
        store.put_json(documents[0]["blocks_key"], [{"id": "s0-b1", "text": "Tampered stored source with an unchanged declared digest."}])
    runs = store.one("SELECT COUNT(*) n FROM runs")["n"]
    calls = len(provider.calls)
    with pytest.raises(ValueError):
        invoke(cohort)
    assert store.one("SELECT COUNT(*) n FROM runs")["n"] == runs
    assert len(provider.calls) == calls


def test_committed_copy_with_lost_acknowledgement_can_resume_idempotently(cohort, monkeypatch):
    _client, store, old_id, _documents, _changed, _provider = cohort
    ledger = store.query("SELECT * FROM budget_ledger")
    original_batch = store.batch
    lost = False
    def lost_acknowledgement(statements):
        nonlocal lost
        result = original_batch(statements)
        if not lost and any("INSERT INTO v3_assessments" in statement["sql"] for statement in statements):
            lost = True
            raise RuntimeError("Synthetic lost fork acknowledgement")
        return result
    with monkeypatch.context() as context:
        context.setattr(store, "batch", lost_acknowledgement)
        with pytest.raises(RuntimeError, match="Synthetic lost fork acknowledgement"):
            invoke(cohort)
    assert lost
    result = invoke(cohort)
    assert store.one("SELECT COUNT(*) n FROM runs")["n"] == 2
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [result["run_id"]])["n"] == 18
    assert store.query("SELECT * FROM budget_ledger") == ledger
    assert store.one("SELECT status FROM runs WHERE id=?", [old_id])["status"] == "cancelled"


def test_unknown_historical_usage_keeps_cumulative_cap_without_second_monthly_charge(cohort):
    client, store, old_id, _documents, _changed, provider = cohort
    reservation = store.one("SELECT id,usage_json FROM budget_reservations WHERE run_id=? LIMIT 1", [old_id])
    usage = json.loads(reservation["usage_json"])
    usage["cost_usd"] = None
    store.execute("UPDATE budget_reservations SET usage_json=? WHERE id=?", [json.dumps(usage), reservation["id"]])
    old = client.post(f"/api/runs/{old_id}/cancel").json()
    assert old["usage"]["cost_usd"] is None
    calls = len(provider.calls)
    ledger = store.query("SELECT * FROM budget_ledger")
    result = invoke(cohort, budget=old["spent_usd"] + 0.0005)
    paused = finish(client, result["run_id"])
    assert paused["status"] == "paused_budget"
    assert paused["usage"]["cost_usd"] is None
    assert paused["spent_usd"] == pytest.approx(old["spent_usd"])
    assert len(provider.calls) == calls
    assert store.query("SELECT * FROM budget_ledger") == ledger


def test_reused_human_overlay_keeps_original_and_linked_review_history(cohort):
    client, store, old_id, documents, _changed, _provider = cohort
    assessment = store.one("SELECT id FROM v3_assessments WHERE run_id=? AND document_id=? AND result='meets_criterion' LIMIT 1", [old_id, documents[0]["id"]])
    response = client.patch(f"/api/v3/findings/{assessment['id']}/review", json={
        "review_status": "confirmed", "review_note": "Synthetic reviewed source context.", "corrections": {},
    })
    assert response.status_code == 200, response.text
    original_event = response.json()["review_event"]
    result = invoke(cohort)
    copied = next(row for row in store.query("SELECT * FROM v3_assessments WHERE run_id=?", [result["run_id"]])
                  if json.loads(row["payload_json"])["reused_from_assessment_id"] == assessment["id"])
    assert copied["review_status"] == "confirmed" and copied["review_note"] == "Synthetic reviewed source context."
    event = store.one("SELECT * FROM v3_review_events WHERE assessment_id=?", [copied["id"]])
    assert event and event["id"] != original_event["id"] and event["actor_kind"] == "human"
    assert store.one("SELECT id FROM v3_review_events WHERE id=?", [original_event["id"]])


def test_published_checkpoint_freezes_current_cohort_and_preserves_original_receipt(cohort):
    client, store, old_id, _documents, _changed, _provider = cohort
    result = invoke(cohort)
    new = client.get(f"/api/runs/{result['run_id']}").json()
    selected = forking.source_rows(store, result["project_id"], new["config"]["source_snapshot"])
    selection = {"collection": "news-sample", "documents": 4,
                 "sources": [{"source_id": key, "content_sha256": row["sha256"]} for key, row in selected.items()]}
    original_receipt = {"synthetic_original_receipt": True}
    (store.root / "frozen-engine-compatibility.json").write_text(json.dumps(original_receipt))
    output = store.root / "new-summary.json"
    state = forking.publish_checkpoint(store, result, selection, {"sessions": [{"synthetic_original": True}], "elapsed_seconds": 12},
                                      output=output, data_dir=store.root)
    assert state["run_id"] == result["run_id"] and state["run_id"] != old_id
    assert len(state["source_snapshots"]) == 4
    assert state["sessions"] == [] and state["historical_elapsed_seconds"] == 12
    assert json.loads(output.read_text())["progress"]["tasks_done"] == 3
    assert json.loads((store.root / "fork-provenance" / f"{old_id}-engine.json").read_text()) == original_receipt
    config = {key: new["config"][key] for key in ("provider", "model", "methodology_version", "allow_remote_text", "lens_ids", "question", "persona")}
    assert state["signature"] == forking.digest({"config": config, "selection": selection, "engine": state["engine_fingerprint"]})
    assert json.loads(output.with_suffix(".json.checkpoint.json").read_text())["signature"] == state["signature"]


def test_settled_run_tolerates_float_residue_but_never_ignores_reserved_request_rows(cohort):
    _client, store, old_id, _documents, _changed, _provider = cohort
    store.execute("UPDATE runs SET reserved_usd=? WHERE id=?", [4.716279450311944e-17, old_id])
    assert forking.stopped_run(store, old_id)["id"] == old_id
    reservation = store.one("SELECT id FROM budget_reservations WHERE run_id=? LIMIT 1", [old_id])
    store.execute("UPDATE budget_reservations SET status='reserved' WHERE id=?", [reservation["id"]])
    with pytest.raises(ValueError, match="requests must all be settled"):
        forking.stopped_run(store, old_id)


def test_original_checkpoint_signature_is_proven_against_original_not_corrected_selection(cohort):
    _client, store, old_id, _documents, _changed, _provider = cohort
    old = forking.stopped_run(store, old_id)
    sources = forking.source_rows(store, old["project_id"], old["config"]["source_snapshot"])
    selection = {"collection": "news", "sources": [{"source_id": key, "content_sha256": row["sha256"]} for key, row in sources.items()]}
    config = {key: old["config"][key] for key in ("provider", "model", "methodology_version", "allow_remote_text", "lens_ids", "question", "persona")}
    state = {"run_id": old_id, "project_id": old["project_id"], "data_dir": str(store.root), "engine_fingerprint": "synthetic-original-engine",
             "selection": selection, "source_snapshots": old["config"]["source_snapshot"]}
    state["signature"] = forking.digest({"config": config, "selection": selection, "engine": state["engine_fingerprint"]})
    forking.validate_original_checkpoint(store, old, state, store.root)
    altered = deepcopy(state)
    altered["selection"]["sources"][0]["content_sha256"] = "different-version"
    with pytest.raises(ValueError, match="signature is invalid"):
        forking.validate_original_checkpoint(store, old, altered, store.root)
