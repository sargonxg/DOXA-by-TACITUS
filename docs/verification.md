# What was actually verified

For the current 0.3.0 workbench, see the superseding [v3 verification record](verification-v3.md). This document retains the historical v1 measurements below.

This release was exercised in a Linux execution environment on November 16, 2026. The application is a research preview; these checks do not establish interpretation accuracy.

| Check | Actual result |
|---|---|
| Python application, engine, extraction, export, storage and benchmark tests | **161 passed**, plus 6 subtests; Ruff and dependency consistency checks passed |
| Cloudflare runtime | **14 tests passed**, TypeScript passed; D1/R2 were also exercised through the actual HTTP bridge in local Workers emulation |
| Real long-document search | 380,511-character document indexed into 69 bounded fragments; queries spanning fragments returned the correct document |
| Generated-artifact transport | Actual 33 MiB multipart R2 upload/download; exact SHA-256 match |
| Container | Built and started the portable image; imported the saved 20-source/1,071-observation run with partial coverage and zero new inference spend |
| Browser | Eight actual Chromium flows: real collection, library search, CSV metadata, evidence highlights/review, comparisons, provider readiness, XLSX download and mobile layout; no JavaScript errors |
| Browser upload recovery | Interrupted 11 MiB PDF upload survived refresh and resent only the unfinished part |
| Import comparisons | Two three-file imports produced two batches and five distinct documents; added/changed/unchanged/not-in-later counts were each one |
| 500 MiB input | Actual 524,288,000-byte synthetic padded PDF, 63 parts, repeated part, application restart, successful extraction and deduplication; about 117 MiB sampled combined app/parser memory |
| Scheduling load | 1,000 synthetic document records / 10,000 synthetic page records; pagination, leases, recovery and replay passed |
| Offline corpus | All 20 real source snapshots passed their checksum/locator audit |
| Lexical corpus scan | All 8,999 blocks processed; 7,213 exact term matches; no semantic interpretation performed |
| Cloud model | Actual Gemma run over the full 20-source selection retained **1,071** observations; **partial**, with 5,788/8,999 blocks passing all checks |
| Local model | Actual CPU Qwen3.5 4B run sampled one passage from each of 20 sources; **32** observations retained, one source range rejected |
| Reports | PDF, HTML, XLSX, CSV and JSONL exercised; canonical example briefing PDF has 22 pages with explicit partial status and coverage; artifact hashes are in `examples/verification.json` |
| Illustrated guide | Actual HTML and PNG served successfully from the container; the 8-bit map is also included in repository documentation |

The base container uses a portable, hashed runtime dependency lock. Frontend and Worker npm audits reported zero advisories. The Python application dependency audit was clean after upgrading WeasyPrint to 70.0. Provider/service credentials and private runtime files were excluded from source control; source/artifact scans included the decompressed showcase.

## Interpretation and deployment limits

Citation validation is a software gate: retained quotations resolve to stored source text. It does not prove the attribution, criterion application or interpretation. Rejections and failed ranges remain visible. Block coverage counts source blocks once; character coverage weights text length. The sample local run cannot establish whole-corpus local speed or accuracy.

The [qualitative inspection](benchmark-observations.md) examines fifteen actual observations and concrete review issues. It was performed by an AI assistant and is not independent human annotation. No accuracy, F1 or fairness score is claimed.

Cloud inference was run against Cloudflare. The complete cloud application was tested through Docker and local Workers/D1/R2 emulation; a persistent production cloud workspace was not provisioned. It requires the operator's account, resource IDs and Cloudflare Access configuration described in [deployment](deployment.md). The temporary authenticated benchmark gateway was deleted after the user explicitly approved cleanup.

The 500 MiB check uses inert PDF padding to test transport and recovery. The scheduling load uses synthetic records. Neither measures OCR throughput, processing 500 MiB of meaningful prose, or model interpretation quality. Full sources, errors, usage and model profiles are available in the [benchmark artifacts](../benchmarks/README.md).
