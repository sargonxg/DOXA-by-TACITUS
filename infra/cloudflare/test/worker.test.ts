import { env } from "cloudflare:workers";
import { beforeAll, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { consume, recoverDispatch } from "../src/queue";
import { validObjectKey } from "../src/http";
import migration from "../migrations/0001_cloud.sql?raw";
import reportSchema from "../../../doxa/report_jobs_schema.sql?raw";
import contextualSchema from "../../../doxa/v3_schema.sql?raw";

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
  await env.DB.exec(contextualSchema.replace(/\n/g, " "));
  await env.DB.exec(reportSchema.replace(/\n/g, " "));
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
  it("dispatches typed report work and refuses acceptance without a committed checkpoint", async () => {
    const ack = vi.fn(), retry = vi.fn();
    const fetch = vi.fn(async (input: Request) => {
      expect(new URL(input.url).pathname).toBe("/internal/report-work/report-typed");
      expect(input.headers.get("x-doxa-service-token")).toBe(token);
      expect(await input.json()).toEqual({ deadline_seconds: 240 });
      return Response.json({ pending: true }, { status: 202 });
    });
    const testEnv = { ...env, PYTHON: { getByName: () => ({ fetch }) } } as unknown as Env;
    await consume({ messages: [{ body: { kind: "report", job_id: "report-typed" }, ack, retry }] } as unknown as MessageBatch<import("../src/queue").WorkMessage>, testEnv);
    expect(ack).not.toHaveBeenCalled(); expect(retry).toHaveBeenCalledOnce();
    fetch.mockImplementationOnce(async () => Response.json({ pending: false }));
    await consume({ messages: [{ body: { kind: "report", job_id: "report-typed" }, ack, retry }] } as unknown as MessageBatch<import("../src/queue").WorkMessage>, testEnv);
    expect(ack).toHaveBeenCalledOnce();
  });
  it("persists report enqueue intent independently of the delivery attempt", async () => {
    const send = vi.fn(async () => {});
    const testEnv = { ...env, WORK_QUEUE: { send } } as unknown as Env;
    const result = await worker.fetch(request("/internal/storage/enqueue", "POST", { kind: "report", job_id: "report-enqueue" }), testEnv);
    expect(result.status).toBe(200);
    expect(send).toHaveBeenCalledWith({ kind: "report", job_id: "report-enqueue" });
    expect((await worker.fetch(request("/internal/storage/enqueue", "POST", { kind: "report", job_id: "../bad" }), testEnv)).status).toBe(400);
    expect((await worker.fetch(request("/internal/storage/enqueue", "POST", { kind: "report" }), testEnv)).status).toBe(400);
  });
  it("recovers a queued report and commits its input checkpoint with a D1 lease guard", async () => {
    await env.DB.prepare("INSERT INTO report_jobs(id,project_id,query_id,publication_type,format,profile,status,scope_hash,snapshot_version,config_key,created_at,updated_at,lease_token) VALUES('report-recover','test-project','q','briefing','html','briefing','queued','scope','revision','unused','now','now','lease')").run();
    const send = vi.fn(async () => {});
    await recoverDispatch({ ...env, WORK_QUEUE: { send } } as unknown as Env);
    expect(send).toHaveBeenCalledWith({ kind: "report", job_id: "report-recover" });
    await env.DB.prepare("UPDATE report_jobs SET status='running' WHERE id='report-recover'").run();
    const commit = async (lease: string, ordinal: number) => env.DB.batch([
      env.DB.prepare("INSERT INTO report_job_parts(job_id,stage,ordinal,object_key,bytes,sha256,row_count,last_key) SELECT 'report-recover','documents',?,'part',10,'hash',2,'last' WHERE EXISTS(SELECT 1 FROM report_jobs WHERE id='report-recover' AND lease_token=? AND status='running' AND cancel_requested=0)").bind(ordinal, lease),
      env.DB.prepare("UPDATE report_jobs SET state_json='checkpoint' WHERE id='report-recover' AND lease_token=? AND changes()=1").bind(lease),
    ]);
    expect((await commit("stale", 0))[0].meta.changes).toBe(0);
    expect((await commit("lease", 0))[0].meta.changes).toBe(1);
    expect(await env.DB.prepare("SELECT state_json FROM report_jobs WHERE id='report-recover'").first()).toEqual({ state_json: "checkpoint" });
    await env.DB.prepare("UPDATE report_jobs SET cancel_requested=1,state_json='cancelled checkpoint' WHERE id='report-recover'").run();
    expect((await commit("lease", 1))[0].meta.changes).toBe(0);
    expect(await env.DB.prepare("SELECT state_json FROM report_jobs WHERE id='report-recover'").first()).toEqual({ state_json: "cancelled checkpoint" });
  });
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

describe("immutable report artifact streaming", () => {
  it("serves authenticated full/range/conditional/HEAD reads directly from real R2", async () => {
    const bytes = new TextEncoder().encode("actual immutable report bytes");
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(value => value.toString(16).padStart(2, "0")).join("");
    await env.OBJECTS.put("report-jobs/download-test/report.pdf", bytes);
    await env.DB.prepare("INSERT INTO report_jobs(id,project_id,query_id,publication_type,format,profile,status,scope_hash,snapshot_version,config_key,created_at,updated_at) VALUES('download-test','test-project','q','briefing','pdf','briefing','completed','scope','revision','unused','now','now')").run();
    await env.DB.prepare("INSERT INTO report_job_artifacts(job_id,format,object_key,filename,bytes,sha256,content_type,created_at) VALUES('download-test','pdf','report-jobs/download-test/report.pdf','report.pdf',?,?,'application/pdf','now')").bind(bytes.byteLength, digest).run();
    const path = "/api/v3/report-jobs/download-test/artifacts/pdf";
    expect((await worker.fetch(request(path, "GET", undefined, false), env)).status).toBe(401);
    const full = await worker.fetch(request(path), env);
    expect(full.status).toBe(200); expect(await full.text()).toBe("actual immutable report bytes");
    const input = request(path); input.headers.set("range", "bytes=7-15");
    const partial = await worker.fetch(input, env);
    expect(partial.status).toBe(206); expect(await partial.text()).toBe("immutable");
    expect(partial.headers.get("content-range")).toBe(`bytes 7-15/${bytes.byteLength}`);
    const conditional = request(path); conditional.headers.set("if-none-match", `"${digest}"`);
    expect((await worker.fetch(conditional, env)).status).toBe(304);
    const head = await worker.fetch(request(path, "HEAD"), env);
    expect(head.headers.get("content-length")).toBe(String(bytes.byteLength)); expect(await head.text()).toBe("");
    const invalid = request(path); invalid.headers.set("range", "bytes=9999999-");
    expect((await worker.fetch(invalid, env)).status).toBe(416);
    const suffix = request(path); suffix.headers.set("range", "bytes=-5");
    expect(await (await worker.fetch(suffix, env)).text()).toBe("bytes");
    await env.DB.prepare("UPDATE report_jobs SET status='failed' WHERE id='download-test'").run();
    expect((await worker.fetch(request(path), env)).status).toBe(409);
    await env.DB.prepare("DELETE FROM report_job_artifacts WHERE job_id='download-test'").run();
    expect((await worker.fetch(request(path), env)).status).toBe(409);
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

describe("recorded GPT Responses profiles", () => {
  const path = "/internal/inference/v1/chat/completions";
  const gpt = "@cf/openai/gpt-oss-120b";
  const schema = { type: "object", properties: { decisions: { type: "array" } }, required: ["decisions"] };
  const messages = [{ role: "system", content: "Use the declared source schema." }, { role: "user", content: "An exact source passage." }];
  function input(extra: Record<string, unknown> = {}): Request {
    return request(path, "POST", { model: gpt, messages, response_format: { type: "json_schema", json_schema: { name: "criteria", schema, strict: true } }, max_tokens: 400, ...extra });
  }
  it.each(["low", "medium", "high"])("forwards only an explicitly recorded %s effort with Responses fields", async effort => {
    const run = vi.fn(async () => ({ id: "recorded-response", status: "completed", output_text: "DO_NOT_USE_AGGREGATED_TEXT", output: [
      { type: "reasoning", content: [{ type: "reasoning_text", text: "PRIVATE_REASONING_MUST_NOT_LEAK" }], summary: [{ text: "PRIVATE_REASONING_SUMMARY" }] },
      { type: "message", role: "assistant", status: "completed", content: [{ type: "output_text", text: '{"decisions":' }, { type: "output_text", text: '[]}' }] },
    ], usage: { input_tokens: 89, output_tokens: 21, total_tokens: 110 }, reasoning: { summary: "PRIVATE_REASONING_CONFIG" } }));
    const testEnv = { ...env, AI: { run } } as unknown as Env;
    const response = await worker.fetch(input({ reasoning_effort: effort, max_completion_tokens: 2048 }), testEnv);
    expect(response.status).toBe(200);
    expect(run).toHaveBeenCalledWith(gpt, { input: messages, temperature: 0, max_output_tokens: 2048, reasoning: { effort }, stream: false, store: false, text: { format: { type: "json_schema", name: "criteria", schema, strict: true } } });
    const raw = await response.text();
    expect(raw).not.toContain("PRIVATE_REASONING");
    expect(raw).not.toContain("AGGREGATED_TEXT");
    expect(JSON.parse(raw)).toEqual({ id: "recorded-response", object: "chat.completion", model: gpt, choices: [{ index: 0, message: { role: "assistant", content: '{"decisions":[]}' }, finish_reason: "stop" }], usage: { prompt_tokens: 89, completion_tokens: 21, total_tokens: 110 } });
  });
  it.each(["none", "LOW", "", null, false, 3])("rejects invalid explicit effort %j without calling AI", async effort => {
    const run = vi.fn();
    const response = await worker.fetch(input({ reasoning_effort: effort }), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(400);
    expect(run).not.toHaveBeenCalled();
  });
  it("keeps unrecorded GPT calls on the messages/default-effort path and strips chat reasoning", async () => {
    const run = vi.fn(async () => ({ choices: [{ message: { role: "assistant", content: '{"decisions":[]}', reasoning_content: "PRIVATE_CHAT_REASONING" }, finish_reason: "stop" }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
    const response = await worker.fetch(input(), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(200);
    expect(run).toHaveBeenCalledWith(gpt, { messages, temperature: 0, max_completion_tokens: 400, stream: false, store: false, response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } } });
    expect(await response.text()).not.toContain("PRIVATE_CHAT_REASONING");
  });
  it("preserves Gemma's exact request payload and chat normalization", async () => {
    const result = { choices: [{ message: { role: "assistant", content: '{"decisions":[]}' }, finish_reason: "stop" }], usage: { prompt_tokens: 4, completion_tokens: 2 } };
    const run = vi.fn(async () => result);
    const model = "@cf/google/gemma-4-26b-a4b-it";
    const response = await worker.fetch(input({ model, reasoning_effort: "high" }), { ...env, AI: { run } } as unknown as Env);
    expect(run).toHaveBeenCalledWith(model, { messages, temperature: 0, max_completion_tokens: 400, store: false, stream: false, chat_template_kwargs: { enable_thinking: false }, response_format: { type: "json_schema", json_schema: { name: "doxa_findings", schema, strict: true } } });
    expect(await response.json()).toEqual({ ...result, model });
  });
  it.each([true, false])("marks exhausted Responses output as length, including reasoning-only output %s", async reasoningOnly => {
    const run = vi.fn(async () => ({ status: "incomplete", incomplete_details: { reason: "max_output_tokens" }, output: [
      { type: "reasoning", content: [{ type: "reasoning_text", text: "PRIVATE_EXHAUSTED_REASONING" }] },
      ...(reasoningOnly ? [] : [{ type: "message", role: "assistant", status: "incomplete", content: [{ type: "output_text", text: '{"decisions":[' }] }]),
    ], usage: { input_tokens: 100, output_tokens: 2048 } }));
    const response = await worker.fetch(input({ reasoning_effort: "low", max_tokens: 2048 }), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(200);
    const raw = await response.text();
    expect(raw).not.toContain("PRIVATE_EXHAUSTED_REASONING");
    expect(JSON.parse(raw)).toMatchObject({ choices: [{ message: { content: reasoningOnly ? "" : '{"decisions":[' }, finish_reason: "length" }], usage: { prompt_tokens: 100, completion_tokens: 2048, total_tokens: 2148 } });
  });
  it("normalizes a refusal without treating it as a completed evidence result", async () => {
    const run = vi.fn(async () => ({ status: "completed", output: [{ type: "message", role: "assistant", content: [{ type: "refusal", refusal: "I cannot provide that response." }] }], usage: { input_tokens: 20, output_tokens: 8 } }));
    const response = await worker.fetch(input({ reasoning_effort: "low" }), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ choices: [{ message: { role: "assistant", content: null, refusal: "I cannot provide that response." }, finish_reason: "content_filter" }], usage: { prompt_tokens: 20, completion_tokens: 8 } });
  });
  it.each(["max_output_tokens", "content_filter"])("normalizes legacy GPT finish %s without dropping token usage", async reason => {
    const run = vi.fn(async () => ({ choices: [{ message: { role: "assistant", content: null }, finish_reason: reason }], usage: { prompt_tokens: 15, completion_tokens: 40 } }));
    const response = await worker.fetch(input(), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ choices: [{ message: { content: null }, finish_reason: reason === "max_output_tokens" ? "length" : "content_filter" }], usage: { prompt_tokens: 15, completion_tokens: 40 } });
  });
  it("rejects a completed reasoning-only result without leaking its content", async () => {
    const run = vi.fn(async () => ({ status: "completed", output: [{ type: "reasoning", summary: [{ text: "PRIVATE_WITHOUT_FINAL_MESSAGE" }] }], usage: { input_tokens: 10, output_tokens: 100 } }));
    const response = await worker.fetch(input({ reasoning_effort: "low" }), { ...env, AI: { run } } as unknown as Env);
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("PRIVATE_WITHOUT_FINAL_MESSAGE");
  });
});
