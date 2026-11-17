import { boundedJson, HttpError } from "./http";

export type WorkMessage = { run_id: string } | { kind: "report"; job_id: string };

export async function enqueueReport(env: Env, jobId: string): Promise<void> {
  if (typeof jobId !== "string" || !/^[a-zA-Z0-9_-]{1,120}$/.test(jobId)) throw new HttpError(400, "Invalid report job ID");
  // The report_jobs row is the durable outbox. Recovery finds queued/expired jobs.
  await env.WORK_QUEUE.send({ kind: "report", job_id: jobId });
}

export async function enqueue(env: Env, runId: string): Promise<void> {
  if (typeof runId !== "string" || !/^[a-zA-Z0-9_-]{1,120}$/.test(runId)) throw new HttpError(400, "Invalid run ID");
  await env.DB.prepare("INSERT INTO cloud_dispatch (run_id,status,updated_at) VALUES (?, 'pending', ?) ON CONFLICT(run_id) DO UPDATE SET status='pending',updated_at=excluded.updated_at").bind(runId, Date.now()).run();
  await env.WORK_QUEUE.send({ run_id: runId });
  await env.DB.prepare("UPDATE cloud_dispatch SET status='queued', updated_at=? WHERE run_id=? AND status='pending'").bind(Date.now(), runId).run();
}

export async function consume(batch: MessageBatch<WorkMessage>, env: Env): Promise<void> {
  for (const message of batch.messages) {
    if (!message.body || typeof message.body !== "object") { message.ack(); continue; }
    if (message.body && "kind" in message.body && message.body.kind === "report") {
      const jobId = message.body.job_id;
      if (typeof jobId !== "string" || !/^[a-zA-Z0-9_-]{1,120}$/.test(jobId)) { message.ack(); continue; }
      try {
        const response = await env.PYTHON.getByName("pool-0").fetch(new Request(`http://container/internal/report-work/${jobId}`, {
          method: "POST", headers: { "x-doxa-service-token": env.DOXA_SERVICE_TOKEN, "content-type": "application/json" },
          body: JSON.stringify({ deadline_seconds: 240 }), signal: AbortSignal.timeout(300000),
        }));
        if (response.status !== 200) { await response.body?.cancel(); throw new Error(`Report work returned HTTP ${response.status}`); }
        const result = await boundedJson<{ pending: boolean }>(response, 65536);
        if (typeof result.pending !== "boolean") throw new Error("Report work must return committed pending state");
        if (result.pending) await enqueueReport(env, jobId);
        message.ack();
      } catch {
        console.error(JSON.stringify({ event: "report_work_retry", job_id: jobId }));
        message.retry({ delaySeconds: 30 });
      }
      continue;
    }
    if (!message.body || !("run_id" in message.body)) { message.ack(); continue; }
    const runId = message.body?.run_id;
    if (!runId || !/^[a-zA-Z0-9_-]{1,120}$/.test(runId)) {
      message.ack();
      continue;
    }
    try {
      await env.DB.prepare("UPDATE cloud_dispatch SET status='processing',updated_at=? WHERE run_id=?").bind(Date.now(), runId).run();
      const headers = { "x-doxa-service-token": env.DOXA_SERVICE_TOKEN, "content-type": "application/json" };
      const response = await env.PYTHON.getByName("pool-0").fetch(new Request(`http://container/internal/work/${runId}`, {
        method: "POST", headers, body: JSON.stringify({ deadline_seconds: 420 }), signal: AbortSignal.timeout(480000),
      }));
      // 202 only says accepted. Acknowledge only after Python has committed its bounded work.
      if (response.status !== 200) { await response.body?.cancel(); throw new Error(`Work returned HTTP ${response.status}`); }
      const result = await boundedJson<{ pending: boolean }>(response, 65536);
      if (typeof result.pending !== "boolean") throw new Error("Work response must contain committed pending state");
      await env.DB.prepare("UPDATE cloud_dispatch SET attempts=0,error=NULL WHERE run_id=?").bind(runId).run();
      if (result.pending) await enqueue(env, runId);
      else await env.DB.prepare("UPDATE cloud_dispatch SET status='complete',updated_at=?,error=NULL WHERE run_id=?").bind(Date.now(), runId).run();
      message.ack();
    } catch (error) {
      const summary = error instanceof Error ? error.message.slice(0,300) : "Dispatch failed";
      await env.DB.prepare("UPDATE cloud_dispatch SET status='pending',updated_at=?,attempts=attempts+1,error=? WHERE run_id=?").bind(Date.now(), summary, runId).run();
      console.error(JSON.stringify({ event: "work_retry", run_id: runId, error: summary }));
      message.retry({ delaySeconds: 30 });
    }
  }
}

export async function recoverDispatch(env: Env): Promise<void> {
  const cutoff = Date.now() - 12 * 60 * 1000;
  const result = await env.DB.prepare("SELECT run_id FROM cloud_dispatch WHERE status!='complete' AND updated_at<? AND attempts<20 LIMIT 20").bind(cutoff).all<{ run_id: string }>();
  for (const row of result.results) await enqueue(env, row.run_id);
  if (await env.DB.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='report_jobs'").first()) {
    const reports = await env.DB.prepare("SELECT id FROM report_jobs WHERE status='queued' OR(status='running' AND COALESCE(lease_until,0)<?) ORDER BY created_at LIMIT 20").bind(Date.now() / 1000).all<{ id: string }>();
    for (const row of reports.results) await enqueueReport(env, row.id);
  }
  // Expired staged uploads are never left indefinitely in a private bucket.
  const expired = await env.DB.prepare("SELECT id,object_key,r2_upload_id,status FROM cloud_uploads WHERE updated_at<? AND status!='completed' LIMIT 20").bind(Date.now() - 86400000).all<{ id:string; object_key:string; r2_upload_id:string; status:string }>();
  for (const row of expired.results) {
    // Registration may have committed just before its HTTP response was lost.
    // Never collect an original that a durable document record already references.
    const registered = await env.DB.prepare("SELECT id FROM documents WHERE object_key=? LIMIT 1").bind(row.object_key).first();
    if (registered) {
      await env.DB.prepare("UPDATE cloud_uploads SET status='completed',updated_at=? WHERE id=?").bind(Date.now(), row.id).run();
      continue;
    }
    try { await env.OBJECTS.resumeMultipartUpload(row.object_key, row.r2_upload_id).abort(); } catch { /* already completed or absent */ }
    await env.OBJECTS.delete(row.object_key);
    await env.DB.batch([env.DB.prepare("DELETE FROM cloud_upload_parts WHERE upload_id=?").bind(row.id), env.DB.prepare("DELETE FROM cloud_uploads WHERE id=?").bind(row.id)]);
  }
}
