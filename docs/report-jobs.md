# Persistent reports and curated casebooks

The Publications panel prepares reports from recorded results. It makes no new inference request and does not load a model. Preparation can still consume CPU, disk, object-storage operations and container time.

A **query report** preserves the selected run, source cohort, filters, duplicate policy and workspace revision. A **casebook** preserves explicitly selected cases and their captured source/framework records. Casebooks illustrate an inquiry; they are not a representative sample or an accuracy benchmark.

## Prepare a report

1. Select the run and filters in the workspace. To retain all outcomes, select `meets_criterion`, `does_not_meet`, `uncertain`, and `not_applicable`. The ordinary evidence view otherwise emphasizes matches and uncertainty. Choose source identities rather than content deduplication when separate publications or attributions matter.
2. Open Publications and choose the format and audience. The audience changes presentation; it does not change the analysis method or create new findings.
3. Prepare the report and inspect its saved job status/progress. A ready job exposes verified artifact links. PDF/HTML appendices have disclosed presentation limits; the complete selected records remain in the machine-readable artifacts.
4. Download the report, compressed snapshot and checksum manifest. Query reports also provide a ZIP of frozen input parts, retaining original assessment payloads and review events separately from the resolved reporting projection.

Source metadata and the exact recorded evidence are retained in report inputs. Query report archives do not silently copy every original document file or every extracted block; preserve the workspace's originals/object files alongside its database for a complete backup. Casebook snapshots include the exact captured source blocks needed for their selected cases.

## Curate a casebook

Pin cases from the evidence inspector. A draft may combine all four outcomes within its retained **source cohort, run, and selected framework/criterion/speech-role/review/stance filters**, even when the current evidence view filters to one outcome. Changing the current view cannot expand that draft's retained filters; create a new draft for a broader inquiry. An unresolved pin refers to an actual recorded unfinished source unit and has no invented assessment or negative result.

Give the draft a title, state its selection rationale, order its cases and add optional editorial guidance. Original model output, captured review overlays, framework conditions and operator notes remain separate. Operator notes are self-declared editorial material, not independent reference labels. Original missing explanations remain unrecorded.

A submitted casebook report copies that draft revision. Later draft edits do not change its report inputs. Previously captured pins remain usable after a library source is edited or deleted; adding a new pin requires the matching frozen source version. New optional semantic annotations are not silently inferred from old records.

## States and recovery

| State | Meaning |
| --- | --- |
| `queued` | Saved job waiting for a worker, or a committed work checkpoint waiting to continue. |
| `running` | A leased worker is freezing inputs, materializing a private rendering database, rendering, or publishing. |
| `completed` | All ready files passed size/SHA checks and their artifact records were committed together. |
| `failed` | Preparation stopped with an explicit error. Restart is available when its inputs can be reused safely. |
| `cancelled` | Cancellation was committed. Previously captured inputs can support an explicit restart. |

Before query inputs are completely frozen, a changed workspace revision fails with `source_changed`; refresh the query and create a new report. Once freezing completes, a restart reads immutable saved parts instead of a changed live source. Cancellation never turns an unfinished upload into a ready download.

Native startup recovers queued and expired jobs. Cloud Queues acknowledge a work message only after Python returns its committed checkpoint; scheduled recovery also finds queued/expired report jobs. Duplicate delivery is guarded by a lease. Each attempt uses separate temporary paths, so a renderer left behind by a killed parent cannot overwrite a reclaimed job's files.

Rendering uses a cancellable child process. Native work calls allow up to 420 seconds; the Cloudflare dispatcher requests 240-second work calls. Very large render selections can exceed the rendering deadline and must be narrowed. An individual artifact is capped at 512 MiB. A full XLSX may temporarily require much more disk than its compressed download size because it uses a private SQLite database and worksheet XML files. Temporary attempt files are removed when the work call ends; durable checkpoints remain available for recovery.

## HTTP contract

Create a current workspace query, including all outcomes when needed:

```http
POST /api/v3/projects/{project_id}/query
Content-Type: application/json

{"run_id":"RECORDED_RUN","duplicate_policy":"sources","results":["meets_criterion","does_not_meet","uncertain","not_applicable"]}
```

Submit its returned `query_id`:

```http
POST /api/v3/projects/{project_id}/report-jobs
Content-Type: application/json

{"query_id":"SAVED_QUERY","format":"xlsx","profile":"briefing","publication_type":"archive"}
```

The response is HTTP 202 with the durable job resource. Supported formats are `pdf`, `html`, `xlsx`, `csv`, and `jsonl`; CSV/JSONL are gzip-compressed. Profiles are `research`, `briefing`, and `teaching`. Publication types are `briefing`, `casebook`, `teaching`, and `archive`. A casebook publication supplies `casebook_id`; the job takes its frozen query and draft revision from that resource.

| Request | Result |
| --- | --- |
| `GET /api/v3/projects/{project_id}/report-jobs?limit=20&cursor=…` | Bounded `{items,next_cursor,total}` list; maximum page size 50. |
| `GET /api/v3/report-jobs/{job_id}` | Saved state, progress, errors, restart availability and ready artifact links. |
| `POST /api/v3/report-jobs/{job_id}/cancel` | Request cancellation, or return the existing terminal resource. |
| `POST /api/v3/report-jobs/{job_id}/restart` | Resume a restartable failed/cancelled job with the same inputs. |
| `GET` / `HEAD /api/v3/report-jobs/{job_id}/artifacts/{format}` | Ready file, SHA-256 ETag and byte-range support. |

Artifact links are normal authenticated same-origin downloads; the browser need not buffer a large workbook in JavaScript. A pending artifact returns 409. Ready downloads support `Range`, `If-Range`, and `If-None-Match`. Cloudflare streams directly from private R2; native deployments stream from disk. The private deployment remains one shared workspace behind its configured authentication boundary.

## Verification and scope

The focused tests cover full source/assessment identities, all four outcomes, raw/review separation, bounded capture reads, stale revisions, cancellation, restart, expired leases, artifact readiness, workbook safety, offline rendering and actual byte-range downloads. Worker runtime tests exercise real emulated D1/R2, typed dispatch and atomic checkpoint guards. A real browser test downloads a generated PDF through its native artifact link.

The optional `scripts.verify_report_jobs.verify_report_jobs` hook independently checks a newly generated full workbook, compressed snapshot and frozen input archive on a disposable restored Store. Its receipt records observed resource use and full identity/outcome fidelity. This is software reproducibility evidence. It does not establish semantic accuracy or production D1 capacity. The historical synchronous export endpoint remains available; its memory behavior is separate from this persistent job path.
