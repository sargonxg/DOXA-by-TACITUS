"""Executable measurement contracts, not claims of validated interpretation.

Cards freeze the operational rubric and the limited scholarly claim it borrows.
Framework definitions are identified separately from empirical research. A
reference supports a design choice; it does not validate DOXA's implementation.
"""
from __future__ import annotations

from copy import deepcopy

from .lineage import content_sha256
from .lenses import LENSES, SOURCE_SCOPE, get_lens, validate_custom_lens

METHOD_CARD_VERSION = "doxa-measurement-card-1.0"
REGISTRY_VERSION = "doxa-measurement-registry-0.6"

REFERENCES = {
    "measurement": {
        "title": "Measurement and Fairness", "authors": "Jacobs and Wallach", "year": 2021,
        "url": "https://arxiv.org/abs/1912.05511", "kind": "measurement_framework",
        "supported_claim": "An operational measure needs an explicit construct, assumptions and validity argument.",
        "does_not_establish": "Validity or accuracy of any DOXA criterion or model.",
    },
    "framing": {
        "title": "Framing: Toward Clarification of a Fractured Paradigm", "authors": "Entman", "year": 1993,
        "url": "https://doi.org/10.1111/j.1460-2466.1993.tb01304.x", "kind": "theoretical_framework",
        "supported_claim": "Problem definition, causal interpretation, evaluation and remedies are distinct framing functions.",
        "does_not_establish": "That a frame is unfair, false, prejudiced or influential.",
    },
    "frames_corpus": {
        "title": "The Media Frames Corpus: Annotations of Frames Across Issues", "authors": "Card et al.", "year": 2015,
        "url": "https://aclanthology.org/P15-2072/", "kind": "annotation_research",
        "supported_claim": "Frames can be operationalized through explicit annotation instructions and contextual text spans.",
        "does_not_establish": "Transfer accuracy to DOXA domains, or permission to redistribute underlying licensed news.",
    },
    "stance": {
        "title": "SemEval-2016 Task 6: Detecting Stance in Tweets", "authors": "Mohammad et al.", "year": 2016,
        "url": "https://aclanthology.org/S16-1003/", "kind": "task_definition",
        "supported_claim": "Stance is toward a specified target and is distinct from sentiment; absent evidence is not neutrality.",
        "does_not_establish": "Document-level stance accuracy or an author's political identity.",
    },
    "basil": {
        "title": "In Plain Sight: Media Bias Through the Lens of Factual Reporting", "authors": "Fan et al.", "year": 2019,
        "url": "https://aclanthology.org/D19-1664/", "kind": "comparative_annotation_research",
        "supported_claim": "Lexical and informational bias are distinct; comparison needs related source material and context.",
        "does_not_establish": "Intentional omission or unfairness from an isolated source.",
    },
    "variation": {
        "title": "The Problem of Human Label Variation: On Ground Truth in Data, Modeling and Evaluation", "authors": "Plank", "year": 2022,
        "url": "https://aclanthology.org/2022.emnlp-main.731/", "kind": "evaluation_methodology",
        "supported_claim": "Meaningful differences in human interpretation should be retained rather than treated automatically as noise.",
        "does_not_establish": "That model agreement or one adjudication is universal ground truth.",
    },
}


def _reference_keys(lens_id, criterion_id):
    keys = ["measurement", "variation"]
    if criterion_id in {"stance", "expressed_policy_position"}:
        keys.append("stance")
    if lens_id in {"discourse", "political-framing", "language-norms"}:
        keys.extend(["framing", "frames_corpus"])
    if criterion_id in {"evaluative_language", "source_voice_foregrounding", "comparator_dependent_asymmetry"}:
        keys.append("basil")
    return list(dict.fromkeys(keys))


def card_from_pack(pack: dict, criterion_id: str) -> dict:
    criterion = next((item for item in pack.get("criteria", []) if item["id"] == criterion_id), None)
    if criterion is None:
        raise ValueError("Criterion not found in the declared lens")
    custom = pack["id"] == "custom"
    kind = criterion.get("kind", "descriptive")
    comparator = criterion_id in {"comparator_dependent_asymmetry", "institutional_pattern"}
    card = {
        "schema_version": METHOD_CARD_VERSION, "registry_version": REGISTRY_VERSION,
        "id": f"{pack['id']}/{criterion_id}", "lens_id": pack["id"], "lens_version": pack["version"],
        "criterion_id": criterion_id, "criterion_kind": kind, "label": criterion["label"],
        "construct": "descriptive_discourse_pattern" if kind == "descriptive" else "framework_boundary" if kind == "boundary" else "framework_specific_indicator",
        "operational_rule": criterion["description"], "scope": pack.get("scope", ""),
        "unit_of_analysis": "An exact contextual passage and explicitly attributed speaker, target or proposition.",
        "required_context": [pack.get("context_rules", ""), "Resolve quotation, reporting, negation and counter-speech before attributing endorsement.",
                             "Retain heading, message and source-version lineage; record unavailable context."],
        "inclusion_rule": "The supplied passage supports the operational criterion under the declared framework and context.",
        "exclusion_rules": ["Topic or identity-term mention alone is not sufficient.",
                            "Quoted or condemned expression is not automatically author endorsement.",
                            "Unprocessed material is not a negative result.",
                            "Insufficient context remains uncertain; no private beliefs, identity or intent are inferred."],
        "comparator_required": comparator,
        "alternative_readings": "Preserve defensible context- or framework-dependent alternatives and reviewer disagreements.",
        "output_contract": ["meets_criterion", "does_not_meet", "not_applicable", "uncertain"],
        "permitted_report_wording": "The passage was read as meeting this named operational criterion, with stated evidence and review status.",
        "prohibited_conclusions": ["Universal bias score", "Character or private-belief verdict", "Legal liability", "Established causal or audience effect"],
        "references": [deepcopy(REFERENCES[key]) for key in _reference_keys(pack["id"], criterion_id)],
        "framework_source": {"url": pack.get("source_url", ""), "kind": "user_authored_rubric" if custom else "operational_definition_source",
                             "status": "not_independently_validated_by_DOXA"},
        "validation": {"status": "reference_pending", "independent_interpretation_accuracy": None,
                       "notice": "Citations motivate the method; they do not certify DOXA or establish transfer accuracy."},
        "origin": "user_authored_operational_contract" if custom else "DOXA_operational_measurement_contract",
    }
    if criterion.get("applicability") == SOURCE_SCOPE:
        card["applicability"] = SOURCE_SCOPE
        card["required_context"].append(
            "Every selected source is eligible for this criterion. A source with only inapplicable units stays uncertain."
        )
    card["card_sha256"] = content_sha256(card)
    return card


def get_method_card(lens_id: str, criterion_id: str, *, custom_lens=None) -> dict:
    pack = validate_custom_lens(custom_lens) if lens_id == "custom" and custom_lens else get_lens(lens_id)
    return card_from_pack(pack, criterion_id)


def freeze_method_cards(lens_ids=None, custom_lens=None) -> dict:
    ids = [pack["id"] for pack in LENSES] if lens_ids is None else list(lens_ids)
    if len(ids) > 12 or any(not isinstance(value, str) for value in ids):
        raise ValueError("Choose at most 12 named lens packs")
    cards, seen = [], set()
    for identity in ids:
        pack = validate_custom_lens(custom_lens) if identity == "custom" and custom_lens else get_lens(identity)
        if pack["id"] in seen:
            continue
        seen.add(pack["id"])
        cards.extend(card_from_pack(pack, criterion["id"]) for criterion in pack["criteria"])
    value = {"registry_version": REGISTRY_VERSION, "origin": "frozen_registry_at_brief_creation", "cards": cards}
    value["snapshot_sha256"] = content_sha256(value)
    return value


def validate_snapshot(value: dict) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("cards"), list) or len(value["cards"]) > 120:
        raise ValueError("Invalid method-card snapshot")
    if content_sha256({key: item for key, item in value.items() if key != "snapshot_sha256"}) != value.get("snapshot_sha256"):
        raise ValueError("Method-card snapshot hash changed")
    for card in value["cards"]:
        if (not isinstance(card, dict) or any(not isinstance(card.get(key), str) or not card[key] for key in ("id", "lens_id", "criterion_id", "operational_rule", "schema_version"))
                or card["id"] != f"{card['lens_id']}/{card['criterion_id']}"
                or content_sha256({key: item for key, item in card.items() if key != "card_sha256"}) != card.get("card_sha256")):
            raise ValueError("Frozen method-card hash changed")


def study_protocol() -> dict:
    """A collection contract with no pretend participants, labels or results."""
    return {"version": "doxa-independent-study-0.6", "status": "requires_external_human_work", "template_not_live_project_counters": True,
            "news_protocol": {"prepared_cases": 200, "selection": "enriched diagnostic", "population_accuracy_claim": False,
                              "parent_outputs_public": True, "reviewer_exposure_must_be_recorded": True},
            "domain_panels": [{"profile_id": profile, "target_independent_clusters": 1000,
                               "development": 400, "calibration": 200, "locked_test": 400,
                               "collected_clusters": 0, "independent_reference_labels": 0}
                              for profile in ("company", "public", "personal")],
            "collection_rules": ["Obtain source rights and private-data permissions before collection.",
                                 "Use multiple organizations or source families; record sampling probabilities and exclusions.",
                                 "Assign company, thread/story and near-duplicate groups wholly to one split.",
                                 "Keep probability samples separate from enriched diagnostic cases.",
                                 "Use two qualified, model-blind independent reviewers and a separate adjudicator.",
                                 "Use at least three context-specific reader ratings for perception claims; retain the distribution.",
                                 "Preregister methods and stopping rules before reviews; never train or tune on locked test cases."],
            "external_requirements": ["qualified compensated reviewers", "independent adjudication", "authorized representative source collection", "exposure and rights records"],
            "fabricated_reviewers_or_labels": False, "interpretation_accuracy": None}
