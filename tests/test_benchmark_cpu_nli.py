import gzip
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("benchmark_cpu_nli", Path(__file__).resolve().parents[1] / "scripts/benchmark_cpu_nli.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
HYPOTHESES, canonical_bytes, run_benchmark, sha = benchmark.HYPOTHESES, benchmark.canonical_bytes, benchmark.run_benchmark, benchmark.sha


def selection_fixture(root):
    sources = []
    for index in range(20):
        blocks = [{"id": f"{index}-a", "text": "Equal rights — including religious freedom."},
                  {"id": f"{index}-b", "text": "The report describes discrimination."}]
        digest = sha(canonical_bytes(blocks))
        source = {"id": f"case-{index}", "title": f"Test fixture {index}", "blocks": blocks,
                  "content_sha256": digest, "word_count": 10, "author": "Test fixture author",
                  "license": "CC-BY-3.0", "attribution": "Synthetic unit-test fixture credits"}
        path = root / f"case-{index}.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        sources.append({"source_id": source["id"], "content_sha256": digest, "path": path.name})
    return {"collection": "news-sample", "documents": 20, "passage_sampling": False, "sources": sources}


class FixtureRuntime:
    def __init__(self, gap=False):
        self.calls = 0
        self.gap = gap

    def health(self, *args, **kwargs):
        return {"ok": True}

    def model_profile(self, key):
        return {"model": "test-only-fixture", "revision": "not-a-real-model"}

    def score_rubrics(self, text, hypotheses, **kwargs):
        self.calls += 1
        scores = [{"id": hypothesis["id"], "hypothesis": hypothesis["text"],
                   "entailment_score": 0.731, "logits": {"entailment": 0.5, "not_entailment": -0.5}}
                  for hypothesis in hypotheses]
        windows = [{"start": 0, "end": len(text), "text": text, "scores": scores, "token_count": 16}]
        if self.gap:
            windows = [{**windows[0], "end": 4, "text": text[:4]},
                       {**windows[0], "start": 15, "text": text[15:]}]
        return {"windows": windows, "aggregation": "none", "full_source_token_coverage": True,
                "pairs_evaluated": len(windows) * len(hypotheses)}


def test_full_twenty_article_scores_keep_exact_unicode_blocks_and_all_hypotheses(tmp_path):
    selection = selection_fixture(tmp_path)
    runtime = FixtureRuntime()
    path = tmp_path / "nli-summary.json"
    summary = run_benchmark(selection, path, root=tmp_path, runtime=runtime)
    assert runtime.calls == summary["documents_complete"] == 20
    assert summary["pairs_evaluated"] == 80
    assert summary["interpretation_accuracy"] is None
    assert summary["aggregation"] == "none" and summary["threshold"] is None
    scores_path = tmp_path / summary["complete_scores"]["path"]
    assert sha(scores_path.read_bytes()) == summary["complete_scores"]["sha256"]
    with gzip.open(scores_path, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert rows[0]["hypotheses"] == HYPOTHESES
    assert len(rows) == 41
    documents = [row for row in rows if row["record_type"] == "document"]
    assert len(documents) == 20
    assert all(row["credits_and_provenance"]["attribution"] == "Synthetic unit-test fixture credits" for row in documents)
    for row in (row for row in rows if row["record_type"] == "window_scores"):
        assert len(row["scores"]) == 4
        assert len(row["source_block_refs"]) == 2
        assert row["text"] == "\n\n".join(reference["text"] for reference in row["source_block_refs"])
        assert all(reference["start"] == 0 and reference["end"] == len(reference["text"]) for reference in row["source_block_refs"])
    with pytest.raises(ValueError, match="output exists"):
        run_benchmark(selection, path, root=tmp_path, runtime=runtime)


def test_changed_source_fails_before_any_inference_or_public_output(tmp_path):
    selection = selection_fixture(tmp_path)
    path = tmp_path / selection["sources"][17]["path"]
    changed = json.loads(path.read_text())
    changed["blocks"][0]["text"] = "Changed source text"
    path.write_text(json.dumps(changed))
    runtime = FixtureRuntime()
    output = tmp_path / "nli-summary.json"
    with pytest.raises(ValueError, match="frozen source"):
        run_benchmark(selection, output, root=tmp_path, runtime=runtime)
    assert runtime.calls == 0
    assert not output.exists() and not (tmp_path / "nli-scores.jsonl.gz").exists()


def test_omitted_source_content_cannot_publish_a_completed_proof(tmp_path):
    selection = selection_fixture(tmp_path)
    output = tmp_path / "nli-summary.json"
    with pytest.raises(ValueError, match="omitted source content"):
        run_benchmark(selection, output, root=tmp_path, runtime=FixtureRuntime(gap=True))
    assert not output.exists()
    assert not (tmp_path / "nli-scores.jsonl.gz").exists()
    assert not (tmp_path / "nli-scores.jsonl.gz.tmp").exists()
