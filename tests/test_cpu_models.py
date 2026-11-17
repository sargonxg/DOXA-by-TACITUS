import hashlib
import json
import re

import pytest

from doxa.cpu_models import CPUModelError, CPUModels, token_windows, verify_installation
from doxa.model_registry import MANIFEST_VERSION, get_model_profile


def bundle(root, key="minilm"):
    profile = get_model_profile(key)
    directory = root / key
    directory.mkdir(parents=True)
    artifacts = []
    for filename in profile["files"]:
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        data = f"test-only artifact {filename}".encode()
        path.write_bytes(data)
        artifacts.append({"path": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"manifest_version": MANIFEST_VERSION, "model": profile["model"],
                "revision": profile["revision"], "license": profile["license"], "artifacts": artifacts}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory, manifest


class Words:
    def __call__(self, text, *, add_special_tokens=False, return_offsets_mapping=False, truncation=False, verbose=False):
        offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        result = {"input_ids": list(range(len(offsets) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = offsets
        return result

    def num_special_tokens_to_add(self, pair=False):
        return 3 if pair else 2


def test_bundle_identity_checksum_and_symlink_safety(tmp_path):
    directory, manifest = bundle(tmp_path)
    assert verify_installation("minilm", tmp_path)["hashes_verified"]
    weights = directory / "model.safetensors"
    data = weights.read_bytes()
    weights.write_bytes(b"x" * len(data))
    with pytest.raises(CPUModelError, match="checksum"):
        verify_installation("minilm", tmp_path)
    weights.write_bytes(data)
    manifest["revision"] = "a" * 40
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(CPUModelError, match="identity"):
        verify_installation("minilm", tmp_path)


def test_manifest_cannot_add_paths_and_model_dir_cannot_escape(tmp_path):
    directory, manifest = bundle(tmp_path)
    manifest["artifacts"][0]["path"] = "../outside"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(CPUModelError, match="Unexpected"):
        verify_installation("minilm", tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    directory.rename(other / "minilm")
    directory.symlink_to(other / "minilm", target_is_directory=True)
    with pytest.raises(CPUModelError, match="symbolic"):
        verify_installation("minilm", tmp_path)


def test_health_does_not_download_or_create_directories(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Runtime health must never fetch artifacts")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    root = tmp_path / "not-created"
    health = CPUModels(root).health()
    assert not health["ok"] and not health["automatic_downloads"]
    assert all(row["code"] == "model_not_installed" for row in health["models"])
    assert not root.exists()


def test_windows_cover_unicode_source_with_exact_offsets_and_overlap():
    source = "  安全 source one two three four five six seven eight nine ten  "
    windows = token_windows(source, Words(), 5, 2)
    assert len(windows) > 1 and windows[0]["start"] == 0 and windows[-1]["end"] == len(source)
    covered = set()
    for window in windows:
        assert window["text"] == source[window["start"]:window["end"]]
        assert window["token_count"] <= 5
        covered.update(range(window["start"], window["end"]))
    assert covered == set(range(len(source)))
    with pytest.raises(CPUModelError, match="Overlap"):
        token_windows(source, Words(), 5, 5)


def test_rubric_scores_are_independent_not_cross_label_softmax(tmp_path, monkeypatch):
    runtime = CPUModels(tmp_path)
    monkeypatch.setattr(runtime, "_get_tokenizer", lambda key: Words())
    observed_pairs = []
    def predict(pairs):
        observed_pairs.extend(pairs)
        return [[4.0, 0.0] for _ in pairs]
    monkeypatch.setattr(runtime, "_predict_pairs", predict)
    source = " ".join(f"token{i}" for i in range(1200))
    hypotheses = [{"id": "a", "text": "The source supports equal rights."},
                  {"id": "b", "text": "The source mentions a religious group."}]
    result = runtime.score_rubrics(source, hypotheses)
    assert result["full_source_token_coverage"] and result["aggregation"] == "none"
    assert len(result["windows"]) >= 3 and len(observed_pairs) == result["pairs_evaluated"]
    for premise, hypothesis in observed_pairs:
        assert len(Words()(premise)["input_ids"]) + len(Words()(hypothesis)["input_ids"]) + 3 <= 512
    for window in result["windows"]:
        assert all(score["entailment_score"] > 0.98 for score in window["scores"])
        assert window["text"] == source[window["start"]:window["end"]]
    assert "not contradiction" in result["score_meaning"]


def test_embeddings_require_explicit_windowing_instead_of_silent_truncation(tmp_path, monkeypatch):
    runtime = CPUModels(tmp_path)
    monkeypatch.setattr(runtime, "_get_tokenizer", lambda key: Words())
    monkeypatch.setattr(runtime, "_encode_embeddings", lambda texts: [[1.0] + [0.0] * 383 for text in texts])
    assert len(runtime.embed(["one short input"])[0]) == 384
    source = " ".join(["evidence"] * 800)
    with pytest.raises(CPUModelError, match="256"):
        runtime.embed([source])
    result = runtime.embed_windows([source])
    assert len(result["windows"]) >= 4
    assert result["windows"][-1]["end"] == len(source)


def test_malformed_hypotheses_and_nonfinite_logits_fail_closed(tmp_path, monkeypatch):
    runtime = CPUModels(tmp_path)
    with pytest.raises(CPUModelError, match="unique"):
        runtime.score_rubrics("source", [{"id": "x", "text": "one"}, {"id": "x", "text": "two"}])
    monkeypatch.setattr(runtime, "_get_tokenizer", lambda key: Words())
    monkeypatch.setattr(runtime, "_predict_pairs", lambda pairs: [[float("nan"), 0]])
    with pytest.raises(CPUModelError, match="finite"):
        runtime.score_rubrics("source", [{"id": "x", "text": "one"}])


def test_formatting_only_input_is_disclosed_in_embedding_windows(tmp_path, monkeypatch):
    class NoTokens(Words):
        def __call__(self, text, **kwargs):
            if text == "\u200b":
                return {"input_ids": [], "offset_mapping": []}
            return super().__call__(text, **kwargs)
    runtime = CPUModels(tmp_path)
    monkeypatch.setattr(runtime, "_get_tokenizer", lambda key: NoTokens())
    monkeypatch.setattr(runtime, "_encode_embeddings", lambda texts: [[1.0] + [0.0] * 383 for text in texts])
    result = runtime.embed_windows(["\u200b", "ordinary source"])
    assert result["nonindexable_inputs"] == [{"input_index": 0, "reason": "tokenizer_produced_no_source_tokens", "characters": 1}]
    assert len(result["windows"]) == 1 and result["windows"][0]["input_index"] == 1
    with pytest.raises(CPUModelError, match="no source tokens"):
        runtime.score_rubrics("\u200b", [{"id": "one", "text": "ordinary hypothesis"}])


def test_cpu_batch_policy_is_explicit_and_incomplete_vectors_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="batching"):
        CPUModels(tmp_path, embedding_batching="unregistered")
    runtime = CPUModels(tmp_path, threads=2, batch_size=16, embedding_batching="length_ascending")
    profile = runtime.model_profile("minilm")
    assert profile["runtime_settings"] == {"device": "cpu", "threads": 2, "batch_size": 16,
                                           "batching_policy": "length_ascending", "pooling": "attention_mask_mean_then_l2"}
    monkeypatch.setattr(runtime, "_get_tokenizer", lambda key: Words())
    monkeypatch.setattr(runtime, "_encode_embeddings", lambda texts: [])
    with pytest.raises(CPUModelError, match="counts differ"):
        runtime.embed_windows(["An exact source paragraph"])
