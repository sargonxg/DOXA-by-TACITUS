"""Operational local deadlines change leases, not frozen source interpretation."""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app, task_execution_limits


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_LOCAL_TASK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


class DeadlineProvider:
    def __init__(self, store, *, local=True, block=False):
        self.store = store
        self.profile = {"provider": "synthetic", "model": "deadline-fixture", "revision": "fixed-test-revision",
                        "mode": "local" if local else "remote", "context_limit": 8192 if local else 32768,
                        "source_pack_bytes": 3600 if local else 12000}
        self.calls = []
        self.block = block
        self.started = threading.Event()
        self.release = asyncio.Event()

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_args):
        return 0

    async def generate(self, messages, schema, **_options):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        self.lease = self.store.one("SELECT lease_until FROM tasks WHERE document_id=? AND status='running'", [payload["document"]["id"]])["lease_until"]
        self.observed_at = time.time()
        self.started.set()
        if self.block:
            await self.release.wait()
        content = {"decisions": [{"unit_id": unit["unit_id"], "results": [0] * len(payload["criterion_order"])}
                                 for unit in payload["units"]]}
        if payload["phase"] == "details":
            content["details"] = []
        return {"content": content, "finish_reason": "stop", "usage": {
            "input_tokens": 10, "output_tokens": 10, "cost_usd": 0, "requests": 1, "estimated": False}}


def prepare(client, monkeypatch, *, provider="ollama", block=False):
    project = client.post("/api/projects", json={"name": "Synthetic operational timing fixture"}).json()["id"]
    response = client.post(f"/api/projects/{project}/upload", files=[
        ("files", ("source.txt", b"A short synthetic source checks lease timing and explicit outcome persistence.", "text/plain")),
    ])
    assert response.status_code == 200, response.text
    instance = DeadlineProvider(client.app.state.store, local=provider == "ollama", block=block)
    monkeypatch.setattr("doxa.providers.get_provider", lambda _config: instance)
    response = client.post(f"/api/v3/projects/{project}/runs", json={
        "provider": provider, "model": "deadline-fixture", "lens_ids": ["ihra"], "allow_remote_text": True,
    })
    assert response.status_code == 201, response.text
    return response.json(), instance


def capture_deadline(monkeypatch):
    deadlines = []
    async def immediate(awaitable, timeout):
        deadlines.append(timeout)
        return await awaitable
    monkeypatch.setattr("doxa.api.asyncio.wait_for", immediate)
    return deadlines


@pytest.mark.parametrize("seconds", [480, 1800, 7200])
def test_local_override_is_bounded_and_lease_covers_deadline_plus_commit_margin(monkeypatch, seconds):
    monkeypatch.setenv("DOXA_LOCAL_TASK_TIMEOUT_SECONDS", str(seconds))
    limits = task_execution_limits("ollama")
    assert limits["task_timeout_seconds"] == seconds
    assert limits["task_lease_seconds"] >= seconds + 120
    assert limits["task_timeout_source"] == "server_environment"


@pytest.mark.parametrize("value", ["479", "7201", "invalid", "1.5", "", "nan"])
def test_invalid_local_setting_is_rejected_without_affecting_remote_limits(monkeypatch, value):
    monkeypatch.setenv("DOXA_LOCAL_TASK_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="integer between 480 and 7200"):
        task_execution_limits("ollama")
    assert task_execution_limits("cloudflare")["task_timeout_seconds"] == 480
    assert task_execution_limits("ollama", cloud_state=True)["task_timeout_seconds"] == 420


def test_invalid_local_setting_rejects_run_before_provider_creation(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "Invalid operational setting"}).json()["id"]
    monkeypatch.setenv("DOXA_LOCAL_TASK_TIMEOUT_SECONDS", "7201")
    def no_provider(_config):
        pytest.fail("Invalid operational bounds must be rejected before provider creation")
    monkeypatch.setattr("doxa.providers.get_provider", no_provider)
    response = client.post(f"/api/v3/projects/{project}/runs", json={"provider": "ollama", "model": "fixture", "lens_ids": ["ihra"]})
    assert response.status_code == 422
    assert "between 480 and 7200" in response.json()["detail"]
    assert client.app.state.store.one("SELECT COUNT(*) n FROM runs WHERE project_id=?", [project])["n"] == 0


def test_existing_local_run_without_timing_fields_uses_1800_and_longer_lease(client, monkeypatch):
    run, provider = prepare(client, monkeypatch)
    config = dict(run["config"])
    for key in ("task_timeout_seconds", "task_lease_seconds", "task_timeout_source", "task_timing_history"):
        config.pop(key, None)
    client.app.state.store.execute("UPDATE runs SET config_json=? WHERE id=?", [json.dumps(config), run["id"]])
    deadlines = capture_deadline(monkeypatch)
    result = client.portal.call(client.app.state.work_once, run["id"])["run"]
    assert result["status"] == "completed", result["errors"]
    assert deadlines == [1800]
    assert 1910 < provider.lease - provider.observed_at <= 1920
    assert result["config"]["task_timeout_seconds"] == result["model_profile"]["task_timeout_seconds"] == 1800
    assert result["model_profile"]["task_lease_seconds"] == 1920
    assert result["config"]["task_timing_history"][-1]["reason"] == "worker_claim"


def test_operational_override_records_change_without_changing_method_fields(client, monkeypatch):
    run, provider = prepare(client, monkeypatch)
    assert run["config"]["task_timeout_seconds"] == 1800
    monkeypatch.setenv("DOXA_LOCAL_TASK_TIMEOUT_SECONDS", "2400")
    deadlines = capture_deadline(monkeypatch)
    result = client.portal.call(client.app.state.work_once, run["id"])["run"]
    assert result["status"] == "completed"
    assert deadlines == [2400]
    assert 2510 < provider.lease - provider.observed_at <= 2520
    assert [item["task_timeout_seconds"] for item in result["config"]["task_timing_history"]] == [1800, 2400]
    for key in ("pipeline_version", "unit_builder_version", "unit_max_chars", "lens_snapshots_sha256", "source_snapshot", "question", "persona"):
        assert result["config"][key] == run["config"][key]


def test_cloud_inference_retains_480_second_deadline_and_600_second_lease(client, monkeypatch):
    run, provider = prepare(client, monkeypatch, provider="cloudflare")
    monkeypatch.setenv("DOXA_LOCAL_TASK_TIMEOUT_SECONDS", "7200")
    deadlines = capture_deadline(monkeypatch)
    result = client.portal.call(client.app.state.work_once, run["id"])["run"]
    assert result["status"] == "completed"
    assert deadlines == [480]
    assert 590 < provider.lease - provider.observed_at <= 600
    assert result["model_profile"]["task_timeout_seconds"] == 480
    assert result["model_profile"]["task_lease_seconds"] == 600


def test_local_cancellation_invalidates_long_lease_and_blocks_late_results(client, monkeypatch):
    run, provider = prepare(client, monkeypatch, block=True)
    deadlines = capture_deadline(monkeypatch)
    future = client.portal.start_task_soon(client.app.state.work_once, run["id"])
    assert provider.started.wait(3)
    assert provider.lease - time.time() > 1800
    cancelled = client.post(f"/api/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    client.portal.call(provider.release.set)
    result = future.result(timeout=5)["run"]
    assert result["status"] == "cancelled" and deadlines == [1800]
    store = client.app.state.store
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [run["id"]])["n"] == 0
    assert store.one("SELECT lease_token,lease_until FROM tasks WHERE run_id=?", [run["id"]]) == {"lease_token": None, "lease_until": None}
    assert store.one("SELECT COUNT(*) n FROM budget_reservations WHERE run_id=? AND status='reserved'", [run["id"]])["n"] == 0


@pytest.mark.parametrize("before_cancel", ["pending", "failed"])
def test_cancelled_local_run_resume_refreshes_progress_before_any_long_task_starts(client, monkeypatch, before_cancel):
    run, provider = prepare(client, monkeypatch)
    store = client.app.state.store
    if before_cancel == "failed":
        store.execute("UPDATE tasks SET status='failed',error='Synthetic previous timeout' WHERE run_id=?", [run["id"]])
    cancelled = client.post(f"/api/runs/{run['id']}/cancel").json()
    assert cancelled["progress"]["documents_settled"] == 1
    assert cancelled["progress"]["documents_complete"] == 0
    assert cancelled["progress"]["tasks_failed"] == int(before_cancel == "failed")

    response = client.post(f"/api/runs/{run['id']}/resume", json={})
    assert response.status_code == 200, response.text
    resumed = response.json()
    assert resumed["status"] == "queued"
    assert resumed["progress"]["documents_complete"] == 0
    assert resumed["progress"]["documents_settled"] == resumed["progress"]["documents_done"] == 0
    assert resumed["progress"]["tasks_failed"] == 0
    assert resumed["progress"]["complete"] is False
    assert provider.calls == [], "Counters must refresh before the next 1,800-second unit starts"
    assert store.one("SELECT status,error FROM tasks WHERE run_id=?", [run["id"]]) == {"status": "pending", "error": None}
    assert client.get(f"/api/runs/{run['id']}").json()["progress"] == resumed["progress"]
