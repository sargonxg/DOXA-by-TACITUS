# Reproducible checks

These files distinguish processing checks from model interpretation. No independent human interpretation labels or accuracy/F1 claims are supplied.

- `corpus-audit.json`: offline checksums, exact real-source manifest and locator checks for all twenty publications.
- `load-test.json`: 1,000 synthetic document records / 10,000 synthetic page records; storage, pagination, leases, crash recovery and replay. No OCR or model calls.
- `large-upload.json`: actual 500 MiB resumable transport, application restart, repeated part, extraction of a synthetic single-page PDF with inert padding, and deduplication. This does not measure processing 500 MiB of meaningful prose.
- `*-summary.json`: inspectable summaries of actual finished runs, including model, coverage, usage, rejections and unresolved interpretation accuracy.
- `*.json.gz`: complete compressed run snapshots with source blocks, findings, coverage, errors and provenance. Saved results do not require an inference key to inspect.

The lexical baseline processes the whole corpus but performs **no semantic interpretation**. Local model samples cover explicitly selected passages, not all source text. The cloud corpus run records the complete source selection; failed ranges and rejected observations remain visible and prevent a false complete-coverage claim.

Reported token costs are estimates from provider usage and the recorded rate table. Conservative budget accounting also reserves for calls whose billing is unknown after interruption. Benchmark-driver memory excludes memory in a separate Ollama server or managed cloud model. See `local-runtime.json` when present for an observed Ollama model allocation.

See [benchmarking instructions](../docs/benchmarking.md). Package a finished run with `python scripts/package_benchmark.py SOURCE.json --name NAME`. Only an unsampled twenty-source selection may be copied to `corpus/showcase.json.gz`.
