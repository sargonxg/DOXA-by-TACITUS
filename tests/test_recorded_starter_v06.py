"""Real frozen examples: offline provenance checks, not interpretation accuracy."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from doxa.recorded_starter import DATA_DIRECTORY, canonical_hash, load_recorded_example, recorded_artifact, register_recorded_api, validate_example

ROOT = Path(__file__).resolve().parents[1]


def test_pack_preserves_twenty_licensed_development_sources_and_all_recorded_outcomes(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("example used network"))
    monkeypatch.setattr("doxa.store.Store.init", lambda *args, **kwargs: pytest.fail("example initialized mutable Store"))
    example = load_recorded_example()
    counts = validate_example(example)
    assert counts == {"documents": 20, "assessments": 645, "exact_references": 11280,
                      "outcomes": {"does_not_meet": 359, "meets_criterion": 112, "not_applicable": 172, "uncertain": 2},
                      "unit_states": {"accepted": 33, "partial": 1}}
    assert example["parent"]["status"] == "partial" and example["parent"]["counts"]["documents"] == 5000
    assert example["selection"]["years"] == [str(year) for year in range(2020,2026)]
    assert example["selection"]["selection_uses_recorded_outcomes"]
    assert example["fresh_inference_requests"] == 0 and not example["independent_human_reference_labels"]
    assert example["interpretation_accuracy"] is None and not example["mutable_workspace_installed"]
    assert len({document["metadata"]["story_group_id"] for document in example["documents"]}) == 20
    assert all(document["metadata"]["split"] == "development" and document["license"] == "CC-BY-3.0" for document in example["documents"])
    assert any(assessment["criterion_kind"] == "indicator" and assessment["result"] == "meets_criterion" for record in example["records"] for assessment in record["assessments"])


def test_pack_excludes_canonical_and_existing_evaluation_holdouts():
    example = load_recorded_example()
    with (ROOT / "corpus/news/holdout.jsonl").open() as stream:
        holdouts = [json.loads(line) for line in stream]
    excluded = {row["id"] for row in holdouts}
    stories = {row["story_group_id"] for row in holdouts}
    scaffold = json.loads((ROOT / "benchmarks/evaluation-v04/scaffold.json").read_text())
    for case in scaffold["cases"]:
        if case["split"] == "heldout":
            excluded.add(case["source"]["source_id"])
            stories.add(case["story_group"])
    assert not excluded.intersection(document["source_id"] for document in example["documents"])
    assert not stories.intersection(document["metadata"]["story_group_id"] for document in example["documents"])


def test_pack_reloads_independently_and_detects_source_quote_and_count_tampering():
    example = load_recorded_example()
    changed = copy.deepcopy(example)
    changed["documents"][0]["blocks"][0]["text"] += " forged"
    with pytest.raises(ValueError, match="hash"):
        validate_example(changed)
    changed = copy.deepcopy(example)
    changed["records"][0]["assessments"][0]["evidence_refs"][0]["quote"] = "invented"
    changed["documents"][0]["archive_record_sha256"] = canonical_hash(changed["records"][0])
    with pytest.raises(ValueError, match="quotation"):
        validate_example(changed)
    changed = copy.deepcopy(example)
    changed["selection"]["counts"]["outcomes"]["uncertain"] = 0
    with pytest.raises(ValueError, match="coverage"):
        validate_example(changed)
    example["parent"]["status"] = "completed"
    assert load_recorded_example()["parent"]["status"] == "partial"


def test_display_credits_and_model_cannot_be_relabelled_without_changing_frozen_evidence():
    example = load_recorded_example()
    changed = copy.deepcopy(example)
    changed["documents"][0]["author"] = "Invented author credit"
    with pytest.raises(ValueError, match="display metadata"):
        validate_example(changed)
    changed = copy.deepcopy(example)
    changed["parent"]["model"] = "different model"
    with pytest.raises(ValueError, match="display model"):
        validate_example(changed)


def test_package_checksum_changes_fail_before_reading_customer_state(tmp_path):
    for name in ("recorded-starter.manifest.json", "recorded-starter.json.gz"):
        shutil.copyfile(DATA_DIRECTORY / name, tmp_path / name)
    path = tmp_path / "recorded-starter.json.gz"
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="compressed checksum"):
        load_recorded_example(tmp_path)


def test_read_only_api_serves_real_example_and_verified_artifacts_without_store(monkeypatch):
    monkeypatch.setattr("doxa.store.Store.init", lambda *args, **kwargs: pytest.fail("example initialized Store"))
    app = FastAPI()
    register_recorded_api(app)
    with TestClient(app) as client:
        result = client.get("/api/system/recorded-example")
        assert result.status_code == 200 and len(result.json()["records"]) == 20
        for format in ("pdf", "xlsx", "html", "json.gz"):
            response = client.get("/api/system/recorded-example/artifacts/" + format)
            path, _, _ = recorded_artifact(format)
            assert response.status_code == 200 and response.content == path.read_bytes()
            assert "attachment" in response.headers["content-disposition"]
            assert response.headers["cache-control"] == "private, no-store"
        assert client.get("/api/system/recorded-example/artifacts/unknown").status_code == 404


def test_pdf_html_appendices_retain_four_outcomes_source_credits_and_no_human_validation():
    reader = PdfReader(DATA_DIRECTORY / "recorded-example.pdf")
    text = "\n".join(page.extract_text() for page in reader.pages)
    for label in ("Criterion met", "Criterion not met", "Not applicable", "Uncertain"):
        assert label in text
    assert "79 missing" in text and "partial" in text
    assert "application/AI-authored" in text and "CC BY 3.0" in text
    assert "645" in text and "No new inference" in text
    links = [annotation.get_object() for page in reader.pages for annotation in page.get("/Annots", [])]
    assert any("globalvoices.org" in str(annotation) for annotation in links)
    html = (DATA_DIRECTORY / "recorded-example.html").read_text()
    assert html.count("<article class='case'>") == 8
    assert "not a representative sample" in html and "not human adjudication" in html


def test_xlsx_preserves_every_original_record_and_formula_safety():
    example = load_recorded_example()
    namespace = {"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(DATA_DIRECTORY / "recorded-example.xlsx") as workbook:
        strings = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        shared = ["".join(node.itertext()) for node in strings]
        sheets = [name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        for name in sheets:
            tree = ET.fromstring(workbook.read(name))
            assert not tree.findall(".//s:f",namespace)
        # Sheet6 is the complete JSON-part ledger, including raw parent records.
        tree = ET.fromstring(workbook.read("xl/worksheets/sheet6.xml"))
        values = []
        for row in tree.findall(".//s:sheetData/s:row",namespace)[1:]:
            fields = []
            for cell in row.findall("s:c",namespace):
                value = cell.find("s:v",namespace)
                fields.append(shared[int(value.text)] if cell.get("t")=="s" else int(value.text) if value is not None else None)
            values.append(fields)
        for record in example["records"]:
            parts = sorted([row for row in values if row[0]=="archive_record" and row[1]==record["document_id"]],key=lambda row:row[2])
            restored = json.loads("".join(row[3] for row in parts))
            assert restored == record
        assessments = ET.fromstring(workbook.read("xl/worksheets/sheet4.xml"))
        assert len(assessments.findall(".//s:sheetData/s:row",namespace)) == 646


def test_packaged_publication_bytes_match_public_repository_manifests():
    manifest = json.loads((ROOT / "examples/v06-recorded-example.manifest.json").read_text())
    for entry in manifest["files"]:
        public = ROOT / "examples" / entry["file"]
        packaged = DATA_DIRECTORY / entry["packaged_file"]
        assert public.read_bytes() == packaged.read_bytes()
        assert hashlib.sha256(public.read_bytes()).hexdigest() == entry["sha256"]
    raw = gzip.decompress((DATA_DIRECTORY / "recorded-starter.json.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == manifest["snapshot_sha256"]
