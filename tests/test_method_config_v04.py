import copy
from dataclasses import FrozenInstanceError

import pytest

from doxa.method_config import execution_policy, method_config, method_fingerprint, new_run_session, source_metadata_hash, structural_fingerprint


def test_method_hash_excludes_execution_but_includes_unknown_analytical_settings_and_bibliography():
    base = {"provider": "ollama", "model": "qwen3.5:4b", "question": "Which proposition?", "lens_ids": ["ihra"],
            "source_metadata_hash": source_metadata_hash([{"title": "Original", "author": "A"}]), "budget_usd": 1}
    other = {**base, "budget_usd": 4, "concurrency": 8, "source_manifest_key": "runs/other/source.json",
             "task_timing_history": [{"task_timeout_seconds": 1800}], "provider_preflight": {"checked_at": "later"}}
    assert method_fingerprint(base) == method_fingerprint(other)
    assert execution_policy(base).fingerprint != execution_policy(other).fingerprint
    for change in ({"question": "Different"}, {"novel_analytical_setting": True},
                   {"source_metadata_hash": source_metadata_hash([{"title": "Changed", "author": "A"}])},
                   {"reasoning_effort": "low"}, {"max_output_tokens": 1200}):
        assert method_fingerprint(base) != method_fingerprint({**base, **change})


def test_structural_comparison_excludes_memory_only_but_method_preserves_memory():
    before = {"model": "fixed", "source_snapshot": [{"id": "d", "sha256": "x"}], "learning_snapshot_id": None}
    after = {**before, "learning_snapshot_id": "snapshot", "approved_examples": [{"quote": "Example"}]}
    assert structural_fingerprint(before) == structural_fingerprint(after)
    assert method_fingerprint(before) != method_fingerprint(after)
    assert structural_fingerprint(before) != structural_fingerprint({**after, "source_snapshot": [{"id": "d", "sha256": "changed"}]})


def test_deep_immutable_detached_records_and_no_credentials():
    config = {"lens_ids": ["ihra"], "api_key": "DO-NOT-RETAIN", "nested": {"authorization": "SECRET", "temperature": 0},
              "_record_usage": lambda x: x}
    frozen = method_config(config)
    config["lens_ids"].append("jda")
    detached = frozen.as_dict()
    detached["settings"]["lens_ids"].append("nexus")
    assert frozen.as_dict()["settings"]["lens_ids"] == ["ihra"]
    assert "SECRET" not in frozen._json and "DO-NOT-RETAIN" not in frozen._json
    with pytest.raises(FrozenInstanceError):
        frozen._json = "changed"
    with pytest.raises(ValueError):
        method_config({"temperature": float("nan")})


def test_session_is_separate_and_source_hash_must_match():
    config = {"model": "fixed", "budget_usd": 0}
    first = new_run_session(config, session_id="one", started_at="2026-11-17")
    second = new_run_session(config, session_id="two", started_at="2026-11-18")
    assert first.fingerprint != second.fingerprint
    assert first.as_dict()["method_fingerprint"] == second.as_dict()["method_fingerprint"]
    manifest = [{"id": "d", "author": "Original"}]
    assert method_config(config, manifest).as_dict()["settings"]["source_metadata_hash"] == source_metadata_hash(manifest)
    bad = copy.deepcopy(config)
    bad["source_metadata_hash"] = "incorrect"
    with pytest.raises(ValueError, match="metadata hash"):
        method_config(bad, manifest)
