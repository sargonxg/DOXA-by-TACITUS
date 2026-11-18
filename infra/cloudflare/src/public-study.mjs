// Only the published edition is served here. The research API runs separately.
const base =
  "https://raw.githubusercontent.com/sargonxg/DOXA-by-TACITUS/v0.6.1/frontend/public/studies/muslim-coverage/";
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
]);

export default {
  async fetch(request) {
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response(null, {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }
    const entry = files.get(new URL(request.url).pathname);
    if (!entry) return new Response("File not found", { status: 404 });
    const [file, type] = entry;
    const upstream = await fetch(base + file, {
      method: request.method,
      cf: {
        cacheEverything: true,
        cacheTtlByStatus: { "200-299": 86400, "400-599": 0 },
      },
    });
    if (!upstream.ok) {
      return new Response("Published study temporarily unavailable", {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      });
    }
    const headers = new Headers({
      "Content-Type": type,
      "Cache-Control": "public, max-age=300",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    });
    if (
      file.endsWith(".pdf") ||
      file.endsWith(".xlsx") ||
      file.endsWith(".zip")
    )
      headers.set("Content-Disposition", 'attachment; filename="' + file + '"');
    return new Response(upstream.body, { status: 200, headers });
  },
};
