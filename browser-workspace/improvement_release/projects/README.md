# Local review projects

The project modules retain the original source files, document metadata,
reports, review decisions, mitigation drafts and evidence checks in IndexedDB.
The UI initiates save, open and delete actions. Portable `.doxa` archives can
also retain a complete review without relying on browser storage.

## Integration

`project-store.ts` exposes the following functions.

- `saveProject(snapshot, {name, id?, expectedRevision?, signal?})` returns a
  `ProjectRecord`. Omit the ID for a new copy. When overwriting, supply the
  loaded ID and revision. An existing project rejects a stale or omitted
  revision lease.
- `listProjects({signal?})` returns lightweight summaries with IDs, names,
  revision, timestamps, source bytes, document count and decision count.
- `loadProject(id, {signal?})` restores and validates the complete record.
- `deleteProject(id, {expectedRevision?, signal?})` deletes a project. The UI
  should supply the listed revision so another tab's update is preserved.
- `projectStorageStatus()` returns the browser's quota estimate and retention
  status when the platform supports them.
- `requestProjectRetention()` asks the browser to retain this origin's storage.
  The browser decides whether to grant the request.

`project-format.ts` exposes `exportProject(record, {signal?})` and
`importProject(blob, {signal?})`. Export returns a Blob with MIME
`application/vnd.tacitus.doxa-project`. Import returns a validated record and
does not write IndexedDB. Save an imported snapshot with a new ID to create
a local copy. A deliberate overwrite uses the current stored revision.

The record has `format`, `version`, `id`, `name`, `revision`, `createdAt`,
`updatedAt` and `snapshot`. The snapshot has `documents`, `decisions`,
`evidenceChecks`, `selectedDocument`, `failures` and `canceled`.
`snapshot.extra` holds JSON extensions such as review timing and contextual
model results. Documents can carry a string-valued `metadata` map for
comparison cohorts. Unknown JSON report, decision, draft, document, snapshot
and record fields survive preparation and portable round trips.

Portable export can work when browser storage fails. Construct a record with
revision zero and the captured snapshot, then call `exportProject`. It will
apply the same validation and hashing as a stored record. Browser quota
failure leaves the previously saved revision intact.

## Archive format version 1

The file begins with UTF-8 `DOXA-PROJECT\n`, followed by a four-byte unsigned
big-endian manifest length and the 32-byte SHA-256 of the manifest bytes.
The UTF-8 JSON manifest follows, then the source files in descriptor order.
The manifest contains `format: doxa-project-archive`, `version: 1`, the saved
project and file descriptors with original name, canonical MIME, byte count,
last-modified time and SHA-256. The binary source bytes are stored directly.
The importer checks the exact total file length, each descriptor and source
hash before returning the record.

The format accepts up to twelve files, 32 MiB per file and 128 MiB combined,
plus up to 64 MiB of report metadata. File hashing and slicing are sequential.
Direct binary framing keeps source files outside the JSON representation.
Abort signals stop before adoption or an uncommitted database write.

PDF sources must have a PDF signature and receive `application/pdf` MIME.
Text and Markdown use validated UTF-8 and canonical text MIME. Source and
research-license links accept HTTP or HTTPS. Invalid document references,
source spans, review outcomes, metadata structures and unsafe object keys
are rejected. Optional fields absent in earlier document reports receive
compatible defaults, including section criteria and evidence passages.

Known report extensions are validated before a project can be adopted.
PDF extraction quality uses unique existing page numbers, finite boxes,
nonnegative counters, supported source and reading-order values, bounded
confidence values and valid original line numbers. Trimmed geometry marked
`retained_findings` carries `originalLineCount`; the stored geometry describes
the retained findings rather than all lines on the page. Processing time and
the separate Limitations passage pool also receive explicit checks.

A study-context packet must account for Methods, Results and Limitations
exactly once, using a selected passage, unavailable status or no-match status.
Its selected text and location must equal the corresponding retained evidence
passage, and its claim must equal the evidence check's claim. Reviewer
annotations carry `reviewer_entered` provenance. Supporting and countervailing
selections must match exact UTF-16 slices of the associated retained finding;
out-of-range offsets, split Unicode surrogate pairs and duplicate selections
are rejected. These checks establish structural consistency and source identity
within a snapshot; they do not authenticate a model run or reviewer identity.

## Interpretation and retention

Archive hashes check byte identity and transfer integrity. Reviewer identity
and model-run provenance require separately attributable evidence. An imported
project is a saved analysis snapshot. Its source context and recorded provenance
remain available for inspection, and the app can rerun analysis when appropriate.

Browser storage is specific to the app's origin and browser profile. Clearing
site data or browser eviction can remove it. Downloading a portable project
provides a separate copy. Project archives contain the original source
documents and reviewer notes. Their sharing follows the source's sensitivity.

## Verification

Run `node improvement_release/projects/browser-check.mjs` from the repository
root. The check launches a separate local Vite server on port 8167 and uses
real Chromium IndexedDB. It exercises reload, two-tab concurrent updates,
portable round trips, altered manifest and source bytes, truncated and
appended data, cancellation, a simulated quota failure and successful retry,
stale deletion, compatible legacy fields, canonical PDF MIME and unsafe source
URL rejection. Valid geometry, study context and reviewer evidence survive a
round trip. A further 39 crafted archives have recalculated manifest checksums
so that malformed known extensions exercise schema validation rather than
merely failing integrity checks. Direct preparation also rejects non-finite
geometry. The check observes requests for source-content leakage.

The fixture is fictional. Results are written to `checks.json` and the
command output can be saved to `checks.log`.
