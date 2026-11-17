"""Frozen case captures keep raw outcomes, source spans and later review apart."""
import hashlib
import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from doxa.casebooks import create_casebook, get_casebook, initialize_casebooks, pin_case, register_casebooks
from doxa.contextual import build_units
from doxa.lenses import get_lens
from doxa.store import utcnow
from doxa.workspace import Workspace, WorkspaceQuery
from test_workspace import seed_assessment, seed_document, seed_run, store as store


@pytest.fixture
def prepared(store):
    initialize_casebooks(store)
    for identity in ("a", "b"):
        seed_document(store, identity, text="Source statement.")
    seed_run(store, sources=[{"id": x, "sha256": x + "-hash"} for x in ("a", "b")])
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='run'")["config_json"])
    config["lens_snapshots"] = [get_lens("ihra"), get_lens("jda"), get_lens("discourse")]
    store.execute("UPDATE runs SET config_json=? WHERE id='run'", [json.dumps(config)])
    for index, outcome in enumerate(("meets_criterion", "does_not_meet", "uncertain", "not_applicable")):
        payload = seed_assessment(store, "case-" + str(index), "a", result=outcome, unit="unit-" + str(index))
        payload["evidence_refs"] = [{"block_id": "b1", "start": 0, "end": 17, "quote": "Source statement."}]
        if outcome in {"does_not_meet", "not_applicable"}:
            payload["detail_supplied"] = False
            payload.pop("explanation")
        store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(payload), payload["id"]])
    query = Workspace(store).create("p", WorkspaceQuery(run_id="run", results=["meets_criterion"]))["query_id"]
    return store, query


def test_all_four_outcomes_can_be_pinned_without_altering_scope_or_model_records(prepared):
    store, query = prepared
    before = store.query("SELECT id,payload_json,review_json FROM v3_assessments ORDER BY id")
    book = create_casebook(store, "p", query, "Balanced real-record contract fixture")
    for index in range(4):
        book = pin_case(store, book["id"], "a", "case-" + str(index))
    assert {case["result"] for case in book["cases"]} == {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
    assert store.query("SELECT id,payload_json,review_json FROM v3_assessments ORDER BY id") == before
    assert book["cases"][1]["assessment_snapshot"].get("explanation") is None
    assert book["cases"][0]["frozen_snapshot"]["criterion_capture_status"] == "frozen_run_snapshot"
    repeated = pin_case(store, book["id"], "a", "case-0")
    assert repeated["revision"] == book["revision"] and len(repeated["cases"]) == 4


def test_source_cohort_and_exact_evidence_are_enforced(prepared):
    store, query = prepared
    book = create_casebook(store, "p", query, "Scope fixture")
    seed_document(store, "outside", text="Source statement.")
    with pytest.raises(HTTPException) as error:
        pin_case(store, book["id"], "outside", "case-0")
    assert error.value.status_code == 422
    payload = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id='case-0'")["payload_json"])
    payload["evidence_refs"][0]["quote"] = "Invented source words"
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id='case-0'", [json.dumps(payload)])
    with pytest.raises(HTTPException) as error:
        pin_case(store, book["id"], "a", "case-0")
    assert error.value.status_code == 409
    assert not get_casebook(store, book["id"])["cases"]


def test_frozen_case_survives_later_model_projection_and_library_metadata_changes(prepared):
    store, query = prepared
    book = create_casebook(store, "p", query, "Immutable capture")
    book = pin_case(store, book["id"], "a", "case-0")
    capture = book["cases"][0]["capture_sha256"]
    store.execute("UPDATE documents SET title='Changed library title' WHERE id='a'")
    store.execute("UPDATE v3_assessments SET result='does_not_meet',review_status='revised',review_json=? WHERE id='case-0'",
                  [json.dumps({"resolved": {"result": "does_not_meet"}})])
    after = get_casebook(store, book["id"])["cases"][0]
    assert after["source_snapshot"]["title"] == "Source a"
    assert after["assessment_snapshot"]["result"] == "meets_criterion"
    assert after["capture_sha256"] == capture


def test_unresolved_cases_use_actual_rebuilt_units_and_never_invent_assessments(prepared):
    store, query = prepared
    version = hashlib.sha256(b"b:b-hash").hexdigest()[:32]
    units = build_units({"id": "b", "sha256": "b-hash", "source_version_id": version,
                         "blocks": [{"id": "b1", "text": "Source statement.", "page": None, "paragraph": 1}]}, max_unit_chars=4000)
    unit = units[0]
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "b", unit["id"], "partial", 17, json.dumps({"failed_criteria": ["ihra/collective_responsibility"]}), utcnow()])
    book = create_casebook(store, "p", query, "Unresolved is not negative")
    book = pin_case(store, book["id"], "b", unit_id=unit["id"])
    case = book["cases"][0]
    assert case["kind"] == "unresolved" and case["assessment_id"] is None
    assert case["result"] is None and case["assessment_snapshot"] is None
    assert case["frozen_snapshot"]["blocks"][0]["text"] == "Source statement."


def test_casebook_http_notes_order_and_deletion_keep_original_captures(prepared):
    store, query = prepared
    app = FastAPI()
    def require_project(identity):
        row = store.one("SELECT * FROM projects WHERE id=?", [identity])
        if not row:
            raise HTTPException(404)
        return row
    register_casebooks(app, {"db": lambda: store, "require_project": require_project})
    with TestClient(app) as client:
        response = client.post("/api/v3/projects/p/casebooks", json={"query_id": query, "title": "HTTP casebook"})
        assert response.status_code == 201, response.text
        identity = response.json()["id"]
        for index in (0, 1):
            response = client.post(f"/api/v3/casebooks/{identity}/cases", json={"document_id": "a", "assessment_id": "case-" + str(index)})
            assert response.status_code == 200, response.text
        cases = response.json()["cases"]
        capture = cases[0]["capture_sha256"]
        note = "Operator guidance, not an independent verdict."
        response = client.patch(f"/api/v3/casebooks/{identity}/cases/{cases[0]['id']}", json={"editorial_note": note})
        assert response.status_code == 200 and response.json()["cases"][0]["capture_sha256"] == capture
        order = [case["id"] for case in reversed(cases)]
        response = client.put(f"/api/v3/casebooks/{identity}/order", json={"case_ids": order})
        assert [case["id"] for case in response.json()["cases"]] == order
        assert response.json()["cases"][1]["editorial_note"] == note
        invalid = client.put(f"/api/v3/casebooks/{identity}/order", json={"case_ids": [order[0], order[0]]})
        assert invalid.status_code == 422
        assert client.get("/api/v3/projects/p/casebooks").json()["total"] == 1
        listed = client.get("/api/v3/projects/p/casebooks").json()["items"][0]
        assert listed["case_count"] == 2 and listed["cases"] == []
        assert "frozen_snapshot" not in response.json()["cases"][0]
        deleted = client.delete(f"/api/v3/casebooks/{identity}/cases/{order[0]}").json()
        repeated = client.delete(f"/api/v3/casebooks/{identity}/cases/{order[0]}").json()
        assert deleted["revision"] == repeated["revision"] and len(repeated["cases"]) == 1


def test_corrupted_capture_is_not_silently_presented_as_recorded_evidence(prepared):
    store, query = prepared
    book = pin_case(store, create_casebook(store, "p", query, "Integrity fixture")["id"], "a", "case-0")
    row = store.one("SELECT * FROM v4_casebook_cases WHERE id=?", [book["cases"][0]["id"]])
    broken = store.get_json(row["snapshot_key"])
    broken["source"]["title"] = "Tampered capture"
    store.put_json(row["snapshot_key"], broken)
    with pytest.raises(HTTPException) as error:
        get_casebook(store, book["id"])
    assert error.value.status_code == 409


def test_pin_appends_after_deletion_without_reusing_an_existing_order_position(prepared):
    store, query = prepared
    book = create_casebook(store, "p", query, "Append after a deleted case")
    for index in (0, 1, 2):
        book = pin_case(store, book["id"], "a", "case-" + str(index))
    deleted = book["cases"][0]
    retained = [case["assessment_id"] for case in book["cases"][1:]]
    # A deleted first case leaves a real gap in persisted order positions.
    store.batch([
        {"sql": "DELETE FROM v4_casebook_cases WHERE id=? AND casebook_id=?", "params": [deleted["id"], book["id"]]},
        {"sql": "UPDATE v4_casebooks SET revision=revision+1 WHERE id=?", "params": [book["id"]]},
    ])
    book = pin_case(store, book["id"], "a", "case-3")
    assert [case["assessment_id"] for case in book["cases"]] == [*retained, "case-3"]
    positions = [case["ordinal"] for case in book["cases"]]
    assert len(set(positions)) == len(positions)
    assert positions[-1] > max(positions[:-1])


@pytest.mark.parametrize("context_sentences", [0, 4])
def test_unresolved_capture_rebuilds_frozen_nondefault_neighbour_context(prepared, context_sentences):
    store, query = prepared
    blocks = [{"id": "b" + str(number), "text": sentence * 10, "paragraph": number}
              for number, sentence in enumerate(("Earlier sentence. ", "Target statement. ", "Later discussion. "), 1)]
    source = store.one("SELECT blocks_key FROM documents WHERE id='b'")
    store.put_json(source["blocks_key"], blocks)
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='run'")["config_json"])
    config.update(unit_max_chars=200, context_sentences=context_sentences)
    store.execute("UPDATE runs SET config_json=? WHERE id='run'", [json.dumps(config)])
    version = hashlib.sha256(b"b:b-hash").hexdigest()[:32]
    units = build_units({"id": "b", "sha256": "b-hash", "source_version_id": version, "blocks": blocks},
                        max_unit_chars=200, context_sentences=context_sentences)
    assert len(units) >= 3
    wanted = units[len(units) // 2]
    if context_sentences:
        assert len(wanted["context_before"]) == context_sentences
        assert len(wanted["context_after"]) == context_sentences
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "b", wanted["id"], "partial", wanted["source_characters"], "{}", utcnow()])
    book = create_casebook(store, "p", query, "Non-default frozen context")
    book = pin_case(store, book["id"], "b", unit_id=wanted["id"])
    actual = book["cases"][0]["frozen_snapshot"]["unit"]["rebuilt_unit"]
    assert actual["builder_config"] == wanted["builder_config"]
    assert actual["context_before"] == wanted["context_before"]
    assert actual["context_after"] == wanted["context_after"]
    assert actual["segments"] == wanted["segments"]


def test_case_capture_reads_assessment_review_and_unit_from_one_read_snapshot(prepared, monkeypatch):
    store, query = prepared
    book = create_casebook(store, "p", query, "Consistent review capture")
    original = store.query
    observed = set()

    def check_snapshot(sql, params=()):
        if sql.startswith("SELECT * FROM v3_assessments"):
            kind = "assessment"
        elif sql.startswith("SELECT * FROM v3_review_events"):
            kind = "review_events"
        elif sql.startswith("SELECT * FROM v3_unit_statuses"):
            kind = "unit_status"
        else:
            kind = None
        if kind:
            observed.add(kind)
            assert store._transaction_depth > 0
            assert store._db.in_transaction
            assert not store._transaction_write
        return original(sql, params)

    monkeypatch.setattr(store, "query", check_snapshot)
    book = pin_case(store, book["id"], "a", "case-0")
    assert len(book["cases"]) == 1
    assert observed == {"assessment", "review_events", "unit_status"}


def test_compact_negative_rejects_changed_blocks_even_without_a_retained_quote(prepared):
    store, query = prepared
    source = store.one("SELECT blocks_key FROM documents WHERE id='a'")
    blocks = store.get_json(source["blocks_key"])
    blocks_hash = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    store.execute("UPDATE documents SET metadata_json=? WHERE id='a'",
                  [json.dumps({"extraction_manifest": {"blocks_sha256": blocks_hash}})])
    raw = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id='case-1'")["payload_json"])
    raw.pop("quote", None)
    for reference in raw["evidence_refs"]:
        reference.pop("quote", None)
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id='case-1'", [json.dumps(raw)])
    pristine = pin_case(store, create_casebook(store, "p", query, "Valid compact negative")["id"], "a", "case-1")
    assert pristine["cases"][0]["frozen_snapshot"]["source_block_integrity"]["status"] == "verified"
    book = create_casebook(store, "p", query, "Frozen blocks before corruption")
    # The DB source SHA, IDs, offsets and block length stay unchanged. Bounds
    # checks alone therefore cannot detect this altered source artifact.
    blocks[0]["text"] = "Forged statement."
    assert len(blocks[0]["text"]) == raw["evidence_refs"][0]["end"]
    store.put_json(source["blocks_key"], blocks)
    with pytest.raises(HTTPException) as error:
        pin_case(store, book["id"], "a", "case-1")
    assert error.value.status_code == 409
    assert "extraction digest" in error.value.detail
    assert not get_casebook(store, book["id"])["cases"]


def test_source_unit_context_rejects_changed_blocks_before_rebuilding_source_unit(prepared):
    from doxa.review_queue import unit_context

    store, query = prepared
    source = store.one("SELECT blocks_key FROM documents WHERE id='b'")
    blocks = store.get_json(source["blocks_key"])
    blocks_hash = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
    store.execute("UPDATE documents SET metadata_json=? WHERE id='b'",
                  [json.dumps({"extraction_manifest": {"blocks_sha256": blocks_hash}})])
    version = hashlib.sha256(b"b:b-hash").hexdigest()[:32]
    unit = build_units({"id": "b", "sha256": "b-hash", "source_version_id": version, "blocks": blocks},
                       max_unit_chars=4000)[0]
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "b", unit["id"], "partial", unit["source_characters"], "{}", utcnow()])
    valid = unit_context(store, "p", query, "b", unit["id"])
    assert valid["source_block_integrity"]["status"] == "verified"
    assert valid["unit"]["text"] == "Source statement."
    blocks[0]["text"] = "Forged statement."
    store.put_json(source["blocks_key"], blocks)
    with pytest.raises(HTTPException) as error:
        unit_context(store, "p", query, "b", unit["id"])
    assert error.value.status_code == 409
    assert "extraction digest" in error.value.detail


def test_legacy_case_retains_explicitly_unrecorded_block_integrity(prepared):
    store, query = prepared
    book = pin_case(store, create_casebook(store, "p", query, "Historical anchor-only guarantee")["id"], "a", "case-0")
    integrity = book["cases"][0]["frozen_snapshot"]["source_block_integrity"]
    assert integrity["status"] == "not_recorded"
    assert "no frozen extraction-block digest" in integrity["notice"]
    assert "blocks_sha256" not in integrity


def test_cross_outcome_pins_preserve_frozen_framework_filters(prepared):
    store, _ = prepared
    query = Workspace(store).create("p", WorkspaceQuery(run_id="run", lens_ids=["discourse"], results=["meets_criterion"]))["query_id"]
    book = create_casebook(store, "p", query, "Selected framework remains explicit")
    with pytest.raises(HTTPException) as error:
        pin_case(store, book["id"], "a", "case-0")
    assert error.value.status_code == 422
    assert not get_casebook(store, book["id"])["cases"]
