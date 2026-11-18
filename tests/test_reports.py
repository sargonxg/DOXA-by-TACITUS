import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from doxa.reports import build_report


def snapshot():
    text = 'A reporter quoted "the harmful claim" and then rejected it.'
    quote = '"the harmful claim"'
    start = text.index(quote)
    return {
        "project": {"id": "p1", "name": "Context & conviction <research>"},
        "profile": "research", "generated_at": "2026-11-16T12:00:00Z",
        "filters": {"lens_id": "test-lens"},
        "metrics": {"coverage": {"completed_chunks": 1, "total_chunks": 2}},
        "documents": [{"id": "d1", "title": "=HYPERLINK(\"https://evil.invalid\")", "author": "+Untrusted author", "word_count": 11, "page_count": 1, "status": "ready", "filename": "source.txt", "sha256": "a"*64, "license": "CC0", "blocks": [{"id": "b000001", "text": text, "page": 1, "paragraph": 1}]}],
        "findings": [{"id": "f1", "document_id": "d1", "block_id": "b000001", "start": start, "end": start+len(quote), "quote": quote, "title": "Quoted speech is rebutted", "explanation": "The reporter rejects the quoted claim.", "alternative": "The quotation could be misread without its rebuttal.", "category": "quoted_claim", "lens_id": "test-lens", "stance": "rebuttal", "speaker": "quoted speaker", "attribution": "quotation", "certainty": "medium", "review_status": "unreviewed", "run_id": "r1"}],
        "runs": [{"id": "r1", "status": "partial", "config": {"lens_id": "test-lens"}, "model_profile": {"model": "test-model", "digest": "abc"}, "progress": {"completed": 1, "total": 2}, "usage": {"input_tokens": 140, "output_tokens": 90}, "spent_usd": 0.001}],
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_html_is_offline_escaped_and_contains_evidence_context_and_coverage(self):
        data = snapshot()
        data["findings"][0]["title"] = '<script>alert("unsafe")</script>'
        text = build_report(data, "html", self.directory).read_text()
        self.assertNotIn('<script>alert("unsafe")</script>', text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("default-src 'none'", text)
        self.assertIn("<svg", text)
        self.assertIn("<mark>&quot;the harmful claim&quot;</mark>", text)
        self.assertIn("then rejected it", text)
        self.assertIn("Completed chunks", text)
        self.assertIn("characters", text)
        self.assertIn("Alternative reading", text)
        self.assertNotIn("<script src=", text)
        self.assertNotIn("<link", text)

    def test_xlsx_has_eight_sheets_real_charts_and_no_untrusted_formulas(self):
        if importlib.util.find_spec("xlsxwriter") is None:
            self.skipTest("XlsxWriter unavailable")
        path = build_report(snapshot(), "xlsx", self.directory)
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            names = [s.attrib["name"] for s in workbook.findall("s:sheets/s:sheet", namespace)]
            self.assertEqual(names, ["Overview", "Documents", "Findings", "Evidence", "Authors", "Lenses", "Runs", "Methodology"])
            self.assertEqual(len([p for p in archive.namelist() if p.startswith("xl/charts/chart") and p.endswith(".xml")]), 2)
            strings = archive.read("xl/sharedStrings.xml").decode()
            self.assertIn("HYPERLINK", strings)
            self.assertIn("then rejected it", strings)
            for name in archive.namelist():
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    root = ET.fromstring(archive.read(name))
                    self.assertFalse(root.findall(".//s:f", namespace), name)

    def test_xlsx_long_evidence_is_continued_without_silent_truncation(self):
        data = snapshot()
        data["findings"][0]["quote"] = "A"*31999 + "BOUNDARY" + "B"*10000
        path = build_report(data, "xlsx", self.directory)
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            values = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            self.assertTrue(any(value.endswith("B") and len(value) > 9000 for value in values))
            self.assertIn("1/2", values)
            self.assertIn("2/2", values)
            self.assertTrue(any(value == "A"*31999 + "B" for value in values))
            self.assertTrue(any(value.startswith("OUNDARY") for value in values))

    def test_csv_formula_safety_and_jsonl_exact_provenance(self):
        data = snapshot()
        path = build_report(data, "csv", self.directory)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["source_title"][0], "'")
        self.assertEqual(rows[0]["author"], "'+Untrusted author")
        self.assertEqual(rows[0]["quote"], data["findings"][0]["quote"])
        lines = [json.loads(line) for line in build_report(data, "jsonl", self.directory).read_text().splitlines()]
        self.assertEqual([r["record_type"] for r in lines], ["manifest", "document", "finding", "run"])
        self.assertEqual(lines[2]["data"]["quote"], data["findings"][0]["quote"])
        self.assertEqual(lines[1]["data"]["blocks"], data["documents"][0]["blocks"])

    def test_all_profiles_use_the_same_evidence(self):
        for profile, label in [("research", "Research dossier"), ("briefing", "Executive briefing"), ("teaching", "Teaching edition")]:
            data = snapshot()
            data["profile"] = profile
            text = build_report(data, "html", self.directory).read_text()
            self.assertIn(label, text)
            self.assertIn("Observation 001", text)
            self.assertNotIn("Observation 002", text)
            self.assertIn("then rejected it", text)

    def test_source_attribution_license_reference_and_rights_limits_survive_exports(self):
        data = snapshot()
        attribution = "Contains public sector information licensed under the Open Government Licence v3.0."
        data["documents"][0]["metadata"] = {"attribution": attribution, "license_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/", "rights_notes": "Images and third-party reproductions are excluded."}
        text = build_report(data, "html", self.directory).read_text()
        self.assertIn(attribution, text)
        self.assertIn("Images and third-party reproductions are excluded.", text)
        path = build_report(data, "xlsx", self.directory)
        with zipfile.ZipFile(path) as workbook:
            self.assertIn(attribution, workbook.read("xl/sharedStrings.xml").decode())

    def test_pdf_fallback_is_a_real_pdf_with_source_and_alternative(self):
        try:
            from doxa.pdf_tools import open_pdf
        except ImportError:
            self.skipTest("PDF verification dependencies unavailable")
        with patch.dict("sys.modules", {"weasyprint": None}):
            path = build_report(snapshot(), "pdf", self.directory)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        with open_pdf(path) as pdf:
            text = " ".join(page.get_text() for page in pdf)
        self.assertIn("harmful claim", text)
        self.assertIn("Alternative reading", text)
        self.assertIn("abc", text)
        self.assertIn("Completed chunks", text)

    def test_unknown_formats_and_profiles_are_explicit_errors(self):
        with self.assertRaises(ValueError):
            build_report(snapshot(), "exe", self.directory)
        data = snapshot()
        data["profile"] = "unknown"
        with self.assertRaises(ValueError):
            build_report(data, "html", self.directory)

    def test_readable_evidence_appendix_is_explicitly_bounded_but_data_exports_are_complete(self):
        data = snapshot()
        original = data["findings"][0]
        data["findings"] = [{**original, "id": f"f{i}", "title": f"Unique observation {i:03d}", "evidence_kind": "lexical_match"} for i in range(201)]
        for profile, limit in [("research", 100), ("briefing", 25), ("teaching", 50)]:
            data["profile"] = profile
            text = build_report(data, "html", self.directory).read_text()
            self.assertIn(f"Showing {limit} of 201 observations", text)
            self.assertIn(f"Unique observation {limit-1:03d}", text)
            self.assertNotIn(f"Unique observation {limit:03d}", text)
            self.assertIn("Literal baseline only", text)
            self.assertIn("No model performed semantic interpretation", text)
        self.assertEqual(len(data["findings"]), 201)
        records = [json.loads(line) for line in build_report(data, "jsonl", self.directory).read_text().splitlines()]
        self.assertEqual(sum(r["record_type"] == "finding" for r in records), 201)
        with build_report(data, "csv", self.directory).open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 201)
        try:
            from doxa.pdf_tools import open_pdf
        except ImportError:
            return
        data["profile"] = "briefing"
        with patch.dict("sys.modules", {"weasyprint": None}):
            path = build_report(data, "pdf", self.directory)
        with open_pdf(path) as pdf:
            text = " ".join(page.get_text() for page in pdf)
        self.assertIn("Showing 25 of 201 observations", text)
        self.assertIn("Unique observation 024", text)
        self.assertNotIn("Unique observation 025", text)
        self.assertIn("LITERAL BASELINE ONLY", text)

    def test_human_reports_use_explicit_coverage_units_and_compact_provenance_not_telemetry_dumps(self):
        data = snapshot()
        sentinel = "private-metric-field-not-for-narrative"
        data["metrics"] = {"coverage": 64.3, "coverage_detail": {"blocks_done": 5788, "blocks_total": 8999, "processed_characters": 1570904, "selected_characters": 2349640, "tasks_failed": 115, "complete": False}, "internal": {"private": sentinel}, "recent_findings": [{"foo": sentinel}]*300}
        data["runs"][0]["config"].update({"question": "How are public assumptions justified?", "persona": "An evidence-focused reviewer", "source_snapshot": [{"foo": sentinel}]*100})
        data["runs"][0]["model_profile"].update({"provider": "cloudflare", "revision": "unresolved", "prompt_version": "doxa-evidence-1.2", "rubric_versions": {"discourse": "1.0.0"}, "private": sentinel})
        html = build_report(data, "html", self.directory).read_text()
        self.assertNotIn(sentinel, html)
        self.assertIn("5,788 of 8,999 (64.3%)", html)
        self.assertIn("1,570,904 of 2,349,640 (66.9%)", html)
        self.assertIn("Extracted blocks processed", html)
        self.assertIn("Extracted characters processed", html)
        self.assertIn("Partial coverage", html)
        self.assertIn("How are public assumptions justified?", html)
        self.assertIn("An evidence-focused reviewer", html)
        self.assertIn("doxa-evidence-1.2", html)
        self.assertIn("140 input tokens; 90 output tokens", html)
        self.assertNotIn("<pre>", html)
        records = [json.loads(line) for line in build_report(data, "jsonl", self.directory).read_text().splitlines()]
        self.assertEqual(records[0]["data"]["metrics"]["internal"]["private"], sentinel)
        self.assertEqual(next(r["data"] for r in records if r["record_type"] == "run")["config"]["source_snapshot"][0]["foo"], sentinel)
        from doxa.pdf_tools import open_pdf
        for fallback in (False, True):
            if fallback:
                with patch.dict("sys.modules", {"weasyprint": None}):
                    path = build_report(data, "pdf", self.directory)
            else:
                path = build_report(data, "pdf", self.directory)
            with open_pdf(path) as pdf:
                text = " ".join(page.get_text() for page in pdf)
            self.assertNotIn(sentinel, text)
            self.assertIn("Partial coverage", text)
            self.assertIn("How are public assumptions justified?", text)
            self.assertIn("doxa-evidence-1.2", text)




def v3_snapshot(document_count=2):
    """Synthetic metadata-only snapshot; no model-quality claim is made."""
    data = snapshot()
    data.update({"schema_version": "3.0", "query_id": "query-frozen-v3", "scope_hash": "scope-" + "a" * 64,
                 "snapshot_id": "export-snapshot-3", "snapshot_version": "42"})
    data["metrics"] = {"coverage": 100, "coverage_detail": {"units_total": 2, "units_done": 2,
                       "characters": 200, "processed_characters": 200, "basis": "contextual source units"}}
    data["runs"][0]["status"] = "completed"
    data["runs"][0]["config"].update({"methodology_version": "3.0", "provider": "openai"})
    data["filters"] = {"run_id": "r1", "document_ids": [f"source-{index:05d}" for index in range(document_count)],
                       "lens_ids": ["ihra", "jda", "discourse"], "date_basis": "publication", "results": ["meets_criterion", "uncertain"]}
    data["documents"] = [{"id": f"source-{index:05d}", "title": f"Unique source title {index:05d}",
                          "filename": f"source-{index:05d}.txt", "author": f"Unique author {index:05d}",
                          "document_type": "article", "published_at": "2024-01-15", "status": "ready",
                          "word_count": 20, "sha256": f"hash-{index:05d}", "source_url": f"https://source.example/{index}",
                          "license": "CC BY 3.0", "external_reference": f"additional-source-field-{index:05d}",
                          "metadata": {"story_group_id": f"group-{index:05d}", "language": "en",
                                       "attribution": f"Attribution for source {index:05d}", "rights_notes": "Source-specific rights retained."}}
                         for index in range(document_count)]
    original = {**data["findings"][0], "document_id": data["documents"][-1]["id"],
                "criterion_id": "anti_jewish_generalization", "criterion_kind": "indicator", "result": "meets_criterion",
                "proposition": "The represented proposition is acceptable.", "speech_role": "attributed_quotation",
                "speaker_stance": "supports", "document_stance": "opposes", "attribution": "quoted_speaker",
                "hypothetical": True, "unit_id": "unit-stable-1", "source_version_id": "version-source-1",
                "locator": {"page": 7, "paragraph": 9}, "context": 'A reporter quoted "the harmful claim" and then rejected it.'}
    data["findings"] = [original]
    return data


def workbook_cells(path, sheet_number):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(node.itertext()) for node in strings.findall("s:si", ns)]
        sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet_number}.xml"))
        rows = []
        for row in sheet.findall("s:sheetData/s:row", ns):
            cells = {}
            for cell in row.findall("s:c", ns):
                value = cell.find("s:v", ns)
                if value is not None:
                    cells[cell.attrib["r"]] = shared[int(value.text)] if cell.attrib.get("t") == "s" else value.text
            rows.append(cells)
        return rows


class V3ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_5000_source_human_register_and_authors_are_bounded_with_cited_sources_prioritized(self):
        from bs4 import BeautifulSoup
        data = v3_snapshot(5000)
        for profile, limit in [("research", 100), ("briefing", 25), ("teaching", 50)]:
            data["profile"] = profile
            html = build_report(data, "html", self.directory).read_text()
            parsed = BeautifulSoup(html, "html.parser")
            entries = parsed.select("#sources article.document")
            self.assertEqual(len(entries), limit)
            self.assertIn(f"Showing {limit} of 5,000 source entries", parsed.get_text())
            self.assertIn(f"Showing {limit} of 5,000 authors", parsed.get_text())
            self.assertIn("Unique source title 04999", entries[0].get_text())
            self.assertNotIn("Unique source title 04998", html)
            source_href = parsed.select_one("article.finding p.citation a")["href"]
            self.assertIsNotNone(parsed.select_one(source_href))
            self.assertLess(len(html.encode()), 300_000)
        self.assertEqual(len(data["documents"]), 5000)

    def test_v3_unit_coverage_and_query_provenance_are_readable_without_telemetry(self):
        data = v3_snapshot()
        data["runs"][0]["progress"]["private_debug"] = "private-progress-sentinel"
        html = build_report(data, "html", self.directory).read_text()
        for value in ["Contextual source units assessed", "2 of 2 (100.0%)", "200 of 200 (100.0%)",
                      "Processing is complete for the stated selection", "Query ID", "query-frozen-v3",
                      "Scope hash", data["scope_hash"], "Snapshot ID", "export-snapshot-3", "Snapshot version", "42",
                      "Proposition", "The represented proposition is acceptable.", "attributed quotation", "Document stance", "opposes", "p. 7", "paragraph 9"]:
            self.assertIn(value, html)
        self.assertNotIn("private-progress-sentinel", html)
        self.assertNotIn("Coverage counts were not supplied", html)
        self.assertNotIn("<pre>", html)

    def test_typed_kind_and_result_counts_remain_distinct_in_human_and_jsonl_reports(self):
        data = v3_snapshot()
        original = data["findings"][0]
        variations = [("boundary", "meets_criterion"), ("descriptive", "meets_criterion"),
                      ("indicator", "does_not_meet"), ("indicator", "uncertain"), (None, "legacy_unclassified")]
        data["findings"] += [{**original, "id": f"typed-{index}", "criterion_kind": kind, "result": result}
                             for index, (kind, result) in enumerate(variations)]
        html = build_report(data, "html", self.directory).read_text()
        for value in ["Typed assessment results", "indicator", "boundary", "descriptive", "does not meet", "uncertain", "unknown / legacy", "no combined prejudice index"]:
            self.assertIn(value, html)
        records = [json.loads(line) for line in build_report(data, "jsonl", self.directory).read_text().splitlines()]
        counts = records[0]["data"]["exported_assessment_counts"]
        self.assertEqual(sum(row["count"] for row in counts), 6)
        self.assertEqual(next(row["count"] for row in counts if row["criterion_kind"] == "indicator" and row["result"] == "meets_criterion"), 1)
        self.assertEqual(sum(row["record_type"] == "finding" for row in records), 6)
        self.assertEqual(next(row["data"] for row in records if row["record_type"] == "finding")["proposition"], original["proposition"])

    def test_5000_sources_and_metadata_survive_complete_v3_csv_jsonl_and_xlsx_exports(self):
        data = v3_snapshot(5000)
        data["profile"] = "briefing"
        with build_report(data, "csv", self.directory).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        sources = [row for row in rows if row["record_type"] == "document"]
        assessments = [row for row in rows if row["record_type"] == "assessment"]
        self.assertEqual(len(sources), 5000)
        self.assertEqual(len(assessments), 1)
        self.assertEqual(json.loads(sources[-1]["source_metadata"])["story_group_id"], "group-04999")
        self.assertEqual(json.loads(sources[-1]["source_record"])["external_reference"], "additional-source-field-04999")
        assessment = assessments[0]
        for key in ("criterion_kind", "result", "proposition", "speech_role", "speaker_stance", "document_stance", "unit_id", "source_version_id"):
            self.assertEqual(assessment[key], str(data["findings"][0][key]))
        self.assertEqual(assessment["source_context"], data["findings"][0]["context"])
        self.assertEqual(assessment["scope_hash"], data["scope_hash"])
        self.assertEqual(assessment["source_attribution"], "Attribution for source 04999")
        records = [json.loads(line) for line in build_report(data, "jsonl", self.directory).read_text().splitlines()]
        self.assertEqual(sum(row["record_type"] == "document" for row in records), 5000)
        self.assertEqual(records[0]["data"]["query_id"], data["query_id"])
        self.assertEqual(next(row["data"] for row in records if row["record_type"] == "document" and row["data"]["id"] == "source-04999"), data["documents"][-1])
        workbook = build_report(data, "xlsx", self.directory)
        document_rows = workbook_cells(workbook, 2)
        self.assertEqual(len(document_rows), 5001)
        self.assertIn("additional-source-field-04999", " ".join(document_rows[-1].values()))
        finding_rows = workbook_cells(workbook, 3)
        headers = list(finding_rows[0].values())
        for label in ["Criterion kind", "Assessment result", "Proposition", "Speech role", "Speaker stance", "Document stance", "Hypothetical", "Source unit ID", "Source version ID"]:
            self.assertIn(label, headers)
        evidence = " ".join(workbook_cells(workbook, 4)[1].values())
        self.assertIn(data["findings"][0]["context"], evidence)
        method = " ".join(value for row in workbook_cells(workbook, 8) for value in row.values())
        self.assertIn(data["scope_hash"], method)
        overview = " ".join(value for row in workbook_cells(workbook, 1) for value in row.values())
        self.assertIn("Contextual unit coverage (%)", overview)
        self.assertIn("indicator", overview)

    def test_v3_partial_units_never_claim_complete_processing(self):
        data = v3_snapshot()
        data["metrics"]["coverage_detail"].update({"units_done": 1, "processed_characters": 100})
        html = build_report(data, "html", self.directory).read_text()
        self.assertIn("1 of 2 (50.0%)", html)
        self.assertIn("Partial coverage", html)
        self.assertNotIn("Processing is complete for the stated selection", html)

    def test_pdf_engines_bound_5000_source_registers_and_preserve_typed_fields(self):
        from doxa.pdf_tools import open_pdf
        data = v3_snapshot(5000)
        data["profile"] = "briefing"
        for fallback in (False, True):
            if fallback:
                with patch.dict("sys.modules", {"weasyprint": None}):
                    path = build_report(data, "pdf", self.directory)
            else:
                path = build_report(data, "pdf", self.directory)
            with open_pdf(path) as pdf:
                text = " ".join(" ".join(page.get_text() for page in pdf).split())
                self.assertLess(len(pdf), 30)
            for value in ["Showing 25 of 5,000 source entries", "Unique source title 04999", "Contextual source units assessed",
                          "2 of 2 (100.0%)", "Typed assessment results", "Proposition", "The represented proposition is acceptable.",
                          "Document stance", "opposes", "query-frozen-v3", "export-snapshot-3"]:
                self.assertIn(value.casefold(), text.casefold())
            self.assertNotIn("Unique source title 04998", text)
            self.assertIn("then rejected it", text)


def test_unknown_provider_invoice_is_not_replaced_with_budget_accounted_zero(tmp_path):
    data = snapshot()
    data["runs"][0]["usage"].update({"cost_usd": None, "estimated": True})
    data["runs"][0]["spent_usd"] = 0.417891
    html = build_report(data, "html", tmp_path).read_text()
    assert "Provider cost unavailable" in html
    assert "$0.4179 accounted spend" in html
    assert "inference cost: $0.0000" not in html
    workbook = build_report(data, "xlsx", tmp_path)
    rows = workbook_cells(workbook, 7)
    assert "Budget accounted spend (USD)" in rows[0].values()
    assert "Provider inference cost (USD; blank if unknown)" in rows[0].values()
    assert rows[1]["I2"] == "0.417891"
    assert rows[1].get("K2", "") == ""
    from doxa.pdf_tools import open_pdf
    for fallback in (False, True):
        if fallback:
            with patch.dict("sys.modules", {"weasyprint": None}):
                path = build_report(data, "pdf", tmp_path)
        else:
            path = build_report(data, "pdf", tmp_path)
        with open_pdf(path) as pdf:
            text = " ".join(" ".join(page.get_text() for page in pdf).split())
        assert "Provider cost unavailable" in text
        assert "$0.4179 accounted spend" in text
        assert "inference cost: $0.0000" not in text


def test_v3_compatibility_block_progress_does_not_mislabel_source_units(tmp_path):
    data = v3_snapshot()
    data["runs"][0]["config"]["task_index_basis"] = "source_units_v3"
    data["runs"][0]["progress"].update({"blocks_done": 2, "blocks_total": 2})
    html = build_report(data, "html", tmp_path).read_text()
    assert "Contextual source units assessed" in html and "2 of 2 (100.0%)" in html
    assert "Extracted blocks processed" not in html
    data["metrics"]["coverage_detail"].update({"blocks_done": 5, "blocks_total": 8})
    html = build_report(data, "html", tmp_path).read_text()
    assert "Extracted blocks processed" in html and "5 of 8 (62.5%)" in html


if __name__ == "__main__":
    unittest.main()
