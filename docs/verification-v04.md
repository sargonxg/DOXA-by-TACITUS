# v0.4 verification and release gates

The release preserves the v0.3 source/result baseline and adds the evidence workbench. Checks below distinguish application behavior, source fidelity, processing coverage and interpretation quality.

## Reproducible checks

| Layer | Command / artifact | What it establishes |
| --- | --- | --- |
| Python application | `python -m pytest -q` | Persistence, extraction, framework contracts, APIs, queues, reports and evaluation gates on isolated fixtures. |
| Static checks | `python -m ruff check doxa scripts tests setup.py` | Selected Python correctness/style checks. |
| Browser build | `npm --prefix frontend ci` then `npm --prefix frontend run build` | TypeScript and production UI compilation. |
| Browser workflows | `python tests/browser_v3.py`, `python tests/browser_import_scope.py`, `python tests/browser_publications_v04.py`, `python tests/browser_report_jobs_v04.py`, `python tests/browser_accessibility_v04.py` | Linked queries/imports; casebook selection; explicit outcomes; persistent report progress/cancel/restart; actual file download. See individual scripts for fixture boundaries. |
| Worker runtime | `npm --prefix infra/cloudflare run typecheck` and `npm --prefix infra/cloudflare test` | Authenticated job dispatch, D1/R2 boundaries, leases, idempotency, ranges and failure recovery in the Workers emulator. |
| Installed distribution | `python scripts/build_wheel.py` and `python scripts/check_wheel.py dist/doxa_by_tacitus-0.4.0-py3-none-any.whl` | Compiled UI, API and report service work from an installed wheel in an empty directory without checkout assets or model calls. |
| Repository publications | `python scripts/verify_repository.py` | Relative documentation links, artifact checksums and aligned application versions; no network or inference. |
| Published real-source cases | `python -m pytest -q tests/test_v04_publications.py` | Preserved original payloads, exact anchors, explicit origins/limits and publication manifests. |
| Complete recorded corpus | `python scripts/verify_evidence_release.py --execute --expected-package-sha256 783432b8430aeadc0bcce665e897bba33d8b9a14f718f70f0c5a988c1e884827` | Fresh isolated 5,000-source import/restore, all retained references, idempotent replay, unchanged logical state and zero provider/model/network calls. Uses temporary disk and removes only its scratch workspace. |

The full replay is deliberately opt-in locally and runs as a separate release workflow. Its default receipt is `benchmarks/v04-evidence-replay.json`; the immutable older [v0.3 restore proof](../benchmarks/v3-news-restore-verification.json) remains available. The package SHA gate prevents accidentally substituting a different run.

## Data and methodological checks

The [publication evidence pack](../examples/v04-evidence-pack.manifest.json) retains checksums, original parent artifacts and processed lineage. Its [five-archive source audit](../examples/v04-corpus-verification.json) checks **4,207,998 exact retained source references**, including the news run's **3,860,336 references**. This verifies text anchoring, not whether an interpretation is persuasive.

The [evaluation scaffold](evaluation-v04.md) contains **200 cases with zero invented human labels** and **120 authored functional fixtures**. Model-blind reviewers, independent reference labels, adjudication and model runs of those fixtures remain pending. Accuracy, F1, calibration and subgroup comparisons therefore remain unavailable. Neither agreement between two models nor a passing software test unlocks those metrics.

The [bounded CPU study](../benchmarks/v04-cpu-study.json) records selected articles, explicit quantization/digests, resources, request failures and incomplete work. It is a feasibility study, not a representative quality benchmark or a promotion decision. Shared-machine load limits any speed comparison. Earlier [CPU retrieval](../benchmarks/cpu-semantic-5000-summary.json) and [NLI](../examples/local-cpu-nli-20.receipt.json) measurements establish different workloads.

## Operational limits

- Browser and API tests use isolated fixtures; actual native PDF download and range behavior are checked separately from mocked UI contracts.
- Cloud tests emulate infrastructure; they do not demonstrate production multi-tenant capacity or a production 5,000-source report on Cloudflare.
- Offline replay and report generation use saved outputs, with inference blocked. They do not repair the baseline's 79 unresolved news decisions.
- OCR behavior has extraction coverage and provenance tests; real scanned-document OCR accuracy remains unmeasured.
- Default request-attempt records contain operational metadata. Historical visible prompts/responses that were not captured remain `not_recorded`; reconstructed templates are labeled as reconstructed.
- Application-accounted usage is separate from provider invoices. Installation, tests and publication builds do not provision infrastructure or download model weights.

## CI

[Application workflow](../.github/workflows/ci.yml) covers Python 3.11/3.12/3.13, UI build, browser workflows, repository checks, installed wheel, Workers and Docker health. [Evidence workflow](../.github/workflows/evidence.yml) gates complete offline replay on manual dispatch and version tags. Each run produces its own actual logs; planned jobs are not reported as completed checks.
