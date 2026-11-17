"""CPU rubric experiments use actual source passages without prior assessments.

Synthetic5,000-source persistence records exercise real workspace/context APIs.
The explicit scoring endpoint is mocked; no model or inference is requested.
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
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-cpu-source-v3"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-cpu-source-"))
    project = "synthetic-unassessed-cpu-corpus"
    document = "cpu-source-no-finding"
    title = "CPU source without an assessment"
    blocks = [
        {
            "id": "actual-source-paragraph-one",
            "text": "The reporter attributes a policy claim to a quoted official.",
            "page": None,
            "paragraph": 1,
        },
        {
            "id": "actual-source-paragraph-two",
            "text": "The following paragraph describes the available public evidence.",
            "page": None,
            "paragraph": 2,
        },
    ]
    store = Store(directory)
    store.init()
    store.execute(
        "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
        [project, "Synthetic unassessed CPU workspace", utcnow()],
    )
    store.execute(
        "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
        ["batch-a", project, "Synthetic5,000-source import", utcnow()],
    )
    seed_many_documents(store, 4999, project)
    store.execute(
        "INSERT INTO batch_members(batch_id,document_id) SELECT batch_id,id FROM documents WHERE project_id=?",
        [project],
    )
    seed_document(store, document, project, text=blocks[0]["text"])
    store.put_json(f"test-sources/{document}/blocks.json", blocks)
    store.execute(
        "UPDATE documents SET title=?,created_at=? WHERE id=?",
        [title, "2099-01-01T00:00:00+00:00", document],
    )
    assert store.query("SELECT COUNT(*) AS n FROM v3_assessments")[0]["n"] == 0
    store.close()

    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        captured = []
        prohibited_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                prohibited_requests.append(request.url)
                if request.method == "POST"
                and any(
                    request.url.endswith(path)
                    for path in ("/runs", "/models/health", "/semantic-index", "/verification")
                )
                else None
            ),
        )

        def score(route):
            captured.append(route.request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "document_id": document,
                        "results": [],
                        "notice": "Synthetic scorer response; no model loaded or result assessed.",
                    }
                ),
            )

        page.route(f"**/api/v3/projects/{project}/rubric-score", score)
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
        page.get_by_label("Analysis snapshot", exact=True).select_option("none")
        page.get_by_text("Corpus · not analyzed", exact=True).wait_for()
        page.locator(".w-source-row").filter(has_text=title).get_by_role("button").first.click()
        page.locator(".w-source-passage").get_by_text(blocks[1]["text"], exact=True).wait_for()
        page.get_by_role("button", name="Cloud / local", exact=True).click()
        page.get_by_text("Optional CPU retrieval & rubric experiments", exact=True).click()
        passage = page.get_by_label("Selected source passage", exact=True)
        assert passage.input_value() == blocks[0]["id"]
        button = page.get_by_role("button", name="Score the selected source passage", exact=True)
        assert button.is_enabled(), "An unassessed source passage must be independently scoreable"
        passage.select_option(blocks[1]["id"])
        page.locator("dialog").get_by_text(blocks[1]["text"], exact=False).wait_for()
        hypothesis = "The passage describes publicly available evidence."
        page.get_by_label("Experimental rubric proposition", exact=True).fill(hypothesis)
        button.click()
        page.get_by_text("Actual task output & provenance", exact=True).wait_for()
        assert captured == [
            {
                "document_id": document,
                "block_id": blocks[1]["id"],
                "hypotheses": [{"id": "explicit-proposition", "text": hypothesis}],
            }
        ]
        assert not prohibited_requests, prohibited_requests
        assert not errors, errors
        page.screenshot(path=str(output / "synthetic-unassessed-source-cpu.png"), full_page=True)
        browser.close()

    result = {
        "fixture_notice": "Synthetic corpus, real query/context APIs, scorer mocked; no model or inference requested.",
        "unassessed_corpus_sources": 5000,
        "prior_assessments": 0,
        "selected_document_id": document,
        "selected_authoritative_block_id": blocks[1]["id"],
        "source_only_score_enabled": True,
        "score_requests": captured,
        "model_or_analysis_requests": prohibited_requests,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
