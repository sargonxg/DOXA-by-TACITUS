"""Real-browser smoke checks; run against an isolated DOXA workspace.

DOXA_BROWSER_URL=http://127.0.0.1:8003 python tests/browser_smoke.py
Requires playwright and a system Chromium browser. This imports the real demo
collection and changes review state, so use a disposable data directory.
"""
from __future__ import annotations

import json
import os
import time
import csv
import io
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    base = os.environ.get("DOXA_BROWSER_URL", "http://127.0.0.1:8003")
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser"))
    output.mkdir(parents=True, exist_ok=True)
    results: dict = {"base_url": base, "checks": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, accept_downloads=True)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="domcontentloaded")
        page.get_by_role("heading", name="Corpus overview", exact=True).wait_for()
        page.wait_for_timeout(500)
        if page.get_by_role("button", name="Load research collection").count():
            page.get_by_role("button", name="Load research collection").click()
            page.get_by_role("status").filter(has_text="collection loaded").wait_for(timeout=120000)
        projects = page.request.get(f"{base}/api/projects").json()
        project_id = projects[0]["id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            runs = page.request.get(f"{base}/api/projects/{project_id}/runs").json()
            if runs and runs[0]["status"] in {"completed", "partial"}:
                break
            page.wait_for_timeout(1000)
        else:
            raise AssertionError("Real corpus run did not finish before screenshot capture")
        latest_run = runs[0]
        metrics = page.request.get(f"{base}/api/projects/{project_id}/dashboard").json()
        results["analysis"] = {
            "provider": latest_run.get("config", {}).get("provider"),
            "model": latest_run.get("model_profile", {}).get("model") or latest_run.get("config", {}).get("model"),
            "run_status": latest_run["status"],
            "saved_snapshot": bool(latest_run.get("config", {}).get("saved_snapshot")),
            "processing_coverage": metrics.get("processing_coverage"),
            "semantic_coverage": metrics.get("semantic_coverage"),
            "coverage_detail": metrics.get("coverage_detail"),
            "findings": metrics.get("findings"),
        }
        page.reload(wait_until="domcontentloaded")
        page.locator(".stat-card").first.locator(".stat-value").filter(has_text="20").wait_for()
        if latest_run["status"] == "partial":
            page.locator(".incomplete-coverage-notice").filter(has_text="Partial analysis coverage").wait_for()
        if results["analysis"]["saved_snapshot"]:
            page.locator(".saved-snapshot-notice").filter(has_text="Previously computed result snapshot").wait_for()
        page.wait_for_timeout(500)
        if page.get_by_role("button", name="Dismiss notification", exact=True).count():
            page.get_by_role("button", name="Dismiss notification", exact=True).click()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(output / "overview-desktop.png"), full_page=True)
        results["checks"].append("real corpus import and overview")

        page.get_by_role("button", name="Document library", exact=True).click()
        page.locator(".documents-table tbody tr").first.wait_for()
        assert page.locator(".documents-table tbody tr").count() == 20
        page.get_by_label("Search documents").fill("Prevent")
        assert 1 <= page.locator(".documents-table tbody tr").count() < 20
        page.get_by_label("Search documents").fill("")
        results["checks"].append("library search over 20 real publications")

        source = page.request.get(f"{base}/api/projects/{project_id}/documents").json()[0]
        metadata = io.StringIO()
        writer = csv.writer(metadata)
        writer.writerow(["id", "title", "author", "institution", "document_type", "published_at"])
        writer.writerow([source.get(key, "") for key in ["id", "title", "author", "institution", "document_type", "published_at"]])
        page.locator('input[accept=".csv,text/csv"]').set_input_files({"name": "metadata.csv", "mimeType": "text/csv", "buffer": metadata.getvalue().encode()})
        page.get_by_role("status").filter(has_text="1 document records updated").wait_for()
        results["checks"].append("actual metadata CSV import")

        page.get_by_role("button", name="Evidence workbench", exact=True).click()
        page.locator(".findings-list .finding-card").first.wait_for(timeout=30000)
        assert page.locator(".findings-list .finding-card").count() <= 30
        page.locator(".findings-list .finding-card").first.click()
        page.locator(".source-detail .source-title h2").wait_for()
        page.locator(".source-block mark").first.wait_for()
        page.get_by_label("Review note").fill("Browser smoke check: source passage inspected.")
        page.get_by_role("button", name="Confirm", exact=True).click()
        page.get_by_role("status").filter(has_text="Finding confirmed").wait_for()
        page.get_by_label("Review note").fill("")
        page.get_by_role("button", name="Return to unreviewed", exact=True).click()
        page.get_by_role("status").filter(has_text="Finding returned to review").wait_for()
        page.get_by_role("button", name="Dismiss notification", exact=True).click()
        pane = page.locator(".source-detail").bounding_box()
        confirm = page.get_by_role("button", name="Confirm", exact=True).bounding_box()
        assert pane and confirm and confirm["y"] + confirm["height"] <= pane["y"] + pane["height"] + 1
        assert page.locator(".source-detail").evaluate("element => element.scrollTop") == 0
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(output / "evidence-desktop.png"), full_page=True)
        results["checks"].append("paginated evidence, exact source highlight, persisted review")

        page.get_by_role("button", name="Compare perspectives", exact=True).click()
        page.get_by_role("tab", name="Authors & institutions").click()
        page.locator(".matrix-table tbody tr").first.wait_for()
        assert page.get_by_role("columnheader", name="ANALYSIS COVERAGE", exact=True).count() or page.get_by_role("columnheader", name="SCAN COVERAGE", exact=True).count()
        results["checks"].append("author and lens comparison")

        page.get_by_role("button", name="Analysis runs", exact=True).click()
        page.get_by_label("Inference provider", exact=True).select_option("lexical")
        page.get_by_role("button", name="Check provider", exact=True).click()
        page.locator(".health-result.healthy").wait_for(timeout=30000)
        results["checks"].append("provider health check")

        page.get_by_role("button", name="Reports & exports", exact=True).click()
        page.get_by_role("button", name="Excel workbook").click()
        with page.expect_download(timeout=120000) as event:
            page.get_by_role("button", name="Download XLSX", exact=True).click()
        download = event.value
        download.save_as(str(output / "browser-report.xlsx"))
        assert (output / "browser-report.xlsx").stat().st_size > 1000
        results["checks"].append("actual XLSX download")
        if page.get_by_role("button", name="Dismiss notification", exact=True).count():
            page.get_by_role("button", name="Dismiss notification", exact=True).click()

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Open navigation", exact=True).click()
        page.get_by_role("button", name="Overview", exact=True).click()
        page.get_by_role("heading", name="Corpus overview", exact=True).wait_for()
        page.wait_for_timeout(350)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "overview-mobile.png"), full_page=True)
        results["checks"].append("390px navigation, layout, no horizontal document overflow")
        results["page_errors"] = errors
        assert not errors, errors
        browser.close()
    (output / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
