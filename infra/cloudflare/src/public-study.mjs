// Only the published edition is served here. The research API runs separately.
const base =
  "https://raw.githubusercontent.com/sargonxg/DOXA-by-TACITUS/v0.8.0/frontend/public/studies/";
const files = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/reader.js", ["reader.js", "text/javascript; charset=utf-8"]],
  ["/report.pdf", ["report.pdf", "application/pdf"]],
  [
    "/evidence.xlsx",
    [
      "evidence.xlsx",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ],
  ],
  ["/sources.zip", ["sources.zip", "application/zip"]],
  ["/evidence.jsonld", ["evidence.jsonld", "application/ld+json"]],
]);
const caseFiles = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/antisemitism-case.pdf", ["antisemitism-case.pdf", "application/pdf"]],
  ["/evidence.jsonld", ["evidence.jsonld", "application/ld+json"]],
  ["/decisions.csv", ["decisions.csv", "text/csv; charset=utf-8"]],
  ...["evidence-desk.png", "source.png", "voice.png", "criterion.png"].map(
    (name) => ["/" + name, [name, "image/png"]],
  ),
]);

function unavailable() {
  return new Response("Published study temporarily unavailable", {
    status: 503,
    headers: { "Cache-Control": "no-store" },
  });
}

export default {
  async fetch(request) {
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response(null, {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }
    const path = new URL(request.url).pathname;
    if (path === "/antisemitism") {
      return new Response(null, { status: 308, headers: { Location: "/antisemitism/" } });
    }
    const isCase = path.startsWith("/antisemitism/");
    const entry = isCase ? caseFiles.get(path.slice("/antisemitism".length)) : files.get(path);
    if (!entry) return new Response("File not found", { status: 404 });
    const [file, type] = entry;
    const edition = isCase ? "antisemitism/" : "muslim-coverage/";
    let upstream;
    try {
      upstream = await fetch(base + edition + file, {
        method: request.method,
        cf: {
          cacheEverything: true,
          cacheTtlByStatus: { "200-299": 86400, "400-599": 0 },
        },
      });
    } catch {
      return unavailable();
    }
    if (!upstream.ok) return unavailable();
    const headers = new Headers({
      "Content-Type": type,
      "Cache-Control": "public, max-age=300",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    });
    if (
      file.endsWith(".pdf") ||
      file.endsWith(".xlsx") ||
      file.endsWith(".zip") ||
      file.endsWith(".jsonld") ||
      file.endsWith(".csv")
    )
      headers.set("Content-Disposition", 'attachment; filename="' + file + '"');
    return new Response(request.method === "HEAD" ? null : upstream.body, { status: 200, headers });
  },
};
