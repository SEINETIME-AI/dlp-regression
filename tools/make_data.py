"""Generates the synthetic documents in data/. Content is invented.

Each file only carries the trigger words of the rules its case is about (see the
suite's setup.yaml), and no two files share content, so the agent's per-content
cache never answers for another case. Existing files are kept (their bytes would
change on every run); pass --force to rewrite them all.

  python tools/make_data.py [--force]
"""
import sys
from pathlib import Path

from make_docx import write_docx
from make_media import text_image, write_png, write_scanned_pdf, write_text_pdf

DATA = Path(__file__).resolve().parent.parent / "data"

FILES = {
    "classification/01-higher-secrecy-wins/thong-bao.docx": lambda p: write_docx(p, [
        "Thông báo nội bộ, tuyệt mật — không phát tán ra ngoài.",
        "Kế hoạch tổ chức lại khối văn phòng (dữ liệu giả lập).",
    ]),
    "classification/02-multi-domain/tong-hop.docx": lambda p: write_docx(p, [
        "Tổng hợp lưu hành nội bộ.",
        "Phiếu chi văn phòng phẩm và bảng lương khối hành chính (dữ liệu giả lập).",
    ]),
    "classification/03-no-match-is-public/lich-hop.docx": lambda p: write_docx(p, [
        "Lịch họp giao ban tuần tới tại phòng họp tầng hai.",
        "Mời các trưởng nhóm tham dự đúng giờ (dữ liệu giả lập).",
    ]),
    # Format cases: the same rules, reached through each format's extraction path.
    "classification/04-pdf-text-layer/bao-cao.pdf": lambda p: write_text_pdf(p, [
        "Báo cáo lưu hành nội bộ.",
        "Kèm phiếu chi mua thiết bị văn phòng (dữ liệu giả lập).",
    ]),
    "classification/05-pdf-scanned/ban-scan.pdf": lambda p: write_scanned_pdf(p, [
        "tài liệu tuyệt mật",
        "không sao chép",
    ]),
    "classification/06-image-png/anh-chup.png": lambda p: write_png(p, [
        "bảng lương phòng hành chính",
    ]),
    "classification/07-docx-embedded-image/bien-ban.docx": lambda p: write_docx(
        p, ["Biên bản giao nhận thiết bị (dữ liệu giả lập)."],
        media={"image1.png": text_image(["lưu hành nội bộ"])}),
}

if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    for relative, write in FILES.items():
        target = DATA / relative
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write(str(target))
        print("wrote", target.relative_to(DATA.parent))
