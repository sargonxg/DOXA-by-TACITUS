"""Annotation guides retain real evidence boundaries without claiming human review."""
import copy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from doxa.store import Store, utcnow
from scripts.build_walkthrough import AUTHORSHIP, audit_snapshot, build, digest, freeze_run, render_html
from test_workspace import seed_assessment, seed_document, seed_run


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path / "workspace")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Synthetic test fixture',?)", [utcnow()])
    text = 'A witness said "a disputed claim". The author rejects that position.'
    seed_document(store, "source", text=text, metadata={"attribution": "Synthetic test source; not public benchmark evidence."})
    seed_run(store, "run", sources=[{"id": "source", "sha256": "source-hash"}])
    original = seed_assessment(store, "assessment", "source", text=text)
    original.update({"evidence_refs": [{"block_id": "b1", "start": 0, "end": len(text), "quote": text, "document_id": "source"}]})
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id='assessment'", [json.dumps(original)])
    yield store, original
    store.close()


def test_read_only_freeze_preserves_raw_assessment_and_separate_review_overlay(recorded):
    store, original = recorded
    overlay = {"actor_kind": "human", "resolved": {**original, "result": "does_not_meet"}}
    store.execute("UPDATE v3_assessments SET review_json=?,review_status='revised' WHERE id='assessment'", [json.dumps(overlay)])
    before = store.one("SELECT COUNT(*) n FROM settings")["n"]
    frozen = freeze_run(store.root, "run")
    item = frozen["assessments"][0]
    assert item["explanation"] == original["explanation"] and item["result"] == original["result"]
    assert item["recorded_review_overlay"] == overlay
    assert item["quote"] == original["quote"]
    assert frozen["source_anchor_audit"]["valid"] is True
    assert frozen["independent_human_labels"] is False
    assert frozen["commentary_authorship"] == AUTHORSHIP
    assert store.one("SELECT COUNT(*) n FROM settings")["n"] == before


def test_unfinished_capture_requires_explicit_flag_and_preserves_current_status(recorded):
    store, _ = recorded
    store.execute("UPDATE runs SET status='running' WHERE id='run'")
    with pytest.raises(ValueError, match="settle"):
        freeze_run(store.root, "run")
    frozen = freeze_run(store.root, "run", allow_active=True)
    assert frozen["run"]["status"] == "running"
    assert "Status: running" in render_html(frozen, "Synthetic test guide", "news-monitoring")


def test_source_hash_changes_invented_spans_and_snapshot_tampering_fail_closed(recorded):
    store, _ = recorded
    frozen = freeze_run(store.root, "run")
    broken = copy.deepcopy(frozen)
    broken["assessments"][0]["quote"] = "Invented quotation"
    with pytest.raises(ValueError, match="exact source span"):
        audit_snapshot(broken)
    broken = copy.deepcopy(frozen)
    broken["documents"][0]["blocks"][0]["text"] += " Changed source."
    with pytest.raises(ValueError, match="checksum"):
        audit_snapshot(broken)
    frozen["snapshot_sha256"] = digest(frozen)
    frozen["run"]["status"] = "forged-complete"
    with pytest.raises(ValueError, match="snapshot checksum"):
        audit_snapshot(frozen)
    store.execute("UPDATE documents SET sha256='changed' WHERE id='source'")
    with pytest.raises(ValueError, match="frozen source version"):
        freeze_run(store.root, "run")


def test_foreign_assessment_selection_or_cross_source_evidence_is_rejected(recorded):
    store, _ = recorded
    with pytest.raises(ValueError, match="unique noteworthy"):
        freeze_run(store.root, "run", assessment_ids=["foreign"])
    frozen = freeze_run(store.root, "run")
    frozen["assessments"][0]["evidence_refs"][0]["document_id"] = "foreign"
    with pytest.raises(ValueError, match="another source"):
        audit_snapshot(frozen)


def test_annotated_pdf_html_xlsx_keep_quote_commentary_and_clickable_source_separate(recorded, tmp_path):
    from doxa.pdf_tools import open_pdf
    store, original = recorded
    store.execute("UPDATE documents SET source_url='https://example.org/source' WHERE id='source'")
    frozen = freeze_run(store.root, "run")
    manifest = build(frozen, tmp_path / "guide", "Synthetic test guide", "news-monitoring")
    assert {Path(item["file"]).suffix for item in manifest["files"]} == {".pdf", ".html", ".xlsx", ".gz"}
    html = (tmp_path / "guide.html").read_text()
    assert original["explanation"] in html
    assert "Recorded model interpretation" in html and "AI-authored walkthrough commentary" in html
    assert "not independent human adjudication" in html
    assert "https://example.org/source" in html
    assert '<script' not in html
    assert html.count('class="diagram"') == 3
    with open_pdf(tmp_path / "guide.pdf") as pdf:
        text = " ".join(page.get_text() for page in pdf)
        links = [link for page in pdf for link in page.get_links()]
    assert "disputed claim" in text and "recorded model interpretation" in text.casefold()
    assert any(link.get("uri") == "https://example.org/source" for link in links)
    with gzip.open(tmp_path / "guide.snapshot.json.gz", "rt") as source:
        saved = json.load(source)
    assert saved["snapshot_sha256"] == frozen["snapshot_sha256"]
    assert saved["assessments"][0]["quote"] == original["quote"]
    assert audit_snapshot(saved)["valid"]
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(tmp_path / "guide.xlsx") as workbook:
        text = workbook.read("xl/sharedStrings.xml").decode()
        assert "Recorded model reading" in text and "AI note: attribution" in text and "Separate review overlay" in text
        for name in workbook.namelist():
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                assert not ET.fromstring(workbook.read(name)).findall(".//s:f", ns)


def test_added_case_notes_remain_ai_authored_escaped_and_do_not_rewrite_model_output(recorded):
    store, original = recorded
    frozen = freeze_run(store.root, "run")
    frozen["commentary_notes"] = {"assessment": {"what_is_captured": "<script>Not executable</script> Specific AI reading note."}}
    html = render_html(frozen, "An annotated fixture", "news-monitoring")
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert original["explanation"] in html
    assert frozen["assessments"][0]["explanation"] == original["explanation"]
    assert AUTHORSHIP in html
    frozen["commentary_notes"]["assessment"]["human_reviewed"] = True
    with pytest.raises(ValueError, match="Commentary notes"):
        render_html(frozen, "Invalid note fixture", "news-monitoring")


def test_political_framing_label_requires_that_actual_recorded_lens(recorded, tmp_path):
    store, _ = recorded
    frozen = freeze_run(store.root, "run")
    with pytest.raises(ValueError, match="recorded political-framing"):
        build(frozen, tmp_path / "not-political", "A mislabeled fixture", "political-framing")


def test_unknown_provider_cost_and_conservative_budget_spend_are_separate(recorded):
    store, _ = recorded
    store.execute("UPDATE runs SET usage_json=?,spent_usd=? WHERE id='run'", [json.dumps({"cost_usd": None, "estimated": True}), 0.417891])
    frozen = freeze_run(store.root, "run")
    html = render_html(frozen, "Cost interpretation fixture", "news-monitoring")
    assert "Unknown provider invoice cost" in html
    assert "$0.417891 conservatively accounted" in html
    assert "provider inference cost: $0.000000" not in html
    assert frozen["run"]["usage"]["cost_usd"] is None
