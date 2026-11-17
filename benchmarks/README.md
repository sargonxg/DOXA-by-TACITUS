# Recorded runs and reproducible checks

These artifacts distinguish source integrity, software behavior, model coverage and interpretation quality. **No independent human gold labels, accuracy or F1 scores are supplied.** Read the `status`, `progress`, `unit_states`, model profile and source selection before comparing counts.

## v0.5 release qualification

The [completed clean-runner workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44168398715) restored all 5,000 sources and 204,342 retained assessments, checked 3,860,336 exact references, replayed idempotently and generated a complete persistent XLSX. Whole logical-table/object fingerprints and post-report source/model state remained unchanged. Zero new provider requests, model loads, budget reservations or spend occurred.

[Exact replay receipt](v05-evidence-replay.json) · [exact report receipt](v05-report-job-5000.json) · [tested revision, measurements and limits](../docs/verification-v05.md#completed-5000-source-release-qualification). The combined proof took 554.19 seconds, including 332.16 seconds for report capture/render/verification/cleanup. The underlying inference remains partial with 79 missing decisions and no independent human reference labels; this is a software/provenance qualification.

## Contextual v3 results

| Artifact | Actual scope and result |
| --- | --- |
| [5,000-article cloud backfill](v3-news-final-summary.json) · [portable package](v3-news/showcase.json) · [recovery record](v3-news-recovery.json) | Gemma 4, six lenses, all frozen source texts attempted. Settled **partial**: **10,686/10,759 units accepted (99.32%)**, **204,342/204,421 explicit criterion decisions (99.96%)**, **4,927/5,000 complete documents**. **73 units and 79 decisions remain unresolved** after two bounded recovery passes. Packaging rechecked **3,860,336 source references**. Cumulative application accounting **$14.1058649** and **158.39 active benchmark minutes** include inherited provenance once; neither is a vendor-invoice or portable-throughput promise. |
| [Twenty institutional sources](v3-institutions-final-summary.json) · [portable package](v3-institutions/showcase.json) | Gemma 4, six lenses, full selected source texts. **613/616 units accepted**, **11,701/11,704 explicit criterion decisions**, **18/20 complete documents**. Finished **partial**; three decisions remain unresolved. |
| [Political-framing packet](v3-political-summary.json) · [portable package](v3-political/showcase.json) | Four full institutional documents, six descriptive framing criteria. **59/60 units accepted**, **358/360 decisions**, **3/4 complete documents**. Finished **partial**; two unresolved decisions. |
| [CPU passage retrieval, 20 sources](cpu-semantic-20-summary.json) | All twenty institutional documents indexed into **9,249 passages**. Three example searches returned exact-source-validated hits. Resumed timing and nonindexable formatting inputs are disclosed; ranking accuracy is unmeasured. |
| [CPU passage retrieval, 5,000 articles](cpu-semantic-5000-summary.json) | The current canonical cohort indexed into **120,464 passages**; all **5,000** bundles were reused after restart and returned quotations were checked against source text. **49.91 minutes** total active indexing across recorded attempts, approximately **0.59GiB** peak index-driver RSS. These are resource measurements, not ranking accuracy. |
| [Whole-article CPU NLI, 20 articles](cpu-nli-20-news-summary.json) · [complete scores](cpu-nli-20-news-scores.jsonl.gz) · [integrity audit](cpu-nli-20-news-integrity.json) | **Completed 20/20 original articles**, 18,709 words, **60 source windows and 240 independent hypothesis pairs**. DeBERTa CPU inference took **247.102 seconds**, including the first model load in that process, with **2.038GiB** peak process RSS and no GPU or paid provider calls. All **537 original-block references** and full source-token coverage were checked. Four auxiliary hypotheses, no aggregation or threshold; scores are uncalibrated and do not assign prejudice labels. |
| [Whole-article local Qwen trial](v3-local-news-summary.json) · [observations](v3-local-news-observations.json) · [resources](v3-local-news-resources.json) · [inspection archive](v3-local-news/showcase.json) | A bounded, **cancelled** trial selected 20 complete articles and 115 source units under six lenses/19 criteria. **2/20 articles and 8/115 units completed**; two units remain partial and 105 unprocessed. All 186 retained assessment quotes and 1,042 references were checked. The sampled combined driver/Ollama process RSS peak reached **9.238GiB**; this sum can double-count shared pages and excludes browser/OS memory. This is incomplete local generation, not the completed CPU NLI or retrieval baseline. The cancelled archive is for inspection, not installation as a completed demo. |
| [CPU runtime smoke](cpu-runtime-smoke.json) | Real pinned MiniLM and DeBERTa runtimes, generated functional text, full token windows and observed memory. This is not a news quality evaluation. |
| [Embedding batching parity](cpu-embedding-batching-parity.json) | Thirty-two actual news windows, unchanged source order after length grouping, vector/cosine drift below the declared tolerance. A microbenchmark, not a portable corpus speed promise. |
| [GPT-OSS wire probe](gpt-oss-low-wire-probe.json) | One real 3,727-character source unit, nineteen criteria, valid structured decision vector through the low-reasoning Responses profile. A protocol check, not interpretation accuracy. |
| [Default-reasoning verifier diagnostic](v3-independent-verification.json) | Stopped profile: insufficient generation allowance left **0/79 fully assessed units**. Partial criterion outputs and conservative charges remain recorded. The later explicit low-reasoning profile is a separate run. |

Download the three **commented PDF/HTML/XLSX examples** from the [main README](../README.md#open-a-real-commented-report). Their frozen snapshots preserve original model readings, exact source spans, AI-authored commentary and actual review state separately. The [full news dashboard PDF](../examples/news-corpus-dashboard.pdf), [complete workbook](../examples/news-corpus-dashboard.xlsx) and [export manifest](../examples/news-corpus-dashboard.manifest.json) retain the settled partial run; their human appendix is bounded, while the structured data retains all selected records.

The completed **[local CPU NLI workbook](../examples/local-cpu-nli-20.xlsx)** exposes the actual results in six sheets: Method, Models, Sources, Windows, Scores and Hypotheses. Its [checksum receipt](../examples/local-cpu-nli-20.receipt.json) records the input/output hashes and source/score checks. The workbook preserves both logits, independent decimal scores, exact source windows, locators and credits. Exporting it from the frozen scores requests no inference; the underlying CPU NLI run did perform model inference. Neither operation establishes interpretation accuracy. [CPU profiles and reproduction](../docs/models-v3.md).

The full 5,000-article cloud backfill is now settled **partial**, with its residual gaps preserved. Completed CPU retrieval and 20-article NLI do not fill missing generative decisions, and the local Qwen trial remains bounded and incomplete. Their workspaces, timings and memory measurements stay separate. The [original-cohort cancelled summary](v3-news-original-cohort-cancelled.json) is retained as historical fork provenance: its **$4.2409458** is already inherited in the final run's total and must not be added twice. Historical cancelled/paused checkpoints are not installed as completed demos. The temporary paid-inference gateway was [disabled after settlement](v3-gateway-cleanup.json).

## Read a portable package

A v3 package contains `showcase.json` and bounded `assessments-part-*.jsonl.gz` files. The manifest records the frozen run, source selection, outcomes, usage, method sessions and SHA256 of every part. Each record includes source identity/hash, extracted-block checksum, unit states and explicit assessments. The archive includes negative, uncertain and not-applicable decisions, plus unprocessed sources and visible gaps.

Loading a saved demo validates every part, source version, rebuilt unit, criterion and evidence span before installation. It preserves historical usage without new inference or a new ledger debit. A failed import can resume; already installed local review events survive repeat imports. Only settled `completed` or `partial` packages are accepted. Source revisions remain separate immutable versions.

```bash
python scripts/package_v3.py --data-dir data/YOUR_RUN \
  --run-id RUN_ID --output-dir benchmarks/YOUR_PACKAGE
```

`scripts/benchmark_v3.py` records whole-source selections and supports same-method `--resume`. `scripts/verify_v3.py` creates a separate second-model subset only with explicit `--allow-remote-text`; a verifier never overwrites the primary readings. Model agreement is not human adjudication. See [benchmarking](../docs/benchmarking.md) and [v3 research](../docs/research-v3.md).

## Source and infrastructure checks

- [News audit](../corpus/news/audit.json) and [independent visible-source audit](../corpus/news/source-audit.json): **5,000/5,000** real licensed source snapshots; these are source checks, not inference coverage.
- `corpus-audit.json`: checksum and locator checks for the twenty institutional publications.
- `load-test.json`: 1,000 **synthetic** document records / 10,000 synthetic page records, pagination, leases, recovery and replay; no model/OCR quality claim.
- `large-upload.json`: actual 500MiB transport/restart/deduplication using a PDF with inert padding. It does not measure reading 500MiB of meaningful prose.

## v0.4 complete offline replay and persistent report

The [actual clean-runner gate](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44141301077) passed: 5,000 sources, 204,342 saved assessments, 10,759 units and 3,860,336 exact references. A complete 101,686,507-byte XLSX retained all four outcomes and every expected source/assessment identity. Restore replay and post-report logical/object fingerprints remained unchanged; no inference or new budget debit occurred. The analysis remains partial with 79 unresolved decisions.

[Exact replay receipt](v04-evidence-replay.json) · [exact report receipt](v04-report-job-5000.json) · [measurement scope and operating limits](../docs/verification-v04.md). The proof took 847.46 seconds overall; its 534.31-second report phase includes capture, rendering, file verification and cleanup. These are not semantic-accuracy measurements or production-cloud capacity results. Earlier diagnostic failures are preserved separately.

## v0.4 bounded local generation

The [readable study](../docs/local-study-v04.md), [frozen summary](v04-cpu-study.json) and [offline audit](v04-cpu-study-audit.json) retain two Q4 profiles over six short whole articles / 17 units. Both completed one article; 2B completed 12 units and 4B three within the declared operating envelope. These are processing outcomes, not measured interpretation accuracy. Of 95 jointly retained decisions, 89 outcome categories differed; pairing is conditioned on retained results and the stopping point. No model is promoted. The audit reproduced 72 retained request message/schema hashes and checked 2,822 exact references without inference.

## Historical v1 artifacts

The earlier `cloud-*-summary.json`, `local-*-summary.json` and compressed JSON snapshots describe the original pipeline. The v1 cloud run retained 1,071 observations with incomplete block coverage. Its local benchmark sampled one passage per source; **it is not the v3 whole-article local pilot**. The lexical run records exact term matches and performs no semantic interpretation. Legacy observations remain unclassified under the v3 contract.

Costs use recorded token receipts and configured rates; they are not provider invoices. Unknown billing stays unknown. Conservative accounting may charge a reserved maximum after interrupted calls. Driver RSS excludes separately hosted models unless the resource artifact explicitly combines them. Historical reused request rows are provenance, not new provider requests or duplicate monthly debits.
