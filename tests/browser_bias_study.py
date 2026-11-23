"""Read the shipped field study and drill from a real workspace query to its exact span."""

import hashlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from playwright.sync_api import Error, expect, sync_playwright
from browser_support import ROOT, chromium_launch_options, local_app

sys.path.insert(0, str(ROOT))
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_assessment, seed_document, seed_run  # noqa: E402


def main():
    output = Path(os.environ["DOXA_STUDY_SCREENSHOTS"]) if os.environ.get("DOXA_STUDY_SCREENSHOTS") else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="doxa-study-reader-") as tmp:
        data = Path(tmp)
        store = Store(data)
        store.init()
        store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Reader test',?)", [utcnow()])
        store.execute(
            "INSERT INTO batches(id,project_id,name,created_at) VALUES('batch-a','p','Reading test',?)",
            [utcnow()],
        )
        quote = "The witness described unequal treatment in the hearing."
        seed_document(store, "a", text=quote)
        seed_run(store, sources=[{"id": "a", "sha256": "a-hash"}])
        seed_assessment(store, "recorded-reading", "a", text=quote)
        store.close()
        with local_app(data) as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(**chromium_launch_options())
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors, transfers = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request", lambda req: transfers.append(req.url) if not req.url.startswith(base) else None
            )
            query = page.request.post(base + "/api/v3/projects/p/query", data={"run_id": "run"}).json()
            page.goto(base + "/?workspace=p&query=" + query["query_id"])
            scorecard = page.get_by_role("region", name="Analysis scorecard")
            scorecard.get_by_role("button", name="Open worked example").click()
            expect(scorecard.get_by_text("60.0 / 100")).to_be_visible()
            expect(scorecard.get_by_role("table").first.get_by_text("75.0% · 3/4")).to_be_visible()
            expect(scorecard.get_by_role("table").first.get_by_text("Unresolved range 0.0%–25.0%")).to_be_visible()
            expect(scorecard.get_by_role("link", name="Download the import-ready sources")).to_have_attribute(
                "href", "/studies/scorecard/civic-square-sources.zip")
            expect(scorecard.get_by_text("50.0 pp")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            scorecard.get_by_role("button", name="Close worked example").click()
            panel = page.get_by_role("region", name="Bias indicators in this selection")
            expect(panel).to_contain_text("1 / 1")
            panel.get_by_role("button", name="Read passage").click()
            expect(page.locator(".w-inspector-rail")).to_contain_text(quote)
            opener = page.get_by_role("button", name="Read the 60-article study")
            opener.click()
            dialog = page.get_by_role("dialog", name="60-article bias study")
            expect(dialog.get_by_role("heading", name="Reading anti-Muslim bias in the news")).to_be_visible()
            expect(dialog.locator(".w-study-lead")).to_contain_text("14 of 60 articles")
            if output:
                dialog.screenshot(path=str(output / "muslim-coverage-dashboard.png"))
            dialog.get_by_role("button", name="Reports or statements of unequal treatment").click()
            expect(dialog.get_by_role("status")).to_have_text("Showing 8 of 11 matching articles.")
            dialog.get_by_role("button", name="Show more articles").click()
            expect(dialog.get_by_role("status")).to_have_text("Showing 11 of 11 matching articles.")
            dialog.get_by_label("Find a title or credit").fill("Ramadan")
            expect(dialog.get_by_role("status")).to_have_text("Showing 1 of 1 matching articles.")
            dialog.locator(".w-study-register:visible summary").click()
            expect(dialog.locator(".w-study-register:visible")).to_contain_text("expulsion")
            dialog.get_by_label("Find a title or credit").fill("ZZZ-no-source")
            expect(dialog.get_by_role("status")).to_have_text("No articles match these filters.")
            dialog.get_by_role("button", name="Clear filters").click()
            expect(dialog.get_by_role("status")).to_have_text("Showing 8 of 60 matching articles.")
            dialog.get_by_label("Publication year").select_option("2024")
            expect(dialog.get_by_role("status")).to_have_text("Showing 8 of 10 matching articles.")
            for name in ("report.pdf", "evidence.xlsx", "sources.zip", "evidence.jsonld"):
                label = dialog.locator(f"a[download][href$='/{name}']")
                with page.expect_download() as capture:
                    label.click()
                path = capture.value.path()
                assert (
                    hashlib.sha256(path.read_bytes()).digest()
                    == hashlib.sha256(
                        (ROOT / "frontend/public/studies/muslim-coverage" / name).read_bytes()
                    ).digest()
                )
            dialog.get_by_role("button", name="Read the passages", exact=True).click()
            expect(dialog.get_by_role("heading", name="A flag to challenge")).to_be_visible()
            dialog.locator(".w-study-case").first.locator("summary").click()
            expect(dialog.locator(".w-study-case").first).to_contain_text("gv-721315-b00004")
            expect(dialog.locator(".w-study-case").first).to_contain_text("Frozen criterion")
            dialog.get_by_role("button", name="Method", exact=True).click()
            expect(dialog.get_by_text("248 eligible", exact=False)).to_be_visible()
            expect(dialog.get_by_role("table")).to_contain_text("All unresolved flagged")
            dialog.get_by_role("button", name="Back to workspace").click()
            expect(dialog).not_to_be_visible()
            expect(opener).to_be_focused()
            page.get_by_role("button", name="Close evidence inspector").click()
            page.set_viewport_size({"width": 390, "height": 844})
            opener.click()
            expect(dialog.get_by_role("heading", name="Reading anti-Muslim bias in the news")).to_be_visible()
            assert dialog.locator(".w-study-body").evaluate("el => el.scrollWidth <= el.clientWidth")
            if output:
                dialog.screenshot(path=str(output / "muslim-coverage-mobile.png"))
            page.set_viewport_size({"width": 320, "height": 844})
            dialog.get_by_role("button", name="All articles", exact=True).click()
            expect(dialog.get_by_label("Publication year")).to_be_visible()
            assert dialog.locator(".w-study-body").evaluate("el => el.scrollWidth <= el.clientWidth")
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            case_url = base + "/studies/antisemitism/index.html"
            page.get_by_role("link", name="Read the antisemitism case").click()
            page.wait_for_url(case_url)
            expect(page.get_by_role("heading", name="Reading antisemitism across three frameworks")).to_be_visible()
            assert page.locator("img").evaluate_all("(images) => images.every(img => img.complete && img.naturalWidth)")
            panel = page.locator("#plzen-poster")
            panel.get_by_text("Reveal the passage and archived result").click()
            expect(panel.get_by_text("Frozen criterion.", exact=True)).to_be_visible()
            expect(panel.locator("blockquote")).to_contain_text("God cursed them")
            assert page.locator("#three-frameworks tbody tr").count() == 9
            assert page.locator("#red-flags article").count() == 9
            assert page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
            standalone = browser.new_page(viewport={"width": 320, "height": 844})
            standalone.goto(base + "/studies/muslim-coverage/index.html")
            standalone.get_by_role("link", name="Follow the evidence").click()
            assert standalone.url.endswith("#case-school-hijab")
            expect(standalone.locator("#case-school-hijab")).to_contain_text("One school decision")
            standalone.get_by_role("button", name="Exclusion linked to Muslim identity").click()
            expect(standalone.get_by_role("status")).to_have_text("Showing 8 of 8 matching articles.")
            standalone.get_by_label("Article reading").select_option("uncertain")
            expect(standalone.get_by_role("status")).to_have_text("Showing 1 of 1 matching articles.")
            assert standalone.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
            # Some managed Chromium installations disallow all file:// navigation.
            # The CI browser must exercise the unzipped edition directly.
            try:
                standalone.goto((ROOT / "frontend/public/studies/muslim-coverage/index.html").as_uri())
            except Error as error:
                if "ERR_BLOCKED_BY_ADMINISTRATOR" not in str(error) or os.getenv("DOXA_REQUIRE_FILE_STUDY"):
                    raise
                print("Local browser policy blocks file URLs. Standalone interactive checks passed over loopback.")
            else:
                standalone.get_by_role("button", name="Exclusion linked to Muslim identity").click()
                expect(standalone.get_by_role("status")).to_have_text("Showing 8 of 8 matching articles.")
                print("Direct file URL study filters passed.")
            standalone.close()
            assert not errors and not transfers, (errors, transfers)
            browser.close()
    print(
        "Indicator drill-down, both research readers, downloads, keyboard return and mobile viewport passed offline."
    )


if __name__ == "__main__":
    main()
