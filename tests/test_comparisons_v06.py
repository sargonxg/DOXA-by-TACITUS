import json

import pytest

from doxa.comparisons import build_comparison
from doxa.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    value.init()
    value.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Comparison','now')")
    value.execute("INSERT INTO projects(id,name,created_at) VALUES('other','Other','now')")
    yield value
    value.close()


def source(store, identity, *, project="p", day="2026-02-01", event="budget vote", family=None, digest=None):
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,published_at,filename,mime_type,sha256,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, project, "batch", identity, day, identity + ".txt", "text/plain", digest or identity,
                   "ready", "now", json.dumps({"event": event, "topic": "public spending", "genre": "news", "source_family": family or identity})])


def test_nearest_date_pairs_and_missing_eligibility_are_explicit(store):
    source(store, "a", day="2026-02-01")
    source(store, "b", day="2026-02-03")
    source(store, "c", day="2026-02-02")
    source(store, "missing", event="")
    result = build_comparison(store, "p", {"left_document_ids": ["a", "b", "missing"], "right_document_ids": ["c"]})
    assert result["pairs"] == [{"left_document_id": "a", "right_document_id": "c", "date_gap_days": 1,
                               "matched_on": {"event": "budget vote", "topic": "public spending", "genre": "news"}}]
    assert {r["reason"] for r in result["excluded"]} == {"missing_matching_metadata", "no_eligible_one_to_one_match"}
    assert result["inference_statistics_enabled"] is False
    assert result["summary"]["excluded_sources"] == 2


def test_matching_recovers_a_pair_that_nearest_dates_alone_miss(store):
    for identity, day in (("a", 10), ("b", 8), ("c", 10), ("d", 12)):
        source(store, identity, day=f"2026-02-{day:02d}")
    result = build_comparison(store, "p", {"left_document_ids": ["a", "b"],
                                           "right_document_ids": ["c", "d"], "max_date_gap_days": 2})
    assert [(pair["left_document_id"], pair["right_document_id"]) for pair in result["pairs"]] == [("a", "d"), ("b", "c")]
    assert not result["excluded"]


def test_duplicate_content_does_not_add_cohort_votes(store):
    for identity, digest in (("a", "one"), ("a-copy", "one"), ("b", "two"), ("c", "three"), ("c-copy", "three")):
        source(store, identity, digest=digest)
    result = build_comparison(store, "p", {"left_document_ids": ["a-copy", "c", "a"],
                                           "right_document_ids": ["b", "c-copy"]})
    assert [(pair["left_document_id"], pair["right_document_id"]) for pair in result["pairs"]] == [("a", "b")]
    assert {item["document_id"]: item["reason"] for item in result["excluded"]} == {
        "a-copy": "duplicate_content", "c": "content_in_both_cohorts", "c-copy": "content_in_both_cohorts"}


@pytest.mark.parametrize("change", [{"event": "other event"}, {"day": None}, {"family": "a"}, {"digest": "a"}])
def test_unmatched_dates_events_same_family_and_duplicates_are_not_comparisons(store, change):
    source(store, "a")
    source(store, "b", **change)
    result = build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["b"]})
    assert result["status"] == "comparison_not_established"
    assert not result["pairs"]


def test_source_metadata_is_frozen_without_changing_original(store):
    source(store, "a")
    source(store, "b")
    before = store.one("SELECT metadata_json FROM documents WHERE id='a'")["metadata_json"]
    result = build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["b"],
                                          "per_document": {"a": {"story_group": "group1"}}})
    store.execute("UPDATE documents SET title='Changed' WHERE id='a'")
    saved = json.loads(store.one("SELECT payload_json FROM matched_comparisons WHERE id=?", [result["id"]])["payload_json"])
    assert saved["sources"][0]["title"] == "a"
    assert len(saved["snapshot_sha256"]) == 64
    assert store.one("SELECT metadata_json FROM documents WHERE id='a'")["metadata_json"] == before


def test_cross_project_source_and_invalid_run_fail_without_source_leakage(store):
    source(store, "a")
    source(store, "private", project="other")
    with pytest.raises(ValueError, match="unavailable project sources"):
        build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["private"]})
    source(store, "b")
    with pytest.raises(ValueError, match="run is unavailable"):
        build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["b"], "run_id": "other"})


def test_recorded_outcomes_keep_missing_sources_and_negative_decisions(store):
    source(store, "a")
    source(store, "b")
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES('r','p','partial','{}','now')")
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,payload_json,created_at) VALUES('x','p','r','a','u','political-framing','frame','descriptive','does_not_meet','{}','now')")
    result = build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["b"], "run_id": "r"})
    assert result["analysis"]["sources_without_retained_decisions"] == {"left": 0, "right": 1}
    assert result["analysis"]["criteria"][0]["left"]["does_not_meet"] == 1
    assert result["analysis"]["criteria"][0]["right"]["meets_criterion"] == 0


@pytest.mark.parametrize("body", [{"max_date_gap_days": True}, {"max_date_gap_days": -1}, {"matching_fields": []}, {"per_document": {"a": {"event": 1}}}])
def test_matching_contract_is_bounded_and_explicit(store, body):
    source(store, "a")
    source(store, "b")
    with pytest.raises(ValueError):
        build_comparison(store, "p", {"left_document_ids": ["a"], "right_document_ids": ["b"], **body})
