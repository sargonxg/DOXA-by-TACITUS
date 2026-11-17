"""A new import stays visible beside an existing 5,000-source run snapshot.

Synthetic source-membership fixtures verify corpus/run scope behavior only.
The attempted Analyze request is intercepted; no inference is requested.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright
from browser_support import chromium_launch_options, local_app
from browser_v3 import fixtures
from doxa.store import Store
from doxa.workspace import Workspace
from test_workspace import seed_run


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-import-scope-v3"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-import-scope-"))
    _, project = fixtures(directory)
    run_id = "synthetic-frozen-scale-run"
    store = Store(directory)
    store.init()
    sources = store.query("SELECT id,sha256 FROM documents WHERE project_id=? ORDER BY id", [project])
    assert len(sources) == 5000
    seed_run(store, run_id, project, sources=sources)
    statements = [
        {
            "sql": "INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES(?,?,?)",
            "params": [run_id, source["id"], source["sha256"]],
        }
        for source in sources
    ]
    for start in range(0, len(statements), 100):
        store.batch(statements[start : start + 100])
    Workspace(store).touch(project)
    store.close()
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        run_requests = []
        trajectory_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        trajectory_requests = []
        page.on(
            "request",
            lambda request: (
                trajectory_requests.append(request.url) if "/trajectories?" in request.url else None
            ),
        )
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
        page.get_by_label("Analysis snapshot", exact=True).select_option(run_id)
        page.get_by_role("button", name="Import", exact=True).click()
        page.locator("dialog input[type=file][multiple]").set_input_files(
            {
                "name": "fresh-source-after-run.txt",
                "mimeType": "text/plain",
                "buffer": b"A new synthetic source imported after the frozen five-thousand-source cohort.",
            }
        )
        page.get_by_role("status").filter(has_text="1 selected files imported").wait_for()
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.locator(".w-metric").first.get_by_text("5,001", exact=True).wait_for()
        assert page.get_by_label("Analysis snapshot", exact=True).input_value() == "none"
        assert page.locator(".w-metric").nth(1).locator("strong").inner_text() == "—"
        page.get_by_text("Corpus · not analyzed", exact=True).wait_for()
        page.locator(".w-source-row").filter(has_text="fresh-source-after-run").wait_for()
        corpus = context.request.post(
            f"{base}/api/v3/projects/{project}/query", data={"run_id": "none"}
        ).json()
        frozen = context.request.post(
            f"{base}/api/v3/projects/{project}/query", data={"run_id": run_id}
        ).json()
        assert corpus["documents"]["total"] == 5001 and corpus["scope"]["run_id"] == "none"
        assert frozen["documents"]["total"] == 5000
        page.reload(wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,001", exact=True).wait_for()
        assert page.get_by_label("Analysis snapshot", exact=True).input_value() == "none"
        page.get_by_role("tab", name="Trajectory", exact=True).click()
        page.get_by_text("Select a contextual run", exact=True).wait_for()
        page.get_by_role("tab", name="History", exact=True).click()
        page.get_by_text("Select an analysis snapshot", exact=True).wait_for()
        assert not trajectory_requests
        page.get_by_role("tab", name="Signals", exact=True).click()
        page.get_by_role("button", name="Analyze", exact=True).click()
        page.get_by_text("5,001 matching sources", exact=True).wait_for()

        def capture(route):
            run_requests.append(route.request.post_data_json)
            route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"detail": "Corpus selection checked; no inference requested."}),
            )

        page.route(f"**/api/v3/projects/{project}/runs", capture)
        page.get_by_label("Allow selected source text to this cloud provider", exact=False).check()
        page.get_by_role("button", name="Start matching-source analysis", exact=True).click()
        page.get_by_role("alert").filter(has_text="Corpus selection checked").wait_for()
        assert len(run_requests) == 1
        restored = context.request.get(f"{base}/api/v3/queries/{run_requests[0]['query_id']}").json()
        assert restored["scope"]["run_id"] == "none" and restored["documents"]["total"] == 5001
        assert not errors, errors
        page.screenshot(path=str(output / "synthetic-corpus-after-import.png"), full_page=True)
        browser.close()
    result = {
        "fixture_notice": "Synthetic corpus-membership regression; Analyze POST mocked, no inference requested.",
        "frozen_run_sources": 5000,
        "corpus_sources_after_import": 5001,
        "corpus_sentinel_persisted": True,
        "fresh_source_visible": True,
        "analyze_query_sources": 5001,
        "unassessed_metrics_unavailable": True,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
