"""Fidelity of real frozen publications; not interpretation-quality labels."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

from doxa.pdf_tools import open_pdf
import pytest

from doxa.lineage import content_sha256
from scripts.build_v04_publications import audit_atlas, selected_window
from scripts.build_walkthrough import audit_snapshot, freeze_run, render_html, render_workbook
from doxa.lenses import get_lens
from doxa.store import Store, utcnow
from test_workspace import seed_assessment, seed_document, seed_run


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def atlas():
    with gzip.open(ROOT / "examples/v04-case-atlas.snapshot.json.gz", "rt", encoding="utf-8") as stream:
        return json.load(stream)


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def test_fifteen_real_cases_retain_every_outcome_and_a_distinct_missing_decision(atlas):
    audit = audit_atlas(atlas)
    assert audit["valid"] and audit["cases"] == 15
    assert audit["outcomes"] == {"meets_criterion": 9, "uncertain": 2, "not_applicable": 2, "does_not_meet": 1, "unresolved": 1}
    assert audit["checked_evidence_references"] == 112
    assert atlas["independent_human_labels"] is False and atlas["interpretation_accuracy"] is None
    missing = atlas["cases"][-1]
    assert missing["assessment"] is None and missing["unresolved_unit"]["result"] is None
    assert missing["unresolved_unit"]["missing_criterion_keys"] == ["anti-muslim-hostility/discrimination_claim"]


@pytest.mark.parametrize("number", range(15))
def test_real_display_windows_and_model_records_are_exact_and_separately_authored(atlas, number):
    case = atlas["cases"][number]
    source = case["source"]
    window = selected_window(case)
    block = next(block for block in source["blocks"] if block["id"] == window["block_id"])
    assert block["text"][window["start"]:window["end"]] == window["quote"]
    assert source["author"] and source["published_at"] and source["license"] and source["source_url"].startswith("https://")
    assert "Editorial" in window["origin"]
    assert "AI-authored" in case["editorial"]["authorship"]
    assert case["editorial"]["review_status"] == "pending_independent_human_review"
    assert case["request_capture"]["status"] == "not_recorded"
    for item in [case["assessment"], case.get("comparison", {}).get("assessment")]:
        if item is None:
            continue
        assert item["recorded_payload_sha256"] == content_sha256(item["recorded_payload"])
        assert item["result"] == item["recorded_payload"]["result"]
        if item.get("detail_supplied") is False:
            assert item["origin_labels"]["evidence_selection"] == "software_attached_unit_anchors"
            assert item["origin_labels"]["model_interpretation"] == "not_recorded"
            assert item.get("explanation") is None


def test_rubrics_use_original_snapshots_or_hash_verified_compatible_captures(atlas):
    for case in atlas["cases"]:
        if case["assessment"] is None:
            continue
        item, definition = case["assessment"], case["framework"]
        capture = next(capture for capture in atlas["captures"].values() if capture["run"]["id"] == case["run"]["id"])
        snapshots = case["run"]["config"].get("lens_snapshots", [])
        pack = next(pack for pack in capture["rubric_capture"]["packs"] if pack["id"] == item["lens_id"])
        criterion = next(criterion for criterion in pack["criteria"] if criterion["id"] == item["criterion_id"])
        assert criterion == definition["criterion"]
        assert definition["version"] == pack["version"]
        if snapshots:
            assert pack == next(pack for pack in snapshots if pack["id"] == item["lens_id"])
            assert definition["origin"] == "frozen_run_lens_snapshot"
        else:
            assert definition["origin"] == "verified_compatible_registry_capture"
            receipt = capture["rubric_capture"]["compatibility_receipt"]["recorded_receipt"]
            by_id = {pack["id"]: pack for pack in capture["rubric_capture"]["packs"]}
            assert content_sha256([by_id[identity] for identity in receipt["lens_ids"]]) == receipt["components"]["selected_lenses"]
            assert "not an original embedded request" in capture["rubric_capture"]["compatibility_receipt"]["notice"]


def test_research_audit_declares_source_access_partial_coverage_and_absent_human_validation():
    audit = json.loads((ROOT / "docs/v04-criterion-source-audit.json").read_text())
    assert len(audit["criteria"]) == 28
    assert len({row["criterion_key"] for row in audit["criteria"]}) == 28
    assert all(row["independent_human_interpretation_validation"] is False and row["semantic_change_to_v03"] is False for row in audit["criteria"])
    assert all("blocked" in row["source_audit_status"] for row in audit["criteria"] if row["criterion_key"].startswith("ihra/"))
    assert audit["frameworks"]["anti-muslim-racism"]["pdf_pages"] == 19
    assert audit["frameworks"]["anti-muslim-racism"]["pdf_sha256"] == "ccdb6531b43a1f398fcbfa2a88a3aee6da42c73e339517cb88e64728f8ea86fa"
    assert all(not row["recorded_model_profiles"] for row in audit["criteria"] if row["criterion_key"].startswith("language-norms/"))
    assert all(row["source_locator"] and row["source_summary"] for row in audit["criteria"])
    scholarly = [row for row in audit["criteria"] if row["criterion_key"].split("/")[0] in {"discourse", "political-framing", "language-norms"}]
    assert all(row["academic_precedents"] and all(item["relation"].endswith("not_validation_of_exact_operational_criterion") for item in row["academic_precedents"]) for row in scholarly)


def test_corpus_receipt_counts_every_original_archive_without_claiming_accuracy():
    audit = json.loads((ROOT / "examples/v04-corpus-verification.json").read_text())
    assert audit["status"] == "passed" and len(audit["archives"]) == 5
    assert sum(row["counts"]["audited_references"] for row in audit["archives"]) == 4_207_998
    news = next(row for row in audit["archives"] if row["archive"] == "benchmarks/v3-news/showcase.json")
    assert news["counts"]["documents"] == 5000 and news["status"] == "partial"
    assert news["retained_decisions"] == 204342 and news["unresolved_decisions"] > 0
    assert audit["interpretation_accuracy"] is None


def test_lineage_graph_hashes_and_missing_capture_are_explicit():
    index = json.loads((ROOT / "examples/v04-lineage-index.json").read_text())
    nodes = {row["id"]: row for row in index["records"]}
    assert len(nodes) == len(index["records"])
    assert index["historical_actual_request_capture"]["status"] == "not_recorded"
    assert index["raw_provider_responses"] == "not_recorded"
    assert not any(row["kind"].startswith("actual_request") or row["kind"] == "raw_response" for row in nodes.values())
    for row in nodes.values():
        raw = {key: value for key, value in row.items() if key not in {"id", "lineage_sha256"}}
        assert content_sha256(raw) == row["lineage_sha256"]
        for parent in row["parents"]:
            assert parent["id"] in nodes
            assert parent["sha256"] == nodes[parent["id"]]["lineage_sha256"]
        if row["kind"] == "reconstructed_template":
            assert row["availability"] == "reconstructed_not_actual_request"
    assert all(f"case-{number:02d}:comparison" in nodes for number in (7, 8, 9))


@pytest.mark.parametrize("slug,low,high", [("v04-reader-explainer", 16, 20), ("v04-technical-annex", 30, 40), ("v04-case-atlas", 35, 45)])
def test_actual_pdf_page_counts_content_links_and_manuscripts(slug, low, high):
    manifest = json.loads((ROOT / "examples" / (slug + ".manifest.json")).read_text())
    with open_pdf(ROOT / "examples" / (slug + ".pdf")) as pdf:
        assert low <= len(pdf) <= high and len(pdf) == manifest["pdf_pages"]
        assert all(len(page.get_text().strip()) > 100 for page in pdf)
        assert sum(len(page.get_links()) for page in pdf) > 0
        text = " ".join(page.get_text() for page in pdf)
        assert "independent human" in text.lower()
    html = (ROOT / "examples" / (slug + ".html")).read_text()
    assert "<script" not in html and "Content-Security-Policy" in html
    assert (ROOT / "docs" / (slug + ".md")).is_file()
    for file in manifest["files"]:
        assert sha(ROOT / file["file"]) == file["sha256"]


def test_evidence_pack_files_and_all_original_parents_are_unchanged():
    manifest = json.loads((ROOT / "examples/v04-evidence-pack.manifest.json").read_text())
    assert manifest["baseline_preservation"]["verified_unchanged"] is True
    assert manifest["baseline_preservation"]["files"] == len(manifest["parents"])
    assert manifest["paid_inference_calls_for_build"] == 0
    for file in [*manifest["files"], *manifest["parents"]]:
        assert sha(ROOT / file["file"]) == file["sha256"]
    assert all(Path(file["file"]).name.startswith("v04-") for file in manifest["files"])


def test_download_bundle_matches_its_detached_receipt_and_internal_manifest():
    receipt = json.loads((ROOT / "examples/v04-evidence-pack.zip.receipt.json").read_text())
    path = ROOT / receipt["file"]
    assert sha(path) == receipt["sha256"] and path.stat().st_size == receipt["bytes"]
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == receipt["members"]
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in archive.namelist())
        raw_manifest = archive.read(receipt["internal_manifest"])
        assert hashlib.sha256(raw_manifest).hexdigest() == receipt["manifest_sha256"]
        for item in json.loads(raw_manifest)["files"]:
            with archive.open(item["file"]) as stream:
                hashed = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hashed.update(block)
                assert hashed.hexdigest() == item["sha256"]


def test_walkthrough_exports_keep_actual_missing_decisions_visible(atlas, tmp_path):
    snapshot = copy.deepcopy(atlas["captures"]["institutional"])
    snapshot["snapshot_sha256"] = content_sha256(snapshot)
    html = render_html(snapshot, "Actual unresolved teaching case", "institutional-context")
    assert "Missing; not a negative" in html and "anti-muslim-hostility/discrimination_claim" in html
    target = tmp_path / "source-linked-unresolved.xlsx"
    render_workbook(snapshot, "Actual unresolved teaching case", target)
    with zipfile.ZipFile(target) as archive:
        assert "Unresolved decisions" in archive.read("xl/workbook.xml").decode()
        assert "Missing decision; no negative result or model rationale invented." in archive.read("xl/sharedStrings.xml").decode()


def test_tampered_source_payload_and_missing_decision_fail_closed(atlas):
    broken = copy.deepcopy(atlas)
    broken["cases"][0]["assessment"]["recorded_payload"]["explanation"] = "A fabricated interpretation"
    with pytest.raises(ValueError, match="payload"):
        audit_atlas(broken)
    capture = copy.deepcopy(atlas["captures"]["institutional"])
    capture["unresolved_units"][0]["result"] = "does_not_meet"
    with pytest.raises(ValueError, match="unresolved"):
        audit_snapshot(capture)


def test_freezer_selects_actual_negatives_and_frozen_rubrics_without_adding_rationales(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path)
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Synthetic freezer contract fixture',?)", [utcnow()])
    text = "The report contains a plain factual statement."
    seed_document(store, "source", text=text, metadata={"source_id": "fixture-source"})
    seed_run(store, sources=[{"id": "source", "sha256": "source-hash"}])
    pack = get_lens("discourse")
    pack["criteria"][0]["description"] = "The frozen criterion, distinct from the current registry."
    store.execute("UPDATE runs SET config_json=? WHERE id='run'", [json.dumps({"methodology_version": "3.0", "lens_ids": ["discourse"], "lens_snapshots": [pack], "source_snapshot": [{"id": "source", "sha256": "source-hash"}]})])
    payload = seed_assessment(store, "negative", "source", lens="discourse", criterion="stance", kind="descriptive", result="does_not_meet", text=text)
    payload.update({"quote": None, "explanation": None, "alternative": None, "detail_supplied": False,
                    "evidence_refs": [{"block_id": "b1", "start": 0, "end": len(text)}]})
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id='negative'", [json.dumps(payload)])
    try:
        with pytest.raises(ValueError):
            freeze_run(store.root, "run", assessment_ids=["negative"])
        frozen = freeze_run(store.root, "run", assessment_ids=["negative"], include_all_outcomes=True)
        item = frozen["assessments"][0]
        assert item["quote"] == text and item["explanation"] is None
        assert item["recorded_payload"]["quote"] is None
        assert frozen["criterion_definitions"]["discourse/stance"]["criterion"]["description"] == pack["criteria"][0]["description"]
        assert item["origin_labels"]["model_interpretation"] == "not_recorded"
        assert json.loads(store.one("SELECT payload_json FROM v3_assessments WHERE id='negative'")["payload_json"]) == payload
    finally:
        store.close()
