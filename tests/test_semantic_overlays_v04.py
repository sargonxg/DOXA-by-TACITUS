import json

import pytest

from doxa import semantic_overlays
from doxa.store import Store, utcnow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    value = Store(tmp_path)
    value.init()
    value.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Overlay',?)", [utcnow()])
    payload = {"id": "a", "result": "meets_criterion", "evidence_refs": [{"sentence_id": "s1"}], "explanation": "Original model interpretation"}
    value.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,payload_json,created_at) VALUES('a','p','r','d','u','ihra','c','indicator','meets_criterion','unknown','unknown','unclear','unclear',?,?)", [json.dumps(payload), utcnow()])
    yield value
    value.close()


def test_optional_overlay_defaults_and_append_only_actor_version_origin(store):
    original = store.one("SELECT payload_json FROM v3_assessments WHERE id='a'")["payload_json"]
    assert semantic_overlays.assessment_overlays(store, "p", "a")["default"]["status"] == "NOT_ASSESSED"
    first = semantic_overlays.record_overlay(store, "p", "a", {"evidence_function": "definition"}, actor_id="operator", actor_kind="human")
    second = semantic_overlays.record_overlay(store, "p", "a", {"evidence_function": "reported_incident", "missing_context": ["Original speaker is unnamed."]}, actor_id="assistant", actor_kind="agent")
    records = semantic_overlays.assessment_overlays(store, "p", "a")["annotations"]
    assert len(records) == 2 and first["id"] != second["id"]
    assert records[0]["lineage"]["kind"] == "editorial_annotation"
    assert store.one("SELECT payload_json FROM v3_assessments WHERE id='a'")["payload_json"] == original


def test_overlay_rejects_source_mutation_and_unsupported_chain_anchors(store):
    for fields in ({"result": "does_not_meet"}, {"evidence_function": "invented"},
                   {"attribution_chain": [{"speaker": "Someone", "role": "quoted_speaker", "sentence_ids": ["not-frozen"]}]}):
        with pytest.raises(ValueError):
            semantic_overlays.record_overlay(store, "p", "a", fields, actor_id="operator", actor_kind="human")
    with pytest.raises(ValueError, match="project"):
        semantic_overlays.record_overlay(store, "other", "a", {"evidence_function": "definition"}, actor_id="operator", actor_kind="human")
