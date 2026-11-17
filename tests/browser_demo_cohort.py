"""News demo retains its returned cohort and surfaces archive notices.

The demo response is mocked over synthetic5001-source persistence records;
actual query APIs verify the5000-source batch and retained historical version.
No model, archive restoration, or paid inference is requested.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import sync_playwright
from browser_support import chromium_launch_options, ROOT, local_app

sys.path.insert(0, str(ROOT))
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_document, seed_many_documents  # noqa: E402


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-demo-cohort-v3"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-demo-cohort-"))
    project = "synthetic-demo-cohort-project"
    store = Store(directory)
    store.init()
    store.execute(
        "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
        [project, "Synthetic versioned news demo", utcnow()],
    )
    for batch, name in [
        ("batch-a", "Current5000-source news edition"),
        ("historical-batch", "Historical edition"),
    ]:
        store.execute(
            "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
            [batch, project, name, utcnow()],
        )
    seed_many_documents(store, 5000, project)
    store.execute(
        "INSERT INTO batch_members(batch_id,document_id) SELECT batch_id,id FROM documents WHERE project_id=?",
        [project],
    )
    old_id = "historical-version-retained"
    seed_document(
        store,
        old_id,
        project,
        batch="historical-batch",
        sha="older-version-content-hash",
        text="Earlier source edition retained for history.",
    )
    store.execute("UPDATE documents SET filename=? WHERE id=?", ["source-00000.txt", old_id])
    store.close()
    notice = (
        "Current news cohort loaded. Paused or unfinished result archive skipped; no inference requested."
    )
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        demo_requests = []
        analysis_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                analysis_requests.append(request.url)
                if request.method == "POST" and request.url.endswith("/runs")
                else None
            ),
        )

        def demo(route):
            demo_requests.append(route.request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "documents": 5000,
                        "batch": {"id": "batch-a", "name": "Current5000-source news edition"},
                        "run": None,
                        "saved_results": False,
                        "notice": notice,
                    }
                ),
            )

        page.route(f"**/api/v3/projects/{project}/demo", demo)
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,001", exact=True).wait_for()
        page.get_by_role("button", name="Import", exact=True).click()
        page.get_by_role("button", name="Licensed news collection", exact=False).click()
        page.get_by_role("status").filter(has_text=notice).wait_for()
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
        assert page.get_by_label("Analysis snapshot", exact=True).input_value() == "none"
        selected_query = page.url.split("query=", 1)[1].split("&", 1)[0]
        result = context.request.get(f"{base}/api/v3/queries/{selected_query}").json()
        assert result["scope"]["batch_ids"] == ["batch-a"] and result["documents"]["total"] == 5000
        assert result["scope"]["run_id"] == "none"
        page.reload(wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
        versions = context.request.post(
            f"{base}/api/v3/projects/{project}/query",
            data={"run_id": "none", "document_ids": [old_id, "source-00000"]},
        ).json()
        assert versions["documents"]["total"] == 2
        items = versions["documents"]["items"]
        assert len({item["filename"] for item in items}) == 1
        assert len({item["sha256"] for item in items}) == 2
        corpus = context.request.post(
            f"{base}/api/v3/projects/{project}/query", data={"run_id": "none"}
        ).json()
        assert corpus["documents"]["total"] == 5001
        page.get_by_role("button", name="Clear filters", exact=True).click()
        page.locator(".w-metric").first.get_by_text("5,001", exact=True).wait_for()
        assert demo_requests == [{"collection": "news"}]
        assert not analysis_requests and not errors
        page.screenshot(path=str(output / "synthetic-retained-demo-versions.png"), full_page=True)
        browser.close()
    report = {
        "fixture_notice": "Synthetic version/cohort regression; demo response mocked, query APIs real, no inference.",
        "all_retained_source_records": 5001,
        "current_demo_cohort": 5000,
        "historical_and_current_versions_preserved": True,
        "cohort_filter_restored": True,
        "archive_notice_displayed": notice,
        "analysis_requests": 0,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
