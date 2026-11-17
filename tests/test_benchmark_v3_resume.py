from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("benchmark_v3_resume", Path(__file__).resolve().parents[1] / "scripts/benchmark_v3.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def receipt(tmp_path, collection="news-sample"):
    analytical = {"provider": "ollama", "model": "qwen3.5:4b", "lens_ids": ["ihra"], "question": "Apply criteria."}
    selection = {"collection": collection, "documents": 20, "sampled_documents": True,
                 "passage_sampling": False, "selection_policy": "fixed whole articles", "publisher": "Public publisher",
                 "manifest_sha256": "a" * 64, "collection_sha256": "b" * 64,
                 "sources": [{"source_id": f"source-{i}", "content_sha256": str(i) * 64, "path": f"corpus/news/{i}.json"} for i in range(20)]}
    engine = "original-engine"
    state = {"selection": deepcopy(selection), "engine_fingerprint": engine, "data_dir": str(tmp_path.resolve()),
             "signature": benchmark.checksum({"config": analytical, "selection": selection, "engine": engine})}
    return state, analytical, selection


def test_unchanged_twenty_sources_can_resume_after_parent_digests_change(tmp_path):
    state, config, current = receipt(tmp_path)
    original_selection, original_signature = deepcopy(state["selection"]), state["signature"]
    current.update(manifest_sha256="c" * 64, collection_sha256="d" * 64)
    result = benchmark.validate_resume_state(state, config, current, "original-engine", tmp_path, {})
    assert result["parent_provenance_updated"]
    assert state["selection"] == original_selection and state["signature"] == original_signature
    update = state["parent_provenance_updates"][0]
    assert update["old"]["manifest_sha256"] == "a" * 64 and update["new"]["manifest_sha256"] == "c" * 64
    assert update["at"] and update["selected_sources_sha256"]
    assert not benchmark.validate_resume_state(state, config, current, "original-engine", tmp_path, {})["parent_provenance_updated"]
    assert len(state["parent_provenance_updates"]) == 1


@pytest.mark.parametrize("change", ["hash", "id", "path", "order", "policy"])
def test_parent_exception_cannot_change_any_selected_scope_setting(tmp_path, change):
    state, config, current = receipt(tmp_path)
    current["manifest_sha256"] = "c" * 64
    if change == "hash":
        current["sources"][0]["content_sha256"] = "changed"
    elif change == "id":
        current["sources"][0]["source_id"] = "another"
    elif change == "path":
        current["sources"][0]["path"] = "another.json"
    elif change == "order":
        current["sources"].reverse()
    else:
        current["selection_policy"] = "different"
    original = deepcopy(state)
    with pytest.raises(ValueError, match="Selected sources"):
        benchmark.validate_resume_state(state, config, current, "original-engine", tmp_path, {})
    assert state == original


def test_full_news_parent_changes_still_require_new_run(tmp_path):
    state, config, current = receipt(tmp_path, collection="news")
    current["manifest_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="Selected sources"):
        benchmark.validate_resume_state(state, config, current, "original-engine", tmp_path, {})


def test_original_checkpoint_signature_and_selected_method_receipt_are_enforced(tmp_path):
    state, config, current = receipt(tmp_path)
    current["manifest_sha256"] = "c" * 64
    frozen = {"engine_fingerprint": "original-engine", "components": {"contextual": "same", "models": "same", "selected_lenses": "same"}}
    accepted = benchmark.validate_resume_state(state, config, current, "unselected-extension", tmp_path, frozen["components"], frozen)
    assert accepted["compatible_extension"] and state["engine_fingerprint"] == "original-engine"
    with pytest.raises(ValueError, match="Selected engine"):
        benchmark.validate_resume_state(state, config, current, "changed-core", tmp_path, {"contextual": "changed"}, frozen)
    with pytest.raises(ValueError, match="analytical configuration"):
        benchmark.validate_resume_state(state, {**config, "lens_ids": ["another"]}, current, "original-engine", tmp_path, {})
    state["selection"]["sources"][0]["content_sha256"] = "tampered"
    with pytest.raises(ValueError, match="checkpoint signature"):
        benchmark.validate_resume_state(state, config, current, "original-engine", tmp_path, {})


def test_concurrency48_is_explicit_upper_bound(tmp_path):
    args = ["--data-dir", str(tmp_path), "--output", str(tmp_path / "summary.json"), "--concurrency"]
    assert benchmark.parse_args([*args, "48"]).concurrency == 48
    with pytest.raises(SystemExit):
        benchmark.parse_args([*args, "49"])
