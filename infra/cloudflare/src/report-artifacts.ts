import { HttpError } from "./http";

type Artifact = { object_key: string; filename: string; bytes: number; sha256: string; content_type: string; status: string; scope_hash: string; snapshot_version: string };

/** Access authentication is enforced by the caller before reaching this route. */
export async function reportArtifactRoute(request: Request, env: Env, path: string): Promise<Response | null> {
  const match = /^\/api\/v3\/report-jobs\/([a-zA-Z0-9_-]{1,120})\/artifacts\/([a-z0-9_-]{1,20})$/.exec(path);
  if (!match || !["GET", "HEAD"].includes(request.method)) return null;
  const item = await env.DB.prepare("SELECT a.*,j.status,j.scope_hash,j.snapshot_version FROM report_jobs j LEFT JOIN report_job_artifacts a ON a.job_id=j.id AND a.format=? WHERE j.id=?").bind(match[2], match[1]).first<Artifact>();
  if (!item) throw new HttpError(404, "Report artifact not found");
  if (item.status !== "completed") throw new HttpError(409, "Report artifacts are not ready");
  if (!item.object_key) throw new HttpError(404, "Report artifact not found");
  const etag = `"${item.sha256}"`;
  const headers = new Headers({ "Content-Type": item.content_type, "ETag": etag, "Accept-Ranges": "bytes", "X-DOXA-Scope": item.scope_hash, "X-DOXA-Snapshot": item.snapshot_version, "Cache-Control": "private, no-cache", "Content-Disposition": `attachment; filename="${item.filename.replace(/[^a-zA-Z0-9_.-]/g, "_")}"` });
  if (request.headers.get("if-none-match")?.split(",").map(value => value.trim().replace(/^W\//, "")).some(value => value === etag || value === "*")) return new Response(null, { status: 304, headers });
  let offset = 0, length = item.bytes, status = 200;
  const range = request.headers.get("range");
  if (range && (!request.headers.get("if-range") || request.headers.get("if-range") === etag)) {
    const parsed = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!parsed || (!parsed[1] && !parsed[2])) { headers.set("Content-Range", `bytes */${item.bytes}`); return new Response(null, { status: 416, headers }); }
    if (parsed[1]) {
      offset = Number(parsed[1]);
      const end = parsed[2] ? Math.min(Number(parsed[2]), item.bytes - 1) : item.bytes - 1;
      length = end - offset + 1;
    } else {
      length = Math.min(Number(parsed[2]), item.bytes);
      offset = item.bytes - length;
    }
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(length) || offset < 0 || length <= 0 || offset >= item.bytes) {
      headers.set("Content-Range", `bytes */${item.bytes}`); return new Response(null, { status: 416, headers });
    }
    status = 206;
    headers.set("Content-Range", `bytes ${offset}-${offset + length - 1}/${item.bytes}`);
  }
  headers.set("Content-Length", String(length));
  if (request.method === "HEAD") {
    const object = await env.OBJECTS.head(item.object_key);
    if (!object || object.size !== item.bytes) throw new HttpError(503, "Artifact integrity verification failed");
    return new Response(null, { status, headers });
  }
  const object = await env.OBJECTS.get(item.object_key, status === 206 ? { range: { offset, length } } : undefined);
  if (!object || object.size !== item.bytes) throw new HttpError(503, "Artifact integrity verification failed");
  // R2 is private and artifact keys immutable; readiness follows a verified upload.
  return new Response(object.body, { status, headers });
}
