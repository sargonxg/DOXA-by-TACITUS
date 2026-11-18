"""Actual permissive PDF extraction, review boundaries and immutable text."""
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from doxa.extraction import extract_document
from doxa.pdf_tools import open_pdf
from pdf_fixtures import pdf_bytes


def test_default_pdf_does_not_import_legacy_engine_and_records_exact_parser_versions(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def guard(name, *args, **kwargs):
        if name in {"fitz", "pymupdf"}:
            raise AssertionError("Legacy AGPL engine must not load by default")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard)
    monkeypatch.delenv("DOXA_PDF_EXTRACTOR", raising=False)
    path = tmp_path / "source.pdf"
    path.write_bytes(pdf_bytes(["A documented statement and an alternative reading."]))
    first = extract_document(path)
    assert first == extract_document(path)
    assert first["extractor"] == "pdfminer-six"
    assert first["extractor_version"] == "0.6.0"
    manifest = first["extraction_manifest"]
    assert set(manifest["engine_versions"]) == {"doxa-extraction", "pdfminer.six", "pypdf"}
    assert manifest["settings"]["pdf_backend"] == "pdfminer"
    assert first["blocks"][0]["bbox"]
    assert first["blocks"][0]["text"] == "A documented statement and an alternative reading.\n"
    assert first["blocks"][0]["text"][2:12] == "documented"


def test_column_reading_order_is_a_review_signal_not_silent_complete_coverage(tmp_path):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800), invariant=True)
    for line in range(5):
        pdf.drawString(35, 730-line*17, f"Left column source line {line}.")
        pdf.drawString(350, 730-line*17, f"Right column evidence {line}.")
    pdf.save()
    path = tmp_path / "columns.pdf"
    path.write_bytes(buffer.getvalue())
    result = extract_document(path)
    text = " ".join(block["text"] for block in result["blocks"])
    assert "Left column source line 4." in text and "Right column evidence 4." in text
    assert result["extraction_manifest"]["pages"][0]["reading_order_review"]
    assert result["extraction_manifest"]["needs_review"]
    assert not result["extraction_manifest"]["source_coverage_complete"]
    assert "reading-order review" in " ".join(result["extraction_warnings"])


def test_unicode_text_is_retained_and_inspection_is_independent_of_pdfminer(tmp_path):
    from weasyprint import HTML
    path = tmp_path / "unicode.pdf"
    path.write_bytes(HTML(string='<p>Café and Ελληνικά are source evidence.</p>').write_pdf())
    result = extract_document(path)
    text = " ".join(block["text"] for block in result["blocks"])
    assert "Café" in text and "Ελληνικά" in text
    with open_pdf(path) as inspected:
        assert len(inspected) == 1
        assert "Café" in inspected[0].get_text()


def test_backend_is_explicit_and_bad_pdf_never_falls_back(tmp_path, monkeypatch):
    path = tmp_path / "damaged.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a PDF object graph")
    with pytest.raises(ValueError, match="damaged"):
        extract_document(path)
    monkeypatch.setenv("DOXA_PDF_EXTRACTOR", "automatic")
    with pytest.raises(ValueError, match="DOXA_PDF_EXTRACTOR"):
        extract_document(path)


def test_page_and_character_limits_fail_before_partial_success(tmp_path, monkeypatch):
    path = tmp_path / "limits.pdf"
    path.write_bytes(pdf_bytes(["First page.", "Second page."]))
    monkeypatch.setattr("doxa.extraction.MAX_PAGES", 1)
    with pytest.raises(ValueError, match="page extraction limit"):
        extract_document(path)
    monkeypatch.setattr("doxa.extraction.MAX_PAGES", 10000)
    monkeypatch.setattr("doxa.extraction.MAX_TEXT_CHARS", 5)
    with pytest.raises(ValueError, match="text exceeds"):
        extract_document(path)


def test_html_heading_metadata_preserves_original_text(tmp_path):
    path = tmp_path / "heading.html"
    path.write_text('<h2>  Reported allegation </h2><p>The writer rejects it.</p>')
    result = extract_document(path)
    assert result["blocks"][0]["text"] == "  Reported allegation "
    assert result["blocks"][0]["heading_level"] == 2
    assert "heading_level" not in result["blocks"][1]


def test_docx_builtin_heading_styles_are_explicit_without_guessing_custom_styles(tmp_path):
    import zipfile
    path = tmp_path / "sections.docx"
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Section title</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="MyBoldStyle"/></w:pPr><w:r><w:t>A custom styled paragraph.</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    blocks = extract_document(path)["blocks"]
    assert blocks[0]["heading_level"] == 2 and blocks[0]["text"] == "Section title"
    assert "heading_level" not in blocks[1]
