"""Synthetic PDFs and images for the format cases. Content is invented."""
import io
import math

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


STAMP_RED = (200, 30, 35)
BOLD_FONT = FONT.replace("arial.ttf", "arialbd.ttf")


def round_stamp(diameter: int, ring_text: str, center_text: str) -> Image.Image:
    """A red company stamp: two rings, text around the outer one, a word in the middle."""
    img = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = diameter // 2
    draw.ellipse((4, 4, diameter - 4, diameter - 4), outline=STAMP_RED, width=max(6, diameter // 45))
    inner = int(r * 0.62)
    draw.ellipse((r - inner, r - inner, r + inner, r + inner), outline=STAMP_RED, width=max(3, diameter // 90))
    ring_font = ImageFont.truetype(BOLD_FONT, max(14, diameter // 14))
    for i, char in enumerate(ring_text):
        angle = -math.pi / 2 + 2 * math.pi * i / len(ring_text)
        glyph = Image.new("RGBA", (diameter // 8, diameter // 8), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((diameter // 32, diameter // 64), char, fill=STAMP_RED, font=ring_font)
        glyph = glyph.rotate(-math.degrees(angle) - 90, resample=Image.BICUBIC)
        x, y = r + r * 0.80 * math.cos(angle), r + r * 0.80 * math.sin(angle)
        img.alpha_composite(glyph, (int(x - glyph.width / 2), int(y - glyph.height / 2)))
    center_font = ImageFont.truetype(BOLD_FONT, max(18, diameter // 7))
    width = draw.textlength(center_text, font=center_font)
    draw.text((r - width / 2, r - diameter // 14), center_text, fill=STAMP_RED, font=center_font)
    return img.filter(ImageFilter.GaussianBlur(0.6))  # ink edges, as on a scan


def square_stamp(width: int, height: int, lines: list[str]) -> Image.Image:
    """A red rectangular stamp (approval or title stamp): a double frame around a few lines."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((3, 3, width - 4, height - 4), outline=STAMP_RED, width=6)
    draw.rectangle((14, 14, width - 15, height - 15), outline=STAMP_RED, width=2)
    font = ImageFont.truetype(BOLD_FONT, max(16, height // (len(lines) + 2)))
    step = (height - 30) / len(lines)
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        draw.text(((width - w) / 2, 15 + i * step + step * 0.15), line, fill=STAMP_RED, font=font)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def faded(stamp: Image.Image, opacity: float) -> Image.Image:
    """The same stamp printed with less ink."""
    alpha = stamp.getchannel("A").point(lambda a: int(a * opacity))
    out = stamp.copy()
    out.putalpha(alpha)
    return out


def scan_page(title: str, lines: list[str], stamp: Image.Image | None = None, at=(1000, 1650),
              rotate: float = 0, extra=None) -> Image.Image:
    """A scanned A4 page as an image: a title, body lines, a signature block and an
    optional stamp (rotated by `rotate` degrees, its top-left corner at `at`).
    `extra(draw, page)` adds anything else, e.g. a decoy for a negative case."""
    page = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(page)
    draw.text((500, 160), title, fill="black", font=ImageFont.truetype(BOLD_FONT, 44))
    body = ImageFont.truetype(FONT, 34)
    for i, line in enumerate(lines):
        draw.text((150, 320 + i * 70), line, fill="black", font=body)
    draw.text((1050, 1500), "ĐẠI DIỆN BÊN GIAO", fill="black", font=body)
    if extra:
        extra(draw, page)
    if stamp is not None:
        if rotate:
            stamp = stamp.rotate(rotate, resample=Image.BICUBIC, expand=True)
        page.paste(stamp, at, stamp)
    return page


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def write_page_pdf(path: str, page: Image.Image):
    """The page as a scanned PDF: image only, no text layer."""
    pdf = FPDF()
    pdf.add_page()
    pdf.image(io.BytesIO(_png(page)), x=0, y=0, w=210)
    pdf.output(path)


def write_page_png(path: str, page: Image.Image, scale: float = 0.6):
    """The page as a phone photo or scanner PNG (downscaled like a typical upload)."""
    page.resize((int(page.width * scale), int(page.height * scale)), Image.LANCZOS).save(path, "PNG")


def page_png_bytes(page: Image.Image, scale: float = 0.6) -> bytes:
    return _png(page.resize((int(page.width * scale), int(page.height * scale)), Image.LANCZOS))


def write_stamped_scan(path: str, title: str, lines: list[str], stamp: Image.Image):
    """A scanned A4 page (image only, no text layer) with a signature block and a stamp on it."""
    write_page_pdf(path, scan_page(title, lines, stamp))
