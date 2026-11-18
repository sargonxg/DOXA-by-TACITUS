"""Synthetic PDF fixtures built with ReportLab, inspected with pypdf."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


def pdf_bytes(pages, *, password=None):
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=(595, 842), invariant=True)
    for text in pages:
        if text:
            document.drawString(72, 770, text)
        document.showPage()
    document.save()
    if password is None:
        return output.getvalue()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(output.getvalue())))
    writer.encrypt(password, owner_password="owner", algorithm="AES-256")
    encrypted = BytesIO()
    writer.write(encrypted)
    return encrypted.getvalue()


def mixed_pdf_bytes():
    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    image = Image.new("RGB", (500, 620), "white")
    ImageDraw.Draw(image).text((35, 45), "The body is rasterized source evidence.", fill="black")
    output = BytesIO()
    document = canvas.Canvas(output, pagesize=(595, 842), invariant=True)
    document.drawString(72, 777, "Selectable header")
    document.drawImage(ImageReader(image), 35, 82, width=525, height=650)
    document.save()
    return output.getvalue()
