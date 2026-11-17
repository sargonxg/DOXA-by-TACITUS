"""Completion must reflect task state and explicit source-unit coverage."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa import lenses


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


class CompletionProvider:
    name = "completion-fixture"
    profile = {"provider": name, "model": "completion-test", "mode": "remote", "revision": "fixed-fixture",
               "context_limit": 32768, "source_pack_bytes": 12000}

    def __init__(self, *, timeout=False):
        self.timeout = timeout
        self.calls = []

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_args):
        return 0.01

    async def generate(self, messages, schema, **_options):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        if self.timeout:
            raise TimeoutError()
        content = {"decisions": [{"unit_id": unit["unit_id"], "results": [0] * len(payload["criterion_order"])}
                                 for unit in payload["units"]]}
        if payload["phase"] == "details":
            content["details"] = []
        return {"content": content, "finish_reason": "stop", "usage": {
            "input_tokens": 10, "output_tokens": 10, "cost_usd": 0.001, "estimated": False,
        }}


def start(client, monkeypatch, provider):
    monkeypatch.setattr("doxa.providers.get_provider", lambda _config: provider)
    response = client.post("/api/projects", json={"name": "Synthetic completion regression"})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    response = client.post(f"/api/projects/{project_id}/upload", files=[
        ("files", ("source.txt", b"A synthetic source tests scheduler completion and immutable methods.", "text/plain")),
    ])
    assert response.status_code == 200 and response.json()[0]["status"] == "ready", response.text
    response = client.post(f"/api/v3/projects/{project_id}/runs", json={
        "provider": "openai", "model": "completion-test", "lens_ids": ["ihra"],
        "allow_remote_text": True, "budget_usd": 1,
    })
    assert response.status_code == 201, response.text
    return response.json()


def step(client, run_id):
    return client.portal.call(client.app.state.work_once, run_id)["run"]


def seed_settled_task(client, run_id, *, task_status="completed", error=None, processed=1, unit_status="accepted"):
    store = client.app.state.store
    assert store.one("SELECT COUNT(*) n FROM tasks WHERE run_id=?", [run_id])["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM v3_unit_statuses WHERE run_id=?", [run_id])["n"] == 1
    store.execute("UPDATE tasks SET status=?,error=?,coverage_json=? WHERE run_id=?", [
        task_status, error, json.dumps({"complete": True, "total_blocks": 1, "processed_blocks": processed}), run_id,
    ])
    if unit_status is None:
        store.execute("DELETE FROM v3_unit_statuses WHERE run_id=?", [run_id])
    else:
        store.execute("UPDATE v3_unit_statuses SET status=? WHERE run_id=?", [unit_status, run_id])
    store.execute("UPDATE runs SET status='running' WHERE id=?", [run_id])


def test_empty_message_exception_is_persisted_and_never_completes_run(client, monkeypatch):
    provider = CompletionProvider(timeout=True)
    run = start(client, monkeypatch, provider)
    result = step(client, run["id"])

    assert result["status"] == "partial"
    assert result["progress"]["complete"] is False
    assert result["progress"]["tasks_failed"] == 1
    assert result["progress"]["documents_complete"] == 0
    assert result["progress"]["documents_settled"] == 1
    task = client.app.state.store.one("SELECT status,error FROM tasks WHERE run_id=?", [run["id"]])
    assert task == {"status": "failed", "error": "TimeoutError"}
    assert result["errors"][0]["message"] == "TimeoutError"
    assert len(provider.calls) == 1


@pytest.mark.parametrize("stored_error", [None, "", "   "])
def test_historical_failed_task_with_blank_error_is_partial_even_with_full_coverage(client, monkeypatch, stored_error):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    # Old checkpoints can already contain a failed task with a blank message.
    # Even a stale full-coverage counter must not turn it into a completed run.
    seed_settled_task(client, run["id"], task_status="failed", error=stored_error)
    result = step(client, run["id"])

    assert result["status"] == "partial"
    assert result["progress"]["complete"] is False
    assert result["progress"]["tasks_failed"] == 1
    assert result["errors"] and result["errors"][0]["message"].strip()
    assert provider.calls == []


@pytest.mark.parametrize("unit_status", ["pending", "partial", "failed", None])
def test_completed_task_with_unaccepted_or_missing_unit_cannot_complete_run(client, monkeypatch, unit_status):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    seed_settled_task(client, run["id"], unit_status=unit_status)
    result = step(client, run["id"])

    assert result["status"] == "partial"
    assert result["progress"]["complete"] is False
    assert result["progress"]["tasks_failed"] == 0
    assert provider.calls == []


def test_accepted_units_with_incomplete_task_coverage_cannot_complete_run(client, monkeypatch):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    seed_settled_task(client, run["id"], processed=0)
    result = step(client, run["id"])
    assert result["status"] == "partial"
    assert result["progress"]["complete"] is False
    assert result["progress"]["blocks_done"] == 0
    assert provider.calls == []


def test_genuinely_completed_tasks_and_units_still_complete(client, monkeypatch):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    result = step(client, run["id"])
    assert result["status"] == "completed", result["errors"]
    assert result["progress"]["complete"] is True
    assert result["progress"]["tasks_done"] == 1 and result["progress"]["tasks_failed"] == 0
    assert result["progress"]["documents_complete"] == 1
    assert len(provider.calls) == 1


def test_changed_selected_registry_pack_cannot_silently_replace_frozen_run_method(client, monkeypatch):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    frozen = deepcopy(run["config"]["lens_snapshots"])
    registry = deepcopy(lenses.LENSES)
    selected = next(pack for pack in registry if pack["id"] == "ihra")
    selected["criteria"][0]["description"] += " Synthetic changed interpretation."
    monkeypatch.setattr(lenses, "LENSES", registry)

    result = step(client, run["id"])
    assert result["status"] == "paused_method_changed"
    assert result["progress"]["complete"] is False
    assert result["config"]["lens_snapshots"] == frozen
    assert provider.calls == []
    task = client.app.state.store.one("SELECT status,lease_token,lease_until FROM tasks WHERE run_id=?", [run["id"]])
    assert task == {"status": "pending", "lease_token": None, "lease_until": None}
    assert client.app.state.store.one("SELECT COUNT(*) n FROM budget_reservations WHERE run_id=?", [run["id"]])["n"] == 0
    assert client.app.state.store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [run["id"]])["n"] == 0


def test_unselected_registry_extension_preserves_frozen_run_and_can_complete(client, monkeypatch):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    frozen = deepcopy(run["config"]["lens_snapshots"])
    extension = deepcopy(lenses.get_lens("discourse"))
    extension["id"] = "synthetic-unselected-extension"
    monkeypatch.setattr(lenses, "LENSES", [*lenses.LENSES, extension])

    result = step(client, run["id"])
    assert result["status"] == "completed", result["errors"]
    assert result["progress"]["complete"] is True
    assert result["config"]["lens_snapshots"] == frozen
    assert len(provider.calls) == 1
    observed = provider.calls[0]["criteria"]
    assert [criterion["description"] for criterion in observed] == [criterion["description"] for criterion in frozen[0]["criteria"]]


def test_tampered_snapshot_hash_pauses_before_inference(client, monkeypatch):
    provider = CompletionProvider()
    run = start(client, monkeypatch, provider)
    config = deepcopy(run["config"])
    config["lens_snapshots_sha256"] = "a-different-frozen-pack-checksum"
    client.app.state.store.execute("UPDATE runs SET config_json=? WHERE id=?", [json.dumps(config), run["id"]])
    result = step(client, run["id"])
    assert result["status"] == "paused_method_changed"
    assert provider.calls == []
