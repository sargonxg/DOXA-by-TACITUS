"""Shared-scheduler benchmark plumbing; fake responses are never public quality results."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from doxa.providers import ProviderError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("doxa_benchmark_cli", ROOT / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def args(tmp_path, *extra):
    return benchmark.parse_args([
        "--provider", "lexical", "--max-documents", "2", "--max-blocks-per-document", "2",
        "--concurrency", "2", "--lens-ids", "discourse",
        "--data-dir", str(tmp_path / "data"), "--output", str(tmp_path / "result.json"),
        *extra,
    ])


def test_concurrency_bounds_and_ollama_memory_guard(tmp_path):
    with pytest.raises(SystemExit):
        args(tmp_path, "--concurrency", "9")
    with pytest.raises(SystemExit):
        args(tmp_path, "--provider", "ollama", "--concurrency", "2")


@pytest.mark.asyncio
async def test_fast_worker_claims_next_task_before_slow_tail_finishes():
    slow_started = asyncio.Event()
    slow_finished = asyncio.Event()
    fast_refilled = asyncio.Event()
    claimed = []
    completed = []
    errors = []

    async def work_once(_run_id):
        ordinal = len(claimed)
        claimed.append(ordinal)
        if ordinal >= 3:
            return {"pending": False}
        if ordinal == 0:
            slow_started.set()
            # The slow task cannot finish until another worker has already
            # claimed the third task. A wave/barrier scheduler fails this.
            await asyncio.wait_for(fast_refilled.wait(), timeout=2)
            slow_finished.set()
        elif ordinal == 1:
            await slow_started.wait()
        else:
            assert not slow_finished.is_set()
            fast_refilled.set()
        completed.append(ordinal)
        return {"pending": True}

    async def on_settled(_result, error):
        if error:
            errors.append(error)
        return True

    unexpected = await benchmark.run_worker_pool(work_once, "test", 2, on_settled)
    assert unexpected == [] and errors == []
    assert completed.index(2) < completed.index(0)
    assert fast_refilled.is_set() and slow_finished.is_set()


@pytest.mark.asyncio
async def test_two_document_parallel_lexical_run_and_resume(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    options = args(tmp_path)
    assert await benchmark.run_benchmark(options) == 0
    first = json.loads(options.output.read_text())
    assert first["runs"][0]["status"] == "completed"
    assert first["metrics"]["coverage_detail"]["selected_input_complete"] is True
    assert first["metrics"]["coverage_detail"]["full_corpus_scan_complete"] is False
    assert first["metrics"]["coverage_detail"]["semantic_analysis_performed"] is False
    assert first["benchmark"]["sessions"][-1]["concurrency"] == 2
    assert first["benchmark"]["citation_audit"]["invalid_finding_ids"] == []
    initial_ids = {finding["id"] for finding in first["findings"]}
    options.resume = True
    options.concurrency = 4  # Operational tuning does not change the source/model signature.
    assert await benchmark.run_benchmark(options) == 0
    second = json.loads(options.output.read_text())
    assert {finding["id"] for finding in second["findings"]} == initial_ids
    assert second["benchmark"]["sessions"][-1]["concurrency"] == 4
    assert second["metrics"]["usage"]["requests"] == 0


@pytest.mark.asyncio
async def test_parallel_provider_failure_remains_partial(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)

    class ControlledProvider:
        name = "controlled_test_provider"
        profile = {"provider": name, "model": "controlled-test-model", "mode": "local", "revision": "test"}
        calls = 0

        async def health(self):
            return {"ok": True, **self.profile}

        def estimate_cost(self, *_):
            return 0.0

        async def generate(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls >= 2:
                raise ProviderError("controlled_test_failure", "Deliberate test-only provider failure.")
            return {"content": {"findings": []}, "finish_reason": "stop",
                    "usage": {"input_tokens": 1, "output_tokens": 0, "cost_usd": 0,
                              "estimated": False, "requests": 1},
                    "model_profile": self.profile}

    controlled = ControlledProvider()

    async def factory(_config):
        return controlled

    monkeypatch.setattr(benchmark, "get_provider", factory)
    monkeypatch.setattr("doxa.providers.get_provider", factory)
    options = args(tmp_path, "--provider", "openai", "--model", "controlled-test-model")
    assert await benchmark.run_benchmark(options) == 1
    result = json.loads(options.output.read_text())
    assert result["runs"][0]["status"] == "partial"
    assert result["metrics"]["coverage_detail"]["selected_input_complete"] is False
    assert any(error.get("code") == "controlled_test_failure" for error in result["benchmark"]["errors"])
    assert result["metrics"]["interpretation_accuracy"] is None
