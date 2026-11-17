"""Inspect an existing real-news run through an isolated, consistent browser copy.

Uses SQLite online backup plus copied source artifacts. Analysis workers are
explicitly disabled; no model calls are made and original state is untouched.
DOXA_BROWSER_PUBLISH_DOCS=1 copies the actual screenshots into docs/images.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright
from browser_support import ROOT, local_app


def main() -> None:
    source = Path(os.environ.get("DOXA_BROWSER_DATA", str(ROOT / "data/v3-news")))
    project = os.environ.get("DOXA_BROWSER_PROJECT", "13780ed307c84dbb937182940febddd0")
    run = os.environ.get("DOXA_BROWSER_RUN", "211bd5b28cf24a38b170497dc5373977")
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-real-corpus-v3"))
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_data": str(source),
        "project_id": project,
        "run_id": run,
        "snapshot_notice": "Online backup of an actual ongoing/recorded run. Coverage remains partial. No inference calls or source/review mutations were requested.",
        "checks": [],
    }
    with tempfile.TemporaryDirectory(prefix="doxa-real-browser-") as temporary:
        snapshot = Path(temporary)
        with (
            sqlite3.connect(f"file:{source / 'doxa.sqlite3'}?mode=ro", uri=True) as original,
            sqlite3.connect(snapshot / "doxa.sqlite3") as copy,
        ):
            original.backup(copy)
        shutil.copytree(source / "objects", snapshot / "objects")
        with local_app(snapshot) as base, sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
            )
            context = browser.new_context(viewport={"width": 1600, "height": 1000})
            response = context.request.post(f"{base}/api/v3/projects/{project}/query", data={"run_id": run})
            assert response.ok, response.text()
            query = response.json()
            assert query["documents"]["total"] == 5000 and len(query["documents"]["items"]) == 50
            assert query["metrics"]["analysis_kind"] == "contextual_v3"
            report["metrics"] = query["metrics"]
            report["run_status"] = query["run"]["status"]
            report["model"] = query["run"].get("model_profile", {})
            report["checks"].append(
                "Actual5000-source query,50-record first page and contextual method identity"
            )
            page = context.new_page()
            errors = []
            api_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: (
                    api_requests.append({"method": request.method, "path": request.url.removeprefix(base)})
                    if "/api/" in request.url
                    else None
                ),
            )
            page.goto(
                f"{base}/?{urlencode({'workspace': project, 'query': query['query_id']})}",
                wait_until="domcontentloaded",
            )
            page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for(timeout=60000)
            page.locator(".w-source-row").first.wait_for()
            assert page.locator(".w-source-row").count() < 20
            page.screenshot(path=str(output / "workspace-v3-news.png"), full_page=True)
            page.locator(".w-source-list").get_by_role("button", name="Next result page", exact=True).click()
            page.get_by_text("5,000 matching · page 2", exact=True).wait_for()
            report["checks"].append("Actual-news cursor page2 and bounded virtual source rows")
            if query["findings"]["items"]:
                page.locator(".w-observation-row").first.click()
                page.locator(".w-source-passage mark").wait_for()
                quote = page.locator(".w-source-passage mark").first.inner_text()
                assert quote and quote in query["findings"]["items"][0]["quote"]
                report["selected_observation"] = {
                    key: query["findings"]["items"][0].get(key)
                    for key in [
                        "id",
                        "document_id",
                        "title",
                        "lens_id",
                        "criterion_id",
                        "criterion_kind",
                        "result",
                        "attribution",
                        "speaker_stance",
                        "document_stance",
                    ]
                }
                report["selected_quote"] = quote
                page.screenshot(path=str(output / "workspace-v3-evidence.png"), full_page=True)
                page.get_by_role("tab", name="Interpretation", exact=True).click()
                page.locator(".w-facts").wait_for()
                page.screenshot(path=str(output / "workspace-v3-interpretation.png"), full_page=True)
                page.get_by_role("button", name="Close evidence inspector", exact=True).click()
                report["checks"].append(
                    "Actual retained model observation opens exact quote and separate attribution/stance interpretation"
                )
            page.get_by_label("Analytical framework", exact=True).select_option("anti-muslim-hostility")
            page.wait_for_timeout(600)
            page.get_by_role("tab", name="Trajectory", exact=True).click()
            page.locator(".w-trajectory .w-spinner").wait_for(state="detached", timeout=60000)
            assert not page.locator(".w-trajectory .w-error").count()
            page.screenshot(path=str(output / "workspace-v3-trajectory.png"), full_page=True)
            report["checks"].append(
                "Actual frozen-scope publication trajectory displays rates or explicit unavailable state without invented spikes"
            )
            page.get_by_role("tab", name="Signals", exact=True).click()
            page.get_by_label("Analytical framework", exact=True).select_option("language-norms")
            page.locator(".w-coverage-note").filter(has_text="Not assessed in this run").wait_for()
            assert page.locator(".w-metric").nth(1).locator("strong").inner_text() == "—"
            page.get_by_role("button", name="Analyze", exact=True).click()
            assert (
                page.locator(".w-lens-selection label")
                .filter(has_text="Language norms")
                .locator("input[type=checkbox]")
                .is_checked()
            )
            page.get_by_role("button", name="Close panel", exact=True).click()
            report["checks"].append(
                "Unassessed framework shows unavailable counts and preselects that framework for a new inquiry"
            )
            page.get_by_label("Analytical framework", exact=True).select_option("")
            page.wait_for_timeout(600)
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator(".w-center").evaluate("(element) => element.scrollTop=0")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "workspace-v3-mobile.png"), full_page=True)
            page.get_by_role("button", name="Sources", exact=True).click()
            page.locator("dialog .w-source-row").first.click()
            page.locator("dialog .w-source-meta h2").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            report["checks"].append(
                "Actual-news390px source drawer and inspector without horizontal overflow"
            )
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(base + "/guide.html", wait_until="domcontentloaded")
            page.get_by_role("link", name="Open Import", exact=False).click()
            page.get_by_role("heading", name="Bring your sources together", exact=True).wait_for()
            assert page.locator("dialog[open]").count() == 1
            report["checks"].append("Updated Quest guide opens the actual Import drawer")
            assert not [
                request
                for request in api_requests
                if request["method"] == "POST" and request["path"].endswith("/runs")
            ]
            assert not errors, errors
            report["page_errors"] = errors
            report["api_requests"] = api_requests
            browser.close()
    if os.environ.get("DOXA_BROWSER_PUBLISH_DOCS") == "1":
        for image in output.glob("workspace-v3-*.png"):
            shutil.copy2(image, ROOT / "docs/images" / image.name)
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"api_requests", "model"}}, indent=2
        )
    )


if __name__ == "__main__":
    main()
