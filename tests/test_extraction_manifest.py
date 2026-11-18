"""New extraction provenance and fast-path parity; no model downloads."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from doxa.extraction import extract_document, extract_text_document


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("filename", ["article.txt", "article.md", "article.markdown"])
@pytest.mark.parametrize("data", [
    "  First ‘quote’.\r\nA continuation.  \r\n \t\r\nOther e\u0301vidence 👩🏽‍💻.\n\n".encode(),
    b"\xef\xbb\xbfBOM content.\n\nSecond paragraph.",
    "Quoted “claim” — context.".encode("cp1252"),
])
def test_plain_text_in_process_and_subprocess_entry_use_identical_policy(tmp_path, filename, data):
    path = tmp_path / "upload.bin"
    path.write_bytes(data)
    fast = extract_text_document(path, filename)
    general = extract_document(path, filename)
    assert fast == general
    encoding = fast["metadata"]["encoding"]
    text = data.decode("cp1252" if encoding == "windows-1252" else "utf-8-sig")
    for block in fast["blocks"]:
        offset = block["source_char_start"]
        assert text[offset:offset + len(block["text"])] == block["text"]
    assert fast["blocks"][0]["id"] == "b000001"
    if encoding == "windows-1252":
        assert "Windows-1252" in " ".join(fast["extraction_warnings"])
        assert fast["extraction_manifest"]["needs_review"]
        assert not fast["extraction_manifest"]["source_coverage_complete"]


@pytest.mark.parametrize("data", [b"a\x00b", "UTF sixteen".encode("utf-16"), b"\x01\x02binary", b"\x81"])
def test_plain_text_binary_and_unsupported_encodings_fail_consistently(tmp_path, data):
    path = tmp_path / "source.txt"
    path.write_bytes(data)
    for entry in (extract_text_document, extract_document):
        with pytest.raises(ValueError):
            entry(path)


def test_manifest_binds_raw_bytes_blocks_settings_and_preserves_declared_exclusions(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("  Claim.\n\nOther context.  ", encoding="utf-8")
    first = extract_document(path)
    manifest = first["extraction_manifest"]
    assert manifest == first["metadata"]["extraction_manifest"]
    assert manifest["raw_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest["blocks_sha256"] == canonical_hash(first["blocks"])
    assert manifest["settings_hash"] == canonical_hash(manifest["settings"])
    assert manifest["source_characters"] == sum(len(b["text"]) for b in first["blocks"])
    assert manifest["encoding"] == "utf-8"
    assert manifest["language"] == "und"
    assert manifest["pages"] == []
    assert manifest["source_coverage_complete"]
    assert manifest["exclusions"]
    assert "visual content" in manifest["scope"]
    assert first == extract_document(path)
    path.write_text("  Claim.\n\nChanged context.  ", encoding="utf-8")
    changed = extract_document(path)["extraction_manifest"]
    assert changed["extraction_version_id"] != manifest["extraction_version_id"]
    assert changed["raw_sha256"] != manifest["raw_sha256"]
    assert changed["blocks_sha256"] != manifest["blocks_sha256"]


def test_text_limits_apply_to_both_entry_points(tmp_path, monkeypatch):
    path = tmp_path / "source.txt"
    path.write_text("Text must not silently truncate.")
    monkeypatch.setattr("doxa.extraction.MAX_TEXT_CHARS", 4)
    for entry in (extract_text_document, extract_document):
        with pytest.raises(ValueError, match="exceeds"):
            entry(path)


def test_plain_text_entry_rejects_non_text_format(tmp_path):
    path = tmp_path / "source.html"
    path.write_text("<p>HTML uses the visible-text extractor.</p>")
    with pytest.raises(ValueError, match="only supports"):
        extract_text_document(path)
    result = extract_document(path)
    assert result["extraction_manifest"]["extractor_id"] == "stdlib-html-visible-text"
    assert any("hidden text" in item for item in result["extraction_manifest"]["exclusions"])


def test_docx_declares_unextracted_additional_text_parts(tmp_path):
    path = tmp_path / "notes.docx"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Body source text.</w:t></w:r></w:p></w:body></w:document>')
        archive.writestr("word/footnotes.xml", "<footnotes>Additional source note</footnotes>")
        archive.writestr("word/header1.xml", "<header>Header text</header>")
    path.write_bytes(buffer.getvalue())
    result = extract_document(path)
    manifest = result["extraction_manifest"]
    assert result["blocks"][0]["text"] == "Body source text."
    assert result["metadata"]["excluded_text_parts"] == ["word/footnotes.xml", "word/header1.xml"]
    assert any("word/footnotes.xml" in item for item in manifest["exclusions"])
    assert manifest["needs_review"] and not manifest["source_coverage_complete"]
    assert manifest["coverage_basis"] == "declared_extraction_scope"


def mixed_pdf(tmp_path):
    from pdf_fixtures import mixed_pdf_bytes
    path = tmp_path / "mixed.pdf"
    path.write_bytes(mixed_pdf_bytes())
    return path


def test_mixed_selectable_header_and_raster_body_is_not_complete_text_coverage(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_ENABLE_OCR", raising=False)
    result = extract_document(mixed_pdf(tmp_path))
    assert "Selectable header" in " ".join(block["text"] for block in result["blocks"])
    assert result["metadata"]["mixed_scan_pages"] == [1]
    assert result["metadata"]["pages_needing_ocr"] == [1]
    assert result["metadata"]["pages_without_text"] == []
    manifest = result["extraction_manifest"]
    assert manifest["needs_review"] and not manifest["source_coverage_complete"]
    assert manifest["pages"][0]["method"] == "text"
    assert manifest["pages"][0]["needs_review"]
    assert manifest["pages"][0]["image_area_fraction"] >= .35
    assert "selectable text alone" in " ".join(result["extraction_warnings"])


def test_pdf_blank_page_remains_explicitly_unavailable(tmp_path, monkeypatch):
    from pdf_fixtures import pdf_bytes
    monkeypatch.delenv("DOXA_ENABLE_OCR", raising=False)
    path = tmp_path / "partial.pdf"
    path.write_bytes(pdf_bytes(["Selectable text on page one.", None]))
    manifest = extract_document(path)["extraction_manifest"]
    assert manifest["pages"][0]["method"] == "text"
    assert manifest["pages"][1]["method"] == "unavailable"
    assert manifest["pages"][1]["characters"] == 0
    assert not manifest["source_coverage_complete"]


def test_mixed_scan_ocr_requires_explicit_prefetched_artifacts(tmp_path, monkeypatch):
    path = mixed_pdf(tmp_path)
    monkeypatch.setenv("DOXA_ENABLE_OCR", "true")
    monkeypatch.delenv("DOXA_DOCLING_ARTIFACTS", raising=False)
    monkeypatch.setattr("doxa.extraction.importlib.util.find_spec", lambda name: object())
    # Reaching a Docling import without artifacts would be a policy regression.
    monkeypatch.setitem(sys.modules, "docling.document_converter", None)
    result = extract_document(path)
    assert "no models were downloaded" in " ".join(result["extraction_warnings"])
    assert result["extractor"] == "pdfminer-six"
    assert result["extraction_manifest"]["pages"][0]["needs_review"]


def test_explicit_ocr_preserves_selectable_header_and_declares_recognition_uncertainty(tmp_path, monkeypatch):
    path = mixed_pdf(tmp_path)
    artifacts = tmp_path / "prefetched"
    artifacts.mkdir()
    monkeypatch.setenv("DOXA_ENABLE_OCR", "1")
    monkeypatch.setenv("DOXA_DOCLING_ARTIFACTS", str(artifacts))
    monkeypatch.setattr("doxa.extraction.importlib.util.find_spec", lambda name: object())
    captured = {}

    class PipelineOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.ocr_options = SimpleNamespace(force_full_page_ocr=False, download_enabled=True)
            captured["ocr_options"] = self.ocr_options

    class Converter:
        def __init__(self, **kwargs):
            captured["converter"] = kwargs

        def convert(self, actual_path):
            assert actual_path == path
            items = [SimpleNamespace(text="Selectable header\n", prov=[SimpleNamespace(page_no=1)]),
                     SimpleNamespace(text="Recovered raster body evidence.", prov=[SimpleNamespace(page_no=1)])]
            return SimpleNamespace(document=SimpleNamespace(iterate_items=lambda: ((item, 0) for item in items)))

    converter_module = ModuleType("docling.document_converter")
    converter_module.DocumentConverter = Converter
    converter_module.PdfFormatOption = lambda **kwargs: kwargs
    base_module = ModuleType("docling.datamodel.base_models")
    base_module.InputFormat = SimpleNamespace(PDF="pdf")
    options_module = ModuleType("docling.datamodel.pipeline_options")
    options_module.PdfPipelineOptions = PipelineOptions
    for name, module in (("docling.document_converter", converter_module),
                         ("docling.datamodel.base_models", base_module),
                         ("docling.datamodel.pipeline_options", options_module)):
        monkeypatch.setitem(sys.modules, name, module)
    result = extract_document(path)
    texts = [block["text"] for block in result["blocks"]]
    assert sum("Selectable header" in text for text in texts) == 1
    assert "Recovered raster body evidence." in texts
    assert captured["enable_remote_services"] is False
    assert captured["artifacts_path"] == Path(artifacts)
    assert captured["ocr_options"].force_full_page_ocr
    assert captured["ocr_options"].download_enabled is False
    manifest = result["extraction_manifest"]
    assert manifest["pages"][0]["method"] == "ocr"
    assert manifest["pages"][0]["characters"] == sum(map(len, texts))
    assert manifest["needs_review"] and not manifest["source_coverage_complete"]
    assert result["extractor"] == "pdfminer-six+docling-ocr"
