import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from doxa.extraction import extract_document


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, data):
        path = self.directory / name
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def test_text_blocks_preserve_exact_evidence_offsets_and_are_stable(self):
        path = self.put("stored.bin", "  A quoted ‘claim’.\nA second line.  \n\nOther paragraph.")
        first = extract_document(path, "original.txt")
        second = extract_document(path, "original.txt")
        self.assertEqual(first, second)
        self.assertEqual(first["blocks"][0]["text"], "  A quoted ‘claim’.\nA second line.  ")
        self.assertEqual(first["blocks"][0]["id"], "b000001")
        self.assertEqual(first["metadata"]["filename"], "original.txt")
        self.assertEqual(len(first["metadata"]["sha256"]), 64)
        text = first["blocks"][0]["text"]
        start = text.index("‘claim’")
        self.assertEqual(text[start:start+7], "‘claim’")

    def test_html_never_includes_scripts_hidden_text_or_external_content(self):
        path = self.put("news.html", """<html><head><title>Not body</title><script>steal()</script></head><body><h1>Visible heading</h1><p>Quoted <em>words</em> &amp; context.</p><script>fetch('https://secret.invalid')</script><p hidden>secret</p><iframe src="https://secret.invalid">remote</iframe><p style="display: none">hidden</p><div>Final paragraph</div></body></html>""")
        result = extract_document(path)
        text = "\n".join(b["text"] for b in result["blocks"])
        self.assertIn("Quoted words & context.", text)
        self.assertIn("Final paragraph", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("steal", text)
        self.assertNotIn("Not body", text)
        self.assertTrue(result["extraction_warnings"])

    def test_docx_preserves_hyperlink_text_tables_and_author(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t xml:space="preserve"> A </w:t></w:r><w:hyperlink><w:r><w:t>linked claim</w:t></w:r></w:hyperlink><w:r><w:tab/><w:t>end</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table evidence</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
            z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Named author</dc:creator></cp:coreProperties>')
        result = extract_document(self.put("sample.docx", buffer.getvalue()))
        self.assertEqual(result["blocks"][0]["text"], " A linked claim\tend")
        self.assertEqual(result["blocks"][1]["text"], "Table evidence")
        self.assertEqual(result["metadata"]["author"], "Named author")

    def test_docx_macros_and_xml_entities_are_rejected(self):
        for dangerous in ("macro", "entity"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as z:
                z.writestr("word/document.xml", '<!DOCTYPE doc [<!ENTITY x "boom">]><doc/>' if dangerous == "entity" else "<doc/>")
                if dangerous == "macro":
                    z.writestr("word/vbaProject.bin", b"macro")
            with self.subTest(dangerous=dangerous), self.assertRaises(ValueError):
                extract_document(self.put(f"{dangerous}.docx", buffer.getvalue()))

    def test_empty_unsupported_binary_and_resource_exhaustion_fail_explicitly(self):
        for name, content in [("empty.txt", ""), ("binary.txt", b"a\x00b"), ("macro.docm", b"data"), ("damaged.docx", b"not zip")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                extract_document(self.put(name, content))
        path = self.put("large.txt", "Full source content must never truncate silently.")
        with patch("doxa.extraction.MAX_TEXT_CHARS", 10), self.assertRaisesRegex(ValueError, "exceeds"):
            extract_document(path)

    def test_pdf_text_locations_and_partial_extraction_warning(self):
        from pdf_fixtures import pdf_bytes
        path = self.directory / "source.pdf"
        path.write_bytes(pdf_bytes(["Source evidence on page one.", None]))
        result = extract_document(path)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(result["blocks"][0]["page"], 1)
        self.assertEqual(result["metadata"]["pages_without_text"], [2])
        self.assertIn("require OCR or are blank", " ".join(result["extraction_warnings"]))

    def test_encrypted_and_image_only_pdf_fail_actionably(self):
        from pdf_fixtures import pdf_bytes
        blank_path = self.directory / "blank.pdf"
        blank_path.write_bytes(pdf_bytes([None]))
        with self.assertRaisesRegex(ValueError, "OCR"):
            extract_document(blank_path)
        protected_path = self.directory / "protected.pdf"
        protected_path.write_bytes(pdf_bytes(["Protected source"], password="password"))
        with self.assertRaisesRegex(ValueError, "encrypted"):
            extract_document(protected_path)


if __name__ == "__main__":
    unittest.main()
