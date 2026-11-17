import hashlib
import io
import importlib.util
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from doxa.cpu_models import CPUModelError
from doxa.model_registry import get_model_profile
spec = importlib.util.spec_from_file_location("model_setup", Path(__file__).resolve().parents[1] / "scripts" / "model_setup.py")
model_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_setup)


def upstream(monkeypatch, *, tamper=False):
    profile = get_model_profile("minilm")
    files = {name: f"test-only {name}".encode() for name in profile["files"]}
    metadata = {"sha": profile["revision"], "cardData": {"license": "apache-2.0"},
                "siblings": [{"rfilename": name, "size": len(data),
                              "lfs": {"sha256": hashlib.sha256(data).hexdigest()}} for name, data in files.items()]}
    calls = []
    def open_url(url):
        calls.append(url)
        if "?blobs=true" in url:
            return io.BytesIO(json.dumps(metadata).encode())
        path = unquote(url.split(profile["revision"] + "/", 1)[1])
        data = files[path]
        if tamper and path == "model.safetensors":
            data = b"x" * len(data)
        return io.BytesIO(data)
    monkeypatch.setattr(model_setup, "_open", open_url)
    return calls


def test_explicit_download_is_pinned_verified_idempotent_and_pickle_free(tmp_path, monkeypatch):
    calls = upstream(monkeypatch)
    result = model_setup.download_model("minilm", tmp_path)
    assert result["hashes_verified"] and result["manifest"]["license"] == "Apache-2.0"
    assert all(get_model_profile("minilm")["revision"] in url for url in calls)
    assert all(not item["path"].endswith(".bin") for item in result["manifest"]["artifacts"])
    count = len(calls)
    model_setup.download_model("minilm", tmp_path)
    assert len(calls) == count
    (tmp_path / "minilm" / "config.json").write_text("damaged")
    with pytest.raises(CPUModelError):
        model_setup.download_model("minilm", tmp_path)
    assert len(calls) == count


def test_download_rejects_byte_limit_before_artifact_fetch_and_cleans_failed_stage(tmp_path, monkeypatch):
    calls = upstream(monkeypatch)
    with pytest.raises(ValueError, match="download limit"):
        model_setup.download_model("minilm", tmp_path, max_download_bytes=1)
    assert len(calls) == 1 and not (tmp_path / "minilm").exists()
    upstream(monkeypatch, tamper=True)
    with pytest.raises(ValueError, match="SHA256"):
        model_setup.download_model("minilm", tmp_path)
    assert not list(tmp_path.iterdir())
