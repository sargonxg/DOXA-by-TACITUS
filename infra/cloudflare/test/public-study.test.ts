import { expect, it } from "vitest";
import study from "../src/public-study.mjs";

it("retires study pages and artifacts in favor of the GitHub repository", async () => {
  const repository = "https://github.com/sargonxg/DOXA-by-TACITUS";
  for (const [path, target] of [
    ["/", repository + "#worked-studies"],
    ["/report.pdf", repository + "#worked-studies"],
    ["/antisemitism/", repository + "/blob/main/docs/antisemitism-case.md"],
    ["/antisemitism/evidence.jsonld", repository + "/blob/main/docs/antisemitism-case.md"],
  ]) {
    for (const method of ["GET", "HEAD"]) {
      const response = study.fetch(new Request("https://study.example" + path + "?private=query", { method }));
      expect(response.status).toBe(308);
      expect(response.headers.get("location")).toBe(target);
      expect(await response.text()).toBe("");
    }
  }
  expect(study.fetch(new Request("https://study.example/", { method: "POST" })).status).toBe(405);
});
