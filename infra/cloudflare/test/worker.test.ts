import { env } from "cloudflare:workers";
import { beforeAll, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { consume, recoverDispatch } from "../src/queue";
import { validObjectKey } from "../src/http";
import migration from "../migrations/0001_cloud.sql?raw";

const token = "test-only-strong-service-token-0123456789abcdef";
function request(path: string, method = "GET", body?: unknown, authenticated = true): Request {
  const headers: Record<string,string> = {};
  if (authenticated) headers["x-doxa-service-token"] = token;
  if (body !== undefined) headers["content-type"] = "application/json";
  return new Request(`https://test.invalid${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
}
beforeAll(async () => {
  await env.DB.exec(migration.replace(/\n/g, " "));
  await env.DB.exec("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY); INSERT OR IGNORE INTO projects VALUES ('test-project');");
  await env.DB.exec("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, object_key TEXT);");
});

describe("private cloud boundary", () => {
  it("rejects unsigned Access headers and unauthenticated API/internal access", async () => {
    expect((await worker.fetch(request("/api/projects", "GET", undefined, false), env)).status).toBe(401);
    const forged = request("/internal/storage/sql", "POST", { statements: [{ sql: "SELECT 1" }] }, false);
    forged.headers.set("cf-access-authenticated-user-email", "someone@example.com");
    forged.headers.set("cf-access-jwt-assertion", "forged.jwt.value");
    expect((await worker.fetch(forged, env)).status).toBe(401);
  });
  it("keeps health public without exposing workspace records", async () => {
    const response = await worker.fetch(request("/healthz", "GET", undefined, false), env);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok", authentication: "required", storage: "D1 + R2" });
    expect(response.headers.get("x-frame-options")).toBe("DENY");
  });
  it("rejects cross-origin state changes", async () => {
    const input = request("/api/projects", "POST", { name: "bad" });
    input.headers.set("origin", "https://unrelated.invalid");
    expect((await worker.fetch(input, env)).status).toBe(403);
  });
  it("forwards validated browser writes across the container Host boundary", async () => {
    const fetch = vi.fn(async (forwarded: Request) => {
      expect(forwarded.headers.get("origin")).toBeNull();
      expect(forwarded.headers.get("cookie")).toBeNull();
      expect(forwarded.headers.get("x-doxa-service-token")).toBe(token);
      return Response.json({ id: "created" }, { status: 201 });
    });
    const testEnv = { ...env, PYTHON: { getByName: () => ({ fetch }) } } as unknown as Env;
    const input = request("/api/projects", "POST", { name: "Private project" });
    input.headers.set("origin", "https://test.invalid");
    input.headers.set("cookie", "CF_Authorization=test-cookie");
    expect((await worker.fetch(input, testEnv)).status).toBe(201);
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("D1 and R2 persistence bridges", () => {
  it("commits a SQL batch and rolls back all writes when one statement fails", async () => {
    let response = await worker.fetch(request("/internal/storage/sql", "POST", { statements: [{ sql: "CREATE TABLE IF NOT EXISTS atomic_test (id INTEGER PRIMARY KEY, value TEXT)", params: [] }, { sql: "INSERT INTO atomic_test VALUES (1, ?)", params: ["kept"] }] }), env);
    expect(response.status).toBe(200);
    response = await worker.fetch(request("/internal/storage/sql", "POST", { statements: [{ sql: "INSERT INTO atomic_test VALUES (2, 'rollback')" }, { sql: "INSERT INTO missing_table VALUES (1)" }] }), env);
    expect(response.status).toBe(500);
    const rows = await env.DB.prepare("SELECT * FROM atomic_test ORDER BY id").all();
    expect(rows.results).toEqual([{ id: 1, value: "kept" }]);
  });
  it("preserves changes() guards between statements in an atomic D1 batch", async () => {
    await env.DB.exec("CREATE TABLE IF NOT EXISTS reservation_guard (id INTEGER PRIMARY KEY, settled INTEGER);");
    await env.DB.batch([env.DB.prepare("INSERT OR IGNORE INTO reservation_guard VALUES (1,0)"), env.DB.prepare("UPDATE reservation_guard SET settled=1 WHERE id=1 AND changes()=1")]);
    expect(await env.DB.prepare("SELECT settled FROM reservation_guard WHERE id=1").first()).toEqual({ settled: 1 });
    await env.DB.batch([env.DB.prepare("INSERT OR IGNORE INTO reservation_guard VALUES (1,0)"), env.DB.prepare("UPDATE reservation_guard SET settled=2 WHERE id=1 AND changes()=1")]);
    expect(await env.DB.prepare("SELECT settled FROM reservation_guard WHERE id=1").first()).toEqual({ settled: 1 });
  });
  it("persists artifacts independently of a container process", async () => {
    const put = new Request("https://test.invalid/internal/storage/objects/checkpoints/test.json", { method: "PUT", headers: { "x-doxa-service-token": token, "content-type": "application/json" }, body: '{"committed":true}' });
    expect((await worker.fetch(put, env)).status).toBe(200);
    const read = await worker.fetch(request("/internal/storage/objects/checkpoints/test.json"), env);
    expect(await read.json()).toEqual({ committed: true });
    expect((await worker.fetch(request("/internal/storage/objects/checkpoints/test.json", "DELETE"), env)).status).toBe(200);
    expect((await worker.fetch(request("/internal/storage/objects/checkpoints/test.json"), env)).status).toBe(404);
    expect(validObjectKey("../private")).toBe(false);
    expect(validObjectKey("originals/id/source.pdf")).toBe(true);
  });
  it("streams trusted artifact parts to real R2 and supports completion and abort", async () => {
    const key = "reports/multipart-test/result.bin";
    const init = await worker.fetch(request("/internal/storage/multipart/init", "POST", { key, content_type: "application/octet-stream" }), env);
    expect(init.status).toBe(200);
    const upload = await init.json() as { key: string; upload_id: string };
    const path = `/internal/storage/multipart/${encodeURIComponent(upload.upload_id)}/parts/1?key=${encodeURIComponent(key)}`;
    const bytes = new TextEncoder().encode("committed artifact bytes");
    const input = new Request(`https://test.invalid${path}`, { method: "PUT", headers: { "x-doxa-service-token": token, "content-length": String(bytes.byteLength) }, body: bytes });
    const response = await worker.fetch(input, env);
    expect(response.status).toBe(200);
    const uploaded = await response.json() as { partNumber: number; etag: string };
    expect(uploaded.partNumber).toBe(1);
    expect((await worker.fetch(request("/internal/storage/multipart/complete", "POST", { key, upload_id: upload.upload_id, parts: [uploaded] }), env)).status).toBe(200);
    expect(await (await env.OBJECTS.get(key))?.text()).toBe("committed artifact bytes");
    const abortInit = await worker.fetch(request("/internal/storage/multipart/init", "POST", { key: "reports/multipart-test/abort.bin" }), env);
    const abandoned = await abortInit.json() as { key: string; upload_id: string };
    expect((await worker.fetch(request("/internal/storage/multipart/abort", "POST", abandoned), env)).status).toBe(200);
  });
});

describe("bounded multipart uploads", () => {
  it("validates declared file sizes and retains uploaded part metadata", async () => {
    expect((await worker.fetch(request("/api/projects/test-project/uploads/init", "POST", { filename: "x.pdf", size: 501 * 1024 * 1024 }), env)).status).toBe(413);
    const init = await worker.fetch(request("/api/projects/test-project/uploads/init", "POST", { filename: "source.txt", size: 3, mime_type: "text/plain" }), env);
    expect(init.status).toBe(201);
    const upload = await init.json() as { id: string; part_size: number };
    expect(upload.part_size).toBe(8388608);
    const part = await worker.fetch(new Request(`https://test.invalid/api/uploads/${upload.id}/parts/1`, { method: "PUT", headers: { "x-doxa-service-token": token }, body: "abc" }), env);
    expect(part.status).toBe(200);
    expect(await part.json()).toMatchObject({ partNumber: 1 });
    const stored = await env.DB.prepare("SELECT size FROM cloud_upload_parts WHERE upload_id=?").bind(upload.id).first();
    expect(stored).toEqual({ size: 3 });
    expect((await worker.fetch(request(`/api/uploads/${upload.id}`, "DELETE"), env)).status).toBe(200);
  });
  it("preserves a registered original if the completion response was lost", async () => {
    const key = "originals/upload-registered/source";
    await env.OBJECTS.put(key, "source");
    await env.DB.prepare("INSERT INTO documents (id,object_key) VALUES (?,?)").bind("registered-doc", key).run();
    await env.DB.prepare("INSERT INTO cloud_uploads (id,project_id,object_key,r2_upload_id,filename,mime_type,size,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'ingesting',0,0)").bind("registered-upload", "test-project", key, "old-upload", "source.txt", "text/plain", 6).run();
    await recoverDispatch(env);
    expect(await env.OBJECTS.head(key)).not.toBeNull();
    expect(await env.DB.prepare("SELECT status FROM cloud_uploads WHERE id='registered-upload'").first()).toEqual({ status: "completed" });
  });
});

describe("queue commit semantics", () => {
  it("never acknowledges a 202 acceptance as completed work", async () => {
    const ack = vi.fn();
    const retry = vi.fn();
    const fakeContainer = { fetch: vi.fn(async () => new Response('{"pending":true}', { status: 202 })) };
    const testEnv = { ...env, PYTHON: { getByName: () => fakeContainer } } as unknown as Env;
    const message = { body: { run_id: "run-test" }, ack, retry };
    await consume({ messages: [message] } as unknown as MessageBatch<{ run_id: string }>, testEnv);
    expect(ack).not.toHaveBeenCalled();
    expect(retry).toHaveBeenCalledOnce();
  });
  it("acknowledges only after the executor returns committed terminal state", async () => {
    const ack = vi.fn();
    const retry = vi.fn();
    const fakeContainer = { fetch: vi.fn(async () => Response.json({ pending: false })) };
    const testEnv = { ...env, PYTHON: { getByName: () => fakeContainer } } as unknown as Env;
    const message = { body: { run_id: "run-done" }, ack, retry };
    await consume({ messages: [message] } as unknown as MessageBatch<{ run_id: string }>, testEnv);
    expect(ack).toHaveBeenCalledOnce();
    expect(retry).not.toHaveBeenCalled();
  });
});

describe("private inference binding", () => {
  it("blocks unauthenticated paid calls and retired/unlisted models", async () => {
    const run = vi.fn();
    const testEnv = { ...env, AI: { run } } as unknown as Env;
    expect((await worker.fetch(request("/internal/inference/v1/chat/completions", "POST", {}, false), testEnv)).status).toBe(401);
    expect((await worker.fetch(request("/internal/inference/v1/chat/completions", "POST", { model: "@cf/qwen/qwen3-30b-a3b-fp8" }), testEnv)).status).toBe(400);
    expect(run).not.toHaveBeenCalled();
  });
  it("accepts the container bearer token and keeps inference private and bounded", async () => {
    const run = vi.fn(async () => ({ choices: [{ message: { role: "assistant", content: '{"findings":[]}' }, finish_reason: "stop" }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
    const testEnv = { ...env, AI: { run } } as unknown as Env;
    const input = new Request("https://test.invalid/internal/inference/v1/chat/completions", { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify({ model: "@cf/google/gemma-4-26b-a4b-it", messages: [{ role: "user", content: "Analyze the quoted source." }], response_format: { type: "json_schema", json_schema: { name: "findings", schema: { type: "object", properties: { findings: { type: "array" } } } } }, max_tokens: 3000 }) });
    const response = await worker.fetch(input, testEnv);
    expect(response.status).toBe(200);
    expect(run).toHaveBeenCalledWith("@cf/google/gemma-4-26b-a4b-it", expect.objectContaining({ store: false, chat_template_kwargs: { enable_thinking: false }, max_completion_tokens: 3000 }));
    expect(await response.json()).toMatchObject({ model: "@cf/google/gemma-4-26b-a4b-it", usage: { completion_tokens: 5 } });
  });
});
