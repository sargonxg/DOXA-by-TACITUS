// Retired public study host. The published materials now live in the repository.
const repository = "https://github.com/sargonxg/DOXA-by-TACITUS";

export default {
  fetch(request) {
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const path = new URL(request.url).pathname;
    const target = path === "/antisemitism" || path.startsWith("/antisemitism/")
      ? repository + "/blob/main/docs/antisemitism-case.md"
      : repository + "#worked-studies";
    return new Response(null, { status: 308, headers: { Location: target, "X-Robots-Tag": "noindex" } });
  },
};
