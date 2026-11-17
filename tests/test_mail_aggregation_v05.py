"""Mail context stays in evidence while source metadata is not treated as endorsement."""
import json
import xml.etree.ElementTree as ET
import zipfile

from doxa import recorded_export, reports
from doxa.mail import MAIL_AUTHOR_NOTICE
from doxa.workspace import Workspace, WorkspaceQuery, unpack_assessment, unpack_document
from test_recorded_export import recorded_workspace  # noqa: F401
from test_workspace import seed_assessment, seed_document, seed_run, store  # noqa: F401


def make_mail_scope(value):
    seed_document(value, "mail", author="Mailbox metadata", metadata={"mail_context_version": "mail-context-1.0"})
    value.execute("UPDATE documents SET filename='selected.mbox' WHERE id='mail'")
    seed_document(value, "public", author="Public author")
    seed_run(value, sources=[{"id": "mail", "sha256": "mail-hash"}, {"id": "public", "sha256": "public-hash"}])
    for index, role in enumerate(("authored", "quoted", "header", "signature", "unknown")):
        payload = seed_assessment(value, f"mail-{index}", "mail", unit=f"mail-unit-{index}", attribution="author",
                                  result="does_not_meet" if role == "authored" else "meets_criterion")
        payload["mail_context"] = {"mail_role": role, "message_key": "message-1"}
        if role != "unknown":
            payload["eligible_for_author_counts"] = role == "authored"
        value.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(payload), payload["id"]])
    seed_assessment(value, "public-positive", "public", attribution="author", unit="public-unit")
    return WorkspaceQuery(run_id="run", lens_ids=["ihra"], results=["meets_criterion", "does_not_meet"],
                          metric="endorsed_indicators", duplicate_policy="sources")


def test_mail_matrix_filters_context_but_overall_evidence_and_source_totals_remain(store):  # noqa: F811
    query = make_mail_scope(store)
    result = Workspace(store).create("p", query)
    assert result["metrics"]["documents"] == 2
    assert result["metrics"]["findings"] == 6
    assert result["findings"]["total"] == 6
    assert result["metrics"]["indicators"] == 2
    # Model-provided author attribution on quoted/header/signature text cannot
    # make the mailbox an author-endorsed positive source.
    assert result["metrics"]["endorsed_indicators"] == 1
    assert result["metrics"]["author_analysis"]["excluded_context_assessments"] == 4
    assert result["metrics"]["author_analysis"]["notice"] == MAIL_AUTHOR_NOTICE
    mail = next(cell for cell in result["matrix"] if cell["author"] == "Mailbox metadata")
    public = next(cell for cell in result["matrix"] if cell["author"] == "Public author")
    assert mail["count"] == 1 and mail["all_context_assessments"] == 5
    assert mail["excluded_context_count"] == 4
    assert mail["observations_per_10000_words"] is None
    assert public["count"] == 1 and public["excluded_context_count"] == 0
    assert all(point["rate"] is None and not point["spike_candidate"] for point in result["timeline"])
    assert all("message-aware" in point["rate_disabled_reason"] for point in result["timeline"])


def test_mail_html_and_xlsx_keep_all_findings_and_qualify_author_table(store, tmp_path):  # noqa: F811
    make_mail_scope(store)
    documents = [unpack_document(row) for row in store.query("SELECT * FROM documents ORDER BY id")]
    findings = [unpack_assessment(row) for row in store.query("SELECT * FROM v3_assessments ORDER BY id")]
    snapshot = {"schema_version": "3.0", "project": {"name": "Synthetic mail count proof"}, "documents": documents,
                "findings": findings, "runs": [], "profile": "research", "generated_at": "2026-11-17T00:00:00Z"}
    prepared = reports._prepare(snapshot)
    assert len(prepared["findings"]) == 6
    assert prepared["authors"]["Mailbox metadata"]["findings"] == 1
    assert prepared["authors"]["Mailbox metadata"]["excluded_context_findings"] == 4
    assert prepared["authors"]["Public author"]["findings"] == 1
    html = reports.build_report(snapshot, "html", tmp_path).read_text()
    assert MAIL_AUTHOR_NOTICE in html
    assert "Document author groups" in html
    assert "Every assessment has a source" in html
    xlsx = reports.build_report(snapshot, "xlsx", tmp_path)
    with zipfile.ZipFile(xlsx) as archive:
        strings = archive.read("xl/sharedStrings.xml").decode()
        assert "Suppressed for mail" in strings and "Excluded context assessments" in strings
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        findings_rows = ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall("s:sheetData/s:row", namespace)
        assert len(findings_rows) - 1 == 6


def test_recorded_mail_summary_filters_only_author_counts_and_full_workbook_retains_records(recorded_workspace, tmp_path):  # noqa: F811
    saved_store, root = recorded_workspace
    row = saved_store.one("SELECT metadata_json FROM documents WHERE id='source-a'")
    metadata = json.loads(row["metadata_json"])
    metadata["mail_context_version"] = "mail-context-1.0"
    saved_store.execute("UPDATE documents SET metadata_json=?,filename='selected.eml' WHERE id='source-a'", [json.dumps(metadata)])
    for index in range(5):
        original = saved_store.one("SELECT payload_json FROM v3_assessments WHERE id=?", [f"assessment-{index}"])
        payload = json.loads(original["payload_json"])
        payload["eligible_for_author_counts"] = index == 1
        saved_store.execute("UPDATE v3_assessments SET payload_json=? WHERE id=?", [json.dumps(payload), f"assessment-{index}"])
    with recorded_export.recorded_snapshot(root, "run") as frozen:
        data = recorded_export.prepared_human_data(frozen)
        assert data["authors"]["Fixture Author"]["findings"] == 1
        assert data["authors"]["Fixture Author"]["excluded_context_findings"] == 4
        assert len(frozen["records"]) == 5
        target = tmp_path / "mail.xlsx"
        receipt = recorded_export.write_xlsx(frozen, data, target)
        assert receipt["logical_rows"]["Findings"] == 5
        with zipfile.ZipFile(target) as archive:
            xml = b"\n".join(archive.read(name) for name in archive.namelist() if name.startswith("xl/worksheets/"))
            assert b"Excluded context assessments" in xml
            assert b"Mail author groups" in xml


def test_nonmail_author_counts_and_rates_remain_available(store):  # noqa: F811
    seed_document(store, "public", author="Public author")
    seed_run(store, sources=[{"id": "public", "sha256": "public-hash"}])
    seed_assessment(store, "public-positive", "public", attribution="author")
    result = Workspace(store).create("p", WorkspaceQuery(run_id="run", lens_ids=["ihra"], results=["meets_criterion"],
                                                         metric="endorsed_indicators", duplicate_policy="sources"))
    assert "author_analysis" not in result["metrics"]
    assert result["matrix"][0]["count"] == 1
    assert result["matrix"][0]["observations_per_10000_words"] is not None
    assert result["timeline"][0]["rate"] == 1
