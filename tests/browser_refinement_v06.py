"""Actual v0.6 browser/API refinement flows with explicit synthetic sources.

No routes are mocked, no model inference is requested and no cloud resources or
payments are created. Queued publication is observed without starting its worker.
Interpretation quality is not measured by this software workflow.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader
from browser_support import ROOT, chromium_launch_options, local_app

sys.path.insert(0, str(ROOT))
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_document  # noqa: E402


@contextmanager
def environment(**changes):
    previous = {key: os.environ.get(key) for key in changes}
    for key, value in changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def fixtures(directory):
    store = Store(directory)
    store.init()
    store.execute("INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)",
                  ["refinement", "Synthetic council reporting study", "Fictional sources for browser behavior, not model-quality evidence.", utcnow()])
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
                  ["refinement-batch", "refinement", "Synthetic browser sources", utcnow()])
    rows = [
        ("east", "East Gazette — council budget", "2026-10-01", "council budget vote", "East Gazette", "The council approved the budget. The mayor described it as an investment in shared services."),
        ("west", "West Ledger — council budget", "2026-10-02", "council budget vote", "West Ledger", "The council approved its budget. An opposition member questioned its long-term costs."),
        ("unmatched", "East Gazette — transport hearing", "2026-10-03", "transport hearing", "East Gazette", "The committee heard evidence on transport. No budget vote was held at this hearing."),
    ]
    for identity, title, published, event, family, text in rows:
        seed_document(store, identity, "refinement", batch="refinement-batch", text=text,
                      author="Fictional attributed reporter", published=published, sha=hashlib.sha256(text.encode()).hexdigest(),
                      metadata={"event": event, "topic": "municipal spending", "genre": "news", "source_family": family,
                                "data_origin": "synthetic_browser_fixture", "independent_reference_labels": False})
        store.execute("UPDATE documents SET title=? WHERE id=?", [title, identity])
    store.close()
    return {row[0]: row[1] for row in rows}


def main():
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-v06-browser"))
    output.mkdir(parents=True, exist_ok=True)
    checks, errors, inference_requests = [], [], []
    with tempfile.TemporaryDirectory(prefix="doxa-refinement-v06-") as temporary, environment(
        DOXA_ACCESS_MODE="local", DOXA_ACCESS_CONFIG_FILE=None, DOXA_INTERNAL_TOKEN=None,
        DOXA_CLOUD_STATE_URL=None, DOXA_LOCAL_PROFILE="low-memory", DOXA_REQUIRE_ACCEPTED_QUOTE="false",
        DOXA_AUTOMATIC_RETENTION="false", DOXA_DELETION_LEDGER_PATH=None, DOXA_DELETION_LEDGER_KEY=None,
        DOXA_DELETION_LEDGER_PUBLISH_ARGV=None, DOXA_INSTALLATION_ID=None, HF_HUB_OFFLINE="1", DOXA_ENABLE_OCR="0",
    ):
        data = Path(temporary) / "data"
        titles = fixtures(data)
        with local_app(data) as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(**chromium_launch_options())
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: inference_requests.append(request.url)
                    if request.method == "POST" and re.search(r"/api/(?:v3/)?projects/[^/]+/runs$", request.url) else None)
            page.goto(f"{base}/?workspace=refinement", wait_until="domcontentloaded")
            panel = page.get_by_role("region", name="Guided workflow and research quality")
            expect(panel.get_by_role("heading", name="Start small. Keep the evidence in view.")).to_be_visible()
            expect(panel.get_by_role("list", name="Research workflow").get_by_role("listitem")).to_have_count(5)
            expect(panel.get_by_text("low memory profile", exact=False)).to_be_visible()
            checks.append("The same one-page workbench presents five research steps and an explicit low-memory profile")

            panel.get_by_role("button", name="Readiness", exact=True).click()
            expect(panel.get_by_role("heading", name="What is ready on this computer?")).to_be_visible()
            expect(panel.get_by_text("These checks do not load a model", exact=False)).to_be_visible()
            readiness = page.request.get(f"{base}/api/system/readiness").json()
            assert readiness["profile"]["name"] == "low-memory"
            assert readiness["profile"]["application_budget_mib"] == 4096
            assert readiness["profile"]["parser_slots"] == 1
            assert readiness["model_requests"] == readiness["network_requests"] == 0
            assert readiness["private_paths_included"] is False
            checks.append("Readiness exposes bounded low-memory settings and safe metadata without loading a model")
            panel.get_by_role("button", name="Readiness", exact=True).click()

            before_projects = page.request.get(f"{base}/api/projects").json()
            before_documents = page.request.get(f"{base}/api/projects/refinement/documents").json()
            before_activity = page.request.get(f"{base}/api/v3/projects/refinement/activity").json()
            panel.get_by_role("button", name="Explore 20 recorded sources", exact=True).click()
            explorer = panel.locator(".w-recorded-example")
            expect(explorer.get_by_role("heading", name="Real sources. Recorded readings. Inspectable context.")).to_be_visible()
            expect(explorer.get_by_role("combobox", name="Recorded example source").locator("option")).to_have_count(20)
            expect(explorer).to_contain_text("645 original model assessments")
            recorded = page.request.get(f"{base}/api/system/recorded-example").json()
            assert recorded["mode"] == "read_only_recorded_exploration"
            assert recorded["fresh_inference_requests"] == 0 and recorded["mutable_workspace_installed"] is False
            assert recorded["independent_human_reference_labels"] is False
            assert recorded["selection"]["counts"]["exact_references"] == 11280
            uncertain = next(row for row in recorded["records"] if any(item["result"] == "uncertain" for item in row["assessments"]))
            explorer.get_by_role("combobox", name="Recorded example source").select_option(uncertain["document_id"])
            explorer.get_by_role("combobox", name="Recorded outcome").select_option("uncertain")
            explorer.locator(".w-method-card").first.locator("summary").click()
            expect(explorer.locator(".w-method-card").first).to_contain_text("not an independently adjudicated finding")
            explorer.screenshot(path=str(output / "recorded-example-v06.png"))
            explorer.get_by_text("Read the frozen source text and credits", exact=True).click()
            selected_source = next(row for row in recorded["documents"] if row["id"] == uncertain["document_id"])
            expect(explorer).to_contain_text(selected_source["blocks"][0]["text"])
            assert page.request.get(f"{base}/api/projects").json() == before_projects
            assert page.request.get(f"{base}/api/projects/refinement/documents").json() == before_documents
            assert page.request.get(f"{base}/api/v3/projects/refinement/activity").json() == before_activity
            checks.append("The read-only explorer preserves twenty real recorded sources, uncertain model readings and exact full text without changing projects or activities")

            with page.expect_download() as downloaded:
                explorer.get_by_role("link", name="Download PDF", exact=True).click()
            pdf = output / "v06-recorded-example-downloaded.pdf"
            downloaded.value.save_as(pdf)
            manifest = json.loads((ROOT / "doxa/data/recorded-example.manifest.json").read_text())
            expected_pdf = next(row for row in manifest["files"] if row["packaged_file"] == "recorded-example.pdf")
            assert pdf.stat().st_size == expected_pdf["bytes"]
            assert hashlib.sha256(pdf.read_bytes()).hexdigest() == expected_pdf["sha256"]
            assert len(PdfReader(pdf).pages) > 0
            checks.append("A real recorded-example PDF download matches its publication SHA-256 and opens as a valid PDF without inference")
            panel.get_by_role("button", name="Explore 20 recorded sources", exact=True).click()

            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/projects/refinement/upload")) as uploaded:
                panel.get_by_role("button", name="Try five practice sources", exact=True).click()
            assert uploaded.value.status == 200
            expect(panel.get_by_role("status")).to_contain_text("Five fictional practice sources are ready")
            sources = page.request.get(f"{base}/api/projects/refinement/documents").json()
            assert len(sources) == 8
            starter = page.request.get(f"{base}/api/system/starter-pack").json()
            assert starter["data_origin"] == "synthetic_demonstration" and starter["recorded_model_results"] is False
            for row in starter["files"]:
                assert hashlib.sha256(row["text"].encode()).hexdigest() == row["sha256"]
            checks.append("All five published synthetic starter texts upload through real intake with verified input hashes and no fabricated model results")

            panel.get_by_role("button", name="Method cards", exact=True).click()
            expect(panel.get_by_role("heading", name="The method behind each observation")).to_be_visible()
            expect(panel.locator(".w-method-card").first).to_be_visible()
            panel.locator(".w-method-card").first.locator("summary").click()
            expect(panel.locator('.w-method-card[open] a[href="https://arxiv.org/abs/1912.05511"]')).to_be_visible()
            expect(panel.locator(".w-method-card[open]")).to_contain_text("do not certify DOXA")
            panel.locator(".w-method-card[open]").screenshot(path=str(output / "method-card-v06.png"))
            cards = page.request.get(f"{base}/api/v3/method-cards").json()
            assert cards["cards"] and all(card["validation"]["status"] == "reference_pending" for card in cards["cards"])
            checks.append("Criterion method cards open real academic references and retain unmeasured interpretation status")
            panel.get_by_role("button", name="Method cards", exact=True).click()

            panel.get_by_role("button", name="Matched comparisons", exact=True).click()
            expect(panel.get_by_role("heading", name="Compare like with like")).to_be_visible()
            panel.get_by_role("checkbox", name=f"Left cohort: {titles['east']}", exact=True).check()
            panel.get_by_role("checkbox", name=f"Left cohort: {titles['unmatched']}", exact=True).check()
            panel.get_by_role("checkbox", name=f"Right cohort: {titles['west']}", exact=True).check()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/v3/projects/refinement/comparisons")) as frozen:
                panel.get_by_role("button", name="Freeze matched comparison", exact=True).click()
            assert frozen.value.status == 201
            comparison = frozen.value.json()
            assert comparison["summary"] == {"left_selected": 2, "right_selected": 1, "matched_pairs": 1, "excluded_sources": 1}
            assert comparison["pairs"][0]["left_document_id"] == "east"
            assert comparison["pairs"][0]["right_document_id"] == "west"
            assert comparison["excluded"] == [{"document_id": "unmatched", "reason": "no_eligible_one_to_one_match", "missing_fields": []}]
            assert comparison["inference_statistics_enabled"] is False
            expect(panel.get_by_role("heading", name=re.compile(r"^1 matched pairs? · 1 excluded sources?$"))).to_be_visible()
            panel.get_by_text("Inspect exclusions", exact=True).click()
            expect(panel.locator(".w-comparison-result")).to_contain_text("no eligible one to one match")
            panel.locator(".w-comparison-result").screenshot(path=str(output / "comparison-v06.png"))
            checks.append("A frozen comparison matches the declared event/date/genre and visibly excludes an unrelated source")

            panel.get_by_role("checkbox", name=f"Left cohort: {titles['east']}", exact=True).uncheck()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/v3/projects/refinement/comparisons")) as not_matched:
                panel.get_by_role("button", name="Freeze matched comparison", exact=True).click()
            assert not_matched.value.json()["status"] == "comparison_not_established"
            expect(panel.locator(".w-comparison-result")).to_contain_text("comparison not established")
            checks.append("An unmatched selection reports comparison not established instead of inventing an asymmetry")
            panel.get_by_role("button", name="Matched comparisons", exact=True).click()

            quote_response = page.request.post(f"{base}/api/pilot/projects/refinement/quotes", data={
                "idempotency_key": "browser-synthetic-quote", "profile_id": "public", "fixed_price_cents": 12500,
                "processing_cap_microusd": 2_000_000, "scope": {"documents": 20, "characters": 100_000, "criteria": 6, "ocr_pages": 0},
            })
            assert quote_response.status == 201, quote_response.text()
            offered = quote_response.json()
            panel.get_by_role("button", name="Pilot scope", exact=True).click()
            expect(panel.get_by_role("heading", name="Your agreed pilot scope")).to_be_visible()
            expect(panel.get_by_text("$125.00 USD fixed price", exact=False)).to_be_visible()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(f"/quotes/{offered['id']}/accept")) as accepted:
                panel.get_by_role("button", name="Accept agreed scope", exact=True).click()
            assert accepted.value.status == 200
            actual = accepted.value.json()
            assert actual["scope_sha256"] == offered["scope_sha256"] and actual["status"] == "accepted"
            assert actual["payment_status"] == "not_recorded"
            expect(panel.get_by_text("public · accepted", exact=False)).to_be_visible()
            checks.append("The customer accepts the exact operator-issued scope hash while price, processing cap and unrecorded payment remain distinct")
            panel.get_by_role("button", name="Pilot scope", exact=True).click()

            brief_response = page.request.put(f"{base}/api/pilot/projects/refinement/brief",
                                              data={"profile_id": "public", "expected_revision": 0})
            assert brief_response.status == 200, brief_response.text()
            queued = page.request.post(f"{base}/api/v3/projects/refinement/jobs", headers={"Idempotency-Key": "browser-queued-guided-report"},
                                       data={"kind": "guided_report", "document_ids": ["east", "west"], "formats": ["json"],
                                             "expert_commentary": [], "review_ack": False})
            assert queued.status == 202, queued.text()
            job_id = queued.json()["id"]
            panel.get_by_role("button", name="Activity center", exact=True).click()
            expect(panel.get_by_role("heading", name="Committed work survives the browser")).to_be_visible()
            expect(panel.locator(".w-activity-line").filter(has_text="guided report")).to_contain_text("queued")
            page.reload(wait_until="domcontentloaded")
            panel.get_by_role("button", name="Activity center", exact=True).click()
            expect(panel.locator(".w-activity-line").filter(has_text="guided report")).to_contain_text("queued")
            persisted = page.request.get(f"{base}/api/v3/jobs/{job_id}").json()
            assert persisted["id"] == job_id and persisted["status"] == "queued"
            checks.append("The activity center recovers the same committed queued guided-report job after browser refresh; no publication worker or model runs")
            panel.get_by_role("button", name="Activity center", exact=True).click()

            focus_button = panel.get_by_role("button", name="Readiness", exact=True)
            focus_button.focus()
            expect(focus_button).to_be_focused()
            page.keyboard.press("Enter")
            expect(panel.get_by_role("heading", name="What is ready on this computer?")).to_be_visible()
            focus_style = focus_button.evaluate("el => ({style:getComputedStyle(el).outlineStyle,width:getComputedStyle(el).outlineWidth})")
            assert focus_style["style"] != "none" and float(focus_style["width"].removesuffix("px")) >= 2
            page.keyboard.press("Enter")
            checks.append("Keyboard activation and a visible focus outline work on the refinement controls")

            page.screenshot(path=str(output / "workspace-v06-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(panel.get_by_role("heading", name="Start small. Keep the evidence in view.")).to_be_visible()
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not overflow
            panel.get_by_role("button", name="Readiness", exact=True).click()
            expect(panel.get_by_role("heading", name="What is ready on this computer?")).to_be_visible()
            page.screenshot(path=str(output / "workspace-v06-mobile.png"), full_page=True)
            panel.get_by_role("button", name="Readiness", exact=True).click()
            panel.get_by_role("button", name="Explore 20 recorded sources", exact=True).click()
            expect(panel.get_by_role("combobox", name="Recorded example source").locator("option")).to_have_count(20)
            assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            panel.locator(".w-recorded-example").screenshot(path=str(output / "recorded-example-v06-mobile.png"))
            checks.append("The 390-pixel viewport retains readable controls and the recorded-source reader without horizontal page overflow")
            assert not errors, errors
            assert not inference_requests, inference_requests
            browser.close()
    receipt = {"schema_version": "doxa-browser-refinement-1", "status": "passed", "data_origin": "synthetic_browser_fixtures_with_recorded_real_public_example",
               "checks": checks, "check_count": len(checks), "page_errors": errors, "mocked_routes": 0,
               "analysis_run_submissions": len(inference_requests), "new_model_inference_requests": 0,
               "publication_worker_started": False, "cloud_resources_created": False, "payments_created": False,
               "interpretation_accuracy": None, "independent_human_reference_labels": False,
               "screenshots": ["workspace-v06-desktop.png", "workspace-v06-mobile.png", "method-card-v06.png", "comparison-v06.png", "recorded-example-v06.png", "recorded-example-v06-mobile.png"]}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
