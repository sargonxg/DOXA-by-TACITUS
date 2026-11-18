# Persistent import, indexing and guided publication

The one-page workbench now submits long indexing and guided-publication work as native persistent activities. ZIP uploads can also be handed to an import activity after the archive has been durably staged. Closing the browser does not cancel these jobs. This is a single-installation scheduler, not a shared SaaS queue or a throughput certification.

## Submission and recovery

`POST /api/v3/projects/{project_id}/jobs` returns HTTP 202. Supply an `Idempotency-Key` header of 1–128 printable non-space ASCII characters and one of these bodies:

```json
{"kind":"index","document_ids":["selected-source-id"]}
```

```json
{"kind":"guided_report","document_ids":["selected-source-id"],"formats":["pdf","html","xlsx","json"],"expert_commentary":[],"review_ack":false}
```

Index submission also accepts a current `query_id`; omitting a selection explicitly captures all currently ready sources, capped at 10,000. Guided publication retains its existing bounds of 100 sources, 2,000 blocks/decisions and 500,000 characters. Include a `run_id` only when the saved analysis used the same frozen inquiry brief and unchanged source metadata. Historical analyses retain their original casebook/report path. Submitting the same key and inputs returns the same activity; a conflicting payload returns409. User-supplied actor or review-authority fields are rejected.

Inspect `GET /api/v3/jobs/{job_id}`, cancel with `POST .../cancel`, and explicitly restart failed/cancelled/paused work with `POST .../restart`. Restarting preserves captured inputs. Changed/corrupt frozen sources require a new activity. `GET /api/v3/projects/{project_id}/jobs` returns `{items,total}`; `/activity` also includes existing analysis runs, logical import sessions and report jobs, preserving their original identities.

The server starts one activity worker by default in native mode. It checkpoints between bounded operations and does not download models. Scheduling deadlines yield after a source is committed; they are not hard per-source runtime promises. Cancellation and access checks also run within archive copying and embedding batches. Extraction retains its separate parser timeout. Indexing needs an explicitly installed CPU runtime; a missing runtime produces actionable HTTP 422 before a job is scheduled. Runtime failures during execution remain visible and recoverable. Accepted document bundles are reused after interruption, while the previous active index stays available until a new complete manifest is published. Indexing is passage retrieval, not prejudice classification.

## Guided reports reuse the report engine

Source text, wording observations, the inquiry, original contextual decisions and server-attributed notes are captured at submission. Each requested format becomes a deterministic child of the existing persistent report engine, with its existing bounded renderer process, cancellation, verified artifacts and resumable downloads. The activity provides one grouped completion result. It does not reread edited sources to change a pending report.

The report panel restores both earlier synchronous reports and new queued publications. It polls pending work while open, exposes cancellation/restart, and presents verified downloads when complete. Independent reference status remains pending unless separate evaluation establishes otherwise; operator commentary does not certify expertise or model accuracy.

## Durable ZIP intake

Create a logical import session with the existing imports endpoint. Then upload `multipart/form-data` to `/api/v3/projects/{project_id}/jobs/import-archive`, using fields `import_id` and `file`, plus the idempotency header. Transport is capped at 500 MiB; an archive supports at most 10,000 files and 1 GiB expanded content. Unsafe paths, repeated names, symlinks and encrypted members are rejected before durable execution.

The worker verifies the staged archive hash, commits each source before advancing its cursor, and reuses the existing import commit identities. A corrupt document can become an explicit failed source while other files finish. The activity reports `partial` when its submitted archive contains source failures; its ready/failed counts cover that archive rather than unrelated groups in the same logical session.

## Authority, privacy and operating limits

When `DOXA_REQUIRE_ACCEPTED_QUOTE=true`, both queued and legacy guided publication also require an explicitly accepted operator-issued `quote_id`. The report panel lists accepted quotes without accepting one automatically. Source counts, characters, rubric criteria and OCR pages are derived from the frozen inquiry and sources; the request cannot supply its own smaller scope. The activity and renderer recheck quote authority before rendering and publication, so a revoked or cap-exceeded quote cannot authorize new output. Quote identifiers remain operational metadata rather than changing the analytical source snapshot.

Jobs capture server-identified executors and recheck their current write/analyze/export permission during work. Export policy is checked during publication and download. Revocation pauses the activity; an authorized account must explicitly restart it. Viewer access does not permit cancelling another user's activity.

Ownership is registered before writing captured inputs, archives, caches and artifacts. Actual worker threads retain project activity barriers, so erasure waits for live writers even if an HTTP awaiter is cancelled. Low-memory mode serializes heavy work in the actual workers; import intake acquires its extraction slot inside its own extraction thread rather than holding a second slot while awaiting it.

No job indicates that all imported text was successfully interpreted. Ready sources, index coverage, retained model decisions, report completion and human review remain distinct quantities. No commercial-scale capacity or fresh-inference speed follows from offline replay.
