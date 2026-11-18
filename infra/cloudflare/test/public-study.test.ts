import { afterEach, expect, it, vi } from "vitest";
import study from "../src/public-study.mjs";

afterEach(() => vi.unstubAllGlobals());

it("serves only published study files and keeps caller inputs out of the origin request", async () => {
  const origin = vi.fn().mockResolvedValue(new Response("public PDF"));
  vi.stubGlobal("fetch", origin);
  const result = await study.fetch(new Request("https://study.example/report.pdf?filter=fixture"));
  expect(result.status).toBe(200);
  expect(result.headers.get("content-type")).toBe("application/pdf");
  expect(new TextDecoder().decode(await result.arrayBuffer())).toBe("public PDF");
  expect(origin.mock.calls[0][0]).toBe("https://raw.githubusercontent.com/sargonxg/DOXA-by-TACITUS/v0.6.1/frontend/public/studies/muslim-coverage/report.pdf");
  expect((await study.fetch(new Request("https://study.example/missing"))).status).toBe(404);
  expect((await study.fetch(new Request("https://study.example/", {method:"POST"}))).status).toBe(405);
  expect(origin).toHaveBeenCalledTimes(1);
});

it("reports an unavailable release file without serving it as a successful study", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null,{status:404})));
  const response = await study.fetch(new Request("https://study.example/"));
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
});
