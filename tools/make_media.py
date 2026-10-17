"""Synthetic PDFs and images for the format cases. Content is invented."""
import io

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

FONT = r"C:\Windows\Fonts\arial.ttf"  # has the Vietnamese glyphs


def text_image(lines: list[str], size=(1400, 360)) -> bytes:
    """Black text on white, large enough for OCR (the agent skips images under 150x150)."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 64)
    y = 50
    for line in lines:
        draw.text((60, y), line, fill="black", font=font)
        y += 110
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def write_png(path: str, lines: list[str]):
    with open(path, "wb") as f:
        f.write(text_image(lines))


def write_text_pdf(path: str, paragraphs: list[str]):
    """A PDF with a real text layer (embedded TrueType font with Unicode mapping)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("Arial", fname=FONT)
    pdf.set_font("Arial", size=14)
    for paragraph in paragraphs:
        pdf.multi_cell(0, 10, paragraph)
        pdf.ln(4)
    pdf.output(path)


def write_scanned_pdf(path: str, lines: list[str]):
    """A PDF whose only content is a picture of text: no text layer, so it needs OCR."""
    pdf = FPDF()
    pdf.add_page()
    pdf.image(io.BytesIO(text_image(lines, size=(1600, 500))), x=10, y=20, w=190)
    pdf.output(path)
