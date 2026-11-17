import json

import pytest

from doxa import learning
from doxa.store import Store, utcnow

TEXT = 'A speaker said, "Jews control the media." The author rejects this claim.'
QUOTE = "Jews control the media."


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    value = Store(tmp_path)
    value.init()
    for project in ("p", "other"):
        value.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [project, project, utcnow()])
    yield value
    value.close()


def document(store, identity, *, group=None, split="development", language="en"):
    key = identity + "/blocks.json"
    store.put_json(key, [{"id": "b", "text": TEXT, "page": None, "paragraph": 1}])
    metadata = {"story_group_id": group or identity, "split": split, "language": language}
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,author,document_type,published_at,filename,mime_type,sha256,status,word_count,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, "p", "b", identity, "Source author", "article", "2024-01-01", identity + ".txt",
                   "text/plain", identity + "-hash", "ready", 12, key, utcnow(), json.dumps(metadata)])


def run(store, identity, sources=None, snapshot=None):
    config = {"provider": "fake-test-only", "model": "test-model", "lens_ids": ["ihra"],
              "source_snapshot": sources or [{"id": "eval", "sha256": "eval-hash"}],
              "learning_snapshot_id": snapshot}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,model_profile_json) VALUES(?,?,'completed',?,?,?)",
                  [identity, "p", json.dumps(config), utcnow(), json.dumps({"revision": "fixed-test-revision"})])


def assessment(store, identity, document_id, run_id="r", *, explanation="The quoted claim is rejected."):
    if not store.one("SELECT id FROM runs WHERE id=?", [run_id]):
        run(store, run_id)
    start = TEXT.index(QUOTE)
    payload = {"id": identity, "document_id": document_id, "unit_id": "u", "lens_id": "ihra",
               "criterion_id": "anti_jewish_generalization", "criterion_key": "ihra/anti_jewish_generalization",
               "criterion_kind": "indicator", "result": "meets_criterion", "quote": QUOTE,
               "block_id": "b", "start": start, "end": start + len(QUOTE), "context": TEXT,
               "attribution": "quoted_speaker", "speech_role": "attributed_quotation",
               "speaker_stance": "supports", "document_stance": "opposes", "hypothetical": False,
               "explanation": explanation, "alternative": "Read the full source context."}
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, "p", run_id, document_id, "u", "ihra", payload["criterion_id"], "indicator",
                   "meets_criterion", "quoted_speaker", "attributed_quotation", "supports", "opposes",
                   json.dumps(payload), utcnow()])
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,unit_id) DO NOTHING",
                  [run_id, document_id, "u", "accepted", len(TEXT), "{}", utcnow()])
    return payload


def approved(store, identity, *, group=None, split="development", language="en", explanation="The quoted claim is rejected."):
    document(store, identity, group=group, split=split, language=language)
    assessment(store, "a-" + identity, identity, explanation=explanation)
    review = learning.record_review(store, "p", "a-" + identity, {}, actor_kind="human",
                                    actor_id="test-operator", adjudicated=True)
    return learning.approve_example(store, "p", review["id"], actor_kind="human", actor_id="test-operator")


def test_human_corrections_preserve_raw_model_payload_and_append_history(store):
    document(store, "train")
    original = assessment(store, "a", "train")
    first = learning.record_review(store, "p", "a", {"result": "does_not_meet"}, actor_kind="human",
                                   actor_id="operator", note="Context rejects the claim.", adjudicated=True)
    second = learning.record_review(store, "p", "a", {"document_stance": "mixed"}, actor_kind="human",
                                    actor_id="operator", adjudicated=True)
    row = store.one("SELECT * FROM v3_assessments WHERE id='a'")
    assert json.loads(row["payload_json"]) == original
    assert row["result"] == "does_not_meet"
    assert json.loads(row["review_json"])["resolved"]["document_stance"] == "mixed"
    assert second["previous_review_id"] == first["id"]
    assert store.one("SELECT COUNT(*) AS n FROM v3_review_events")["n"] == 2
    assert json.loads(store.one("SELECT resolved_json FROM v3_review_events WHERE id=?", [first["id"]])["resolved_json"])["document_stance"] == "opposes"


def test_agents_cannot_apply_human_reviews_or_approve_memory(store):
    document(store, "train")
    assessment(store, "a", "train")
    proposal = learning.record_review(store, "p", "a", {"result": "does_not_meet"}, actor_kind="agent", actor_id="model-reviewer")
    assert proposal["state_applied"] is False
    assert store.one("SELECT result FROM v3_assessments WHERE id='a'")["result"] == "meets_criterion"
    with pytest.raises(ValueError, match="Agents"):
        learning.record_review(store, "p", "a", {}, actor_kind="agent", actor_id="model-reviewer", adjudicated=True)
    with pytest.raises(ValueError):
        learning.approve_example(store, "p", proposal["id"], actor_kind="human", actor_id="operator")
    with pytest.raises(ValueError):
        learning.create_snapshot(store, "p", actor_kind="agent", actor_id="model")


def test_structured_review_rejects_invented_evidence_and_cross_project_scope(store):
    document(store, "train")
    assessment(store, "a", "train")
    with pytest.raises(ValueError, match="exact persisted"):
        learning.record_review(store, "p", "a", {"quote": "Invented words"}, actor_kind="human", actor_id="operator")
    with pytest.raises(ValueError, match="scope"):
        learning.record_review(store, "p", "a", {"document_id": "other-source"}, actor_kind="human", actor_id="operator")
    with pytest.raises(ValueError, match="project"):
        learning.record_review(store, "other", "a", {}, actor_kind="human", actor_id="operator")


def test_holdout_and_same_story_as_holdout_cannot_enter_memory(store):
    document(store, "held", group="same-story", split="holdout")
    document(store, "train", group="same-story")
    assessment(store, "a", "train")
    review = learning.record_review(store, "p", "a", {}, actor_kind="human", actor_id="operator", adjudicated=True)
    with pytest.raises(ValueError, match="Holdout"):
        learning.approve_example(store, "p", review["id"], actor_kind="human", actor_id="operator")


def test_snapshots_are_immutable_retrieval_is_relevant_bounded_and_group_diverse(store):
    first = approved(store, "training-a", group="a", explanation="Banking media conspiracy is explicitly rejected.")
    old = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    approved(store, "training-b", group="b", explanation="A discussion of unrelated workplace policies.")
    approved(store, "training-c", group="b", explanation="Same story is a duplicate training example.")
    approved(store, "french", language="fr")
    new = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    assert old["id"] != new["id"]
    assert [item["example_id"] for item in learning.select_examples(store, "p", old["id"], "ihra/anti_jewish_generalization")] == [first["id"]]
    selected = learning.select_examples(store, "p", new["id"], "ihra/anti_jewish_generalization",
                                        source_text="banking media conspiracy", limit=4)
    assert selected[0]["example_id"] == first["id"]
    assert len({item["story_group_id"] for item in selected}) == len(selected)
    assert all(item["language"] == "en" for item in selected)
    assert len(json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode()) <= 4800
    assert not learning.select_examples(store, "p", old["id"], "ihra/anti_jewish_generalization",
                                        exclude_story_groups=["a"])
    with pytest.raises(ValueError):
        learning.select_examples(store, "other", old["id"], "ihra/anti_jewish_generalization")
    with pytest.raises(ValueError):
        learning.select_examples(store, "p", old["id"], "ihra/anti_jewish_generalization", limit=5)


def test_new_snapshot_omits_obsolete_human_approval(store):
    approved(store, "training")
    old = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    learning.record_review(store, "p", "a-training", {"result": "does_not_meet"}, actor_kind="human",
                           actor_id="operator", adjudicated=True)
    new = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    assert new["example_count"] == 0
    assert len(learning.select_examples(store, "p", old["id"], "ihra/anti_jewish_generalization")) == 1


def test_snapshot_membership_cannot_silently_change_after_freezing(store):
    approved(store, "first")
    frozen = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    extra = approved(store, "later")
    store.execute("INSERT INTO v3_learning_snapshot_items(snapshot_id,example_id,ordinal) VALUES(?,?,?)",
                  [frozen["id"], extra["id"], 1])
    with pytest.raises(ValueError, match="membership"):
        learning.select_examples(store, "p", frozen["id"], "ihra/anti_jewish_generalization")


def test_candidate_requires_paired_report_explicit_human_activation_and_supports_rollback(store):
    approved(store, "training")
    memory = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    candidate = learning.create_candidate(store, "p", memory["id"], actor_kind="human", actor_id="operator")
    document(store, "eval", split="holdout")
    run(store, "before", snapshot=None)
    run(store, "after", snapshot=memory["id"])
    assessment(store, "before-a", "eval", "before")
    assessment(store, "after-a", "eval", "after")
    report = learning.candidate_report(store, "p", candidate["id"], "before", "after")
    assert report["eligible_for_activation"] is True and report["interpretation_accuracy"] is None
    with pytest.raises(ValueError):
        learning.activate_candidate(store, "p", candidate["id"], report["id"], actor_kind="agent", actor_id="model")
    learning.activate_candidate(store, "p", candidate["id"], report["id"], actor_kind="human", actor_id="operator")
    assert learning.learning_overview(store, "p")["active"]["snapshot_id"] == memory["id"]
    assert json.loads(store.one("SELECT config_json FROM runs WHERE id='before'")["config_json"])["learning_snapshot_id"] is None
    learning.rollback_candidate(store, "p", actor_kind="human", actor_id="operator")
    assert learning.learning_overview(store, "p")["active"]["snapshot_id"] is None
    learning.rollback_candidate(store, "p", candidate["id"], actor_kind="human", actor_id="operator")
    assert learning.learning_overview(store, "p")["active"]["snapshot_id"] == memory["id"]


def test_candidate_rejects_learning_story_in_evaluation_and_incomplete_coverage(store):
    approved(store, "training", group="shared")
    memory = learning.create_snapshot(store, "p", actor_kind="human", actor_id="operator")
    candidate = learning.create_candidate(store, "p", memory["id"], actor_kind="human", actor_id="operator")
    document(store, "eval", group="shared")
    run(store, "before")
    run(store, "after", snapshot=memory["id"])
    assessment(store, "before-a", "eval", "before")
    assessment(store, "after-a", "eval", "after")
    store.execute("UPDATE v3_unit_statuses SET status='partial' WHERE run_id='after'")
    report = learning.candidate_report(store, "p", candidate["id"], "before", "after")
    assert report["eligible_for_activation"] is False
    assert any("story" in reason for reason in report["blocking_reasons"])
    assert any("coverage" in reason for reason in report["blocking_reasons"])
    with pytest.raises(ValueError):
        learning.activate_candidate(store, "p", candidate["id"], report["id"], actor_kind="human", actor_id="operator")
