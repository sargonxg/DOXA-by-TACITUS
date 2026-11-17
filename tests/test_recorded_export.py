"""Recorded-export fidelity checks; fixtures are synthetic, not accuracy evidence."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest

from doxa.store import Store, utcnow
from scripts.export_recorded_run import export_run


RESULTS = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}


@pytest.fixture
def recorded_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    root = tmp_path / "recorded"
    store = Store(root)
    store.init()
    now = utcnow()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('project','Recorded fixture',?)", [now])
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES('batch','project','Import',?)", [now])
    sources = [{"id": name, "sha256": "a" * 64} for name in ["source-a", "source-b"]]
    config = {"methodology_version": "3.0", "schema_version": "3.0", "provider": "openai",
              "model": "synthetic-fixed", "lens_ids": ["ihra", "jda", "discourse"],
              "question": "Fixture for export fidelity", "persona": "research", "source_snapshot": sources}
    progress = {"units_done": 5, "units_total": 6, "complete": False}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,progress_json,usage_json,model_profile_json,created_at,completed_at,spent_usd) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ["run", "project", "partial", json.dumps(config), json.dumps(progress), json.dumps({"cost_usd": None}),
                   json.dumps({"provider": "openai", "model": "synthetic-fixed", "revision": "fixture-1"}), now, now, 0.04])
    for name in ["source-a", "source-b", "outside-run"]:
        metadata = {"attribution": "Synthetic test source", "license_url": "https://example.invalid/cc0",
                    "custom_source_value": "Retain <metadata> literally", "translators": [{"name": "Fixture Translator"}]}
        store.execute("INSERT INTO documents(id,project_id,batch_id,title,author,document_type,published_at,filename,mime_type,sha256,status,word_count,source_url,license,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [name, "project", "batch", '=HYPERLINK("https://example.invalid") ' + name, "Fixture Author", "news_article",
                       "2024-01-15", name + ".txt", "text/plain", "a" * 64, "ready", 8,
                       "https://example.invalid/" + name, "CC0", "unopened/" + name, now, json.dumps(metadata)])
        store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES('batch',?)", [name])
        if name != "outside-run":
            store.execute("INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES('run',?,?)", [name, "a" * 64])
    for index, (result, kind, lens, criterion) in enumerate([
        ("meets_criterion", "indicator", "ihra", "anti_jewish_generalization"),
        ("does_not_meet", "indicator", "ihra", "anti_jewish_generalization"),
        ("uncertain", "indicator", "ihra", "anti_jewish_generalization"),
        ("not_applicable", "boundary", "jda", "political_expression_boundary"),
        ("meets_criterion", "descriptive", "discourse", "framing"),
    ]):
        identity, unit = f"assessment-{index}", f"unit-{index}"
        text = "A represented claim."
        payload = {"id": identity, "document_id": "source-a", "unit_id": unit, "run_id": "run",
                   "lens_id": lens, "criterion_id": criterion, "criterion_kind": kind, "result": result,
                   "title": "Synthetic persistence assessment", "quote": text, "context": text,
                   "block_id": "block-1", "start": 0, "end": len(text), "speaker": "Represented speaker",
                   "attribution": "quoted_speaker", "speech_role": "attributed_quotation", "speaker_stance": "supports",
                   "document_stance": "opposes", "proposition": "A represented proposition", "explanation": "Raw interpretation.",
                   "evidence_refs": [{"block_id": "block-1", "start": 0, "end": len(text), "quote": text}],
                   "alternative": "Read the surrounding source."}
        overlay = {"resolved": {"explanation": "Review-adjusted interpretation."}} if index == 0 else {}
        store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,review_status,payload_json,review_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [identity, "project", "run", "source-a", unit, lens, criterion, kind, result, "quoted_speaker",
                       "attributed_quotation", "supports", "opposes", "revised" if index == 0 else "unreviewed",
                       json.dumps(payload), json.dumps(overlay), now])
        store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES('run','source-a',?,'accepted',20,'{}',?)", [unit, now])
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES('run','source-b','unprocessed','pending',20,'{}',?)", [now])
    yield store, root
    store.close()


def logical_source_state(root):
    connection = sqlite3.connect((root / "doxa.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {name: connection.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall()
                for name in ["runs", "documents", "v3_assessments", "v3_unit_statuses", "v3_run_sources",
                             "v3_review_events", "settings", "v3_query_snapshots", "reports"]}
    finally:
        connection.close()


def xlsx_values(path):
    """Support both ordinary shared strings and constant-memory inline strings."""
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        values = []
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            sheet = ET.fromstring(archive.read(name))
            assert not sheet.findall(".//s:f", namespace), "Source values must remain literal Excel strings"
            for cell in sheet.findall(".//s:c", namespace):
                if cell.attrib.get("t") == "s":
                    value = cell.find("s:v", namespace)
                    values.append(shared[int(value.text)])
                elif cell.attrib.get("t") == "inlineStr":
                    values.append("".join(cell.find("s:is", namespace).itertext()))
        return values


@pytest.mark.parametrize("status", ["queued", "running", "importing", "paused_budget"])
def test_active_or_unsettled_run_refused_without_creating_artifacts(recorded_workspace, tmp_path, status):
    store, root = recorded_workspace
    store.execute("UPDATE runs SET status=? WHERE id='run'", [status])
    prefix = tmp_path / "public" / "refused"
    before = logical_source_state(root)
    with pytest.raises(ValueError):
        export_run(root, "run", prefix, formats=("html",))
    assert not list(prefix.parent.glob(prefix.name + ".*"))
    assert logical_source_state(root) == before


def test_partial_export_retains_all_results_all_frozen_sources_and_checksums(recorded_workspace, tmp_path):
    _, root = recorded_workspace
    prefix = tmp_path / "public" / "recorded"
    before = logical_source_state(root)
    manifest = export_run(root, "run", prefix, formats=("html", "xlsx"))
    assert logical_source_state(root) == before, "Export must not mutate runs, reviews, queries or reports"
    assert json.loads(Path(str(prefix) + ".manifest.json").read_text()) == manifest
    with gzip.open(Path(str(prefix) + ".snapshot.json.gz"), "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    assert {row["id"] for row in snapshot["documents"]} == {"source-a", "source-b"}
    assert all("blocks" not in row for row in snapshot["documents"])
    assert all(row["metadata"]["custom_source_value"] == "Retain <metadata> literally" for row in snapshot["documents"])
    assert {row["id"] for row in snapshot["findings"]} == {f"assessment-{index}" for index in range(5)}
    assert {row["result"] for row in snapshot["findings"]} == RESULTS
    assert {row["criterion_kind"] for row in snapshot["findings"]} == {"indicator", "boundary", "descriptive"}
    assert set(snapshot["filters"]["results"]) == RESULTS
    assert snapshot["filters"]["duplicate_policy"] == "sources"
    assert snapshot["runs"][0]["status"] == "partial"
    assert snapshot["metrics"]["documents"] == 2
    assert snapshot["metrics"]["coverage_detail"]["units_done"] == 5
    assert snapshot["metrics"]["coverage_detail"]["units_total"] == 6
    positive = next(row for row in snapshot["findings"] if row["id"] == "assessment-0")
    assert positive["explanation"] == "Review-adjusted interpretation."
    assert positive["evidence_refs"][0]["quote"] == "A represented claim."
    assert manifest["counts"]["documents"] == 2 and manifest["counts"]["assessments"] == 5
    typed = Counter((row["criterion_kind"], row["result"]) for row in snapshot["findings"])
    assert {(row["criterion_kind"], row["result"]): row["count"] for row in manifest["typed_counts"]} == typed
    for key, rows in [("source_ids_sha256", snapshot["documents"]), ("assessment_ids_sha256", snapshot["findings"])]:
        expected = hashlib.sha256("".join(row + "\n" for row in sorted(item["id"] for item in rows)).encode()).hexdigest()
        assert manifest[key] == expected
    files = list(manifest["files"].values()) if isinstance(manifest["files"], dict) else manifest["files"]
    for entry in files:
        path = prefix.parent / entry["file"]
        assert path.stat().st_size == entry["bytes"] < 100 * 1024 * 1024
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    values = xlsx_values(Path(str(prefix) + ".xlsx"))
    assert all(identity in values for identity in ["source-a", "source-b", *(f"assessment-{index}" for index in range(5))])
    assert RESULTS.issubset(set(values))
    assert any(value.startswith('=HYPERLINK("https://example.invalid")') for value in values)
    html = Path(str(prefix) + ".html").read_text()
    assert "partial" in html and "5 of 6" in html
    assert "briefing" in html and "25" in html
    assert "Unknown provider invoice" in html or "Provider cost unavailable" in html


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_export_refuses_changed_or_missing_frozen_source(recorded_workspace, tmp_path, change):
    store, root = recorded_workspace
    if change == "changed":
        store.execute("UPDATE documents SET sha256=? WHERE id='source-b'", ["b" * 64])
    else:
        store.execute("DELETE FROM documents WHERE id='source-b'")
    prefix = tmp_path / "public" / "invalid-source"
    with pytest.raises(ValueError):
        export_run(root, "run", prefix, formats=("html",))
    assert not list(prefix.parent.glob(prefix.name + ".*"))


def test_export_size_limit_cleans_incomplete_public_artifacts(recorded_workspace, tmp_path):
    _, root = recorded_workspace
    prefix = tmp_path / "public" / "too-large"
    with pytest.raises(ValueError):
        export_run(root, "run", prefix, formats=("html", "xlsx"), max_file_bytes=1024)
    assert not list(prefix.parent.glob(prefix.name + ".*"))


def test_full_workbook_continues_long_metadata_without_silent_cell_truncation(recorded_workspace, tmp_path):
    store, root = recorded_workspace
    metadata = json.loads(store.one("SELECT metadata_json FROM documents WHERE id='source-b'")["metadata_json"])
    value = "START_LONG_RIGHTS " + "x" * 40000 + " END_LONG_RIGHTS"
    metadata["rights_notes"] = value
    store.execute("UPDATE documents SET metadata_json=? WHERE id='source-b'", [json.dumps(metadata)])
    prefix = tmp_path / "public" / "long-metadata"
    export_run(root, "run", prefix, formats=("xlsx",))
    with gzip.open(Path(str(prefix) + ".snapshot.json.gz"), "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    source = next(row for row in snapshot["documents"] if row["id"] == "source-b")
    assert source["metadata"]["rights_notes"] == value
    values = xlsx_values(Path(str(prefix) + ".xlsx"))
    assert any("START_LONG_RIGHTS" in text for text in values)
    assert any("END_LONG_RIGHTS" in text for text in values)
    assert "1/2" in values and "2/2" in values


def test_grouped_record_order_has_its_own_hash_without_changing_canonical_identity_hash(recorded_workspace, tmp_path):
    store, root = recorded_workspace
    assignments = {
        "assessment-0": ("source-b", "unit-common", "ihra", "anti_jewish_generalization", "indicator"),
        "assessment-1": ("source-a", "unit-z", "ihra", "anti_jewish_generalization", "indicator"),
        "assessment-2": ("source-a", "unit-a", "ihra", "israel_related_context", "indicator"),
        "assessment-3": ("source-a", "unit-a", "ihra", "anti_jewish_generalization", "indicator"),
        "assessment-4": ("source-a", "unit-a", "discourse", "framing", "descriptive"),
    }
    for identity, (document, unit, lens, criterion, kind) in assignments.items():
        payload = json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [identity])["payload_json"])
        payload.update(document_id=document, unit_id=unit, lens_id=lens, criterion_id=criterion, criterion_kind=kind)
        store.execute("UPDATE v3_assessments SET document_id=?,unit_id=?,lens_id=?,criterion_id=?,criterion_kind=?,payload_json=? WHERE id=?",
                      [document, unit, lens, criterion, kind, json.dumps(payload), identity])
    store.execute("DELETE FROM v3_unit_statuses WHERE run_id='run'")
    for document, unit in sorted({(row[0], row[1]) for row in assignments.values()}):
        store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES('run',?,?,'accepted',20,'{}',?)",
                      [document, unit, utcnow()])
    before = logical_source_state(root)
    prefix = tmp_path / "public" / "grouped-records"
    manifest = export_run(root, "run", prefix, formats=("xlsx",))
    assert logical_source_state(root) == before
    with gzip.open(Path(str(prefix) + ".snapshot.json.gz"), "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    order = ["document_id", "unit_id", "lens_id", "criterion_id", "id"]
    findings = snapshot["findings"]
    expected_ids = ["assessment-4", "assessment-3", "assessment-2", "assessment-1", "assessment-0"]
    assert [row["id"] for row in findings] == expected_ids
    assert findings == sorted(findings, key=lambda row: tuple(row[key] for key in order))
    assert manifest["assessment_record_order"] == order
    expected_order_hash = hashlib.sha256("".join(identity + "\n" for identity in expected_ids).encode()).hexdigest()
    canonical_hash = hashlib.sha256("".join(identity + "\n" for identity in sorted(expected_ids)).encode()).hexdigest()
    assert manifest["assessment_record_order_sha256"] == expected_order_hash
    assert manifest["assessment_ids_sha256"] == canonical_hash
    assert expected_order_hash != canonical_hash
    values = xlsx_values(Path(str(prefix) + ".xlsx"))
    assert [value for value in values if value in assignments] == expected_ids
