"""What the admin does in the NextDLP console, one method per task."""
import re
from pathlib import Path

from playwright.sync_api import Page, expect

SHORT = 1500


class AdminConsole:
    def __init__(self, page: Page, base_url: str, shots: Path):
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.shots = shots
        self._shot_index = 0

    def shot(self, name: str):
        self._shot_index += 1
        self.shots.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(self.shots / f"{self._shot_index:02d}-{name}.png"), full_page=True)

    def open(self, path: str):
        self.page.goto(self.base_url + path)
        # The console keeps an SSE stream open, so wait for content, not network idle.
        self.page.locator("main").wait_for()
        self.page.wait_for_timeout(SHORT)

    def dialog(self):
        return self.page.get_by_role("dialog").last

    def pick(self, combobox, option: str):
        combobox.click()
        self.page.get_by_role("option", name=option, exact=True).click()
        self.page.wait_for_timeout(300)

    # ── Session ──────────────────────────────────────────────────────────────
    def login(self, user: str, password: str):
        self.page.goto(self.base_url + "/login")
        self.page.get_by_label(re.compile("tên đăng nhập|username", re.I)).fill(user)
        self.page.get_by_label(re.compile("mật khẩu|password", re.I)).first.fill(password)
        self.page.keyboard.press("Enter")
        self.page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

    # ── Taxonomy ─────────────────────────────────────────────────────────────
    def create_document_type(self, code: str, name: str):
        self.open("/taxonomy")
        self.page.get_by_role("tab", name="Loại tài liệu").click()
        self.page.get_by_role("button", name=re.compile("Loại tài liệu mới")).click()
        dialog = self.dialog()
        dialog.get_by_label(re.compile("^Mã")).first.fill(code)
        dialog.get_by_label(re.compile("^Tên hiển thị")).first.fill(name)
        self.shot(f"doctype-{code}")
        dialog.get_by_role("button", name="Tạo", exact=True).click()
        expect(dialog).to_be_hidden()

    # ── Data sources ─────────────────────────────────────────────────────────
    def create_data_source(self, name: str, root: str, company: str, department: str):
        self.open("/data-sources")
        self.page.get_by_role("button", name="Thêm nguồn dữ liệu", exact=True).click()
        dialog = self.dialog()
        dialog.get_by_label(re.compile("^Tên nguồn dữ liệu")).fill(name)
        dialog.get_by_label(re.compile("^Đường dẫn")).fill(root)
        self.pick(dialog.get_by_role("combobox", name=re.compile(r"Công ty con \/ Ban chuyên môn")), company)
        # Owner is a multi-select that starts at "all departments": clear that, pick ours.
        dialog.get_by_role("combobox", name=re.compile("^Owner")).click()
        self.page.get_by_role("option", name=re.compile("^Tất cả phòng ban")).click()
        self.page.get_by_role("option", name=department, exact=True).click()
        self.page.keyboard.press("Escape")  # closes the open listbox, not the dialog
        expect(self.page.get_by_role("listbox")).to_be_hidden()
        self.shot("data-source-form")
        dialog.get_by_role("button", name="Tạo nguồn", exact=True).click()
        expect(dialog).to_be_hidden()

    # ── Classification policy ────────────────────────────────────────────────
    def create_policy(self, name: str, company: str, department: str, conditions: list[dict],
                      secrecy_names: dict, type_names: dict):
        self.open("/classification-policies")
        self.page.get_by_role("button", name="Chính sách mới", exact=True).click()
        form = self.page.get_by_role("dialog", name=re.compile("^Chính sách phân loại mới"))
        form.get_by_label(re.compile("^Tên")).first.fill(name)
        self.pick(form.get_by_role("combobox", name=re.compile(r"Công ty con \/ Ban chuyên môn")), company)
        self.pick(form.locator('[aria-labelledby="org-unit-label"]'), department)
        form.get_by_role("checkbox", name="Kích hoạt").check()  # new policies start inactive
        self.shot("policy-general")

        form.get_by_role("tab", name=re.compile("Điều kiện phân loại")).click()
        form.get_by_role("button", name="Thêm điều kiện", exact=True).click()
        editor = self.page.get_by_role("dialog", name=re.compile("^Thêm điều kiện phân loại"))
        for index, condition in enumerate(conditions, start=1):
            if index > 1:
                editor.get_by_role("button", name="Thêm điều kiện", exact=True).click()
                self.page.get_by_role("menuitem", name=re.compile(r"Từ khóa \/ Regex")).click()
                self.page.wait_for_timeout(300)
            card = editor.get_by_role("group", name=f"Điều kiện #{index}", exact=True)
            card.get_by_label("Tên điều kiện").fill(condition["name"])
            if condition.get("secrecy"):
                self.pick(card.get_by_role("combobox", name="Mức bảo mật"), secrecy_names[condition["secrecy"]])
            if condition.get("document_type"):
                card.get_by_role("combobox", name="Loại tài liệu").click()
                self.page.get_by_role("treeitem", name=type_names[condition["document_type"]]).click()
                # The picker closes itself on select; an Escape here would close the editor instead.
                expect(self.page.get_by_role("tree")).to_be_hidden()
            card.get_by_label(re.compile("^Giá trị")).first.fill(condition["keyword"])
            self.shot(f"policy-condition-{index}")
        # "Lưu điều kiện" for one condition, "Lưu N điều kiện" for more.
        editor.get_by_role("button", name=re.compile(r"^Lưu( \d+)? điều kiện$")).click()
        expect(editor).to_be_hidden()
        self.shot("policy-ready")
        form.get_by_role("button", name="Tạo chính sách", exact=True).click()
        expect(form).to_be_hidden()

    # ── Documents ────────────────────────────────────────────────────────────
    def document_rows(self, search: str) -> dict[str, str]:
        """Inventory rows whose path contains `search`, as file name -> row text."""
        self.open("/documents")
        self.page.get_by_label("Tìm tệp / đường dẫn").fill(search)
        self.page.wait_for_timeout(2000)
        self.shot("documents")
        rows = {}
        for row in self.page.get_by_role("row").all()[1:]:
            text = row.inner_text()
            first_line = text.strip().split("\n")[0].strip()
            if first_line:
                rows[first_line] = text
        return rows
