"""Contract tests with synthetic tensors, not a benchmark of model quality."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxa.cpu_lite import ONNXEmbeddings, inspect_bundle
from doxa.cpu_models import CPUModelError
from doxa.model_registry import get_model_profile


def bundle(path):
    path.mkdir()
    artifacts = []
    for name in ("model.onnx", "tokenizer.json"):
        data = ("synthetic contract fixture " + name).encode()
        (path / name).write_bytes(data)
        artifacts.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    base = get_model_profile("minilm")
    manifest = {"schema_version": "doxa-onnx-embedding-1", "base_model": base["model"],
                "base_revision": base["revision"], "license": base["license"], "precision": "int8", "artifacts": artifacts}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_candidate_hashes_identity_and_no_automatic_promotion(tmp_path):
    root = tmp_path / "bundle"
    bundle(root)
    result = inspect_bundle(root)
    assert result["hashes_verified"] and result["quality_status"] == "unqualified_candidate"
    assert not result["automatic_downloads"]
    runtime = ONNXEmbeddings(root)
    assert runtime._session is None
    profile = runtime.model_profile()
    assert not profile["semantic_parity_measured"] and profile["dimensions"] == 384
    data = (root / "model.onnx").read_bytes()
    (root / "model.onnx").write_bytes(b"x" * len(data))
    with pytest.raises(CPUModelError, match="checksum"):
        inspect_bundle(root)


def test_unpinned_missing_and_linked_candidates_are_rejected(tmp_path):
    root = tmp_path / "bundle"
    manifest = bundle(root)
    manifest["base_revision"] = "other-model"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(CPUModelError, match="identity"):
        inspect_bundle(root)
    with pytest.raises(CPUModelError, match="explicitly|Explicitly"):
        inspect_bundle(tmp_path / "not-created")
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(CPUModelError, match="symbolic"):
        inspect_bundle(link)


def test_batching_normalization_and_padding_mask_keep_source_order(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    root = tmp_path / "bundle"
    bundle(root)
    runtime = ONNXEmbeddings(root, batch_size=2)
    class Tokenizer:
        def encode_batch(self, texts, add_special_tokens):
            return [SimpleNamespace(ids=[101, *range(1, len(text.split()) + 1), 102], type_ids=[0] * (len(text.split()) + 2)) for text in texts]
    calls = []
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name=name) for name in ("input_ids", "attention_mask", "token_type_ids")]
        def run(self, outputs, inputs):
            calls.append(inputs)
            hidden = np.zeros((*inputs["input_ids"].shape, 384), dtype=np.float32)
            hidden[:, :, 0] = inputs["input_ids"]
            hidden[:, :, 1] = 1
            hidden[inputs["attention_mask"] == 0, 2] = 99999
            return [hidden]
    runtime._tokenizer, runtime._session = Tokenizer(), Session()
    monkeypatch.setattr(runtime, "_load", lambda: None)
    vectors = runtime.embed(["one", "one two three", "one two"])
    assert len(calls) == 2 and len(vectors) == 3
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert all(vector[2] == 0 for vector in vectors)
    assert vectors[0] != vectors[1]
    assert calls[0]["input_ids"].shape == (2, 5)


def test_oversize_input_is_rejected_without_truncating_or_running_model(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    root = tmp_path / "bundle"
    bundle(root)
    runtime = ONNXEmbeddings(root)
    runtime._tokenizer = SimpleNamespace(encode_batch=lambda *args, **kwargs: [SimpleNamespace(ids=list(range(257)))])
    runtime._session = SimpleNamespace(run=lambda *args: pytest.fail("oversized source reached inference"))
    monkeypatch.setattr(runtime, "_load", lambda: None)
    with pytest.raises(CPUModelError, match="without truncating"):
        runtime.embed(["one source"])


def test_nonfinite_and_zero_vectors_are_rejected(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    root = tmp_path / "bundle"
    bundle(root)
    runtime = ONNXEmbeddings(root)
    runtime._tokenizer = SimpleNamespace(encode_batch=lambda *args, **kwargs: [SimpleNamespace(ids=[101, 1, 102], type_ids=[0, 0, 0])])
    runtime._session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name="input_ids")], run=lambda *args: [np.zeros((1, 3, 384))])
    monkeypatch.setattr(runtime, "_load", lambda: None)
    with pytest.raises(CPUModelError, match="empty or invalid"):
        runtime.embed(["one"])
    runtime._session.run = lambda *args: [np.full((1, 3, 384), float("nan"))]
    with pytest.raises(CPUModelError, match="finite"):
        runtime.embed(["one"])


def test_benchmark_preflight_does_not_execute_or_include_source_text(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from scripts.benchmark_cpu_lite import benchmark
    root = tmp_path / "bundle"
    bundle(root)
    monkeypatch.setattr(ONNXEmbeddings, "embed", lambda *args: pytest.fail("preflight executed a model"))
    result = benchmark(root, ["Private authorized benchmark sentence"])
    assert result["status"] == "ready_not_executed" and not result["fresh_inference_executed"]
    assert "Private authorized" not in json.dumps(result)
    assert result["interpretation_accuracy"] is None and not result["model_promotion"]


def test_benchmark_receipt_does_not_invent_measured_parity(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from scripts.benchmark_cpu_lite import benchmark
    root = tmp_path / "bundle"
    bundle(root)
    monkeypatch.setattr(ONNXEmbeddings, "embed", lambda self, texts: [[1.] + [0.] * 383 for _ in texts])
    result = benchmark(root, ["synthetic fixture"], execute=True)
    assert result["fresh_inference_executed"] and result["produced_vectors"] == 1
    assert not result["baseline_compared"] and "numerical_parity" not in result
