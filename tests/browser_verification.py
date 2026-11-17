"""Focused verifier UI contract check. Every verifier request is mocked.

Synthetic fixtures check consent, budgets, eligible primary selection, separate
counts and bounded comparison rendering. No remote inference is requested.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from playwright.sync_api import sync_playwright
from browser_support import chromium_launch_options, local_app
from browser_v3 import fixtures
from test_workspace import seed_run
from doxa.store import Store


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-verification-v3"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-verifier-ui-"))
    project, _ = fixtures(directory)
    store = Store(directory)
    store.init()
    for identity, version, status in [
        ("legacy-ui-only", "1.0", "completed"),
        ("pending-ui-only", "3.0", "running"),
    ]:
        seed_run(store, identity, project, version=version, status=status)
        store.execute("UPDATE runs SET created_at=? WHERE id=?", ["2026-01-01T00:00:00+00:00", identity])
    store.close()
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        runs = context.request.get(f"{base}/api/v3/projects/{project}/runs").json()
        primary = next(
            run
            for run in runs
            if run["status"] == "completed" and run["config"].get("methodology_version") == "3.0"
        )
        original = context.request.post(
            f"{base}/api/v3/projects/{project}/query", data={"run_id": primary["id"]}
        ).json()
        finding = original["findings"]["items"][0]
        verifier_id = "verifier-ui-contract"
        created = {
            "id": verifier_id,
            "status": "queued",
            "created_at": "2026-11-17T00:00:00+00:00",
            "config": {
                "methodology_version": "3.0",
                "verification_of": primary["id"],
                "provider": "cloudflare",
                "model": "@cf/openai/gpt-oss-120b",
                "budget_usd": 1.2,
            },
            "progress": {"blocks_done": 0, "blocks_total": 2},
            "usage": {},
        }
        comparison = {
            "primary_run_id": primary["id"],
            "verifier_run_id": verifier_id,
            "primary_run_profile": {"model": "compact-test", "status": "completed"},
            "verifier_run_profile": {"model": "@cf/openai/gpt-oss-120b", "status": "running"},
            "selected_units": 2,
            "verifier_units_present": 2,
            "verifier_units_accepted": 1,
            "verifier_units_missing": 0,
            "comparison_available": True,
            "comparison_complete_on_selected_units": False,
            "comparable_settings": True,
            "comparability_notices": [],
            "counts": {"paired_assessments": 2, "any_mismatches": 1},
            "selection_counts": {
                "indicator_flagged_units": 1,
                "sampled_other_units": 1,
                "other_sample_fraction_actual": 0.5,
            },
            "disagreements": [
                {
                    "primary_assessment_id": finding["id"],
                    "verifier_assessment_id": "mock-verifier-assessment",
                    "document_id": finding["document_id"],
                    "unit_id": "synthetic-unit",
                    "lens_id": finding["lens_id"],
                    "criterion_id": finding["criterion_id"],
                    "criterion_kind": finding["criterion_kind"],
                    "differences": {
                        "result": {"primary": "meets_criterion", "verifier": "uncertain"},
                        "attribution": {"primary": "quoted_speaker", "verifier": "reported_speaker"},
                    },
                    "primary": {"evidence_refs": [{"block_id": finding["block_id"]}]},
                    "verifier": {"evidence_refs": []},
                }
            ],
            "disagreement_limit": 200,
            "disagreements_truncated": False,
            "notice": "Literal differences between raw model outputs, not accuracy or error counts. Human review remains separate.",
        }
        page = context.new_page()
        errors = []
        captured = []
        comparison_reads = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def create(route):
            captured.append(route.request.post_data_json)
            route.fulfill(status=201, content_type="application/json", body=json.dumps(created))

        def report(route):
            comparison_reads.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(comparison))

        page.route(f"**/api/v3/projects/{project}/verification", create)
        page.route(f"**/api/v3/projects/{project}/verification/{verifier_id}", report)
        page.route(
            f"**/api/v3/runs/{verifier_id}/status",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "id": verifier_id,
                        "status": "running",
                        "progress": {"blocks_done": 1, "blocks_total": 2},
                        "usage": {},
                    }
                ),
            ),
        )
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-observation-row").first.wait_for()
        initial_count = page.locator(".w-metric").nth(3).locator("strong").inner_text()
        page.get_by_role("button", name=re.compile("Processing (record|sources)")).click()
        page.get_by_text("Independent model verification", exact=True).click()
        select = page.get_by_label("Verification primary run", exact=True)
        assert select.input_value() == primary["id"]
        assert select.locator("option").count() == 2
        assert not select.locator('option[value="legacy-ui-only"]').count()
        assert not select.locator('option[value="pending-ui-only"]').count()
        start = page.get_by_role("button", name="Start independent verification", exact=True)
        assert start.is_disabled() and not captured and not comparison_reads
        page.get_by_label("Verification budget in USD", exact=True).fill("2.5")
        page.get_by_label("Allow verification source text to Cloudflare", exact=False).check()
        assert start.is_disabled() and not captured
        page.get_by_label("Verification budget in USD", exact=True).fill("1.2")
        start.click()
        page.get_by_role("status").filter(has_text="Independent verifier started").wait_for()
        assert captured == [{"run_id": primary["id"], "allow_remote_text": True, "budget_usd": 1.2}], captured
        assert not comparison_reads
        assert page.get_by_label("Analysis snapshot", exact=True).input_value() == primary["id"]
        assert page.locator(".w-metric").nth(3).locator("strong").inner_text() == initial_count
        page.get_by_role("button", name="Inspect saved comparison", exact=True).click()
        page.get_by_role("region", name="Independent verification comparison", exact=True).wait_for()
        assert len(comparison_reads) == 1
        page.get_by_text("Selected units incomplete", exact=True).wait_for()
        assert page.locator(".w-verification-counts dd").all_text_contents() == ["2", "1 / 2", "2", "1"]
        assert page.locator(".w-verification-differences>details").count() == 1
        page.locator(".w-verification-differences>details>summary").click()
        assert "uncertain" in page.locator(".w-verification-differences table").inner_text()
        assert "not accuracy" in page.locator(".w-verification-report").inner_text()
        assert page.locator(".w-metric").nth(3).locator("strong").inner_text() == initial_count
        page.screenshot(path=str(output / "synthetic-verification-panel.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "synthetic-verification-mobile.png"), full_page=True)
        page.get_by_role("button", name="Open source context", exact=True).click()
        page.locator("dialog .w-source-meta h2").wait_for()
        assert page.locator("dialog .w-source-passage").inner_text()
        assert not errors, errors
        browser.close()
    result = {
        "fixture_notice": "Synthetic comparison UI contract; verifier POST/GET mocked, no paid inference.",
        "eligible_primary_runs": 1,
        "rejected_primary_statuses": ["legacy", "running"],
        "explicit_consent": True,
        "budget_cap_usd": 2,
        "posted": captured[0],
        "comparison_reads": len(comparison_reads),
        "primary_count_preserved": initial_count,
        "comparison_separate": True,
        "partial_selection_visible": True,
        "source_opened": True,
        "mobile_overflow": False,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
