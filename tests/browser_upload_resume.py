"""Verify browser refresh recovery after an interrupted multipart upload.

Uses a disposable DOXA data directory at DOXA_BROWSER_URL (default port8003).
Creates an 11MiB valid PDF, interrupts its second part, refreshes, selects the
same file, and verifies that only the remaining part is uploaded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import fitz
from playwright.sync_api import sync_playwright


def main() -> None:
    base = os.environ.get("DOXA_BROWSER_URL", "http://127.0.0.1:8003")
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser"))
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "upload-resume-smoke.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "A source document for verifying interrupted browser uploads.")
    fixture.write_bytes(pdf.tobytes() + b"\n" + b" " * (11 * 1024 * 1024))
    pdf.close()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.goto(base, wait_until="domcontentloaded")
        page.get_by_role("heading", name="Corpus overview", exact=True).wait_for()
        page.wait_for_timeout(500)
        project_id = page.request.get(f"{base}/api/projects").json()[0]["id"]
        original = {doc["id"] for doc in page.request.get(f"{base}/api/projects/{project_id}/documents").json()}
        page.route("**/api/uploads/*/parts/2", lambda route: route.abort("failed"))
        page.locator('input[type="file"][multiple]').set_input_files(str(fixture))
        page.get_by_role("alert").filter(has_text="Select the same file").wait_for(timeout=30000)
        checkpoints = page.evaluate("Object.entries(localStorage).filter(([key]) => key.startsWith('doxa-upload:')).map(([,value])=>JSON.parse(value))")
        assert len(checkpoints) == 1 and len(checkpoints[0]["parts"]) == 1, checkpoints
        page.unroute("**/api/uploads/*/parts/2")
        uploaded_parts: list[str] = []
        page.on("request", lambda req: uploaded_parts.append(req.url.rsplit("/", 1)[-1]) if "/parts/" in req.url else None)
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("heading", name="Document library", exact=True).wait_for()
        page.wait_for_timeout(500)
        page.locator('input[type="file"][multiple]').set_input_files(str(fixture))
        page.get_by_role("status").filter(has_text="1 document imported").wait_for(timeout=120000)
        assert uploaded_parts == ["2"], uploaded_parts
        assert page.evaluate("Object.keys(localStorage).filter(key=>key.startsWith('doxa-upload:')).length") == 0
        created = [doc for doc in page.request.get(f"{base}/api/projects/{project_id}/documents").json() if doc["id"] not in original]
        assert len(created) == 1
        assert page.request.delete(f"{base}/api/documents/{created[0]['id']}").ok
        browser.close()
    print(json.dumps({"interrupted_after_parts": 1, "resumed_parts": uploaded_parts, "bytes": fixture.stat().st_size, "created_document": True, "cleanup": True}, indent=2))


if __name__ == "__main__":
    main()
