#!/usr/bin/env python3
"""Score twenty complete frozen news articles with an explicitly installed CPU NLI model.

This is a functional/resource baseline, not an accuracy benchmark or prejudice
classifier. No weights are downloaded and no paid inference is called.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
from importlib.metadata import PackageNotFoundError, version
import io
import json
import os
from pathlib import Path
import platform
import sys
import time

try:
    import resource
except ImportError:
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.cpu_models import CPUModels  # noqa: E402
from doxa.store import utcnow  # noqa: E402
from scripts.benchmark_v3 import source_selection  # noqa: E402

HYPOTHESES = [
    {"id": "policy-remedy", "text": "The text proposes a policy or institutional remedy for discrimination."},
    {"id": "reported-hostility", "text": "The text reports hostility or discriminatory treatment directed at a religious group."},
    {"id": "condemnation", "text": "The text condemns hostility or discrimination against a religious group."},
    {"id": "equal-rights", "text": "The text expresses support for equal rights regardless of religion."},
]


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def hardware_observation() -> dict:
    observed = {"platform": platform.platform(), "logical_cpus_visible": os.cpu_count(),
                "cpu_quota_cores": None, "memory_limit_bytes": None}
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            observed["cpu_quota_cores"] = int(quota) / int(period)
        value = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if value != "max":
            observed["memory_limit_bytes"] = int(value)
    except (OSError, ValueError):
        pass
    return observed


def source_text(blocks: list[dict]) -> tuple[str, list[dict]]:
    """Join every original block, retaining an exact reversible offset map."""
    pieces, mapping, offset, identities = [], [], 0, set()
    for block in blocks:
        identity, text = block.get("id"), block.get("text")
        if not isinstance(identity, str) or identity in identities or not isinstance(text, str):
            raise ValueError("Source blocks need unique IDs and original string text")
        identities.add(identity)
        if pieces:
            offset += 2
        mapping.append({"block_id": identity, "start": offset, "end": offset + len(text)})
        pieces.append(text)
        offset += len(text)
    text = "\n\n".join(pieces)
    if not text.strip():
        raise ValueError("A whole article must contain source text")
    return text, mapping


def map_window(text: str, mapping: list[dict], window: dict) -> list[dict]:
    start, end = window.get("start"), window.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != window.get("text"):
        raise ValueError("NLI source window differs from its original text/offsets")
    result = []
    for block in mapping:
        left, right = max(start, block["start"]), min(end, block["end"])
        if left < right:
            result.append({"block_id": block["block_id"], "start": left - block["start"],
                           "end": right - block["start"], "text": text[left:right]})
    if not result:
        raise ValueError("NLI source window has no original block content")
    return result


def verify_full_coverage(text: str, windows: list[dict]) -> None:
    cursor = 0
    for window in sorted(windows, key=lambda value: (value["start"], value["end"])):
        if window["start"] > cursor and text[cursor:window["start"]].strip():
            raise ValueError("NLI windows omitted source content")
        cursor = max(cursor, window["end"])
    if cursor != len(text):
        raise ValueError("NLI windows did not reach the final source character")


def run_benchmark(selection: dict, output: Path, *, model_dir: Path | None = None,
                  threads: int = 4, batch_size: int = 8, root: Path = ROOT,
                  runtime=None) -> dict:
    """Evaluate all pairs; test runtimes are injected only by unit fixtures."""
    output = Path(output)
    scores_path = output.with_name(output.stem.removesuffix("-summary") + "-scores.jsonl.gz")
    if output.exists() or scores_path.exists():
        raise ValueError("Benchmark output exists; choose a new destination to preserve its provenance")
    if selection.get("documents") != 20 or len(selection.get("sources", [])) != 20 or selection.get("passage_sampling"):
        raise ValueError("This baseline requires exactly twenty whole selected sources")
    # Verify every frozen source before model loading or output creation.
    sources = []
    for member in selection["sources"]:
        path = (root / member["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Source path escapes the frozen corpus")
        source = json.loads(path.read_text(encoding="utf-8"))
        digest = sha(canonical_bytes(source["blocks"]))
        if source.get("id") != member["source_id"] or source.get("content_sha256") != digest or digest != member["content_sha256"]:
            raise ValueError("A selected article differs from its frozen source identity/hash")
        text, mapping = source_text(source["blocks"])
        sources.append((source, text, mapping))
    runtime = runtime or CPUModels(model_dir, threads=threads, batch_size=batch_size)
    health = runtime.health("deberta", verify_artifacts=True)
    if not health.get("ok"):
        raise ValueError("Install and verify the pinned CPU NLI bundle explicitly before benchmarking")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_scores = scores_path.with_suffix(scores_path.suffix + ".tmp")
    started = time.perf_counter()
    documents, windows_total, pairs_total = [], 0, 0
    try:
        with temp_scores.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped, io.TextIOWrapper(zipped, encoding="utf-8") as stream:
            stream.write(json.dumps({"record_type": "benchmark", "hypotheses": HYPOTHESES,
                "model_profile": runtime.model_profile("deberta"), "aggregation": "none",
                "score_meaning": "Independent uncalibrated entailment scores; not probabilities of prejudice, contradiction or private beliefs."}, ensure_ascii=False) + "\n")
            for source, text, mapping in sources:
                doc_start = time.perf_counter()
                result = runtime.score_rubrics(text, HYPOTHESES, window_overlap=32)
                windows = result["windows"]
                if result.get("aggregation") != "none" or not result.get("full_source_token_coverage") or result.get("pairs_evaluated") != len(windows) * len(HYPOTHESES):
                    raise ValueError("NLI result lacks the full independent pair/window scope")
                verify_full_coverage(text, windows)
                credit_keys = ("title", "author", "authors", "translators", "editors", "institution", "source_url",
                               "published_at", "modified_at", "license", "license_url", "attribution", "rights_source_url",
                               "rights_rule_version", "rights_review", "language", "language_basis", "extractor_version")
                credits = {key: source[key] for key in credit_keys if key in source}
                stream.write(json.dumps({"record_type": "document", "source_id": source["id"],
                    "source_content_sha256": source["content_sha256"], "joined_text_sha256": sha(text.encode()),
                    "credits_and_provenance": credits,
                    "source_blocks_metadata": [{key: value for key, value in block.items() if key != "text"} for block in source["blocks"]],
                    "block_offset_map": mapping}, ensure_ascii=False, separators=(",", ":")) + "\n")
                records = []
                for index, window in enumerate(windows):
                    refs = map_window(text, mapping, window)
                    if {value["id"] for value in window["scores"]} != {value["id"] for value in HYPOTHESES} or len(window["scores"]) != len(HYPOTHESES):
                        raise ValueError("An NLI window omitted or duplicated a hypothesis score")
                    records.append({"record_type": "window_scores", "source_id": source["id"],
                        "source_content_sha256": source["content_sha256"], "joined_text_sha256": sha(text.encode()),
                        "window_index": index, "offset_unit": "unicode_code_points", **window,
                        "source_block_refs": refs})
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                windows_total += len(windows)
                pairs_total += result["pairs_evaluated"]
                documents.append({"source_id": source["id"], "title": source.get("title"),
                    "source_url": source.get("source_url"), "author": source.get("author"),
                    "license": source.get("license"), "license_url": source.get("license_url"), "attribution": source.get("attribution"),
                    "source_content_sha256": source["content_sha256"], "joined_text_sha256": sha(text.encode()),
                    "blocks": len(mapping), "characters": len(text), "words": len(text.split()),
                    "windows": len(windows), "pairs_evaluated": result["pairs_evaluated"],
                    "final_window_end": windows[-1]["end"], "full_source_token_coverage": True,
                    "seconds": round(time.perf_counter() - doc_start, 6)})
                print(f"{len(documents)}/20 whole articles scored; {pairs_total} independent pairs", flush=True)
        if len(documents) != 20:
            raise ValueError("The twenty-article baseline did not complete")
        versions = {}
        for name in ("torch", "transformers", "tokenizers", "safetensors", "numpy"):
            try:
                versions[name] = version(name)
            except PackageNotFoundError:
                versions[name] = None
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
        peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024) if peak is not None else None
        summary = {"schema_version": "3.0", "status": "completed", "generated_at": utcnow(),
            "kind": "actual_whole_news_cpu_nli_resource_baseline", "synthetic_sources": False,
            "interpretation_accuracy": None, "independent_human_reference_labels": False,
            "collection": selection, "model_profile": runtime.model_profile("deberta"),
            "hypotheses": HYPOTHESES, "aggregation": "none", "threshold": None,
            "score_meaning": "Softmax(entailment,not_entailment)[entailment] for each independent source/hypothesis pair. Uncalibrated; not probability of prejudice. Not-entailment is not contradiction.",
            "documents": documents, "documents_complete": 20, "windows": windows_total,
            "pairs_evaluated": pairs_total, "passage_sampling": False, "full_source_token_coverage": True,
            "source_text_basis": "All original source blocks in order joined with literal double newlines. Window offsets use this exact joined text; source_block_refs map back to exact original block offsets.",
            "window_overlap_tokens": 32, "max_combined_pair_tokens": 512,
            "seconds_including_first_model_load": round(time.perf_counter() - started, 6),
            "peak_process_rss_bytes": peak_bytes, "device": "cpu", "gpu_used": False,
            "model_cache": "The pinned model is loaded once and reused within this process. All scores are freshly computed; no score cache or previous scores are read.",
            "software": versions, "hardware": hardware_observation(),
            "offline_environment_flags": {name: os.environ.get(name) for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")},
            "model_artifacts_hashes_verified_before_load": all(entry.get("hashes_verified") for entry in health.get("models", [])) if health.get("models") else None,
            "provider_api_fee_usd": 0, "cost_basis": "CPU hardware, electricity and installation excluded.",
            "complete_scores": {"path": scores_path.name, "sha256": sha(temp_scores.read_bytes()), "bytes": temp_scores.stat().st_size, "format": "gzip JSONL: benchmark provenance, document credits/block metadata, and every source-window score/logit record"},
            "limitations": ["Functional coverage and resource observations, with no independent accuracy labels or calibration.",
                "Four explicit research hypotheses are independent and can overlap; no winner, automatic threshold, document prejudice label or author private-belief judgment is produced.",
                "Short NLI windows can omit wider discourse context. Reported speech, negation and stance need review; entailment alone does not establish speaker endorsement.",
                "The first source includes the first model load in this process; later sources reuse loaded weights. Filesystem caches are uncontrolled. Timings exclude installation and are not portable throughput guarantees.",
                "Process peak RSS excludes browser, OS and other service processes. This benchmark runs sequentially after the local LLM trial."]}
        temp_summary = output.with_suffix(output.suffix + ".tmp")
        temp_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_scores.replace(scores_path)
        temp_summary.replace(output)
        return summary
    except BaseException:
        temp_scores.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/cpu-nli-20-news-summary.json")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args(argv)
    run_benchmark(source_selection("news-sample", 20), args.output, model_dir=args.model_dir,
                  threads=args.threads, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
