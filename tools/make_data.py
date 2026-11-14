"""Generates the synthetic documents in data/. Content is invented.

Each file only carries the trigger words of the rules its case is about (see the
suite's setup.yaml), and no two files share content, so the agent's per-content
cache never answers for another case. Existing files are kept (their bytes would
change on every run); pass --force to rewrite them all.

  python tools/make_data.py [--force]
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageFont

from make_docx import write_docx
from make_pptx import write_pptx
from make_xlsx import write_xlsx
from make_large import write_large_pdf, write_large_scan_pdf, write_large_text
from make_broken import write_corrupt_docx, write_password_docx
from make_media import (BOLD_FONT, FONT, STAMP_RED, faded, page_png_bytes, round_stamp, scan_page, square_stamp,
                        text_image, write_page_pdf, write_page_png, write_png, write_scanned_pdf,
                        write_stamped_scan, write_text_pdf)

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
    "classification/03-no-match-is-unlabelled/lich-hop.docx": lambda p: write_docx(p, [
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

    "classification/11-own-threshold-wins/kho-van.docx": lambda p: write_docx(p, [
        "Đã lập phiếu xuất kho cho lô vật tư tuần này.",
        "Kèm biên nhận của bộ phận giao hàng (dữ liệu giả lập).",
    ]),
    "classification/12-one-term-below/bien-nhan.docx": lambda p: write_docx(p, [
        "Bộ phận lễ tân đã ký biên nhận bưu phẩm (dữ liệu giả lập).",
    ]),
    # Every condition of every mode policy matches this one.
    "mode/01-each-policy-its-own-technique/dien-tap.docx": lambda p: write_docx(p, [
        "Kế hoạch diễn tập phòng cháy chữa cháy và hướng dẫn thoát hiểm tại tòa nhà văn phòng.",
        "Buổi diễn tập phòng cháy kiểm tra bình chữa cháy và lối thoát hiểm ở mỗi tầng (dữ liệu giả lập).",
    ]),
    "mode/02-other-text-matches-nothing/thuc-don.docx": lambda p: write_docx(p, [
        "Thực đơn bữa trưa tuần sau của căng tin gồm cơm gà, canh rau và trái cây (dữ liệu giả lập).",
    ]),
    # Read and labelled through an SMB share.
    "smb/01-labelled-over-smb/kiem-ke.docx": lambda p: write_docx(p, [
        "Biên bản kiểm kê kho hồ sơ quý ba tại chi nhánh phía nam.",
        "Số thùng hồ sơ đã đối chiếu khớp với sổ theo dõi (dữ liệu giả lập).",
    ]),
    "smb/02-no-match-over-smb/cay-xanh.docx": lambda p: write_docx(p, [
        "Lịch tưới cây xanh ở sảnh và ban công các tầng trong tháng tới (dữ liệu giả lập).",
    ]),
    # Customer test cases (TC-…); triggers stay off the real policy and the other rules.
    "classification/18-and-both-keywords/dieu-dong.docx": lambda p: write_docx(p, [
        "Lệnh điều động xe công vụ cho đoàn công tác tuần sau (dữ liệu giả lập).",
    ]),
    "classification/19-and-one-keyword-missing/dieu-dong-thieu.docx": lambda p: write_docx(p, [
        "Lệnh điều động máy xúc ra công trường số hai (dữ liệu giả lập).",
    ]),
    "classification/20-min-count-not-reached/chung-chi-mot.docx": lambda p: write_docx(p, [
        "Hồ sơ dự tuyển kèm một chứng chỉ ngoại ngữ (dữ liệu giả lập).",
    ]),
    "classification/21-min-count-reached/chung-chi-hai.docx": lambda p: write_docx(p, [
        "Chứng chỉ tin học đã nộp đủ.",
        "Chứng chỉ ngoại ngữ sẽ bổ sung trong tuần (dữ liệu giả lập).",
    ]),
    "classification/22-regex-matcher/ma-nhan-vien.docx": lambda p: write_docx(p, [
        "Mã nhân viên NV-20481 được cấp thẻ ra vào tầng năm (dữ liệu giả lập).",
    ]),
    "classification/23-semantic-paraphrase/cong-no.docx": lambda p: write_docx(p, [
        "Khách hàng đã hoàn tất nghĩa vụ thanh toán còn tồn đọng. "
        "Khoản công nợ đã được tất toán và không còn số dư phải thu.",
    ]),
    "classification/24-semantic-other-wording/tuyen-dung.docx": lambda p: write_docx(p, [
        "Ứng viên đã hoàn thành vòng phỏng vấn cuối cùng. "
        "Bộ phận tuyển dụng đang chuẩn bị thư mời và chế độ đãi ngộ cho vị trí mới.",
    ]),
    "classification/25-semantic-below-threshold/lich-hop-ky-thuat.docx": lambda p: write_docx(p, [
        "Lịch họp phòng kỹ thuật được chuyển sang 9 giờ sáng thứ Hai. Phòng họp số 3 đã được đặt trước.",
    ]),
    "classification/26-two-weak-terms-reach/nong-san.docx": lambda p: write_docx(p, [
        "Hợp tác xã ký kế hoạch thu mua nông sản vụ hè (dữ liệu giả lập).",
    ]),
    "classification/27-one-strong-term/kiem-ke.docx": lambda p: write_docx(p, [
        "Gửi kèm báo cáo kiểm kê kho cuối tháng (dữ liệu giả lập).",
    ]),
    "classification/28-repeated-term-counts-once/nong-san-lap.docx": lambda p: write_docx(p, [
        "Nông sản sấy khô, nông sản đông lạnh, nông sản tươi và nông sản chế biến "
        "đều được nhập về kho nông sản mới (dữ liệu giả lập).",
    ]),
    "classification/29-terms-far-apart/nong-san-xa.docx": lambda p: write_docx(p, [
        "Hợp tác xã gửi đề xuất mở rộng khu sơ chế.",
        *['Đoạn 1: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 2: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 3: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 4: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 5: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 6: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 7: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).', 'Đoạn 8: mô tả kế hoạch sắp xếp kho bãi, lịch vận chuyển và phân công trực ca (dữ liệu giả lập).'],
        "Phần cuối: danh mục nông sản dự kiến thu hoạch trong mùa tới.",
    ]),
    "classification/30-no-synonyms/nong-nghiep.docx": lambda p: write_docx(p, [
        "Tổ hợp tác thu mua hàng nông nghiệp và sản phẩm trồng trọt của bà con (dữ liệu giả lập).",
    ]),
    "classification/31-xlsx/bang-tinh.xlsx": lambda p: write_xlsx(p, [
        "Bảng lương khối văn phòng",
        "Tài liệu lưu hành nội bộ (dữ liệu giả lập)",
    ]),
    # TC-H-01: the stamp is the only signal; no keyword of the suite is on the page.
    "classification/32-scanned-stamp/ban-giao-co-dau.pdf": lambda p: write_stamped_scan(p, "BIÊN BẢN BÀN GIAO", [
        "Hôm nay, hai bên tiến hành bàn giao thiết bị văn phòng.",
        "Bên nhận đã kiểm tra số lượng và tình trạng thiết bị.",
        "Biên bản được lập thành hai bản có giá trị như nhau.",
        "(dữ liệu giả lập)",
    ], round_stamp(360, "CÔNG TY TNHH THỬ NGHIỆM UAT * HÀ NỘI * ", "MẬT")),
    # TC-H-03/H-04: too big to commit (see .gitignore); written at run time.
    "classification/33-large-text-12mb/nhat-ky-12mb.txt": lambda p: write_large_text(
        p, 12, "Ghi chú cuối: tài liệu tuyệt mật, không phát tán (dữ liệu giả lập).", 10.5, "A"),
    # TC-H-03 with the other techniques: one signal opens the file, the other ends it.
    "classification/35-large-text-dictionary/nhat-ky-tu-dien-22mb.txt": lambda p: write_large_text(
        p, 22, "Ghi chú cuối: thu mua nông sản vụ mùa (dữ liệu giả lập).", None, "C",
        first_line="Biên bản họp hợp tác xã thôn Đông (dữ liệu giả lập)."),
    "classification/36-large-text-hybrid/nhat-ky-du-an-22mb.txt": lambda p: write_large_text(
        p, 22, "Ghi chú cuối: hồ sơ DA-KHCN đã nộp (dữ liệu giả lập).", None, "D",
        first_line="Nhật ký mã dự án nghiên cứu (dữ liệu giả lập)."),
    # A PDF above 20 MB is read page by page from both ends.
    "classification/37-large-pdf-dictionary/bien-ban-22mb.pdf": lambda p: write_large_pdf(
        p, "Biên bản họp hợp tác xã thôn Bắc (dữ liệu giả lập).",
        "Phụ lục: kế hoạch thu mua nông sản vụ đông (dữ liệu giả lập).", 22),
    "classification/34-large-text-22mb/nhat-ky-22mb.txt": lambda p: write_large_text(
        p, 22, "Ghi chú cuối: tài liệu tuyệt mật, không phát tán (dữ liệu giả lập).", None, "B"),
    # Over the agent's 250 MB limit: only its ends are read, so the label rests on part of it.
    "classification/46-over-size-limit/nhat-ky-260mb.txt": lambda p: write_large_text(
        p, 260, "Ghi chú giữa tệp: không bao giờ được đọc (dữ liệu giả lập).", 130, "E",
        first_line="Nhật ký tuyệt mật, không phát tán (dữ liệu giả lập)."),
    # TC-E-07: starts with a secrecy code no taxonomy has; no rule matches its text.
    "classification/38-unknown-label-is-unlabelled/nhan-la.docx": lambda p: write_docx(p, [
        "Thông báo lịch bảo trì hệ thống điều hòa tầng ba vào sáng thứ bảy (dữ liệu giả lập).",
    # Spelled as the customer's tester typed it in Word: Office matches names without case.
    ], custom_properties={"NextDLP_secrecy": "top_secret", "NextDLP_Domains": "",
                          "NextDLP_ClassifiedAt": "2026-01-01T00:00:00Z"}),
    # A secrecy label created in the console for the run, above secret.
    "classification/39-new-secrecy-label/chi-thi.docx": lambda p: write_docx(p, [
        "Chỉ thị tối mật về lịch kiểm tra kho lưu trữ hồ sơ giấy (dữ liệu giả lập).",
    ]),
    "classification/39-new-secrecy-label/ke-hoach.docx": lambda p: write_docx(p, [
        "Kế hoạch tối mật, phụ lục tuyệt mật: lộ trình di dời văn phòng đại diện (dữ liệu giả lập).",
    ]),
    "classification/13-keyword-and-pattern/ho-so.docx": lambda p: write_docx(p, [
        "Hồ sơ khởi động, mã dự án DA-KHCN.",
        "Phạm vi: chuẩn hóa quy trình tiếp nhận yêu cầu (dữ liệu giả lập).",
    ]),
    "classification/14-keyword-without-pattern/ghi-chu.docx": lambda p: write_docx(p, [
        "Ghi chú: mã dự án sẽ được cấp sau buổi duyệt.",
        "Nhóm phụ trách cập nhật lại kế hoạch tuần (dữ liệu giả lập).",
    ]),
    "classification/15-wordlist-reaches-threshold/ha-tang.docx": lambda p: write_docx(p, [
        "Bản cập nhật sơ đồ mạng tầng ba.",
        "Cấu hình tường lửa mới áp dụng từ tuần sau (dữ liệu giả lập).",
    ]),
    "classification/16-wordlist-below-threshold/thiet-bi.docx": lambda p: write_docx(p, [
        "Danh mục thiết bị: một máy chủ dự phòng đặt tại kho.",
        "Kiểm kê định kỳ theo lịch của phòng (dữ liệu giả lập).",
    ]),
    "classification/17-pdf-both-rules/tong-hop.pdf": lambda p: write_text_pdf(p, [
        "Tổng hợp mã dự án DA-HTMG.",
        "Hạng mục: thay máy chủ và tường lửa (dữ liệu giả lập).",
    ]),
}


# ── stamp suite ──────────────────────────────────────────────────────────────
# One line per sample: (case folder, file, page). The page's title differs per sample so
# no two files share content. The format comes from the extension: .pdf is a scanned
# page, .png an image of it, .docx a Word file with the image embedded.
_LINES = ["Hôm nay, hai bên tiến hành bàn giao thiết bị văn phòng.",
          "Bên nhận đã kiểm tra số lượng và tình trạng thiết bị.", "(dữ liệu giả lập)"]


def _round(diameter=360, center="MẬT"):
    return round_stamp(diameter, "CÔNG TY TNHH THỬ NGHIỆM UAT * HÀ NỘI * ", center)


def _blue(stamp):
    red, green, blue, alpha = stamp.split()
    return Image.merge("RGBA", (blue, green, red, alpha))


def _red_title(draw, page):
    draw.text((300, 900), "THÔNG BÁO KHẨN", fill=STAMP_RED, font=ImageFont.truetype(BOLD_FONT, 110))


def _red_ring(draw, page):
    draw.ellipse((1000, 1650, 1360, 2010), outline=STAMP_RED, width=8)


def _red_logo(draw, page):
    draw.ellipse((120, 120, 300, 300), fill=STAMP_RED)
    draw.text((160, 180), "UAT", fill="white", font=ImageFont.truetype(BOLD_FONT, 50))


def _signature(draw, page):
    draw.line([(1050 + i * 12, 1700 + int(40 * math.sin(i / 2.5))) for i in range(40)], fill=(20, 20, 120), width=5)


def _second_stamp(draw, page):
    stamp = _round(300, "KHẨN")
    page.paste(stamp, (200, 1700), stamp)


def _flat(stamp):
    page = Image.new("RGB", stamp.size, "white")
    page.paste(stamp, (0, 0), stamp)
    return page


STAMP_SAMPLES = [
    ("round-stamp", "co-ban.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 01", _LINES, _round())),
    ("round-stamp", "dau-nho.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 02", _LINES, _round(220))),
    ("round-stamp", "dau-lon.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 03", _LINES, _round(520), at=(900, 1550))),
    ("round-stamp", "dau-xoay.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 04", _LINES, _round(), rotate=20)),
    ("round-stamp", "dau-de-chu.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 05", _LINES, _round(), at=(700, 250))),
    ("round-stamp", "dau-mo.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 06", _LINES, faded(_round(), 0.5))),
    ("round-stamp", "chu-khac.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 07", _LINES, _round(center="ĐÃ KÝ"))),
    ("round-stamp", "hai-dau.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 08", _LINES, _round(), extra=_second_stamp)),
    ("round-stamp", "dau-xanh.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 09", _LINES, _blue(_round()))),
    ("round-stamp", "anh-chup.png", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 10", _LINES, _round())),
    ("round-stamp", "anh-mo.png", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 11", _LINES, _round())),
    ("round-stamp", "word-ca-trang.docx", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 12", _LINES, _round())),
    ("round-stamp", "word-chi-dau.docx", lambda: _flat(_round(360, "UAT"))),
    ("rectangular-stamp", "dau-vuong.pdf",
     lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 13", _LINES, square_stamp(520, 200, ["ĐÃ PHÊ DUYỆT", "PHÒNG HÀNH CHÍNH"]))),
    ("no-stamp", "khong-dau.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 14", _LINES)),
    ("no-stamp", "khong-dau.png", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 15", _LINES)),
    ("no-stamp", "chu-ky.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 16", _LINES, extra=_signature)),
    ("red-decoys", "tieu-de-do.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 17", _LINES, extra=_red_title)),
    ("red-decoys", "vong-tron-do.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 18", _LINES, extra=_red_ring)),
    ("red-decoys", "logo-do.pdf", lambda: scan_page("BIÊN BẢN GIAO NHẬN SỐ 19", _LINES, extra=_red_logo)),
]
_LOW_RES = {"anh-mo.png": 0.3}


def _write_sample(path: str, page):
    if path.endswith(".pdf"):
        write_page_pdf(path, page)
    elif path.endswith(".png"):
        write_page_png(path, page, _LOW_RES.get(Path(path).name, 0.6))
    else:
        write_docx(path, [f"Biên bản kèm ảnh quét {Path(path).stem} (dữ liệu giả lập)."],
                   media={"image1.png": page_png_bytes(page, 1.0 if page.width < 1000 else 0.6)})


for _case, _name, _page in STAMP_SAMPLES:
    FILES[f"stamp/{_case}/{_name}"] = lambda p, page=_page: _write_sample(p, page())

# ── lifecycle and offline suites ─────────────────────────────────────────────
FILES.update({
    "lifecycle/rename-keeps-label/ho-so-doi-ten.docx": lambda p: write_docx(p, [
        "Danh mục hồ sơ lưu trữ của phòng hành chính năm nay (dữ liệu giả lập).",
    ]),
    "lifecycle/move-keeps-label/ho-so-chuyen.docx": lambda p: write_docx(p, [
        "Hồ sơ lưu trữ về việc sắp xếp kho giấy tờ tầng hai (dữ liệu giả lập).",
    ]),
    "lifecycle/move-keeps-label/ban-luu.pdf": lambda p: write_text_pdf(p, [
        "Bản hồ sơ lưu trữ đã số hóa của tổ văn thư (dữ liệu giả lập).",
    ]),
    "lifecycle/copy-carries-label/ho-so-goc.docx": lambda p: write_docx(p, [
        "Mục lục hồ sơ lưu trữ bàn giao cho chi nhánh mới (dữ liệu giả lập).",
    ]),
    "lifecycle/edit-raises-secrecy/ho-so-nang.docx": lambda p: write_docx(p, [
        "Sổ đăng ký hồ sơ lưu trữ của ban dự án (dữ liệu giả lập).",
    ]),
    "lifecycle/edit-removes-trigger/ho-so-bo.docx": lambda p: write_docx(p, [
        "Bảng kê hồ sơ lưu trữ cần tiêu hủy theo kế hoạch (dữ liệu giả lập).",
    ]),
    "lifecycle/edit-keeps-label/ho-so-them.docx": lambda p: write_docx(p, [
        "Quy trình mượn trả hồ sơ lưu trữ tại phòng đọc (dữ liệu giả lập).",
    ]),
    "offline/changes-while-offline-sync-later/so-tay.docx": lambda p: write_docx(p, [
        "Sổ tay vận hành trạm bơm khu công nghiệp (dữ liệu giả lập).",
    ]),
    "offline/changes-while-offline-sync-later/ghi-chep-offline.docx": lambda p: write_docx(p, [
        "Bổ sung sổ tay vận hành ca đêm khi mất kết nối (dữ liệu giả lập).",
    ]),
})

# ── stamp-issuer suite ───────────────────────────────────────────────────────
# The stamp rule names one company, "Thử Nghiệm UAT", and fires only on its stamp. The
# agent reads the ring of each stamp; when that reads as noise (a faint seal printed over
# text) it looks for the company in the text around the stamp (the signature block)
# instead, never in the rest of the page. Every page here names the company in its body,
# in the signature block, or not at all, on purpose, so each case isolates one path.
_ISSUER = "Công ty TNHH Thử Nghiệm UAT"
_NAMES_ISSUER = [f"Bên giao: {_ISSUER}, số 1 phố Thử Nghiệm, Hà Nội.", *_LINES]
_NAMES_NOBODY = ["Bên giao: đại diện kho vật tư số 2.", *_LINES]


def _signed_by(name):
    # The signature block names the signer's company under the signature, next to the stamp.
    def extra(draw, page):
        _signature(draw, page)
        draw.text((900, 1980), name, fill="black", font=ImageFont.truetype(FONT, 30))
    return extra


def _other_company(diameter=360):
    return round_stamp(diameter, "CÔNG TY CỔ PHẦN VẬN TẢI ABC * TP HỒ CHÍ MINH * ", "MẬT")


def _red_header(draw, page):
    # A digital-signature block printed in red, as e-signing services add to a PDF's first page.
    font = ImageFont.truetype(FONT, 26)
    for i, line in enumerate(["Cơ quan phát hành: CÔNG TY TNHH THỬ NGHIỆM UAT", "Người ký: PHÒNG HÀNH CHÍNH",
                              "Thời gian ký: 03.11.2026 12:07:13 +07:00"]):
        draw.text((900, 40 + i * 34), line, fill=STAMP_RED, font=font)


# A seal printed with half the ink, placed so the signature runs through its ring. The
# model still scores it about 0.91 here (over the "ĐẠI DIỆN BÊN GIAO" text it drops to 0.5).
_OVER_SIGNATURE = (1250, 1580)


def _faint(stamp, opacity=0.5):
    return faded(stamp, opacity)


STAMP_ISSUER_SAMPLES = [
    # The company's own stamp, read on the ring.
    ("named-stamp", "dau-cong-ty.pdf", lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 21", _NAMES_NOBODY, _round())),
    ("named-stamp", "dau-cong-ty-nho.pdf", lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 22", _NAMES_NOBODY, _round(260))),
    ("named-stamp", "dau-cong-ty.png", lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 23", _NAMES_NOBODY, _round())),
    # Another company's stamp: legible, so the page is not searched even when it names the issuer.
    ("other-company", "dau-cong-ty-khac.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 24", _NAMES_NOBODY, _other_company())),
    ("other-company", "dau-khac-trang-nhac-ten.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 25", _NAMES_ISSUER, _other_company(), at=_OVER_SIGNATURE,
                       extra=_signed_by(f"Bên nhận: {_ISSUER}"))),
    # The company's faint seal with the signature through its ring, on a page naming the
    # company: fires whether the ring is read or the page is.
    ("faint-stamp-page-names-issuer", "dau-mo-de-chu.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 26", _NAMES_ISSUER, _faint(_round()), at=_OVER_SIGNATURE,
                       extra=_signed_by(_ISSUER))),
    # Another company's faint seal, the same way, on a page naming nobody: whether its ring
    # is read (another company) or not (nobody on the page), there is no issuer.
    ("faint-stamp-page-names-nobody", "dau-mo-khong-ten.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 27", _NAMES_NOBODY, _faint(_other_company()), at=_OVER_SIGNATURE,
                       extra=_signature)),
    # No stamp at all: the company's name in the text never fires a stamp rule.
    ("no-stamp-page-names-issuer", "khong-dau-co-ten.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 28", _NAMES_ISSUER, extra=_signature)),
    # A red e-signature block and the company's name, no stamp (the real-world decoy).
    ("red-decoy-page-names-issuer", "chu-ky-so-do.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 29", _NAMES_ISSUER, extra=_red_header)),
]

for _case, _name, _page in STAMP_ISSUER_SAMPLES:
    FILES[f"stamp-issuer/{_case}/{_name}"] = lambda p, page=_page: _write_sample(p, page())

# Past the 20 MiB large-file threshold: the agent reads stamps there without OCR'ing the
# document, so the stamp on the last page must still be read and matched.
FILES["stamp-issuer/large-scan/ho-so-22mb.pdf"] = lambda p: write_large_scan_pdf(p, [
    scan_page("HỒ SƠ BÀN GIAO TRANG 1", _NAMES_NOBODY),
    scan_page("HỒ SƠ BÀN GIAO TRANG 2", _NAMES_NOBODY),
    scan_page("HỒ SƠ BÀN GIAO TRANG CUỐI", _NAMES_ISSUER, _round(), extra=_signed_by(_ISSUER)),
], 22)
FILES["stamp-issuer/large-scan/ho-so-22mb-khac.pdf"] = lambda p: write_large_scan_pdf(p, [
    scan_page("HỒ SƠ VẬN CHUYỂN TRANG 1", _NAMES_NOBODY),
    scan_page("HỒ SƠ VẬN CHUYỂN TRANG CUỐI", _NAMES_NOBODY, _other_company()),
], 22)

# ── hybrid-stamp suite ───────────────────────────────────────────────────────
# Hybrid rules that combine a stamp with a keyword or a topic. The stamped pages are images,
# so the topic reaches the agent through OCR: body text at 52 px, short lines, every line
# readable on its own. Each rule has its own trigger (kiểm định thang máy; nghiệm thu cây
# xanh; the debt-settlement topic), so a page only carries the triggers of its case.
_BIG = 52
_LIFT_LINES = ["Đơn vị đã kiểm định thang máy khu nhà B.", "Kết quả kiểm định thang máy: đạt yêu cầu.",
               "Thời hạn kiểm định lần sau: mười hai tháng.", "(dữ liệu giả lập)"]
_HANDOVER_LINES = ["Hai bên bàn giao bàn ghế phòng họp tầng ba.", "Bên nhận đã đếm đủ số lượng bàn ghế.",
                   "(dữ liệu giả lập)"]
_GREENERY_LINES = ["Hai bên nghiệm thu cây xanh tuyến đường nội khu.", "Biên bản nghiệm thu cây xanh lập thành hai bản.",
                   "(dữ liệu giả lập)"]
_DEBT_LINES = ["Khách hàng đã thanh toán xong khoản nợ tồn đọng.", "Công nợ của khách hàng đã được tất toán.",
               "Đối chiếu cuối kỳ không còn số dư phải thu.", "(dữ liệu giả lập)"]
_SECOND_COMPANY = "Công ty Cổ phần Vận tải ABC"


def _named_below(name):
    # The company's name just below the stamp, clear of its ring, so the stamp's score stays clean.
    def extra(draw, page):
        draw.text((950, 2040), name, fill="black", font=ImageFont.truetype(FONT, 30))
    return extra


HYBRID_STAMP_SAMPLES = [
    # Keyword and the stamp of any company: rule A.
    ("stamp-and-keyword", "kiem-dinh-co-dau.pdf",
     lambda: scan_page("BIÊN BẢN KIỂM ĐỊNH SỐ 31", _LIFT_LINES, _round(), body_size=_BIG)),
    # A stamp without the keyword.
    ("stamp-without-keyword", "ban-giao-co-dau.pdf",
     lambda: scan_page("BIÊN BẢN BÀN GIAO SỐ 32", _HANDOVER_LINES, _round(), body_size=_BIG)),
    # The same keyword twice, each company's stamp, the company also named below its stamp.
    ("company-names", "nghiem-thu-dau-thu-nghiem.pdf",
     lambda: scan_page("BIÊN BẢN NGHIỆM THU SỐ 33", _GREENERY_LINES, _round(), body_size=_BIG,
                       extra=_named_below(_ISSUER))),
    ("company-names", "nghiem-thu-dau-van-tai.pdf",
     lambda: scan_page("BIÊN BẢN NGHIỆM THU SỐ 34", _GREENERY_LINES, _other_company(), body_size=_BIG,
                       extra=_named_below(_SECOND_COMPANY))),
    # The topic of the semantic sub-condition with a stamp.
    ("semantic-and-stamp", "tat-toan-co-dau.pdf",
     lambda: scan_page("XÁC NHẬN TẤT TOÁN CÔNG NỢ", _DEBT_LINES, _round(), body_size=_BIG)),
]

for _case, _name, _page in HYBRID_STAMP_SAMPLES:
    FILES[f"hybrid-stamp/{_case}/{_name}"] = lambda p, page=_page: _write_sample(p, page())


def _write_txt(path: str, lines: list[str]):
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


FILES.update({
    # The keyword without any stamp: plain text has no stamp information (the condition is
    # skipped), a text PDF has it and shows no stamp (the condition fails).
    "hybrid-stamp/keyword-without-stamp/kiem-dinh.txt": lambda p: _write_txt(p, [
        "Sổ theo dõi kiểm định thang máy khu nhà C.", "Lần kiểm định thang máy gần nhất: đạt.", "(dữ liệu giả lập)"]),
    "hybrid-stamp/keyword-without-stamp/kiem-dinh-ban-in.pdf": lambda p: write_text_pdf(p, [
        "Thông báo lịch kiểm định thang máy khu nhà D.", "Cư dân dùng thang bộ trong thời gian kiểm định thang máy.",
        "(dữ liệu giả lập)"]),
    # The topic without a stamp: an "all" rule with a stamp condition stays silent.
    "hybrid-stamp/semantic-and-stamp/tat-toan.txt": lambda p: _write_txt(p, [
        "Khoản nợ quá hạn của khách hàng đã được thanh toán đầy đủ.",
        "Kế toán đã tất toán công nợ, số dư phải thu bằng không.", "(dữ liệu giả lập)"]),
})

if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    for relative, write in FILES.items():
        target = DATA / relative
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write(str(target))
        print("wrote", target.relative_to(DATA.parent))
