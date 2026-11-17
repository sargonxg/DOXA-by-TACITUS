"""V0.4 casebook and publication UI contracts; no model or paid requests.

Real synthetic source/query/context APIs are used. Publication jobs, draft
persistence and review-queue envelopes are mocked explicitly for the UI test.
Backend persistence and streaming have their own integration tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from browser_support import ROOT, chromium_launch_options, local_app

sys.path.insert(0, str(ROOT))
from doxa.lenses import get_lens  # noqa: E402
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_assessment, seed_document, seed_run  # noqa: E402


def fixtures(directory):
    project, document, run = "publication-ui", "case-source", "recorded-ui-run"
    text = "The source reports a quoted claim and provides surrounding policy context."
    store = Store(directory)
    store.init()
    store.execute(
        "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
        [project, "Synthetic publication UI", utcnow()],
    )
    store.execute(
        "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
        ["batch-a", project, "Synthetic source", utcnow()],
    )
    seed_document(store, document, project, text=text)
    seed_run(store, run, project, status="partial", sources=[{"id": document, "sha256": document + "-hash"}])
    lens = get_lens("discourse")
    config = {
        "methodology_version": "3.0",
        "provider": "openai",
        "model": "mock-recorded",
        "lens_ids": ["discourse"],
        "lens_snapshots": [lens],
        "source_snapshot": [{"id": document, "sha256": document + "-hash"}],
    }
    store.execute("UPDATE runs SET config_json=? WHERE id=?", [json.dumps(config), run])
    records = {}
    for index, result in enumerate(("meets_criterion", "does_not_meet", "uncertain", "not_applicable")):
        identity = f"case-{index}"
        value = seed_assessment(
            store,
            identity,
            document,
            run,
            project,
            lens="discourse",
            criterion=lens["criteria"][index]["id"],
            kind="descriptive",
            result=result,
            text=text,
        )
        value["detail_supplied"] = result in {"meets_criterion", "uncertain"}
        store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(value), identity])
        records[identity] = value
    store.execute(
        "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
        [run, document, "unfinished-unit", "partial", len(text), json.dumps({"block_ids": ["b1"]}), utcnow()],
    )
    store.close()
    return project, document, run, records


def main():
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-publications-v04"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-publications-v04-"))
    project, document, run, records = fixtures(directory)
    state = {
        "book": None,
        "jobs": [],
        "pin_requests": [],
        "job_requests": [],
        "job_actions": [],
        "queue_reads": [],
    }
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

        def fulfill(route, value, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(value))

        def books(route):
            if route.request.method == "GET":
                return fulfill(
                    route,
                    {
                        "items": [state["book"]] if state["book"] else [],
                        "total": int(state["book"] is not None),
                        "next_cursor": None,
                    },
                )
            body = route.request.post_data_json
            frozen = context.request.get(f"{base}/api/v3/queries/{body['query_id']}").json()
            state["book"] = {
                "id": "saved-casebook",
                "project_id": project,
                "query_id": body["query_id"],
                "run_id": run,
                "scope_hash": frozen["scope_hash"],
                "snapshot_version": frozen["snapshot_version"],
                "title": body["title"],
                "selection_rationale": "",
                "publication_type": "casebook",
                "revision": 1,
                "cases": [],
            }
            return fulfill(route, state["book"])

        def draft(route):
            method = route.request.method
            path = route.request.url.split("/api/v3/casebooks/saved-casebook", 1)[1]
            book = state["book"]
            if method == "GET":
                return fulfill(route, book)
            body = route.request.post_data_json if method != "DELETE" else {}
            if path == "" and method == "PATCH":
                book.update(body)
            elif path == "/cases" and method == "POST":
                state["pin_requests"].append(body)
                assessment = records.get(body.get("assessment_id"))
                book["cases"].append(
                    {
                        "id": f"pin-{len(book['cases'])}",
                        "kind": "assessment" if assessment else "unresolved",
                        "assessment_id": body.get("assessment_id"),
                        "document_id": body["document_id"],
                        "unit_id": body.get("unit_id"),
                        "result": assessment["result"] if assessment else None,
                        "criterion_kind": "descriptive" if assessment else None,
                        "ordinal": len(book["cases"]),
                        "editorial_note": "",
                        "selection_rationale": "",
                        "source_snapshot": {"title": "Source case-source", "author": "Author A"},
                        "assessment_snapshot": assessment,
                        "unit_snapshot": {"status": "partial"} if not assessment else None,
                    }
                )
            elif path == "/order":
                by_id = {item["id"]: item for item in book["cases"]}
                book["cases"] = [by_id[identity] for identity in body["case_ids"]]
            elif path.startswith("/cases/"):
                identity = path.rsplit("/", 1)[1]
                if method == "DELETE":
                    book["cases"] = [item for item in book["cases"] if item["id"] != identity]
                else:
                    next(item for item in book["cases"] if item["id"] == identity).update(body)
            else:
                raise AssertionError((method, path))
            book["revision"] += 1
            return fulfill(route, book)

        def jobs(route):
            if route.request.method == "GET":
                return fulfill(
                    route, {"items": state["jobs"], "total": len(state["jobs"]), "next_cursor": None}
                )
            body = route.request.post_data_json
            state["job_requests"].append(body)
            book = state["book"]
            state["jobs"] = [
                {
                    "id": "persistent-publication",
                    "project_id": project,
                    "query_id": body["query_id"],
                    "scope_hash": book["scope_hash"],
                    "snapshot_version": book["snapshot_version"],
                    "casebook_revision": book["revision"],
                    "status": "running",
                    "phase": "rendering",
                    "progress": {"percent": 40},
                    "artifacts": [],
                    "publication_type": body["publication_type"],
                    "format": body["format"],
                    "profile": body["profile"],
                    "frozen_case_count": len(book["cases"]),
                }
            ]
            return fulfill(route, state["jobs"][0], 202)

        def job_action(route):
            action = route.request.url.rsplit("/", 1)[1]
            job = state["jobs"][0]
            if action in {"cancel", "restart"}:
                state["job_actions"].append(action)
                job.update(
                    {
                        "status": "cancelled" if action == "cancel" else "completed",
                        "phase": "cancelled" if action == "cancel" else "completed",
                    }
                )
                if action == "restart":
                    job["artifacts"] = [{"format": "pdf", "filename": "synthetic-casebook.pdf", "bytes": 24}]
                return fulfill(route, job)
            route.fulfill(
                status=200,
                content_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="synthetic-casebook.pdf"'},
                body=b"%PDF-1.7 synthetic UI data",
            )

        def queue(route):
            state["queue_reads"].append(route.request.url)
            value = {
                "id": "unfinished-review-item",
                "type": "unfinished",
                "document_id": document,
                "unit_id": "unfinished-unit",
                "assessment_id": None,
                "unit_context_available": True,
                "source_title": "Source case-source",
                "source_author": "Author A",
                "status": "partial",
                "missing_criterion_keys": ["discourse/framing"],
                "accepted_criteria": 0,
                "errors": [{"code": "unresolved", "message": "Synthetic missing decision"}],
            }
            return fulfill(
                route,
                {
                    "items": [value],
                    "total": 1,
                    "next_cursor": None,
                    "scope_note": "Synthetic unfinished source-unit queue; no model decision has been invented.",
                },
            )

        page.route(f"**/api/v3/projects/{project}/casebooks**", books)
        page.route("**/api/v3/casebooks/saved-casebook**", draft)
        page.route(f"**/api/v3/projects/{project}/report-jobs**", jobs)
        context.route("**/api/v3/report-jobs/persistent-publication/**", job_action)
        page.route(f"**/api/v3/projects/{project}/review-queue**", queue)

        def unit_context(route):
            source = context.request.get(f"{base}/api/v3/documents/{document}/context").json()
            source.update(
                {
                    "finding": None,
                    "unit": {"id": "unfinished-unit", "block_id": "b1", "text": source["blocks"][0]["text"]},
                }
            )
            return fulfill(route, source)

        page.route(f"**/api/v3/projects/{project}/unit-context**", unit_context)
        initial = context.request.post(
            f"{base}/api/v3/projects/{project}/query",
            data={"run_id": run, "authors": ["Author A"], "results": ["meets_criterion"]},
        ).json()
        page.goto(f"{base}/?workspace={project}&query={initial['query_id']}", wait_until="domcontentloaded")
        page.locator(".w-observation-row").first.click()
        page.locator(".w-source-passage mark").wait_for()
        source_tab = page.get_by_role("tab", name="Source passage", exact=True)
        source_tab.focus()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("tab", name="Interpretation", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        page.get_by_role("region", name="Framework condition").wait_for()
        page.get_by_role("button", name="Pin this case", exact=True).click()
        page.get_by_role("button", name="Pinned to casebook", exact=True).wait_for()
        page.get_by_role("button", name="Open casebook", exact=True).click()
        dialog = page.get_by_role("dialog", name="Export this research scope", exact=True)
        expect(dialog).to_be_visible()
        rationale = page.get_by_label("Case selection rationale", exact=True)
        rationale.fill(
            "Illustrate all four outcomes and one missing decision; this is a curated software fixture, not an error-rate sample."
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
        assert {records[item["assessment_id"]]["result"] for item in state["pin_requests"]} == {
            "meets_criterion",
            "does_not_meet",
            "uncertain",
            "not_applicable",
        }
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.get_by_role("button", name="Review queues", exact=True).click()
        page.get_by_label("Review queue", exact=True).select_option("unfinished")
        page.get_by_role("button", name="Open unfinished unit context", exact=True).click()
        page.get_by_text("Unfinished source unit · decisions incomplete", exact=True).wait_for()
        expect(page.get_by_role("tab", name="Interpretation", exact=True)).to_be_disabled()
        assert "unit=unfinished-unit" in page.url
        page.reload(wait_until="domcontentloaded")
        page.get_by_text("Unfinished source unit · decisions incomplete", exact=True).wait_for()
        page.get_by_role("button", name="Review queues", exact=True).click()
        page.get_by_label("Review queue", exact=True).select_option("unfinished")
        page.get_by_role("button", name="Pin review case", exact=True).click()
        page.get_by_role("button", name="Pinned to casebook", exact=True).wait_for()
        assert state["pin_requests"][-1] == {"document_id": document, "unit_id": "unfinished-unit"}
        page.get_by_role("button", name="Close panel", exact=True).click()
        page.get_by_role("button", name="Export query report", exact=True).click()
        page.get_by_label("Publication purpose", exact=True).select_option("casebook")
        expect(page.locator(".w-pinned-case")).to_have_count(5)
        page.get_by_role("button", name="Prepare publication", exact=True).click()
        page.get_by_role("button", name="Cancel publication", exact=True).wait_for()
        assert state["job_requests"][0]["casebook_id"] == "saved-casebook"
        assert state["jobs"][0]["frozen_case_count"] == 5
        page.get_by_role("button", name="Cancel publication", exact=True).click()
        page.get_by_role("button", name="Restart publication", exact=True).click()
        link = page.get_by_role("link", name="Download PDF", exact=False)
        link.wait_for()
        # Chromium's native downloads bypass Playwright route interception.
        # Real endpoint downloads are exercised separately; this mocked test
        # checks the same-origin artifact link without inventing a saved file.
        assert link.get_attribute("href") == "/api/v3/report-jobs/persistent-publication/artifacts/pdf"
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Export query report", exact=True).click()
        page.get_by_label("Publication purpose", exact=True).select_option("casebook")
        expect(page.locator(".w-pinned-case")).to_have_count(5)
        page.get_by_role("link", name="Download PDF", exact=False).wait_for()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("dialog[open]")).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "publication-composer-mobile.png"), full_page=True)
        page.get_by_label("Publication purpose", exact=True).select_option("archive")
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/projects/{project}/report-jobs")
                and response.request.method == "POST"
            )
        ):
            page.get_by_role("button", name="Prepare publication", exact=True).click()
        archive = state["job_requests"][-1]
        assert archive["publication_type"] == "archive" and "casebook_id" not in archive
        frozen_archive = context.request.get(f"{base}/api/v3/queries/{archive['query_id']}").json()
        assert set(frozen_archive["scope"]["results"]) == {
            "meets_criterion",
            "does_not_meet",
            "uncertain",
            "not_applicable",
        }
        assert frozen_archive["scope"]["authors"] == ["Author A"]
        assert not errors, errors
        assert not inference, inference
        browser.close()
    result = {
        "fixture_notice": __doc__,
        "all_four_outcomes_pinned": True,
        "unfinished_case_has_no_assessment": True,
        "persistent_job_controls": state["job_actions"],
        "job_payloads": state["job_requests"],
        "casebook_restored_after_reload": True,
        "mobile_single_named_dialog": True,
        "artifact_download_href_checked": True,
        "archive_explicitly_selects_four_outcomes_preserving_author_scope": True,
        "page_errors": errors,
        "inference_requests": inference,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
