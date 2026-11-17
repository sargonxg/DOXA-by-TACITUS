import copy
import hashlib
import json

import pytest

from doxa import evaluation
from doxa.contextual import build_units
from doxa.lenses import get_lens
from doxa.store import Store, utcnow


def candidate(index, split="development"):
    return {"source": {"id": f"d{index}", "sha256": f"hash{index}", "metadata": {"split": split, "story_group_id": f"story{index}"}},
            "assessment": {"id": f"a{index}", "unit_id": f"u{index}", "lens_id": "ihra", "criterion_id": "criterion",
                           "criterion_kind": "indicator", "result": "does_not_meet", "attribution": "unknown",
                           "speaker_stance": "unclear", "document_stance": "unclear"}}


def test_scaffold_is_deterministic_without_labels_and_story_splits_never_overlap():
    cases = [candidate(i, "development" if i < 8 else "heldout") for i in range(16)]
    first = evaluation.build_scaffold(cases, development_count=4, heldout_count=4)
    second = evaluation.build_scaffold(reversed(cases), development_count=4, heldout_count=4)
    assert first == second
    assert first["interpretation_accuracy"] is None and not first["independent_reference_labels"]
    assert all(case["reference_labels"] is None for case in first["cases"])
    groups = {split: {c["story_group"] for c in first["cases"] if c["split"] == split} for split in ("development", "heldout")}
    assert groups["development"].isdisjoint(groups["heldout"])
    cases[8]["source"]["metadata"]["story_group_id"] = "story0"
    excluded = evaluation.build_scaffold(cases, development_count=4, heldout_count=4)
    assert "story0" in excluded["sampling"]["excluded_cross_split_story_groups"]
    assert all(c["story_group"] != "story0" for c in excluded["cases"])
    with pytest.raises(ValueError, match="Insufficient"):
        evaluation.build_scaffold(cases[:2], development_count=4, heldout_count=4)


@pytest.fixture
def reviewed_store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path)
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Reference',?)", [utcnow()])
    config = {"provider": "test", "unit_max_chars": 1200, "context_sentences": 2}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES('r','p','partial',?,?)", [json.dumps(config), utcnow()])
    criterion = get_lens("ihra")["criteria"][0]["id"]
    for i in range(8):
        identity = f"d{i}"
        blocks = [{"id": "b", "text": f"The public report {i} describes equal access to local services.", "paragraph": 1}]
        digest = hashlib.sha256(json.dumps(blocks).encode()).hexdigest()
        key = f"{identity}/blocks.json"
        store.put_json(key, blocks)
        source = {"id": identity, "sha256": digest, "blocks": blocks}
        unit = build_units(source)[0]
        metadata = {"split": "development" if i < 4 else "heldout", "story_group_id": f"story{i}"}
        store.execute("INSERT INTO documents(id,project_id,batch_id,title,filename,mime_type,sha256,status,blocks_key,metadata_json,created_at) VALUES(?,'p','b',?,'source.txt','text/plain',?,'ready',?,?,?)", [identity, identity, digest, key, json.dumps(metadata), utcnow()])
        payload = {"id": f"a{i}", "document_id": identity, "source_version_id": identity, "unit_id": unit["id"], "lens_id": "ihra", "criterion_id": criterion,
                   "criterion_kind": "indicator", "result": "does_not_meet", "attribution": "unknown", "speaker_stance": "unclear", "document_stance": "unclear",
                   "explanation": "SECRET_MODEL_EXPLANATION", "evidence_refs": [{"sentence_id": unit["sentences"][0]["id"], "block_id": "b", "start": 0, "end": len(blocks[0]["text"])}]}
        store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,payload_json,created_at) VALUES(?,'p','r',?,?,'ihra',?,'indicator','does_not_meet','unknown','unknown','unclear','unclear',?,?)", [f"a{i}", identity, unit["id"], criterion, json.dumps(payload), utcnow()])
    yield store
    store.close()


def labels():
    return {"result": "does_not_meet", "attribution": "unknown", "speaker_stance": "unclear", "document_stance": "unclear",
            "evidence_adequacy": "adequate", "criterion_application": "supported", "context_sufficient": True,
            "acceptable_alternatives": [], "note": "Test-only synthetic human reference; not a benchmark result."}


def test_blinded_forms_two_reviews_adjudication_idempotence_and_immutable_originals(reviewed_store):
    store = reviewed_store
    original = store.query("SELECT id,payload_json FROM v3_assessments ORDER BY id")
    scaffold = evaluation.create_scaffold(store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    retry = evaluation.create_scaffold(store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    assert scaffold["id"] == retry["id"]
    form = evaluation.export_review_form(store, "p", scaffold["id"])
    assert "SECRET_MODEL_EXPLANATION" not in json.dumps(form)
    assert form["cases"][0]["scored_unit"]["sentences"]
    case = form["cases"][0]
    row = {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"], "labels": labels(), "independent_blinded_review": True}
    with pytest.raises(ValueError, match="human"):
        evaluation.import_reviews(store, "p", scaffold["id"], [row], reviewer_id="agent", actor_kind="agent")
    evaluation.import_reviews(store, "p", scaffold["id"], [row], reviewer_id="reviewer-one")
    assert evaluation.import_reviews(store, "p", scaffold["id"], [row], reviewer_id="reviewer-one")["idempotent_duplicates"] == 1
    with pytest.raises(ValueError, match="Two distinct"):
        evaluation.adjudicate_case(store, "p", scaffold["id"], case["id"], labels(), actor_id="adjudicator")
    evaluation.import_reviews(store, "p", scaffold["id"], [row], reviewer_id="reviewer-two")
    evaluation.adjudicate_case(store, "p", scaffold["id"], case["id"], labels(), actor_id="adjudicator")
    report = evaluation.reference_report(store, "p", scaffold["id"])
    assert report["adjudicated_cases"] == 1 and report["heldout_metrics_locked"]
    assert report["interpretation_accuracy"] is None
    assert store.query("SELECT id,payload_json FROM v3_assessments ORDER BY id") == original
    corrupt = copy.deepcopy(row)
    corrupt["source_hash"] = "tampered"
    with pytest.raises(ValueError, match="hash"):
        evaluation.import_reviews(store, "p", scaffold["id"], [corrupt], reviewer_id="reviewer-three")
    with pytest.raises(ValueError, match="Locked"):
        evaluation.export_review_form(store, "p", scaffold["id"], split="heldout", include_model=True)


def test_interrupted_case_import_repair_and_tampered_blocks_rejected(reviewed_store):
    store = reviewed_store
    scaffold = evaluation.create_scaffold(store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    first = store.one("SELECT id,document_id FROM v4_evaluation_cases WHERE evaluation_id=? AND split='development' LIMIT 1", [scaffold["id"]])
    store.execute("DELETE FROM v4_evaluation_cases WHERE id=?", [first["id"]])
    evaluation.create_scaffold(store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    assert store.one("SELECT COUNT(*) AS n FROM v4_evaluation_cases WHERE evaluation_id=?", [scaffold["id"]])["n"] == 4
    source = store.one("SELECT blocks_key FROM documents WHERE id=?", [first["document_id"]])
    store.put_json(source["blocks_key"], [{"id": "b", "text": "Changed source"}])
    with pytest.raises(ValueError, match="blocks changed"):
        evaluation.export_review_form(store, "p", scaffold["id"])


def test_review_rubric_is_frozen_not_silently_reloaded_and_embedded_hash_checked(reviewed_store, monkeypatch):
    store = reviewed_store
    scaffold = evaluation.create_scaffold(store, "p", "r", development_count=2, heldout_count=2, actor_id="operator")
    before = evaluation.export_review_form(store, "p", scaffold["id"])
    assert before["cases"][0]["rubric"]["origin"] == "versioned_registry_captured_at_evaluation_creation"
    import doxa.lenses
    monkeypatch.setattr(doxa.lenses, "get_lens", lambda identity: (_ for _ in ()).throw(AssertionError("Live rubric must not be consulted")))
    after = evaluation.export_review_form(store, "p", scaffold["id"])
    assert before == after
    with pytest.raises(ValueError, match="rubric snapshot hash"):
        evaluation.freeze_rubric({"lens_snapshots": [{"id": "ihra", "version": "changed"}], "lens_snapshots_sha256": "tampered"}, "ihra")
