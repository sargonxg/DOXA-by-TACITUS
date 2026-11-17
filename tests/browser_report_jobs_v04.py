"""Actual v0.4 API/browser publication lifecycle on synthetic frozen records.

There are no mocked HTTP endpoints. Analysis workers stay off; the test invokes
only the offline report worker after explicit UI requests. No model is loaded.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from browser_support import ROOT, chromium_launch_options, local_app

sys.path.insert(0, str(ROOT))
from doxa.contextual import build_units  # noqa: E402
from doxa.lenses import get_lens  # noqa: E402
from doxa.report_jobs import ReportJobs  # noqa: E402
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_assessment, seed_document, seed_run  # noqa: E402


def fixtures(directory):
    project, document, run = "real-publication-api", "source-fixture", "frozen-ui-run"
    texts = [
        "A witness says the proposal gives one institution too much power. The testimony is reported as a claim rather than independently established as fact.",
        "The source records a second proposition without a completed assessment. Its remaining criterion decisions are intentionally absent in this synthetic software fixture.",
    ]
    blocks = [
        {"id": f"b{index + 1}", "text": text, "page": None, "paragraph": index + 1}
        for index, text in enumerate(texts)
    ]
    source_hash = document + "-hash"
    source_version = hashlib.sha256(f"{document}:{source_hash}".encode()).hexdigest()[:32]
    units = build_units(
        {"id": document, "sha256": source_hash, "source_version_id": source_version, "blocks": blocks},
        max_unit_chars=200,
    )
    assert len(units) == 2
    store = Store(directory)
    store.init()
    store.execute(
        "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
        [project, "Synthetic real API publication", utcnow()],
    )
    store.execute(
        "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
        ["batch-a", project, "Synthetic source import", utcnow()],
    )
    seed_document(store, document, project, text=texts[0])
    store.put_json(f"test-sources/{document}/blocks.json", blocks)
    store.execute(
        "UPDATE documents SET page_count=0,word_count=? WHERE id=?",
        [sum(len(text.split()) for text in texts), document],
    )
    seed_run(store, run, project, status="partial", sources=[{"id": document, "sha256": document + "-hash"}])
    lens = get_lens("discourse")
    config = {
        "methodology_version": "3.0",
        "provider": "openai",
        "model": "frozen-software-fixture",
        "unit_max_chars": 200,
        "lens_ids": ["discourse"],
        "lens_snapshots": [lens],
        "source_snapshot": [{"id": document, "sha256": document + "-hash"}],
    }
    store.execute("UPDATE runs SET config_json=? WHERE id=?", [json.dumps(config), run])
    for index, outcome in enumerate(("meets_criterion", "does_not_meet", "uncertain", "not_applicable")):
        identity = f"assessment-{index}"
        value = seed_assessment(
            store,
            identity,
            document,
            run,
            project,
            lens="discourse",
            criterion=lens["criteria"][index]["id"],
            kind="descriptive",
            result=outcome,
            text=texts[0],
            unit=units[0]["id"],
        )
        value.update(
            {
                "detail_supplied": outcome in {"meets_criterion", "uncertain"},
                "evidence_refs": [{"block_id": "b1", "start": 0, "end": len(texts[0]), "quote": texts[0]}],
            }
        )
        if not value["detail_supplied"]:
            value["explanation"] = (
                f"The model explicitly returned {outcome}; no detailed rationale was supplied."
            )
        store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(value), identity])
    store.execute(
        "UPDATE v3_unit_statuses SET payload_json=? WHERE run_id=? AND unit_id=?",
        [json.dumps(units[0]), run, units[0]["id"]],
    )
    store.execute(
        "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
        [run, document, units[1]["id"], "partial", len(units[1]["text"]), json.dumps(units[1]), utcnow()],
    )
    store.close()
    return project, document, run, units[1]["id"]


def finish_report(directory, identity):
    store = Store(directory)
    manager = ReportJobs(store)
    try:
        for _ in range(8):
            value = manager.get(identity)
            if value["status"] in {"completed", "failed", "cancelled"}:
                assert value["status"] == "completed", value
                return value
            manager.work_once(identity, deadline_seconds=120)
        raise AssertionError("Offline report did not settle within bounded test steps")
    finally:
        store.close()


def main():
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-report-jobs-real-v04"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-report-jobs-real-"))
    project, document, run, unfinished = fixtures(directory)
    errors, inference = [], []
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1600, "height": 1080}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                inference.append(request.url)
                if request.method == "POST"
                and any(
                    request.url.endswith(path)
                    for path in ("/runs", "/verification", "/rubric-score", "/semantic-index")
                )
                else None
            ),
        )
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-observation-row").first.click()
        page.locator(".w-source-passage mark").wait_for()
        page.get_by_role("button", name="Pin this case", exact=True).click()
        page.get_by_role("button", name="Pinned to casebook", exact=True).wait_for()
        page.get_by_role("button", name="Open casebook", exact=True).click()
        page.get_by_label("Case selection rationale", exact=True).fill(
            "Curated software fixture: all four recorded outcomes and a genuinely unfinished source unit; no quality estimate is inferred."
        )
        page.get_by_role("button", name="Save draft details", exact=True).click()
        page.get_by_text("Browse cases across all four recorded outcomes", exact=True).click()
        for outcome in ("does_not_meet", "uncertain", "not_applicable"):
            page.get_by_label("Case browser outcome", exact=True).select_option(outcome)
            candidate = page.locator(".w-case-candidates article").filter(
                has=page.get_by_text(outcome.replace("_", " "), exact=True)
            )
            candidate.get_by_role("button", name="Pin case", exact=True).click()
            expect(candidate.get_by_role("button", name="Pinned", exact=True)).to_be_visible()
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.get_by_role("button", name="Review queues", exact=True).click()
        page.get_by_label("Review queue", exact=True).select_option("unfinished")
        page.get_by_role("button", name="Open unfinished unit context", exact=True).click()
        page.get_by_text("Unfinished source unit · decisions incomplete", exact=True).wait_for()
        assert "unit=" + unfinished in page.url
        assert page.locator(".w-source-passage").inner_text().find("second proposition") >= 0
        expect(page.get_by_role("tab", name="Interpretation", exact=True)).to_be_disabled()
        page.reload(wait_until="domcontentloaded")
        page.get_by_text("Unfinished source unit · decisions incomplete", exact=True).wait_for()
        page.get_by_role("button", name="Review queues", exact=True).click()
        page.get_by_label("Review queue", exact=True).select_option("unfinished")
        page.get_by_role("button", name="Pin review case", exact=True).click()
        page.get_by_role("button", name="Pinned to casebook", exact=True).wait_for()
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.get_by_role("button", name="Export query report", exact=True).click()
        page.get_by_label("Publication purpose", exact=True).select_option("casebook")
        expect(page.locator(".w-pinned-case")).to_have_count(5)
        page.get_by_label("Publication format", exact=True).select_option("pdf")
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/projects/{project}/report-jobs")
                and response.request.method == "POST"
            )
        ) as created:
            page.get_by_role("button", name="Prepare publication", exact=True).click()
        assert created.value.status == 202, created.value.text()
        job = created.value.json()
        page.get_by_role("button", name="Cancel publication", exact=True).wait_for()
        page.get_by_role("button", name="Cancel publication", exact=True).click()
        page.get_by_role("button", name="Restart publication", exact=True).click()
        completed = finish_report(directory, job["id"])
        assert completed["status"] == "completed"
        link = page.get_by_role("link", name="Download PDF", exact=False)
        link.wait_for(timeout=120000)
        with page.expect_download(timeout=60000) as download:
            link.click()
        file = output / download.value.suggested_filename
        download.value.save_as(file)
        assert file.read_bytes().startswith(b"%PDF") and file.stat().st_size > 2000
        artifact_url = f"{base}/api/v3/report-jobs/{job['id']}/artifacts/pdf"
        partial = context.request.get(artifact_url, headers={"Range": "bytes=0-31"})
        assert partial.status == 206 and len(partial.body()) == 32
        assert partial.headers.get("etag")
        books = context.request.get(f"{base}/api/v3/projects/{project}/casebooks").json()
        book = context.request.get(f"{base}/api/v3/casebooks/{books['items'][0]['id']}").json()
        assert len(book["cases"]) == 5
        assert {case["result"] for case in book["cases"] if case["kind"] == "assessment"} == {
            "meets_criterion",
            "does_not_meet",
            "uncertain",
            "not_applicable",
        }
        unresolved = next(case for case in book["cases"] if case["kind"] == "unresolved")
        assert unresolved["assessment_id"] is None and unresolved["result"] is None
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Export query report", exact=True).click()
        page.get_by_label("Publication purpose", exact=True).select_option("casebook")
        expect(page.locator(".w-pinned-case")).to_have_count(5)
        page.get_by_role("link", name="Download PDF", exact=False).wait_for()
        page.screenshot(path=str(output / "actual-api-casebook-composer.png"), full_page=True)
        assert not errors, errors
        assert not inference, inference
        browser.close()
    result = {
        "fixture_notice": __doc__,
        "run_id": run,
        "casebook_id": book["id"],
        "four_outcomes_and_unresolved": True,
        "actual_unit_context_restored": True,
        "job_id": job["id"],
        "job_status": completed["status"],
        "cancel_restart_completed": True,
        "download_filename": file.name,
        "download_bytes": file.stat().st_size,
        "range_http_status": partial.status,
        "etag": partial.headers["etag"],
        "casebook_and_artifact_restored": True,
        "page_errors": errors,
        "inference_requests": inference,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
