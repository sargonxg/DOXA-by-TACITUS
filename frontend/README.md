# DOXA interface

React + TypeScript + Vite. The Python API serves the production build;
Cloudflare serves the same interface.

```sh
npm ci
npm run dev
```

The development server proxies `/api` to `127.0.0.1:8000`.
`npm run build` type-checks and writes `dist/`; `npm run typecheck` checks types.

The single workspace links a paginated source collection, timeline, author ×
criterion matrix, retained observations, and an exact-source evidence inspector.
Global filters and exports share a server query snapshot. The explicit
**Corpus · all imported sources** selection has no model measurements; successful
manual imports select it so new files stay visible. An analysis selection shows
that run's frozen source cohort. New analysis can freeze the current corpus scope. Source and evidence
pages request 50 records and render a bounded number of rows; the client does
not download all 5,000 source bodies. Drawers contain import, analysis, model
readiness, processing jobs, reports, filters, and reviewed learning.

The v0.4 Reports drawer composes persistent casebooks and publication jobs.
Pin an actual assessment in the inspector, or browse all four recorded outcomes
within the frozen source cohort. An unfinished source unit is a separate case
with no invented assessment. Add a selection rationale and attributed operator
notes, preview source / frozen framework / model / review layers, then prepare a
briefing, casebook, teaching edition or archive. Drafts and publication revisions
are server records; browser storage retains only the active draft ID.
Archive preparation explicitly expands the outcome filter to all four results
while retaining the source, framework, criterion, speech-role, review and stance
filters. Case browsing also preserves those analytical filters.

Publication progress, cancel, restart and verified artifacts remain available
after closing or refreshing the browser. Downloads use native same-origin links
with server Range and ETag support, avoiding a large JavaScript Blob copy.
Preparation uses recorded evidence and makes no inference requests. The older
single-request scoped export remains under **Direct scoped export · compatibility
tools**. PDF/HTML appendix limits remain distinct from complete data exports.

Review queues page criterion uncertainty, original-model disagreements and
unfinished units. Unit context is resolved lazily from the frozen source and can
be restored through its scoped URL. It never manufactures a Finding. Extraction
coverage has a separate bounded page ledger; processing coverage explicitly
names sources, source units and criterion decisions. None is interpretation
accuracy. Human reference labels remain pending unless actual reviews exist.

Source lists support Arrow Up/Down and Home/End within the loaded page; result
page controls load another bounded page. Analytical and inspector tabs support
Arrow Left/Right and Home/End. Named native dialogs support Escape and focus
return. The timeline also has a paginated data table.

Contextual indicator measurements count distinct sources. Reported and endorsed
source counts can overlap. Observation counts remain separate. Legacy snapshots
and lexical navigation do not become v3 contextual measurements. Source text and
evidence scroll separately; exact quote offsets use Unicode code points.

Files above 10 MiB use resumable multipart requests. Browser storage retains
upload identifiers, completed part checksums, logical import identity and file
metadata; source bytes and provider credentials are not cached there. Reselecting
the same file after a refresh resumes its remaining parts in the original import.
Small-file selections use groups of at most 100 files and 20 MiB under one import
session. Repeated content retains batch membership without duplicating a source.
Import filters let users compare controlled source groups; absence from an
incremental import does not imply deletion.

Import metadata CSV in the Import drawer using document `id`, content `sha256`,
or `filename` plus editable metadata columns. Remote inference requires explicit
text-transfer consent and a run budget. Models exposes server health, the monthly
cap, and optional explicit CPU experiments; opening it never downloads a model.
Saved showcase results display historical usage and cannot be resumed.

Independent model verification is available in Processing and Models. It selects
a completed primary contextual run, requires separate cloud text-transfer consent,
and caps its visible budget at $2. The GPT-OSS 120B verifier is a separate job;
primary counts remain pinned. Saved comparison reads show selected-unit coverage,
paired raw decisions and literal field differences, with at most 200 detail rows
and 20 initially displayed. Model agreement is not interpretation accuracy.

Run browser checks from the **repository root** after building the interface:

```sh
.venv/bin/python tests/browser_v3.py
.venv/bin/python tests/browser_upload_resume.py
.venv/bin/python tests/browser_batches.py
.venv/bin/python tests/browser_import_scope.py
.venv/bin/python tests/browser_verification.py
.venv/bin/python tests/browser_provider_rates.py
.venv/bin/python tests/browser_cpu_source.py
.venv/bin/python tests/browser_publications_v04.py
.venv/bin/python tests/browser_report_jobs_v04.py
.venv/bin/python tests/browser_accessibility_v04.py
```

These start isolated local servers and require Playwright plus system Chromium
or Playwright's installed Chromium (`python -m playwright install chromium`).
`DOXA_BROWSER_EXECUTABLE` selects an explicit browser path.
No paid inference requests are made. The main suite uses explicitly synthetic
5,000-source persistence fixtures and a mocked provider to check paging, linked
scope, exact-source review, learning gates, exports, cloud-consent controls and
390px layouts. It establishes UI behavior, not model quality or throughput.

The v0.4 publication contract test mocks publication services over real synthetic
source/query APIs. The separate report-job browser test mocks no HTTP endpoints:
it pins all four outcomes and a canonical unfinished unit, restores unit context,
cancels and restarts a publication, runs only the offline report worker, downloads
an actual PDF, checks Range/ETag and restores the saved draft/artifact. Analysis
workers remain disabled. The accessibility check exercises virtual-row keyboard
focus, row positions, tab navigation, a chart table, and dialog focus return.
Receipts default to `/tmp/doxa-publications-v04`,
`/tmp/doxa-report-jobs-real-v04`, and `/tmp/doxa-accessibility-v04.json`.

The upload test interrupts part 2 of an actual 11 MiB PDF, refreshes, reselects
the same file, and verifies that only part 2 transfers, one source is extracted,
and one logical import remains. The batch test uploads two three-file groups,
checks two three-member imports and five unique source versions, verifies all
four API delta categories, and opens an earlier source version in the inspector.
These two tests optionally accept `DOXA_BROWSER_URL` for an already-running
**disposable** local server. Outputs default to `/tmp/doxa-browser-*-v3`.

For an installed real-news corpus and existing run, `tests/browser_real_corpus.py`
uses a SQLite online backup and copied source artifacts to inspect the actual
5,000-source workspace with analysis workers disabled. `DOXA_BROWSER_DATA`,
`DOXA_BROWSER_PROJECT`, and `DOXA_BROWSER_RUN` select the saved state. It checks
actual paging, source highlights, trajectory availability, unassessed framework
labels, mobile layout and the guide drawer link, without requesting inference.
Set `DOXA_BROWSER_PUBLISH_DOCS=1` to copy the captured screenshots to `docs/images`.

The corpus-scope test imports one new source after a frozen 5,000-source run,
checks 5,001 corpus sources versus 5,000 in that run, restores the literal `none`
sentinel, and verifies the Analyze query includes the new source. The verifier
suite mocks every verification request to test consent, the $2 cap, eligible run
selection, separate counts, partial comparison coverage, and mobile layout.

`tests/browser_smoke.py` forwards to the current v3 suite. The original seven-view
smoke script is retained in `tests/legacy/browser_smoke_v1.py` as historical
verification code, not a test of the current interface.

DM Sans and Manrope are bundled for offline use under their SIL Open Font
Licenses in `public/fonts/`. Original sources:
<https://github.com/google/fonts/tree/main/ofl/dmsans> and
<https://github.com/google/fonts/tree/main/ofl/manrope>.
