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
            import fitz
        except ImportError:
            self.skipTest("PDF verification dependencies unavailable")
        with patch.dict("sys.modules", {"weasyprint": None}):
            path = build_report(snapshot(), "pdf", self.directory)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        with fitz.open(path) as pdf:
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
            import fitz
        except ImportError:
            return
        data["profile"] = "briefing"
        with patch.dict("sys.modules", {"weasyprint": None}):
            path = build_report(data, "pdf", self.directory)
        with fitz.open(path) as pdf:
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
        import fitz
        for fallback in (False, True):
            if fallback:
                with patch.dict("sys.modules", {"weasyprint": None}):
                    path = build_report(data, "pdf", self.directory)
            else:
                path = build_report(data, "pdf", self.directory)
            with fitz.open(path) as pdf:
                text = " ".join(page.get_text() for page in pdf)
            self.assertNotIn(sentinel, text)
            self.assertIn("Partial coverage", text)
            self.assertIn("How are public assumptions justified?", text)
            self.assertIn("doxa-evidence-1.2", text)


if __name__ == "__main__":
    unittest.main()
