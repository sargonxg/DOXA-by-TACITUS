import { DurableObject } from "cloudflare:workers";

/** Explicit server-only environment; browser and arbitrary bindings never pass through. */
export function containerEnvironment(bindings: Env): Record<string, string> {
  const result: Record<string, string> = {
    DOXA_DATA_DIR: "/data", DOXA_CLOUD_STATE_URL: bindings.DOXA_PUBLIC_ORIGIN,
    DOXA_SERVICE_TOKEN: bindings.DOXA_SERVICE_TOKEN, DOXA_INTERNAL_TOKEN: bindings.DOXA_SERVICE_TOKEN,
    DOXA_CLOUDFLARE_BASE_URL: `${bindings.DOXA_PUBLIC_ORIGIN.replace(/\/$/, "")}/internal/inference/v1`,
    DOXA_CLOUDFLARE_API_TOKEN: bindings.DOXA_SERVICE_TOKEN,
  };
  for (const key of ["DOXA_OPENAI_API_KEY", "DOXA_RUNPOD_API_KEY", "OPENAI_API_KEY", "RUNPOD_API_KEY", "DOXA_MIGRATION_BACKUP_RECEIPT", "DOXA_REVIEWER_ID"] as const) {
    if (bindings[key]) result[key] = bindings[key];
  }
  return result;
}

/** One bounded executor for a private shared workspace; D1 and R2 hold all durable data. */
export class PythonContainer extends DurableObject<Env> {
  private starting: Promise<void> | undefined;

  async fetch(request: Request): Promise<Response> {
    this.starting ??= this.start().finally(() => { this.starting = undefined; });
    await this.starting;
    const container = this.ctx.container;
    if (!container) throw new Error("Container binding is missing");
    await container.setInactivityTimeout(60000);
    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return container.getTcpPort(8000).fetch(forwarded);
  }

  private async start(): Promise<void> {
    const container = this.ctx.container;
    if (!container) throw new Error("Container binding is missing");
    if (!this.env.DOXA_SERVICE_TOKEN || this.env.DOXA_SERVICE_TOKEN.length < 32) throw new Error("Configure a strong DOXA_SERVICE_TOKEN secret");
    if (!container.running) {
      container.start({ image: container.images.base, instance: "standard-2", enableInternet: true, env: containerEnvironment(this.env) });
    }
    await container.setInactivityTimeout(60000);
    const port = container.getTcpPort(8000);
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const response = await port.fetch("http://container/api/health", { headers: { "x-doxa-service-token": this.env.DOXA_SERVICE_TOKEN }, signal: AbortSignal.timeout(1000) });
        await response.body?.cancel();
        if (response.ok) return;
      } catch { /* Only readiness probes are retried; application mutations never are. */ }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw new Error("Python container did not become ready");
  }
}
