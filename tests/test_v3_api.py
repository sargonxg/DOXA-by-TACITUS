"""Real HTTP/persistence flows for v3; mocked inference makes no quality claim."""
import io
import json
import stat
import zipfile

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.lenses import get_lens
from test_workspace import seed_assessment, seed_document, seed_many_documents, seed_run


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


def project(client):
    response = client.post("/api/projects", json={"name": "Synthetic v3 integration corpus"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def query(client, project_id, **scope):
    response = client.post(f"/api/v3/projects/{project_id}/query", json=scope)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, project_id, text, filename="source.txt", import_id=None):
    response = client.post(f"/api/projects/{project_id}/upload", params={"import_id": import_id} if import_id else {},
                           files=[("files", (filename, text.encode(), "text/plain"))])
    assert response.status_code == 200, response.text
    value = response.json()[0]
    assert value["status"] == "ready", value
    return value


def finish(client, run_id):
    for _ in range(30):
        result = client.portal.call(client.app.state.work_once, run_id)
        if not result["pending"]:
            return client.get(f"/api/runs/{run_id}").json()
    pytest.fail("Synthetic run did not complete in 30 bounded work steps")


class CompactProvider:
    name = "mock"
    profile = {"provider": "mock", "model": "compact-test", "mode": "remote", "revision": "fixed-fixture-1",
               "context_limit": 32768, "source_pack_bytes": 12000}

    def __init__(self, *, invalid=False):
        self.calls = []
        self.invalid = invalid

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_):
        return 0.001

    async def generate(self, messages, schema, **kwargs):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        decisions, details = [], []
        for unit in payload["units"]:
            text = "".join(sentence["text"] for sentence in unit["scored_sentences"])
            selected = next((criterion["key"] for criterion in payload["criteria"] if
                             ("QUOTED" in text and criterion["key"].startswith("ihra/")) or
                             ("BOUNDARY" in text and criterion["key"] == "jda/political_expression_boundary")), None)
            for criterion in payload["criteria"]:
                matched = criterion["key"] == selected
                decisions.append({"unit_id": unit["unit_id"], "criterion_key": criterion["key"],
                                  "result": "meets_criterion" if matched else "does_not_meet"})
                if matched:
                    details.append({"unit_id": unit["unit_id"], "criterion_key": criterion["key"],
                                    "evidence_sentence_ids": ["invented"] if self.invalid else [unit["scored_sentences"][0]["id"]],
                                    "title": "Synthetic quoted statement", "explanation": "The test source represents a speaker and rejects the proposition.",
                                    "alternative": "The passage does not establish the author's endorsement.",
                                    "target": "A stated proposition", "proposition": "The represented proposition is acceptable.",
                                    "speaker": "The represented witness", "attribution": "quoted_speaker", "speaker_stance": "supports",
                                    "document_stance": "opposes", "hypothetical": False, "certainty": "supported"})
        return {"content": {"decisions": decisions, "details": details}, "finish_reason": "stop",
                "usage": {"input_tokens": 100, "output_tokens": 60, "cost_usd": 0.001, "estimated": False}}


def start_mock(client, project_id, monkeypatch, provider=None, **options):
    provider = provider or CompactProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    response = client.post(f"/api/v3/projects/{project_id}/runs", json={"provider": "openai", "model": "compact-test",
                           "lens_ids": ["ihra", "jda"], "allow_remote_text": True, "budget_usd": 1, **options})
    assert response.status_code == 201, response.text
    return response.json(), provider


def test_mocked_compact_run_persists_all_decisions_typed_kinds_and_unit_statuses(client, monkeypatch):
    pid = project(client)
    docs = [upload(client, pid, text, filename) for text, filename in [
        ("QUOTED. A witness states a hostile proposition. The author rejects it.", "quoted.txt"),
        ("BOUNDARY. Political disagreement alone does not establish prejudice.", "boundary.txt"),
        ("COUNTER. The author supports equal rights and rejects collective blame.", "counter.txt")]]
    run, provider = start_mock(client, pid, monkeypatch)
    initial = query(client, pid, run_id=run["id"])
    assert initial["metrics"]["coverage_detail"]["units_done"] == 0
    completed = finish(client, run["id"])
    assert completed["status"] == "completed", completed.get("errors")
    db = client.app.state.store
    expected = 3 * sum(len(get_lens(lens)["criteria"]) for lens in ("ihra", "jda"))
    rows = db.query("SELECT * FROM v3_assessments WHERE run_id=?", [run["id"]])
    assert len(rows) == expected
    assert db.one("SELECT COUNT(*) n FROM v3_unit_statuses WHERE run_id=? AND status='accepted'", [run["id"]])["n"] == 3
    positives = [row for row in rows if row["result"] == "meets_criterion"]
    assert {row["criterion_kind"] for row in positives} == {"indicator", "boundary"}
    assert all(row["document_stance"] == "opposes" and row["attribution"] == "quoted_speaker" for row in positives)
    negative_rows = [row for row in rows if row["result"] == "does_not_meet"]
    assert len(negative_rows) == expected - 2
    assert all("explanation" not in json.loads(row["payload_json"]) for row in negative_rows)
    current = query(client, pid, run_id=run["id"])
    assert current["metrics"]["coverage"] == 100
    assert current["metrics"]["indicators"] == current["metrics"]["reported_indicators"] == 1
    assert current["metrics"]["endorsed_indicators"] == 0
    assert current["metrics"]["findings"] == 2
    assert client.get(f"/api/v3/queries/{initial['query_id']}/findings").status_code == 409
    assert provider.calls
    calls_before_replay = len(provider.calls)
    assert finish(client, run["id"])["status"] == "completed"
    assert len(provider.calls) == calls_before_replay
    assert db.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [run["id"]])["n"] == expected
    for finding in current["findings"]["items"]:
        context = client.get(f"/api/v3/documents/{finding['document_id']}/context", params={"finding_id": finding["id"], "radius": 0})
        assert context.status_code == 200
        source = context.json()["blocks"][0]["text"]
        assert source[finding["start"]:finding["end"]] == finding["quote"]
    assert len({doc["id"] for doc in docs}) == 3


def test_invalid_compact_evidence_stays_partial_without_becoming_a_negative(client, monkeypatch):
    pid = project(client)
    upload(client, pid, "QUOTED. The author rejects a represented hostile statement.")
    run, provider = start_mock(client, pid, monkeypatch, CompactProvider(invalid=True), lens_ids=["ihra"])
    completed = finish(client, run["id"])
    assert completed["status"] == "partial"
    result = query(client, pid, run_id=run["id"])
    assert result["metrics"]["coverage"] == 0
    assert result["metrics"]["findings"] == result["metrics"]["indicators"] == 0
    db = client.app.state.store
    assert db.one("SELECT COUNT(*) n FROM v3_unit_statuses WHERE run_id=? AND status='partial'", [run["id"]])["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=? AND result='meets_criterion'", [run["id"]])["n"] == 0
    assert len(provider.calls) >= 2


def test_query_paging_scope_headers_reports_and_source_mutations(client):
    pid = project(client)
    db = client.app.state.store
    seed_document(db, "selected", project=pid, text="Selected exact source.")
    seed_document(db, "other", project=pid, author="Other author", text="Other exact source.")
    seed_run(db, "run", project=pid)
    seed_assessment(db, "selected-assessment", "selected", project=pid, text="Selected exact source.")
    seed_assessment(db, "other-assessment", "other", project=pid, text="Other exact source.")
    first = query(client, pid, run_id="run", authors=["Author A"], duplicate_policy="sources")
    response = client.post(f"/api/v3/projects/{pid}/reports", json={"query_id": first["query_id"], "format": "jsonl", "profile": "research"})
    assert response.status_code == 200, response.text[:1000]
    assert response.headers["x-doxa-scope"] == first["scope_hash"]
    assert response.headers["x-doxa-snapshot"] == first["snapshot_version"]
    report = db.one("SELECT * FROM reports WHERE project_id=?", [pid])
    snapshot = db.get_json(report["snapshot_key"])
    assert snapshot["query_id"] == first["query_id"] and snapshot["scope_hash"] == first["scope_hash"]
    assert [doc["id"] for doc in snapshot["documents"]] == ["selected"]
    assert [item["id"] for item in snapshot["findings"]] == ["selected-assessment"]
    assert snapshot["metrics"]["documents"] == snapshot["metrics"]["indicators"] == 1
    immutable = response.content
    assert client.patch("/api/documents/selected", json={"author": "Changed author"}).status_code == 200
    assert client.get(f"/api/v3/queries/{first['query_id']}/documents").status_code == 409
    assert client.post(f"/api/v3/projects/{pid}/reports", json={"query_id": first["query_id"], "format": "jsonl"}).status_code == 409
    restored = client.get(f"/api/v3/queries/{first['query_id']}")
    assert restored.status_code == 200 and restored.json()["documents"]["total"] == 0
    assert client.get(f"/api/reports/{report['id']}/download").content == immutable
    second = query(client, pid, run_id="run", duplicate_policy="sources")
    assert client.delete("/api/documents/other").status_code == 200
    assert client.get(f"/api/v3/queries/{second['query_id']}/documents").status_code == 409
    foreign = project(client)
    current = query(client, pid, run_id="run")
    assert client.post(f"/api/v3/projects/{foreign}/reports", json={"query_id": current["query_id"], "format": "jsonl"}).status_code == 404


def test_http_cursors_are_bound_to_query_and_resource_and_new_run_invalidates_latest(client, monkeypatch):
    pid = project(client)
    upload(client, pid, "A first source.", "first.txt")
    upload(client, pid, "A distinct second source.", "second.txt")
    first = query(client, pid)
    page = client.get(f"/api/v3/queries/{first['query_id']}/documents", params={"limit": 1}).json()
    assert page["next_cursor"]
    other = query(client, pid, q="first")
    for query_id, resource in [(other["query_id"], "documents"), (first["query_id"], "findings")]:
        assert client.get(f"/api/v3/queries/{query_id}/{resource}", params={"cursor": page["next_cursor"]}).status_code == 422
    assert client.get(f"/api/v3/queries/{first['query_id']}/unknown").status_code == 404
    run, _ = start_mock(client, pid, monkeypatch)
    assert client.get(f"/api/v3/queries/{first['query_id']}/documents").status_code == 409
    restored = client.get(f"/api/v3/queries/{first['query_id']}").json()
    assert restored["scope"]["run_id"] == run["id"]


def test_review_snapshot_candidate_activation_and_rollback_http_flow(client):
    pid = project(client)
    db = client.app.state.store
    source = "The represented speaker makes a claim. The author rejects it."
    seed_document(db, "train", project=pid, text=source, metadata={"story_group_id": "train", "split": "development", "language": "en"})
    seed_document(db, "eval", project=pid, text=source, metadata={"story_group_id": "eval", "split": "holdout", "language": "en"})
    seed_run(db, "training", project=pid)
    original = seed_assessment(db, "train-a", "train", run="training", project=pid, text=source)
    reviewed = client.patch("/api/v3/findings/train-a/review", json={"review_status": "revised", "review_note": "Corrected by the test operator.",
                            "corrections": {"title": "Corrected attribution title", "document_stance": "opposes", "hypothetical": True}})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["title"] == "Corrected attribution title"
    event = reviewed.json()["review_event"]
    assert event["actor_kind"] == "human" and event["adjudicated"] is True
    assert json.loads(db.one("SELECT payload_json FROM v3_assessments WHERE id='train-a'")["payload_json"]) == original
    approved = client.post(f"/api/v3/projects/{pid}/learning/examples", json={"review_id": event["id"]})
    assert approved.status_code == 200, approved.text
    snapshot = client.post(f"/api/v3/projects/{pid}/learning/snapshots").json()
    assert snapshot["example_count"] == 1
    candidate = client.post(f"/api/v3/projects/{pid}/learning/candidates", json={"snapshot_id": snapshot["id"]}).json()
    sources = [{"id": "eval", "sha256": "eval-hash"}]
    seed_run(db, "baseline", project=pid, sources=sources)
    seed_run(db, "candidate", project=pid, sources=sources, snapshot=snapshot["id"])
    for run_id in ("baseline", "candidate"):
        seed_assessment(db, run_id + "-a", "eval", run=run_id, project=pid, text=source)
    report = client.post(f"/api/v3/projects/{pid}/learning/candidates/{candidate['id']}/evaluate",
                         json={"baseline_run_id": "baseline", "candidate_run_id": "candidate"})
    assert report.status_code == 200, report.text
    assert report.json()["eligible_for_activation"] is True, report.json()
    assert report.json()["interpretation_accuracy"] is None
    activated = client.post(f"/api/v3/projects/{pid}/learning/candidates/{candidate['id']}/activate", json={"report_id": report.json()["id"]})
    assert activated.status_code == 200, activated.text
    overview = client.get(f"/api/v3/projects/{pid}/learning").json()
    assert overview["active_snapshot_id"] == snapshot["id"] and overview["examples"] == 1
    rolled = client.post(f"/api/v3/projects/{pid}/learning/rollback", json={"target_candidate_id": None})
    assert rolled.status_code == 200, rolled.text
    assert client.get(f"/api/v3/projects/{pid}/learning").json()["active_snapshot_id"] is None
    updated = client.patch("/api/v3/findings/train-a/review", json={"review_status": "revised", "corrections": {"result": "does_not_meet", "target": "Corrected target"}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Corrected attribution title"
    assert updated.json()["target"] == "Corrected target" and updated.json()["is_indicator"] is False
    assert updated.json()["review_event"]["previous_review_id"] == event["id"]
    assert json.loads(db.one("SELECT payload_json FROM v3_assessments WHERE id='train-a'")["payload_json"]) == original
    replacement = client.post(f"/api/v3/projects/{pid}/learning/snapshots").json()
    assert replacement["example_count"] == 0 and replacement["id"] != snapshot["id"]
    assert db.one("SELECT COUNT(*) n FROM v3_learning_snapshot_items WHERE snapshot_id=?", [snapshot["id"]])["n"] == 1


def test_review_rejects_invented_evidence_and_foreign_approval(client):
    pid, foreign = project(client), project(client)
    db = client.app.state.store
    seed_document(db, "review-source", project=pid, text="Exact evidence.")
    seed_run(db, "review-run", project=pid)
    seed_assessment(db, "review-a", "review-source", run="review-run", project=pid, text="Exact evidence.")
    assert client.patch("/api/v3/findings/review-a/review", json={"corrections": {"quote": "Fabricated quotation"}}).status_code == 422
    event = client.patch("/api/v3/findings/review-a/review", json={"review_status": "confirmed", "corrections": {}}).json()["review_event"]
    assert client.post(f"/api/v3/projects/{foreign}/learning/examples", json={"review_id": event["id"]}).status_code == 422
    assert client.patch("/api/v3/findings/historical-only/review", json={"corrections": {}}).status_code == 409


def test_import_session_combines_groups_resumes_and_preserves_distinct_source_identity(client):
    pid = project(client)
    session = client.post(f"/api/v3/projects/{pid}/imports", json={"name": "One logical collection", "total_files": 3}).json()
    body = "Equal bytes can belong to independent publications."
    first = upload(client, pid, body, "publisher-a/same.txt", session["id"])
    progress = client.get(f"/api/v3/projects/{pid}/imports/{session['id']}").json()
    assert progress["completed_files"] == 1 and progress["status"] == "running"
    repeated = upload(client, pid, body, "publisher-a/same.txt", session["id"])
    assert repeated["id"] == first["id"]
    response = client.post(f"/api/projects/{pid}/upload", params={"import_id": session["id"]}, files=[
        ("files", ("publisher-b/same.txt", body.encode(), "text/plain")),
        ("files", ("another.txt", b"A distinct publication.", "text/plain"))])
    assert response.status_code == 200, response.text
    final = client.get(f"/api/v3/projects/{pid}/imports/{session['id']}").json()
    assert final["status"] == "completed" and final["completed_files"] == 3 and final["failed_files"] == 0
    documents = client.get(f"/api/projects/{pid}/documents").json()
    assert len(documents) == 3 and {item["batch_id"] for item in documents} == {session["batch_id"]}
    equal = [item for item in documents if item["sha256"] == first["sha256"]]
    assert len(equal) == 2 and len({item["id"] for item in equal}) == 2
    assert {item["metadata"]["source_identity"] for item in equal} == {"publisher-a/same.txt", "publisher-b/same.txt"}
    assert query(client, pid, duplicate_policy="sources")["documents"]["total"] == 3
    assert query(client, pid, duplicate_policy="exact_content")["documents"]["total"] == 2


def archive_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, text in entries:
            archive.writestr(name, text)
    return output.getvalue()


@pytest.mark.parametrize("unsafe", ["../outside.txt", "/absolute.txt", "folder/../../outside.txt", "folder\\..\\..\\outside.txt"])
def test_archive_rejects_unsafe_paths_before_importing_any_member(client, unsafe):
    pid = project(client)
    session = client.post(f"/api/v3/projects/{pid}/imports", json={"total_files": 2}).json()
    payload = archive_bytes([("safe.txt", "Safe source."), (unsafe, "Must not be extracted.")])
    response = client.post(f"/api/v3/projects/{pid}/imports/{session['id']}/archive", files={"file": ("sources.zip", payload, "application/zip")})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/projects/{pid}/documents").json() == []
    assert not (client.app.state.store.root.parent / "outside.txt").exists()


def test_archive_resumes_safely_and_rejects_symlinks_and_bad_zip(client):
    pid = project(client)
    session = client.post(f"/api/v3/projects/{pid}/imports", json={"total_files": 2}).json()
    endpoint = f"/api/v3/projects/{pid}/imports/{session['id']}/archive"
    payload = archive_bytes([("a/source.txt", "Identical article bytes."), ("b/source.txt", "Identical article bytes.")])
    first = client.post(endpoint, files={"file": ("sources.zip", payload, "application/zip")})
    assert first.status_code == 200 and first.json()["completed_files"] == 2
    ids = {item["id"] for item in client.get(f"/api/projects/{pid}/documents").json()}
    replay = client.post(endpoint, files={"file": ("sources.zip", payload, "application/zip")})
    assert replay.status_code == 200 and replay.json()["completed_files"] == 2
    assert {item["id"] for item in client.get(f"/api/projects/{pid}/documents").json()} == ids
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        link = zipfile.ZipInfo("link.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../source.txt")
    assert client.post(endpoint, files={"file": ("link.zip", output.getvalue(), "application/zip")}).status_code == 422
    assert client.post(endpoint, files={"file": ("invalid.zip", b"not a zip", "application/zip")}).status_code == 422
    assert {item["id"] for item in client.get(f"/api/projects/{pid}/documents").json()} == ids


def test_report_rechecks_scope_if_source_changes_after_initial_query_lookup(client, monkeypatch):
    from doxa.workspace import Workspace
    pid = project(client)
    db = client.app.state.store
    seed_document(db, "racing-source", project=pid, text="A frozen test source.")
    selected = query(client, pid, authors=["Author A"])
    original = Workspace.require_query
    changed = False

    def changed_after_lookup(self, query_id, **kwargs):
        nonlocal changed
        snapshot = original(self, query_id, **kwargs)
        if query_id == selected["query_id"] and not changed:
            changed = True
            db.execute("UPDATE documents SET author='Updated author' WHERE id='racing-source'")
            self.touch(pid)
        return snapshot

    monkeypatch.setattr(Workspace, "require_query", changed_after_lookup)
    response = client.post(f"/api/v3/projects/{pid}/reports", json={"query_id": selected["query_id"], "format": "jsonl"})
    assert response.status_code == 409, "A report must not combine cached counts with a newer source selection"
    assert db.one("SELECT COUNT(*) n FROM reports WHERE project_id=?", [pid])["n"] == 0


def test_context_is_bounded_and_foreign_evidence_cannot_be_attached(client):
    pid = project(client)
    db = client.app.state.store
    seed_document(db, "long-context", project=pid, text="Placeholder source.")
    seed_document(db, "different-source", project=pid, text="Foreign evidence.")
    db.put_json("test-sources/long-context/blocks.json", [{"id": f"b{i}", "text": f"Exact paragraph {i}.", "paragraph": i} for i in range(100)])
    seed_run(db, "context-run", project=pid)
    seed_assessment(db, "foreign-a", "different-source", run="context-run", project=pid, text="Foreign evidence.")
    response = client.get("/api/v3/documents/long-context/context", params={"block_id": "b50", "radius": 1000})
    assert response.status_code == 200
    body = response.json()
    assert body["total_blocks"] == 100 and body["start"] == 45 and len(body["blocks"]) == 11
    assert body["next_cursor"] == 56 and "blocks" not in body["document"]
    assert client.get("/api/v3/documents/long-context/context", params={"finding_id": "foreign-a"}).status_code == 404


def test_remote_v3_run_requires_explicit_text_transfer_before_provider_preflight(client, monkeypatch):
    pid = project(client)
    upload(client, pid, "A private source passage.")
    calls = []
    monkeypatch.setattr("doxa.providers.get_provider", lambda config: calls.append(config))
    response = client.post(f"/api/v3/projects/{pid}/runs", json={"provider": "openai", "model": "mock", "lens_ids": ["ihra"]})
    assert response.status_code == 422 and "remote text transfer" in response.text
    assert calls == []
    assert client.app.state.store.one("SELECT COUNT(*) n FROM runs WHERE project_id=?", [pid])["n"] == 0


def test_chunked_uploads_join_one_import_and_replay_does_not_duplicate_sources(client, monkeypatch):
    monkeypatch.setattr("doxa.api.PART_SIZE", 16)
    pid = project(client)
    session = client.post(f"/api/v3/projects/{pid}/imports", json={"name": "Browser chunked intake", "total_files": 2}).json()
    source = b"Identical content from independent source names."

    def complete(filename):
        initialized = client.post(f"/api/projects/{pid}/uploads/init", json={"filename": filename, "size": len(source), "import_id": session["id"]})
        assert initialized.status_code == 200, initialized.text
        uid = initialized.json()["id"]
        parts = []
        for offset in range(0, len(source), 16):
            response = client.put(f"/api/uploads/{uid}/parts/{offset // 16 + 1}", content=source[offset:offset + 16])
            assert response.status_code == 200
            parts.append(response.json())
        response = client.post(f"/api/uploads/{uid}/complete", json={"parts": parts})
        assert response.status_code == 200, response.text
        assert response.json()["batch"]["id"] == session["batch_id"]
        return response.json()

    first = complete("publisher-a.txt")
    replay = complete("publisher-a.txt")
    assert replay["document"]["id"] == first["document"]["id"] and replay["duplicate"] is True
    second = complete("publisher-b.txt")
    assert second["document"]["id"] != first["document"]["id"]
    result = client.get(f"/api/v3/projects/{pid}/imports/{session['id']}").json()
    assert result["completed_files"] == 2 and result["failed_files"] == 0
    assert result["status"] == "completed"
    assert query(client, pid, duplicate_policy="sources")["documents"]["total"] == 2


def test_http_query_reaches_all_5000_metadata_records_with_bounded_pages(client, monkeypatch):
    pid = project(client)
    store = client.app.state.store
    seed_many_documents(store, project=pid)
    monkeypatch.setattr(store, "get_json", lambda *_: pytest.fail("Library paging must not open source bodies"))
    selected = query(client, pid)
    assert selected["documents"]["total"] == selected["metrics"]["documents"] == 5000
    assert len(selected["documents"]["items"]) == 50
    assert len(json.dumps(selected).encode()) < 100_000
    ids, cursor = set(), None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = client.get(f"/api/v3/queries/{selected['query_id']}/documents", params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page["items"]) <= 200 and page["query_hash"] == selected["scope_hash"]
        assert page["snapshot_version"] == selected["snapshot_version"]
        assert all("blocks" not in item for item in page["items"])
        next_ids = {item["id"] for item in page["items"]}
        assert not ids.intersection(next_ids)
        ids.update(next_ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(ids) == 5000
