import { boundedJson, HttpError, validObjectKey } from "./http";

type Statement = { sql: string; params?: (string | number | null)[] };
const ARTIFACT_PART_SIZE = 8 * 1024 * 1024;

function objectKey(value: unknown): string {
  if (typeof value !== "string" || !validObjectKey(value)) throw new HttpError(400, "Invalid object key");
  return value;
}

function uploadId(value: unknown): string {
  if (typeof value !== "string" || value.length < 1 || value.length > 1024 || /[\r\n\0]/.test(value)) throw new HttpError(400, "Invalid multipart upload ID");
  return value;
}

export async function storageRoute(request: Request, env: Env, path: string): Promise<Response | null> {
  if (path === "/internal/storage/multipart/init" && request.method === "POST") {
    const input = await boundedJson<{ key: string; content_type?: string }>(request, 8192);
    const key = objectKey(input.key);
    const contentType = typeof input.content_type === "string" ? input.content_type.slice(0,256) : "application/octet-stream";
    const upload = await env.OBJECTS.createMultipartUpload(key, { httpMetadata: { contentType } });
    return Response.json({ key, upload_id: upload.uploadId });
  }
  if (path === "/internal/storage/multipart/abort" && request.method === "POST") {
    const input = await boundedJson<{ key: string; upload_id: string }>(request, 8192);
    await env.OBJECTS.resumeMultipartUpload(objectKey(input.key), uploadId(input.upload_id)).abort();
    return Response.json({ aborted: true });
  }
  if (path === "/internal/storage/multipart/complete" && request.method === "POST") {
    const input = await boundedJson<{ key: string; upload_id: string; parts: R2UploadedPart[] }>(request, 2 * 1024 * 1024);
    const key = objectKey(input.key);
    if (!Array.isArray(input.parts) || input.parts.length < 1 || input.parts.length > 10000) throw new HttpError(400, "Use between 1 and 10000 uploaded parts");
    const seen = new Set<number>();
    const parts = input.parts.map(part => {
      if (!Number.isInteger(part?.partNumber) || part.partNumber < 1 || part.partNumber > 10000 || seen.has(part.partNumber) || typeof part.etag !== "string" || !part.etag || part.etag.length > 256) throw new HttpError(400, "Invalid multipart completion manifest");
      seen.add(part.partNumber);
      return { partNumber: part.partNumber, etag: part.etag };
    });
    const object = await env.OBJECTS.resumeMultipartUpload(key, uploadId(input.upload_id)).complete(parts);
    return Response.json({ key, etag: object.etag });
  }
  const part = path.match(/^\/internal\/storage\/multipart\/([^/]+)\/parts\/(\d+)$/);
  if (part && request.method === "PUT") {
    const key = objectKey(new URL(request.url).searchParams.get("key"));
    const id = uploadId(decodeURIComponent(part[1]));
    const partNumber = Number(part[2]);
    if (!Number.isInteger(partNumber) || partNumber < 1 || partNumber > 10000) throw new HttpError(400, "Invalid part number");
    const sizeHeader = request.headers.get("content-length");
    if (sizeHeader === null) throw new HttpError(411, "Content-Length is required for artifact parts");
    const size = Number(sizeHeader);
    if (!Number.isSafeInteger(size) || size < 1 || size > ARTIFACT_PART_SIZE) throw new HttpError(413, "Artifact parts must be between 1 byte and 8 MiB");
    if (!request.body) throw new HttpError(400, "Part body required");
    // FixedLengthStream both enforces the declared size and supplies R2's known-length contract.
    const fixed = new FixedLengthStream(size);
    const [, uploaded] = await Promise.all([
      request.body.pipeTo(fixed.writable),
      env.OBJECTS.resumeMultipartUpload(key, id).uploadPart(partNumber, fixed.readable),
    ]);
    return Response.json({ partNumber: uploaded.partNumber, etag: uploaded.etag });
  }
  if (path === "/internal/storage/sql" && request.method === "POST") {
    const input = await boundedJson<{ statements: Statement[]; transaction?: boolean }>(request, 2 * 1024 * 1024);
    if (!Array.isArray(input.statements) || input.statements.length < 1 || input.statements.length > 100) throw new HttpError(400, "Use between 1 and 100 SQL statements");
    const prepared = input.statements.map(s => {
      if (typeof s.sql !== "string" || s.sql.length > 100000 || !Array.isArray(s.params ?? [])) throw new HttpError(400, "Invalid SQL statement");
      return env.DB.prepare(s.sql).bind(...(s.params ?? []));
    });
    // D1 batch is transactional; related writes never cross separate HTTP calls.
    const results = await env.DB.batch(prepared);
    return Response.json({ results: results.map(r => ({ rows: r.results ?? [], changes: r.meta.changes ?? 0 })) });
  }
  if (path.startsWith("/internal/storage/objects/")) {
    const key = decodeURIComponent(path.slice("/internal/storage/objects/".length));
    if (!validObjectKey(key)) throw new HttpError(400, "Invalid object key");
    if (request.method === "GET" || request.method === "HEAD") {
      const object = await env.OBJECTS.get(key);
      if (!object) throw new HttpError(404, "Object not found");
      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set("Content-Length", String(object.size));
      headers.set("ETag", object.httpEtag);
      return new Response(request.method === "HEAD" ? null : object.body, { headers });
    }
    if (request.method === "PUT") {
      if (!request.body) throw new HttpError(400, "Object body required");
      const object = await env.OBJECTS.put(key, request.body, { httpMetadata: { contentType: request.headers.get("content-type") ?? "application/octet-stream" } });
      return Response.json({ key, etag: object?.etag });
    }
    if (request.method === "DELETE") {
      await env.OBJECTS.delete(key);
      return Response.json({ deleted: true });
    }
    throw new HttpError(405, "Unsupported object method");
  }
  return null;
}
