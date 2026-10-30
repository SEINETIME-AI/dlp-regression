"""Generates the large-file performance corpus in data/perf-corpus/ (git-ignored).

Text, PDF (text layer), PDF (scanned images), PDF (scanned contract pages, every
other one with a red round stamp), DOCX and XLSX files at the requested sizes, a 25 MB file whose matches sit across the agent's 5 MiB
two-pointer chunk seams (boundary-25mb.expect.txt lists them), and a sparse
50 GB file. Everything is streamed, so a 2 GB file needs a few MB of RAM.
Same seed, same files. Existing files are kept, so a stopped run can be resumed.

  python tools/make_corpus.py                               # 1M, 20M, 200M, 2G (about 11 GB)
  python tools/make_corpus.py --sizes 1M,20M,200M           # about 1.2 GB
  python tools/make_corpus.py --types text,docx --no-huge

Time one file with the agent build:
  nextdlp.exe classify data/perf-corpus/text-20mb.txt --timing --repeat 5
"""

import argparse
import io
import os
import random
import subprocess
import sys
import zipfile
from pathlib import Path

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

# Same as KeywordClassifier::kChunkBytes: the boundary file puts matches across these seams.
CHUNK_BYTES = 5 * MIB

WORDS_VI = ("hợp đồng bảo mật tài chính ngân hàng số tài khoản khách hàng báo cáo doanh thu "
            "lương nhân sự mật khẩu nội bộ kế hoạch chiến lược dự án hồ sơ pháp lý").split()
WORDS_ASCII = ("hop dong bao mat tai chinh ngan hang so tai khoan khach hang bao cao doanh thu "
               "luong nhan su mat khau noi bo ke hoach chien luoc du an ho so phap ly").split()


def parse_size(text):
    units = {"K": KIB, "M": MIB, "G": GIB}
    text = text.strip().upper().rstrip("B")
    return int(float(text[:-1]) * units[text[-1]]) if text[-1] in units else int(text)


def size_label(size):
    return f"{size // GIB}gb" if size >= GIB else f"{size // MIB}mb" if size >= MIB else f"{size // KIB}kb"


def cccd(rng):
    return "0" + "".join(rng.choice("0123456789") for _ in range(11))


def sentence(rng, words):
    """One line of filler, with a sensitive value in about one line out of eight."""
    line = " ".join(rng.choice(words) for _ in range(rng.randint(8, 16)))
    if rng.random() < 0.125:
        line += f" CCCD {cccd(rng)} STK {rng.randint(10**11, 10**12 - 1)}"
    return line


def text_block(rng, words, size):
    """About `size` bytes of lines. Fresh random lines keep deflate from shrinking files too far."""
    lines, total = [], 0
    while total < size:
        line = sentence(rng, words)
        lines.append(line)
        total += len(line.encode("utf-8")) + 1
    return lines


def progress(path, done, target):
    sys.stdout.write(f"\r  {os.path.basename(path)}: {done / MIB:,.0f} / {target / MIB:,.0f} MB")
    sys.stdout.flush()


# ── Plain text ───────────────────────────────────────────────────────────────

def write_text(path, size, rng):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        written = 0
        while written < size:
            chunk = "\n".join(text_block(rng, WORDS_VI, 256 * KIB)) + "\n"
            f.write(chunk)
            written += len(chunk.encode("utf-8"))
            if written % (64 * MIB) < 256 * KIB:
                progress(path, written, size)


def write_boundary(path, size, rng):
    """Text whose matches sit across the 5 MiB chunk seams; the .expect.txt file lists them."""
    probes = [
        ("cccd", "CCCD 079012345678"),
        ("keyword", "MATKHAU_RANHGIOI_NOIBO"),
        ("utf8", "bảo mật tuyệt đối"),
    ]
    expect = []
    with open(path, "wb") as f:
        seam = CHUNK_BYTES
        index = 0
        while seam < size:
            probe_name, probe = probes[index % len(probes)]
            data = probe.encode("utf-8")
            # Split the probe in the middle, on the seam.
            start = seam - len(data) // 2
            while f.tell() < start:
                line = (sentence(rng, WORDS_VI) + "\n").encode("utf-8")
                gap = start - f.tell()
                if len(line) > gap:
                    line = b"x" * (gap - 1) + b"\n" if gap > 1 else b" " * gap
                f.write(line)
            f.write(data + b"\n")
            expect.append(f"{start}\t{seam}\t{probe_name}\t{probe}")
            seam += CHUNK_BYTES
            index += 1
        while f.tell() < size:
            f.write((sentence(rng, WORDS_VI) + "\n").encode("utf-8"))
    with open(os.path.splitext(path)[0] + ".expect.txt", "w", encoding="utf-8") as e:
        e.write("# start_byte\tseam_byte\tkind\ttext  (every probe must be found)\n")
        e.write("\n".join(expect) + "\n")


def write_huge_sparse(path, size, rng):
    """A sparse file: real text at both ends, zeros (no disk space) in between."""
    head = ("\n".join(text_block(rng, WORDS_VI, 4 * MIB)) + "\n").encode("utf-8")
    tail = ("\n".join(text_block(rng, WORDS_VI, 4 * MIB)) + "\n").encode("utf-8")
    open(path, "wb").close()
    result = subprocess.run(["fsutil", "sparse", "setflag", path], capture_output=True, text=True)
    if result.returncode != 0:
        os.remove(path)
        raise RuntimeError(f"fsutil sparse setflag failed: {result.stdout or result.stderr}")
    with open(path, "r+b") as f:
        f.write(head)
        f.seek(size - len(tail))
        f.write(tail)


# ── OOXML ────────────────────────────────────────────────────────────────────

CONTENT_TYPES_DOCX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS_DOCX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def xml_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_docx(path, size, rng):
    raw = open(path, "wb")  # zipfile streams into it, so tell() is the size so far
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES_DOCX)
        z.writestr("_rels/.rels", RELS_DOCX)
        with z.open("word/document.xml", "w", force_zip64=True) as doc:
            doc.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                      b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>')
            while raw.tell() < size:
                paragraphs = "".join(f"<w:p><w:r><w:t>{xml_escape(line)}</w:t></w:r></w:p>"
                                     for line in text_block(rng, WORDS_VI, 256 * KIB))
                doc.write(paragraphs.encode("utf-8"))
                progress(path, raw.tell(), size)
            doc.write(b"</w:body></w:document>")
    raw.close()


CONTENT_TYPES_XLSX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{sheets}
</Types>"""

RELS_XLSX = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

MAX_ROWS_PER_SHEET = 1_000_000  # Excel stops at 1,048,576


def write_xlsx(path, size, rng):
    raw = open(path, "wb")  # zipfile streams into it, so tell() is the size so far
    sheet_count = 0
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        while raw.tell() < size:
            sheet_count += 1
            with z.open(f"xl/worksheets/sheet{sheet_count}.xml", "w", force_zip64=True) as sheet:
                sheet.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                            b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
                row = 0
                while raw.tell() < size and row < MAX_ROWS_PER_SHEET:
                    rows = []
                    for _ in range(2000):
                        row += 1
                        text = xml_escape(" ".join(rng.choice(WORDS_VI) for _ in range(rng.randint(3, 8))))
                        rows.append(f'<row r="{row}"><c r="A{row}" t="inlineStr"><is><t>{text}</t></is></c>'
                                    f'<c r="B{row}"><v>{cccd(rng)}</v></c>'
                                    f'<c r="C{row}"><v>{rng.randint(1, 10**9)}</v></c></row>')
                    sheet.write("".join(rows).encode("utf-8"))
                    progress(path, raw.tell(), size)
                sheet.write(b"</sheetData></worksheet>")
        sheets = "".join(f'<sheet name="Sheet{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, sheet_count + 1))
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f"<sheets>{sheets}</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
                             f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, sheet_count + 1))
                   + "</Relationships>")
        z.writestr("[Content_Types].xml", CONTENT_TYPES_XLSX.format(sheets="".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, sheet_count + 1))))
        z.writestr("_rels/.rels", RELS_XLSX)
    raw.close()


# ── PDF ──────────────────────────────────────────────────────────────────────

class PdfWriter:
    """Minimal streaming PDF: objects are written as they come, the page tree and xref at the end."""

    def __init__(self, path):
        self.f = open(path, "wb")
        self.offsets = {}
        self.page_ids = []
        self.next_id = 4  # 1 catalog, 2 page tree, 3 font
        self.f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    def obj(self, oid, body, stream=None):
        self.offsets[oid] = self.f.tell()
        self.f.write(f"{oid} 0 obj\n".encode())
        if stream is None:
            self.f.write(body.encode() + b"\nendobj\n")
        else:
            self.f.write(body.encode() + f"\nstream\n".encode() + stream + b"\nendstream\nendobj\n")

    def add_page(self, content, resources):
        content_id, page_id = self.new_id(), self.new_id()
        self.obj(content_id, f"<< /Length {len(content)} >>", content)
        self.obj(page_id, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                          f"/Resources {resources} /Contents {content_id} 0 R >>")
        self.page_ids.append(page_id)

    def close(self):
        self.obj(3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        kids = " ".join(f"{p} 0 R" for p in self.page_ids)
        self.obj(2, f"<< /Type /Pages /Count {len(self.page_ids)} /Kids [{kids}] >>")
        self.obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
        xref = self.f.tell()
        count = self.next_id
        self.f.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
        for oid in range(1, count):
            self.f.write(f"{self.offsets.get(oid, 0):010d} 00000 n \n".encode())
        self.f.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        self.f.close()


def write_pdf_text(path, size, rng):
    pdf = PdfWriter(path)
    while pdf.f.tell() < size:
        lines = [sentence(rng, WORDS_ASCII) for _ in range(70)]
        ops = ["BT /F1 8 Tf 40 810 Td 11 TL"] + [f"({line}) '" for line in lines] + ["ET"]
        pdf.add_page("\n".join(ops).encode("latin-1"), "<< /Font << /F1 3 0 R >> >>")
        if len(pdf.page_ids) % 2000 == 0:
            progress(path, pdf.f.tell(), size)
    pdf.close()


def scan_images(rng, count=4):
    """A few page photos of text (JPEG), reused across pages."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    images = []
    for _ in range(count):
        page = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(page)
        y = 80
        while y < 1680:
            draw.text((80, y), sentence(rng, WORDS_VI), fill=0, font=font)
            y += 40
        buf = io.BytesIO()
        page.save(buf, "JPEG", quality=80)
        images.append(buf.getvalue())
    return images


def write_pdf_scan(path, size, rng):
    images = scan_images(rng)
    pdf = PdfWriter(path)
    while pdf.f.tell() < size:
        jpeg = images[len(pdf.page_ids) % len(images)]
        image_id = pdf.new_id()
        pdf.obj(image_id, f"<< /Type /XObject /Subtype /Image /Width 1240 /Height 1754 /ColorSpace /DeviceGray "
                          f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>", jpeg)
        pdf.add_page(b"q 595 0 0 842 0 0 cm /Im0 Do Q", f"<< /XObject << /Im0 {image_id} 0 R >> >>")
        if len(pdf.page_ids) % 200 == 0:
            progress(path, pdf.f.tell(), size)
    pdf.close()


def stamped_pages(rng):
    """Scanned A4 contract pages (JPEG, colour): every other one carries a red round stamp,
    each at its own angle, so the stamp detector runs on pages with and without one."""
    from make_media import round_stamp, scan_page
    stamp = round_stamp(360, "CÔNG TY TNHH THỬ NGHIỆM UAT * HÀ NỘI * ", "MẬT")
    pages = []
    for i, rotate in enumerate((0, None, 12, None, -20, None)):
        lines = [sentence(rng, WORDS_VI)[:70] for _ in range(14)]
        page = scan_page(f"HỢP ĐỒNG SỐ {i + 1:02d}/UAT", lines,
                         stamp if rotate is not None else None, rotate=rotate or 0)
        buf = io.BytesIO()
        page.save(buf, "JPEG", quality=75)
        pages.append((buf.getvalue(), page.size, rotate is not None))
    return pages


def write_pdf_stamp(path, size, rng):
    pages = stamped_pages(rng)
    pdf = PdfWriter(path)
    stamped = 0
    while pdf.f.tell() < size:
        jpeg, (width, height), has_stamp = pages[len(pdf.page_ids) % len(pages)]
        stamped += has_stamp
        image_id = pdf.new_id()
        pdf.obj(image_id, f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
                          f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>", jpeg)
        pdf.add_page(b"q 595 0 0 842 0 0 cm /Im0 Do Q", f"<< /XObject << /Im0 {image_id} 0 R >> >>")
        if len(pdf.page_ids) % 100 == 0:
            progress(path, pdf.f.tell(), size)
    pdf.close()
    name = os.path.basename(path).removesuffix(".partial")
    print(f"\r  {name}: {len(pdf.page_ids)} pages, {stamped} with a stamp" + " " * 20)


# ── Main ─────────────────────────────────────────────────────────────────────

WRITERS = {
    "text": ("text-{}.txt", write_text),
    "pdf": ("pdf-text-{}.pdf", write_pdf_text),
    "pdf-scan": ("pdf-scan-{}.pdf", write_pdf_scan),
    "pdf-stamp": ("pdf-stamp-{}.pdf", write_pdf_stamp),
    "docx": ("docx-{}.docx", write_docx),
    "xlsx": ("xlsx-{}.xlsx", write_xlsx),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent / "data" / "perf-corpus"))
    parser.add_argument("--sizes", default="1M,20M,200M,2G", help="comma list, e.g. 1M,20M,200M,2G")
    parser.add_argument("--types", default=",".join(WRITERS), help="comma list of " + ", ".join(WRITERS))
    parser.add_argument("--no-boundary", action="store_true", help="skip the 25 MB chunk-seam file")
    parser.add_argument("--no-huge", action="store_true", help="skip the sparse 50 GB file")
    parser.add_argument("--huge-size", default="50G")
    parser.add_argument("--seed", type=int, default=20261030, help="same seed, same files")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    jobs = []
    for size in map(parse_size, args.sizes.split(",")):
        for kind in args.types.split(","):
            name, writer = WRITERS[kind]
            jobs.append((name.format(size_label(size)), writer, size))
    if not args.no_boundary:
        jobs.append(("boundary-25mb.txt", write_boundary, 25 * MIB))
    if not args.no_huge:
        size = parse_size(args.huge_size)
        jobs.append((f"huge-{size_label(size)}-sparse.txt", write_huge_sparse, size))

    for name, writer, size in jobs:
        path = os.path.join(args.out_dir, name)
        if os.path.exists(path):
            print(f"  {name}: exists, kept")
            continue
        rng = random.Random(f"{args.seed}:{name}")
        tmp = path + ".partial"
        try:
            writer(tmp, size, rng)
            os.replace(tmp, path)
            if os.path.exists(os.path.splitext(tmp)[0] + ".expect.txt"):
                os.replace(os.path.splitext(tmp)[0] + ".expect.txt", os.path.splitext(path)[0] + ".expect.txt")
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        print(f"\r  {name}: {os.path.getsize(path) / MIB:,.1f} MB" + " " * 20)


if __name__ == "__main__":
    main()
