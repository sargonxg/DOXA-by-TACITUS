# DOXA interface

React + TypeScript + Vite. The production build is served by the Python API;
Cloudflare also serves the same static build.

```sh
npm ci
npm run dev
```

The development server proxies `/api` to `127.0.0.1:8000`.
`npm run build` type-checks and writes `dist/`. `npm run typecheck` checks the
application without making a production bundle.

The interface uses real API data. It distinguishes lexical matches from model
interpretations, keeps counts within one selected analysis run, and preserves
that run when exporting reports. Evidence and source text have separate scroll
areas and pagination. Citation offsets use Unicode code points.

Files above 10 MiB use bounded multipart requests. Completed part identifiers are
saved in browser storage; selecting the same file after an interrupted session
resumes its remaining parts. Source contents and provider credentials are not
stored in browser storage. File size/last-modified metadata identify the selected
file for recovery, while the server verifies uploaded bytes.

Metadata CSV import accepts a document `id`, content `sha256`, or `filename` plus
editable metadata columns. The library provides a template.

Small-file selections are imported together in groups of at most 100 files and
20 MiB. Larger files use separate multipart imports. Compare → Imported batches
shows added, changed, unchanged, and not-included sources between selected
imports. Not being included in an incremental import does not mean a source was
deleted. Matching content can belong to several imports without duplication.

Previously computed showcase runs are labeled as saved result snapshots.
Recorded usage is historical; loading a snapshot makes no inference requests.
Those fixed runs cannot be resumed. Users can start a new analysis with an
explicitly selected provider.

Run the real browser checks against a disposable API data directory:

```sh
DOXA_BROWSER_URL=http://127.0.0.1:8003 python tests/browser_smoke.py
DOXA_BROWSER_URL=http://127.0.0.1:8003 python tests/browser_upload_resume.py
DOXA_BROWSER_URL=http://127.0.0.1:8003 python tests/browser_batches.py
```

Both require Playwright and system Chromium. The smoke test loads the real
20-document collection and changes a review state. The upload test injects a
network interruption, refreshes the page, resumes an 11 MiB PDF, and deletes its
temporary document.
The batch test creates a separate verification project with small functional
test documents and checks all four delta categories plus grouped upload counts.

DM Sans and Manrope are bundled for offline use under their SIL Open Font
Licenses in `public/fonts/`. Original sources:
<https://github.com/google/fonts/tree/main/ofl/dmsans> and
<https://github.com/google/fonts/tree/main/ofl/manrope>.
