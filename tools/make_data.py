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
from make_pptx import write_pptx
from make_broken import write_corrupt_docx, write_password_docx
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
    # Each trigger sits in a different place a deck keeps text: body, table, notes.
    "classification/08-pptx-slides/trinh-chieu.pptx": lambda p: write_pptx(p, [
        ("Kế hoạch quý tới", ["Tổng hợp phiếu chi văn phòng (dữ liệu giả lập)."], []),
        ("Phân bổ nhân sự", [], [["Hạng mục", "Ghi chú"], ["Bảng lương", "Khối kỹ thuật"]]),
    ], notes="Tài liệu lưu hành nội bộ."),
    # Files the agent cannot read: it must report them confidential, never public.
    # Their text would match uat-finance if it could be read.
    "classification/09-docx-password/hop-dong-khoa.docx": lambda p: write_password_docx(p, [
        "Hợp đồng kèm phiếu chi tạm ứng (dữ liệu giả lập).",
    ]),
    "classification/10-docx-corrupt/bien-ban-hong.docx": lambda p: write_corrupt_docx(p, [
        "Biên bản đối chiếu phiếu chi tháng trước (dữ liệu giả lập).",
    ]),

    "dictionary/01-own-threshold-wins/kho-van.docx": lambda p: write_docx(p, [
        "Đã lập phiếu xuất kho cho lô vật tư tuần này.",
        "Kèm biên nhận của bộ phận giao hàng (dữ liệu giả lập).",
    ]),
    "dictionary/02-one-term-below/bien-nhan.docx": lambda p: write_docx(p, [
        "Bộ phận lễ tân đã ký biên nhận bưu phẩm (dữ liệu giả lập).",
    ]),
    "hybrid/01-keyword-and-pattern/ho-so.docx": lambda p: write_docx(p, [
        "Hồ sơ khởi động, mã dự án DA-KHCN.",
        "Phạm vi: chuẩn hóa quy trình tiếp nhận yêu cầu (dữ liệu giả lập).",
    ]),
    "hybrid/02-keyword-without-pattern/ghi-chu.docx": lambda p: write_docx(p, [
        "Ghi chú: mã dự án sẽ được cấp sau buổi duyệt.",
        "Nhóm phụ trách cập nhật lại kế hoạch tuần (dữ liệu giả lập).",
    ]),
    "hybrid/03-wordlist-reaches-threshold/ha-tang.docx": lambda p: write_docx(p, [
        "Bản cập nhật sơ đồ mạng tầng ba.",
        "Cấu hình tường lửa mới áp dụng từ tuần sau (dữ liệu giả lập).",
    ]),
    "hybrid/04-wordlist-below-threshold/thiet-bi.docx": lambda p: write_docx(p, [
        "Danh mục thiết bị: một máy chủ dự phòng đặt tại kho.",
        "Kiểm kê định kỳ theo lịch của phòng (dữ liệu giả lập).",
    ]),
    "hybrid/05-pdf-both-rules/tong-hop.pdf": lambda p: write_text_pdf(p, [
        "Tổng hợp mã dự án DA-HTMG.",
        "Hạng mục: thay máy chủ và tường lửa (dữ liệu giả lập).",
    ]),
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
