"""Bounded publication, evidence integrity and review provenance for pilots."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import zipfile
from xml.etree import ElementTree

from doxa.pdf_tools import open_pdf
import pytest

from doxa.lenses import get_lens
from doxa.pilot_reports import build_pilot_report, freeze_snapshot
from doxa.service_profiles import observe_communication, validate_brief


def sample(profile="personal"):
    return {"brief": validate_brief({"profile_id": profile}),
            "documents": [{"id": "d1", "title": "Selected writing — José and Μαρία", "author": "José",
                           "document_type": "email", "sha256": "a" * 64, "source_version_id": "version-1",
                           "blocks": [{"id": "b1", "text": "Please review this, José. Perhaps we will respond before Friday.", "mail_role": "authored"},
                                      {"id": "b2", "text": "> Please confirm the quotation.", "mail_role": "quoted", "eligible_for_author_counts": False}]}],
            "privacy_disclosure": {"mode": "originals", "destination": "local report renderer", "identity_mapping_included": False}}


def note():
    return {"document_id": "d1", "block_id": "b1", "start": 0, "end": 6, "quote": "Please",
            "note": "This is a request marker. Check the whole thread before interpreting its directness.",
            "actor_id": "reviewer-1", "review_status": "operator_reviewed"}


def finding(result="meets_criterion"):
    lens = get_lens("discourse")
    return {"id": "finding-" + result, "document_id": "d1", "block_id": "b1", "start": 0, "end": 6, "quote": "Please",
            "lens_id": "discourse", "criterion_id": "stance", "criterion_kind": lens["criteria"][0]["kind"],
            "criterion_version": lens["version"], "result": result, "speaker": "Unknown", "attribution": "unknown",
            "proposition": "An explicit sample proposition", "explanation": "Synthetic saved interpretation for a software test.",
            "alternative": "This is not independent reference evidence.", "source_hash": "a" * 64, "source_version_id": "version-1",
            "evidence_refs": [{"document_id": "d1", "block_id": "b1", "start": 0, "end": 6, "quote": "Please"}]}


@pytest.mark.parametrize("profile", ["company", "public", "personal"])
@pytest.mark.parametrize("format", ["pdf", "html", "xlsx", "json"])
def test_every_package_builds_real_formats_with_integrity_and_pending_validation(profile, format):
    source = sample(profile)
    before = deepcopy(source)
    result = build_pilot_report(source, format)
    assert source == before
    assert result["manifest"]["sha256"] == hashlib.sha256(result["content"]).hexdigest()
    assert result["manifest"]["bytes"] == len(result["content"])
    assert result["manifest"]["interpretation_status"] == "reference_pending"
    assert not result["manifest"]["model_inference_performed_by_renderer"]
    assert result["manifest"]["counts"]["documents"] == 1
    assert result["manifest"]["source_records"][0]["source_sha256"] == "a" * 64
    if format == "pdf":
        with open_pdf(stream=result["content"], filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
            assert "DOXA" in text and "REFERENCE LABELS PENDING" in text
            assert "José" in text and "Μαρία" in text
            assert len(pdf) >= 2
    elif format == "html":
        html = result["content"].decode()
        assert "Content-Security-Policy" in html
        assert "Reference labels pending" in html
    elif format == "xlsx":
        with zipfile.ZipFile(BytesIO(result["content"])) as archive:
            assert archive.testzip() is None
            xml = archive.read("xl/workbook.xml").decode()
            assert "Source blocks" in xml and "Wording observations" in xml
    else:
        output = json.loads(result["content"])
        assert output["documents"][0]["blocks"][0]["text"] == source["documents"][0]["blocks"][0]["text"]
        assert output["report_brief"]["brief"]["profile_id"] == profile


def test_fake_review_status_cannot_turn_into_verified_expert_or_reference_labels():
    source = sample()
    source["expert_commentary"] = [note()]
    ordinary = freeze_snapshot(source)
    assert ordinary["expert_commentary"][0]["review_status"] == "provisional"
    assert ordinary["expert_commentary"][0]["actor_id"] == ""
    assert ordinary["counts"]["operator_signed_notes"] == 0
    trusted = freeze_snapshot(source, trusted_reviews=True)
    assert trusted["expert_commentary"][0]["review_status"] == "operator_reviewed"
    assert trusted["expert_commentary"][0]["actor_id"] == "reviewer-1"
    assert not trusted["expert_commentary"][0]["independent_validation"]
    assert trusted["expert_qualification"] == "not_verified_by_application"
    assert trusted["interpretation_status"] == "reference_pending"
    missing_actor = deepcopy(source)
    missing_actor["expert_commentary"][0]["actor_id"] = ""
    assert freeze_snapshot(missing_actor, trusted_reviews=True)["counts"]["operator_signed_notes"] == 0


@pytest.mark.parametrize("change", [{"quote": "Invented"}, {"start": True}, {"end": 1000000},
                                     {"block_id": "other-block"}, {"document_id": "other-customer"}])
def test_operator_commentary_must_reference_actual_selected_source(change):
    source = sample()
    source["expert_commentary"] = [{**note(), **change}]
    with pytest.raises(ValueError):
        build_pilot_report(source, "json", trusted_reviews=True)


def test_all_four_contextual_outcomes_remain_separate_from_wording_markers():
    source = sample()
    source["findings"] = [finding(result) for result in ["meets_criterion", "does_not_meet", "not_applicable", "uncertain"]]
    output = freeze_snapshot(source, trusted_reviews=True)
    assert {item["result"] for item in output["findings"]} == {"meets_criterion", "does_not_meet", "not_applicable", "uncertain"}
    assert all(item["review_status"] == "provisional_model_output" for item in output["findings"])
    assert output["counts"]["saved_contextual_decisions"] == 4
    assert output["observation_counts"]["request_marker"] == 1
    assert output["counts"]["operator_signed_notes"] == 0


@pytest.mark.parametrize("change", [{"source_hash": "b" * 64}, {"source_version_id": "other-version"},
                                     {"criterion_version": "historical"}, {"criterion_kind": "indicator"},
                                     {"criterion_id": "imaginary"}, {"result": "universal_bias"},
                                     {"quote": "Invented"}, {"evidence_refs": [None]}])
def test_saved_model_decisions_cannot_be_rebound_to_changed_sources_or_criteria(change):
    source = sample()
    source["findings"] = [{**finding(), **change}]
    with pytest.raises(ValueError):
        build_pilot_report(source, "json")


def test_supplied_observation_counts_are_recomputed_and_anchor_invention_refused():
    source = sample()
    observations = observe_communication([{**block, "document_id": "d1"} for block in source["documents"][0]["blocks"]])
    source["observations"] = {"d1": {**observations, "counts": {"bias": 999}}}
    output = freeze_snapshot(source)
    assert "bias" not in output["observation_counts"]
    assert output["observation_counts"]["request_marker"] == 1
    source["observations"]["d1"]["observations"][0]["quote"] = "Invented"
    with pytest.raises(ValueError):
        freeze_snapshot(source)


def test_large_source_blocks_are_losslessly_split_across_workbook_cells():
    source = sample()
    text = "=HYPERLINK(\"https://example.invalid/\",\"do not execute\")\n" + "context " * 6000
    source["documents"][0]["blocks"] = [{"id": "b1", "text": text}]
    source["documents"][0]["title"] = "=1+2"
    output = build_pilot_report(source, "xlsx")
    with zipfile.ZipFile(BytesIO(output["content"])) as archive:
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        strings_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(item.itertext()) for item in strings_root]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        fragments = []
        for row in sheet.findall("s:sheetData/s:row", namespace)[1:]:
            cell = next(item for item in row if item.attrib["r"].startswith("E"))
            fragments.append(strings[int(cell.find("s:v", namespace).text)])
        assert "".join(fragments) == text
        for name in archive.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                assert b"<f>" not in archive.read(name)
        assert not any("externalLink" in name for name in archive.namelist())


def test_html_escapes_source_and_commentary_without_loading_external_resources():
    source = sample()
    source["documents"][0]["title"] = '<script src="https://evil.invalid/"></script>'
    source["documents"][0]["blocks"][0]["text"] += ' <img src="file:///secret" onerror="alert(1)">'
    source["expert_commentary"] = [{"document_id": "d1", "note": "<script>alert(1)</script>"}]
    html = build_pilot_report(source, "html")["content"].decode()
    assert "<script" not in html and "<img" not in html
    assert "&lt;script" in html
    assert "default-src 'none'" in html


def test_bounded_appendix_discloses_selection_while_json_keeps_full_selected_data():
    source = sample()
    source["documents"][0]["blocks"] = [{"id": "b1", "text": "Please. " * 40}]
    html = build_pilot_report(source, "html")
    assert "Showing 30 of 40 wording observations" in html["content"].decode()
    assert html["manifest"]["appendix"]["wording_observations_total"] == 40
    full = json.loads(build_pilot_report(source, "json")["content"])
    assert len(full["observations"]) == 40
    assert full["documents"][0]["blocks"][0]["text"] == "Please. " * 40


def test_limits_and_duplicate_selection_fail_explicitly_instead_of_truncating():
    source = sample()
    for replacement in [[], source["documents"] * 2, source["documents"] * 101]:
        with pytest.raises(ValueError):
            freeze_snapshot({**source, "documents": replacement})
    source["documents"][0]["blocks"][0]["text"] = "x" * 500001
    with pytest.raises(ValueError, match="limit|characters"):
        freeze_snapshot(source)
    with pytest.raises(ValueError):
        build_pilot_report(sample(), "zip")


def test_snapshot_hash_is_reproducible_and_changes_with_user_goal_or_source():
    source = sample()
    first = build_pilot_report(source, "json")
    second = build_pilot_report(deepcopy(source), "json")
    assert first["content"] == second["content"]
    assert first["manifest"]["snapshot_sha256"] == second["manifest"]["snapshot_sha256"]
    source["documents"][0]["blocks"][0]["text"] += " Additional context."
    third = build_pilot_report(source, "json")
    assert third["manifest"]["snapshot_sha256"] != first["manifest"]["snapshot_sha256"]


def test_mail_source_chain_and_page_locators_survive_frozen_snapshot():
    source = sample()
    context = {"page": 2, "paragraph": 3, "message_key": "m1", "thread_key": "t1",
               "mail_sender": "Unverified source header", "source_char_start": 100,
               "source_chain": {"body_role": "authored", "verification": "not_independently_verified"}}
    source["documents"][0]["blocks"][0].update(context)
    snapshot = json.loads(build_pilot_report(source, "json")["content"])
    for key, value in context.items():
        assert snapshot["documents"][0]["blocks"][0][key] == value
    source["documents"][0]["blocks"][0]["source_chain"]["verification"] = "tampered"
    assert snapshot["documents"][0]["blocks"][0]["source_chain"]["verification"] == "not_independently_verified"


def test_synthetic_origin_cannot_be_misread_as_actual_model_output():
    source = sample("public")
    source["data_origin"] = "synthetic_demonstration"
    source["findings"] = [finding()]
    result = build_pilot_report(source, "json")
    frozen = json.loads(result["content"])
    assert frozen["findings"][0]["review_status"] == "provisional_fixture"
    assert "not model output" in frozen["findings"][0]["origin"]
    assert result["manifest"]["data_origin"] == "synthetic_demonstration"
    assert "Illustrative contextual decision fixtures" in build_pilot_report(source, "html")["content"].decode()


def run_provenance(source):
    return {"run_id": "actual-run-1", "run_status": "partial", "provider": "recorded-provider", "model": "recorded-model",
            "model_revision": None, "method_fingerprint": "b" * 64,
            "question": source["brief"]["question"], "perspective": source["brief"]["perspective"] + "\nReport audience: " + source["brief"]["audience"],
            "service_brief_fingerprint": source["brief"]["fingerprint"],
            "selected_document_ids": ["d1"], "selection": "selected_sources_and_declared_lenses",
            "coverage": {"original_run": {"documents_total": 2, "documents_complete": 1, "blocks_total": 4, "blocks_done": 2, "tasks_failed": 1},
                         "selected_sources": [{"document_id": "d1", "units_total": 3, "units_accepted": 2, "decisions_retained": 5}]}}


def test_actual_run_provenance_roundtrips_without_reconstructing_unknown_revision():
    source = sample()
    provenance = run_provenance(source)
    source["run_provenance"] = provenance
    snapshot = json.loads(build_pilot_report(source, "json")["content"])
    assert snapshot["run_provenance"] == provenance
    result = build_pilot_report(source, "xlsx")
    assert result["manifest"]["run_provenance"] == provenance
    with zipfile.ZipFile(BytesIO(result["content"])) as archive:
        assert b"Run provenance" in archive.read("xl/workbook.xml")
        assert b"recorded-provider" in archive.read("xl/sharedStrings.xml")
        assert b"recorded-model" in archive.read("xl/sharedStrings.xml")
    for format in ("html", "pdf"):
        rendered = build_pilot_report(source, format)
        if format == "html":
            text = rendered["content"].decode()
        else:
            with open_pdf(stream=rendered["content"], filetype="pdf") as pdf:
                text = "\n".join(page.get_text() for page in pdf)
        assert "actual-run-1" in text and "recorded-model" in text
        assert "Model revision: not recorded" in text
        assert "Status: partial" in text
        assert "Original run coverage" in text and "Selected-source coverage" in text
        assert "Missing counters and expected decision counts are not inferred" in text


def test_absent_run_provenance_remains_absent_without_inventing_a_model():
    source = sample()
    result = build_pilot_report(source, "json")
    assert json.loads(result["content"])["run_provenance"] is None
    assert result["manifest"]["run_provenance"] is None
    assert "No contextual run provenance was supplied" in build_pilot_report(source, "html")["content"].decode()


@pytest.mark.parametrize("change", [{"authorization": "must never reach a report"}, {"run_id": ""},
                                     {"selected_document_ids": ["other-project-document"]}, {"selected_document_ids": ["d1", "d1"]},
                                     {"selection": "entire project"}, {"service_brief_fingerprint": "other-brief"},
                                     {"provider": {"secret": "not a provider name"}}])
def test_run_provenance_rejects_secret_fields_wrong_scope_and_brief_mismatch(change):
    source = sample()
    source["run_provenance"] = {**run_provenance(source), **change}
    with pytest.raises(ValueError):
        build_pilot_report(source, "json")


@pytest.mark.parametrize("coverage", [[], {"original_run": {"blocks_total": -1}},
    {"original_run": {"tasks_failed": True}}, {"original_run": {"documents_total": 1, "documents_complete": 2}},
    {"original_run": {"private_note": "not a numeric counter"}},
    {"selected_sources": [{"document_id": "other-source", "units_total": 0}]},
    {"selected_sources": []}, {"selected_sources": [{"document_id": "d1", "units_total": 1, "units_accepted": 2}]}])
def test_run_coverage_refuses_noncounts_wrong_source_scope_and_impossible_completion(coverage):
    source = sample()
    source["run_provenance"] = {**run_provenance(source), "coverage": coverage}
    with pytest.raises(ValueError):
        build_pilot_report(source, "json")


def test_unknown_expected_decision_counts_are_not_manufactured_from_retained_decisions():
    source = sample()
    coverage = {"selected_sources": [{"document_id": "d1", "decisions_retained": 0}]}
    source["run_provenance"] = {**run_provenance(source), "coverage": coverage}
    frozen = json.loads(build_pilot_report(source, "json")["content"])
    assert frozen["run_provenance"]["coverage"] == coverage
    assert "units_total" not in frozen["run_provenance"]["coverage"]["selected_sources"][0]
