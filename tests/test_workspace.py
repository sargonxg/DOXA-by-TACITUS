"""Bounded workspace queries keep one source/assessment scope across views.

Fixtures below are synthetic persistence records, not model-quality evidence.
"""
import json

import pytest
from fastapi import HTTPException

from doxa.store import Store, utcnow
from doxa.workspace import Workspace, WorkspaceQuery


def seed_document(store, identity, project="p", *, author="Author A", published="2024-01-15",
                  document_type="news", batch="batch-a", sha=None, text=None, metadata=None):
    key = f"test-sources/{identity}/blocks.json"
    if text is not None:
        store.put_json(key, [{"id": "b1", "text": text, "page": None, "paragraph": 1}])
    store.execute(
        "INSERT INTO documents(id,project_id,batch_id,title,author,document_type,published_at,filename,mime_type,sha256,status,word_count,page_count,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [identity, project, batch, "Source " + identity, author, document_type, published, identity + ".txt",
         "text/plain", sha or identity + "-hash", "ready", 30, 1, key, "2026-11-16T12:00:00+00:00", json.dumps(metadata or {})])
    store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?)", [batch, identity])
    return identity


def seed_run(store, identity="run", project="p", *, version="3.0", status="completed", sources=None, snapshot=None):
    config = {"methodology_version": version, "schema_version": version, "provider": "openai", "model": "mock-fixed",
              "lens_ids": ["ihra", "jda", "discourse"], "learning_snapshot_id": snapshot,
              "source_snapshot": sources or [{"id": "eval", "sha256": "eval-hash"}]}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,model_profile_json) VALUES(?,?,?,?,?,?)",
                  [identity, project, status, json.dumps(config), utcnow(), json.dumps({"revision": "fixture-1"})])
    return identity


def seed_assessment(store, identity, document, run="run", project="p", *, lens="ihra", criterion="anti_jewish_generalization",
                    kind="indicator", result="meets_criterion", attribution="quoted_speaker", stance="supports",
                    document_stance="opposes", review="unreviewed", hypothetical=False, text="Source statement.",
                    status="accepted", unit="unit-1"):
    role = {"quoted_speaker": "attributed_quotation", "reported_speaker": "reported_speech", "author": "author_assertion", "unknown": "unknown"}[attribution]
    payload = {"id": identity, "document_id": document, "run_id": run, "unit_id": unit, "lens_id": lens,
               "criterion_id": criterion, "criterion_key": lens + "/" + criterion, "criterion_kind": kind, "result": result,
               "block_id": "b1", "start": 0, "end": len(text), "quote": text, "context": text,
               "attribution": attribution, "speech_role": role, "speaker_stance": stance, "document_stance": document_stance,
               "hypothetical": hypothetical, "speaker": "A represented speaker", "target": "A stated claim",
               "proposition": "A stated proposition", "title": "Synthetic persistence fixture",
               "explanation": "An explicit test assessment.", "alternative": "Read surrounding source context."}
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,hypothetical,review_status,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  [identity, project, run, document, unit, lens, criterion, kind, result, attribution, role, stance,
                   document_stance, int(hypothetical), review, json.dumps(payload), utcnow()])
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,unit_id) DO NOTHING",
                  [run, document, unit, status, len(text), "{}", utcnow()])
    return payload


def seed_many_documents(store, count=5000, project="p"):
    statements = []
    for index in range(count):
        identity = f"source-{index:05d}"
        statements.append({"sql": "INSERT INTO documents(id,project_id,batch_id,title,author,filename,mime_type,sha256,status,word_count,blocks_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           "params": [identity, project, "batch-a", identity, "Author " + str(index % 17), identity + ".txt",
                                      "text/plain", identity + "-hash", "ready", 100, "never-read/" + identity, utcnow()]})
    for start in range(0, len(statements), 100):
        store.batch(statements[start:start + 100])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    value = Store(tmp_path)
    value.init()
    for identity in ("p", "other"):
        value.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [identity, identity, utcnow()])
    for identity, project in (("batch-a", "p"), ("batch-b", "p"), ("other-batch", "other")):
        value.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", [identity, project, identity, utcnow()])
    yield value
    value.close()


def test_all_5000_documents_are_cursor_paged_without_opening_source_bodies(store, monkeypatch):
    seed_many_documents(store)
    seed_document(store, "foreign", project="other", batch="other-batch")
    monkeypatch.setattr(store, "get_json", lambda *_: pytest.fail("A workspace page must not load document bodies"))
    service = Workspace(store)
    envelope = service.create("p", WorkspaceQuery())
    assert envelope["metrics"]["documents"] == 5000
    assert len(envelope["documents"]["items"]) == 50
    assert len(json.dumps(envelope).encode()) < 100_000
    saved = service.require_query(envelope["query_id"])
    seen, cursor, pages = set(), None, 0
    while True:
        page = service.page(saved, "documents", cursor, limit=200)
        assert page["total"] == 5000
        assert page["query_hash"] == envelope["scope_hash"]
        assert len(page["items"]) <= 200
        ids = {item["id"] for item in page["items"]}
        assert len(ids) == len(page["items"]) and not seen.intersection(ids)
        assert all("blocks" not in item and "text" not in item for item in page["items"])
        seen.update(ids)
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 25 and len(seen) == 5000 and "foreign" not in seen


def test_global_filters_share_source_assessment_aggregate_and_cursor_scope(store):
    for identity, options in [("selected", {}), ("wrong-author", {"author": "Author B"}),
                              ("wrong-date", {"published": "2023-12-31"}),
                              ("wrong-type", {"document_type": "report"}), ("wrong-batch", {"batch": "batch-b"})]:
        seed_document(store, identity, **options)
    seed_run(store)
    for identity in ("selected", "wrong-author", "wrong-date", "wrong-type", "wrong-batch"):
        seed_assessment(store, "a-" + identity, identity, review="confirmed")
    seed_assessment(store, "wrong-lens", "selected", lens="nexus", criterion="anti_jewish_tropes")
    seed_assessment(store, "wrong-review", "selected", review="rejected")
    query = WorkspaceQuery(run_id="run", authors=["Author A"], document_types=["news"], batch_ids=["batch-a"],
                           from_date="2024-01-01", to_date="2024-01-31", lens_ids=["ihra"],
                           criterion_ids=["anti_jewish_generalization"], review_status="confirmed",
                           speech_roles=["attributed_quotation"], stance="supports", results=["meets_criterion"],
                           duplicate_policy="sources")
    service = Workspace(store)
    result = service.create("p", query)
    assert [row["id"] for row in result["documents"]["items"]] == ["selected"]
    assert [row["id"] for row in result["findings"]["items"]] == ["a-selected"]
    assert result["metrics"]["documents"] == result["metrics"]["findings"] == result["metrics"]["indicators"] == 1
    assert result["timeline"][0]["date"] == "2024-01"
    assert result["timeline"][0]["indicators"] == 1
    assert result["facets"]["authors"] == [{"name": "Author A", "count": 1}]
    assert {cell["criterion_id"] for cell in result["matrix"]} == {"anti_jewish_generalization"}
    assert service.document_ids(service.require_query(result["query_id"])) == ["selected"]
    assert service.create("p", query)["query_id"] == result["query_id"]


def test_cursor_rejects_other_query_resource_malformed_or_stale_scope(store):
    for index in range(4):
        seed_document(store, str(index))
    service = Workspace(store)
    first = service.create("p", WorkspaceQuery())
    first_saved = service.require_query(first["query_id"])
    cursor = service.page(first_saved, "documents", limit=1)["next_cursor"]
    other = service.create("p", WorkspaceQuery(authors=["Author A"]))
    for saved, resource, value in [(service.require_query(other["query_id"]), "documents", cursor),
                                    (first_saved, "findings", cursor), (first_saved, "documents", "not-json")]:
        with pytest.raises(HTTPException) as error:
            service.page(saved, resource, value)
        assert error.value.status_code == 422
    service.touch("p")
    with pytest.raises(HTTPException) as error:
        service.require_query(first["query_id"])
    assert error.value.status_code == 409
    restored = service.create("p", WorkspaceQuery.model_validate(first_saved["scope"]))
    assert restored["query_id"] != first["query_id"]
    assert restored["scope_hash"] == first["scope_hash"]


def test_boundary_descriptive_negative_and_reported_indicator_remain_distinct(store):
    for identity in ("boundary", "descriptive", "negative", "reported", "failed"):
        seed_document(store, identity)
    seed_run(store)
    seed_assessment(store, "b", "boundary", lens="jda", criterion="political_expression_boundary", kind="boundary")
    seed_assessment(store, "d", "descriptive", lens="discourse", criterion="problem_framing", kind="descriptive")
    seed_assessment(store, "n", "negative", result="does_not_meet")
    seed_assessment(store, "r", "reported", document_stance="opposes")
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "failed", "missing", "failed", 200, "{}", utcnow()])
    service = Workspace(store)
    result = service.create("p", WorkspaceQuery(run_id="run"))
    assert result["metrics"]["findings"] == 3
    assert result["metrics"]["indicators"] == result["metrics"]["reported_indicators"] == 1
    assert result["metrics"]["endorsed_indicators"] == 0
    assert result["metrics"]["coverage"] == 80
    assert result["metrics"]["coverage_detail"]["units_total"] == 5
    assert result["metrics"]["coverage_detail"]["units_done"] == 4
    negatives = service.create("p", WorkspaceQuery(run_id="run", results=["does_not_meet"]))
    assert [row["id"] for row in negatives["findings"]["items"]] == ["n"]
    assert negatives["metrics"]["indicators"] == 0
    assert negatives["metrics"]["coverage"] == 80


def test_source_identity_and_exact_content_are_explicit_views(store):
    seed_document(store, "a", author="First author", sha="same-content")
    seed_document(store, "b", author="Second author", sha="same-content")
    service = Workspace(store)
    assert service.create("p", WorkspaceQuery(duplicate_policy="sources"))["metrics"]["documents"] == 2
    assert service.create("p", WorkspaceQuery(duplicate_policy="exact_content"))["metrics"]["documents"] == 1
    assert store.one("SELECT COUNT(*) n FROM documents WHERE project_id='p'")["n"] == 2


def test_foreign_run_and_invalid_dates_fail_before_creating_query(store):
    seed_run(store, "foreign-run", project="other")
    service = Workspace(store)
    for query, status in [(WorkspaceQuery(run_id="foreign-run"), 404),
                           (WorkspaceQuery(from_date="2024-1-1"), 422)]:
        with pytest.raises(HTTPException) as error:
            service.create("p", query)
        assert error.value.status_code == status
    assert store.one("SELECT COUNT(*) n FROM v3_query_snapshots")["n"] == 0


def test_explicit_large_source_scope_fits_d1_bound_parameter_limit(store, monkeypatch):
    seed_many_documents(store, count=123)
    original = store.query

    def d1_parameter_ceiling(sql, params=()):
        assert len(params) <= 100, "Cloud D1 queries permit at most 100 bound parameters"
        return original(sql, params)

    monkeypatch.setattr(store, "query", d1_parameter_ceiling)
    selected = [f"source-{index:05d}" for index in range(123)]
    result = Workspace(store).create("p", WorkspaceQuery(document_ids=selected, duplicate_policy="sources"))
    assert result["metrics"]["documents"] == result["documents"]["total"] == 123
    assert {row["id"] for row in result["documents"]["items"]}.issubset(set(selected))


def test_twenty_terms_all_filters_and_content_dedup_fit_d1_for_both_cursor_resources(store, monkeypatch):
    terms = [f"needle{index}" for index in range(20)]
    text = " ".join(terms[:10]) + " filler" * 1800 + " " + " ".join(terms[10:])
    for identity, sha in [("selected-a", "same-content"), ("selected-alias", "same-content"), ("selected-b", "other-content")]:
        seed_document(store, identity, sha=sha, text=text)
        store.index_document(identity, "p", "Synthetic long source", [{"id": "b1", "text": text}])
    missing = " ".join(terms[:-1])
    seed_document(store, "missing-term", text=missing)
    store.index_document("missing-term", "p", "Synthetic incomplete match", [{"id": "b1", "text": missing}])
    seed_document(store, "foreign", project="other", batch="other-batch", text=text)
    store.index_document("foreign", "other", "Foreign complete match", [{"id": "b1", "text": text}])
    assert store.one("SELECT COUNT(*) n FROM document_search WHERE id='selected-a'")["n"] > 1
    seed_run(store)
    for identity in ("selected-a", "selected-alias", "selected-b", "missing-term"):
        seed_assessment(store, "finding-" + identity, identity, review="confirmed", unit="unit-" + identity)
    # Maximum allowed list sizes remain JSON parameters, never thousands of
    # placeholders. The old per-term predicates exceeded 100 even at size one.
    query = WorkspaceQuery(
        run_id="run", q=" ".join(terms), document_ids=["selected-a", "selected-alias", "selected-b", "missing-term", "foreign"] + [f"absent-{n}" for n in range(4995)],
        authors=["Author A"] + [f"unused-author-{n}" for n in range(99)],
        document_types=["news"] + [f"unused-type-{n}" for n in range(99)],
        batch_ids=["batch-a"] + [f"unused-batch-{n}" for n in range(99)],
        from_date="2024-01-01", to_date="2024-12-31",
        lens_ids=["ihra"] + [f"unused-lens-{n}" for n in range(11)],
        criterion_ids=["anti_jewish_generalization"] + [f"unused-criterion-{n}" for n in range(99)],
        speech_roles=["attributed_quotation"] + [f"unused-role-{n}" for n in range(9)],
        results=["meets_criterion"] + [f"unused-result-{n}" for n in range(9)],
        review_status="confirmed", stance="supports", duplicate_policy="exact_content",
    )
    original = store.query
    counts = []
    def bounded(sql, params=()):
        counts.append(len(params))
        assert len(params) <= 100, "Every aggregate/count/page must fit the D1 binding ceiling"
        return original(sql, params)
    monkeypatch.setattr(store, "query", bounded)
    service = Workspace(store)
    result = service.create("p", query)
    assert result["documents"]["total"] == result["findings"]["total"] == 2
    assert result["metrics"]["documents"] == result["metrics"]["findings"] == 2
    saved = service.require_query(result["query_id"])
    for resource, expected in [("documents", ["selected-a", "selected-b"]),
                               ("findings", ["finding-selected-a", "finding-selected-b"])]:
        first = service.page(saved, resource, limit=1)
        assert [row["id"] for row in first["items"]] == expected[:1] and first["next_cursor"]
        last = service.page(saved, resource, cursor=first["next_cursor"], limit=1)
        assert [row["id"] for row in last["items"]] == expected[1:] and last["next_cursor"] is None
    assert max(counts) <= 100


@pytest.mark.parametrize("query,expected", [
    ("alpha beta", {"across", "operators"}),
    ("alpha alpha beta", {"across", "operators"}),
    ('"alpha" OR beta* -NOT café', {"operators"}),
    ("CAFÉ", {"operators"}),
    ("alpha beta " + "alpha " * 18 + "unmatchedtwentyfirst", {"across", "operators"}),
])
def test_fts_term_intersection_keeps_fragment_unicode_operator_and_duplicate_semantics(store, query, expected):
    examples = {"across": "alpha " + "filler " * 1800 + "beta", "operators": "alpha OR beta NOT café", "missing": "alpha"}
    for identity, text in examples.items():
        seed_document(store, identity, text=text)
        store.index_document(identity, "p", "Synthetic query fixture", [{"id": "b1", "text": text}])
    result = Workspace(store).create("p", WorkspaceQuery(run_id="none", q=query, duplicate_policy="sources"))
    assert {row["id"] for row in result["documents"]["items"]} == expected


def test_non_fts_fallback_keeps_title_or_author_phrase_matching(store, monkeypatch):
    seed_document(store, "title", author="Other author")
    store.execute("UPDATE documents SET title='Amber Birch dossier' WHERE id='title'")
    seed_document(store, "author", author="Amber Birch")
    seed_document(store, "body-only", author="Other author", text="Amber Birch")
    monkeypatch.setattr(store, "fts", False)
    service = Workspace(store)
    result = service.create("p", WorkspaceQuery(run_id="none", q="Amber Birch", duplicate_policy="sources"))
    assert {row["id"] for row in result["documents"]["items"]} == {"title", "author"}
    reversed_phrase = service.create("p", WorkspaceQuery(run_id="none", q="Birch Amber", duplicate_policy="sources"))
    assert reversed_phrase["documents"]["total"] == 0


def test_indicator_readings_use_all_selected_outcomes_and_distinct_sources(store):
    for doc in ("seen", "absent", "uncertain", "rejected", "outside"):
        seed_document(store, doc, author="Elsewhere" if doc == "outside" else "Reading group")
    seed_run(store)
    seed_assessment(store, "flag", "seen")
    seed_assessment(store, "another-flag", "seen", unit="unit-2")
    seed_assessment(store, "mixed", "seen", unit="unit-3", result="uncertain")
    seed_assessment(store, "negative", "absent", result="does_not_meet")
    seed_assessment(store, "unresolved", "uncertain", result="uncertain")
    seed_assessment(store, "excluded", "rejected", review="rejected")
    seed_assessment(store, "foreign-author", "outside")
    seed_assessment(store, "frame", "seen", kind="descriptive", lens="discourse", criterion="stance")
    service = Workspace(store)
    result = service.create("p", WorkspaceQuery(run_id="run", authors=["Reading group"]))
    rows = result["metrics"]["indicator_readings"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["with_decision"], row["flagged"], row["uncertain"], row["rejected_decisions"]) == (4,1,2,1)
    assert row["example"] == {"assessment_id": "another-flag", "document_id": "seen", "result": "meets_criterion"}
    legacy = service.require_query(result["query_id"])["aggregates"]
    del legacy["metrics"]["indicator_readings"]
    store.execute("UPDATE v3_query_snapshots SET aggregates_json=? WHERE id=?", [json.dumps(legacy), result["query_id"]])
    refreshed = service.create("p", WorkspaceQuery(run_id="run", authors=["Reading group"]))
    assert refreshed["query_id"] != result["query_id"] and refreshed["scope_hash"] == result["scope_hash"]
    assert refreshed["metrics"]["indicator_readings"] == rows
    assert "indicator_readings" not in service.require_query(result["query_id"])["aggregates"]["metrics"]
    restricted = service.create("p", WorkspaceQuery(run_id="run", authors=["Reading group"], results=["uncertain"]))
    row = restricted["metrics"]["indicator_readings"][0]
    assert (row["with_decision"], row["flagged"], row["uncertain"]) == (2, 0, 2)
    assert row["example"]["result"] == "uncertain"
    empty = service.create("p", WorkspaceQuery(run_id="none"))
    assert "indicator_readings" not in empty["metrics"]
