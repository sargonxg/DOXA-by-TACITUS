# Reproducible benchmarks

Use **`scripts/benchmark_v3.py` for current contextual analysis**. It selects complete real source documents, freezes their identities and hashes, and uses the application's v3 run creation, durable scheduler, provider adapters, budget reservations and source-evidence validation. The older `scripts/benchmark.py` commands below reproduce the historical v1 contract, including its passage pilots.

Keep four kinds of evidence separate: source integrity; generative criterion outcomes and processing coverage; CPU retrieval/NLI functionality; and synthetic infrastructure checks. None supplies independent human reference labels. Interpretation accuracy, F1, calibrated prejudice probabilities and retrieval relevance accuracy remain unmeasured.

## Current v3 selections and recorded results

| Selection or tool | Scope and current evidence |
| --- | --- |
| `--collection news` | Always the complete frozen **5,000 Global Voices articles**, with every extracted text block selected. `--limit` is rejected. Selecting all sources does not mean that every requested decision has finished; read the [current news summary](../benchmarks/v3-news-final-summary.json). |
| `--collection news-sample --limit 20` | Twenty **whole articles**, selected deterministically at equal positions across the frozen source order. No keyword or expected label selects them. This is a scoped pilot, not a representative sample of all journalism or a full 5,000-article run. The [recorded Qwen local pilot](../benchmarks/v3-local-news-summary.json) remains incomplete after the recorded bounded attempts; its [observations](../benchmarks/v3-local-news-observations.json) retain active-session timing, scheduling failures, restarts and resource limitations. |
| `--collection institutions` | All **20 institutional documents**, over 350,000 words. The [recorded Gemma run](../benchmarks/v3-institutions-final-summary.json) is finished **partial**: **613/616 source units**, **11,701/11,704 decisions**, **18/20 documents** fully assessed. Three decisions remain unresolved. |
| `--collection political --lens-ids political-framing` | Four complete sources, `us01`, `us03`, `us04`, `uk07`. The [recorded packet](../benchmarks/v3-political-summary.json) is finished **partial**: **59/60 units**, **358/360 decisions**, **3/4 documents** complete. These purposive examples differ in genre/date; they are not a matched fairness comparison. |
| MiniLM CPU retrieval | The [canonical 5,000-article index](../benchmarks/cpu-semantic-5000-summary.json) is complete: **120,464 passages** and restart/reuse checks. Approximately **49.91 active indexing minutes** combines multiple attempts, interrupted work, cached restarts and a canonical-source refresh. It is not a single cold run, generative analysis or ranking-quality evaluation. |
| Independent cloud verifier | A [separate recorded model comparison](v3-independent-review.md) fully assessed **79 selected units** and produced **1,500 paired criterion assessments**. These are a subset of the institutional run. Agreement and disagreement are not accuracy; the verifier does not fill the primary run's unresolved decisions or adjudicate them. |

The [benchmark artifact index](../benchmarks/README.md) identifies source audits, current summaries, portable packages and historical runs. Live full-news and local summaries can change while work continues; their saved status, timestamps, source selection and denominators take precedence over an elapsed process or a document count described as “settled.”

## Start a new whole-source run

Install the application and chosen inference runtime first; see the [README](../README.md) and [model profiles](models-v3.md). Ollama must already have the explicitly chosen model. Optional MiniLM/DeBERTa bundles are installed separately; no benchmark silently downloads weights.

Use a **fresh local data directory, output file and package directory for each comparison**. These benchmark workspaces must not use `DOXA_CLOUD_STATE_URL` or a live production database. Give each run sole ownership of its state; do not start the app's background workers against that same directory.

`--env-file` loads server configuration before provider initialization. Keep it in an ignored local file such as `.env.benchmark`; credentials belong there or in the server environment, never in command arguments, snapshots or a committed report. See [.env.example](../.env.example) and [deployment configuration](deployment.md). Do not print the file to reproduce a run.

The local example below uses **`--budget 0`** and makes no paid inference calls. It still consumes CPU, memory, disk and electricity. The cloud examples **explicitly request paid remote inference and transfer selected source text to the configured provider**: selecting `--provider cloudflare` is the CLI's affirmative remote-text setting. Start with a scoped run and inspect its measured throughput/cost before committing a larger budget.

```bash
# New local pilot: all text of 20 articles, six explicit lenses, CPU concurrency 1.
python scripts/benchmark_v3.py --collection news-sample --limit 20 \
  --provider ollama --model qwen3.5:4b --concurrency 1 --budget 0 \
  --lens-ids discourse ihra jda nexus anti-muslim-hostility anti-muslim-racism \
  --env-file .env.benchmark \
  --data-dir data/my-v3-local20 --output benchmarks/my-v3-local20-summary.json \
  --package-dir benchmarks/my-v3-local20

# New paid institutional run: all 20 complete source texts.
python scripts/benchmark_v3.py --collection institutions \
  --provider cloudflare --model @cf/google/gemma-4-26b-a4b-it \
  --concurrency 4 --budget 2 \
  --lens-ids discourse ihra jda nexus anti-muslim-hostility anti-muslim-racism \
  --env-file .env.benchmark \
  --data-dir data/my-v3-institutions --output benchmarks/my-v3-institutions-summary.json \
  --package-dir benchmarks/my-v3-institutions

# New paid full-news run: all 5,000 articles; the budget does not promise completion.
python scripts/benchmark_v3.py --collection news \
  --provider cloudflare --model @cf/google/gemma-4-26b-a4b-it \
  --concurrency 4 --budget 10 \
  --lens-ids discourse ihra jda nexus anti-muslim-hostility anti-muslim-racism \
  --env-file .env.benchmark \
  --data-dir data/my-v3-news --output benchmarks/my-v3-news-summary.json \
  --package-dir benchmarks/my-v3-news

# New paid political-framing packet: six descriptive criteria, four whole sources.
python scripts/benchmark_v3.py --collection political \
  --provider cloudflare --model @cf/google/gemma-4-26b-a4b-it \
  --concurrency 4 --budget 1 --lens-ids political-framing \
  --env-file .env.benchmark \
  --data-dir data/my-v3-political --output benchmarks/my-v3-political-summary.json \
  --package-dir benchmarks/my-v3-political
```

The v3 driver supports `cloudflare` and `ollama`; generic OpenAI/RunPod examples later on this page belong to the historical v1 driver. The v3 default rubric list contains five packs; reproducing the recorded **six-pack** institutional/local inquiry requires the explicit `--lens-ids` above. The political pack is a different inquiry. Record `--question` and `--persona` when changing either; these changes require a new analytical run.

The driver defaults to four workers and permits **1–48 remote scheduler workers** through `--concurrency`. Ollama requires `--concurrency 1`. This ceiling is an isolated CLI scheduling option, not a promised provider quota or recommended default. Higher concurrency can increase request pressure, reservations, retries and memory. The normal application uses four workers per remote run; the shipped Cloudflare deployment has queue-consumer concurrency one and a single routed container. Those are separate execution paths. A local benchmark using Cloudflare inference is **not** a throughput benchmark of deployed Queues, D1 or R2. See [deployment](deployment.md).

## What coverage and outcomes mean

Every selected document contributes all its extracted source text to the v3 unit builder; the model does not silently substitute the first paragraphs. Source-unit size, neighboring context and provider packing are frozen in the run. The recorded CPU and cloud profiles can use different packing/unit settings, so their pipeline differences must accompany a comparison.

Classification first requests an explicit result for **every unit × selected criterion**: `meets_criterion`, `does_not_meet`, `uncertain` or `not_applicable`. Positive/uncertain classifications then require the bounded detailed evidence/attribution step. The detailed re-evaluation can disagree with the initial classification, and both remain in the trace. Explicit negative/not-applicable outcomes may be stored compactly with source-derived anchors and without a model-written rationale. They are not inferred from an empty response.

An accepted unit has every requested criterion outcome. A partial/failed/pending unit can retain valid decisions already committed while its other decisions remain unresolved. Do not promote those partial rows to full coverage, turn failures into negative findings, or count a settled document as complete. A descriptive or boundary criterion being met is not a prejudice indicator. Quotation, reporting, hypothetical examples, counter-speech, speaker stance and document stance remain separate.

Coverage concerns persisted **extracted text**. Images, extraction omissions and scanned text without successful OCR are outside that denominator. Exact Unicode quotation checks establish source anchoring, not interpretive correctness, factual truth or complete document comprehension. Review [the methodology](methodology.md) and [political-framing limits](political-bias-methodology.md) before interpreting rates.

The output summary records the source scope, run/model configuration, statuses, accepted decisions, usage, budget-accounted spend, elapsed sessions and errors. The durable database/artifacts and neighboring `.checkpoint.json` retain recovery state; keep them together. A portable v3 package contains `showcase.json` and checksum-listed, bounded gzip JSONL parts with explicit outcomes, source versions and unit states. Saved-demo restoration accepts settled `completed` or `partial` archives, rechecks their anchors and makes no inference calls or new budget debit. An active/paused archive is not a completed demonstration.

## Resume the same method; preserve changed-source history

Repeat the original command with the **same paths and analytical arguments**, adding `--resume`:

```bash
python scripts/benchmark_v3.py --collection news-sample --limit 20 \
  --provider ollama --model qwen3.5:4b --concurrency 1 --budget 0 \
  --lens-ids discourse ihra jda nexus anti-muslim-hostility anti-muslim-racism \
  --env-file .env.benchmark \
  --data-dir data/my-v3-local20 --output benchmarks/my-v3-local20-summary.json \
  --package-dir benchmarks/my-v3-local20 --resume
```

Committed outcomes remain durable; unresolved work can be retried and incur further charges. Checkpoints and summaries update after settled tasks. Resume verifies the original signature, state directory, source identities/order/hashes, model/provider, inquiry/persona and selected method. A previously concrete provider revision must still match; unresolved cloud revisions remain a reproducibility limit. Exit 0 means completed selected input; exit 1 means it did not complete successfully; exit 2 signals configuration/readiness/driver failure, and an interruption exits 130. Inspect the saved state as well as the exit code.

Budget and concurrency are operational settings, recorded per session rather than silently redefining the inquiry. A budget supplied on resume is the **total run cap**, not an additional allowance. Increasing it deliberately authorizes further work subject to the workspace's other caps. A compatible unselected-lens extension is allowed only when the saved compatibility receipt proves the core engine, response contract and selected rubric packs unchanged; changing a selected criterion or model method requires a new run.

There is one narrow parent-metadata exception: a `news-sample` run may record changed parent manifest/collection digests when **every selected article identity, content hash, order, path and other selection setting is unchanged**. The original selection/signature remains intact and `parent_provenance_updates` records the change. This exception does not authorize altered selected articles or a changed complete-news cohort.

For a changed full-news cohort, preserve the stopped original run and use the explicit [cohort-fork helper](../scripts/fork_benchmark_v3.py), with a reason and acknowledgement that workers are stopped. It checks the original receipt, creates a separate frozen run and reuses assessments only for identical source versions. Changed versions receive fresh work; old documents, results and source indexes remain historical. The helper does not start inference. Resume the new checkpoint deliberately to request additional provider work.

The recorded full-news fork followed a citation-link metadata cleanup for `gv-816538`, which was still unassessed. Text/locators were checked separately, but the changed canonical block hash still required a new immutable source version and an audited full-cohort fork. Inspect `fork_provenance` and its changed/reused-source receipts rather than replacing the old checkpoint's selection or hiding the original attempt.

Fork cost and time are **cumulative provenance**. The new run inherits historical token/request usage and budget-accounted spend; it does not debit those historical requests to the monthly ledger twice. Do not add the old run's usage to an already inherited new-run total. Preserve `historical_sessions`, `historical_elapsed_seconds`, current `elapsed_seconds` and their `total_active_analysis_seconds`; active processing time excludes downtime and is not a single uninterrupted wall-clock throughput measurement. A source audit, refreshed index or renamed output file cannot reset analytical cost or establish new inference coverage.

## Costs, verification and CPU tools

The configured rates checked on 16 November 2026 are **$0.10 input / $0.30 output per million tokens for Gemma 4**, **$0.35 / $0.75 for GPT-OSS-120B**, and **$0.20 / $0.30 for GPT-OSS-20B**. Recheck [the provider catalogue](https://developers.cloudflare.com/workers-ai/models/) before a new run. Saved token receipts/rate profiles and `budget_accounted_usd` are application accounting, not a vendor invoice. A failed request can still be billed: conservative reservations may count as spend when usage is unknown. Keep unknown billing visible even when a reservation-backed spend figure is available.

Each run has its total cap; the local workspace additionally defaults to a configurable **$20 monthly variable-spend cap shared by its runs**. Separate benchmark data directories have separate ledgers, so their caps are not a combined account limit. Neither cap controls the full vendor bill, subscriptions, inference performed outside this workspace, container/GPU idle time, storage or electricity. Set provider/account controls separately. A `$10` run budget is permission to spend up to that application's cap, not a guarantee that 5,000 articles will finish.

Independent verification is a separately budgeted remote run, capped at **$2**. It requires explicit `--allow-remote-text`, selects all indicator-flagged units plus a deterministic document-stratified sample of other accepted units, and preserves both models' raw outcomes. See [the actual 79-unit comparison](v3-independent-review.md) for selection, packing differences, category changes and reading questions. Model agreement does not supply human accuracy labels or resolve missing primary decisions.

```bash
# Optional paid second-model subset of a run you have already completed/settled.
python scripts/verify_v3.py --data-dir data/my-v3-institutions \
  --run-id PRIMARY_RUN_ID --allow-remote-text --budget 1.5 --concurrency 4 \
  --env-file .env.benchmark --output benchmarks/my-v3-verifier-summary.json \
  --package-dir benchmarks/my-v3-verifier

# CPU retrieval after importing a project and explicitly installing pinned MiniLM.
python scripts/semantic_index.py build PROJECT_ID --data-dir data/my-workspace
python scripts/semantic_index.py search PROJECT_ID --data-dir data/my-workspace \
  --text 'religious discrimination and equal rights'
```

MiniLM indexes source windows for retrieval; it does not generate criterion assessments. Its 5,000-article timing above includes cached work across restarts; do not call the final refresh's short duration the full indexing time. Run heavy CPU indexing and Qwen generation sequentially when they contend for the same CPU quota, and distinguish driver RSS from the separately hosted Ollama process. The incomplete local Qwen pilot is not evidence of a completed 20-article or 5,000-article local generative benchmark.

The [whole-article DeBERTa resource runner](../scripts/benchmark_cpu_nli.py) completed **20/20 original articles**, producing **60 source windows and 240 independent hypothesis pairs** in **247.102 seconds**, including the first model load in that process. Peak process RSS was **2.038GiB**; no GPU, weight download or paid provider call was used. The [summary](../benchmarks/cpu-nli-20-news-summary.json), [complete scores](../benchmarks/cpu-nli-20-news-scores.jsonl.gz) and [integrity audit](../benchmarks/cpu-nli-20-news-integrity.json) preserve full source-token coverage, logits and exact offsets; all 537 original-block references were checked. The [six-sheet workbook](../examples/local-cpu-nli-20.xlsx) and [checksum receipt](../examples/local-cpu-nli-20.receipt.json) expose those frozen results without requesting further inference. This four-hypothesis CPU baseline is separate from the 19-criterion generative inquiry. Scores remain uncalibrated: not-entailment is not contradiction, a winning prejudice category or an author judgment, and completed processing does not establish interpretation accuracy. See [CPU setup and model limits](models-v3.md).

## Historical v1 commands and passage pilots

The remainder of this section reproduces the earlier pipeline. Its findings, block coverage, concurrency limits and passage-sampling flags are **not the v3 unit/criterion contract**. Legacy observations remain unclassified under v3 unless separately reassessed.

### Historical twenty-document benchmark

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

### Historical v1 checkpoints, resume and results

Use a separate data directory and output path for each model/configuration comparison. The output is a report-compatible snapshot containing documents and source blocks, findings, runs, usage and metrics. A neighboring `.checkpoint.json` file identifies the durable run and source/configuration signature.

Checkpoints are written after each bounded scheduler task. The rich output is refreshed at document boundaries and on normal termination. After interruption, use the same arguments, output and data directory with `--resume`. Completed tasks remain committed; the isolated CLI recovers expired/interrupted task leases. Incomplete work may be retried and may incur additional provider charges.

```bash
python scripts/benchmark.py --provider ollama --model qwen3.5:4b \
  --lens-ids discourse --output benchmarks/local-full.json --data-dir data/benchmark-local --resume
```

Resume rejects changed source snapshots, analytical configuration, or data directory. It also rejects a changed model revision when a prior provider preflight supplied a concrete revision. Remote model revisions may remain unresolved; that limitation is recorded. Do not run two workers against the same benchmark workspace/output simultaneously.

For this **historical v1 driver**, remote providers can process up to eight shared-scheduler tasks concurrently with `--concurrency 8` (default 1). Ollama requires concurrency 1. Independent workers immediately claim another task when their current task finishes, so one slow request does not leave the other workers idle. Each task uses the application's atomic lease and shared budget accounting. Checkpoint updates are serialized and read the authoritative run after every completed task; a final read follows settlement of all workers. Concurrency is an operational setting recorded for each session, so an existing cloud run can resume with higher concurrency without changing its model/source configuration. Paused/cancelled runs and unexpected worker errors stop new claims, while already leased work settles. Errors do not become successful empty results.

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
