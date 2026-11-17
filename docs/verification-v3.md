# DOXA v3 verification record

This record concerns the 0.3.0 workbench and checks performed on **16 November 2026 (America/New_York)**. Artifact timestamps use UTC and may show 17 November. It supersedes the v1 release-status summary in [verification.md](verification.md); that earlier document remains historical evidence. Software correctness, source integrity, operational model coverage, and interpretation accuracy are different claims.

## Automated and recorded checks

| Check | Observed result | Scope and limit |
| --- | --- | --- |
| Final local Python suite | **496 passed**, plus **6 subtests**, in **75.74 s** (then two focused resume regressions passed) | Normal application tests, including source integrity and mocked inference. No paid model call or fresh weight download. |
| Final local Ruff pass | **All checks passed** for `doxa scripts tests` | Python sources, scripts and tests; not a proof of runtime or model correctness. |
| GitHub Actions | All four jobs passed on `0b8e175`, `1df3c924` and `5f68dea` | Python tests, frontend build, Cloudflare typecheck/tests, and Docker build/health smoke. Later runtime follow-ups have focused local checks and need their own CI run. |
| Current Cloudflare runtime | **31 tests passed** and TypeScript passed, recorded by the runtime owner | Includes the structured GPT-oss Responses bridge; not a production account deployment check. |
| Production Docker flow | Actual 20-source saved v3 run, canonical 5,000-source import, PDF/XLSX export, container restart and browser flow passed | No inference credentials, requests or ledger debits. See [HTTP/export receipt](../benchmarks/v3-container-verification.json) and [browser receipt](../benchmarks/v3-container-browser-verification.json). |
| Actual independent-model subset | GPT-oss completed **79/79 selected units**; **1,500 paired assessments** with the partial primary | Agreement is not accuracy. The [matrix and worked disagreements](v3-independent-review.md) preserve both raw readings, the unpaired primary gap and exact anchors. |
| Package versions | Python project/module, frontend package/lock and Worker package/lock all **0.3.0** | Version fields aligned; dependency graphs were not changed by this review. |
| Real news source collection | **5,000/5,000** file/provenance and independent source-character audits passed | 2020–2025 Global Voices collection, not a representative sample of all news; not an interpretation benchmark. |
| Actual CPU retrieval cohort | **5,000 documents / 120,464 passages**, restart reuse **5,000/5,000**; returned search quotations validated | Approximately 49.91 active indexing minutes across recorded attempts and 0.59GiB peak index-driver RSS. These measurements do not establish ranking accuracy. See [the full resource artifact](../benchmarks/cpu-semantic-5000-summary.json). |
| V3 browser workspace | Seven recorded Chromium checks passed; no page errors | Synthetic 5,000-source paging/filters, exact context/review, reviewed memory, actual XLSX download, transfer consent, responsive 390px layout, and bounded requests. |
| Browser import recovery | One 11,535,217-byte two-part fixture; only unfinished part 2 resent after refresh; one document and one logical batch | Real transport/recovery of a synthetic PDF; not meaningful-prose throughput or OCR validation. |
| Browser batches and scope | Two three-member imports produced five distinct sources; each delta category counted one | Earlier version inspection, batch scoping, and no mobile overflow. A separate import added source 5,001 without losing the 5,000-source corpus sentinel; Analyze used the updated 5,001-source query. |
| Browser verifier workflow | Recorded consent, budget, eligibility, separate comparison, partial selection, context and mobile checks passed | Mocked model responses; no quality improvement is established by agreement. |
| Saved v3 package restore | **34 focused restore/integration cases passed**, included in the final Python suite | Source hashes/anchors, result types, unit completeness, bounded archive parsing and replay are checked before publishing saved results. Historical usage does not charge a new inference ledger. |

The Actions records are public: [first v3 workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44002850772) and [follow-up workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44004146925). Every Python, frontend, Cloudflare and container job concluded `success` in both records. Their exact commits are `0b8e1759071aea8f5e302073857bd21bd9217d4f` and `1df3c924e39ba3de455ea186fb8b11a81618ad3c`.

The browser reports inspected in this verification were produced by the checked-in browser scripts under `/tmp/doxa-v3-browser-checks`, `/tmp/doxa-browser-resume-v3`, `/tmp/doxa-browser-batches-v3`, `/tmp/doxa-browser-import-scope-v3`, and `/tmp/doxa-browser-verification-v3`. These are execution artifacts, not independent annotations. The normal pytest suite does not automatically execute those browser scripts, load actual CPU model weights, or run paid provider benchmarks. CI does not currently run Ruff or the manual browser suite; those have separate verification paths.

## Important failure modes now covered

Completion requires successful tasks, exact scheduled source coverage, and every expected contextual unit accepted. A failed task cannot become a completed run because its error message is empty. `TimeoutError()` retains a nonempty diagnostic. Historical blank-error failures, missing/pending/partial/failed units, and stale full-coverage counters are explicit regression cases. “Settled documents” and “complete documents” remain separate quantities.

Unknown remote billing remains unknown in reported provider usage. Its conservative reservation is charged exactly once in the durable ledger. A transient response failure followed by valid recovery can finish; unknown usage alone is not treated as budget exhaustion. Genuine run and monthly caps still stop before the next inference request, and explicit cap increases permit resume. Replay and crash recovery retain spend and accepted observations.

A run stores selected lens snapshots and their checksum. Changed selected criteria pause with `paused_method_changed` before inference; use a new run for the changed method. Adding an unselected lens does not change the frozen method. Benchmark resume rejects changed engine/schema, selected rubric, inquiry, source versions, data directory or concrete model revision. A news sample may accept audited parent-manifest metadata updates only when every selected article identity/hash is unchanged; a changed complete-news cohort requires an explicit fork. Operational concurrency/budget changes are recorded separately.

Workspace tests cover 5,000 synthetic source records with server cursors and shared scope, stale/cross-scope cursor rejection, distinct boundary/descriptive/indicator outcomes, source/content views, and D1 parameter limits. Trajectory tests retain incomplete/uncertain denominators, quoted/reporting/hypothetical distinctions, duplicate groups, unknown dates and review scope. Spike tests include unchanged rates under doubled volume and suppression when source composition changes.

Reviewed-learning tests preserve original model payloads and append human corrections, reject invented/cross-project evidence, exclude heldout stories from memory, freeze snapshot membership, bound/group-diversify retrieval, require paired candidate evaluation and explicit human activation, and preserve rollback. This workflow changes approved example context; it does not train model weights or allow an agent to approve its own claims.

The source-cohort fork tests use four synthetic sources with one changed immutable version. Only the three exact versions reuse outcomes; the changed version receives no reused assessment. The old run remains unchanged, partial peers remain recoverable, committed copy batches survive lost acknowledgements, and historical billing is inherited without debiting the monthly ledger twice. Original checkpoint signatures are verified against their original source selection. Tiny floating-point aggregate reserve residue is tolerated only when no durable request reservation remains open. The helper requires an explicit stopped-worker acknowledgement and never starts inference.

Strict verifier tests reject coerced consent, boolean/malformed/nonfinite/overflowing/out-of-range budgets, and invalid run IDs before provider/run creation. Remote consent must be a real boolean, and verification budget must be finite and positive with a $2 cap. Compact negative assessments can now be reviewed and approved after their missing quotation fields are reconstructed from exact frozen source anchors; corrupt anchors, supplied wrong quotations and stale approvals remain rejected. Original model payloads are not rewritten by review.

## Model evidence and current boundaries

The [paired pilot](../benchmarks/v3-pilot-comparison.json) used two real `us01` units, 7,701 scored characters and 38 requested decisions. The final two-stage profile accepted 38/38 structured decisions versus 32/38 in the earlier one-pass profile, with lower measured token use. This is operational evidence from one selected source, not an accuracy comparison. See [v3 research](research-v3.md) for profiles, sources, raw-diagnostic scope and limitations.

The source collection is complete; its large model-analysis run is a separate process. At this verification checkpoint, the news cloud benchmark remained incomplete after a successful, audited continuation onto the corrected immutable source cohort. The [institutional v3 summary](../benchmarks/v3-institutions-final-summary.json) records **613/616** accepted units and partial status; the [four-source political-framing summary](../benchmarks/v3-political-summary.json) records **59/60** and partial status. The [whole-article local benchmark](../benchmarks/v3-local-news-summary.json) remained in progress. These are dated checkpoints; consult the current summaries for later results. No incomplete run is described as a complete 5,000-document analysis. A saved package is loadable only when its recorded status is completed/partial and its source/anchor checks pass; merely finding a package file on disk is not evidence of completion.

The CPU resource monitor observed an Ollama runner and ancillary processes inside a 16GiB cgroup. Its sampled RSS is a lower bound, includes a documented process-recognition correction, and excludes any assertion about whole-corpus local completion time. [Model documentation](models-v3.md) distinguishes actual runtime/index measurements from mocked tests and uncalibrated NLI scores.

No independently adjudicated reference set exists for the supplied model outcomes. Accuracy, F1, fairness, causal attribution, private beliefs, and calibrated probabilities are not established. Exact source anchors verify provenance rather than the interpretation. Heldout source groups are reserved for later independent evaluation; their existence does not itself supply gold labels.

## Reproduce the software checks

Install the development environment using the [README](../README.md), including the system libraries needed for report rendering. From the repository root:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check doxa scripts tests
python scripts/fetch_corpus.py --audit
python scripts/fetch_news_corpus.py --audit
```

The independent `python scripts/fetch_news_corpus.py --audit-sources` additionally needs the frozen publisher source cache used by collection; it does not substitute a fresh changing website for the captured source. Missing source cache must remain an explicit limitation.

For the key completion/budget/method-freeze cases:

```bash
python -m pytest -q tests/test_v3_completion.py tests/test_v3_budget.py \
  tests/test_benchmark_v3_resume.py tests/test_contextual.py
```

In `frontend/`, run `npm ci` then `npm run build`. In `infra/cloudflare/`, run `npm ci`, `npm run typecheck`, then `npm test` with `WRANGLER_SEND_METRICS=false`. The workflow uses Python 3.12 and Node 24. A matching local container smoke is `docker build --tag doxa:verify .`, followed by starting the image and requesting `/api/health`. The recorded production-image flow additionally checked both actual demo collections, PDF/XLSX export, restart persistence and a desktop/mobile browser flow. A successful health response alone establishes startup, not a complete deployment or successful inference.

The browser scripts require Playwright and system Chromium at their configured executable path. Build the frontend first, then run them from the root:

```bash
python tests/browser_v3.py
python tests/browser_upload_resume.py
python tests/browser_batches.py
python tests/browser_import_scope.py
python tests/browser_verification.py
```

Each creates isolated temporary data and labels synthetic fixtures. On environments that block the sockets AnyIO uses internally, use the environment's approved local execution mechanism; do not disable TLS or application authentication.

Real inference is a separate explicit operation, can transfer source text and incur charges, and is not needed to reproduce unit/CI checks. The contextual driver is `scripts/benchmark_v3.py` with `--collection news|news-sample|institutions|political`; the older `scripts/benchmark.py` reproduces historical v1 runs. The `news` collection always selects the complete frozen 5,000-source cohort, while `news-sample` selects complete articles. Use [benchmark instructions](benchmarking.md), a dedicated data directory, explicit provider/model and budget, and the same frozen arguments plus `--resume` for recovery. Do not run duplicate owners against an active benchmark workspace.

## Remaining release risks

- **Interpretive validation:** independently trained reviewers, adjudication, disagreement analysis, source-separated evaluation and slice-specific error reporting remain future quality work.
- **Scale and latency:** synthetic 5,000-source UI/query checks and an audited real collection do not establish completed large-corpus inference, OCR speed, or a universal 16GB laptop throughput guarantee.
- **Provider behavior and cost:** managed weights may lack an immutable revision; availability, structured decoders, pricing and rate limits can change. Some remote usage can be billed without a response. Conservative application accounting is separate from the vendor invoice and platform/storage charges.
- **Disk capacity:** a 32GB execution workspace filled during a source-fork copy with model artifacts, Docker cache and SQLite WAL present. The fork stopped safely; partial-copy replay is tested. Budget disk headroom for originals, immutable versions, outcomes, archives, indexes and build cache, and verify checkpoints before cleanup.
- **Deployment:** CI Docker and local Worker emulation do not establish a production account's Access/D1/R2/Queues configuration, retention or recovery operations. Follow the [deployment guide](deployment.md).
- **Evidence scope:** the news collection is one publisher, and institutional examples are purposive. Text extraction omits declared non-text material. A saved partial run retains those gaps; neither loading a showcase nor generating polished reports upgrades it to a fully validated corpus.
