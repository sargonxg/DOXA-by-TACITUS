# Changelog

## 0.9.1 — 2026-11-23

- Add a native Ollama readiness check and an import-ready Civic Square archive with timed caption sources.
- Preserve declared source eligibility, expose per-criterion missing-data ranges, and find the maximum number of eligible one-to-one matches after removing exact duplicates.
- Verify graph exports against the frozen scorecard and test isolated wheel workflows on Linux, macOS, and Windows.
- Fix native macOS parser startup and preserve Unicode text from the extractor on all three operating systems.
- Refresh the GitHub landing page and document the technical method for political framing and prejudice lenses.

## 0.9.0 — 2026-11-22

- Run bounded contextual analysis through a local Qwen3.5 2B Q4 Ollama preset. Check workload, preserve caption timings, and pause and resume with saved decisions.
- Save source-level scorecards with distinct uncertainty and coverage measures, preregistered weights and sensitivity scenarios, matched contrasts, and linked evidence.
- Export the frozen scorecard as PDF, HTML, XLSX, CSV, or JSONL with a JSON-LD evidence graph. Try the fictional Civic Square example or inspect the archived research cases locally.
- The GitHub repository contains the public documentation and downloads. The standalone public study host redirects to GitHub.

## 0.8.0 — 2026-11-22

- [The antisemitism case](docs/antisemitism-case.md) compares the saved IHRA, Jerusalem Declaration, and Nexus readings of a quotation reported in Plzeň. Four source readings, nine research cues, and a JSON-LD graph trace the evidence.
- Four original 8-bit illustrations guide the reader through source, speaker, and criterion. The new study includes a tagged PDF, a decision ledger, and an offline edition.
- The GitHub overview, local workbench, and public DOXA reader link both studies.

## 0.7.1 — 2026-11-22

The study filters now fit the reader at a 320-pixel viewport. Browser coverage
checks the criterion and year filters at that width. The published graph and
recorded study remain the same edition.

## 0.7.0 — 2026-11-22

- The [60-article field study](docs/reading-muslim-coverage.md) now traces model readings through a versioned [JSON-LD evidence graph](docs/evidence-graph.md). It links 822 decisions to their archived run, frozen criteria and 3,064 distinct source passages. The original outcomes and source selection are preserved.
- Both readers show the evidence path and support publication-year and contributor filters. The tagged PDF, workbook and offline edition include new sensitivity checks within the selected sample.
- Study downloads use a versioned release, explicit content types and HTTP status checks. The installed wheel verifies graph access alongside the workspace API, file extraction and background report export.
- The [GitHub overview](README.md) leads with a worked case and installation. [Historical research and operating details](docs/workbench-reference.md) remain available.

## 0.6.1 — 2026-11-18

The main dashboard now leads from a bias criterion and its distinct-source count straight to an exact passage. Each row identifies the sources with matching decisions and retains uncertainty. New guided PDF, HTML and XLSX reports explain the supplied findings before introducing wording metrics.

The [60-article field study](docs/reading-muslim-coverage.md) covers two frameworks and six criteria, with a searchable article directory, six worked examples, an illustrated PDF, a complete workbook and an attributed source ZIP. The sampling rule uses article text, year and source identity before examining the original model output.

## 0.6.0 — 2026-11-17

The next refinement keeps one evidence workbench while making local setup, analytical contracts and long work easier to inspect. The low-memory default targets an 8 GB host; Compose limits the application container and child workers to 4 GiB. This is a configured boundary, not a measured whole-host hardware or model-throughput claim.

- Guided workflow, safe `doxa doctor --json`, UI/API build compatibility checks and five packaged synthetic practice sources and a separate twenty-real-source recorded explorer with PDF/XLSX/HTML/JSON artifacts; optional CPU-lite ONNX adapter remains an unqualified candidate with no automatic weight installation.
- Default PDF extraction uses pdfminer.six and pypdf. New extraction versions preserve historical source text and offsets; scan/layout/OCR limitations remain visible. PyMuPDF is an explicit legacy extra.
- Frozen academic measurement cards in new service briefs and report annexes; preregistered reference panels, criterion diagnostics and evidence-based quality gates without invented human labels.
- Explicit structural-context policy with heading/mail ancestry anchors, missing-context records and additional unexecuted behavioral challenges.
- Auditable matched cohorts, exclusion reasons and prospective alert-review tools. Existing trajectory statistics are retained; comparison does not establish motive, factuality or causation.
- Persistent ZIP import, semantic indexing and guided report activities reuse source identities and the report engine, with restart/cancellation, permission rechecks and verified downloads.
- Optional whole-project retention and signed external deletion obligations applied before serving restored backups. Retention is off by default, anchors at the latest policy save and uses the earliest raw/derived deadline; backup/provider expiry is separately recorded.
- Dedicated GCP pilot tooling and server-owned fixed-scope quotes, exact-scope acceptance and internal processing caps. Deployment checks do not constitute a live customer deployment, payment integration or shared SaaS qualification.
- A separately recorded 10,000-short-synthetic-TXT intake/replay/pagination/export proof and 128-task lexical recovery check; this is not contextual-inference throughput or physical 8 GB qualification.
- Native schema 6 with pre-upgrade backup; Cloudflare's existing private workspace remains schema 4. Historical results and publication artifacts remain unchanged.

The new real-browser refinement flow passed twelve checks with zero mocked routes, page errors or inference requests. See [refinements](docs/refinement-v06.md), [local operation](docs/local-v06.md), [privacy/recovery](docs/privacy-lifecycle-v06.md) and the [verification record](docs/verification-v06.md) for measured scope. Independent human studies, qualified delivery, OAuth connectors, subscriptions and shared SaaS remain commercial milestones.

## 0.5.0 — 2026-11-17

The guided-pilot foundation adds Company, Public and Personal inquiry profiles to the one-page workbench. Save a versioned question and scope, import authorized messages or selected writing, estimate work, inspect exact wording observations, and generate bounded PDF/HTML/XLSX/JSON briefings. New synthetic examples disclose zero inference and pending human reference labels.

- Dedicated native managed access: individual hashed credentials, private sessions, roles/project grants, trusted review identity, worker-time authorization and credential rotation. Shared Cloudflare SaaS tenancy is not introduced.
- Native privacy policies, enforced inference/export checks, versioned pseudonymized source copies, AES-256-GCM identity vault and Unicode span mappings. Original sources remain available to authorized project readers; rules do not guarantee anonymity.
- Durable whole-project live erasure: activity draining through streams and canceled threads, derivative ownership/inventory, resumable deletion, shared-object protection and content-free receipts. Backup/provider/downloaded copies and media wiping are explicitly outside scope.
- Bounded EML/MBOX intake and mail-aware contextual provenance; quotations/history/signatures remain context and are excluded from mail author-group counts. Unsupported mail-normalized author rates are suppressed.
- Expanded independent annotation forms for exact spans, targets/propositions, reporting chains, communicative acts and missing context; no labels or accuracy measures fabricated.
- Native schema 5 with pre-upgrade backup and old-version refusal; existing Cloudflare schema 4 preserved. Original recorded runs and published baseline artifacts are retained.

See [operation](docs/managed-pilots-v05.md), [reports](docs/pilot-reports-v05.md), [verification](docs/verification-v05.md) and the [remaining commercial roadmap](docs/commercial-service-roadmap.md). Independent expert evaluation, real OAuth connectors, payment/subscription integration and multi-customer SaaS are not completed by this release.

## 0.4.0 — 2026-11-17

The evidence workbench adds a browser casebook composer and persistent report jobs. Curators can pin recorded outcomes or unfinished units, freeze source/framework/model/review inputs, annotate and reorder cases, and download PDF, HTML, XLSX, CSV or JSONL publications. Jobs expose progress, cancellation, restart and checksummed range downloads.

- Separate source, unit and criterion-decision coverage; uncertainty, model-disagreement and unfinished-work queues; source-context inspection without invented assessments.
- Exact original model payloads remain distinct from review overlays and editorial notes. Method identity is separated from execution policy; optional semantic overlays retain their own authorship and version.
- Atomic imports, resumable multipart completion, coherent SQLite reads, D1 revision guards, pre-upgrade backups, shared extraction policy, page/block ledgers and immutable re-extraction.
- A reader explainer, technical annex and 15-case atlas, with research bibliography, criterion-source audit, source-reference checks, processed lineage and frozen publication inputs.
- Model-blind 200-case independent-review scaffold and 120 authored functional fixtures. Human labels and model execution of those functional fixtures remain pending.
- Self-contained application wheel, expanded Python/browser/Worker/container CI and a complete offline 5,000-source evidence-replay release gate.

The v0.3 corpus, model results and published baseline artifacts remain unchanged. Existing runs retain their recorded methods and partial coverage. No model fine-tuning, validated interpretation-accuracy claim or automatic model promotion accompanies this release.

## 0.3.0 — 2026-11-17

Published the linked research workspace, 5,000 real Global Voices articles, partial contextual results, independent-model comparison, CPU retrieval/NLI measurements, complete result workbook, commented real-source reports and reproducible export/restore proofs.

See the [v0.3 verification record](docs/verification-v3.md) and [release](https://github.com/sargonxg/DOXA-by-TACITUS/releases/tag/v0.3.0).
