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
  expect(origin.mock.calls[0][0]).toBe("https://raw.githubusercontent.com/sargonxg/DOXA-by-TACITUS/v0.8.0/frontend/public/studies/muslim-coverage/report.pdf");
  expect((await study.fetch(new Request("https://study.example/missing"))).status).toBe(404);
  expect((await study.fetch(new Request("https://study.example/", {method:"POST"}))).status).toBe(405);
  expect(origin).toHaveBeenCalledTimes(1);
});

it("routes the case, its pixel art and ledger to the pinned antisemitism edition", async () => {
  const origin = vi.fn().mockImplementation(() => Promise.resolve(new Response(null)));
  vi.stubGlobal("fetch", origin);
  const redirect = await study.fetch(new Request("https://study.example/antisemitism"));
  expect(redirect.status).toBe(308);
  expect(redirect.headers.get("location")).toBe("/antisemitism/");
  for (const [path, type] of [["", "text/html; charset=utf-8"], ["evidence-desk.png", "image/png"], ["decisions.csv", "text/csv; charset=utf-8"]]) {
    const response = await study.fetch(new Request("https://study.example/antisemitism/" + path));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(type);
    expect(origin.mock.calls.at(-1)?.[0]).toBe(
      "https://raw.githubusercontent.com/sargonxg/DOXA-by-TACITUS/v0.8.0/frontend/public/studies/antisemitism/" + (path || "index.html"),
    );
  }
  expect((await study.fetch(new Request("https://study.example/antisemitism/reader.js"))).status).toBe(404);
  expect(origin).toHaveBeenCalledTimes(3);
});

it("serves a download type and no body for a HEAD request for the graph", async () => {
  const origin = vi.fn().mockResolvedValue(new Response(null));
  vi.stubGlobal("fetch", origin);
  const response = await study.fetch(new Request("https://study.example/evidence.jsonld", {method: "HEAD"}));
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toBe("application/ld+json");
  expect(response.headers.get("content-disposition")).toBe('attachment; filename="evidence.jsonld"');
  expect(response.headers.get("content-security-policy")).toContain("frame-ancestors 'none'");
  expect(await response.text()).toBe("");
  expect(origin.mock.calls[0][1].method).toBe("HEAD");
});

it("reports a network error as temporary unavailability", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  const response = await study.fetch(new Request("https://study.example/"));
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("reports an unavailable release file without serving it as a successful study", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null,{status:404})));
  const response = await study.fetch(new Request("https://study.example/"));
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
});
