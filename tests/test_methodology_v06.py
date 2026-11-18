"""Synthetic software checks; none of these labels are benchmark evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from doxa import evaluation
from doxa.evaluation_metrics import criterion_metrics, exact_binomial_lower, reviewer_agreement, span_concordance
from doxa.method_cards import freeze_method_cards, get_method_card, study_protocol, validate_snapshot
from doxa.service_profiles import report_brief, validate_brief
from test_evaluation_v04 import labels, reviewed_store  # noqa: F401


def record(index, prediction="meets_criterion", reference="meets_criterion", cluster=None):
    return {"id": str(index), "cluster": cluster or str(index), "lens_id": "discourse", "criterion_id": "framing",
            "prediction": {"result": prediction, "attribution": "author"},
            "reference": {"result": reference, "attribution": "author"},
            "reviews": [{"reviewer_id": reviewer, "labels": {"result": reference}} for reviewer in ("synthetic-A", "synthetic-B")],
            "separate_adjudicator": True}


def protocol():
    return {"profile_id": "public", "domain": "Test-only synthetic software fixtures", "language": "en",
            "sampling_design": "enriched_diagnostic", "population_description": "No real population is sampled in this test.",
            "confidence_method": "exact_binomial", "cluster_key": "story_group",
            "sampling_frame_sha256": "a" * 64, "reviewer_protocol_sha256": "b" * 64}


def test_method_cards_freeze_rules_and_distinguish_citation_from_validation():
    snapshot = freeze_method_cards(["discourse", "ihra", "political-framing"])
    validate_snapshot(snapshot)
    assert len(snapshot["cards"]) == 13
    for card in snapshot["cards"]:
        assert card["operational_rule"] and card["references"] and card["required_context"]
        assert card["validation"]["independent_interpretation_accuracy"] is None
        assert all(reference["does_not_establish"] for reference in card["references"])
    changed = deepcopy(snapshot)
    changed["cards"][0]["operational_rule"] = "An unsupported replacement"
    with pytest.raises(ValueError, match="snapshot hash"):
        validate_snapshot(changed)
    first = get_method_card("discourse", "stance")
    first["references"][0]["title"] = "Mutated client value"
    assert get_method_card("discourse", "stance")["references"][0]["title"] != "Mutated client value"


def test_new_briefs_freeze_cards_and_published_legacy_briefs_remain_identical():
    new = validate_brief({"profile_id": "public"})
    assert new["schema_version"].endswith("1.1")
    validate_snapshot(new["method_cards_snapshot"])
    for profile in ("company", "public", "personal"):
        original = json.loads((Path(__file__).resolve().parents[1] / f"examples/v05-pilot-demo-{profile}.input.json").read_text())["brief"]
        assert report_brief(original)["brief"] == original
    changed = deepcopy(new)
    changed["method_cards_snapshot"]["cards"][0]["operational_rule"] = "Invented"
    with pytest.raises(ValueError, match="snapshot hash"):
        report_brief(changed)


def test_exact_one_sided_bounds_match_known_binomial_results():
    assert exact_binomial_lower(100, 100) == pytest.approx(0.9704869503929601)
    assert exact_binomial_lower(1, 1) == pytest.approx(0.05)
    assert exact_binomial_lower(0, 100) == 0
    assert exact_binomial_lower(0, 0) is None
    assert exact_binomial_lower(95, 100) == pytest.approx(0.897746622356, abs=1e-9)
    with pytest.raises(ValueError):
        exact_binomial_lower(True, 100)


def test_abstention_and_unprocessed_positives_reduce_recall_and_coverage():
    cases = [record(0), record(1, "uncertain"), record(2, None), record(3, "meets_criterion", "uncertain")]
    result = criterion_metrics(cases)["criterion_metrics"][0]
    assert result["binary"]["precision"] == 1
    assert result["binary"]["recall"] == pytest.approx(1 / 3)
    assert result["binary"]["ambiguous_reference_cases_excluded_from_binary"] == 1
    assert result["non_abstained_decision_coverage"] == 0.5
    assert result["model_abstentions"] == result["unprocessed_cases"] == 1
    assert not result["release_gate"]["supported_finding_thresholds_met"]


def test_enriched_cases_and_correlated_spans_cannot_manufacture_release_gate():
    cases = [record(index, cluster="one-story") for index in range(200)]
    result = criterion_metrics(cases, protocol=protocol(), heldout_complete=True)["criterion_metrics"][0]
    assert result["independent_predicted_positive_clusters"] == 1
    assert not result["release_gate"]["supported_finding_thresholds_met"]
    assert "exact_binomial_requires_one_primary_case_per_independent_cluster" in result["release_gate"]["blocking_reasons"]
    assert "diagnostic_or_unverified_sampling_design" in result["release_gate"]["blocking_reasons"]


def test_quantitative_pass_does_not_certify_humans_or_authorize_publication():
    cases = [record(index) for index in range(100)]
    designed = {**protocol(), "sampling_design": "probability_sample"}
    result = criterion_metrics(cases, protocol=designed, heldout_complete=True)["criterion_metrics"][0]
    assert result["release_gate"]["supported_finding_thresholds_met"]
    assert result["release_gate"]["automated_detection_thresholds_met"]
    assert not result["release_gate"]["publication_authorized"]
    assert result["release_gate"]["external_checks"]
    cases[0]["separate_adjudicator"] = False
    assert not criterion_metrics(cases, protocol=designed, heldout_complete=True)["criterion_metrics"][0]["release_gate"]["supported_finding_thresholds_met"]


def test_bootstrap_resamples_whole_clusters_reproducibly_and_blocks_degeneracy():
    cases = [record(index, "does_not_meet" if index % 11 == 0 else "meets_criterion",
                    "does_not_meet" if index % 13 == 0 else "meets_criterion", cluster=str(index // 2)) for index in range(100)]
    design = {**protocol(), "confidence_method": "cluster_bootstrap", "bootstrap_replicates": 999, "seed": 7}
    first = criterion_metrics(cases, protocol=design, heldout_complete=True)
    assert first == criterion_metrics(cases, protocol=design, heldout_complete=True)
    assert first["criterion_metrics"][0]["one_sided_95_lower"]["precision"] < 1
    perfect = criterion_metrics([record(i) for i in range(100)], protocol=design, heldout_complete=True)["criterion_metrics"][0]
    assert "degenerate_bootstrap_requires_prespecified_rare_error_procedure" in perfect["release_gate"]["blocking_reasons"]


def test_span_overlap_uses_unicode_unions_and_excludes_fallback_anchors():
    case = record(0)
    case["prediction"].update({"detail_supplied": True, "evidence_refs": [
        {"document_id": "d", "block_id": "b", "start": 0, "end": 5},
        {"document_id": "d", "block_id": "b", "start": 3, "end": 7}]})
    case["reference"]["evidence_spans"] = [{"document_id": "d", "block_id": "b", "start": 2, "end": 7}]
    result = span_concordance([case])
    assert result["unicode_character_precision"] == pytest.approx(5 / 7)
    assert result["unicode_character_recall"] == 1
    case["prediction"]["detail_supplied"] = False
    assert span_concordance([case])["compared_cases"] == 0


def test_reviewer_disagreement_is_not_hidden_by_adjudication():
    first, second = record(0), record(1)
    second["reviews"][1]["labels"]["result"] = "does_not_meet"
    pair = reviewer_agreement([first, second])["pairs"][0]
    assert pair["observed_agreement"] == 0.5
    assert pair["cohen_kappa"] == 0


def test_preregistration_is_before_labels_immutable_and_cannot_relabel_sample(reviewed_store):  # noqa: F811
    created = evaluation.create_scaffold(reviewed_store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    with pytest.raises(ValueError, match="cannot be relabeled"):
        evaluation.preregister_protocol(reviewed_store, "p", created["id"], {**protocol(), "sampling_design": "probability_sample"}, actor_id="operator")
    first = evaluation.preregister_protocol(reviewed_store, "p", created["id"], protocol(), actor_id="operator")
    assert first == evaluation.preregister_protocol(reviewed_store, "p", created["id"], protocol(), actor_id="operator")
    with pytest.raises(ValueError, match="immutable"):
        evaluation.preregister_protocol(reviewed_store, "p", created["id"], {**protocol(), "domain": "Changed"}, actor_id="operator")
    case = evaluation.export_review_form(reviewed_store, "p", created["id"])["cases"][0]
    row = {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"], "labels": labels(), "independent_blinded_review": True}
    for reviewer in ("test-A", "test-B"):
        evaluation.import_reviews(reviewed_store, "p", created["id"], [row], reviewer_id=reviewer)
    with pytest.raises(ValueError, match="separate adjudicator"):
        evaluation.adjudicate_case(reviewed_store, "p", created["id"], case["id"], labels(), actor_id="test-A")
    evaluation.adjudicate_case(reviewed_store, "p", created["id"], case["id"], labels(), actor_id="test-C")
    report = evaluation.reference_report(reviewed_store, "p", created["id"])
    assert report["reference_metrics"]["heldout"] is None
    assert report["preregistration"]["protocol_sha256"] == first["protocol_sha256"]
    assert not report["release_readiness"]["automated_verdicts_enabled"]


def test_unregistered_existing_review_cannot_backdate_protocol(reviewed_store):  # noqa: F811
    created = evaluation.create_scaffold(reviewed_store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    case = evaluation.export_review_form(reviewed_store, "p", created["id"])["cases"][0]
    evaluation.import_reviews(reviewed_store, "p", created["id"], [{"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"], "labels": labels(), "independent_blinded_review": True}], reviewer_id="test-A")
    with pytest.raises(ValueError, match="precede"):
        evaluation.preregister_protocol(reviewed_store, "p", created["id"], protocol(), actor_id="operator")


def test_external_study_protocol_contains_no_manufactured_progress():
    plan = study_protocol()
    assert plan["status"] == "requires_external_human_work"
    assert all(panel["collected_clusters"] == panel["independent_reference_labels"] == 0 for panel in plan["domain_panels"])


def test_reader_perception_keeps_real_reader_ids_contexts_and_ordinal_distributions(reviewed_store):  # noqa: F811
    created = evaluation.create_scaffold(reviewed_store, 'p', 'r', development_count=2, heldout_count=2, actor_id='test-operator')
    case = evaluation.export_review_form(reviewed_store, 'p', created['id'])['cases'][0]
    for index, point in enumerate((1, 3, 5)):
        reference = {**labels(), 'reader_perception': {'scale_version': 'reader-perception-ordinal-1.0',
                     'reader_context': 'Synthetic reader in the same supplied audience and communication goal.', 'ratings': {'clarity': point}}}
        evaluation.import_reviews(reviewed_store, 'p', created['id'], [{'case_id': case['id'], 'case_sha256': case['case_sha256'],
                                  'source_hash': case['source_hash'], 'independent_blinded_review': True, 'labels': reference}], reviewer_id=f'synthetic-reader-{index}')
    report = evaluation.reference_report(reviewed_store, 'p', created['id'])
    reading = report['reader_perception']['cases'][0]
    assert reading['distinct_authenticated_reader_ids'] == 3
    assert reading['dimensions']['clarity']['ordinal_distribution'] == {'1': 1, '2': 0, '3': 1, '4': 0, '5': 1}
    assert reading['dimensions']['clarity']['minimum_reader_count_met']
    assert not reading['dimensions']['perceived_hostility']['minimum_reader_count_met']
    assert not report['reader_perception']['actual_emotional_state_inferred']
    bad = {**labels(), 'reader_perception': {'scale_version': 'reader-perception-ordinal-1.0', 'reader_context': 'Test', 'ratings': {'clarity': True}}}
    with pytest.raises(ValueError, match='ordinal integers'):
        evaluation._labels(bad)
