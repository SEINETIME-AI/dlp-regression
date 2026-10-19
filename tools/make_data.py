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
from make_xlsx import write_xlsx
from make_large import write_large_pdf, write_large_text
from make_broken import write_corrupt_docx, write_password_docx
from make_media import round_stamp, write_stamped_scan, text_image, write_png, write_scanned_pdf, write_text_pdf

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

if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    for relative, write in FILES.items():
        target = DATA / relative
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write(str(target))
        print("wrote", target.relative_to(DATA.parent))
