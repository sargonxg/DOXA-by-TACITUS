# v0.6 verification and operating boundaries

This release refines the native evidence workbench for low-memory local use and dedicated hosted pilots. Software checks, recorded-model provenance, independent interpretation validity and live production qualification are different forms of evidence. No human reference labels, customer payments or live customer deployments were manufactured during this work.

## Completed local checks

- The complete Python regression suite passed **1,116 tests and six subtests** on Python 3.12. One dependency deprecation warning remains in Starlette's HTTPX TestClient integration. Additional infrastructure-specific tests are recorded with their own results.
- The production frontend build and TypeScript checks passed. The [new browser receipt](../benchmarks/v06-browser-smoke.json) records **12 real browser checks**, including offline recorded-source exploration, an actual PDF download with matching SHA-256, practice intake, method cards, source matching/exclusions, quote acceptance, persistent activity, keyboard focus and a 390-pixel viewport. No HTTP routes were mocked and no inference was requested.
- The updated guided-pilot browser flow separately passed **11 checks**, with actual queued background rendering and PDF/HTML/XLSX/JSON downloads. Its [receipt](../benchmarks/v06-pilot-browser-smoke.json) is separate from the unchanged v0.5 baseline.
- TypeScript and **37 Cloudflare Workers emulator tests** passed. Native storage advances to schema 6; the existing Cloudflare shared private-workspace profile stays on schema 4.
- Independent code review identified client pricing overrides, missing report quote checks, evaluation split leakage and recovery-ledger preservation issues. Targeted regressions exercise trusted server pricing, submission/worker/publication authority, whole-cluster splits and fail-closed recovery. These checks are not an external penetration test or scientific certification.

Offline repository verification checked **549 local links and 85 artifact hashes**, with zero failures, network requests or model requests.

## Recorded sources and larger infrastructure proof

The [twenty-source reader](../examples/v06-recorded-example.pdf) contains **20 real CC BY 3.0 development articles**, **645 unchanged model assessments** and **11,280 exact source references**. The selected units retain **33 accepted and one partial** state. The 5,000-source parent remains partial with 79 missing decisions. Selection deliberately illustrates outcomes and excludes canonical/evaluation holdouts; it does not estimate prevalence, independence or accuracy. The reproducible builder verifies canonical metadata, credits, source blocks, original archive records and packaged artifacts without initializing a source workspace or requesting inference. [Manifest](../examples/v06-recorded-example.manifest.json) · [complete XLSX](../examples/v06-recorded-example.xlsx) · [complete JSON](../examples/v06-recorded-example.json.gz).

The [10,000-source receipt](../benchmarks/v06-scale-10000.json) records **22 passed checks**: actual parsing and persistence of 10,000 short synthetic TXT files, replay/deduplication, HTTP pagination and a complete frozen XLSX resumed through a reopened Store. A separate **128-source lexical probe** exercises expired-lease recovery. The combined proof took **118.49 seconds** in the recorded environment. Parent peak RSS was **101,564,416 bytes**; child lifetime peak was **160,342,016 bytes**. These distinct measurements are not summed. [Scope and reproduction](scale-v06.md) · [3,267,994-byte workbook](../examples/v06-scale-10000.xlsx).

That larger proof used no model calls, model loads, network requests or budget reservations. It does not measure meaningful PDF/OCR throughput, 10,000 contextual analyses, production capacity or physical 8 GB laptop usability. Earlier [v0.5 full real-source replay receipts](verification-v05.md) remain unchanged.

## Completed full real-source qualification

The [clean-runner v0.6 qualification](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44219117669) **passed** on implementation revision `cbf276b04f40c99296779d2e9a4298f844cca892`. It restored **5,000 sources**, **204,342 retained assessments** and **10,759 source units**, checking **3,860,336 exact references** and 32,893,897 source characters. Repeated restoration and post-report complete logical-table/object fingerprints remained unchanged. Historical progress, model profile, usage, spend, source hashes and partial status were preserved.

A complete persistent report produced a **101,686,697-byte XLSX** with all expected source/assessment identities and all four outcomes. The combined proof took **826.29 seconds**, including **523.47 seconds** for report capture/render/verification/cleanup in that runner. **Zero new provider requests, model loads, budget reservations or spend** occurred. The original 79 missing decisions were not resolved by replay.

[Exact replay receipt](../benchmarks/v06-evidence-replay.json) · [exact complete-report receipt](../benchmarks/v06-report-job-5000.json). Later reader wording, verifier assertions and documentation refinements do not change the qualified Python engine, extraction/report code or packaged source evidence. These results are software/provenance evidence, not interpretation validity or a portable performance promise.

## Memory, packaging and hosting qualification

The low-memory profile targets an 8 GB host and configures a **4 GiB application container**, one heavy operation, one 1,536 MiB parser, and two CPU threads with batch size two. Native Python worker/parser controls do not impose a whole-process-tree OS memory limit. The browser, operating system and separately configured model servers are outside the Docker application's boundary.

The local Docker build encountered ENOSPC in this 32 GB execution workspace using Docker's **VFS** backend, which copies complete layers. It failed before a test container was created; this is not a passed runtime memory check or evidence of runtime OOM. The [clean-runner container proof](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44219221171) **passed** against revision `6a792a872b91b45c8e8792e55a160345bc150420`. Its [exact receipt](../benchmarks/v06-low-memory-smoke.json) records ten checks, container-loopback HTTP with networking disabled, an actual 4,294,967,296-byte limit, two-CPU quota, **136,634,368-byte recorded cgroup peak** and zero OOM events for the bounded smoke workflow. It exercised the real recorded reader/PDF, own TXT/PDF extraction, four-format durable reporting, download integrity and idempotency, with no PyTorch, PyMuPDF, provider attempts or spend. Elapsed time was **10.48 seconds** after image build; this is not install/build time or large-model throughput. Physical 8 GB laptop usability remains unmeasured. Only completed agent-created scratch/build artifacts were removed; original research data and published receipts were preserved.

The [dedicated GCP recipe](hosted-pilots-v06.md) is subject to Terraform, Compose, Caddy, shell and recovery-contract validation. A validated recipe does not establish a deployed service, actual regional retention, backup expiry or production capacity. Apply requires real operator configuration, secrets, customer scope and cloud resources; none were created for this implementation.

All **20 checks** passed on both the [branch workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44219221171) and [pull-request workflow](https://github.com/sargonxg/DOXA-by-TACITUS/actions/runs/44219338697). The final source revision repeats these checks before merge. The self-contained 0.6 wheel installed into an empty directory and served its compiled UI, performed subprocess extraction and completed an actual XLSX report job without checkout assets or inference. Its packaged engine, interface and offline examples were checked byte-for-byte against the source/build files.

The [GCP validation receipt](../infra/gcp/validation-receipt.json) records Terraform1.13.5/Google provider6.50.0 format/schema validation, rendered shell/Compose checks and actual pinned Caddy configuration validation, alongside the scoped unit tests. This remains distinct from a live GCP staging drill.

## Scientific and commercial limits

Method cards, independent-panel import, preregistration, metrics, reader-perception distributions, functional runners and prospective alert campaigns provide executable protocols. Qualified human reviewers, cleared representative data, independent adjudication and domain-specific evaluation are still real external work. Empty studies say **not measured**; the original outcome-enriched review packet cannot become a population-accuracy claim.

Hosted quote scopes distinguish **per-operation limits** from an **aggregate processing-cost cap**. Server-configured exact provider/model/endpoint prices govern quoted paid requests; customer prices, token accounting and provider invoices remain distinct. Local research behavior remains available without a quote unless the installation explicitly requires one.

Retention is manual by default. Automatic whole-project erasure needs both installation enablement and project opt-in, with the earliest raw/derived deadline measured from the last policy save. The external deletion ledger protects supported restore paths; backup/provider expiry and downloaded copies remain separate. See [privacy operations](privacy-lifecycle-v06.md).

The [runtime SBOM](runtime-sbom-v06.spdx.json) is a partial inventory, not legal clearance or a vulnerability scan. The default PDF runtime uses pdfminer.six and pypdf; optional extras, operating-system libraries, models and source rights need their own review. [Licensing](licenses.md).

OAuth connectors, public self-service subscriptions, shared SaaS tenancy and independently demonstrated interpretation accuracy remain future commercial gates. The [refinement guide](refinement-v06.md) maps the implemented capabilities to their actual limits.
