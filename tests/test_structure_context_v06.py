"""Source-anchored context policy tests; not model-quality measurements."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from doxa.contextual import analyze_units, build_units
from doxa.structure_context import CONTEXT_POLICY
from test_contextual import Provider, negative, one_criterion


def document():
    return {"id": "source", "sha256": "source-version-hash", "author": "Named writer", "blocks": [
        {"id": "heading", "text": "# Examples of claims rejected by this report\n", "heading_level": 1},
        {"id": "padding", "text": "Context kept in the original section. " * 25},
        {"id": "claim", "text": "The document quotes a hostile claim and rejects it.\n" + "Further context is present. " * 8},
    ]}


def test_opt_in_policy_changes_identity_without_changing_legacy_output():
    doc = document()
    before = deepcopy(doc)
    legacy = build_units(doc, max_unit_chars=200, context_sentences=0)
    assert all("context_manifest" not in item and "structural_context" not in item for item in legacy)
    structured = build_units(doc, max_unit_chars=200, context_sentences=0, context_policy=CONTEXT_POLICY)
    assert doc == before
    assert {unit["id"] for unit in legacy}.isdisjoint(unit["id"] for unit in structured)
    assert build_units(doc, max_unit_chars=200, context_sentences=0) == legacy
    target = next(unit for unit in structured if unit["block_id"] == "claim")
    assert target["structural_context"][0]["text"] == doc["blocks"][0]["text"]
    assert target["structural_context"][0]["context_relationship"] == "section_heading"
    assert target["context_manifest"]["context_sufficiency"] == "review_required"


def test_heading_budget_never_silently_truncates_text():
    doc = {"id": "d", "blocks": [{"id": "h", "heading_level": 1, "text": "# " + "Lengthy heading " * 150},
                                   {"id": "p", "text": "A later contextual claim."}]}
    units = build_units(doc, max_unit_chars=4000, context_sentences=0, context_policy=CONTEXT_POLICY)
    # Use separate source units so that a long heading becomes external context.
    doc["blocks"].insert(1, {"id": "gap", "text": "Preserved intervening context. " * 150})
    units = build_units(doc, max_unit_chars=4000, context_sentences=0, context_policy=CONTEXT_POLICY)
    target = units[-1]
    assert target["structural_context"] == []
    assert any(item["reason"] == "character_budget" for item in target["context_manifest"]["omitted_context"])
    assert "Lengthy heading " * 10 in "".join(unit["text"] for unit in units)


def mail_document():
    shared = {"thread_key": "thread", "mail_source_field": "canonical_body", "eligible_for_author_counts": True}
    return {"id": "mail", "blocks": [
        {"id": "parent", "text": "An earlier proposal needs revision.", "message_key": "parent-key", "message_id": "<parent>", "mail_role": "authored", "mail_sender": "Prior sender", **shared},
        {"id": "reply", "text": ">> Earlier quoted claim.\nI disagree with the proposal.", "message_key": "reply-key", "message_id": "<reply>", "mail_role": "authored", "mail_sender": "Current sender", **shared},
    ], "metadata": {"messages": [
        {"message_key": "parent-key", "message_id": "<parent>", "thread_key": "thread", "references": []},
        {"message_key": "reply-key", "message_id": "<reply>", "thread_key": "thread", "references": ["<missing>", "<parent>"]},
    ]}}


def test_mail_context_is_bounded_explicit_ancestry_and_missing_context_is_visible():
    doc = mail_document()
    legacy = build_units(doc, context_sentences=0)
    assert not legacy[-1]["context_before"]
    units = build_units(doc, context_sentences=0, context_policy=CONTEXT_POLICY)
    reply = units[-1]
    assert [row["message_key"] for row in reply["structural_context"]] == ["parent-key"]
    assert reply["structural_context"][0]["mail_sender"] == "Prior sender"
    assert reply["sentences"][0]["quotation_marker_depth"] == 2
    assert reply["context_manifest"]["missing_parent_messages"] == ["<missing>"]


def test_analyzer_sends_source_context_and_records_distinct_method_identity():
    doc = document()
    provider = Provider(negative)
    result = asyncio.run(analyze_units(doc, [one_criterion()], provider,
                                      config={"unit_max_chars": 200, "context_sentences": 0, "context_policy": CONTEXT_POLICY}))
    assert not result["errors"]
    assert result["model_profile"]["prompt_version"].endswith(CONTEXT_POLICY)
    assert any(unit.get("structural_context") for call in provider.calls for unit in call["units"])
    assert all(row["context_manifest"]["context_sufficiency"] == "review_required" for row in result["assessments"])


def test_forged_context_and_mismatched_policy_are_rejected_before_provider_call():
    doc = mail_document()
    units = build_units(doc, context_sentences=0, context_policy=CONTEXT_POLICY)
    provider = Provider(negative)
    units[-1]["structural_context"][0]["text"] = "Invented prior agreement"
    result = asyncio.run(analyze_units(doc, [one_criterion()], provider, units=units,
                                      config={"context_sentences": 0, "context_policy": CONTEXT_POLICY}))
    assert result["errors"][0]["code"] == "invalid_input" and not provider.calls
    good = build_units(doc, context_policy=CONTEXT_POLICY)
    result = asyncio.run(analyze_units(doc, [one_criterion()], provider, units=good))
    assert result["errors"][0]["code"] == "invalid_input" and not provider.calls
    with pytest.raises(ValueError, match="Unknown context policy"):
        build_units(doc, context_policy="automatic")


def test_legacy_source_unit_bytes_match_the_released_implementation():
    # Frozen bytes were generated once by the released v0.5 builder. The
    # fixture also works in shallow CI clones and downloaded source archives.
    root = Path(__file__).resolve().parents[1]
    frozen = json.loads((root / "tests/fixtures/v05-context-units.json").read_text())
    assert frozen["source_commit"] == "c06fee3168f9971716d6beba8f5b5f8527685a25"
    for doc, case in zip((document(), mail_document()), frozen["cases"]):
        assert doc == case["document"]
        new = build_units(doc, **frozen["config"])
        assert json.dumps(case["units"], sort_keys=True) == json.dumps(new, sort_keys=True)


def test_additional_challenges_are_reproducible_unexecuted_protocols():
    from scripts.build_context_challenges_v06 import build_pack
    pack = build_pack()
    stored = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/v06-context-challenges.json").read_text())
    assert pack == stored
    assert len(pack["cases"]) == 12 and pack["model_requests"] == 0
    assert not pack["independent_human_labels"] and pack["interpretation_accuracy"] is None
    assert all(case["model_execution"] == "not_run" and case["reference_labels"] is None and case["expected_interpretation"] is None for case in pack["cases"])
    for case in pack["cases"]:
        assert hashlib.sha256(case["source_text"].encode()).hexdigest() == case["source_sha256"]


def test_http_run_freezes_and_persists_opted_context_with_scripted_inference(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from doxa.api import create_app
    from test_v3_api import finish, project, start_mock, upload
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_ACCESS_MODE", "DOXA_ACCESS_CONFIG_FILE", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path / "context-api", start_workers=False, use_saved_showcase=False)) as client:
        identity = project(client)
        text = "# Historical claims rejected by this report\n\n" + "Source context is preserved. " * 180 + "\n\nThe writer rejects the allegation."
        upload(client, identity, text, "context.md")
        run, provider = start_mock(client, identity, monkeypatch, context_policy=CONTEXT_POLICY, lens_ids=["ihra"])
        settled = finish(client, run["id"])
        assert settled["status"] == "completed", settled
        assert settled["config"]["context_policy"] == CONTEXT_POLICY
        assert any(unit.get("structural_context") for call in provider.calls for unit in call["units"])
        rows = client.app.state.store.query("SELECT payload_json FROM v3_assessments WHERE run_id=?", [run["id"]])
        assert rows
        assert all(json.loads(row["payload_json"])["context_manifest"]["policy"] == CONTEXT_POLICY for row in rows)
