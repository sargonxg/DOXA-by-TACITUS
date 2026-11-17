"""Keyboard and accessible-name checks for bounded v0.4 workspace controls."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from playwright.sync_api import expect, sync_playwright
from browser_support import chromium_launch_options, local_app
from browser_v3 import fixtures


def main():
    directory = Path(tempfile.mkdtemp(prefix="doxa-a11y-v04-"))
    _, project = fixtures(directory)
    with local_app(directory) as base, sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{base}/?workspace={project}", wait_until="domcontentloaded")
        page.locator(".w-metric").first.get_by_text("5,000", exact=True).wait_for()
        sources = page.get_by_role("list", name="Matching source documents", exact=True)
        sources.focus()
        page.keyboard.press("End")
        expect(sources.locator('[aria-posinset="50"] button').first).to_be_focused()
        expect(sources.locator('[aria-posinset="50"]')).to_have_attribute("aria-setsize", "50")
        page.keyboard.press("ArrowUp")
        expect(sources.locator('[aria-posinset="49"] button').first).to_be_focused()
        page.keyboard.press("Home")
        expect(sources.locator('[aria-posinset="1"] button').first).to_be_focused()
        assert sources.get_by_role("listitem").count() < 20
        signals = page.get_by_role("tab", name="Signals", exact=True)
        signals.focus()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("tab", name="Trajectory", exact=True)).to_be_focused()
        expect(page.get_by_role("tab", name="Trajectory", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        page.keyboard.press("Home")
        expect(signals).to_be_focused()
        page.get_by_text("Read timeline as a data table", exact=False).click()
        expect(page.locator(".w-chart-table table")).to_be_visible()
        opener = page.get_by_role("button", name="Export query report", exact=True)
        opener.click()
        expect(page.get_by_role("dialog", name="Export this research scope", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(opener).to_be_focused()
        assert not errors, errors
        browser.close()
    result = {
        "fixture_notice": "Synthetic 5,000-source UI scale fixture; mocked provider used only to seed a separate fixture. No remote inference.",
        "virtual_page_end_home_arrow_focus": True,
        "virtual_row_position_and_size": True,
        "virtual_dom_bounded": True,
        "tab_arrow_home_keyboard": True,
        "timeline_data_table": True,
        "named_dialog_escape_focus_return": True,
        "page_errors": errors,
    }
    Path("/tmp/doxa-accessibility-v04.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
