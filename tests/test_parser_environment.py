from doxa.api import parser_environment


def test_parser_does_not_inherit_inference_or_storage_credentials(monkeypatch):
    for name in ["CLOUDFLARE_API_TOKEN", "DOXA_SERVICE_TOKEN", "OPENAI_API_KEY", "RUNPOD_API_KEY", "HF_TOKEN"]:
        monkeypatch.setenv(name, "test-secret-not-for-extractor")
    monkeypatch.setenv("DOXA_DOCLING_ARTIFACTS", "/tmp/local-models")
    env = parser_environment()
    assert not any("TOKEN" in key or "KEY" in key or "SECRET" in key for key in env)
    assert env["DOXA_DOCLING_ARTIFACTS"] == "/tmp/local-models"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
