"""What the admin does in the NextDLP console, one method per task."""
import re
from pathlib import Path

from playwright.sync_api import Page, expect

SHORT = 1500


class AdminConsole:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def screenshot(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)

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
        dialog.get_by_role("button", name="Tạo", exact=True).click()
        expect(dialog).to_be_hidden()

    # ── Data sources ─────────────────────────────────────────────────────────
    def create_data_source(self, name: str, root: str, company: str, department: str,
                           extra_extensions: list[str] = ()):
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
        # The form starts with .pdf/.docx/.xlsx; other types are typed in.
        include = dialog.get_by_label("Include extensions")
        for extension in extra_extensions:
            include.fill(extension)
            include.press("Enter")
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
            if "hybrid" in condition:
                self.fill_hybrid(card, condition["hybrid"])
            else:
                card.get_by_label(re.compile("^Giá trị")).first.fill(condition["keyword"])
        # "Lưu điều kiện" for one condition, "Lưu N điều kiện" for more.
        editor.get_by_role("button", name=re.compile(r"^Lưu( \d+)? điều kiện$")).click()
        expect(editor).to_be_hidden()
        form.get_by_role("button", name="Tạo chính sách", exact=True).click()
        expect(form).to_be_hidden()

    _COMBINATORS = {"all": "Tất cả điều kiện", "any": "Bất kỳ điều kiện nào"}
    _SUB_TYPES = {"keyword": "Từ khóa", "data_pattern": "Mẫu dữ liệu (regex)", "wordlist": "Danh sách từ"}

    def fill_hybrid(self, card, spec: dict):
        """Sub-conditions are appended at the end, so each one's fields are the last of their kind."""
        self.pick(card.get_by_role("combobox", name="Kỹ thuật nhận diện"), "Hybrid (nhiều tín hiệu)")
        self.pick(card.get_by_role("combobox", name="Tổ hợp"), self._COMBINATORS[spec["combinator"]])
        for index, sub in enumerate(spec["conditions"]):
            (kind, value), = sub.items()
            if index > 0:
                card.get_by_role("button", name="Thêm điều kiện con", exact=True).click()
            if kind != "keyword":  # a new sub-condition starts as a keyword
                self.pick(card.get_by_role("combobox", name="Loại điều kiện").nth(index), self._SUB_TYPES[kind])
            if kind == "keyword":
                field = card.get_by_label("Từ khóa", exact=True).last
                field.fill(value)
                field.press("Enter")
            elif kind == "data_pattern":
                card.get_by_label("Biểu thức RE2", exact=True).last.fill(value)
            elif kind == "wordlist":
                # Comma-separated terms split on blur, each at the default weight 0.5;
                # the default threshold is 1, so any two terms fire it.
                field = card.get_by_label("Từ ngữ", exact=True).last
                field.fill(", ".join(value))
                field.blur()
