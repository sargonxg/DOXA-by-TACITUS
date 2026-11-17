# DOXA v3 verification record

This record concerns the 0.3.0 workbench and checks performed on **16 November 2026 (America/New_York)**. Artifact timestamps use UTC and may show 17 November. It supersedes the v1 release-status summary in [verification.md](verification.md); that earlier document remains historical evidence. Software correctness, source integrity, operational model coverage, and interpretation accuracy are different claims.

## Automated and recorded checks

| Check | Observed result | Scope and limit |
| --- | --- | --- |
| Final local Python suite | **532 passed**, plus **6 subtests**, in **35.70 s** | Current application tests, including CPU NLI integrity, streaming exports, news-import recovery, cloud search parameter limits, source integrity and mocked inference. No paid model call or fresh weight download. |
| Final local Ruff pass | **All checks passed** for `doxa scripts tests` | Python sources, scripts and tests; not a proof of runtime or model correctness. |
| GitHub Actions | All four jobs passed on implementation commit `6af81f5` | Python tests, frontend build, Cloudflare typecheck/tests, and Docker build/health smoke. [Final implementation workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44020369941). The base CI environment omits optional CPU dependencies; actual CPU-model checks are recorded separately. |
| Current Cloudflare runtime | **31 tests passed** and TypeScript passed in the final local check | Includes the structured GPT-oss Responses bridge; not a production account deployment check. |
| Production Docker flow | Actual 20-source saved v3 run, canonical 5,000-source import, PDF/XLSX export, container restart and browser flow passed | No inference credentials, requests or ledger debits. See [HTTP/export receipt](../benchmarks/v3-container-verification.json) and [browser receipt](../benchmarks/v3-container-browser-verification.json). |
| Actual independent-model subset | GPT-oss completed **79/79 selected units**; **1,500 paired assessments** with the partial primary | Agreement is not accuracy. The [matrix and worked disagreements](v3-independent-review.md) preserve both raw readings, the unpaired primary gap and exact anchors. |
| Actual 5,000-source cloud backfill | All **5,000 sources attempted**, **10,686/10,759 accepted units**, **204,342/204,421 accepted decisions**, **4,927 complete sources** | Settled **partial** after two bounded recovery passes. **73 units and 79 decisions remain unresolved**. [Summary](../benchmarks/v3-news-final-summary.json), [recovery record](../benchmarks/v3-news-recovery.json) and [package](../benchmarks/v3-news/showcase.json) preserve the frozen method and inherited accounting. |
| Actual complete news export | All **5,000 source records and 204,342 assessments** retained in the full workbook/snapshot; PDF **35 pages** | Streaming export took **123.81 s**, peaking at **231,300 KiB RSS**, with no inference or database writes. XLSX **92,645,472 bytes**, snapshot **66,704,212 bytes**; [manifest](../examples/news-corpus-dashboard.manifest.json) and [resource receipt](../examples/news-corpus-dashboard.receipt.json) preserve scope, identities, outcomes and hashes. This measured CLI path is distinct from browser-export memory behavior. |
| Independent full-workbook audit | All **5,000 source JSON records, 204,342 assessment JSON records and displayed columns** match the read-only database | ZIP CRCs, grouped ordering, typed totals, credits including **585 translated sources**, and full snapshot SHA checked. Zero worksheet formulas, macros or external connections. [Audit receipt](../examples/news-corpus-dashboard.audit.json). |
| Package versions | Python project/module, frontend package/lock and Worker package/lock all **0.3.0** | Version fields aligned; dependency graphs were not changed by this review. |
| Real news source collection | **5,000/5,000** file/provenance and independent source-character audits passed | 2020–2025 Global Voices collection, not a representative sample of all news; not an interpretation benchmark. |
| Actual CPU retrieval cohort | **5,000 documents / 120,464 passages**, restart reuse **5,000/5,000**; returned search quotations validated | Approximately 49.91 active indexing minutes across recorded attempts and 0.59GiB peak index-driver RSS. These measurements do not establish ranking accuracy. See [the full resource artifact](../benchmarks/cpu-semantic-5000-summary.json). |
| Actual CPU NLI cohort | **20/20 whole articles**, **60 source windows**, **240 hypothesis scores** and **537 original-block references** checked | **247.102 s**, including loading, and **2.038 GiB** peak process RSS. The [workbook](../examples/local-cpu-nli-20.xlsx) preserves full text and scores; no prejudice probability or accuracy is established. |
| V3 browser workspace | Seven recorded Chromium checks passed; no page errors | Synthetic 5,000-source paging/filters, exact context/review, reviewed memory, actual XLSX download, transfer consent, responsive 390px layout, and bounded requests. |
| Final actual news workspace | Seven recorded Chromium checks passed against the settled **5,000-source** run; zero errors and zero analysis starts | Fixed read-only source transaction, in-memory query cache, desktop/390px, exact evidence, cursor paging, trajectory gates and Import navigation. [Public receipt](../benchmarks/v3-browser-news-verification.json); screenshots preserve the true partial status and source credit. |
| Browser import recovery | One 11,535,217-byte two-part fixture; only unfinished part 2 resent after refresh; one document and one logical batch | Real transport/recovery of a synthetic PDF; not meaningful-prose throughput or OCR validation. |
| Browser batches and scope | Two three-member imports produced five distinct sources; each delta category counted one | Earlier version inspection, batch scoping, and no mobile overflow. A separate import added source 5,001 without losing the 5,000-source corpus sentinel; Analyze used the updated 5,001-source query. |
| Browser verifier workflow | Recorded consent, budget, eligibility, separate comparison, partial selection, context and mobile checks passed | Mocked model responses; no quality improvement is established by agreement. |
| Final provider and CPU browser checks | Build/typecheck, custom-provider pricing and source-only CPU flows passed; zero page errors | Missing/invalid remote rates are blocked, confirmed local mode remains usable without rates, and the CPU scorer can use a source paragraph with no prior assessment. No paid inference. Four explanation pages also passed at 390px with all images loaded and no horizontal overflow. |
| Saved v3 package restore | **34 focused restore/integration cases passed**, included in the final Python suite | Source hashes/anchors, result types, unit completeness, bounded archive parsing and replay are checked before publishing saved results. Historical usage does not charge a new inference ledger. |
| Actual fresh 5,000-source restore | **5,000 sources, 204,342 assessments, 10,759 unit states and 3,860,336 exact references** match; second restore is idempotent | **262.17 s** for import, restore, complete payload audit, replay and integrity checks, about **259 MiB peak RSS** and **2.24 GB scratch**. Same partial status/history retained; no network/model attempts, new reservation, ledger debit or CPU-model load. Temporary data cleaned. [Full receipt](../benchmarks/v3-news-restore-verification.json). |

The Actions records are public: [first v3 workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44002850772), [source follow-up](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44004146925), and [current merged runtime](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44012559165). Every Python, frontend, Cloudflare and container job concluded `success`. The latest record checks `542e30743bf84cca16767ed946d15d0eb873cb97`.

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

The source collection is complete, and its separate cloud model-analysis run has settled **partial** after attempting all 5,000 sources. It accepted **10,686/10,759 units (99.32%)** and **204,342/204,421 decisions (99.96%)**, leaving **73 units and 79 decisions incomplete**. The [institutional v3 summary](../benchmarks/v3-institutions-final-summary.json) records **613/616** accepted units and partial status; the [four-source political-framing summary](../benchmarks/v3-political-summary.json) records **59/60** and partial status. The [whole-article local generation benchmark](../benchmarks/v3-local-news-summary.json) stopped as a bounded, cancelled trial with **2/20 complete articles and 8/115 accepted units**. The separate NLI profile completed all 20 articles. These are different workloads. A saved package is loadable only when its recorded status is completed/partial and its source/anchor checks pass; loading it does not erase missing decisions or establish accuracy.

News packaging rechecked **3,860,336 exact source references**. Cumulative application-accounted inference was **$14.1058649**, including inherited historical usage once, with **158.39 active benchmark minutes** across recorded sessions and operational changes. Provider invoice cost remains unknown. No inference tasks or budget reservations remained open when the temporary gateway's workers.dev and preview routes were [disabled](../benchmarks/v3-gateway-cleanup.json); the public TACITUS website was preserved.

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
- **Scale and latency:** the real 5,000-source cloud backfill establishes the recorded partial processing coverage. It does not establish OCR speed, completed 5,000-source local generation or a universal 16GB laptop throughput guarantee. Full browser exports can materialize their selected data; the separate streaming CLI supplies the measured large-export path.
- **Provider behavior and cost:** managed weights may lack an immutable revision; availability, structured decoders, pricing and rate limits can change. Some remote usage can be billed without a response. Conservative application accounting is separate from the vendor invoice and platform/storage charges.
- **Disk capacity:** a 32GB execution workspace filled during a source-fork copy with model artifacts, Docker cache and SQLite WAL present. The fork stopped safely; partial-copy replay is tested. Budget disk headroom for originals, immutable versions, outcomes, archives, indexes and build cache, and verify checkpoints before cleanup.
- **Deployment:** CI Docker and local Worker emulation do not establish a production account's Access/D1/R2/Queues configuration, retention or recovery operations. Follow the [deployment guide](deployment.md).
- **Evidence scope:** the news collection is one publisher, and institutional examples are purposive. Text extraction omits declared non-text material. A saved partial run retains those gaps; neither loading a showcase nor generating polished reports upgrades it to a fully validated corpus.
