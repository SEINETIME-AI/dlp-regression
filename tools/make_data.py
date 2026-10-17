"""Regenerates the synthetic documents in data/. Content is invented.

Each file only carries the trigger words of the rules its case is about (see the
suite's setup.yaml), and no two files share content, so the agent's per-content
cache never answers for another case.
"""
from pathlib import Path

from make_docx import write_docx

DATA = Path(__file__).resolve().parent.parent / "data"

FILES = {
    "classification/01-higher-secrecy-wins/thong-bao.docx": [
        "Thông báo nội bộ, tuyệt mật — không phát tán ra ngoài.",
        "Kế hoạch tổ chức lại khối văn phòng (dữ liệu giả lập).",
    ],
    "classification/02-multi-domain/tong-hop.docx": [
        "Tổng hợp lưu hành nội bộ.",
        "Phiếu chi văn phòng phẩm và bảng lương khối hành chính (dữ liệu giả lập).",
    ],
    "classification/03-no-match-is-public/lich-hop.docx": [
        "Lịch họp giao ban tuần tới tại phòng họp tầng hai.",
        "Mời các trưởng nhóm tham dự đúng giờ (dữ liệu giả lập).",
    ],
}

if __name__ == "__main__":
    for relative, paragraphs in FILES.items():
        target = DATA / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_docx(str(target), paragraphs)
        print("wrote", target.relative_to(DATA.parent))
