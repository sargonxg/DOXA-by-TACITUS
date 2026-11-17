"""Offline casebook evidence contracts; fixtures are not accuracy evidence."""
from copy import deepcopy
from collections import namedtuple
import csv
import gzip
import hashlib
from html import escape
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from fastapi import HTTPException
import pytest

from doxa.casebook_reports import render_casebook
from doxa.casebooks import create_casebook, get_casebook, pin_case
from doxa.contextual import build_units
from doxa.recorded_export import compact
from doxa.report_jobs import ReportJobs
from doxa.store import utcnow
from doxa.workspace import Workspace, WorkspaceQuery
from test_casebooks import prepared as prepared, store as store


RESULTS = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
Cell = namedtuple("Cell", "value data_type")


def worksheet_rows(path, name):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = next(node for node in workbook.findall("s:sheets/s:sheet", namespace) if node.attrib["name"] == name)
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(node.attrib["Target"] for node in relationships if node.attrib["Id"] == sheet.attrib[relationship])
        member = target.lstrip("/") if target.startswith("/") else "xl/" + target
        result = []
        for row in ET.fromstring(archive.read(member)).findall("s:sheetData/s:row", namespace):
            values = {}
            for node in row.findall("s:c", namespace):
                column = 0
                for character in re.match(r"[A-Z]+", node.attrib["r"]).group():
                    column = column * 26 + ord(character) - 64
                kind = node.attrib.get("t", "n")
                if kind == "inlineStr":
                    value = "".join(node.find("s:is", namespace).itertext())
                    kind = "s"
                else:
                    text = node.findtext("s:v", default="", namespaces=namespace)
                    value = shared[int(text)] if kind == "s" else (int(text) if text.isdigit() else text)
                values[column - 1] = Cell(value, kind)
            result.append([values.get(index, Cell(None, "n")) for index in range(max(values, default=-1) + 1)])
        return result


@pytest.fixture
def balanced_book(prepared):
    store, query = prepared
    for identity in ("a", "b"):
        metadata = {"attribution": "Original story by Fixture Author; published by Fixture Publisher.",
                    "translators": [{"name": "Fixture Translation Credit"}],
                    "license_url": "https://example.invalid/fixture-license",
                    "rights_notes": "Synthetic source used to verify publication attribution."}
        store.execute("UPDATE documents SET source_url=?,license=?,metadata_json=? WHERE id=?",
                      ["https://example.invalid/source-" + identity, "CC-BY-3.0", json.dumps(metadata), identity])
        store.execute("INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES('run',?,?) ON CONFLICT(run_id,document_id) DO NOTHING",
                      [identity, identity + "-hash"])
    row = store.one("SELECT payload_json FROM v3_assessments WHERE id='case-0'")
    original = json.loads(row["payload_json"])
    original.update(speaker="unknown", attribution="unknown", speech_role="unknown")
    overlay = {"actor_id": "synthetic-reviewer", "resolved": {"result": "does_not_meet", "explanation": "Captured reviewer reading, distinct from the original model."}}
    store.execute("UPDATE v3_assessments SET result='does_not_meet',review_status='revised',payload_json=?,review_json=? WHERE id='case-0'",
                  [json.dumps(original), json.dumps(overlay)])
    store.execute("INSERT INTO v3_review_events(id,project_id,assessment_id,actor_kind,actor_id,adjudicated,corrections_json,resolved_json,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ["review-case-0", "p", "case-0", "human", "synthetic-reviewer", 1,
                   json.dumps(overlay["resolved"]), json.dumps(overlay["resolved"]), "Synthetic review provenance", utcnow()])
    Workspace(store).touch("p")
    query = Workspace(store).create("p", WorkspaceQuery(run_id="run", results=["meets_criterion"], duplicate_policy="sources"))["query_id"]
    book = create_casebook(store, "p", query, "A balanced frozen casebook", "Illustrative operator selection, not a representative quality sample.")
    for index in range(4):
        book = pin_case(store, book["id"], "a", "case-" + str(index))
    scope = store.get_json(book["scope_key"])
    source = next(row for row in scope["documents"] if row["id"] == "b")
    blocks = store.get_json(source["blocks_key"])
    unit = build_units({**source, "blocks": blocks}, max_unit_chars=4000)[0]
    store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  ["run", "b", unit["id"], "partial", 17, json.dumps({"failed_criteria": ["ihra/collective_responsibility"]}), utcnow()])
    book = pin_case(store, book["id"], "b", unit_id=unit["id"])
    first = book["cases"][0]
    store.execute("UPDATE v4_casebook_cases SET selection_rationale=?,editorial_note=? WHERE id=?",
                  ["Operator chose an attribution example.", "Editorial guidance is separate from the model and review.", first["id"]])
    return store, query, get_casebook(store, book["id"])


def check_artifacts(artifacts):
    for item in artifacts:
        path = Path(item["path"])
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    by_kind = {item["format"]: Path(item["path"]) for item in artifacts}
    manifest = json.loads(by_kind["manifest"].read_text())
    assert manifest["inference_requests_made"] == 0 and manifest["interpretation_accuracy"] is None
    for entry in manifest["files"]:
        path = by_kind[entry["format"]]
        assert path.name == entry["filename"] and path.stat().st_size == entry["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    return by_kind, manifest


def test_casebook_exports_name_frozen_analysis_method_without_connection_config(balanced_book, tmp_path):
    _store, _query, book = balanced_book
    book["method_provenance"].update(question="A frozen research question", persona="A declared reviewer perspective")
    by_kind, manifest = check_artifacts(render_casebook(book, tmp_path / "method", "html", "briefing"))
    html = by_kind["html"].read_text()
    assert "Frozen analysis provenance" in html and "mock-fixed" in html and "fixture-1" in html
    assert "A frozen research question" in html and "A declared reviewer perspective" in html
    assert manifest["method_provenance"] == book["method_provenance"]
    assert manifest["method_provenance"]["run_id"] == "run"
    assert "base_url" not in manifest["method_provenance"]


def test_html_preserves_all_original_outcomes_unknowns_missing_detail_and_unresolved(balanced_book, tmp_path):
    store, _, book = balanced_book
    original_copy = compact(book)
    artifacts = render_casebook(book, tmp_path, "html", "briefing")
    paths, manifest = check_artifacts(artifacts)
    html = paths["html"].read_text()
    assert manifest["cases"] == 5 and manifest["casebook_revision"] == book["revision"]
    for result in RESULTS:
        assert result + ": 1" in html
    assert "unresolved: 1" in html
    sections = html.split('<section class="case">')[1:]
    assert len(sections) == 5
    assert "<dt>Original model result</dt><dd>meets_criterion</dd>" in sections[0]
    assert "<dt>Attribution</dt><dd>unknown</dd>" in sections[0]
    assert "Captured review overlay: does_not_meet" in sections[0]
    assert "Captured reviewer reading, distinct from the original model." in sections[0]
    assert "Editorial guidance is separate from the model and review." in sections[0]
    assert "Original model output above is preserved separately" in sections[0]
    assert "<dt>Model explanation</dt><dd>Not recorded</dd>" in sections[1]
    assert book["cases"][1]["frozen_snapshot"]["assessment"]["detail_supplied"] is False
    assert "Unresolved source unit" in sections[4] and "no assessment exists" in sections[4]
    assert "Missing decisions are unresolved, never negative findings" in sections[4]
    assert "partial" in sections[4] and "Source statement." in sections[4]
    with gzip.open(paths["snapshot"], "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    assert compact(snapshot) == original_copy == compact(book)
    first = snapshot["cases"][0]["frozen_snapshot"]
    raw_payload = store.one("SELECT payload_json FROM v3_assessments WHERE id='case-0'")["payload_json"]
    assert first["original_assessment_payload"] == json.loads(raw_payload)
    assert first["original_payload_sha256"] == hashlib.sha256(raw_payload.encode()).hexdigest()
    assert first["original_assessment_payload"]["result"] == "meets_criterion"
    assert first["review_overlay"]["resolved"]["result"] == "does_not_meet"
    assert first["editorial_note"] == "Editorial guidance is separate from the model and review."
    assert first["review_events"][0]["actor_id"] == "synthetic-reviewer"


def test_html_exact_zero_offset_anchors_and_complete_source_credits(balanced_book, tmp_path):
    _, _, book = balanced_book
    paths, _ = check_artifacts(render_casebook(book, tmp_path, "html", "research"))
    html = paths["html"].read_text()
    assert "Exact source anchor: b1 · characters 0–17" in html
    assert "https://example.invalid/source-a" in html and "CC-BY-3.0" in html
    assert "Fixture Translation Credit" in html
    assert "Original story by Fixture Author; published by Fixture Publisher." in html
    assert "https://example.invalid/fixture-license" in html
    for case in book["cases"]:
        assert case["capture_sha256"] in html


def test_html_escapes_untrusted_source_model_review_and_editorial_fields(balanced_book, tmp_path):
    _, _, original = balanced_book
    book = deepcopy(original)
    attack = '<img src="https://example.invalid/tracker" onerror="alert(1)"><script>alert(2)</script>'
    book["title"] = attack
    frozen = book["cases"][0]["frozen_snapshot"]
    frozen["source"]["title"] = attack
    frozen["assessment"]["explanation"] = attack
    frozen["review_overlay"]["resolved"]["explanation"] = attack
    frozen["editorial_note"] = attack
    frozen["criterion"]["description"] = attack
    paths, _ = check_artifacts(render_casebook(book, tmp_path, "html", "teaching"))
    html = paths["html"].read_text()
    assert escape(attack) in html and attack not in html
    assert not re.search(r'<(?:img|script|iframe)\b', html)
    assert 'src="https://example.invalid/tracker"' not in html


def test_xlsx_raw_capture_chunks_round_trip_formula_prefix_and_original_review_separation(balanced_book, tmp_path):
    _, _, original = balanced_book
    book = deepcopy(original)
    frozen = book["cases"][0]["frozen_snapshot"]
    marker = "=SUM(12345,67890)"
    frozen["zz_raw_capture_probe"] = marker
    position = compact(frozen).index(marker)
    frozen["zz_raw_capture_probe"] = "P" * (-position % 30000) + marker + "Z" * 40000
    assert compact(frozen).index(marker) % 30000 == 0
    frozen["source"]["title"] = "=HYPERLINK(\"https://example.invalid\")"
    # The title length changes the chunk alignment; recalculate it before render.
    frozen["zz_raw_capture_probe"] = marker
    position = compact(frozen).index(marker)
    frozen["zz_raw_capture_probe"] = "P" * (-position % 30000) + marker + "Z" * 40000
    assert compact(frozen).index(marker) % 30000 == 0
    expected = {case["id"]: compact(case["frozen_snapshot"]) for case in book["cases"]}
    assert len(expected[book["cases"][0]["id"]]) > 32767
    paths, _ = check_artifacts(render_casebook(book, tmp_path, "xlsx", "briefing"))
    with zipfile.ZipFile(paths["xlsx"]) as archive:
        assert archive.testzip() is None
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for name in archive.namelist():
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                assert not ET.fromstring(archive.read(name)).findall(".//s:f", namespace)
    chunks = {}
    formula_prefixes = 0
    for identity, part, cell in worksheet_rows(paths["xlsx"], "Full frozen captures")[1:]:
        assert cell.data_type == "s"
        chunks.setdefault(identity.value, []).append((part.value, cell.value))
        formula_prefixes += cell.value.startswith(marker)
    assert formula_prefixes >= 1
    assert {identity: "".join(value for _, value in sorted(parts)) for identity, parts in chunks.items()} == expected
    captured = json.loads(expected[book["cases"][0]["id"]])
    assert captured["original_assessment_payload"]["result"] == "meets_criterion"
    assert captured["review_overlay"]["resolved"]["result"] == "does_not_meet"
    credits = worksheet_rows(paths["xlsx"], "Source credits")
    assert any("Fixture Translation Credit" in str(cell.value) for row in credits for cell in row)


def test_render_pdf_and_jsonl_are_offline_and_keep_frozen_snapshot(balanced_book, tmp_path, monkeypatch):
    _, _, book = balanced_book
    def forbidden_network(*_args, **_kwargs):
        pytest.fail("Casebook publication must not perform inference or network access")
    monkeypatch.setattr("socket.create_connection", forbidden_network)
    pdf, _ = check_artifacts(render_casebook(book, tmp_path / "pdf", "pdf", "briefing"))
    assert pdf["pdf"].read_bytes().startswith(b"%PDF-")
    data, _ = check_artifacts(render_casebook(book, tmp_path / "jsonl", "jsonl", "research"))
    with gzip.open(data["jsonl"], "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    assert records[0]["record_type"] == "manifest"
    assert [row["id"] for row in records[1:]] == [case["id"] for case in book["cases"]]
    for actual, case in zip(records[1:], book["cases"]):
        assert actual["frozen_snapshot"] == case["frozen_snapshot"]


@pytest.mark.parametrize("format", ["xlsx", "csv"])
def test_compact_tables_label_original_review_and_projected_results_separately(balanced_book, tmp_path, format):
    _, _, book = balanced_book
    paths, _ = check_artifacts(render_casebook(book, tmp_path, format, "briefing"))
    if format == "xlsx":
        rows = worksheet_rows(paths["xlsx"], "Cases")
        headers = [cell.value for cell in rows[0]]
        records = [dict(zip(headers, [cell.value for cell in row])) for row in rows[1:]]
    else:
        with gzip.open(paths["csv"], "rt", encoding="utf-8", newline="") as stream:
            records = list(csv.DictReader(stream))
        headers = list(records[0])
    assert {"original_result", "review_result", "projected_result"}.issubset(headers)
    first = records[0]
    assert first["original_result"] == "meets_criterion"
    assert first["review_result"] == first["projected_result"] == "does_not_meet"
    assert {record["original_result"] for record in records[:4]} == RESULTS
    assert records[-1]["kind"] == "unresolved"
    assert records[-1]["original_result"] in {None, "", "unresolved"}
    assert records[-1]["review_result"] in {None, ""}


def test_pinned_casebook_can_queue_after_workspace_query_becomes_stale(balanced_book):
    store, query, book = balanced_book
    Workspace(store).touch("p")
    with pytest.raises(HTTPException) as error:
        Workspace(store).require_query(query)
    assert error.value.status_code == 409
    job = ReportJobs(store).create("p", {"casebook_id": book["id"], "publication_type": "casebook", "format": "html", "profile": "briefing"})
    assert job["status"] == "queued" and job["casebook_id"] == book["id"]
    assert job["casebook_revision"] == book["revision"]
    row = store.one("SELECT config_key FROM report_jobs WHERE id=?", [job["id"]])
    captured = store.get_json(row["config_key"])["casebook"]
    store.execute("UPDATE v4_casebook_cases SET editorial_note='Later editorial change' WHERE id=?", [book["cases"][0]["id"]])
    assert captured["cases"][0]["frozen_snapshot"]["editorial_note"] == "Editorial guidance is separate from the model and review."
    assert store.get_json(row["config_key"])["casebook"] == captured
