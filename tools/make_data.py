"""Regenerates the synthetic documents in data/. Content is invented."""
from pathlib import Path

from make_docx import write_docx

DATA = Path(__file__).resolve().parent.parent / "data"

# The UAT runs next to the department's real policy, so the text avoids its
# triggers: hóa đơn / thuế / phí / số tiền, "quý 1"/"quý 2", and long digit runs.
FILES = {
    "01-keyword-secrecy/bao-cao-quy.docx": [
        "Tài liệu nội bộ, tuyệt mật — không phát tán ra ngoài.",
        "Tổng hợp phiếu chi văn phòng phẩm và bảng lương khối hành chính.",
        "Dữ liệu giả lập phục vụ kiểm thử.",
    ],
    "01-keyword-secrecy/de-xuat.docx": [
        "Đề xuất mua sắm — lưu hành nội bộ.",
        "Kèm phiếu chi tạm ứng cho chuyến công tác.",
    ],
}

if __name__ == "__main__":
    for relative, paragraphs in FILES.items():
        target = DATA / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_docx(str(target), paragraphs)
        print("wrote", target.relative_to(DATA.parent))
