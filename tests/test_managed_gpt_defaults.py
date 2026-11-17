"""New managed GPT runs receive tested defaults before provider preflight."""
import json

import pytest
from fastapi.testclient import TestClient

from doxa.api import create_app
from doxa.store import utcnow


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name in ("DOXA_CLOUD_STATE_URL", "DOXA_INTERNAL_TOKEN", "DOXA_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    preflights = []
    def get_provider(config):
        class Provider:
            async def health(self):
                preflights.append(dict(config))
                return {"ok": True, "provider": config["provider"], "model": config["model"], "revision": "unresolved"}

            async def generate(self, *_args, **_kwargs):
                pytest.fail("Default-profile tests must not call inference")
        return Provider()
    monkeypatch.setattr("doxa.providers.get_provider", get_provider)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as client:
        response = client.post("/api/projects", json={"name": "Synthetic managed profile fixture"})
        assert response.status_code == 201, response.text
        project = response.json()["id"]
        response = client.post(f"/api/projects/{project}/upload", files=[
            ("files", ("source.txt", b"Synthetic source for new-run configuration, with no model-quality claim.", "text/plain")),
        ])
        assert response.status_code == 200, response.text
        yield client, project, preflights


def create(workspace, provider, model, **choices):
    client, project, preflights = workspace
    response = client.post(f"/api/v3/projects/{project}/runs", json={
        "provider": provider, "model": model, "lens_ids": ["ihra"], "allow_remote_text": True, **choices,
    })
    assert response.status_code == 201, response.text
    assert len(preflights) == 1
    run = response.json()
    assert run["status"] == "queued"
    assert client.get(f"/api/runs/{run['id']}").json()["config"] == run["config"]
    return run["config"], preflights[0]


@pytest.mark.parametrize("model", ["@cf/openai/gpt-oss-120b", "@cf/openai/gpt-oss-20b"])
def test_new_managed_gpt_primary_uses_low_and_2048_before_preflight_without_rewriting_history(workspace, model):
    client, project, _preflights = workspace
    old = json.dumps({"provider": "cloudflare", "model": model, "reasoning_token_allowance": 0})
    client.app.state.store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES(?,?,'partial',?,?)",
                                   ["historical-default-profile", project, old, utcnow()])
    config, health_config = create(workspace, "cloudflare", model)
    for value in (config, health_config):
        assert value["reasoning_effort"] == "low"
        assert value["reasoning_token_allowance"] == 2048
    assert client.app.state.store.one("SELECT config_json FROM runs WHERE id='historical-default-profile'")["config_json"] == old


@pytest.mark.parametrize("provider,model", [
    ("cloudflare", "@cf/google/gemma-4-26b-a4b-it"),
    ("openai", "@cf/openai/gpt-oss-120b"),
    ("cloudflare", "custom-gpt-oss-compatible-model"),
])
def test_gemma_and_generic_compatible_choices_keep_their_existing_defaults(workspace, provider, model):
    config, health_config = create(workspace, provider, model)
    for value in (config, health_config):
        assert value.get("reasoning_effort") is None
        assert value["reasoning_token_allowance"] == 0


@pytest.mark.parametrize("choices,effort,allowance", [
    ({"reasoning_effort": "medium", "reasoning_token_allowance": 4096}, "medium", 4096),
    ({"reasoning_effort": "medium"}, "medium", 2048),
    ({"reasoning_token_allowance": 4096}, "low", 4096),
    ({"reasoning_effort": None, "reasoning_token_allowance": 0}, None, 0),
])
def test_explicit_valid_reasoning_choices_are_preserved_field_by_field(workspace, choices, effort, allowance):
    config, health_config = create(workspace, "cloudflare", "@cf/openai/gpt-oss-120b", **choices)
    for value in (config, health_config):
        assert value.get("reasoning_effort") == effort
        assert value["reasoning_token_allowance"] == allowance
