"""Readiness and local scheduling tests; no model or network success is implied."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from doxa.cli import main
from doxa.local_profile import apply_local_environment_defaults, configured_slots, effective_limits, heavy_operation, local_limits, run_heavy
from doxa.readiness import readiness, register_readiness_api
from doxa.starter_pack import starter_pack


def test_low_memory_is_native_default_without_enabling_models_or_remote_processing():
    limits = local_limits({})
    assert limits["name"] == "low-memory"
    assert limits["application_budget_mib"] == 4096
    assert limits["parser_slots"] == limits["heavy_job_slots"] == 1
    assert limits["parser_memory_mib"] == 1536
    assert not limits["automatic_downloads"] and not limits["automatic_cloud_fallback"]
    assert not limits["large_model_required"]


def test_remote_profile_preserves_server_defaults_and_explicit_profiles_are_respected():
    assert local_limits({"DOXA_CLOUD_STATE_URL": "https://private.invalid"})["name"] == "server"
    assert local_limits({"DOXA_LOCAL_PROFILE": "standard"})["parser_slots"] == 2
    assert local_limits({}, cloud_state=True)["name"] == "server"
    with pytest.raises(ValueError, match="DOXA_LOCAL_PROFILE"):
        local_limits({"DOXA_LOCAL_PROFILE": "unlimited"})


def test_defaults_are_only_operational_and_never_overwrite_operator_values():
    environment = {"DOXA_CPU_THREADS": "3", "DOXA_PARSER_MEMORY_MB": "1200"}
    before = dict(environment)
    apply_local_environment_defaults(environment)
    assert environment["DOXA_CPU_THREADS"] == "3" and environment["DOXA_PARSER_MEMORY_MB"] == "1200"
    assert environment["DOXA_CPU_BATCH_SIZE"] == "2"
    assert environment["DOXA_LOCAL_HEAVY_SLOTS"] == "1"
    assert all(key.startswith("DOXA_") for key in environment)
    assert all(environment[key] == value for key, value in before.items())
    effective = effective_limits(environment)
    assert not effective["profile_defaults_retained"]
    assert effective["parser_memory_mib"] == 1200
    assert "DOXA_CPU_THREADS" in effective["operator_overrides"]


def test_invalid_resource_override_has_an_actionable_error():
    with pytest.raises(ValueError, match="DOXA_CPU_BATCH_SIZE"):
        apply_local_environment_defaults({"DOXA_CPU_BATCH_SIZE": "huge"})


@pytest.mark.parametrize("value", ["0", "9", "many", "1.5"])
def test_slot_configuration_fails_closed(value):
    with pytest.raises(ValueError, match="DOXA_LOCAL_HEAVY_SLOTS"):
        configured_slots("heavy", {"DOXA_LOCAL_HEAVY_SLOTS": value})


def test_heavy_gate_is_reentrant_and_released_after_worker_failure(monkeypatch):
    monkeypatch.setenv("DOXA_LOCAL_PROFILE", "low-memory")
    monkeypatch.setenv("DOXA_LOCAL_HEAVY_SLOTS", "1")
    with heavy_operation():
        assert run_heavy(lambda: "nested") == "nested"
    with pytest.raises(RuntimeError):
        run_heavy(lambda: (_ for _ in ()).throw(RuntimeError("fixture")))
    assert run_heavy(lambda: "available") == "available"


def test_low_memory_gate_holds_actual_worker_until_completion(monkeypatch):
    monkeypatch.setenv("DOXA_LOCAL_HEAVY_SLOTS", "1")
    first_entered, release_first, second_entered = threading.Event(), threading.Event(), threading.Event()
    def first():
        first_entered.set()
        assert release_first.wait(3)
    def second():
        second_entered.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(run_heavy, first)
        assert first_entered.wait(2)
        two = pool.submit(run_heavy, second)
        assert not second_entered.wait(.05)
        release_first.set()
        one.result(timeout=3)
        two.result(timeout=3)
    assert second_entered.is_set()


def test_doctor_does_not_create_data_or_model_directories_or_expose_paths(tmp_path, monkeypatch):
    model_dir = tmp_path / "private-model-location"
    monkeypatch.setenv("DOXA_CPU_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("DOXA_OPENAI_API_KEY", "private-secret-token")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("diagnostic used network"))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("diagnostic opened socket"))
    target = tmp_path / "private-data-location" / "not-created"
    report = readiness(target)
    assert not target.exists() and not model_dir.exists()
    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized and "private-secret-token" not in serialized
    assert not report["provider_probe_performed"] and report["network_requests"] == report["model_requests"] == 0
    assert all(not row["installed"] for row in report["models"])
    assert report["source_content_included"] is report["private_paths_included"] is False
    assert report["storage"]["write_probe_performed"] is False


def test_unwritable_storage_has_concrete_recovery_without_writing(tmp_path):
    directory = tmp_path / "protected"
    directory.mkdir(mode=0o500)
    report = readiness(directory, include_models=False)
    check = next(check for check in report["checks"] if check["id"] == "storage_permissions")
    assert not report["ok"] and check["status"] == "error"
    assert "writable data directory" in check["action"]
    assert not list(directory.iterdir())


def test_missing_ocr_and_pdf_dependencies_are_specific_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr("doxa.readiness._module", lambda name: False)
    report = readiness(tmp_path, environ={}, include_models=False)
    checks = {row["id"]: row for row in report["checks"]}
    assert checks["ocr"]["status"] == "optional" and "scanned pages" in checks["ocr"]["message"]
    assert checks["pdf_generation"]["status"] == "error"
    assert checks["pdf_extraction"]["status"] == "warning"
    assert "TXT/Markdown" in checks["pdf_extraction"]["action"]


def test_operator_overrides_cannot_masquerade_as_default_low_memory_profile(tmp_path):
    report = readiness(tmp_path, environ={"DOXA_LOCAL_PROFILE": "low-memory", "DOXA_PARSER_SLOTS": "3"}, include_models=False)
    check = next(row for row in report["checks"] if row["id"] == "execution_profile")
    assert check["status"] == "warning" and report["effective_execution"]["parser_slots"] == 3


def test_cli_doctor_json_works_without_api_or_uvicorn_start(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: pytest.fail("doctor started a server")))
    result = main(["doctor", "--json", "--data-dir", str(tmp_path / "absent")])
    report = json.loads(capsys.readouterr().out)
    assert result in {0, 1} and report["schema_version"] == "doxa-readiness-1"
    assert not (tmp_path / "absent").exists()
    assert not report["automatic_downloads"]


def test_cli_laptop_probe_is_explicit_and_uses_the_fixed_loopback_endpoint(tmp_path, monkeypatch, capsys):
    from doxa.laptop import ENDPOINT, MODEL
    from doxa.providers import OllamaProvider

    async def installed(provider):
        assert provider.model == MODEL and provider.base_url == ENDPOINT
        return {"ok": True, "details": "Model installed", "quantization": "Q4_K_M", "revision": "example"}

    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    monkeypatch.setenv("DOXA_OLLAMA_BASE_URL", "https://external.example")
    monkeypatch.setattr(OllamaProvider, "health", installed)
    main(["doctor", "--json", "--laptop", "--data-dir", str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report["laptop"] == {"endpoint": ENDPOINT, "model": MODEL, "ok": True,
                                "revision": "example", "quantization": "Q4_K_M"}
    assert report["provider_probe_performed"] and report["network_requests"] == 1 and report["model_requests"] == 0


def test_existing_cli_serve_flags_remain_valid_and_apply_low_memory_defaults(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for key in ("DOXA_LOCAL_PROFILE", "DOXA_CLOUD_STATE_URL", "DOXA_PARSER_MEMORY_MB", "DOXA_CPU_THREADS"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))))
    main(["--host", "127.0.0.1", "--port", "8123", "--data-dir", str(tmp_path)])
    assert calls == [(("doxa.api:app",), {"host": "127.0.0.1", "port": 8123})]
    assert os.environ["DOXA_CPU_THREADS"] == "2" and os.environ["DOXA_PARSER_MEMORY_MB"] == "1536"


def test_cli_json_is_not_silently_accepted_by_server(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--json"])
    assert error.value.code == 2
    assert "doxa doctor" in capsys.readouterr().err


def test_starter_pack_exactly_matches_published_synthetic_inputs_and_is_isolated():
    pack = starter_pack()
    root = Path(__file__).resolve().parents[1] / "examples/pilot-v05-inputs"
    assert len(pack["files"]) == 5 and pack["data_origin"] == "synthetic_demonstration"
    assert not pack["recorded_model_results"] and not pack["independent_human_labels"]
    for item in pack["files"]:
        raw = (root / item["filename"]).read_bytes()
        assert item["text"].encode() == raw
        assert item["sha256"] == hashlib.sha256(raw).hexdigest()
    pack["files"][0]["text"] = "changed by caller"
    assert starter_pack()["files"][0]["text"] != "changed by caller"


def test_readiness_api_and_starter_are_read_only_and_content_free(tmp_path):
    app = FastAPI()
    register_readiness_api(app, lambda: SimpleNamespace(root=tmp_path))
    before = list(tmp_path.iterdir())
    with TestClient(app) as client:
        response = client.get("/api/system/readiness")
        assert response.status_code == 200
        assert str(tmp_path) not in response.text
        pack = client.get("/api/system/starter-pack").json()
        assert pack["model_requests"] == 0 and len(pack["files"]) == 5
    assert list(tmp_path.iterdir()) == before
