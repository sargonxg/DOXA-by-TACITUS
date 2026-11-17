import json
import math

import pytest

from doxa.store import Store, utcnow
from doxa.verification import compare_runs, select_verification_units


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path)
    result.init()
    result.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Verification',?)", [utcnow()])
    yield result
    result.close()


def run(store, identity, *, verification=None, selection=None, project="p"):
    config = {"methodology_version": "3.0", "provider": "cloudflare",
              "model": "primary-model" if verification is None else "independent-model",
              "unit_max_chars": 4000, "language": "en", "persona": "researcher",
              "lens_ids": ["ihra"], "inquiry": "Apply the supplied criteria.",
              "source_snapshot": [{"id": "d", "sha256": "source-v1"}]}
    if verification:
        config.update(verification_of=verification, verification_selection=selection)
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,model_profile_json) VALUES(?,?,'completed',?,?,?)",
                  [identity, project, json.dumps(config), utcnow(), json.dumps({"model": config["model"], "revision": "pinned"})])


def unit(store, run_id, identity, document="d", status="accepted"):
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                  [run_id, document, identity, status, json.dumps({"unit_id": identity}), utcnow()])


def assessment(store, run_id, identity, *, document="d", result="does_not_meet", kind="indicator", criterion="c", suffix="", **changes):
    payload = {"unit_id": identity, "result": result, "attribution": "author", "speaker_stance": "supports",
               "document_stance": "supports", "speech_role": "author_statement", "hypothetical": False,
               "explanation": "Test-only comparison fixture", "proposition": "An explicit test proposition.",
               "evidence_refs": [{"sentence_id": "s-" + identity, "block_id": "block", "start": 0, "end": 10}]}
    payload.update(changes)
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speaker_stance,document_stance,speech_role,hypothetical,payload_json,created_at) VALUES(?,'p',?,?,?,'ihra',?,?,?,?,?,?,?,?,?,?)",
                  [run_id + ":" + document + ":" + identity + ":" + criterion + suffix, run_id, document,
                   identity, criterion, kind, result, payload["attribution"], payload["speaker_stance"],
                   payload["document_stance"], payload["speech_role"], int(payload["hypothetical"]), json.dumps(payload), utcnow()])


def test_selection_includes_indicators_and_uses_global_document_stratified_five_percent(store):
    run(store, "primary")
    for i in range(20):
        document = f"doc-{i:02}"
        for j in range(3):
            identity = f"u-{j}"
            status = "partial" if i == 1 and j == 0 else "accepted"
            unit(store, "primary", identity, document, status)
            # General discourse/boundary positives must not make every unit mandatory.
            assessment(store, "primary", identity, document=document, kind="descriptive", result="meets_criterion", criterion="frame")
    assessment(store, "primary", "u-0", document="doc-00", result="meets_criterion")
    assessment(store, "primary", "u-0", document="doc-01", result="uncertain")
    selected = select_verification_units(store, "p", "primary")
    counts = selected["selection_counts"]
    assert counts["indicator_flagged_units"] == 2 and counts["sampled_other_units"] == 3
    assert counts["selected_units"] == 5 and counts["other_accepted_units"] == 58
    assert "u-0" in selected["unit_ids"]["doc-00"] and "u-0" in selected["unit_ids"]["doc-01"]
    assert selected == select_verification_units(store, "p", "primary")
    assert selected["selection_id"] != select_verification_units(store, "p", "primary", seed=7)["selection_id"]
    zero = select_verification_units(store, "p", "primary", negative_fraction=0)
    assert zero["selection_counts"]["selected_units"] == 2


def test_selection_does_not_treat_pending_or_boundary_units_as_indicators(store):
    run(store, "primary")
    unit(store, "primary", "accepted")
    unit(store, "primary", "pending", status="pending")
    assessment(store, "primary", "accepted", kind="boundary", result="meets_criterion")
    selected = select_verification_units(store, "p", "primary", negative_fraction=1)
    assert selected["unit_ids"] == {"d": ["accepted"]}
    assert selected["selection_counts"]["indicator_flagged_units"] == 0
    assert not selected["primary_run_complete"]
    for bad in [float("nan"), float("inf"), -0.1, 1.1, True, "0.05", 10 ** 1000]:
        with pytest.raises(ValueError, match="finite"):
            select_verification_units(store, "p", "primary", negative_fraction=bad)
    with pytest.raises(ValueError, match="project"):
        select_verification_units(store, "another-project", "primary")


def test_review_correction_cannot_hide_an_original_model_indicator_from_selection(store):
    run(store, "primary")
    unit(store, "primary", "reviewed")
    assessment(store, "primary", "reviewed", result="meets_criterion")
    store.execute("UPDATE v3_assessments SET result='does_not_meet',review_status='rejected' WHERE run_id='primary'")
    selected = select_verification_units(store, "p", "primary", negative_fraction=0)
    assert selected["unit_ids"] == {"d": ["reviewed"]}
    assert selected["selection_counts"]["indicator_flagged_units"] == 1


def paired(store):
    run(store, "primary")
    for identity in ("a", "b", "c"):
        unit(store, "primary", identity)
        assessment(store, "primary", identity, result="meets_criterion")
    selection = select_verification_units(store, "p", "primary")
    run(store, "verifier", verification="primary", selection=selection)
    for identity in ("a", "b", "c"):
        unit(store, "verifier", identity, status="pending" if identity == "c" else "accepted")
    assessment(store, "verifier", "a", result="meets_criterion")
    assessment(store, "verifier", "b", result="uncertain", attribution="reported_speaker",
               speaker_stance="opposes", document_stance="neutral", speech_role="reported_speech", hypothetical=True)
    return selection


def test_comparison_preserves_raw_model_outputs_after_human_review_and_reports_partial_pairs(store):
    paired(store)
    store.execute("UPDATE v3_assessments SET result='does_not_meet',attribution='unknown',review_status='revised',review_json=? WHERE run_id='primary' AND unit_id='a'", [json.dumps({"result": "does_not_meet"})])
    before = store.one("SELECT COUNT(*) n FROM v3_assessments")["n"]
    result = compare_runs(store, "p", "primary", "verifier")
    assert result["counts"]["paired_assessments"] == 2
    assert result["counts"]["result_mismatches"] == 1
    assert result["counts"]["attribution_mismatches"] == 1
    assert result["counts"]["speaker_stance_mismatches"] == 1
    assert result["counts"]["document_stance_mismatches"] == 1
    assert result["counts"]["primary_assessments_in_verifier_ledger"] == 3
    assert result["counts"]["unpaired_primary_assessments_in_verifier_ledger"] == 1
    assert result["verifier_units_accepted"] == 2 and result["verifier_units_missing"] == 0
    assert result["comparison_available"] and not result["comparison_complete_on_selected_units"]
    assert result["paired_assessment_fraction_in_verifier_ledger"] == 2 / 3
    assert result["disagreements"][0]["unit_id"] == "b"
    assert result["interpretation_accuracy"] is None and not result["automatic_adjudication"]
    assert result["comparable_settings"]
    assert store.one("SELECT COUNT(*) n FROM v3_assessments")["n"] == before
    assert store.one("SELECT review_status FROM v3_assessments WHERE run_id='primary' AND unit_id='a'")["review_status"] == "revised"


def test_verifier_scope_and_duplicate_pairs_fail_closed(store):
    paired(store)
    unit(store, "verifier", "outside")
    with pytest.raises(ValueError, match="outside its frozen"):
        compare_runs(store, "p", "primary", "verifier")
    store.execute("DELETE FROM v3_unit_statuses WHERE run_id='verifier' AND unit_id='outside'")
    assessment(store, "verifier", "a", result="meets_criterion", suffix="duplicate")
    with pytest.raises(ValueError, match="Duplicate assessment"):
        compare_runs(store, "p", "primary", "verifier")
    with pytest.raises(ValueError, match="distinct"):
        compare_runs(store, "p", "primary", "primary")


def test_disagreement_rows_are_bounded_while_counts_cover_all_pairs(store):
    run(store, "primary")
    for i in range(205):
        identity = f"u-{i:03}"
        unit(store, "primary", identity)
        assessment(store, "primary", identity, result="meets_criterion")
    selection = select_verification_units(store, "p", "primary")
    run(store, "verifier", verification="primary", selection=selection)
    for i in range(205):
        identity = f"u-{i:03}"
        unit(store, "verifier", identity)
        assessment(store, "verifier", identity, result="does_not_meet")
    result = compare_runs(store, "p", "primary", "verifier")
    assert result["counts"]["paired_assessments"] == 205
    assert result["counts"]["any_mismatches"] == 205
    assert len(result["disagreements"]) == 200 and result["disagreements_truncated"]
    assert math.isfinite(result["selection_counts"]["other_sample_fraction_actual"])
