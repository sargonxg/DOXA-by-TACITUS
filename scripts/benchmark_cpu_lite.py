#!/usr/bin/env python3
"""Inspect or explicitly benchmark a local ONNX embedding candidate.

No weights are downloaded/exported. Input text and vectors are not printed or
written to the receipt. Numerical parity is not retrieval relevance or bias
interpretation accuracy.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doxa.cpu_lite import ONNXEmbeddings, inspect_bundle


def benchmark(directory, texts, *, execute=False, baseline_model_dir=None, threads=2, batch_size=2):
    if not isinstance(texts, list) or not 1 <= len(texts) <= 4096 or any(not isinstance(text, str) or not text.strip() or len(text) > 100000 for text in texts):
        raise ValueError("Inputs must be 1–4096 nonempty bounded strings")
    raw = json.dumps(texts, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > 2_000_000:
        raise ValueError("Input JSON exceeds the 2000000-byte benchmark limit")
    bundle = inspect_bundle(directory)
    runtime = ONNXEmbeddings(directory, threads=threads, batch_size=batch_size)
    receipt = {"schema_version": "doxa-cpu-lite-benchmark-1", "status": "ready_not_executed",
               "model_profile": runtime.model_profile(), "manifest_sha256": bundle["manifest_sha256"],
               "input_sha256": hashlib.sha256(raw).hexdigest(), "inputs": len(texts),
               "source_characters": sum(map(len, texts)), "network_requests": 0, "automatic_downloads": False,
               "fresh_inference_executed": False, "interpretation_accuracy": None,
               "retrieval_relevance_accuracy": None, "model_promotion": False,
               "source_text_or_vectors_included": False, "baseline_compared": False,
               "notice": "Explicit local functional/numerical benchmark only. Results do not qualify model interpretation or retrieval relevance."}
    if not execute:
        return receipt
    started = time.perf_counter()
    vectors = runtime.embed(texts)
    receipt.update(status="completed", fresh_inference_executed=True,
                   onnx_elapsed_seconds=round(time.perf_counter() - started, 6),
                   produced_vectors=len(vectors))
    del runtime
    gc.collect()
    if baseline_model_dir is not None:
        from doxa.cpu_models import CPUModels
        baseline = CPUModels(Path(baseline_model_dir), threads=threads, batch_size=batch_size)
        started = time.perf_counter()
        originals = baseline.embed(texts)
        import numpy as np
        original, candidate = np.asarray(originals), np.asarray(vectors)
        if original.shape != candidate.shape:
            raise ValueError("Candidate and baseline vector counts/dimensions differ")
        cosine = np.sum(original * candidate, axis=1) / (np.linalg.norm(original, axis=1) * np.linalg.norm(candidate, axis=1))
        finite = bool(np.isfinite(cosine).all())
        receipt.update(baseline_compared=True,
                       baseline_elapsed_seconds=round(time.perf_counter() - started, 6),
                       baseline_model_profile=baseline.model_profile("minilm"),
                       numerical_parity={"finite": finite, "minimum_cosine": float(cosine.min()) if finite else None,
                                         "mean_cosine": float(cosine.mean()) if finite else None,
                                         "maximum_absolute_component_difference": float(np.abs(original - candidate).max()),
                                         "declared_minimum_cosine": .99,
                                         "candidate_passed_numerical_check": bool(finite and cosine.min() >= .99),
                                         "semantic_quality_established": False})
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="JSON array of authorized source strings")
    parser.add_argument("--execute", action="store_true", help="Explicitly request local inference; absent means metadata-only preflight")
    parser.add_argument("--baseline-model-dir", type=Path, help="Already installed pinned MiniLM bundle root; never downloaded")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--receipt", type=Path, help="Optional new private receipt; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > 2_000_000:
            raise ValueError("Input JSON exceeds the 2000000-byte benchmark limit")
        if args.receipt and args.receipt.exists():
            raise ValueError("The receipt path already exists")
        result = benchmark(args.bundle, json.loads(args.input.read_text()), execute=args.execute,
                           baseline_model_dir=args.baseline_model_dir, threads=args.threads, batch_size=args.batch_size)
        serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.receipt:
            descriptor = os.open(args.receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(serialized)
        print(serialized, end="")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Benchmark did not complete: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
