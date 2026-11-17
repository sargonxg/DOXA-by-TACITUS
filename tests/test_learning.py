import copy
import hashlib
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


def compact_negative(store):
    """A real contextual negative payload compacted like API persistence, no AI."""
    from doxa.contextual import UNIT_BUILDER_VERSION, _criteria, _make_assessment, build_units
    identity = "compact-source"
    document(store, identity)
    text = "🧭 The library opens each morning. " + "The committee publishes its report. " * 11
    blocks = [{"id": "b", "text": text, "page": None, "paragraph": 1}]
    digest = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    metadata = {"source_id": "fixture:compact-source", "content_sha256": digest, "language": "en", "split": "development"}
    store.put_json(identity + "/blocks.json", blocks)
    store.execute("UPDATE documents SET sha256=?,metadata_json=? WHERE id=?", [digest, json.dumps(metadata), identity])
    version = hashlib.sha256(f"{metadata['source_id']}:{digest}".encode()).hexdigest()[:32]
    doc = {"id": identity, "author": "Fixture author", "sha256": digest, "source_version_id": version, "blocks": blocks}
    unit = build_units(doc)[0]
    _, criteria = _criteria(["ihra"], {})
    payload = _make_assessment(doc, unit, next(iter(criteria.values())), "does_not_meet", None)
    for key in ("context", "explanation", "alternative"):
        payload.pop(key, None)
    payload["evidence_refs"] = [{key: ref[key] for key in ("sentence_id", "block_id", "start", "end")} for ref in payload["evidence_refs"]]
    run(store, "compact-run", sources=[{"id": identity, "sha256": digest}])
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='compact-run'")["config_json"])
    config.update({"unit_max_chars": 1200, "context_sentences": 2, "unit_builder_version": UNIT_BUILDER_VERSION})
    store.execute("UPDATE runs SET config_json=? WHERE id='compact-run'", [json.dumps(config)])
    payload.update({"id": "compact-negative", "project_id": "p", "run_id": "compact-run"})
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [payload["id"], "p", "compact-run", identity, unit["id"], payload["lens_id"], payload["criterion_id"], payload["criterion_kind"],
                   payload["result"], payload["attribution"], payload["speech_role"], payload["speaker_stance"], payload["document_stance"], json.dumps(payload), utcnow()])
    return payload, text


def test_actual_contextual_compact_negative_can_be_human_reviewed_and_approved(store):
    raw, text = compact_negative(store)
    assert len(raw["evidence_refs"]) > 8 and all("quote" not in ref for ref in raw["evidence_refs"])
    event = learning.record_review(store, "p", raw["id"], {}, actor_kind="human", actor_id="operator", adjudicated=True)
    resolved = event["resolved"]
    assert resolved["result"] == "does_not_meet"
    for ref in resolved["evidence_refs"]:
        assert ref["quote"] == text[ref["start"]:ref["end"]]
    assert json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [raw["id"]])["payload_json"]) == raw
    example = learning.approve_example(store, "p", event["id"], actor_kind="human", actor_id="operator")
    assert example["result"] == "does_not_meet" and example["quote"].startswith("🧭")
    assert all(ref["quote"] for ref in example["evidence_refs"])


@pytest.mark.parametrize("corruption", ["bounds", "bool_offset", "quote", "null_quote", "source_hash", "version", "document", "sentence", "ref_source_hash", "ref_source_identity", "frozen_run", "body"])
def test_compact_negative_hydration_rejects_corrupt_source_or_evidence(store, corruption):
    payload, text = compact_negative(store)
    broken = copy.deepcopy(payload)
    ref = broken["evidence_refs"][0]
    if corruption == "bounds":
        ref["end"] = len(text) + 1
    elif corruption == "bool_offset":
        ref["start"] = False
    elif corruption in {"quote", "null_quote"}:
        ref["quote"] = "Invented source quotation" if corruption == "quote" else None
    elif corruption == "source_hash":
        broken["source_hash"] = "wrong-source-hash"
    elif corruption == "version":
        broken["source_version_id"] = "wrong-source-version"
    elif corruption == "document":
        broken["document_id"] = "another-source"
    elif corruption == "sentence":
        ref["sentence_id"] = "s_not_a_source_sentence"
    elif corruption == "ref_source_hash":
        ref["source_hash"] = "wrong-source-hash"
    elif corruption == "ref_source_identity":
        ref["source_id"] = "another-source"
    elif corruption == "frozen_run":
        config = json.loads(store.one("SELECT config_json FROM runs WHERE id='compact-run'")["config_json"])
        config["source_snapshot"][0]["sha256"] = "changed-frozen-hash"
        store.execute("UPDATE runs SET config_json=? WHERE id='compact-run'", [json.dumps(config)])
    else:
        store.put_json("compact-source/blocks.json", [{"id": "b", "text": text.replace("library", "archive"), "page": None, "paragraph": 1}])
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(broken), payload["id"]])
    with pytest.raises(ValueError):
        learning.record_review(store, "p", payload["id"], {}, actor_kind="human", actor_id="operator", adjudicated=True)
    assert store.one("SELECT COUNT(*) n FROM v3_review_events")["n"] == 0


def test_quote_omission_cannot_create_a_new_unpersisted_review_anchor(store):
    payload, _ = compact_negative(store)
    changed = copy.deepcopy(payload["evidence_refs"])
    changed[0]["start"] += 1
    with pytest.raises(ValueError, match="frozen source sentence"):
        learning.record_review(store, "p", payload["id"], {"evidence_refs": changed}, actor_kind="human", actor_id="operator")


def test_approval_revalidates_reviewed_compact_source_version(store):
    payload, _ = compact_negative(store)
    event = learning.record_review(store, "p", payload["id"], {}, actor_kind="human", actor_id="operator", adjudicated=True)
    store.execute("UPDATE documents SET sha256='changed-source-version' WHERE id='compact-source'")
    with pytest.raises(ValueError, match="source identity/hash/version"):
        learning.approve_example(store, "p", event["id"], actor_kind="human", actor_id="operator")
    assert store.one("SELECT COUNT(*) n FROM v3_learning_examples")["n"] == 0


def test_api_persisted_negative_supports_confirmed_human_review_and_approved_memory(tmp_path, monkeypatch):
    """Exercise actual compaction/checkpoint/review routes with a scripted provider."""
    from fastapi.testclient import TestClient
    from doxa.api import create_app
    from test_v3_api import CompactProvider, finish, project, start_mock, upload
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path / "http-negative", start_workers=False, use_saved_showcase=False)) as client:
        project_id = project(client)
        text = "🧭 The library opens each morning. " + "The committee publishes its report. " * 11
        upload(client, project_id, text, "scripted-negative.txt")
        analysis, _ = start_mock(client, project_id, monkeypatch, CompactProvider(), lens_ids=["ihra"])
        assert finish(client, analysis["id"])["status"] == "completed"
        database = client.app.state.store
        row = database.one("SELECT * FROM v3_assessments WHERE run_id=? AND result='does_not_meet' ORDER BY id LIMIT 1", [analysis["id"]])
        assert row
        raw = json.loads(row["payload_json"])
        assert len(raw["evidence_refs"]) > 8 and all("quote" not in ref for ref in raw["evidence_refs"])
        reviewed = client.patch(f"/api/v3/findings/{row['id']}/review", json={"review_status": "confirmed", "corrections": {}})
        assert reviewed.status_code == 200, reviewed.text
        event = reviewed.json()["review_event"]
        assert event["resolved"]["result"] == "does_not_meet"
        assert all(ref["quote"] for ref in event["resolved"]["evidence_refs"])
        approved = client.post(f"/api/v3/projects/{project_id}/learning/examples", json={"review_id": event["id"]})
        assert approved.status_code == 200, approved.text
        assert approved.json()["result"] == "does_not_meet"
        assert database.one("SELECT payload_json FROM v3_assessments WHERE id=?", [row["id"]])["payload_json"] == row["payload_json"]


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
