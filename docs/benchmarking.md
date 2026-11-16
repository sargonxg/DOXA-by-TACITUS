# Reproducible benchmarks

There are three different checks: source integrity, real-corpus analysis runs, and synthetic infrastructure load. Their evidence must remain separate. The research preview has no independent interpretation labels; it does not publish interpretation accuracy, F1 or calibrated probabilities.

## The real twenty-document benchmark

`scripts/benchmark.py` imports the bundled public-source text, creates an isolated SQLite/artifact workspace, and uses the same FastAPI run creation, scheduler, budget accounting, provider adapters and evidence-validation pipeline as the application. It does not replace model inference with prewritten findings.

```bash
# Source/provenance checks only; no model, network or inference charges.
python scripts/fetch_corpus.py --audit

# Full twenty-document lexical navigation baseline. This is not AI interpretation.
python scripts/benchmark.py --provider lexical \
  --output benchmarks/lexical-full.json --data-dir data/benchmark-lexical

# Full real-corpus local model run. Ollama must already have the chosen model.
python scripts/benchmark.py --provider ollama --model qwen3.5:4b \
  --lens-ids discourse --output benchmarks/local-full.json --data-dir data/benchmark-local

# Full real-corpus cloud model run. Configure server environment credentials first.
python scripts/benchmark.py --provider cloudflare \
  --model @cf/google/gemma-4-26b-a4b-it --budget-usd 2 \
  --lens-ids discourse --output benchmarks/cloud-full.json --data-dir data/benchmark-cloud
```

All source blocks are selected unless an explicit sampling flag is supplied. Source coverage concerns the persisted extracted text; images, scanned text without OCR and other extraction omissions remain outside that denominator. A lexical scan can finish with 100% of its input scanned while performing no semantic interpretation.

A full model run on this corpus can take substantial time: it contains over 350,000 words. Make a clearly labelled pilot to verify model compatibility and estimate speed before starting a large run:

```bash
python scripts/benchmark.py --provider ollama --model qwen3.5:4b \
  --max-documents 2 --max-blocks-per-document 8 --lens-ids discourse \
  --output benchmarks/local-pilot.json --data-dir data/benchmark-local-pilot
```

The pilot uses the first selected documents and, by default, deterministic positions spread across each document. With `--max-blocks-per-document`, `--sample-policy spread` prefers blocks of at least 120 characters and selects midpoints of equally sized source-position strata; it does not rank by an anticipated finding or label. `--sample-policy prefix` is available for an explicit first-block compatibility check. Output records selected original block IDs and full source-character denominators. A pilot is not a random or representative semantic evaluation and never sets `full_corpus_scan_complete`.

For an OpenAI-compatible endpoint such as vLLM or a managed inference service, set `DOXA_OPENAI_BASE_URL` and, where required, `DOXA_OPENAI_API_KEY` in the server environment. Use an explicit model ID and configured token prices:

```bash
python scripts/benchmark.py --provider openai --model YOUR_MODEL_ID \
  --input-price-per-million 0.10 --output-price-per-million 0.30 \
  --budget-usd 2 --lens-ids discourse \
  --output benchmarks/compatible-full.json --data-dir data/benchmark-compatible
```

Prices in this example are placeholders for accounting, not a claim about that endpoint. Provider rates, retries, reasoning, initialization, idle GPU time and storage can affect actual charges. Local token-price accounting excludes electricity and hardware. Credentials are read by the provider adapter and are not written to benchmark JSON.

## Checkpoints, resume and results

Use a separate data directory and output path for each model/configuration comparison. The output is a report-compatible snapshot containing documents and source blocks, findings, runs, usage and metrics. A neighboring `.checkpoint.json` file identifies the durable run and source/configuration signature.

Checkpoints are written after each bounded scheduler task. The rich output is refreshed at document boundaries and on normal termination. After interruption, use the same arguments, output and data directory with `--resume`. Completed tasks remain committed; the isolated CLI recovers expired/interrupted task leases. Incomplete work may be retried and may incur additional provider charges.

```bash
python scripts/benchmark.py --provider ollama --model qwen3.5:4b \
  --lens-ids discourse --output benchmarks/local-full.json --data-dir data/benchmark-local --resume
```

Resume rejects changed source snapshots, analytical configuration, or data directory. It also rejects a changed model revision when a prior provider preflight supplied a concrete revision. Remote model revisions may remain unresolved; that limitation is recorded. Do not run two workers against the same benchmark workspace/output simultaneously.

Remote providers can process up to eight shared-scheduler tasks concurrently with `--concurrency 8` (default 1). Ollama requires concurrency 1. Independent workers immediately claim another task when their current task finishes, so one slow request does not leave the other workers idle. Each task uses the application's atomic lease and shared budget accounting. Checkpoint updates are serialized and read the authoritative run after every completed task; a final read follows settlement of all workers. Concurrency is an operational setting recorded for each session, so an existing cloud run can resume with higher concurrency without changing its model/source configuration. Paused/cancelled runs and unexpected worker errors stop new claims, while already leased work settles. Errors do not become successful empty results.

Each result records:

- Real source URLs, source/content checksums, exact selected blocks and sampling flags.
- Hardware observations, selected model, available model revision/fingerprint, rubric/prompt metadata and provider readiness.
- Run/task states, source-character coverage, semantic-versus-lexical status, requests, tokens, cost basis, wall time and available process memory measurements.
- A recomputed exact quote/Unicode-offset audit against persisted source blocks.
- A deterministic qualitative-review sample: first five findings per document plus twenty additional findings sampled with seed 20261116.
- Null interpretation accuracy and `independent_human_labels: false`.

Preparing the qualitative sample is not a completed qualitative review. A reviewer must examine the source contexts, attribution and competing interpretations, record their identity or method, and publish observed errors. The tool cannot generate independent human labels by reviewing its own output.

Exit code 0 means the selected input finished with valid persisted citations. A partial run exits 1; unavailable providers/configuration failures exit 2. No provider failure is silently relabelled as a successful neutral result.

## Synthetic infrastructure load

`scripts/load_test.py` generates clearly labelled synthetic records to exercise the real Store schema, FTS/pagination path, scheduler, task persistence and expired-lease recovery. It uses the lexical pipeline with no model calls.

```bash
python scripts/load_test.py --documents 1000 --pages-per-document 10 \
  --data-dir data/load-test --output benchmarks/load-test.json
```

The output checks 1,000 document records and 10,000 **synthetic page records**, pagination without missing/duplicate document IDs, completed task counts, recovery of a simulated crashed worker's lease, and replay of a completed run without additional findings. It records measured import/scheduler times and hardware.

This does not measure parsing 10,000 real PDF pages, OCR, upload throughput, long-document inference, or interpretation quality. The generated short page records are not included in the twenty real-document showcase. Use a fresh isolated data directory for comparisons.

On execution environments whose sandbox blocks the sockets used internally by Python/AnyIO, an in-process API test may stall despite having no external HTTP request. Run the same local command through the environment's approved execution-permission mechanism; do not change TLS verification, proxy settings, or application security to work around that environment issue.

## Offline corpus regression checks

`pytest tests/test_corpus.py` verifies all twenty source snapshots, substantive corpus size, distinct institutions/types/years, content checksums, source metadata/rights, locator validity and preserved EEOC supersession notices. The source audit and these regression checks establish reproducibility/provenance invariants, not model accuracy. Synthetic unit tests elsewhere cover adversarial quotations, malformed evidence and provider failure behavior independently.
