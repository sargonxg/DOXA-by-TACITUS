import { authenticate, isService } from "./auth";
import { boundedJson, HttpError, secured } from "./http";
import { storageRoute } from "./storage";
import { uploadRoute } from "./uploads";
import { inferenceRoute } from "./inference";
import { consume, enqueue, recoverDispatch, type WorkMessage } from "./queue";
export { PythonContainer } from "./container";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const path = new URL(request.url).pathname;
    try {
      if (path === "/healthz") return secured(Response.json({ status: "ok", authentication: "required", storage: "D1 + R2" }));
      if (path.startsWith("/internal/")) {
        if (!await isService(request, env)) throw new HttpError(401, "Service authentication required");
        const inference = await inferenceRoute(request, env, path);
        if (inference) return secured(inference);
        if (path === "/internal/storage/enqueue" && request.method === "POST") {
          const body = await boundedJson<{ run_id: string }>(request, 4096);
          await enqueue(env, body.run_id);
          return secured(Response.json({ queued: true }));
        }
        const response = await storageRoute(request, env, path);
        return secured(response ?? Response.json({ error: "Internal route not found" }, { status: 404 }));
      }
      if (!await authenticate(request, env)) throw new HttpError(401, "Sign in through the configured Cloudflare Access application");
      // Cookie-based Access authentication must not permit cross-origin state changes.
      if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
        const origin = request.headers.get("origin");
        if (origin && origin !== new URL(request.url).origin) throw new HttpError(403, "Cross-origin requests are not allowed");
      }
      const upload = await uploadRoute(request, env, path);
      if (upload) return secured(upload);
      if (path.startsWith("/api/")) {
        const headers = new Headers(request.headers);
        headers.delete("cf-access-jwt-assertion");
        headers.delete("cookie");
        headers.delete("authorization");
        headers.delete("x-doxa-service-token");
        // Origin was checked against the public URL above. The native container
        // rewrites Host to "container"; forwarding the public Origin would make
        // the authenticated Python adapter reject every browser POST as CSRF.
        headers.delete("origin");
        headers.set("x-doxa-service-token", env.DOXA_SERVICE_TOKEN);
        const forwarded = new Request(request, { headers });
        return secured(await env.PYTHON.getByName("pool-0").fetch(forwarded));
      }
      return secured(await env.ASSETS.fetch(request));
    } catch (error) {
      if (error instanceof HttpError) return secured(Response.json({ error: error.message }, { status: error.status }));
      console.error(JSON.stringify({ event: "request_failed", path, name: error instanceof Error ? error.name : "Error" }));
      return secured(Response.json({ error: "Request failed; check the deployment configuration and server logs" }, { status: 500 }));
    }
  },
  async queue(batch: MessageBatch<WorkMessage>, env: Env): Promise<void> { await consume(batch, env); },
  async scheduled(_event: ScheduledController, env: Env): Promise<void> { await recoverDispatch(env); },
} satisfies ExportedHandler<Env, WorkMessage>;
