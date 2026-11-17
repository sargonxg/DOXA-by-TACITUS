import json

import pytest

from doxa.model_registry import get_model_profile, list_model_profiles


def test_registry_is_json_safe_and_does_not_silently_substitute():
    profiles = list_model_profiles()
    assert {profile["id"] for profile in profiles} == {"minilm", "deberta", "cloud-gemma", "cloud-verifier", "local-qwen"}
    json.dumps(profiles)
    with pytest.raises(ValueError, match="Unknown model"):
        get_model_profile("a-missing-model")
    modified = get_model_profile("deberta")
    modified["id2label"]["1"] = "contradiction"
    assert get_model_profile("deberta")["id2label"]["1"] == "not_entailment"


def test_cpu_limits_licenses_and_cloud_revision_constraints_are_explicit():
    assert get_model_profile("minilm")["max_input_tokens"] == 256
    nli = get_model_profile("deberta")
    assert nli["model"].endswith("-c") and nli["license"] == "MIT"
    assert nli["max_input_tokens"] == 512 and len(nli["revision"]) == 40
    assert get_model_profile("cloud-gemma")["revision"] == "provider_managed_unpinned"
    assert get_model_profile("cloud-verifier")["model"] == "@cf/openai/gpt-oss-120b"
