import { boundedJson, HttpError } from "./http";

export const PART_SIZE = 8 * 1024 * 1024;
export const MAX_FILE_SIZE = 500 * 1024 * 1024;
interface Upload {
  id: string; project_id: string; object_key: string; r2_upload_id: string;
  filename: string; mime_type: string; size: number; status: string; result_json: string | null;
}

export async function uploadRoute(request: Request, env: Env, path: string): Promise<Response | null> {
  const init = path.match(/^\/api\/projects\/([a-zA-Z0-9_-]+)\/uploads\/init$/);
  if (init && request.method === "POST") {
    const input = await boundedJson<{ filename: string; size: number; mime_type?: string }>(request, 8192);
    if (typeof input.filename !== "string" || !input.filename.trim() || input.filename.length > 255 || /[\r\n\0]/.test(input.filename)) throw new HttpError(400, "Invalid filename");
    if (!Number.isSafeInteger(input.size) || input.size < 1 || input.size > MAX_FILE_SIZE) throw new HttpError(413, "Each file must be between 1 byte and 500 MiB");
    const project = await env.DB.prepare("SELECT id FROM projects WHERE id=?").bind(init[1]).first();
    if (!project) throw new HttpError(404, "Project not found");
    const id = crypto.randomUUID();
    const key = `originals/upload-${id}/source`;
    const mime = typeof input.mime_type === "string" ? input.mime_type.slice(0,150) : "application/octet-stream";
    const multipart = await env.OBJECTS.createMultipartUpload(key, { httpMetadata: { contentType: mime } });
    try {
      await env.DB.prepare("INSERT INTO cloud_uploads (id,project_id,object_key,r2_upload_id,filename,mime_type,size,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)").bind(id, init[1], key, multipart.uploadId, input.filename, mime, input.size, Date.now(), Date.now()).run();
    } catch (error) {
      await multipart.abort();
      throw error;
    }
    return Response.json({ id, upload_id: id, part_size: PART_SIZE }, { status: 201 });
  }
  const route = path.match(/^\/api\/uploads\/([a-zA-Z0-9_-]+)(?:\/(parts)\/(\d+)|\/(complete))?$/);
  if (!route) return null;
  const upload = await env.DB.prepare("SELECT * FROM cloud_uploads WHERE id=?").bind(route[1]).first<Upload>();
  if (!upload) throw new HttpError(404, "Upload not found or expired");
  if (request.method === "DELETE" && !route[2] && !route[4]) {
    if (upload.status === "completed") throw new HttpError(409, "Upload is already completed");
    try { await env.OBJECTS.resumeMultipartUpload(upload.object_key, upload.r2_upload_id).abort(); } catch { /* may already have completed */ }
    await env.OBJECTS.delete(upload.object_key);
    await env.DB.batch([env.DB.prepare("DELETE FROM cloud_upload_parts WHERE upload_id=?").bind(upload.id), env.DB.prepare("DELETE FROM cloud_uploads WHERE id=?").bind(upload.id)]);
    return Response.json({ aborted: true });
  }
  if (route[2] && request.method === "PUT") {
    if (upload.status !== "uploading") throw new HttpError(409, "Upload is not accepting parts");
    const partNumber = Number(route[3]);
    const count = Math.ceil(upload.size / PART_SIZE);
    if (!Number.isInteger(partNumber) || partNumber < 1 || partNumber > count) throw new HttpError(400, "Invalid part number");
    const expected = Math.min(PART_SIZE, upload.size - (partNumber - 1) * PART_SIZE);
    const declared = request.headers.get("content-length");
    if (declared && Number(declared) !== expected) throw new HttpError(400, "Part size does not match the declared file size");
    // Each part is bounded at 8 MiB; retaining it permits exact length validation before R2 commit.
    const reader = request.body?.getReader();
    if (!reader) throw new HttpError(400, "Part body required");
    const buffers: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      if (size > expected) { await reader.cancel(); throw new HttpError(413, "Part exceeds the expected bounded size"); }
      buffers.push(item.value);
    }
    if (size !== expected) throw new HttpError(400, "Part is incomplete");
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of buffers) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const part = await env.OBJECTS.resumeMultipartUpload(upload.object_key, upload.r2_upload_id).uploadPart(partNumber, bytes);
    await env.DB.batch([
      env.DB.prepare("INSERT INTO cloud_upload_parts (upload_id,part_number,etag,size) VALUES (?,?,?,?) ON CONFLICT(upload_id,part_number) DO UPDATE SET etag=excluded.etag,size=excluded.size").bind(upload.id, partNumber, part.etag, size),
      env.DB.prepare("UPDATE cloud_uploads SET updated_at=? WHERE id=?").bind(Date.now(), upload.id),
    ]);
    return Response.json({ partNumber: part.partNumber, etag: part.etag });
  }
  if (route[4] && request.method === "POST") {
    if (upload.status === "completed" && upload.result_json) return new Response(upload.result_json, { headers: { "content-type": "application/json" } });
    const input = await boundedJson<{ parts: R2UploadedPart[] }>(request, 32768);
    const stored = await env.DB.prepare("SELECT part_number,etag,size FROM cloud_upload_parts WHERE upload_id=? ORDER BY part_number").bind(upload.id).all<{ part_number: number; etag: string; size: number }>();
    if (!Array.isArray(input.parts) || input.parts.length !== Math.ceil(upload.size / PART_SIZE) || stored.results.length !== input.parts.length) throw new HttpError(400, "All file parts must be uploaded before completion");
    const parts = stored.results.map((p, i) => {
      const submitted = input.parts[i];
      if (p.part_number !== i + 1 || submitted?.partNumber !== p.part_number || submitted?.etag !== p.etag) throw new HttpError(400, "Part manifest does not match uploaded parts");
      return { partNumber: p.part_number, etag: p.etag };
    });
    // R2 completion and ingestion are retryable: a committed staged object can be registered again after a transient container failure.
    let object = await env.OBJECTS.head(upload.object_key);
    if (!object) object = await env.OBJECTS.resumeMultipartUpload(upload.object_key, upload.r2_upload_id).complete(parts);
    if (object.size !== upload.size) throw new HttpError(400, "Completed file size mismatch");
    await env.DB.prepare("UPDATE cloud_uploads SET status='ingesting',updated_at=? WHERE id=?").bind(Date.now(), upload.id).run();
    const response = await env.PYTHON.getByName("pool-0").fetch(new Request("http://container/internal/uploads/complete", {
      method: "POST", headers: { "content-type": "application/json", "x-doxa-service-token": env.DOXA_SERVICE_TOKEN },
      body: JSON.stringify({ project_id: upload.project_id, key: upload.object_key, filename: upload.filename, size: upload.size, mime_type: upload.mime_type }),
      signal: AbortSignal.timeout(240000),
    }));
    if (!response.ok) return response;
    const result = await boundedJson<Record<string, unknown>>(response, 262144);
    const resultJson = JSON.stringify(result);
    await env.DB.prepare("UPDATE cloud_uploads SET status='completed',result_json=?,updated_at=? WHERE id=?").bind(resultJson, Date.now(), upload.id).run();
    // Python registers this immutable original key directly; no second 500 MiB upload through Workers.
    return new Response(resultJson, { headers: { "content-type": "application/json" } });
  }
  throw new HttpError(405, "Unsupported upload method");
}
