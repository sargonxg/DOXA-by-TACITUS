"""Verify grouped imports and real batch deltas in a disposable DOXA project."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    base = os.environ.get("DOXA_BROWSER_URL", "http://127.0.0.1:8003")
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser"))
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080})
        project_response = context.request.post(f"{base}/api/projects", data={"name": "Imported batch browser verification"})
        assert project_response.ok, project_response.text()
        project_id = project_response.json()["id"]
        context.add_init_script(f"localStorage.setItem('doxa-project', {json.dumps(project_id)})")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        upload_requests: list[str] = []
        page.on("request", lambda req: upload_requests.append(req.url) if req.method == "POST" and req.url.endswith("/upload") else None)
        page.goto(base, wait_until="domcontentloaded")
        page.get_by_role("heading", name="Corpus overview", exact=True).wait_for()
        page.wait_for_timeout(250)
        first = {"stable.txt": b"This source remains unchanged between imports.", "changed.txt": b"First position: the policy should protect equal access.", "earlier-only.txt": b"This source is included only in the earlier import."}
        second = {"stable.txt": first["stable.txt"], "changed.txt": b"Updated position: the policy should protect equal access and freedom of expression.", "added.txt": b"This new source enters the later import."}
        for group in (first, second):
            page.locator('input[type="file"][multiple]').set_input_files([{"name": name, "mimeType": "text/plain", "buffer": body} for name, body in group.items()])
            page.get_by_role("status").filter(has_text="3 documents imported").wait_for(timeout=60000)
            page.get_by_role("button", name="Dismiss notification", exact=True).click()
        batches = context.request.get(f"{base}/api/projects/{project_id}/batches").json()
        documents = context.request.get(f"{base}/api/projects/{project_id}/documents").json()
        assert len(upload_requests) == 2, upload_requests
        assert len(batches) == 2 and all(batch["documents"] == 3 for batch in batches), batches
        assert len(documents) == 5, documents
        page.get_by_role("button", name="Compare perspectives", exact=True).click()
        page.get_by_role("tab", name="Imported batches", exact=True).click()
        page.locator(".batch-delta-stat").first.wait_for(timeout=30000)
        totals = page.locator(".batch-delta-stat > strong").all_text_contents()
        assert totals == ["1", "1", "1", "1"], totals
        page.locator(".batch-delta-stat").nth(1).click()
        page.locator(".changed-source-pair > .batch-source-card").first.click()
        page.locator(".source-text").filter(has_text="First position").wait_for()
        page.get_by_role("button", name="Compare perspectives", exact=True).click()
        page.get_by_role("tab", name="Imported batches", exact=True).click()
        page.locator(".batch-delta-stat").nth(3).click()
        assert page.locator(".batch-results").get_by_text("Not included in this import", exact=True).count() >= 1
        assert "does not imply deletion" in page.locator(".batch-membership-note").inner_text()
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(output / "batches-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "batches-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print(json.dumps({"grouped_upload_requests": len(upload_requests), "batches": 2, "memberships_per_batch": 3, "unique_documents": 5, "totals": dict(zip(["added", "changed", "unchanged", "not_in_later_batch"], totals)), "source_opened": True, "mobile_overflow": False, "page_errors": errors}, indent=2))


if __name__ == "__main__":
    main()
