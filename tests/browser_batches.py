"""Verify grouped imports and linked v3 source scopes in an isolated browser.

Synthetic source fixtures establish transport, version and membership behavior;
they make no model-quality claim and request no inference.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from browser_support import chromium_launch_options, local_app


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-batches-v3"))
    output.mkdir(parents=True, exist_ok=True)
    with local_app(allow_external=True) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        response = context.request.post(
            f"{base}/api/projects", data={"name": "Synthetic grouped import verification"}
        )
        assert response.ok, response.text()
        project = response.json()["id"]
        page = context.new_page()
        errors = []
        upload_calls = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                upload_calls.append(request.url)
                if request.method == "POST" and urlsplit(request.url).path.endswith("/upload")
                else None
            ),
        )
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("0", exact=True).wait_for()
        page.get_by_role("button", name="Import", exact=True).click()
        first = {
            "stable.txt": b"This source remains unchanged between imports.",
            "changed.txt": b"First position: the policy should protect equal access.",
            "earlier-only.txt": b"This source is included only in the earlier import.",
        }
        second = {
            "stable.txt": first["stable.txt"],
            "changed.txt": b"Updated position: the policy should protect equal access and freedom of expression.",
            "added.txt": b"This new source enters the later import.",
        }
        for group in (first, second):
            page.locator('dialog input[type="file"][multiple]').set_input_files(
                [{"name": name, "mimeType": "text/plain", "buffer": body} for name, body in group.items()]
            )
            page.get_by_role("status").filter(has_text="3 selected files imported").wait_for(timeout=60000)
            page.get_by_role("button", name="Dismiss notification", exact=True).click()
        batches = context.request.get(f"{base}/api/projects/{project}/batches").json()
        query = context.request.post(f"{base}/api/v3/projects/{project}/query", data={}).json()
        assert len(upload_calls) == 2, upload_calls
        assert len(batches) == 2 and all(batch["documents"] == 3 for batch in batches), batches
        assert query["documents"]["total"] == 5, query
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.locator(".w-metric").first.get_by_text("5", exact=True).wait_for()
        page.get_by_role("button", name="Filters", exact=True).click()
        page.get_by_label("Import batch", exact=True).select_option(batches[0]["id"])
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.locator(".w-metric").first.get_by_text("3", exact=True).wait_for()
        assert page.locator(".w-source-row").count() == 3
        page.screenshot(path=str(output / "batch-source-scope.png"), full_page=True)
        delta = context.request.get(
            f"{base}/api/projects/{project}/batches/compare",
            params={"before_batch": batches[1]["id"], "after_batch": batches[0]["id"]},
        ).json()
        assert delta["totals"] == {"added": 1, "changed": 1, "unchanged": 1, "not_in_later_batch": 1}, delta
        assert "does not imply deletion" in delta["note"]
        old_document = delta["changed"][0]["before"]["id"]
        restored = context.request.post(
            f"{base}/api/v3/projects/{project}/query", data={"document_ids": [old_document]}
        ).json()
        page.goto(
            f"{base}/?workspace={project}&query={restored['query_id']}&doc={old_document}",
            wait_until="domcontentloaded",
        )
        page.locator(".w-source-passage").get_by_text(
            "First position: the policy should protect equal access.", exact=True
        ).wait_for()
        page.screenshot(path=str(output / "earlier-version-source.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("dialog .w-source-meta h2").wait_for()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "earlier-version-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    report = {
        "fixture_notice": "Synthetic browser transport and source-version test; no inference requested.",
        "grouped_upload_requests": len(upload_calls),
        "logical_imports": len(batches),
        "memberships_per_import": [batch["documents"] for batch in batches],
        "unique_source_records": query["documents"]["total"],
        "delta": delta["totals"],
        "batch_scope_sources": 3,
        "earlier_source_version_opened": True,
        "mobile_overflow": False,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
