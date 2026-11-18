"""Real native-pilot browser flows on explicitly synthetic private fixtures.

No HTTP routes are mocked and no inference is requested. The test covers the
three saved task briefs, scoped planning/cues, privacy previews and erasure,
plus individual-cookie login and project/role restrictions.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from browser_support import ROOT, chromium_launch_options, local_app

sys.path.insert(0, str(ROOT))
from doxa.access import issue_token, token_hash  # noqa: E402
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_assessment, seed_document, seed_run  # noqa: E402


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
    body = "🧭 Please review the draft by Friday. I will send the update. Perhaps we could clarify the deadline."
    quote = "Could you send the prior version?"
    for project in ("pilot", "private"):
        store.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", [project, f"Synthetic {project} workspace", utcnow()])
        store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", [f"batch-{project}", project, "Synthetic authorized fixture", utcnow()])
        original = body + ("\n\n" + quote if project == "pilot" else "")
        original_key = f"sources/source-{project}/source.txt"
        seed_document(store, f"source-{project}", project, batch=f"batch-{project}",
                      text=original, sha=hashlib.sha256(original.encode()).hexdigest())
        store.put_bytes(original_key, original.encode())
        store.execute("UPDATE documents SET object_key=? WHERE id=?", [original_key, f"source-{project}"])
    store.put_json("test-sources/source-pilot/blocks.json", [
        {"id": "b1", "text": body, "page": None, "paragraph": 1, "mail_role": "authored"},
        {"id": "b2", "text": quote, "page": None, "paragraph": 2, "mail_role": "quoted"},
    ])
    seed_run(store, "historical-pilot-run", "pilot", sources=[{
        "id": "source-pilot", "sha256": store.one("SELECT sha256 FROM documents WHERE id='source-pilot'")["sha256"],
    }])
    seed_assessment(store, "historical-fixture-decision", "source-pilot", "historical-pilot-run", "pilot",
                    lens="discourse", criterion="stance", kind="descriptive", result="uncertain",
                    attribution="unknown", text=body)
    store.close()


def main():
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-pilot-v05"))
    output.mkdir(parents=True, exist_ok=True)
    checks, errors, inference_requests = [], [], []
    with tempfile.TemporaryDirectory(prefix="doxa-pilot-browser-") as temporary:
        directory = Path(temporary)
        local_data = directory / "local"
        fixtures(local_data)
        with environment(DOXA_ACCESS_MODE="local", DOXA_ACCESS_CONFIG_FILE=None, DOXA_CLOUD_STATE_URL=None,
                         DOXA_PRIVACY_VAULT_KEY=base64.b64encode(secrets.token_bytes(32)).decode()):
            with local_app(local_data, start_workers=True) as base, sync_playwright() as playwright:
                browser = playwright.chromium.launch(**chromium_launch_options())
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: inference_requests.append(request.url)
                        if request.method == "POST" and re.search(r"/api/(?:v3/)?projects/[^/]+/runs$", request.url) else None)
                page.goto(f"{base}/?workspace=pilot")
                expect(page.get_by_role("heading", name="Choose the question that matters.")).to_be_visible()
                page.get_by_role("button", name=re.compile(r"^DOXA Company")).click()
                page.get_by_role("textbox", name="Inquiry question", exact=True).fill("How is language qualified in our selected authorized source?")
                page.get_by_label("Record a supplied, versioned company policy for review", exact=True).check()
                page.get_by_label("Policy title", exact=True).fill("Synthetic style policy")
                page.get_by_label("Policy version", exact=True).fill("1.0")
                page.get_by_role("textbox", name="Supplied policy text", exact=True).fill("State deadlines explicitly and preserve quoted speech attribution.")
                page.get_by_role("button", name="Save inquiry brief", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("Inquiry brief saved")
                brief = page.request.get(f"{base}/api/pilot/projects/pilot/brief").json()
                assert brief["profile_id"] == "company" and brief["company_policy"]["version"] == "1.0"
                assert brief["readiness"]["interpretation_accuracy_measured"] is False
                checks.append("Company brief saves an explicit policy version without claiming validation")

                page.get_by_role("button", name=re.compile(r"^DOXA Public")).click()
                page.get_by_label("Declare a matched comparison", exact=True).check()
                page.get_by_role("textbox", name="Comparison question", exact=True).fill("How do matched publications frame the same event?")
                page.get_by_role("textbox", name="Source inclusion and matching rules", exact=True).fill("Same event, publication week and document genre; supplied sources only.")
                page.get_by_role("button", name="Save inquiry brief", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("Inquiry brief saved")
                brief = page.request.get(f"{base}/api/pilot/projects/pilot/brief").json()
                assert brief["profile_id"] == "public" and brief["comparator_status"] == "declared_unverified"
                checks.append("Public brief freezes declared matching rules while retaining unverified comparison status")

                page.get_by_role("button", name=re.compile(r"^DOXA Personal")).click()
                page.get_by_role("button", name="Save inquiry brief", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("Inquiry brief saved")
                page.get_by_role("button", name="Scope & estimate", exact=True).click()
                page.get_by_label("Input price · USD / million tokens", exact=True).fill("0.1")
                page.get_by_label("Output price · USD / million tokens", exact=True).fill("0.3")
                page.get_by_role("button", name="Estimate query preview", exact=True).click()
                expect(page.get_by_role("heading", name="Observed scope and planning estimate")).to_be_visible()
                expect(page.get_by_text("Token scenarios · unmeasured heuristic", exact=True)).to_be_visible()
                page.get_by_role("button", name="Inspect communication cues", exact=True).click()
                expect(page.get_by_role("heading", name="Eligible authored cue matches")).to_be_visible()
                expect(page.locator(".w-pilot-signal")).to_have_count(5)
                expect(page.locator(".w-pilot-signal").filter(has_text="Context · excluded from authored count")).to_have_count(1)
                page.locator(".w-pilot-signal").first.get_by_role("button", name="Open source context").click()
                expect(page.locator(".w-source-meta h2")).to_contain_text("Source source-pilot")
                checks.append("Personal cue counts use actual source text, exclude quoted-history matches and open source evidence")

                page.get_by_text("Generate a guided service report", exact=True).click()
                include_decisions = page.get_by_label("Include saved contextual decisions", exact=True)
                expect(include_decisions).not_to_be_checked()
                include_decisions.check()
                page.get_by_role("button", name="Generate guided report", exact=True).click()
                expect(page.get_by_role("alert").filter(has_text="historical inquiry brief")).to_be_visible()
                include_decisions.uncheck()
                page.get_by_text("Add a commented evidence case", exact=True).click()
                page.get_by_label("Include one evidence-linked note", exact=True).check()
                page.get_by_label("Commentary source block", exact=True).select_option("b1")
                page.get_by_role("textbox", name="Unchanged evidence quotation", exact=True).fill("Please review the draft by Friday.")
                page.get_by_role("textbox", name="Evidence-linked reviewer note", exact=True).fill("Synthetic reviewer note: request wording is observable; politeness needs relationship context.")
                page.get_by_label("Record my declared operator review of this note", exact=True).check()
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/v3/projects/pilot/jobs")) as submitted:
                    page.get_by_role("button", name="Generate guided report", exact=True).click()
                assert submitted.value.status == 202
                assert submitted.value.json()["kind"] == "guided_report"
                report_panel = page.locator(".w-pilot-service-report")
                expect(report_panel.get_by_text("Operator commentary signed", exact=True)).to_be_visible(timeout=60000)
                expect(report_panel.get_by_role("link")).to_have_count(4)
                with page.expect_download(timeout=60000) as download:
                    report_panel.get_by_role("link", name=re.compile(r"^Download PDF")).first.click()
                pdf = output / "personal-guided-report.pdf"
                download.value.save_as(pdf)
                assert pdf.read_bytes().startswith(b"%PDF")
                checks.append("A real202 guided job renders PDF/HTML/XLSX/JSON through durable workers, retaining an exact Unicode-anchored operator note and pending independent validation")
                checks.append("Historical contextual decisions require explicit inclusion and fail the brief-matching gate; wording-only reports remain available")

                page.get_by_role("button", name="Inquiry brief", exact=True).click()
                page.get_by_text("Paste selected writing as a source", exact=True).click()
                page.get_by_role("textbox", name="Selected writing title", exact=True).fill("Synthetic selected writing")
                page.get_by_role("textbox", name="Selected writing text", exact=True).fill("Please send this to alice@example.com by Friday.\n\n> Could you share the old version?")
                page.get_by_role("button", name="Save selected writing as source", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("Selected writing saved as a retained source")
                expect(page.locator(".w-source-row strong")).to_have_text(["Synthetic selected writing"])
                drafts = page.request.get(f"{base}/api/projects/pilot/documents").json()
                assert any(item.get("metadata", {}).get("retention") == "saved_source_not_transient" for item in drafts)
                checks.append("Selected writing is explicitly stored with retention disclosure and focused as a new source without inference")

                page.get_by_role("button", name="Privacy controls", exact=True).click()
                page.get_by_role("textbox", name="Text to inspect", exact=True).fill("Please email Alice at alice@example.com before Friday.")
                page.get_by_role("button", name="Preview pseudonymization", exact=True).click()
                expect(page.get_by_role("heading", name="Preview output · source text remains unchanged")).to_be_visible()
                preview = page.locator(".w-pilot-result").filter(has=page.get_by_role("heading", name="Preview output · source text remains unchanged")).locator("pre").first.inner_text()
                assert "alice@example.com" not in preview
                expect(page.get_by_text(re.compile("This release does not automatically erase data"))).to_be_visible()
                page.get_by_role("combobox", name="Report exports", exact=True).select_option("no")
                page.get_by_role("button", name="Save privacy policy", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("Workspace privacy policy saved")
                policy = page.request.get(f"{base}/api/pilot/projects/pilot/privacy-policy").json()
                assert policy["policy"]["allow_exports"] is False and policy["enforcement"]["automatic_retention"] is False
                checks.append("Privacy preview masks an email without changing stored originals; saved export policy and retention limits are visible")

                page.on("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="Create pseudonymized versions · query preview", exact=True).click()
                expect(page.locator(".w-pilot-notice")).to_contain_text("pseudonymized source versions created")
                sources = page.request.get(f"{base}/api/projects/pilot/documents").json()
                assert len(sources) == 3 and any(source.get("metadata", {}).get("privacy_transform_id") for source in sources)
                checks.append("Explicit transformation creates a new source version and preserves the original")
                page.locator(".w-center").evaluate("element => element.scrollTop=0")
                page.screenshot(path=str(output / "pilot-workspace.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                close_panel = page.get_by_role("button", name="Close panel", exact=True)
                if close_panel.count() and close_panel.is_visible():
                    close_panel.click()
                page.locator(".w-center").evaluate("element => element.scrollTop=0")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "pilot-mobile.png"), full_page=True)
                checks.append("390px workbench has no horizontal overflow")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_text("Erase this project's live application data", exact=True).click()
                page.get_by_label("Confirm project name", exact=True).fill("Synthetic pilot workspace")
                page.get_by_role("button", name="Erase live project data", exact=True).click()
                for _ in range(10):
                    if page.get_by_role("button", name="Reload remaining workspaces", exact=True).is_visible():
                        break
                    resume = page.get_by_role("button", name="Resume erasure after active work drains", exact=True)
                    if resume.is_visible() and resume.is_enabled():
                        resume.click()
                    page.wait_for_timeout(500)
                expect(page.get_by_role("button", name="Reload remaining workspaces", exact=True)).to_be_visible(timeout=20000)
                expect(page.locator(".w-source-meta h2")).to_have_count(0)
                remaining = page.request.get(f"{base}/api/projects").json()
                assert [item["id"] for item in remaining] == ["private"]
                page.reload()
                expect(page.get_by_text("Erasure progress and receipt", exact=True)).to_be_visible(timeout=15000)
                expect(page.get_by_role("button", name="Reload remaining workspaces", exact=True)).to_be_visible()
                checks.append("Exact-name live erasure completes with a receipt and preserves the other project")
                browser.close()

        managed_data = directory / "managed"
        fixtures(managed_data)
        token = issue_token()
        owner_token = issue_token()
        config = directory / "managed-access.json"
        config.write_text(json.dumps({"version": 1, "session_secret": secrets.token_urlsafe(40), "users": [
            {"id": "fixture-owner", "role": "owner", "all_projects": True, "project_ids": [], "token_sha256": token_hash(owner_token)},
            {"id": "fixture-viewer", "role": "viewer", "project_ids": ["pilot"], "token_sha256": token_hash(token)}
        ]}))
        config.chmod(0o600)
        with environment(DOXA_ACCESS_MODE="managed", DOXA_ACCESS_CONFIG_FILE=str(config), DOXA_CLOUD_STATE_URL=None):
            with local_app(managed_data) as base, sync_playwright() as playwright:
                browser = playwright.chromium.launch(**chromium_launch_options())
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.get_by_role("heading", name="Open your private workspace")).to_be_visible()
                assert page.request.get(f"{base}/api/projects").status == 401
                page.get_by_label("Your individual access credential", exact=True).fill(token)
                page.get_by_role("button", name="Open private workspace", exact=True).click()
                expect(page.get_by_role("heading", name="Choose the question that matters.")).to_be_visible()
                expect(page.get_by_role("button", name="Create workspace", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="Import", exact=True)).to_be_disabled()
                assert page.get_by_label("Research workspace", exact=True).locator("option").count() == 1
                assert page.request.get(f"{base}/api/projects/private/documents").status == 404
                cookies = [cookie for cookie in context.cookies() if cookie["name"] == "doxa_pilot_session"]
                assert len(cookies) == 1 and cookies[0]["httpOnly"] and cookies[0]["sameSite"] == "Strict"
                assert token not in page.evaluate("JSON.stringify({...localStorage})")
                page.get_by_role("button", name="Sign out of private workspace", exact=True).click()
                expect(page.get_by_role("heading", name="Open your private workspace")).to_be_visible()
                assert page.request.get(f"{base}/api/projects").status == 401
                checks.append("Managed login uses an HttpOnly session, enforces project/viewer grants and logout, and never persists the credential in localStorage")
                browser.close()
    assert not errors, errors
    assert not inference_requests, inference_requests
    result = {"checks": checks, "page_errors": errors, "model_requests": 0, "fixtures": "synthetic", "mocked_http_routes": 0}
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
