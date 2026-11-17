"""Compatible endpoint pricing is explicit in the analysis interface.

One synthetic source exercises real workspace APIs. Provider readiness and run
creation are mocked, so no model, credential, or paid inference is used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright
from browser_support import ROOT, local_app

sys.path.insert(0, str(ROOT))
from doxa.store import Store, utcnow  # noqa: E402
from test_workspace import seed_document  # noqa: E402


def main() -> None:
    output = Path(os.environ.get("DOXA_BROWSER_OUTPUT", "/tmp/doxa-browser-provider-rates-v3"))
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="doxa-provider-rates-"))
    project = "synthetic-compatible-provider-project"
    store = Store(directory)
    store.init()
    store.execute(
        "INSERT INTO projects(id,name,created_at) VALUES(?,?,?)",
        [project, "Synthetic endpoint pricing workspace", utcnow()],
    )
    store.execute(
        "INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
        ["batch-a", project, "Synthetic source import", utcnow()],
    )
    seed_document(
        store, "endpoint-pricing-source", project, text="A synthetic source for a UI contract check."
    )
    store.close()

    health_requests = []
    run_requests = []
    errors = []
    health_mode = {"value": "remote"}
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def health(route):
            body = route.request.post_data_json
            health_requests.append(body)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "provider": body["provider"],
                        "model": body["model"],
                        "mode": health_mode["value"],
                        "details": "Synthetic provider readiness; no inference attempted.",
                    }
                ),
            )

        def run(route):
            run_requests.append(route.request.post_data_json)
            route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"detail": "Pricing payload captured; no inference requested."}),
            )

        page.route("**/api/v3/models/health", health)
        page.route(f"**/api/v3/projects/{project}/runs", run)
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("1", exact=True).wait_for()
        page.get_by_role("button", name="Analyze", exact=True).click()
        provider = page.get_by_label("Provider", exact=True)
        model = page.get_by_label("Model", exact=True)
        input_price = page.get_by_label("Input price · USD per million tokens", exact=True)
        output_price = page.get_by_label("Output price · USD per million tokens", exact=True)
        start = page.get_by_role("button", name="Start matching-source analysis", exact=True)
        check = page.get_by_role("button", name="Check provider", exact=True)
        assert input_price.count() == output_price.count() == 0, "Cloudflare retains catalogue pricing"
        expect(start).to_be_enabled()

        provider.select_option("openai")
        model.fill("synthetic-compatible-model")
        expect(input_price).to_have_value("")
        expect(output_price).to_have_value("")
        expect(start).to_be_disabled()
        with page.expect_response(lambda response: response.url.endswith("/models/health")):
            check.click()
        page.get_by_text("Provider is ready.", exact=False).wait_for()
        expect(start).to_be_disabled()
        assert "input_price_per_million" not in health_requests[-1]
        assert "output_price_per_million" not in health_requests[-1]
        page.get_by_label("Allow selected source text", exact=False).check()
        input_price.fill("0.1")
        expect(start).to_be_disabled()
        output_price.fill("-1")
        expect(start).to_be_disabled()
        output_price.fill("1e309")
        expect(start).to_be_disabled()
        output_price.fill("0.3")
        expect(start).to_be_enabled()
        with page.expect_response(lambda response: response.url.endswith("/models/health")):
            check.click()
        page.get_by_text("Provider is ready.", exact=False).wait_for()
        assert health_requests[-1]["input_price_per_million"] == 0.1
        assert health_requests[-1]["output_price_per_million"] == 0.3
        start.click()
        page.get_by_role("alert").filter(has_text="Pricing payload captured").wait_for()
        assert len(run_requests) == 1
        assert run_requests[-1]["provider"] == "openai"
        assert run_requests[-1]["model"] == "synthetic-compatible-model"
        assert run_requests[-1]["input_price_per_million"] == 0.1
        assert run_requests[-1]["output_price_per_million"] == 0.3

        model.fill("replacement-compatible-model")
        expect(input_price).to_have_value("")
        expect(output_price).to_have_value("")
        expect(start).to_be_disabled()
        expect(page.get_by_text("Provider is ready.", exact=False)).to_have_count(0)
        input_price.fill("0.1")
        output_price.fill("0.3")
        provider.select_option("runpod")
        model.fill("synthetic-runpod-model")
        expect(input_price).to_have_value("")
        expect(output_price).to_have_value("")
        expect(start).to_be_disabled()
        runpod_copy = page.locator("dialog").inner_text().lower()
        for term in ("initialization", "execution", "idle", "storage"):
            assert term in runpod_copy
        input_price.fill("0.1")
        output_price.fill("0.3")
        with page.expect_response(lambda response: response.url.endswith("/models/health")):
            check.click()
        page.get_by_text("Provider is ready.", exact=False).wait_for()
        assert health_requests[-1]["provider"] == "runpod"
        assert health_requests[-1]["input_price_per_million"] == 0.1
        assert health_requests[-1]["output_price_per_million"] == 0.3
        with page.expect_response(lambda response: response.url.endswith(f"/projects/{project}/runs")):
            start.click()
        assert len(run_requests) == 2 and run_requests[-1]["provider"] == "runpod"
        assert run_requests[-1]["input_price_per_million"] == 0.1
        assert run_requests[-1]["output_price_per_million"] == 0.3

        provider.select_option("openai")
        model.fill("synthetic-server-local-model")
        expect(start).to_be_disabled()
        health_mode["value"] = "local"
        with page.expect_response(lambda response: response.url.endswith("/models/health")):
            check.click()
        page.get_by_text("Provider is ready.", exact=False).wait_for()
        expect(start).to_be_enabled()
        expect(input_price).to_have_value("")
        expect(output_price).to_have_value("")
        with page.expect_response(lambda response: response.url.endswith(f"/projects/{project}/runs")):
            start.click()
        assert len(run_requests) == 3
        assert run_requests[-1]["model"] == "synthetic-server-local-model"
        assert "input_price_per_million" not in run_requests[-1]
        assert "output_price_per_million" not in run_requests[-1]
        assert "inference_mode" not in run_requests[-1], "The browser cannot declare a remote endpoint local"
        page.screenshot(path=str(output / "synthetic-compatible-pricing.png"), full_page=True)
        provider.select_option("ollama")
        expect(model).to_have_value("qwen3.5:4b")
        assert input_price.count() == output_price.count() == 0
        expect(start).to_be_enabled()
        assert not errors, errors
        browser.close()

    result = {
        "fixture_notice": "Synthetic source, real workspace API; all readiness/run requests mocked, no inference.",
        "remote_rates_required_before_start": True,
        "invalid_negative_and_nonfinite_rates_blocked": True,
        "provider_and_model_changes_clear_rates": True,
        "server_confirmed_local_mode_allows_omitted_token_rates": True,
        "cloudflare_and_ollama_pricing_controls_unchanged": True,
        "health_requests": health_requests,
        "captured_run_requests": run_requests,
        "page_errors": errors,
    }
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
