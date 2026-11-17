"""Verify refresh/reselection recovery through the current Import drawer.

Starts a disposable local API; no model or cloud inference is requested.
Optional DOXA_BROWSER_URL targets an already-running disposable local server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pymupdf as fitz
from playwright.sync_api import sync_playwright
from browser_support import local_app


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-resume-v3"))
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "upload-resume-smoke.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "A synthetic source for verifying interrupted browser uploads.")
    fixture.write_bytes(pdf.tobytes() + b"\n" + b" " * (11 * 1024 * 1024))
    pdf.close()
    with local_app(allow_external=True) as base, sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        response = context.request.post(
            f"{base}/api/projects", data={"name": "Synthetic multipart recovery verification"}
        )
        assert response.ok, response.text()
        project = response.json()["id"]
        page = context.new_page()
        errors = []
        init_calls = []
        part_calls = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: init_calls.append(request.url) if request.url.endswith("/uploads/init") else None,
        )
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("0", exact=True).wait_for()
        page.get_by_role("button", name="Import", exact=True).click()
        page.route("**/api/uploads/*/parts/2", lambda route: route.abort("failed"))
        page.locator('dialog input[type="file"][multiple]').set_input_files(str(fixture))
        page.get_by_role("alert").filter(has_text="Select the same file").wait_for(timeout=30000)
        checkpoints = page.evaluate(
            "Object.entries(localStorage).filter(([key]) => key.startsWith('doxa-upload:')).map(([,value])=>JSON.parse(value))"
        )
        assert len(checkpoints) == 1 and len(checkpoints[0]["parts"]) == 1, checkpoints
        assert checkpoints[0]["import_id"], checkpoints
        page.screenshot(path=str(output / "interrupted-upload.png"), full_page=True)
        page.unroute("**/api/uploads/*/parts/2")
        page.on(
            "request",
            lambda request: (
                part_calls.append(request.url.rsplit("/", 1)[-1]) if "/parts/" in request.url else None
            ),
        )
        page.reload(wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("0", exact=True).wait_for()
        page.get_by_role("button", name="Import", exact=True).click()
        page.locator('dialog input[type="file"][multiple]').set_input_files(str(fixture))
        page.get_by_role("status").filter(has_text="1 selected files imported").wait_for(timeout=120000)
        assert part_calls == ["2"], part_calls
        assert len(init_calls) == 1, init_calls
        assert (
            page.evaluate("Object.keys(localStorage).filter(key=>key.startsWith('doxa-upload:')).length") == 0
        )
        result = context.request.post(f"{base}/api/v3/projects/{project}/query", data={}).json()
        assert result["documents"]["total"] == 1, result
        document = result["documents"]["items"][0]
        assert document["status"] == "ready"
        batches = context.request.get(f"{base}/api/projects/{project}/batches").json()
        assert len(batches) == 1 and batches[0]["documents"] == 1, batches
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.locator(".w-source-row").first.click()
        page.locator(".w-source-passage").get_by_text(
            "A synthetic source for verifying interrupted browser uploads.", exact=True
        ).wait_for()
        page.screenshot(path=str(output / "resumed-source.png"), full_page=True)
        assert not errors, errors
        browser.close()
    report = {
        "fixture_notice": "Synthetic 11MiB transport/recovery fixture; no inference requested.",
        "interrupted_after_parts": 1,
        "resumed_parts": part_calls,
        "upload_initializations": len(init_calls),
        "bytes": fixture.stat().st_size,
        "created_documents": 1,
        "logical_batches": 1,
        "source_text_verified": True,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
