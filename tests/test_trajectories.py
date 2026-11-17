import json
import math

import pytest

from doxa import learning
from doxa.store import Store, utcnow
from doxa.trajectories import detect_spikes, fisher_greater, holm_adjust, monthly_trajectories, wilson_interval


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    value = Store(tmp_path)
    value.init()
    value.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Research',?)", [utcnow()])
    value.execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES('r','p','completed',?,?)",
                  [json.dumps({"model": "fixed-model", "source_snapshot": [], "learning_snapshot_id": None}), "2026-11-16T00:00:00Z"])
    yield value
    value.close()


def add(store, identity, month="2024-01", *, positive=False, kind="indicator", role="author_assertion",
        stance="supports", hypothetical=False, status="accepted", institution="Publisher",
        author="Attributed source author", group=None, no_assessment=False, criterion="criterion"):
    text = "Exact source sentence with an explicitly supplied context."
    key = identity + "/blocks.json"
    store.put_json(key, [{"id": "b", "text": text, "paragraph": 1}])
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,author,institution,document_type,published_at,filename,mime_type,sha256,status,word_count,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, "p", "b", identity, author, institution, "article", month + "-01" if month else None,
                   identity + ".txt", "text/plain", identity, "ready", 100, key, utcnow(),
                   json.dumps({"story_group_id": group or identity, "language": "en"})])
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='r'")["config_json"])
    config["source_snapshot"].append({"id": identity, "sha256": identity})
    store.execute("UPDATE runs SET config_json=? WHERE id='r'", [json.dumps(config)])
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES('r',?,?,?,?,?,?)",
                  [identity, "u", status, len(text), "{}", "2026-11-16T00:00:00Z"])
    if not no_assessment:
        attribution = {"author_assertion": "author", "attributed_quotation": "quoted_speaker",
                       "reported_speech": "reported_speaker", "unknown": "unknown"}[role]
        payload = {"id": "a-" + identity + "-" + criterion, "document_id": identity, "unit_id": "u",
                   "lens_id": "ihra", "criterion_id": criterion, "criterion_key": "ihra/" + criterion,
                   "criterion_kind": kind, "result": "meets_criterion" if positive else "does_not_meet",
                   "attribution": attribution, "speech_role": role, "speaker_stance": "supports",
                   "document_stance": stance, "hypothetical": hypothetical, "speaker": "A quoted speaker" if role != "author_assertion" else author,
                   "block_id": "b", "quote": text, "start": 0, "end": len(text), "context": text}
        store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,hypothetical,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [payload["id"], "p", "r", identity, "u", "ihra", criterion, kind, payload["result"],
                       attribution, role, "supports", stance, int(hypothetical), json.dumps(payload), "2026-11-16T00:00:00Z"])
    return "a-" + identity + "-" + criterion


def test_exact_statistics_and_rare_count_intervals():
    assert math.isclose(fisher_greater(6, 8, 1, 5), 0.08624708624708627, rel_tol=1e-10)
    assert holm_adjust([0.001, 0.02, 0.03]) == pytest.approx([0.003, 0.04, 0.04])
    lower, upper = wilson_interval(0, 10)
    assert lower == 0 and 0 < upper < 1
    assert wilson_interval(0, 0) == (None, None)


def test_quotation_counter_speech_boundary_and_hypothetical_are_not_author_endorsement(store):
    add(store, "quoted", positive=True, role="attributed_quotation", stance="opposes")
    add(store, "boundary", positive=True, kind="boundary")
    add(store, "hypothetical", positive=True, hypothetical=True)
    add(store, "endorsed", positive=True)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    row = data["monthly"][0]
    assert row["documents"] == 4 and row["eligible_documents"] == 4
    assert row["findings"] == 4 and row["indicators"] == 3
    assert row["reported"] == 1 and row["counterspeech"] == 1 and row["hypothetical"] == 1
    assert row["endorsed"] == 1 and row["positive_documents"] == 1 and row["rate"] == 0.25
    assert all(item["author"] == "Attributed source author" for item in data["matrix"])


def test_missing_explicit_negative_partial_units_and_unknown_dates_remain_visible(store):
    add(store, "partial", positive=True, status="partial")
    add(store, "missing", no_assessment=True)
    add(store, "unknown", month=None, positive=True)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    row = data["monthly"][0]
    assert row["documents"] == 2 and row["eligible_documents"] == 0
    assert row["rate"] is None and row["coverage"] == 0
    assert data["unknown_period_documents"] == 1
    assert sum(item["documents"] for item in data["matrix"]) == 3


def test_duplicate_cluster_is_one_document_and_zero_volume_month_is_gap(store):
    add(store, "original", month="2024-01", positive=True, group="one-story")
    add(store, "copy", month="2024-02", positive=True, group="one-story")
    add(store, "later", month="2024-03")
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    assert data["duplicate_documents"] == 1
    assert [row["documents"] for row in data["monthly"]] == [1, 0, 1]
    assert data["monthly"][1]["rate"] is None


def test_explicit_uncertainty_is_not_silently_a_negative_outcome(store):
    identity = add(store, "uncertain")
    row = store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [identity])
    payload = json.loads(row["payload_json"])
    payload["result"] = "uncertain"
    store.execute("UPDATE v3_assessments SET result='uncertain',payload_json=? WHERE id=?", [json.dumps(payload), identity])
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    month = data["monthly"][0]
    assert month["coverage"] == 100 and month["analyzed_documents"] == 1
    assert month["eligible_documents"] == 0 and month["uncertain_documents"] == 1
    assert month["rate"] is None
    assert month["unknown_result_lower_bound"] == 0 and month["unknown_result_upper_bound"] == 1


def test_descriptive_and_unscoped_lenses_do_not_produce_indicator_rates(store):
    add(store, "description", positive=True, kind="descriptive")
    assert monthly_trajectories(store, "p", "r", lens_id="ihra")["monthly"][0]["rate"] is None
    assert monthly_trajectories(store, "p", "r")["monthly"][0]["rate"] is None


def test_model_history_and_human_review_are_separate_and_review_denominator_is_explicit(store):
    identity = add(store, "reviewed", positive=True)
    add(store, "unreviewed")
    learning.record_review(store, "p", identity, {"result": "does_not_meet"}, actor_kind="human",
                           actor_id="operator", adjudicated=True)
    provisional = monthly_trajectories(store, "p", "r", lens_id="ihra")
    reviewed = monthly_trajectories(store, "p", "r", lens_id="ihra", review_scope="reviewed")
    assert provisional["monthly"][0]["positive_documents"] == 1
    assert provisional["monthly"][0]["eligible_documents"] == 2
    assert reviewed["monthly"][0]["positive_documents"] == 0
    assert reviewed["monthly"][0]["eligible_documents"] == 1
    history = monthly_trajectories(store, "p", "r", lens_id="ihra", mode="assessment")
    assert history["monthly"][0]["date"] == "2026-11"
    assert provisional["monthly"][0]["date"] == "2024-01"


def test_reviewing_one_criterion_does_not_review_all_criteria_in_the_document(store):
    identity = add(store, "reviewed", positive=True)
    row = store.one("SELECT * FROM v3_assessments WHERE id=?", [identity])
    payload = json.loads(row["payload_json"])
    payload.update({"id": "second", "criterion_id": "second", "criterion_key": "ihra/second"})
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,payload_json,created_at) VALUES('second','p','r','reviewed','u','ihra','second','indicator','meets_criterion',?,?)",
                  [json.dumps(payload), row["created_at"]])
    learning.record_review(store, "p", identity, {}, actor_kind="human", actor_id="operator", adjudicated=True)
    all_criteria = monthly_trajectories(store, "p", "r", lens_id="ihra", review_scope="reviewed")
    one_criterion = monthly_trajectories(store, "p", "r", lens_id="ihra", criterion_id="criterion", review_scope="reviewed")
    assert all_criteria["monthly"][0]["eligible_documents"] == 0
    assert one_criterion["monthly"][0]["eligible_documents"] == 1


def populate_history(store, *, current_n=40, current_k=10, changed_source=False):
    for month in range(1, 14):
        n = current_n if month == 13 else 40
        k = current_k if month == 13 else 1
        date = f"{2024 + (month - 1) // 12:04d}-{(month - 1) % 12 + 1:02d}"
        for i in range(n):
            add(store, f"{month:02d}-{i:03d}", month=date, positive=i < k,
                institution="New publisher" if changed_source and month == 13 else "Publisher")


def test_rare_rate_increase_is_provisional_review_candidate_with_denominators(store):
    populate_history(store)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    assert len(data["spike_candidates"]) == 1
    event = data["spike_candidates"][0]
    assert event["date"] == "2025-01"
    assert event["k"] == 10 and event["n"] == 40 and event["baseline_n"] == 480
    assert event["adjusted_p"] < 0.01 and event["status"] == "review_candidate"
    assert data["interpretation_accuracy"] is None


def test_doubled_volume_at_same_rate_is_not_a_spike(store):
    populate_history(store, current_n=80, current_k=2)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    assert data["spike_candidates"] == []


def test_changed_source_composition_suppresses_pooled_candidate(store):
    populate_history(store, changed_source=True)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    assert data["spike_candidates"] == []
    assert data["statistical_tests"][0]["status"] == "suppressed_composition_change"


def test_document_and_speech_role_scope_applies_before_aggregation_and_graphs(store):
    add(store, "author", positive=True)
    add(store, "quoted", positive=True, role="attributed_quotation", stance="opposes")
    add(store, "excluded", positive=True)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra", document_ids=["quoted"], speech_roles=["attributed_quotation"])
    assert data["monthly"][0]["documents"] == 1
    assert data["monthly"][0]["findings"] == 1 and data["monthly"][0]["endorsed"] == 0
    assert data["document_scope_count"] == 1
    assert {node["reference"]["document_id"] for node in data["graph"]["nodes"] if node["type"] == "document"} == {"quoted"}
    assert monthly_trajectories(store, "p", "r", document_ids=[])["monthly"] == []


def test_unreviewed_negative_context_payload_is_not_materialized(store, monkeypatch):
    identity = add(store, "negative")
    raw = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [identity])["payload_json"])
    raw["context"] = "DO_NOT_MATERIALIZE_NEGATIVE_CONTEXT" * 1000
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(raw), identity])
    original = json.loads
    def guarded(value, *args, **kwargs):
        assert "DO_NOT_MATERIALIZE_NEGATIVE_CONTEXT" not in value
        return original(value, *args, **kwargs)
    monkeypatch.setattr("doxa.trajectories.json.loads", guarded)
    data = monthly_trajectories(store, "p", "r", lens_id="ihra")
    assert data["monthly"][0]["eligible_documents"] == 1
    assert data["monthly"][0]["rate"] == 0


def test_sql_aggregate_spike_helper_requires_known_counts_and_checks_unknown_fraction():
    items = [{"date": f"2024-{month:02d}", "complete_doc_n": 40, "endorsed_indicators": 1,
              "coverage": 100, "composition": {"Publisher|article|short": 40}} for month in range(1, 13)]
    items.append({"date": "2025-01", "complete_doc_n": 40, "endorsed_indicators": 10,
                  "coverage": 100, "composition": {"Publisher|article|short": 40}})
    assert len(detect_spikes(items)["candidates"]) == 1
    items[-1]["unknown_documents"] = 6
    uncertain = detect_spikes(items)
    assert not uncertain["candidates"]
    assert "too_many_unknown_outcomes" in uncertain["tests"][0]["quality_gates"]
    no_denominator = detect_spikes([{"date": "2025-01", "findings": 10, "documents": 40}])
    assert no_denominator["candidates"] == []
    assert no_denominator["suppressed"][0]["reason"] == "explicit_known_document_denominator_required"


def test_graph_is_bounded_typed_and_only_references_actual_rows(store):
    for i in range(10):
        add(store, str(i), positive=True, author="Author " + str(i))
    data = monthly_trajectories(store, "p", "r", lens_id="ihra", max_graph_nodes=5, max_graph_edges=6)
    graph = data["graph"]
    assert graph["truncated"] and len(graph["nodes"]) <= 5 and len(graph["edges"]) <= 6
    assert all(node["reference"] and node["type"] in {"document", "attributed_author", "publication_month", "criterion"} for node in graph["nodes"])
    node_ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])
    with pytest.raises(ValueError):
        monthly_trajectories(store, "p", "r", max_graph_nodes=501)
    with pytest.raises(ValueError):
        monthly_trajectories(store, "not-p", "r")

def _workspace_query(store, **changes):
    from doxa.workspace import Workspace, WorkspaceQuery
    row = store.one("SELECT config_json FROM runs WHERE id='r'")
    config = json.loads(row["config_json"])
    config.update({"methodology_version": "3.0", "schema_version": "3.0", "provider": "mock", "lens_ids": ["ihra"]})
    store.execute("UPDATE runs SET config_json=? WHERE id='r'", [json.dumps(config)])
    query = WorkspaceQuery(run_id="r", lens_ids=["ihra"], metric="endorsed_indicators", **changes)
    return Workspace(store).aggregate("p", query.model_dump())


def test_workspace_sql_timeline_matches_deep_known_source_denominator(store, monkeypatch):
    add(store, "author", positive=True)
    add(store, "quoted", positive=True, role="attributed_quotation", stance="opposes")
    add(store, "negative")
    uncertain_id = add(store, "uncertain")
    raw = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [uncertain_id])["payload_json"])
    raw["result"] = "uncertain"
    store.execute("UPDATE v3_assessments SET result='uncertain',payload_json=? WHERE id=?", [json.dumps(raw), uncertain_id])
    add(store, "partial", positive=True, status="partial")
    monkeypatch.setattr(store, "get_json", lambda *_: pytest.fail("Timeline aggregates must not read source bodies"))
    shallow = _workspace_query(store)["timeline"][0]
    deep = monthly_trajectories(store, "p", "r", lens_id="ihra")["monthly"][0]
    for key in ("documents", "eligible_documents", "positive_documents", "uncertain_documents", "coverage", "rate", "lower", "upper"):
        assert shallow[key] == pytest.approx(deep[key])
    assert shallow["complete_doc_n"] == 4 and shallow["eligible_documents"] == 3
    assert shallow["endorsed_indicators"] == 2  # A retained partial observation is visible separately.
    assert shallow["positive_documents"] == 1  # Rate numerator includes complete sources only.


def test_workspace_unassessed_lens_never_reports_full_coverage(store):
    from doxa.workspace import Workspace, WorkspaceQuery
    add(store, "ready", positive=True)
    _workspace_query(store)
    result = Workspace(store).aggregate("p", WorkspaceQuery(run_id="r", lens_ids=["nexus"]).model_dump())
    assert result["metrics"]["not_assessed_lenses"] == ["nexus"]
    assert result["metrics"]["coverage"] == 0
    assert result["timeline"][0]["coverage"] == 0
    assert result["timeline"][0]["rate"] is None
    assert result["timeline"][0]["spike_candidate"] is False


def test_workspace_review_filter_requires_every_selected_decision_reviewed(store):
    identity = add(store, "partly-reviewed", positive=True)
    row = store.one("SELECT * FROM v3_assessments WHERE id=?", [identity])
    payload = json.loads(row["payload_json"])
    payload.update({"id": "second", "criterion_id": "second", "criterion_key": "ihra/second"})
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,payload_json,created_at) VALUES('second','p','r','partly-reviewed','u','ihra','second','indicator','does_not_meet',?,?)",
                  [json.dumps(payload), row["created_at"]])
    store.execute("UPDATE v3_assessments SET review_status='confirmed' WHERE id=?", [identity])
    all_criteria = _workspace_query(store, review_status="confirmed")["timeline"][0]
    one_criterion = _workspace_query(store, review_status="confirmed", criterion_ids=["criterion"])["timeline"][0]
    assert all_criteria["complete_doc_n"] == 0 and all_criteria["rate"] is None
    assert one_criterion["complete_doc_n"] == 1 and one_criterion["rate"] == 1


def test_workspace_sql_spike_candidate_uses_complete_monthly_document_counts(store):
    populate_history(store)
    data = _workspace_query(store)
    assert data["timeline"][-1]["spike_candidate"] is True
    assert data["timeline"][-1]["positive_documents"] == 10
    assert data["timeline"][-1]["eligible_documents"] == 40
    assert data["timeline"][-1]["spike_policy"]["baseline_months"] == 12
    scoped = _workspace_query(store, document_ids=["13-000", "13-001"])
    assert sum(row["documents"] for row in scoped["timeline"]) == 2
    assert all(not row["spike_candidate"] for row in scoped["timeline"])
