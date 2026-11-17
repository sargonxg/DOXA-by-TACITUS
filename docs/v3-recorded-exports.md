# Export a settled recorded run

The streaming CLI exports a local methodology-v3 model run after it reaches `completed`, `partial`, `failed` or `cancelled`. It rejects active and paused runs. It performs no inference, loads no model and opens SQLite read-only. Existing runs, source metadata, reviews, query-cache rows and report rows remain unchanged.

```bash
.venv/bin/python scripts/export_recorded_run.py \
  --data-dir data/v3-news \
  --run-id b2a2edd4ca7047128a5e433d8a874530 \
  --output-prefix examples/news-corpus-dashboard
```

The command writes an offline HTML briefing, PDF, XLSX, full `.snapshot.json.gz` and checksummed `.manifest.json`. Run it only after the canonical news run has settled. For another recorded workspace, supply its local data directory and run ID.

The query explicitly selects **all four results**: `meets_criterion`, `does_not_meet`, `uncertain` and `not_applicable`. Its source policy preserves independent source identities, including identical bytes credited to different publications. Every frozen selected source remains in the export, including sources with pending or failed assessment units. Missing or changed source hashes cause refusal.

The JSON and XLSX stream from one SQLite read transaction. Records are grouped by document, unit, lens and criterion for readable workbooks and compression locality. Canonically sorted ID-set hashes and the declared serialization-order hash, typed outcome counts, source versions and complete record counts are checked against that transaction. The frozen query carries the same Workspace scope semantics, review overlays, scope hash and workspace revision as the service; its generated query ID is retained in the artifact rather than inserted into the source database. Operational connection probes are omitted from public run provenance and disclosed in the manifest.

The workbook contains Overview, Documents, Findings, Typed results, Authors, Lenses, Units, Runs and Methodology sheets. Documents include source credits, translation credits, licenses, original URLs and complete metadata. Findings include every retained assessment and its complete normalized record. Values longer than an Excel cell continue across physical rows, identified by the `Part` column. The manifest distinguishes logical record counts from physical continuation rows. Source strings are literal values, never formulas or automatic external connections.

The PDF and HTML reuse DOXA's report templates with full SQL-derived totals. Their **25-entry evidence appendix and 25-entry source register** prioritize indicator matches, uncertainty, boundaries and descriptive matches. Context windows are excerpted around the complete primary quote. These presentation limits do not reduce the underlying source selection, outcome totals, workbook or compressed snapshot. Typed outcomes are workload and assessment counts; they are not bias rates, calibrated accuracy or a combined prejudice index.

The full snapshot follows the report contract: complete source metadata, assessments, recorded contexts, run provenance, reviews, query scope and coverage. Original document bodies remain in the separately licensed source corpus rather than being duplicated into every report. Source-hash checks establish identity; they do not independently establish the correctness of an interpretation.

Each output is limited to **99,000,000 bytes by default**, with a 2 GiB uncompressed snapshot ceiling. Workbooks above 95 MB receive lossless ZIP9 recompression; every member SHA256, timestamp and compression method is checked before replacement. Files are prepared in a temporary directory and published after size and integrity checks. An oversized export fails explicitly; no selected records are silently discarded. Bounded contextual archive parts remain available for larger packages. `--formats html xlsx` can omit PDF rendering, and `--max-file-mb` can set a smaller per-file bound. This command's streaming behavior does not describe the separate browser-export endpoint.

An actual terminal 20-source smoke export retained all 11,701 assessments and all four outcomes, with 613 of 616 units fully assessed. On the execution workspace it took 9.9 seconds and peaked at 122,776 KiB RSS; the full snapshot was 3.66 MB and the workbook 5.10 MB. This is an export measurement for that recorded dataset and environment, not a throughput forecast or an interpretation-accuracy benchmark.
