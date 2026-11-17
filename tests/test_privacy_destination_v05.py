"""Transport policy must inspect the exact endpoint selected by the adapter.

No health checks, model requests, sockets or credentials are needed here.
"""
import pytest

from doxa.providers import (
    CloudflareProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    RunpodProvider,
    configured_endpoint,
)


ENVIRONMENT = (
    "DOXA_OLLAMA_BASE_URL", "OLLAMA_BASE_URL", "DOXA_OLLAMA_NUM_THREADS",
    "DOXA_OPENAI_BASE_URL", "OPENAI_BASE_URL", "DOXA_OPENAI_MODE",
    "DOXA_CLOUDFLARE_BASE_URL", "DOXA_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID",
    "DOXA_RUNPOD_BASE_URL", "RUNPOD_BASE_URL", "DOXA_RUNPOD_ENDPOINT_ID", "RUNPOD_ENDPOINT_ID",
    "DOXA_ACCESS_MODE", "DOXA_ACCESS_CONFIG_FILE", "DOXA_CLOUD_STATE_URL",
)


@pytest.fixture(autouse=True)
def clean_operator_settings(monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("provider,constructor,expected", [
    ("ollama", OllamaProvider, "http://127.0.0.1:11434"),
    ("openai", OpenAICompatibleProvider, "https://api.openai.com/v1"),
    ("openai-compatible", OpenAICompatibleProvider, "https://api.openai.com/v1"),
    ("vllm", OpenAICompatibleProvider, "https://api.openai.com/v1"),
    ("cloudflare", CloudflareProvider, "https://api.cloudflare.com/client/v4/accounts/unconfigured/ai/v1"),
    ("runpod", RunpodProvider, "https://api.runpod.ai/unconfigured/openai/v1"),
])
def test_default_resolution_matches_real_adapter_constructor(provider, constructor, expected):
    config = {"provider": provider}
    assert configured_endpoint(config) == expected == constructor(config).base_url


@pytest.mark.parametrize("provider,constructor,primary,legacy", [
    ("ollama", OllamaProvider, "DOXA_OLLAMA_BASE_URL", "OLLAMA_BASE_URL"),
    ("openai", OpenAICompatibleProvider, "DOXA_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    ("runpod", RunpodProvider, "DOXA_RUNPOD_BASE_URL", "RUNPOD_BASE_URL"),
])
def test_config_primary_and_legacy_precedence_exactly_match_adapters(monkeypatch, provider, constructor, primary, legacy):
    config = {"provider": provider}
    monkeypatch.setenv(legacy, "https://legacy.example.invalid/inference/")
    assert configured_endpoint(config) == constructor(config).base_url == "https://legacy.example.invalid/inference"
    monkeypatch.setenv(primary, "https://primary.example.invalid/inference/")
    assert configured_endpoint(config) == constructor(config).base_url == "https://primary.example.invalid/inference"
    config["base_url"] = "https://explicit.example.invalid/inference/"
    assert configured_endpoint(config) == constructor(config).base_url == "https://explicit.example.invalid/inference"
    config["base_url"] = ""
    monkeypatch.setenv(primary, "")
    assert configured_endpoint(config) == constructor(config).base_url == "https://legacy.example.invalid/inference"


def test_cloudflare_preserves_legacy_operator_only_proxy_override(monkeypatch):
    config = {"provider": "cloudflare", "base_url": "https://ignored.example.invalid/v1"}
    monkeypatch.setenv("CF_ACCOUNT_ID", "legacy-account")
    assert configured_endpoint(config) == CloudflareProvider(config).base_url == "https://api.cloudflare.com/client/v4/accounts/legacy-account/ai/v1"
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "middle-account")
    assert "/middle-account/" in configured_endpoint(config)
    monkeypatch.setenv("DOXA_CLOUDFLARE_ACCOUNT_ID", "primary-account")
    assert "/primary-account/" in configured_endpoint(config)
    monkeypatch.setenv("DOXA_CLOUDFLARE_BASE_URL", "https://proxy.example.invalid/v1/")
    assert configured_endpoint(config) == CloudflareProvider(config).base_url == "https://proxy.example.invalid/v1"


def test_runpod_endpoint_id_aliases_match_legacy_adapter(monkeypatch):
    config = {"provider": "runpod"}
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "legacy-id")
    assert configured_endpoint(config) == RunpodProvider(config).base_url == "https://api.runpod.ai/v2/legacy-id/openai/v1"
    monkeypatch.setenv("DOXA_RUNPOD_ENDPOINT_ID", "primary-id")
    assert configured_endpoint(config) == RunpodProvider(config).base_url == "https://api.runpod.ai/v2/primary-id/openai/v1"


@pytest.mark.parametrize("provider", ["lexical", "lexical-baseline"])
def test_lexical_has_no_transport_destination(provider):
    assert configured_endpoint({"provider": provider}) is None


@pytest.mark.parametrize("endpoint", ["ftp://example.invalid/v1", "http://user:password@example.invalid/v1",
                                      "https://example.invalid/v1?secret=value", "https://example.invalid/v1#fragment"])
def test_shared_endpoint_structure_validation_preserves_adapter_contract(endpoint):
    with pytest.raises(ValueError):
        configured_endpoint({"provider": "ollama", "base_url": endpoint})
    with pytest.raises(ValueError):
        OllamaProvider({"base_url": endpoint})


def test_legacy_remote_ollama_alias_is_not_local_policy(monkeypatch, tmp_path):
    from doxa.pilot_api import enforce_analysis_policy, inference_destination
    from doxa.store import Store, utcnow

    monkeypatch.setenv("OLLAMA_BASE_URL", "https://legacy.example.invalid/inference")
    config = {"provider": "ollama", "allow_remote_text": False}
    assert configured_endpoint(config) == OllamaProvider(config).base_url
    assert inference_destination(config) != "local"
    store = Store(tmp_path)
    try:
        store.init()
        store._managed_pilot = True
        store.execute("INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)", ["project-1", "Private pilot", "", utcnow()])
        with pytest.raises(ValueError, match="privacy policy"):
            enforce_analysis_policy(store, "project-1", config)
    finally:
        store.close()


@pytest.mark.parametrize("provider", ["ollama", "openai", "vllm"])
def test_explicit_remote_base_cannot_be_classified_from_local_environment(monkeypatch, provider):
    from doxa.pilot_api import inference_destination

    monkeypatch.setenv("DOXA_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("DOXA_OPENAI_BASE_URL", "http://127.0.0.1:8001/v1")
    config = {"provider": provider, "base_url": "https://override.example.invalid/v1", "inference_mode": "local"}
    assert inference_destination(config) != "local"


@pytest.mark.parametrize("provider,environment", [("cloudflare", "DOXA_CLOUDFLARE_BASE_URL"),
                                                  ("runpod", "DOXA_RUNPOD_BASE_URL")])
def test_cloud_proxy_overrides_require_https_outside_localhost(monkeypatch, provider, environment):
    from doxa.pilot_api import inference_destination

    monkeypatch.setenv(environment, "http://proxy.example.invalid/v1")
    with pytest.raises(ValueError, match="HTTPS"):
        inference_destination({"provider": provider})
    monkeypatch.setenv(environment, "http://127.0.0.1:8001/v1")
    assert inference_destination({"provider": provider, "inference_mode": "local"}) == provider


def test_localhost_openai_gateway_is_remote_unless_explicitly_local(monkeypatch):
    from doxa.pilot_api import inference_destination

    config = {"provider": "openai", "base_url": "http://127.0.0.1:8001/v1"}
    assert OpenAICompatibleProvider(config).mode == "remote"
    assert inference_destination(config) == "openai"
    monkeypatch.setenv("DOXA_OPENAI_MODE", "local")
    assert OpenAICompatibleProvider(config).mode == "local"
    assert inference_destination(config) == "local"
    config["inference_mode"] = "remote"
    assert OpenAICompatibleProvider(config).mode == "remote"
    assert inference_destination(config) == "openai"


@pytest.mark.parametrize("endpoint", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434",
                                      "http://ollama:11434", "http://host.docker.internal:11434"])
def test_supported_local_ollama_hosts_remain_local(endpoint):
    from doxa.pilot_api import inference_destination

    assert inference_destination({"provider": "ollama", "base_url": endpoint}) == "local"
