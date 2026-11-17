"""Source, methodological and financial boundaries for guided service profiles."""
from copy import deepcopy
import math

import pytest

from doxa.contextual import build_units
from doxa.lenses import get_lens
from doxa.service_profiles import (
    estimate_analysis,
    get_profile,
    list_profiles,
    observe_communication,
    report_brief,
    validate_brief,
)


def test_all_three_profiles_have_distinct_constructs_and_pending_references():
    profiles = list_profiles()
    assert {item["id"] for item in profiles} == {"company", "public", "personal"}
    for profile in profiles:
        assert profile["readiness"]["reference_status"] == "reference_pending"
        assert profile["readiness"]["expert_review_required"]
        assert not profile["readiness"]["automated_verdicts_enabled"]
        assert not profile["readiness"]["interpretation_accuracy_measured"]
        assert {item["id"] for item in profile["construct_definitions"]} == set(profile["constructs"])
        assert profile["references"] and profile["limitations"]
        for lens_id in profile["default_lens_ids"]:
            assert get_lens(lens_id)
    assert "reader_perception_requiring_ratings" in get_profile("personal")["constructs"]
    assert "comparator_dependent_asymmetry" in get_profile("public")["constructs"]
    assert "declared_policy_alignment" in get_profile("company")["constructs"]
    profiles[0]["readiness"]["reference_status"] = "fake"
    profiles[0]["construct_definitions"][0]["limit"] = "fake"
    assert get_profile("company")["readiness"]["reference_status"] == "reference_pending"
    assert get_profile("company")["construct_definitions"][0]["limit"] != "fake"


def test_customer_brief_preserves_goal_audience_and_independent_frameworks():
    brief = validate_brief({"profile_id": "personal", "question": " Keep my firmness? ",
                            "audience": "My project collaborator", "perspective": "A professional reply",
                            "lens_ids": ["ihra", "jda", "ihra"]})
    assert brief["question"] == "Keep my firmness?"
    assert brief["audience"] == "My project collaborator"
    assert brief["lens_ids"] == ["ihra", "jda"]
    assert brief["company_policy"] is None
    assert brief["comparator_status"] == "not_declared"
    assert brief["readiness"]["expert_review_required"]
    assert len(brief["fingerprint"]) == 64


@pytest.mark.parametrize("payload", [None, [], {"profile_id": "unknown"},
    {"profile_id": "personal", "readiness": {"reference_status": "validated"}},
    {"profile_id": "personal", "question": " "}, {"profile_id": "public", "audience": 42},
    {"profile_id": "company", "lens_ids": []}, {"profile_id": "company", "lens_ids": ["missing"]},
    {"profile_id": "company", "lens_ids": [False]}, {"profile_id": "company", "reviewer": "fake expert"}])
def test_invalid_or_self_endorsed_customer_briefs_are_rejected(payload):
    with pytest.raises(ValueError):
        validate_brief(payload)


def test_policy_requires_ownership_version_and_company_scope():
    policy = {"title": "Communications policy", "version": "2026-11", "text": "State deadlines explicitly."}
    brief = validate_brief({"profile_id": "company", "company_policy": policy})
    assert brief["company_policy"] == policy
    assert brief["policy_alignment_status"] == "policy_supplied_unverified"
    with pytest.raises(ValueError):
        validate_brief({"profile_id": "company", "company_policy": {**policy, "version": ""}})
    with pytest.raises(ValueError):
        validate_brief({"profile_id": "personal", "company_policy": policy})


def test_comparator_declaration_never_certifies_matching_or_omission():
    comparator = {"question": "How is the same event framed?", "inclusion_rules": "News reports published in this interval",
                  "topic": "A declared event", "genres": ["news", "news"],
                  "date_start": "2026-10-01", "date_end": "2026-10-31",
                  "source_groups": [{"label": "A", "document_ids": ["a1", "a1"]},
                                    {"label": "B", "document_ids": ["b1"]}]}
    brief = validate_brief({"profile_id": "public", "comparator": comparator})
    assert brief["comparator_status"] == "declared_unverified"
    assert brief["comparator"]["source_groups"][0]["document_ids"] == ["a1"]
    assert brief["comparator"]["genres"] == ["news"]
    for change in [{"date_end": "2026-09-30"}, {"date_start": "2026-02-30"}, {"date_start": "20261001"},
                   {"source_groups": [{"label": "A"}, {"label": "A"}]}, {"inclusion_rules": ""},
                   {"proven_omission": True}]:
        with pytest.raises(ValueError):
            validate_brief({"profile_id": "public", "comparator": {**comparator, **change}})


def test_observations_preserve_unicode_source_spans_and_avoid_tone_verdicts():
    text = "🧭 José: Perhaps we will review this. Please reply before Friday.\nSend the update."
    output = observe_communication([{"id": "b1", "text": text}])
    assert {item["kind"] for item in output["observations"]} == {
        "request_marker", "future_commitment_marker", "time_constraint_marker", "hedge_marker", "imperative_form"}
    for item in output["observations"]:
        assert text[item["start"]:item["end"]] == item["quote"]
        assert item["offset_unit"] == "unicode_code_points"
        assert item["attribution"] == "not_assessed"
        assert item["provenance"]["origin"] == "deterministic_rule"
        assert not item["provenance"]["model_inference"]
        assert not {"tone_score", "emotion", "political_identity", "certainty", "bias_score"} & item.keys()
    assert output["coverage"]["source_scan_complete"]
    assert output["coverage"]["observations_complete"]
    assert output["readiness"]["reference_status"] == "reference_pending"


def test_quote_forward_signature_and_unknown_mail_roles_not_counted_as_authored():
    blocks = [{"id": str(index), "text": "Please reply before Friday.", "mail_role": role}
              for index, role in enumerate(["authored", "quoted", "forwarded", "signature", "header", "unknown"])]
    blocks.append({"id": "repeated", "text": "Please reply before Friday.", "mail_role": "authored", "ineligible_for_author_counts": True})
    output = observe_communication(blocks, "company")
    assert output["counts"]["request_marker"] == 1
    assert output["counts"]["time_constraint_marker"] == 1
    assert output["coverage"]["authored_eligible_blocks"] == 1
    assert output["coverage"]["excluded_context_blocks"] == 6
    assert len(output["observations"]) == 14
    assert sum(item["count_eligible"] for item in output["observations"]) == 2


def test_negative_commitment_and_absent_markers_do_not_become_positive_or_negative_verdicts():
    output = observe_communication([{"id": "a", "text": "We will not deliver. I shall never promise that."},
                                    {"id": "b", "text": "A plain descriptive sentence."}])
    assert output["counts"]["future_commitment_marker"] == 0
    assert output["observations"] == []
    assert any("No match is not a negative" in item for item in output["limitations"])


def test_observation_limit_discloses_omissions_but_scans_and_counts_complete_input():
    output = observe_communication([{"id": "a", "text": "Please. Perhaps. Please. Perhaps."}], max_observations=1)
    assert len(output["observations"]) == 1
    assert output["coverage"]["rule_matches"] == 4
    assert output["coverage"]["observations_omitted"] == 3
    assert output["coverage"]["source_scan_complete"]
    assert not output["coverage"]["observations_complete"]
    assert output["counts"]["request_marker"] == 2
    assert output["counts"]["hedge_marker"] == 2


def test_duplicate_block_ids_rejected_within_document_but_allowed_across_documents():
    with pytest.raises(ValueError):
        observe_communication([{"id": "b", "text": "Please."}, {"id": "b", "text": "Perhaps."}])
    output = observe_communication([{"document_id": "d1", "id": "b", "text": "Please."},
                                    {"document_id": "d2", "id": "b", "text": "Please."}])
    assert len({item["id"] for item in output["observations"]}) == 2


def test_estimate_uses_actual_units_and_words_instead_of_conflicting_metadata():
    blocks = [{"id": "a", "text": ("An exact contextual sentence. " * 70) + "A Unicode ending 🧭."}]
    documents = [{"id": "d1", "blocks": blocks, "word_count": 999999}]
    brief = validate_brief({"profile_id": "public"})
    output = estimate_analysis(documents, brief)
    assert output["source"]["words"] == len(blocks[0]["text"].split())
    assert output["source"]["characters"] == len(blocks[0]["text"])
    assert output["work"]["actual_units"] == len(build_units(documents[0]))
    assert output["work"]["estimated_units"] == 0
    assert output["work"]["criterion_count"] == sum(len(get_lens(item)["criteria"]) for item in brief["lens_ids"])
    assert output["work"]["criterion_decisions"] == output["work"]["actual_units"] * output["work"]["criterion_count"]
    assert not output["tokens"]["tokenizer_measured"]
    assert output["modeled_cost"] is None
    assert output["cost_status"] == "rates_not_supplied"


def test_metadata_fallback_is_explicitly_estimated_and_empty_source_cost_is_zero():
    brief = validate_brief({"profile_id": "company"})
    output = estimate_analysis([{"id": "metadata", "word_count": 2000}], brief)
    assert output["source"]["characters"] is None
    assert output["source"]["metadata_only_documents"] == 1
    assert output["work"]["actual_units"] == 0
    assert output["work"]["estimated_units"] > 0
    empty = estimate_analysis([], brief, prices={"input_usd_per_million": 1, "output_usd_per_million": 2})
    assert empty["work"]["total_planning_units"] == 0
    assert empty["modeled_cost"]["low"] == empty["modeled_cost"]["high"] == 0


def test_cost_is_modeled_from_explicit_prices_and_cannot_be_mistaken_for_customer_charge():
    output = estimate_analysis([{"id": "a", "blocks": [{"id": "b", "text": "Please review this."}]}],
                               {"profile_id": "personal"}, prices={"input_usd_per_million": 1.5, "output_usd_per_million": 4.0})
    tokens, cost = output["tokens"], output["modeled_cost"]
    assert cost["low"] == round((tokens["input_low"] * 1.5 + tokens["output_low"] * 4) / 1000000, 8)
    assert cost["high"] > cost["low"]
    assert not cost["customer_price"] and not cost["vendor_invoice"]
    assert "expert review" in output["excluded_costs"]
    assert any("detail pass" in item for item in output["assumptions"])


@pytest.mark.parametrize("value", [True, -1, math.nan, math.inf, "1", 10**400])
def test_invalid_financial_rates_rejected(value):
    with pytest.raises(ValueError):
        estimate_analysis([], {"profile_id": "company"},
                          prices={"input_usd_per_million": value, "output_usd_per_million": 2})


def test_modified_or_future_frozen_brief_is_not_silently_reinterpreted():
    brief = validate_brief({"profile_id": "public"})
    changed = deepcopy(brief)
    changed["question"] = "A different analysis"
    with pytest.raises(ValueError, match="fingerprint"):
        estimate_analysis([], changed)
    with pytest.raises(ValueError, match="version"):
        report_brief({**brief, "method_version": "future"})


def test_task_specific_report_brief_always_requires_expert_signoff():
    for profile in list_profiles():
        output = report_brief(validate_brief({"profile_id": profile["id"]}))
        assert output["service"] == profile["name"]
        assert output["sections"] == profile["report_sections"]
        assert output["review_status"] == "expert_review_required"
        assert output["brief"]["readiness"]["reference_status"] == "reference_pending"
    with pytest.raises(ValueError):
        report_brief(None)


def test_duplicate_source_selection_and_invalid_units_refuse_misleading_estimate():
    with pytest.raises(ValueError, match="duplicate"):
        estimate_analysis([{"id": "a"}, {"id": "a"}], {"profile_id": "company"})
    with pytest.raises(ValueError):
        estimate_analysis([], {"profile_id": "company"}, max_unit_chars=True)
    with pytest.raises(ValueError):
        estimate_analysis([{"word_count": True}], {"profile_id": "company"})
