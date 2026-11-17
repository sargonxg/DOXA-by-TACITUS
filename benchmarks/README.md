# Recorded runs and reproducible checks

These artifacts distinguish source integrity, software behavior, model coverage and interpretation quality. **No independent human gold labels, accuracy or F1 scores are supplied.** Read the `status`, `progress`, `unit_states`, model profile and source selection before comparing counts.

## Contextual v3 results

| Artifact | Actual scope and result |
| --- | --- |
| [Twenty institutional sources](v3-institutions-final-summary.json) · [portable package](v3-institutions/showcase.json) | Gemma 4, six lenses, full selected source texts. **613/616 units accepted**, **11,701/11,704 explicit criterion decisions**, **18/20 complete documents**. Finished **partial**; three decisions remain unresolved. |
| [Political-framing packet](v3-political-summary.json) · [portable package](v3-political/showcase.json) | Four full institutional documents, six descriptive framing criteria. **59/60 units accepted**, **358/360 decisions**, **3/4 complete documents**. Finished **partial**; two unresolved decisions. |
| [CPU passage retrieval, 20 sources](cpu-semantic-20-summary.json) | All twenty institutional documents indexed into **9,249 passages**. Three example searches returned exact-source-validated hits. Resumed timing and nonindexable formatting inputs are disclosed; ranking accuracy is unmeasured. |
| [CPU passage retrieval, 5,000 articles](cpu-semantic-5000-summary.json) | The current canonical cohort indexed into **120,464 passages**; all **5,000** bundles were reused after restart and returned quotations were checked against source text. **49.91 minutes** total active indexing across recorded attempts, approximately **0.59GiB** peak index-driver RSS. These are resource measurements, not ranking accuracy. |
| [CPU runtime smoke](cpu-runtime-smoke.json) | Real pinned MiniLM and DeBERTa runtimes, generated functional text, full token windows and observed memory. This is not a news quality evaluation. |
| [Embedding batching parity](cpu-embedding-batching-parity.json) | Thirty-two actual news windows, unchanged source order after length grouping, vector/cosine drift below the declared tolerance. A microbenchmark, not a portable corpus speed promise. |
| [GPT-OSS wire probe](gpt-oss-low-wire-probe.json) | One real 3,727-character source unit, nineteen criteria, valid structured decision vector through the low-reasoning Responses profile. A protocol check, not interpretation accuracy. |
| [Default-reasoning verifier diagnostic](v3-independent-verification.json) | Stopped profile: insufficient generation allowance left **0/79 fully assessed units**. Partial criterion outputs and conservative charges remain recorded. The later explicit low-reasoning profile is a separate run. |

Download the two **commented PDF/HTML/XLSX examples** from the [main README](../README.md#open-a-real-commented-report). Their frozen snapshots preserve original model readings, exact source spans, AI-authored commentary and actual review state separately.

The full 5,000-article model backfill, CPU retrieval cohort and whole-article local pilot use their own saved workspaces. Their eventual published summaries must disclose the measured final coverage; a source audit or selected 20-article pilot does not establish 5,000-article generative completion. Historical cancelled/paused checkpoints are not installed as completed demos.

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

## Historical v1 artifacts

The earlier `cloud-*-summary.json`, `local-*-summary.json` and compressed JSON snapshots describe the original pipeline. The v1 cloud run retained 1,071 observations with incomplete block coverage. Its local benchmark sampled one passage per source; **it is not the v3 whole-article local pilot**. The lexical run records exact term matches and performs no semantic interpretation. Legacy observations remain unclassified under the v3 contract.

Costs use recorded token receipts and configured rates; they are not provider invoices. Unknown billing stays unknown. Conservative accounting may charge a reserved maximum after interrupted calls. Driver RSS excludes separately hosted models unless the resource artifact explicitly combines them. Historical reused request rows are provenance, not new provider requests or duplicate monthly debits.
