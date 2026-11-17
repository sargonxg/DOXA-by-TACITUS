"""Verification request validation must finish before provider/run creation."""
import math

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from doxa.store import Store, utcnow
from doxa.v3_api import VerificationInput, register


INVALID_CONSENT = ["false", "true", 0, 1, None, [], {}]
INVALID_BUDGETS = [None, "not-a-budget", "NaN", "Infinity", "-Infinity", 0, -0.1, 2.000001, [], {}, True, False]


@pytest.mark.parametrize("consent", INVALID_CONSENT)
def test_verification_schema_requires_real_boolean_consent(consent):
    with pytest.raises(ValidationError):
        VerificationInput.model_validate({"run_id": "primary", "allow_remote_text": consent})


@pytest.mark.parametrize("budget", [*INVALID_BUDGETS, float("nan"), float("inf"), float("-inf")])
def test_verification_schema_rejects_malformed_nonfinite_and_out_of_range_budgets(budget):
    with pytest.raises(ValidationError):
        VerificationInput.model_validate({"run_id": "primary", "allow_remote_text": True, "budget_usd": budget})


@pytest.mark.parametrize("run_id", ["", "x" * 129, None, 1, [], {}])
def test_verification_schema_requires_bounded_run_identifier(run_id):
    with pytest.raises(ValidationError):
        VerificationInput.model_validate({"run_id": run_id})


def test_verification_schema_defaults_do_not_grant_remote_consent():
    value = VerificationInput.model_validate({"run_id": "primary"})
    assert value.allow_remote_text is False
    assert value.budget_usd == 2 and math.isfinite(value.budget_usd)


@pytest.fixture
def route(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path / "verification-fixture")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Synthetic validation fixture',?)", [utcnow()])
    entered, project_calls = [], []
    async def create_run(project_id, body):
        entered.append((project_id, body))
        return {"id": "validation-only-no-inference"}
    def require_project(identity):
        project_calls.append(identity)
        return store.one("SELECT * FROM projects WHERE id=?", [identity])
    def forbidden_provider(*_args, **_kwargs):
        pytest.fail("Request validation fixtures must never create a provider")
    monkeypatch.setattr("doxa.providers.get_provider", forbidden_provider)
    app = FastAPI()
    register(app, {"db": lambda: store, "root": tmp_path, "require_project": require_project, "create_run": create_run})
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, entered, project_calls
    finally:
        store.close()


@pytest.mark.parametrize("consent", INVALID_CONSENT)
def test_http_rejects_coerced_consent_before_entering_run_or_provider_code(route, consent):
    client, entered, project_calls = route
    response = client.post("/api/v3/projects/p/verification", json={"run_id": "primary", "allow_remote_text": consent, "budget_usd": 1})
    assert response.status_code == 422, response.text
    assert entered == project_calls == []


@pytest.mark.parametrize("budget", INVALID_BUDGETS)
def test_http_rejects_bad_budget_before_entering_run_or_provider_code(route, budget):
    client, entered, project_calls = route
    response = client.post("/api/v3/projects/p/verification", json={"run_id": "primary", "allow_remote_text": True, "budget_usd": budget})
    assert response.status_code == 422, response.text
    assert entered == project_calls == []


def test_http_rejects_finite_json_number_that_overflows_runtime_float(route):
    client, entered, project_calls = route
    response = client.post("/api/v3/projects/p/verification", content='{"run_id":"primary","allow_remote_text":true,"budget_usd":1e999}',
                           headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert entered == project_calls == []


@pytest.mark.parametrize("consent,budget", [(True, 0.01), (True, 1), (True, 2), (False, 0.5)])
def test_http_forwards_only_validated_real_boolean_and_finite_capped_budget(route, consent, budget):
    client, entered, project_calls = route
    response = client.post("/api/v3/projects/p/verification", json={"run_id": "primary", "allow_remote_text": consent, "budget_usd": budget})
    assert response.status_code == 201, response.text
    assert project_calls == ["p"] and len(entered) == 1
    project, body = entered[0]
    assert project == "p" and body.verification_of == "primary"
    assert body.allow_remote_text is consent
    assert math.isfinite(body.budget_usd) and 0 < body.budget_usd <= 2 and body.budget_usd == budget
    assert body.methodology_version == "3.0"
    assert body.provider == "cloudflare" and body.model == "@cf/openai/gpt-oss-120b"
    assert body.reasoning_effort == "low" and body.reasoning_token_allowance == 2048


def test_http_omitted_remote_consent_is_forwarded_as_false(route):
    client, entered, _ = route
    response = client.post("/api/v3/projects/p/verification", json={"run_id": "primary"})
    assert response.status_code == 201, response.text
    assert len(entered) == 1
    assert entered[0][1].allow_remote_text is False
    assert entered[0][1].budget_usd == 2


@pytest.mark.parametrize("body", [{}, {"run_id": ""}, {"run_id": "x" * 129}, {"run_id": None}])
def test_http_requires_primary_run_identifier_before_run_creation(route, body):
    client, entered, project_calls = route
    response = client.post("/api/v3/projects/p/verification", json=body)
    assert response.status_code == 422, response.text
    assert entered == project_calls == []
