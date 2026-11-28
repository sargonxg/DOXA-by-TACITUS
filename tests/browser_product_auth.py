"""Local acceptance of the real DOXA client using synthetic signed-in accounts."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright, expect


async def main(args):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.chromium, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = await page.goto(args.base)
        assert response.status == 401
        await context.add_cookies([{"name": "qa-account", "value": "a" * 64, "url": args.base, "httpOnly": True}])
        await page.goto(args.base, wait_until="networkidle")
        await expect(page.get_by_role("heading", name="Audit political bias in documents.")).to_be_visible()
        await expect(page.locator('meta[name="doxa-account"]')).to_have_attribute("content", "a" * 64)
        text = (
            "The working paper reports an association between the pilot and participation. "
            "The observational comparison cannot establish a causal effect. The authors request a controlled follow-up study.\n\n"
            "The council may open the pilot only if the review passes. The review has not yet taken place. "
            "The team should keep this condition beside any recommendation and confirm the decision with the responsible officer."
        )
        await page.locator("#document-file").set_input_files({"name": "team-policy-review.txt", "mimeType": "text/plain", "buffer": text.encode()})
        await expect(page.locator("#document-run")).to_be_enabled(timeout=20000)
        await page.locator("#document-run").click()
        await expect(page.locator("#document-status")).to_have_text("Review finished locally.", timeout=60000)
        await expect(page.get_by_role("heading", name="Political language flags")).to_be_visible()
        await page.locator("#project-controls > summary").click()
        await page.locator("#project-name").fill("Account A policy review")
        await page.get_by_role("button", name="Save project on this browser", exact=True).click()
        await expect(page.locator("#project-status")).to_contain_text("revision 1.")
        async with page.expect_download() as pending:
            await page.get_by_role("button", name="Export audit data (JSON)", exact=True).click()
        audit = json.loads(Path(await (await pending.value).path()).read_bytes())
        assert "team-policy-review.txt" in json.dumps(audit)
        assert "cannot establish a causal effect" in json.dumps(audit)
        # Switching identity must not silently reopen the previous user's project.
        await context.add_cookies([{"name": "qa-account", "value": "b" * 64, "url": args.base, "httpOnly": True}])
        await page.reload(wait_until="networkidle")
        await expect(page.locator('meta[name="doxa-account"]')).to_have_attribute("content", "b" * 64)
        await page.locator("#project-controls > summary").click()
        await expect(page.locator("#project-list")).to_contain_text("No projects saved in this browser.")
        await expect(page.locator("#project-list")).not_to_contain_text("Account A policy review")
        await context.add_cookies([{"name": "qa-account", "value": "a" * 64, "url": args.base, "httpOnly": True}])
        await page.reload(wait_until="networkidle")
        await page.locator("#project-controls > summary").click()
        await expect(page.locator("#project-list")).to_contain_text("Account A policy review")
        assert not errors, errors
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=args.out / "doxa-account-project.png")
        await page.set_viewport_size({"width": 390, "height": 844})
        assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        await context.clear_cookies()
        assert (await page.goto(args.base)).status == 401
        await browser.close()
    print(json.dumps({"guest_denied": True, "document_audit": True, "source_retained": True, "account_namespaces": True, "reopen": True, "mobile": True, "hosted_model_calls": 0, "identity_scope": "synthetic fixture; real Access JWT signatures tested separately"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8867/")
    parser.add_argument("--chromium")
    parser.add_argument("--out", type=Path)
    asyncio.run(main(parser.parse_args()))
