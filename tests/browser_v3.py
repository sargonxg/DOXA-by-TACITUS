"""Real-browser v3 contract checks against isolated synthetic persistence fixtures.

No inference-quality claim: CompactProvider is deliberately mocked. The scale
corpus checks bounded paging/rendering, not news accuracy or model throughput.
Run: .venv/bin/python tests/browser_v3.py
Requires Playwright and system Chromium. Output: /tmp/doxa-v3-browser-checks.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doxa.api import create_app  # noqa: E402
from doxa.store import utcnow  # noqa: E402
from doxa.workspace import Workspace  # noqa: E402
from test_v3_api import CompactProvider, finish, upload  # noqa: E402
from test_workspace import seed_many_documents  # noqa: E402
from browser_support import chromium_launch_options, local_app  # noqa: E402


def fixtures(directory: Path) -> tuple[str, str]:
    with TestClient(create_app(directory, start_workers=False, use_saved_showcase=False)) as client:
        project = client.post("/api/projects", json={"name": "Synthetic contextual UI fixture"}).json()["id"]
        for text, filename in [
            ("QUOTED. A witness states a hostile proposition. The author rejects it.", "quoted.txt"),
            ("BOUNDARY. Political disagreement alone does not establish prejudice.", "boundary.txt"),
            ("COUNTER. The author supports equal rights and rejects collective blame.", "counter.txt"),
        ]:
            upload(client, project, text, filename)
        with patch("doxa.providers.get_provider", return_value=CompactProvider()):
            run = client.post(
                f"/api/v3/projects/{project}/runs",
                json={
                    "provider": "openai",
                    "model": "compact-test",
                    "lens_ids": ["ihra", "jda"],
                    "allow_remote_text": True,
                    "budget_usd": 1,
                },
            )
            assert run.status_code == 201, run.text
            assert finish(client, run.json()["id"])["status"] == "completed"
        scale = client.post("/api/projects", json={"name": "Synthetic 5,000-source scale fixture"}).json()[
            "id"
        ]
        store = client.app.state.store
        store.execute(
            "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
            ["batch-a", scale, "Synthetic scale import", utcnow()],
        )
        seed_many_documents(store, 5000, scale)
        Workspace(store).touch(scale)
        return project, scale


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-v3-browser-checks"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-browser-v3-"))
    project, scale = fixtures(directory)
    result = {
        "fixture_notice": "Synthetic UI integration and scale fixtures. No model-quality or inference-throughput claims.",
        "checks": [],
        "requests": [],
    }
    with local_app(directory) as base:
        with sync_playwright() as p:
            browser = p.chromium.launch(**chromium_launch_options())
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: (
                    result["requests"].append(request.url.removeprefix(base))
                    if "/api/" in request.url
                    else None
                ),
            )
            for _ in range(100):
                try:
                    page.goto(f"{base}/?workspace={scale}", wait_until="domcontentloaded")
                    break
                except Exception:
                    time.sleep(0.1)
            page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
            assert page.locator(".w-source-row").count() < 20
            sources = page.locator(".w-source-list")
            sources.get_by_role("button", name="Next result page").click()
            sources.get_by_text("5,000 matching · page 2", exact=True).wait_for()
            assert page.locator(".w-source-row").count() < 20
            page.get_by_role("button", name="Filters", exact=True).click()
            page.get_by_label("Author / institution", exact=True).select_option("Author 1")
            page.get_by_role("button", name="Close panel", exact=True).click()
            page.locator(".w-metric").first.get_by_text("295", exact=True).wait_for()
            result["checks"].append(
                "5,000 synthetic sources: cursor paging, virtual rows, linked author scope with exact total295"
            )
            page.screenshot(path=str(output / "scale-workspace.png"), full_page=True)

            page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
            page.locator(".w-observation-row").first.wait_for()
            assert page.locator(".w-timeline g[role=button]").first.get_attribute("aria-disabled") == "true"
            page.locator(".w-timeline g[role=button]").first.dispatch_event("click")
            assert page.get_by_label("Period start date", exact=True).input_value() == ""
            page.get_by_role("button", name="Filters", exact=True).click()
            page.get_by_label("Date basis", exact=True).select_option("ingestion")
            page.get_by_label("Timeline bins", exact=True).select_option("week")
            page.get_by_role("button", name="Close panel", exact=True).click()
            page.wait_for_timeout(500)
            page.locator(".w-timeline g[role=button]").first.click()
            assert page.get_by_label("Period start date", exact=True).input_value()
            assert page.get_by_label("Period end date", exact=True).input_value()
            result["checks"].append(
                "Undated bucket is safe; weekly import-time bucket applies a valid date range"
            )
            assert page.locator(".w-metric").nth(1).locator("strong").inner_text() == "1"
            assert page.locator(".w-metric").nth(2).locator("strong").inner_text() == "1 / 0"
            page.locator(".w-observation-row").first.click()
            page.locator(".w-source-passage mark").wait_for()
            page.get_by_role("tab", name="Review & correct", exact=True).click()
            page.get_by_label("Target", exact=True).fill("UI-reviewed proposition")
            page.get_by_label("Review note", exact=True).fill(
                "Synthetic browser fixture: attribution and exact source inspected."
            )
            page.get_by_label("Review state", exact=True).select_option("revised")
            page.get_by_role("button", name="Save review & corrections", exact=True).click()
            page.get_by_role("status").filter(has_text="review saved").wait_for()
            page.get_by_role("button", name="Propose as a reviewed example", exact=True).click()
            page.get_by_role("button", name="Approve this reviewed example", exact=True).click()
            page.get_by_text("1 reviews · 1 approved examples", exact=True).wait_for()
            page.get_by_role("button", name="Create immutable snapshot", exact=True).click()
            page.get_by_role("button", name="Create method candidate", exact=True).click()
            assert page.get_by_role("button", name="Activate candidate", exact=True).is_disabled()
            result["checks"].append(
                "exact quote, persisted structured correction, human example approval, frozen snapshot, candidate activation gate"
            )
            page.get_by_role("button", name="Close panel", exact=True).click()
            page.get_by_role("button", name="Refresh query snapshot", exact=True).click()
            page.wait_for_timeout(500)
            page.get_by_role("tab", name="Interpretation", exact=True).click()
            page.get_by_text("UI-reviewed proposition", exact=True).wait_for()
            page.get_by_role("tab", name="Source passage", exact=True).click()
            page.locator(".w-source-passage mark").wait_for()
            page.screenshot(path=str(output / "contextual-workspace.png"), full_page=True)
            page.get_by_role("button", name="Export query report", exact=True).click()
            page.get_by_text("Direct scoped export · compatibility tools", exact=True).click()
            page.get_by_label("Export format", exact=True).select_option("xlsx")
            with page.expect_download(timeout=60000) as download:
                page.get_by_role("button", name="Download XLSX", exact=True).click()
            path = output / "scoped-report.xlsx"
            download.value.save_as(path)
            assert path.stat().st_size > 1000
            page.get_by_role("button", name="Close panel", exact=True).click()
            result["checks"].append(
                "actual XLSX download uses current query snapshot and reviewed source scope"
            )
            page.get_by_role("button", name="Analyze", exact=True).click()
            page.get_by_role("button", name="Start matching-source analysis", exact=True).click()
            page.get_by_role("alert").filter(has_text="Explicitly enable text transfer").wait_for()
            captured = []

            def capture(route):
                captured.append(route.request.post_data_json)
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps({"detail": "UI run contract inspected; no inference requested."}),
                )

            page.route("**/api/v3/projects/*/runs", capture)
            page.get_by_label("Allow selected source text to this cloud provider").check()
            page.get_by_role("button", name="Start matching-source analysis", exact=True).click()
            page.get_by_role("alert").filter(has_text="UI run contract inspected").wait_for()
            assert (
                len(captured) == 1
                and captured[0]["allow_remote_text"]
                and captured[0]["methodology_version"] == "3.0"
                and captured[0]["query_id"]
            )
            result["checks"].append(
                "cloud text-transfer consent enforced; version3 frozen query run payload verified without inference"
            )
            page.get_by_role("button", name="Close panel", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Close panel", exact=True).click()
            page.locator(".w-center").evaluate("(element) => element.scrollTop=0")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile-workspace.png"), full_page=True)
            page.get_by_role("button", name="Sources", exact=True).click()
            page.locator("dialog .w-source-row").first.click()
            page.locator("dialog .w-source-meta h2").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile-source.png"), full_page=True)
            result["checks"].append(
                "390px responsive sources drawer and source inspector, native dialog focus, no horizontal overflow"
            )
            assert not any(
                ("/documents?" in request or "/findings?" in request) and "/v3/queries/" not in request
                for request in result["requests"]
            )
            result["checks"].append("no unbounded legacy corpus/finding fetches")
            result["page_errors"] = errors
            assert not errors, errors
            browser.close()
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key != "requests"}, indent=2))


if __name__ == "__main__":
    main()
