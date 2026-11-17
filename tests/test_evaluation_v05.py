"""Independent structured annotations bind to frozen source context, never model gold."""
import copy
import hashlib
import json

import pytest

from doxa import evaluation
from doxa.contextual import build_units
from test_evaluation_v04 import labels, reviewed_store  # noqa: F401


def scaffold_case(store, *, body=None, context=2):
    if body is not None:
        blocks = [{"id": "b", "text": body, "paragraph": 1}]
        digest = hashlib.sha256(json.dumps(blocks).encode()).hexdigest()
        row = store.one("SELECT blocks_key FROM documents WHERE id='d0'")
        store.put_json(row["blocks_key"], blocks)
        store.execute("UPDATE documents SET sha256=? WHERE id='d0'", [digest])
        unit = build_units({"id": "d0", "sha256": digest, "blocks": blocks}, context_sentences=context)[0]
        assessment = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id='a0'")["payload_json"])
        assessment["unit_id"] = unit["id"]
        store.execute("UPDATE v3_assessments SET unit_id=?,payload_json=? WHERE id='a0'", [unit["id"], json.dumps(assessment)])
        config = json.loads(store.one("SELECT config_json FROM runs WHERE id='r'")["config_json"])
        config["context_sentences"] = context
        store.execute("UPDATE runs SET config_json=? WHERE id='r'", [json.dumps(config)])
    created = evaluation.create_scaffold(store, "p", "r", development_count=4, heldout_count=4, actor_id="test-operator")
    form = evaluation.export_review_form(store, "p", created["id"])
    return created, next(case for case in form["cases"] if case["source"]["id"] == "d0"), form


def row_for(case, reference):
    return {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
            "independent_blinded_review": True, "labels": reference}


def extended_labels(case):
    sentence = case["scored_unit"]["sentences"][0]
    span = {"document_id": case["source"]["id"], "block_id": sentence["block_id"],
            "start": sentence["start"], "end": sentence["end"], "quote": sentence["text"]}
    return {**labels(), "evidence_spans": [span], "target": "The stated service policy", "proposition": "The policy expresses equal access.",
            "reporting_chain": [{"speaker": "Unverified author", "role": "author", "evidence_span_indices": [0]}],
            "communicative_act": "assertion", "perspective": "Test-only reviewer applying the frozen framework to source wording.",
            "missing_context_reasons": []}


def test_new_forms_add_independent_blank_contract_without_changing_offline_v04_fields(reviewed_store):  # noqa: F811
    created, case, form = scaffold_case(reviewed_store)
    assert form["annotation_contract"]["version"] == "0.5"
    assert not form["annotation_contract"]["automatic_reference_labels"]
    assert "evidence_spans" not in evaluation.ANNOTATION_FIELDS
    assert all(case["reference_labels"][field] is None for field in evaluation.EXTENDED_ANNOTATION_FIELDS)
    assert "SECRET_MODEL_EXPLANATION" not in json.dumps(form)
    # Existing references remain byte-equivalent after import; no fabricated
    # proposition, span or reporting-chain label is filled on their behalf.
    row = row_for(case, labels())
    evaluation.import_reviews(reviewed_store, "p", created["id"], [row], reviewer_id="test-human")
    stored = json.loads(reviewed_store.one("SELECT payload_json FROM v4_reference_reviews")["payload_json"])
    assert stored["labels"] == labels()


def test_unicode_spans_chain_and_context_roundtrip_with_independent_adjudication(reviewed_store):  # noqa: F811
    created, case, _ = scaffold_case(reviewed_store, body="🧭 Café e\u0301 policy: equal access. A later sentence preserves context.")
    reference = extended_labels(case)
    originals = reviewed_store.query("SELECT id,payload_json FROM v3_assessments ORDER BY id")
    for reviewer in ("human-A", "human-B"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id=reviewer)
    adjudicated = evaluation.adjudicate_case(reviewed_store, "p", created["id"], case["id"], reference, actor_id="test-adjudicator")
    assert adjudicated["labels"] == reference
    assert adjudicated["origin"] == "human_reference_adjudication"
    report = evaluation.reference_report(reviewed_store, "p", created["id"])
    progress = report["structured_annotation_progress"]
    assert all(count == 1 for count in progress["cases_with_field_annotated"].values())
    assert report["interpretation_accuracy"] is None and report["heldout_metrics_locked"]
    assert reviewed_store.query("SELECT id,payload_json FROM v3_assessments ORDER BY id") == originals
    assert evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="human-A")["idempotent_duplicates"] == 1


@pytest.mark.parametrize("mutation", ["document", "block", "quote", "offset_type", "offset_range", "unknown_span_key", "chain_link", "chain_role", "act_type"])
def test_forged_or_malformed_annotations_never_write_reference_rows(reviewed_store, mutation):  # noqa: F811
    created, case, _ = scaffold_case(reviewed_store)
    reference = extended_labels(case)
    span = reference["evidence_spans"][0]
    if mutation == "document":
        span["document_id"] = "d1"
    elif mutation == "block":
        span["block_id"] = "nonexistent"
    elif mutation == "quote":
        span["quote"] = "A fabricated accusation absent from the source."
    elif mutation == "offset_type":
        span["start"] = False
    elif mutation == "offset_range":
        span["end"] = 1_000_000
    elif mutation == "unknown_span_key":
        span["model_confidence"] = 0.99
    elif mutation == "chain_link":
        reference["reporting_chain"][0]["evidence_span_indices"] = [32]
    elif mutation == "chain_role":
        reference["reporting_chain"][0]["role"] = []
    elif mutation == "act_type":
        reference["communicative_act"] = []
    with pytest.raises(ValueError):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="test-human")
    assert reviewed_store.one("SELECT COUNT(*) AS n FROM v4_reference_reviews")["n"] == 0


def test_real_source_span_outside_blinded_context_is_rejected(reviewed_store):  # noqa: F811
    body = "Sentence in a long source. " * 180
    created, case, _ = scaffold_case(reviewed_store, body=body, context=0)
    reference = extended_labels(case)
    reference["evidence_spans"] = [{"document_id": "d0", "block_id": "b", "start": 4000, "end": 4020, "quote": body[4000:4020]}]
    with pytest.raises(ValueError, match="outside the displayed"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="test-human")


def test_frozen_source_changes_block_structured_import_and_adjudication(reviewed_store):  # noqa: F811
    created, case, _ = scaffold_case(reviewed_store)
    reference = extended_labels(case)
    for reviewer in ("human-A", "human-B"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id=reviewer)
    row = reviewed_store.one("SELECT blocks_key FROM documents WHERE id='d0'")
    reviewed_store.put_json(row["blocks_key"], [{"id": "b", "text": "Replaced source."}])
    with pytest.raises(ValueError, match="blocks changed"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="human-C")
    with pytest.raises(ValueError, match="blocks changed"):
        evaluation.adjudicate_case(reviewed_store, "p", created["id"], case["id"], reference, actor_id="test-adjudicator")


def test_annotations_and_nested_reviews_are_immutable_and_require_human_identity(reviewed_store):  # noqa: F811
    created, case, _ = scaffold_case(reviewed_store)
    reference = extended_labels(case)
    with pytest.raises(ValueError, match="human"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="model", actor_kind="agent")
    evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, reference)], reviewer_id="human-A")
    modified = copy.deepcopy(reference)
    modified["proposition"] = "A different human interpretation."
    with pytest.raises(ValueError, match="immutable"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, modified)], reviewer_id="human-A")
    with pytest.raises(ValueError, match="not found in this project"):
        evaluation.import_reviews(reviewed_store, "another-project", created["id"], [row_for(case, reference)], reviewer_id="human-A")


def test_unannotated_optional_fields_stay_null_and_new_judgments_require_frozen_source(reviewed_store):  # noqa: F811
    created, case, _ = scaffold_case(reviewed_store)
    blank = {**labels(), **{field: None for field in evaluation.EXTENDED_ANNOTATION_FIELDS}}
    evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, blank)], reviewer_id="human-A")
    stored = json.loads(reviewed_store.one("SELECT payload_json FROM v4_reference_reviews")["payload_json"])
    assert all(stored["labels"][field] is None for field in evaluation.EXTENDED_ANNOTATION_FIELDS)
    reviewed_store.execute("UPDATE documents SET sha256='changed' WHERE id='d0'")
    with pytest.raises(ValueError, match="frozen version changed"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row_for(case, {**labels(), "target": "Human target judgment"})], reviewer_id="human-B")
