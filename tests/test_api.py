"""API workflows use real persisted sources, jobs, evidence and downloadable files.

The mocked provider below exercises transport-independent job and budget behavior;
it is never presented as a model-quality benchmark. These tests make no network
requests or paid inference calls. TestClient needs local event-loop sockets.
"""
import asyncio
import hashlib
import io
import json
import threading
import zipfile

from fastapi.testclient import TestClient
import pytest

from doxa.api import ROOT, create_app
from doxa.store import utcnow


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, start_workers=False, use_saved_showcase=False)
    with TestClient(app) as result:
        yield result


def project(client, name="API integration corpus"):
    response = client.post("/api/projects", json={"name": name, "description": "A reproducible test workspace"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, project_id, content="Equal rights and freedom should be defended. Jews and Muslims are mentioned in context.", filename="source.txt"):
    response = client.post(f"/api/projects/{project_id}/upload", files=[("files", (filename, content.encode(), "text/plain"))])
    assert response.status_code == 200, response.text
    result = response.json()[0]
    assert result["status"] == "ready", result.get("error")
    return result


def start(client, project_id, **changes):
    payload = {"provider": "lexical", "lens_ids": ["discourse", "ihra", "anti-muslim-hostility"], **changes}
    response = client.post(f"/api/projects/{project_id}/runs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def work(client, run_id):
    # Execute on TestClient's actual application event loop, with automatic
    # scheduling disabled, so every checkpoint is deterministic and inspectable.
    return client.portal.call(client.app.state.work_once, run_id)


def finish(client, run_id, limit=1000):
    for _ in range(limit):
        result = work(client, run_id)
        if not result["pending"]:
            return result["run"]
    pytest.fail("Run did not settle within its bounded number of work steps")


def all_findings(client, project_id, **filters):
    values, offset = [], 0
    while True:
        response = client.get(f"/api/projects/{project_id}/findings", params={"limit": 10000, "offset": offset, **filters})
        assert response.status_code == 200, response.text
        page = response.json()
        values.extend(page)
        if len(page) < 10000:
            return values
        offset += len(page)


def test_health_projects_metadata_search_and_pagination(client):
    health = client.get("/api/health")
    assert health.status_code == 200 and health.json()["mode"] == "local"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/projects").json()
    pid = project(client)
    document = upload(client, pid)
    response = client.patch(f"/api/documents/{document['id']}", json={"author": "Researcher A", "document_type": "editorial", "sha256": "forbidden"})
    assert response.status_code == 200
    assert response.json()["author"] == "Researcher A"
    assert response.json()["sha256"] == document["sha256"]
    assert len(client.get(f"/api/projects/{pid}/documents", params={"q": "freedom"}).json()) == 1
    assert client.get(f"/api/projects/{pid}/documents", params={"offset": -1}).status_code == 422
    assert client.get(f"/api/projects/{pid}/documents", params={"limit": 3000}).status_code == 422
    assert client.get("/api/documents/missing").status_code == 404


def test_small_upload_exact_hash_source_roundtrip_and_duplicate(client):
    pid = project(client)
    source = "  An exact quotation 🧭.\nThe second line retains spacing.  "
    document = upload(client, pid, source, "../../source.txt")
    assert document["filename"] == "source.txt"
    assert document["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    detail = client.get(f"/api/documents/{document['id']}").json()
    assert detail["blocks"][0]["text"] == source
    assert detail["metadata"]["sha256"] == document["sha256"]
    assert client.get(f"/api/documents/{document['id']}/source").content == source.encode()
    assert upload(client, pid, source)["id"] == document["id"]
    assert len(client.get(f"/api/projects/{pid}/documents").json()) == 1


def test_multipart_order_checksum_abort_and_duplicate(client, monkeypatch):
    # Small deterministic part size exercises the same chunk protocol without
    # creating multi-megabyte fixture files on every test run.
    monkeypatch.setattr("doxa.api.PART_SIZE", 12)
    pid = project(client)
    source = b"Equal rights should apply to everyone.\n"

    def initialize():
        response = client.post(f"/api/projects/{pid}/uploads/init", json={"filename": "../../parts.txt", "size": len(source), "mime_type": "text/plain"})
        assert response.status_code == 200
        assert response.json()["part_size"] == 12
        return response.json()["id"]

    uid = initialize()
    assert client.put(f"/api/uploads/{uid}/parts/0", content=b"x").status_code == 422
    assert client.put(f"/api/uploads/{uid}/parts/1", content=b"short").status_code == 422
    assert client.post(f"/api/uploads/{uid}/complete", json={"parts": []}).status_code == 422
    parts = []
    for number in reversed(range(1, (len(source)+11)//12+1)):
        chunk = source[(number-1)*12:number*12]
        result = client.put(f"/api/uploads/{uid}/parts/{number}", content=chunk)
        assert result.status_code == 200
        parts.append(result.json())
    bad = [dict(p) for p in parts]
    bad[0]["etag"] = "wrong"
    assert client.post(f"/api/uploads/{uid}/complete", json={"parts": bad}).status_code == 422
    response = client.post(f"/api/uploads/{uid}/complete", json={"parts": parts})
    assert response.status_code == 200, response.text
    assert response.json()["document"]["filename"] == "parts.txt"
    assert response.json()["document"]["sha256"] == hashlib.sha256(source).hexdigest()
    assert response.json()["duplicate"] is False
    assert upload(client, pid, source.decode())["id"] == response.json()["document"]["id"]
    aborted = initialize()
    assert client.delete(f"/api/uploads/{aborted}").status_code == 200
    assert client.put(f"/api/uploads/{aborted}/parts/1", content=source[:12]).status_code == 404
    assert client.post(f"/api/projects/{pid}/uploads/init", json={"filename": "bad.txt", "size": 0}).status_code == 422


def test_host_csrf_and_internal_service_token_protection(client, monkeypatch):
    assert client.get("/api/health", headers={"host": "attacker.invalid"}).status_code == 403
    assert client.post("/api/projects", json={"name": "Denied"}, headers={"origin": "https://attacker.invalid"}).status_code == 403
    assert client.post("/api/projects", json={"name": "Allowed"}, headers={"origin": "http://testserver"}).status_code == 201
    monkeypatch.setenv("DOXA_INTERNAL_TOKEN", "test-service-token")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"x-doxa-service-token": "wrong"}).status_code == 401
    assert client.get("/api/health", headers={"x-doxa-service-token": "test-service-token"}).status_code == 200


def test_failed_extraction_is_not_analyzable_and_active_sources_are_protected(client):
    pid = project(client)
    bad = client.post(f"/api/projects/{pid}/upload", files=[("files", ("bad.pdf", b"not a pdf", "application/pdf"))]).json()[0]
    assert bad["status"] == "failed" and bad["error"]
    assert client.post(f"/api/projects/{pid}/runs", json={"provider": "lexical"}).status_code == 422
    good = upload(client, pid)
    run = start(client, pid)
    assert client.delete(f"/api/documents/{good['id']}").status_code == 409
    client.post(f"/api/runs/{run['id']}/cancel")
    assert client.delete(f"/api/documents/{good['id']}").status_code == 200


def test_lexical_run_exact_evidence_review_audit_and_idempotent_work(client):
    pid = project(client)
    document = upload(client, pid)
    run = start(client, pid)
    complete = finish(client, run["id"])
    assert complete["status"] == "completed"
    assert complete["progress"]["complete"] is True
    assert complete["model_profile"]["semantic_analysis"] is False
    assert complete["progress"]["semantic_analysis_performed"] is False
    assert complete["progress"]["semantic_blocks_done"] == 0
    assert complete["progress"]["lexical_blocks_done"] > 0
    findings = all_findings(client, pid)
    assert findings
    source = client.get(f"/api/documents/{document['id']}").json()["blocks"]
    blocks = {b["id"]: b["text"] for b in source}
    assert all(blocks[f["block_id"]][f["start"]:f["end"]] == f["quote"] for f in findings)
    assert all(f["evidence_kind"] == "lexical_match" and f["certainty"] == "insufficient_context" for f in findings)
    selected = findings[0]
    first = client.patch(f"/api/findings/{selected['id']}/review", json={"review_status": "confirmed", "review_note": "Confirmed only as a literal mention."})
    assert first.status_code == 200
    client.patch(f"/api/findings/{selected['id']}/review", json={"review_status": "revised", "review_note": "Retain the contextual limitation."})
    audit = client.get(f"/api/findings/{selected['id']}/reviews").json()
    assert [a["previous_status"] for a in audit] == ["unreviewed", "confirmed"]
    assert [a["review_status"] for a in audit] == ["confirmed", "revised"]
    assert len(all_findings(client, pid, review_status="revised")) == 1
    assert client.patch(f"/api/findings/{selected['id']}/review", json={"review_status": "invented"}).status_code == 422
    assert work(client, run["id"])["pending"] is False
    assert len(all_findings(client, pid)) == len(findings)
    dashboard = client.get(f"/api/projects/{pid}/dashboard").json()
    assert dashboard["processing_coverage"] == dashboard["lexical_coverage"] == 100
    assert dashboard["coverage"] == dashboard["semantic_coverage"] == 0
    assert dashboard["findings"] == len(findings)
    assert dashboard["method"] == "lexical"


def test_cancel_resume_and_restart_preserve_job_and_evidence(tmp_path):
    app = create_app(tmp_path, start_workers=False, use_saved_showcase=False)
    with TestClient(app) as client:
        pid = project(client)
        upload(client, pid)
        run = start(client, pid)
        cancelled = client.post(f"/api/runs/{run['id']}/cancel").json()
        assert cancelled["status"] == "cancelled"
        assert work(client, run["id"])["pending"] is False
        assert all_findings(client, pid) == []
        assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    restarted = create_app(tmp_path, start_workers=False, use_saved_showcase=False)
    with TestClient(restarted) as client:
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "queued"
        assert len(client.get(f"/api/projects/{pid}/documents").json()) == 1
        assert finish(client, run["id"])["status"] == "completed"
        saved = all_findings(client, pid)
        assert saved
    final = create_app(tmp_path, start_workers=False, use_saved_showcase=False)
    with TestClient(final) as client:
        assert all_findings(client, pid) == saved
        assert work(client, run["id"])["pending"] is False


def test_exports_are_actual_artifacts_with_immutable_snapshot_and_all_profiles(client):
    pid = project(client, "A small export corpus")
    upload(client, pid)
    run = start(client, pid)
    finish(client, run["id"])
    responses = {}
    for format in ("html", "pdf", "xlsx", "csv", "jsonl"):
        response = client.post(f"/api/projects/{pid}/reports", json={"format": format, "profile": "research"})
        assert response.status_code == 200, response.text[:1000]
        responses[format] = response
    assert responses["pdf"].content.startswith(b"%PDF-")
    with zipfile.ZipFile(io.BytesIO(responses["xlsx"].content)) as workbook:
        assert "xl/workbook.xml" in workbook.namelist()
        assert len([p for p in workbook.namelist() if p.startswith("xl/charts/chart") and p.endswith(".xml")]) == 2
    assert "<svg" in responses["html"].text and "Exact case-insensitive word match" in responses["html"].text
    records = [json.loads(line) for line in responses["jsonl"].text.splitlines()]
    assert any(r["record_type"] == "finding" for r in records)
    assert any(r["record_type"] == "document" and r["data"].get("blocks") for r in records)
    for profile, label in [("briefing", "Executive briefing"), ("teaching", "Teaching edition")]:
        response = client.post(f"/api/projects/{pid}/reports", json={"format": "html", "profile": profile})
        assert response.status_code == 200 and label in response.text
    reports = client.get(f"/api/projects/{pid}/reports").json()
    assert len(reports) == 7
    first_html = next(r for r in reports if r["format"] == "html" and r["profile"] == "research")
    before = client.get(f"/api/reports/{first_html['id']}/download").content
    finding = all_findings(client, pid)[0]
    client.patch(f"/api/findings/{finding['id']}/review", json={"review_status": "rejected", "review_note": "Later review"})
    assert client.get(f"/api/reports/{first_html['id']}/download").content == before
    assert client.post(f"/api/projects/{pid}/reports", json={"format": "exe"}).status_code == 422


class BudgetProvider:
    name = "fake"
    profile = {"provider": "fake", "model": "job-budget-test", "mode": "remote"}

    def __init__(self, price=0.1):
        self.price = price
        self.calls = 0

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_):
        return self.price

    async def generate(self, messages, schema, **kwargs):
        self.calls += 1
        request = json.loads(messages[-1]["content"])
        window = request["source_windows"][0]
        quote = window["text"].split()[0]
        start = window["text"].index(quote)
        lens = request["lenses"][0]
        finding = {"block_id": window["block_id"], "start": start, "end": start+len(quote), "quote": quote, "category": "test_observation", "target": "test proposition", "lens_id": lens["id"], "criterion": lens["criteria"][0]["id"], "title": "Fixture observation", "explanation": "A deterministic test provider exercises source evidence persistence.", "alternative": "This is a transport fixture and makes no semantic claim.", "stance": "reports", "speaker": "Unknown", "attribution": "unknown", "certainty": "insufficient_context"}
        return {"content": {"findings": [finding]}, "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": self.price, "estimated": False, "requests": 1}, "finish_reason": "stop", "model_profile": self.profile}


def test_shared_monthly_budget_pauses_without_overrun_or_paid_calls(client, monkeypatch):
    provider = BudgetProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    assert client.put("/api/settings", json={"monthly_budget_usd": 0.15}).status_code == 200
    first = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    second = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    assert finish(client, first["id"])["status"] == "completed"
    assert finish(client, second["id"])["status"] == "paused_budget"
    assert provider.calls == 1
    budget = client.get("/api/settings").json()["budget"]
    assert budget["spent_usd"] == pytest.approx(0.1)
    assert budget["reserved_usd"] == 0
    assert budget["spent_usd"] + budget["reserved_usd"] <= 0.15


def test_budget_pause_resume_keeps_review_history_and_cumulative_usage(client, monkeypatch):
    provider = BudgetProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    # Two genuine bounded inference requests, even when adjacent short blocks
    # are efficiently packed by the analysis engine.
    upload(client, pid, "Jews are mentioned in reported speech. " + "Context sentence. "*115 + "\n\nMuslims are mentioned in a second source paragraph. " + "Another context. "*120)
    client.put("/api/settings", json={"monthly_budget_usd": 0.15})
    run = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    paused = finish(client, run["id"])
    assert paused["status"] == "paused_budget"
    assert provider.calls == 1
    findings = all_findings(client, pid)
    assert findings, "Source-valid observations from a partially processed task must remain reviewable"
    identity = findings[0]["id"]
    client.patch(f"/api/findings/{identity}/review", json={"review_status": "confirmed", "review_note": "Retain this human adjudication across resume."})
    client.put("/api/settings", json={"monthly_budget_usd": 2})
    assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    complete = finish(client, run["id"])
    assert complete["status"] == "completed"
    after = {f["id"]: f for f in all_findings(client, pid)}
    assert after[identity]["review_status"] == "confirmed"
    assert after[identity]["review_note"] == "Retain this human adjudication across resume."
    assert len(client.get(f"/api/findings/{identity}/reviews").json()) == 1
    assert complete["usage"]["requests"] == provider.calls
    assert complete["usage"]["cost_usd"] == pytest.approx(provider.calls*0.1)


def test_dashboard_denominators_follow_selected_run_and_document(client):
    pid = project(client)
    selected = upload(client, pid, "Equal rights should protect everyone.", "selected.txt")
    upload(client, pid, "An unrelated source. " * 80, "other.txt")
    run = start(client, pid, document_ids=[selected["id"]])
    finish(client, run["id"])
    dashboard = client.get(f"/api/projects/{pid}/dashboard", params={"run_id": run["id"], "document_id": selected["id"]}).json()
    assert dashboard["documents"] == 1
    assert dashboard["total_words"] == selected["word_count"]
    assert dashboard["coverage_detail"]["documents_total"] == 1
    assert sum(a["documents"] for a in dashboard["by_author"]) == 1


def test_run_filters_cannot_mix_evidence_across_projects(client):
    first = project(client, "First project")
    second = project(client, "Second project")
    document = upload(client, first)
    foreign_run = start(client, first)
    finish(client, foreign_run["id"])
    assert client.get(f"/api/projects/{second}/findings", params={"run_id": foreign_run["id"]}).status_code == 404
    assert client.get(f"/api/projects/{second}/dashboard", params={"run_id": foreign_run["id"]}).status_code == 404
    assert client.post(f"/api/projects/{second}/reports", json={"format": "jsonl", "filters": {"run_id": foreign_run["id"]}}).status_code == 404
    assert client.post(f"/api/projects/{second}/runs", json={"provider": "lexical", "document_ids": [document["id"]]}).status_code == 422


def test_invalid_inquiry_and_custom_rubric_fail_before_jobs_are_created(client):
    pid = project(client)
    upload(client, pid)
    for invalid in [
        {"provider": "lexical", "question": "x"*4500},
        {"provider": "lexical", "persona": "x"*2100},
        {"provider": "lexical", "lens_ids": ["missing-lens"]},
        {"provider": "lexical", "lens_ids": ["custom"], "custom_lens": {"name": "Broken", "criteria": []}},
    ]:
        response = client.post(f"/api/projects/{pid}/runs", json=invalid)
        assert response.status_code == 422, (invalid, response.text)
    assert client.get(f"/api/projects/{pid}/runs").json() == []


def test_concurrent_jobs_cannot_spend_the_same_shared_budget(client, monkeypatch):
    class ConcurrentProvider(BudgetProvider):
        async def generate(self, *args, **kwargs):
            await asyncio.sleep(0.025)
            return await super().generate(*args, **kwargs)

    provider = ConcurrentProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    client.put("/api/settings", json={"monthly_budget_usd": 0.15})
    first = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    second = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    futures = [client.portal.start_task_soon(client.app.state.work_once, r["id"]) for r in (first, second)]
    results = [future.result(timeout=10)["run"] for future in futures]
    assert sorted(r["status"] for r in results) == ["completed", "paused_budget"]
    assert provider.calls == 1
    budget = client.get("/api/settings").json()["budget"]
    assert budget["reserved_usd"] == 0
    assert budget["spent_usd"] == pytest.approx(0.1)
    runs = client.app.state.store.query("SELECT reserved_usd,spent_usd FROM runs WHERE project_id=?", [pid])
    assert sum(r["reserved_usd"] for r in runs) == 0
    assert sum(r["spent_usd"] for r in runs) == pytest.approx(0.1)


def test_cancelling_inflight_task_cannot_publish_late_findings(client, monkeypatch):
    started, released = threading.Event(), threading.Event()

    class BlockingProvider(BudgetProvider):
        async def generate(self, *args, **kwargs):
            started.set()
            while not released.is_set():
                await asyncio.sleep(0.005)
            return await super().generate(*args, **kwargs)

    provider = BlockingProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    run = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=1)
    future = client.portal.start_task_soon(client.app.state.work_once, run["id"])
    try:
        assert started.wait(5)
        assert client.post(f"/api/runs/{run['id']}/cancel").json()["status"] == "cancelled"
    finally:
        released.set()
    assert future.result(timeout=10)["run"]["status"] == "cancelled"
    assert all_findings(client, pid) == []
    ledger = client.get("/api/settings").json()["budget"]
    assert ledger["reserved_usd"] == 0
    assert ledger["spent_usd"] == pytest.approx(0.1)


def test_expired_lease_settles_orphan_reservation_once_and_retains_unknown_cost(client, monkeypatch):
    provider = BudgetProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    run = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=2)
    store = client.app.state.store
    task = store.one("SELECT id FROM tasks WHERE run_id=?", [run["id"]])
    month = utcnow()[:7]
    store.batch([
        {"sql": "UPDATE tasks SET status='running',lease_token='old-crashed-lease',lease_until=0,attempts=1 WHERE id=?", "params": [task["id"]]},
        {"sql": "UPDATE runs SET status='running',reserved_usd=0.25 WHERE id=?", "params": [run["id"]]},
        {"sql": "INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES(?,0.25,0)", "params": [month]},
        {"sql": "INSERT INTO budget_reservations(id,run_id,task_id,month,amount,status,created_at) VALUES(?,?,?,?,0.25,'reserved',?)", "params": ["orphan", run["id"], task["id"], month, utcnow()]},
    ])
    completed = finish(client, run["id"])
    assert completed["status"] == "completed"
    assert provider.calls == 1
    assert completed["usage"]["cost_usd"] is None
    assert completed["spent_usd"] == pytest.approx(0.35)
    reservations = store.query("SELECT * FROM budget_reservations WHERE run_id=?", [run["id"]])
    assert len(reservations) == 2 and all(r["status"] == "settled" for r in reservations)
    assert next(r for r in reservations if r["id"] == "orphan")["charged_usd"] == pytest.approx(0.25)
    ledger = client.get("/api/settings").json()["budget"]
    assert ledger["reserved_usd"] == 0 and ledger["spent_usd"] == pytest.approx(0.35)
    assert work(client, run["id"])["pending"] is False
    assert client.get("/api/settings").json()["budget"] == ledger


def test_20_real_documents_import_hash_metadata_exhaustive_baseline(client):
    source_files = sorted((ROOT / "corpus" / "documents").glob("*.json"))
    assert len(source_files) == 20, "The required real source corpus must ship with the runnable repository"
    pid = project(client, "20-document integration benchmark")
    response = client.post(f"/api/projects/{pid}/demo")
    assert response.status_code == 200, response.text
    demo = response.json()
    assert len(demo["documents"]) == 20
    sources = {json.loads(path.read_text())["id"]: json.loads(path.read_text()) for path in source_files}
    blocks = {}
    for document in demo["documents"]:
        original = sources[document["metadata"]["source_id"]]
        assert document["sha256"] == original["content_sha256"]
        assert document["metadata"]["source_sha256"] == original["source_sha256"]
        assert document["author"] == original["author"]
        assert document["license"] == original["license"]
        assert document["source_url"] == original["source_url"]
        full = client.get(f"/api/documents/{document['id']}").json()
        canonical = json.dumps(full["blocks"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical).hexdigest() == document["sha256"]
        blocks[document["id"]] = {b["id"]: b["text"] for b in full["blocks"]}
    completed = finish(client, demo["run"]["id"])
    assert completed["status"] == "completed", completed["errors"]
    assert completed["progress"]["documents_total"] == completed["progress"]["documents_done"] == 20
    assert completed["progress"]["blocks_done"] == sum(len(b) for b in blocks.values())
    assert completed["progress"]["complete"] is True
    findings = all_findings(client, pid)
    assert len(findings) > 100
    assert all(blocks[f["document_id"]][f["block_id"]][f["start"]:f["end"]] == f["quote"] for f in findings)
    assert len({f["id"] for f in findings}) == len(findings)
    assert all(f["evidence_kind"] == "lexical_match" for f in findings)
    dashboard = client.get(f"/api/projects/{pid}/dashboard").json()
    assert dashboard["documents"] == 20 and dashboard["processing_coverage"] == 100
    assert dashboard["semantic_coverage"] == 0 and dashboard["lexical_coverage"] == 100
    assert dashboard["total_words"] == sum(d["word_count"] for d in demo["documents"])
    assert dashboard["findings"] == len(findings)
    assert work(client, demo["run"]["id"])["pending"] is False
    # Reimport deduplicates by source content, while a distinct run remains explicit.
    second = client.post(f"/api/projects/{pid}/demo")
    assert second.status_code == 200
    assert {d["id"] for d in second.json()["documents"]} == set(blocks)
    assert len(client.get(f"/api/projects/{pid}/documents").json()) == 20


def test_csv_metadata_import_matches_filename_id_and_sha256_without_changing_sources(client):
    import csv
    pid = project(client, "Metadata reconciliation")
    first = upload(client, pid, "First document should retain its source text.", "first.txt")
    second = upload(client, pid, "Second document has an immutable source digest.", "second.txt")
    third = upload(client, pid, "Third source includes a known document reference.", "third.txt")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "filename", "sha256", "title", "author", "institution", "document_type", "published_at"])
    writer.writeheader()
    writer.writerow({"filename": "first.txt", "title": "Reconciled public testimony", "author": "Doe, Jane", "institution": "A & B", "document_type": "testimony", "published_at": "2026-10-10"})
    writer.writerow({"id": second["id"], "filename": "ignored-conflicting-name.txt", "title": "An identifier-matched record", "author": "Researcher B"})
    writer.writerow({"sha256": third["sha256"], "title": "A checksum-matched record", "author": "Researcher C"})
    writer.writerow({"filename": "does-not-exist.txt", "author": "Unmatched row"})
    response = client.post(f"/api/projects/{pid}/metadata/import", files={"file": ("metadata.csv", b"\xef\xbb\xbf"+output.getvalue().encode(), "text/csv")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == 3 and len(result["unmatched"]) == 1 and result["errors"] == []
    expected = [(first, "Reconciled public testimony", "Doe, Jane"), (second, "An identifier-matched record", "Researcher B"), (third, "A checksum-matched record", "Researcher C")]
    for original, title, author in expected:
        updated = client.get(f"/api/documents/{original['id']}").json()
        assert updated["title"] == title and updated["author"] == author
        assert updated["sha256"] == original["sha256"]
    detail = client.get(f"/api/documents/{first['id']}").json()
    assert detail["institution"] == "A & B" and detail["document_type"] == "testimony"
    assert detail["published_at"] == "2026-10-10"
    # Reconciled titles must participate in the search index as well as metadata.
    assert len(client.get(f"/api/projects/{pid}/documents", params={"q": "reconciled"}).json()) == 1
    assert client.get(f"/api/documents/{first['id']}/source").content == b"First document should retain its source text."


def test_metadata_csv_ambiguous_foreign_invalid_and_oversized_rows_are_explicit(client):
    import csv
    pid, foreign_pid = project(client, "CSV limits"), project(client, "Foreign project")
    first = upload(client, pid, "First same-named document.", "same.txt")
    upload(client, pid, "Second same-named document with distinct contents.", "same.txt")
    foreign = upload(client, foreign_pid, "Private to another project.", "foreign.txt")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "filename", "author"])
    writer.writeheader()
    writer.writerow({"filename": "same.txt", "author": "Ambiguous"})
    writer.writerow({"id": foreign["id"], "author": "Cross-project change"})
    writer.writerow({"id": first["id"], "author": "x"*2001})
    response = client.post(f"/api/projects/{pid}/metadata/import", files={"file": ("metadata.csv", output.getvalue(), "text/csv")})
    assert response.status_code == 200
    assert response.json()["updated"] == 0
    assert len(response.json()["unmatched"]) == 2 and len(response.json()["errors"]) == 1
    assert client.get(f"/api/documents/{foreign['id']}").json()["author"] == foreign["author"]
    assert client.get(f"/api/documents/{first['id']}").json()["author"] == first["author"]
    invalid_csvs = [b"author,title\nNo key,Missing key\n", b"filename,author\nfile.txt,\xff\n", ("filename,author\n" + "missing.txt,A\n"*1001).encode()]
    for raw in invalid_csvs:
        response = client.post(f"/api/projects/{pid}/metadata/import", files={"file": ("invalid.csv", raw, "text/csv")})
        assert response.status_code == 422, response.text
    response = client.post(f"/api/projects/{pid}/metadata/import", files={"file": ("oversize.csv", b"x"*(2*1024*1024+1), "text/csv")})
    assert response.status_code == 413


def test_real_source_rights_and_extraction_scope_survive_api_import_and_jsonl(client):
    pid = project(client, "Rights provenance")
    response = client.post(f"/api/projects/{pid}/demo")
    assert response.status_code == 200, response.text
    originals = {json.loads(path.read_text())["id"]: json.loads(path.read_text()) for path in (ROOT / "corpus" / "documents").glob("*.json")}
    documents = response.json()["documents"]
    assert len(documents) == 20
    preserved = ["attribution", "license_url", "rights_source_url", "rights_notes", "rights_review", "coverage", "language", "synthetic"]
    for document in documents:
        source = originals[document["metadata"]["source_id"]]
        for field in preserved:
            if field in source:
                assert document["metadata"][field] == source[field], (source["id"], field)
    exported = client.post(f"/api/projects/{pid}/reports", json={"format": "jsonl", "profile": "research"})
    assert exported.status_code == 200
    records = [json.loads(line) for line in exported.text.splitlines()]
    sources = [line["data"] for line in records if line["record_type"] == "document"]
    assert len(sources) == 20
    assert all(source["metadata"].get("attribution") for source in sources)
    assert any("Open Government Licence" in source["metadata"]["attribution"] for source in sources)


def test_run_freezes_verified_model_revision_and_resume_rejects_changed_digest(client, monkeypatch):
    class RevisionProvider(BudgetProvider):
        def __init__(self):
            super().__init__()
            self.profile = {**self.profile, "provider": "ollama", "model": "installed-model", "revision": "a"*64, "quantization": "Q4_K_M", "mode": "local"}

    provider = RevisionProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    source = upload(client, pid)
    client.patch(f"/api/documents/{source['id']}", json={"author": "Author when run was created", "title": "Original inquiry title"})
    run = start(client, pid, provider="ollama", model="installed-model", lens_ids=["ihra"])
    assert run["config"]["model_revision"] == "a"*64
    assert run["config"]["quantization"] == "Q4_K_M"
    assert run["config"]["provider_preflight"]["revision"] == "a"*64
    manifest = client.app.state.store.get_json(run["config"]["source_manifest_key"])
    assert manifest[0]["author"] == "Author when run was created"
    assert manifest[0]["title"] == "Original inquiry title"
    assert run["config"]["source_snapshot"][0]["sha256"] == source["sha256"]
    client.post(f"/api/runs/{run['id']}/cancel")
    provider.profile["revision"] = "b"*64
    rejected = client.post(f"/api/runs/{run['id']}/resume", json={"budget_usd": 3})
    assert rejected.status_code == 409, rejected.text
    preserved = client.get(f"/api/runs/{run['id']}").json()
    assert preserved["status"] == "cancelled"
    assert preserved["config"]["model_revision"] == "a"*64
    assert preserved["config"]["budget_usd"] == run["config"]["budget_usd"]
    assert not preserved["config"].get("budget_changes")
    provider.profile["revision"] = "a"*64
    assert client.post(f"/api/runs/{run['id']}/resume").status_code == 200
    assert finish(client, run["id"])["status"] == "completed"


def test_resume_budget_increase_is_validated_and_audited_before_more_work(client, monkeypatch):
    provider = BudgetProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    run = start(client, pid, provider="openai", lens_ids=["ihra"], budget_usd=0.05)
    assert finish(client, run["id"])["status"] == "paused_budget"
    assert provider.calls == 0
    for raw in ('{"budget_usd":NaN}', '{"budget_usd":1e309}', '{"budget_usd":-1}', '{"budget_usd":10001}', '{"budget_usd":"3"}'):
        invalid = client.post(f"/api/runs/{run['id']}/resume", content=raw, headers={"content-type": "application/json"})
        assert invalid.status_code == 422, invalid.text
    paused = client.get(f"/api/runs/{run['id']}").json()
    assert paused["status"] == "paused_budget" and paused["config"]["budget_usd"] == 0.05
    resumed = client.post(f"/api/runs/{run['id']}/resume", json={"budget_usd": 0.3})
    assert resumed.status_code == 200, resumed.text
    changes = resumed.json()["config"]["budget_changes"]
    assert len(changes) == 1 and changes[0]["previous"] == 0.05 and changes[0]["new"] == 0.3 and changes[0]["at"]
    completed = finish(client, run["id"])
    assert completed["status"] == "completed" and provider.calls == 1
    assert completed["config"]["budget_usd"] == 0.3 and completed["config"]["budget_changes"] == changes
    assert completed["spent_usd"] == pytest.approx(0.1)


def test_monthly_settings_reject_nonfinite_numbers_without_overwriting_budget(client):
    original = client.get("/api/settings").json()["monthly_budget_usd"]
    for raw in ('{"monthly_budget_usd":NaN}', '{"monthly_budget_usd":1e309}', '{"monthly_budget_usd":-1}', '{"monthly_budget_usd":10001}'):
        response = client.put("/api/settings", content=raw, headers={"content-type": "application/json"})
        assert response.status_code == 422, response.text
        assert client.get("/api/settings").json()["monthly_budget_usd"] == original


def test_replayed_cloud_upload_completion_keeps_the_existing_original(client):
    pid = project(client, "Retried gateway upload")
    source = b"Equal rights should be protected during retries.\n"
    key = "uploads/gateway-session/source.txt"
    store = client.app.state.store
    store.put_bytes(key, source, "text/plain")
    payload = {"project_id": pid, "key": key, "filename": "source.txt", "size": len(source)}
    first = client.post("/internal/uploads/complete", json=payload)
    assert first.status_code == 200, first.text
    original = first.json()["document"]
    assert first.json()["duplicate"] is False
    replay = client.post("/internal/uploads/complete", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["duplicate"] is True
    assert replay.json()["document"]["id"] == original["id"]
    assert store.get_bytes(key) == source
    assert client.get(f"/api/documents/{original['id']}/source").content == source
    # A distinct redundant upload can be removed without deleting the source
    # key owned by the deduplicated document.
    redundant = "uploads/redundant-session/source.txt"
    store.put_bytes(redundant, source)
    duplicate = client.post("/internal/uploads/complete", json={**payload, "key": redundant})
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
    with pytest.raises(FileNotFoundError):
        store.get_bytes(redundant)
    assert store.get_bytes(key) == source


def test_author_and_public_text_filters_match_dashboard_and_report_sources(client):
    pid = project(client, "Two authors and a public search")
    first = upload(client, pid, "The community should protect equal rights.", "first.txt")
    second = upload(client, pid, "Freedom and security should be balanced because evidence matters.", "second.txt")
    client.patch(f"/api/documents/{first['id']}", json={"author": "Author Alice", "title": "The Nightingale inquiry"})
    client.patch(f"/api/documents/{second['id']}", json={"author": "Author Bob", "title": "The Harbor inquiry"})
    run = start(client, pid, lens_ids=["discourse"])
    finish(client, run["id"])
    all_values = all_findings(client, pid)
    assert {f["author"] for f in all_values} == {"Author Alice", "Author Bob"}
    alice = all_findings(client, pid, author="Author Alice")
    bob = all_findings(client, pid, author="Author Bob")
    assert alice and bob and len(alice)+len(bob) == len(all_values)
    assert all(f["document_id"] == first["id"] for f in alice)
    assert all(f["document_id"] == second["id"] for f in bob)
    # The public document title is searchable even when it is not a finding's
    # own title. Search stays case-insensitive, matching the interface.
    assert {f["id"] for f in all_findings(client, pid, q="NIGHTINGALE")} == {f["id"] for f in alice}
    assert all_findings(client, pid, author="Author Bob", q="Nightingale") == []
    dashboard = client.get(f"/api/projects/{pid}/dashboard", params={"author": "Author Alice", "q": "Nightingale"}).json()
    assert dashboard["documents"] == 1 and dashboard["total_words"] == first["word_count"]
    assert dashboard["findings"] == len(alice)
    assert dashboard["coverage_detail"]["documents_total"] == 1
    assert [a["name"] for a in dashboard["by_author"]] == ["Author Alice"]
    exported = client.post(f"/api/projects/{pid}/reports", json={"format": "jsonl", "profile": "briefing", "filters": {"author": "Author Alice", "q": "Nightingale"}})
    assert exported.status_code == 200, exported.text
    records = [json.loads(line) for line in exported.text.splitlines()]
    docs = [r["data"] for r in records if r["record_type"] == "document"]
    exported_findings = [r["data"] for r in records if r["record_type"] == "finding"]
    assert len(docs) == 1 and docs[0]["id"] == first["id"]
    assert {f["id"] for f in exported_findings} == {f["id"] for f in alice}
    manifest = next(r["data"] for r in records if r["record_type"] == "manifest")
    assert manifest["metrics"]["documents"] == 1 and manifest["metrics"]["total_words"] == first["word_count"]
    # Internal payload extensions are not implicitly exposed as search fields.
    item = alice[0]
    stored = client.app.state.store.one("SELECT payload_json FROM findings WHERE id=?", [item["id"]])
    payload = json.loads(stored["payload_json"])
    payload["foo"] = "private-search-canary"
    client.app.state.store.execute("UPDATE findings SET payload_json=? WHERE id=?", [json.dumps(payload), item["id"]])
    assert all_findings(client, pid, q="private-search-canary") == []


def test_model_digest_change_after_creation_pauses_before_any_inference(client, monkeypatch):
    class ChangingLocalProvider(BudgetProvider):
        def __init__(self):
            super().__init__()
            self.profile = {**self.profile, "provider": "ollama", "mode": "local", "model": "local-revision-test", "revision": "a"*64, "quantization": "Q4_K_M"}
    provider = ChangingLocalProvider()
    monkeypatch.setattr("doxa.providers.get_provider", lambda _: provider)
    pid = project(client)
    upload(client, pid)
    run = start(client, pid, provider="ollama", model="local-revision-test", lens_ids=["ihra"])
    assert run["config"]["model_revision"] == "a"*64
    provider.profile["revision"] = "b"*64
    result = work(client, run["id"])
    assert result["run"]["status"] == "paused_model_changed"
    assert provider.calls == 0
    assert all_findings(client, pid) == []
    assert result["run"]["spent_usd"] == 0 and result["run"]["reserved_usd"] == 0
