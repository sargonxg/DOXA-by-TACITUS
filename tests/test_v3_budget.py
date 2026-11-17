"""V3 job-budget regressions using deterministic, transport-free inference.

Unknown provider billing is preserved as unknown while its conservative reserve
is charged. That accounting state must not masquerade as an exhausted budget.
"""
import json

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.providers import ProviderError


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN", "DOXA_REVIEWER_ID"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as value:
        yield value


class TransientUnknownBillingProvider:
    """The first response is lost; a later explicit outcome is source-complete."""

    name = "synthetic-budget-fixture"
    profile = {"provider": name, "model": "v3-budget-test", "mode": "remote",
               "revision": "fixed-test-revision", "context_limit": 32768, "source_pack_bytes": 12000}
    reservation = 0.10
    known_charge = 0.04

    def __init__(self):
        self.calls = []

    async def health(self):
        return {"ok": True, **self.profile}

    def estimate_cost(self, *_args):
        return self.reservation

    async def generate(self, messages, schema, **_options):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        if len(self.calls) == 1:
            # A real remote timeout may be billed despite having no usage body.
            raise ProviderError("timeout", "Synthetic response loss; invoice usage is unknown")
        rows = [{"unit_id": unit["unit_id"], "results": [0] * len(payload["criterion_order"])}
                for unit in payload["units"]]
        content = {"decisions": rows}
        if payload["phase"] == "details":
            content["details"] = []
        return {"content": content, "finish_reason": "stop", "usage": {
            "input_tokens": 100, "output_tokens": 30, "cost_usd": self.known_charge,
            "requests": 1, "estimated": False,
        }}


def start(client, monkeypatch, provider, *, run_budget=1, monthly_budget=1):
    monkeypatch.setattr("doxa.providers.get_provider", lambda _config: provider)
    response = client.post("/api/projects", json={"name": "Synthetic v3 billing regression"})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    response = client.post(f"/api/projects/{project_id}/upload", files=[
        ("files", ("source.txt", b"A synthetic transport fixture describes equal treatment. No semantic claim is tested.", "text/plain")),
    ])
    assert response.status_code == 200 and response.json()[0]["status"] == "ready", response.text
    response = client.put("/api/settings", json={"monthly_budget_usd": monthly_budget})
    assert response.status_code == 200, response.text
    response = client.post(f"/api/v3/projects/{project_id}/runs", json={
        "provider": "openai", "model": "v3-budget-test", "lens_ids": ["ihra"],
        "allow_remote_text": True, "budget_usd": run_budget,
    })
    assert response.status_code == 201, response.text
    run = response.json()
    assert str(run["config"]["methodology_version"]) == "3.0"
    return run


def finish(client, run_id):
    for _step in range(5):
        response = client.portal.call(client.app.state.work_once, run_id)
        if not response["pending"]:
            return client.get(f"/api/runs/{run_id}").json()
    pytest.fail("A one-unit synthetic run exceeded five bounded work steps")


def reservations(client, run_id):
    return client.app.state.store.query(
        "SELECT * FROM budget_reservations WHERE run_id=? ORDER BY created_at,id", [run_id])


def assert_unknown_reserve_charged_once(rows, provider):
    unknown = [row for row in rows if json.loads(row["usage_json"]).get("cost_usd") is None]
    assert len(unknown) == 1
    assert unknown[0]["status"] == "settled"
    assert unknown[0]["amount"] == pytest.approx(provider.reservation)
    assert unknown[0]["charged_usd"] == pytest.approx(provider.reservation)
    assert all(row["status"] == "settled" for row in rows)


def test_unknown_response_usage_then_retry_completes_without_false_budget_pause(client, monkeypatch):
    provider = TransientUnknownBillingProvider()
    run = start(client, monkeypatch, provider)
    completed = finish(client, run["id"])

    assert completed["status"] == "completed", completed.get("errors")
    assert completed["progress"]["complete"] is True
    assert completed["config"]["budget_usd"] == 1
    assert completed["usage"]["cost_usd"] is None, "Do not invent the missing provider invoice usage"
    assert completed["usage"]["requests"] == len(provider.calls) == 2
    assert [call["phase"] for call in provider.calls] == ["classify", "details"]
    assert completed["spent_usd"] == pytest.approx(provider.reservation + provider.known_charge)
    assert completed["reserved_usd"] == pytest.approx(0)
    rows = reservations(client, run["id"])
    assert len(rows) == 2
    assert_unknown_reserve_charged_once(rows, provider)
    store = client.app.state.store
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [run["id"]])["n"] == 3
    assert store.one("SELECT status FROM v3_unit_statuses WHERE run_id=?", [run["id"]])["status"] == "accepted"
    ledger = client.get("/api/settings").json()["budget"]
    assert ledger["spent_usd"] == pytest.approx(0.14)
    assert ledger["reserved_usd"] == pytest.approx(0)

    # Reading/stepping a terminal run cannot re-bill the uncertain response.
    assert finish(client, run["id"])["status"] == "completed"
    assert len(provider.calls) == 2 and reservations(client, run["id"]) == rows
    assert client.get("/api/settings").json()["budget"] == ledger


@pytest.mark.parametrize("limiting_budget", ["run", "monthly"])
def test_real_cap_exhaustion_still_pauses_after_unknown_usage_and_can_resume(client, monkeypatch, limiting_budget):
    provider = TransientUnknownBillingProvider()
    run = start(client, monkeypatch, provider,
                run_budget=0.15 if limiting_budget == "run" else 1,
                monthly_budget=0.15 if limiting_budget == "monthly" else 1)
    paused = finish(client, run["id"])

    assert paused["status"] == "paused_budget"
    assert paused["progress"]["complete"] is False
    assert len(provider.calls) == 1, "The retry cannot call inference before securing its full reserve"
    assert paused["spent_usd"] == pytest.approx(provider.reservation)
    assert paused["reserved_usd"] == pytest.approx(0)
    assert paused["usage"]["cost_usd"] is None
    rows = reservations(client, run["id"])
    assert len(rows) == 1
    assert_unknown_reserve_charged_once(rows, provider)
    task = client.app.state.store.one("SELECT error FROM tasks WHERE run_id=?", [run["id"]])
    assert any(item["code"] == "budget_exhausted" for item in json.loads(task["error"]))
    assert client.app.state.store.one("SELECT COUNT(*) n FROM v3_assessments WHERE run_id=?", [run["id"]])["n"] == 0
    ledger = client.get("/api/settings").json()["budget"]
    assert ledger["spent_usd"] + ledger["reserved_usd"] <= 0.15

    if limiting_budget == "monthly":
        assert client.put("/api/settings", json={"monthly_budget_usd": 1}).status_code == 200
    response = client.post(f"/api/runs/{run['id']}/resume",
                           json={"budget_usd": 1} if limiting_budget == "run" else {})
    assert response.status_code == 200, response.text
    completed = finish(client, run["id"])
    assert completed["status"] == "completed", completed.get("errors")
    assert len(provider.calls) == 2
    assert completed["usage"]["cost_usd"] is None
    assert completed["spent_usd"] == pytest.approx(provider.reservation + provider.known_charge)
    assert completed["reserved_usd"] == pytest.approx(0)
    assert len(reservations(client, run["id"])) == 2
    assert_unknown_reserve_charged_once(reservations(client, run["id"]), provider)
