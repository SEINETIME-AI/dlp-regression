"""Large plain-text files, written at run time instead of being committed (tens of MB).

Neutral log lines fill the file up to `size_mb`; the trigger line goes in after
`trigger_after_mb` megabytes (or right at the end when that is None). `first_line`,
when given, opens the file, so a rule needing two signals sees one in each half.
"""
import os

from fpdf import FPDF
from PIL import Image

MB = 1024 * 1024
FONT = r"C:\Windows\Fonts\arial.ttf"


def write_large_text(path: str, size_mb: float, trigger: str, trigger_after_mb: float | None, seed: str,
                     first_line: str | None = None):
    target = int(size_mb * MB)
    trigger_at = target - len(trigger.encode("utf-8")) - 200 if trigger_after_mb is None else int(trigger_after_mb * MB)
    written = 0
    placed = False
    line_number = 0
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        if first_line:
            out.write(f"{first_line}\n")
            written += len(first_line.encode("utf-8")) + 1
        while written < target:
            if not placed and written >= trigger_at:
                line = f"{trigger}\n"
                placed = True
            else:
                line_number += 1
                line = f"[{seed}] dòng nhật ký {line_number}: tác vụ sao lưu định kỳ hoàn tất, không có cảnh báo.\n"
            out.write(line)
            written += len(line.encode("utf-8"))


def write_large_pdf(path: str, first_page: str, last_page: str, size_mb: float):
    """A PDF whose first and last pages carry text; a noise image (it does not
    compress) on the page between them brings the file to `size_mb`."""
    side = int((size_mb * MB / 3) ** 0.5)
    noise = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    pdf = FPDF()
    pdf.add_font("Arial", fname=FONT)
    pdf.set_font("Arial", size=14)
    for text in (first_page, None, last_page):
        pdf.add_page()
        if text is None:
            pdf.image(noise, x=10, y=10, w=190)
        else:
            pdf.multi_cell(0, 10, text)
    pdf.output(path)

