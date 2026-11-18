"""Explicit, budgeted execution of reviewed functional fixtures.

Default preparation is offline. Execution records validated model outcomes; it
does not invent expected labels or convert fixtures into population accuracy.
"""
from __future__ import annotations

import copy
import hashlib
import math
from urllib.parse import urlsplit

from .lineage import content_sha256


def validate_casebook(value):
    if not isinstance(value, dict) or not isinstance(value.get("fixtures"), list) or not 1 <= len(value["fixtures"]) <= 240:
        raise ValueError("A functional casebook requires 1–240 bounded fixtures")
    expected = content_sha256({key: item for key, item in value.items() if key != "casebook_sha256"})
    if expected != value.get("casebook_sha256"):
        raise ValueError("Functional casebook hash changed")
    seen = set()
    for fixture in value["fixtures"]:
        if not isinstance(fixture, dict) or fixture.get("id") in seen:
            raise ValueError("Functional fixture IDs must be unique")
        seen.add(fixture["id"])
        text = fixture.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 16000:
            raise ValueError("Functional text must contain 1–16000 characters")
        if hashlib.sha256(text.encode()).hexdigest() != fixture.get("derived_text_sha256"):
            raise ValueError("Derived fixture text hash changed")
        if content_sha256({key: item for key, item in fixture.items() if key != "fixture_sha256"}) != fixture.get("fixture_sha256"):
            raise ValueError("Functional fixture hash changed")
    return value


def prepare_execution(casebook, *, limit=5):
    validate_casebook(casebook)
    if type(limit) is not int or not 1 <= limit <= 120:
        raise ValueError("Choose 1–120 functional fixtures")
    chosen = casebook["fixtures"][:limit]
    return {"status": "prepared_not_run", "casebook_sha256": casebook["casebook_sha256"],
            "available_fixtures": len(casebook["fixtures"]), "selected_fixtures": len(chosen),
            "selected_fixture_ids": [item["id"] for item in chosen], "network_requests": 0,
            "model_requests": 0, "independent_reference_labels": False, "interpretation_accuracy": None,
            "required_before_execution": ["explicit execute flag", "human rubric-review record for each fixture", "provider configuration and finite spend cap", "remote-transfer permission for remote endpoints"],
            "notice": "Authored expectations are not executed model successes. Dry preparation makes no provider calls."}


def _reviewed(review, fixtures, casebook_hash):
    if (not isinstance(review, dict) or review.get("casebook_sha256") != casebook_hash
            or review.get("origin") != "human_rubric_review"
            or not isinstance(review.get("reviewer_id"), str) or not review["reviewer_id"].strip()
            or len(review["reviewer_id"]) > 200):
        raise ValueError("An explicitly identified human rubric review must bind to the exact casebook")
    rows = review.get("fixtures")
    if not isinstance(rows, list) or len(rows) > 240:
        raise ValueError("Review must list bounded fixture approvals")
    approvals = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"fixture_id", "fixture_sha256", "approved", "note"}
                or row["fixture_id"] in approvals or row["approved"] is not True
                or not isinstance(row["note"], str) or not row["note"].strip() or len(row["note"]) > 4000):
            raise ValueError("Each unique fixture approval requires a human note and exact fixture hash")
        approvals[row["fixture_id"]] = row
    if any(approvals.get(fixture["id"], {}).get("fixture_sha256") != fixture["fixture_sha256"] for fixture in fixtures):
        raise ValueError("Every executed fixture requires its exact human-reviewed approval")
    return content_sha256(review)


def _derived_document(fixture):
    from .contextual import build_units
    identity = "functional-" + fixture["id"]
    before, after, blocks = [], [], []
    for side, output in (("context_before", before), ("context_after", after)):
        for index, original in enumerate(fixture.get(side, [])):
            text = original["text"]
            block_id = f"{side}-{index}"
            blocks.append({"id": block_id, "text": text})
            output.append({"id": content_sha256([identity, block_id])[:32], "document_id": identity,
                           "block_id": block_id, "start": 0, "end": len(text), "text": text})
    scored = {"id": "fixture-text", "text": fixture["text"]}
    blocks.append(scored)
    document = {"id": identity, "title": "Explicitly derived functional fixture", "author": "Unknown",
                "document_type": "synthetic_functional_fixture", "sha256": content_sha256(blocks),
                "blocks": blocks, "metadata": {"data_origin": "derived_functional_fixture", "parent_source_hash": fixture["parent_source_hash"]}}
    units = build_units({**document, "blocks": [scored]}, context_sentences=0)
    for unit in units:
        unit["context_before"] = copy.deepcopy(before)
        unit["context_after"] = copy.deepcopy(after)
    return document, units


async def execute_casebook(casebook, review, config, *, execute=False, limit=5, budget_usd=0,
                           allow_remote_text=False, provider_factory=None, engine=None):
    plan = prepare_execution(casebook, limit=limit)
    if not execute:
        return plan
    if isinstance(budget_usd, bool) or not isinstance(budget_usd, (int, float)) or not math.isfinite(budget_usd) or not 0 <= budget_usd <= 100:
        raise ValueError("Declare a finite functional-study spend cap between $0 and $100")
    fixtures = casebook["fixtures"][:limit]
    review_hash = _reviewed(review, fixtures, casebook["casebook_sha256"])
    from .lenses import get_lens
    for fixture in fixtures:
        if content_sha256(get_lens(fixture["lens_id"])) != content_sha256(fixture["rubric"]["pack"]):
            raise ValueError("The current lens differs from the fixture's frozen rubric; create a separate versioned casebook")
    from .providers import configured_endpoint, get_provider
    endpoint = configured_endpoint(config)
    parsed = urlsplit(endpoint) if endpoint else None
    from .pilot_api import inference_destination
    remote = inference_destination(config) != "local"
    if remote and (not allow_remote_text or parsed.scheme != "https"):
        raise ValueError("Remote fixture execution requires explicit text-transfer permission and HTTPS")
    if str(config.get("provider", "")) in {"lexical", "lexical-baseline", ""}:
        raise ValueError("Functional contextual execution requires an explicitly configured semantic provider")
    if provider_factory is None:
        provider_factory = get_provider
    if engine is None:
        from .contextual import analyze_units
        engine = analyze_units
    provider = await provider_factory(config)
    estimate = provider.estimate_cost(1, 1)
    if estimate is None or not math.isfinite(estimate) or estimate < 0:
        raise ValueError("Configure provider token rates before budgeted functional execution")
    reservations, outputs = [], []
    reserved = 0.0

    async def reserve(estimate):
        nonlocal reserved
        if isinstance(estimate, bool) or not isinstance(estimate, (int, float)) or not math.isfinite(estimate) or estimate < 0 or reserved + estimate > budget_usd:
            return False
        reserved += estimate
        reservations.append(estimate)
        return True

    for fixture in fixtures:
        document, units = _derived_document(fixture)
        result = await engine(document, [fixture["lens_id"]], provider, units=units,
                              config={**config, "budget_usd": budget_usd, "max_unit_retries": 0,
                                      "request_journal_mode": "metadata", "_reserve_budget": reserve,
                                      "question": "Apply the frozen rubric to this explicitly derived functional fixture; preserve attribution and uncertainty."})
        outputs.append({"fixture_id": fixture["id"], "fixture_sha256": fixture["fixture_sha256"],
                        "derived_document_hash": document["sha256"], "target_criterion_id": fixture["criterion_id"],
                        "result": result, "human_behavioral_adjudication": "pending", "passed": None})
        if any(error.get("code") == "budget_exhausted" for error in result.get("errors", [])):
            break
    complete = len(outputs) == len(fixtures) and all(item["result"].get("coverage", {}).get("complete") for item in outputs)
    return {"version": "doxa-functional-execution-0.6", "status": "completed" if complete else "partial",
            "casebook_sha256": casebook["casebook_sha256"], "rubric_review_sha256": review_hash,
            "rubric_reviewer_id": review["reviewer_id"], "reviewer_qualification_verified": False,
            "requested_fixtures": len(fixtures), "executed_fixtures": len(outputs), "results": outputs,
            "budget": {"cap_usd": budget_usd, "conservative_reserved_usd": reserved,
                       "reservation_count": len(reservations), "vendor_invoice": False},
            "independent_reference_labels": False, "interpretation_accuracy": None,
            "notice": "Actual validated outputs, not a functional pass rate. A separate human behavioral adjudication is still required; rubric approval does not establish reviewer qualifications."}
