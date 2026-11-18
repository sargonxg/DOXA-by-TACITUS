"""Small permissively licensed PDF inspection helpers used by publication tools.

The reader is independent of pdfminer extraction and never executes embedded
PDF actions. Raster previews are an explicit optional publication dependency.
"""
from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


class PDFPage:
    def __init__(self, page):
        self.page = page

    def get_text(self):
        return self.page.extract_text() or ""

    def get_links(self):
        links = []
        for reference in self.page.get("/Annots", []):
            annotation = reference.get_object()
            action = annotation.get("/A")
            if action:
                action = action.get_object()
                if action.get("/S") == "/URI" and action.get("/URI"):
                    links.append({"uri": str(action["/URI"])})
        return links


class PDFDocument:
    def __init__(self, source):
        self.reader = PdfReader(source, strict=False)
        if self.reader.is_encrypted:
            self.reader.close()
            raise ValueError("PDF is encrypted; inspect an unlocked copy.")

    def __len__(self):
        return len(self.reader.pages)

    def __iter__(self):
        return (PDFPage(page) for page in self.reader.pages)

    def __getitem__(self, number):
        return PDFPage(self.reader.pages[number])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.reader.close()


def open_pdf(path=None, *, stream=None, filetype=None):
    if filetype not in (None, "pdf"):
        raise ValueError("Only PDF inspection is supported")
    if (path is None) == (stream is None):
        raise ValueError("Select exactly one PDF path or byte stream")
    return PDFDocument(BytesIO(stream) if stream is not None else path)


def render_pdf_preview(path, page_number, destination, *, scale=1.15):
    """Render an explicit local PDF page without network access."""
    try:
        import pypdfium2
    except ImportError as exc:
        raise ValueError("PDF preview generation needs the optional publication extra: pip install '.[publication]'") from exc
    with pypdfium2.PdfDocument(str(path)) as document:
        page = document[page_number]
        try:
            bitmap = page.render(scale=scale)
            try:
                bitmap.to_pil().save(destination)
            finally:
                bitmap.close()
        finally:
            page.close()
