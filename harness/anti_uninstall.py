"""The anti-uninstall suite (laptop part): no package can be built before the uninstall password
exists; the admin sets and changes it on the Bộ cài Agent page and nobody can remove it; an agent
played over gRPC enrolls from a UAT package, gets the hash on heartbeat and reports the two machine
events; the package's MSI carries the hash. The stack's own password hash is saved before the run and
put back after it. Installing and uninstalling the real agent is the VM's part. The cases build on
each other, so they run in order and all together."""
import base64
import hashlib
import hmac
import io
import re
import secrets
import shlex
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import grpc
import yaml
from playwright.sync_api import Browser, expect

from harness.api import Api, ApiError
from harness.config import Settings
from harness.console import ApiAnswers
from harness.simulated_agent import SimulatedAgent
from harness.smb import _powershell as powershell
from harness.suite import CaseResult, SuiteResult
from harness.ui import AdminConsole

SPEC_FILE = "anti-uninstall.yaml"
UNIT_ID, UNIT_NAME = "uat-anti-uninstall", "UAT anti-uninstall"
MACHINE = "UAT-ANTI-UNINSTALL"
HASH_PREFIX = "pbkdf2-sha256$600000$"
MSI_PROPERTY, MSI_HASH_LENGTH = "UNINSTALL_PASSWORD_HASH", 128
PASSWORD_PATH = "/api/system/settings/uninstall-password"
PACKAGES_PATH = "/api/agent-packages"
PACKAGES_PAGE = "/agent-deployment"
SAVED_HASH_FILE = "anti-uninstall-saved-hash.txt"
BANNER = "Chưa đặt mật khẩu gỡ agent. Cần đặt mật khẩu trước khi tạo bộ cài"
STATUS_SET = "Mật khẩu gỡ agent: đã đặt"
ALERT_ACTION = "tamper_protection"
EVENT_CHIPS = {"uninstall_denied": "Gỡ agent bị từ chối", "agent_unclean_stop": "Agent dừng bất thường"}
NOTIFICATIONS = {"uninstall_denied": "Việc gỡ agent trên", "agent_unclean_stop": "đã dừng bất thường"}
VIEWPORT = {"width": 1440, "height": 900}
# The server caches the hash it sends on heartbeat for 15 s, so a new password can take that long to reach an agent.
HASH_WAIT_S = 25


class SuiteAborted(RuntimeError):
    """A precondition failed: nothing may be changed."""


def _password_set(api: Api) -> bool:
    return bool(api.get(PACKAGES_PATH).get("uninstall_password_set"))


def _uat_machines(api: Api) -> list[dict]:
    return [m for m in api.items("/api/machines") if m.get("department") == UNIT_ID]


def _uat_packages(api: Api) -> list[dict]:
    return [p for p in api.items(PACKAGES_PATH) if p["organization_unit_id"] == UNIT_ID]


def _uat_unit_exists(api: Api) -> bool:
    return any(unit["id"] == UNIT_ID for unit in api.items("/api/org-units"))


def _psql(settings: Settings, sql: str) -> str:
    """Runs one statement the API has no route for (`UAT_PSQL`); returns its unaligned output."""
    result = subprocess.run(shlex.split(settings.psql, posix=False) + ["-At", "-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _stored_hash(settings: Settings) -> str:
    """The stack's uninstall password hash; '' when none is set."""
    return _psql(settings, "SELECT COALESCE(uninstall_password_hash, '') FROM system_settings WHERE id = 'default'")


def _write_hash(settings: Settings, value: str):
    """The API cannot remove the password or take a hash, so the test resets and restores it with SQL."""
    if not re.fullmatch(r"[A-Za-z0-9$+/=_-]*", value):
        raise RuntimeError("the saved uninstall password hash has unexpected characters; not written")
    literal = f"'{value}'" if value else "NULL"
    _psql(settings, f"UPDATE system_settings SET uninstall_password_hash = {literal} WHERE id = 'default'")


def _saved_hash_file(settings: Settings) -> Path:
    return settings.reports_dir / SAVED_HASH_FILE


def save_and_reset_password(settings: Settings):
    """Keeps the stack's hash in a file (so an aborted run can still be undone), then removes the password."""
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    _saved_hash_file(settings).write_text(_stored_hash(settings), encoding="ascii")
    _write_hash(settings, "")


def restore_password(settings: Settings) -> bool:
    """Puts back the hash a run saved; False when no run left one to restore."""
    saved = _saved_hash_file(settings)
    if not saved.exists():
        return False
    _write_hash(settings, saved.read_text(encoding="ascii").strip())
    saved.unlink()
    return True


def remove_leftovers(api: Api, settings: Settings) -> tuple[list[str], list[str]]:
    """Removes the suite's package, machine and unit and restores the stack's uninstall password.
    Returns (removed, problems)."""
    removed, problems = [], []
    try:
        for package in _uat_packages(api):
            api.call("DELETE", f"{PACKAGES_PATH}/{package['id']}")
            removed.append(f"package {package['id']}")
        machines = _uat_machines(api)
        for machine in machines:
            if machine.get("status") != "revoked":
                api.call("PATCH", f"/api/machines/{machine['id']}/revoke")
        if machines:
            # The API only revokes a machine; its row (events cascade) goes with SQL.
            _psql(settings, f"DELETE FROM machines WHERE department = '{UNIT_ID}'")
            removed += [f"machine {m['name']}" for m in machines]
        if _uat_unit_exists(api):
            api.call("DELETE", f"/api/org-units/{UNIT_ID}")
            removed.append(f"unit {UNIT_ID}")
    except (ApiError, RuntimeError) as error:
        problems.append(f"{UNIT_NAME} objects left: {error}")
    try:
        if restore_password(settings):
            removed.append("the run's uninstall password (the stack's own hash is back)")
    except (OSError, RuntimeError) as error:
        problems.append(f"uninstall password not restored: {error}")
    return removed, problems


def _pbkdf2_matches(encoded: str, password: str) -> bool:
    """Checks the server's hash the way the agent does: NFC UTF-8, PBKDF2-HMAC-SHA256."""
    try:
        scheme, iterations, salt, key = encoded.split("$")
        unpad = lambda text: base64.b64decode(text + "=" * (-len(text) % 4))  # noqa: E731
        derived = hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFC", password).encode("utf-8"),
                                      unpad(salt), int(iterations), len(unpad(key)))
    except ValueError:
        return False
    return scheme == "pbkdf2-sha256" and hmac.compare_digest(derived, unpad(key))


def _msi_property(msi: Path, name: str) -> str | None:
    """Reads one Property row with the Windows Installer COM object (msilib is gone in Python 3.13)."""
    value = powershell(
        "$installer = New-Object -ComObject WindowsInstaller.Installer; "
        "$db = $installer.GetType().InvokeMember('OpenDatabase', 'InvokeMethod', $null, $installer, @($env:UAT_MSI, 0)); "
        "$query = \"SELECT ``Value`` FROM ``Property`` WHERE ``Property``='$env:UAT_NAME'\"; "
        "$view = $db.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $db, @($query)); "
        "[void]$view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null); "
        "$record = $view.GetType().InvokeMember('Fetch', 'InvokeMethod', $null, $view, $null); "
        "if ($record) { $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 1) } "
        "else { '<absent>' }; "
        "[void]$view.GetType().InvokeMember('Close', 'InvokeMethod', $null, $view, $null); "
        "[void][Runtime.InteropServices.Marshal]::ReleaseComObject($db)",
        msi=str(msi), name=name)
    return None if value == "<absent>" else value


class AntiUninstallSuite:
    def __init__(self, folder, settings: Settings, api: Api, only: list[str] | None = None):
        self.name = folder.name
        if only:
            raise ValueError(f"the cases of {self.name} build on each other; run the whole suite")
        spec = yaml.safe_load((folder / SPEC_FILE).read_text(encoding="utf-8"))
        self.cases = spec["cases"]
        self.settings = settings
        self.api = api
        self.failure_shot = settings.reports_dir / f"{self.name}-failure.png"
        self.shots = settings.screenshot_dir
        # Synthetic test data, made per run and never logged.
        self.password = unicodedata.normalize("NFC", f"Khóa gỡ máy trạm {secrets.token_hex(3)}")
        self.new_password = unicodedata.normalize("NFC", f"Mật khẩu đổi lại {secrets.token_hex(3)}")
        self.original_hash = ""
        self.hash = ""
        self.package: dict = {}
        self.agent: SimulatedAgent | None = None
        self.console: AdminConsole | None = None
        self.removed: list[str] = []
        self.problems: list[str] = []

    # ── Helpers ─────────────────────────────────────────────────────────────
    @property
    def page(self):
        return self.console.page

    def shot(self, name: str, full_page: bool = True, around=None):
        """Saves a screenshot; with `around` (a locator) only the box around all its matches."""
        self.shots.mkdir(parents=True, exist_ok=True)
        # MUI dialogs, drawers, popovers and chips fade or slide in; let them settle first.
        self.page.wait_for_timeout(800)
        clip = None
        if around is not None:
            boxes = [around.nth(i).bounding_box() for i in range(around.count())]
            left, top = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
            clip = {"x": left, "y": top, "width": max(b["x"] + b["width"] for b in boxes) - left,
                    "height": max(b["y"] + b["height"] for b in boxes) - top}
        self.page.screenshot(path=str(self.shots / name), full_page=full_page and clip is None, clip=clip)

    def heartbeat_hash_for(self, password: str) -> str:
        """The hash heartbeat sends, once it is the one of @p password or HASH_WAIT_S has passed."""
        deadline = time.monotonic() + HASH_WAIT_S
        while True:
            answer = self.agent.heartbeat()
            value = answer.uninstall_password_hash if answer.HasField("uninstall_password_hash") else ""
            if _pbkdf2_matches(value, password) or time.monotonic() >= deadline:
                return value
            time.sleep(2)

    def password_form(self, title: str):
        """The fields of the open password dialog, which must carry `title`."""
        dialog = self.page.get_by_role("dialog", name=title, exact=True)
        expect(dialog).to_be_visible()
        new = dialog.get_by_label("Mật khẩu mới", exact=True)
        return dialog, new, dialog.get_by_label("Nhập lại mật khẩu", exact=True)

    def save_password(self, dialog, new, confirm, password: str):
        """Fills both fields and saves; returns the PUT answer and the package list the page reloads."""
        new.fill(password)
        confirm.fill(password)
        is_put = lambda r: r.url.endswith(PASSWORD_PATH) and r.request.method == "PUT"  # noqa: E731
        is_list = lambda r: r.url.endswith(PACKAGES_PATH) and r.request.method == "GET"  # noqa: E731
        with self.page.expect_response(is_list) as reloaded, self.page.expect_response(is_put) as answer:
            dialog.get_by_role("button", name="Lưu", exact=True).click()
        return answer.value, reloaded.value

    def refused(self, method: str, path: str, body=None) -> tuple[int, str]:
        """The status and text of an API call the server must refuse; (0, …) when it accepted it."""
        try:
            return 0, str(self.api.call(method, path, body))
        except ApiError as error:
            return error.status, str(error)

    def storage_text(self) -> str:
        return self.page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})")

    def download_msi(self, folder: Path) -> Path:
        """The package's installer; the download is a zip (MSI + helper script) when the template has one."""
        request = urllib.request.Request(f"{self.api.base_url}{PACKAGES_PATH}/{self.package['id']}/download")
        request.add_header("Authorization", "Bearer " + self.api.token)
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        msi = folder / "package.msi"
        if payload[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                member = next(n for n in archive.namelist() if n.lower().endswith(".msi"))
                payload = archive.read(member)
        msi.write_bytes(payload)
        return msi

    def package_request(self) -> dict:
        policy = next(p for p in self.api.items("/api/agent-policies") if p.get("is_active"))
        return {"organization_unit_id": UNIT_ID, "agent_policy_id": policy["id"], "max_enrollments": 1}

    def check_msi(self, case: CaseResult, expected: str, what: str):
        with tempfile.TemporaryDirectory(prefix="uat-msi-") as folder:
            value = _msi_property(self.download_msi(Path(folder)), MSI_PROPERTY)
        shown = "absent" if value is None else f"{len(value)} chars, starts {value[:21]!r}, ends {value[-4:]!r}"
        case.checks.append((value == expected, f"MSI {MSI_PROPERTY}: {shown} (want {what})"))

    # ── Cases ───────────────────────────────────────────────────────────────
    def stack_password_saved(self, case: CaseResult):
        removed, problems = remove_leftovers(self.api, self.settings)
        case.checks.append((not problems, "an earlier run's leftovers: " + ("; ".join(removed + problems) or "none")))
        if problems:
            raise SuiteAborted("an earlier run's objects could not be removed")
        self.original_hash = _stored_hash(self.settings)
        save_and_reset_password(self.settings)
        unset = not _password_set(self.api)
        case.checks.append((unset, f"the stack's hash ({len(self.original_hash)} chars) is saved and the password "
                                   f"removed for the run: uninstall_password_set={not unset} (want false)"))
        if not unset:
            raise SuiteAborted("the uninstall password could not be removed for the run")

    def package_needs_password(self, case: CaseResult):
        parent = self.api.get(f"/api/org-units/{self.settings.department_code}")["parent_id"]
        self.api.call("POST", "/api/org-units", {"id": UNIT_ID, "display_name": UNIT_NAME,
                                                 "unit_type": "Phòng", "parent_id": parent})
        status, text = self.refused("POST", PACKAGES_PATH, self.package_request())
        packages = len(_uat_packages(self.api))
        case.checks.append((status == 409 and "uninstall_password_required" in text and packages == 0,
                            f"create a package without a password -> {text} (want 409 uninstall_password_required), "
                            f"packages of {UNIT_NAME}: {packages}"))
        self.console.open(PACKAGES_PAGE)
        banner = self.page.get_by_role("alert").filter(has_text=BANNER)
        expect(banner).to_have_count(1)
        offered = banner.get_by_role("button", name="Đặt mật khẩu gỡ agent", exact=True).count() == 1
        case.checks.append((offered, f"Bộ cài Agent shows the banner \"{BANNER}…\" with \"Đặt mật khẩu gỡ agent\" ({offered})"))
        self.shot("agent-packages-password-banner.png")

    def console_set_password(self, case: CaseResult):
        answers = ApiAnswers(self.page, PASSWORD_PATH)
        self.page.get_by_role("button", name="Tạo bộ cài mới", exact=True).click()
        dialog, new, confirm = self.password_form("Đặt mật khẩu gỡ agent")
        case.checks.append((True, "\"Tạo bộ cài mới\" opens \"Đặt mật khẩu gỡ agent\" first"))
        self.shot("agent-packages-password-dialog.png", full_page=False)

        save = dialog.get_by_role("button", name="Lưu", exact=True)
        for value, helper, what in (("   ", "Mật khẩu không được để trống.", "whitespace only"),
                                    ("Mật khẩ", "Mật khẩu cần ít nhất 8 ký tự.", "7 characters")):
            since = len(answers.answers)
            new.fill(value)
            confirm.fill(value)
            confirm.press("Enter")
            self.page.wait_for_timeout(1000)
            helper_shown = dialog.get_by_text(helper, exact=True).count() == 1
            sent = answers.last("PUT", since)
            case.checks.append((helper_shown and save.is_disabled() and sent is None,
                                f"{what}: helper \"{helper}\" shown ({helper_shown}), Lưu disabled "
                                f"({save.is_disabled()}), request sent: {sent or 'none'}"))

        answer, reloaded = self.save_password(dialog, new, confirm, self.password)
        put_body, list_body = answer.text(), reloaded.text()
        case.checks.append((answer.status == 200 and '"uninstall_password_set":true' in put_body
                            and "pbkdf2" not in put_body,
                            f"PUT uninstall-password -> {answer.status} {put_body.strip()}"))
        create = self.page.get_by_role("dialog").filter(has_text="Tạo bộ cài")
        expect(dialog).to_be_hidden()
        expect(create).to_be_visible()
        case.checks.append((True, "the password dialog closes and the create-package dialog opens"))
        self.page.keyboard.press("Escape")
        expect(create).to_be_hidden()
        expect(self.page.get_by_text(STATUS_SET, exact=True)).to_be_visible()
        gone = self.page.get_by_role("alert").filter(has_text=BANNER).count() == 0
        case.checks.append((gone, f"the page shows \"{STATUS_SET}\" and the banner is gone ({gone})"))
        leaks = [where for where, text in (("GET agent-packages", list_body), ("browser storage", self.storage_text()))
                 if self.password in text or "pbkdf2" in text]
        case.checks.append(('"uninstall_password_set":true' in list_body and not leaks,
                            "GET agent-packages has uninstall_password_set=true; password or pbkdf2 found in: "
                            + (", ".join(leaks) or "nowhere")))
        self.shot("agent-packages-password-set.png")

        self.console.open("/system?section=agent")
        fields = self.page.get_by_label("Mật khẩu mới", exact=True).count()
        case.checks.append((fields == 0, f"Hệ thống › Cấu hình Agent mặc định has no password form: {fields} fields (want 0)"))

    def heartbeat_carries_hash(self, case: CaseResult):
        created = self.api.call("POST", PACKAGES_PATH, self.package_request())
        self.package = created["package"]
        case.checks.append((self.package["status"] == "ready",
                            f"package {self.package['id']} for {UNIT_NAME}: {self.package['status']}"))
        self.agent = SimulatedAgent(self.settings.grpc_target, self.settings.backend_proto_dir, MACHINE)
        self.agent.enroll(self.package["id"], created["bootstrap_token"])
        case.checks.append((bool(self.agent.machine_id and self.agent.api_key),
                            f"{MACHINE} enrolled as {self.agent.machine_id}"))

        self.hash = self.heartbeat_hash_for(self.password)
        case.checks.append((self.hash.startswith(HASH_PREFIX),
                            f"heartbeat uninstall_password_hash starts {self.hash[:21]!r}, {len(self.hash)} chars "
                            f"(waited up to {HASH_WAIT_S} s for the server's cache)"))
        case.checks.append((_pbkdf2_matches(self.hash, self.password),
                            "hashlib PBKDF2-HMAC-SHA256 of the NFC UTF-8 password matches the hash"))

    def machine_events_on_endpoints(self, case: CaseResult):
        with self.page.expect_response(lambda r: "/api/console/stream" in r.url, timeout=20000):
            self.console.open("/endpoints")
        self.page.wait_for_timeout(1000)
        answer = self.agent.push_machine_events(list(EVENT_CHIPS), ALERT_ACTION)
        case.checks.append((answer.accepted_count == 2,
                            f"BatchSync of uninstall_denied + agent_unclean_stop without file fields: "
                            f"accepted {answer.accepted_count} (want 2)"))
        machine_id = self.agent.machine_id

        # Bell notices live only in page memory: check them before any reload.
        bell = self.page.get_by_role("button", name="Thông báo", exact=True)
        for event_type, message in NOTIFICATIONS.items():
            bell.click()
            popover = self.page.locator(".MuiPopover-paper")
            item = popover.get_by_role("button").filter(has_text=EVENT_CHIPS[event_type]).filter(has_text=MACHINE)
            expect(item).to_have_count(1)
            if event_type == "uninstall_denied":
                self.shot("bell-uninstall-notifications.png", around=popover.get_by_role("button").filter(has_text=MACHINE))
            shown = message in item.inner_text()
            item.click()
            self.page.wait_for_url(lambda url: "machine=" in url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.page.url).query)
            opened = self.page.get_by_role("region", name="Chi tiết máy trạm").is_visible()
            case.checks.append((shown and query.get("machine") == [machine_id] and opened,
                                f"bell: \"{EVENT_CHIPS[event_type]}\" for {MACHINE} (message ok: {shown}) "
                                f"opens /endpoints?machine={query.get('machine')} with the drawer ({opened})"))
            self.page.go_back()
            self.page.wait_for_timeout(500)

        machine_events = self.api.items("/api/agent-events", machine_id=machine_id, category="machine")
        document_events = self.api.items("/api/agent-events", machine_id=machine_id, category="document")
        types = sorted(event["event_type"] for event in machine_events)
        case.checks.append((types == sorted(EVENT_CHIPS) and not document_events,
                            f"API: category=machine gives {types}, category=document gives "
                            f"{len(document_events)} rows (want both types, 0)"))
        detail = self.api.get(f"/api/machines/{machine_id}").get("last_protection_event") or {}
        case.checks.append((detail.get("recent") is True and detail.get("event_type") in EVENT_CHIPS,
                            f"API: /api/machines/{{id}} last_protection_event = {detail}"))

        self.console.open("/classification-events")
        self.page.locator(".MuiDataGrid-root").wait_for()
        self.page.wait_for_timeout(1500)
        timeline_rows = self.page.locator(".MuiDataGrid-row").filter(has_text=MACHINE).count()
        case.checks.append((timeline_rows == 0,
                            f"classification timeline: {timeline_rows} rows for {MACHINE} (want 0)"))

        self.console.open("/endpoints")
        row = self.page.locator(".MuiDataGrid-row").filter(has_text=MACHINE)
        expect(row).to_have_count(1, timeout=30000)
        flagged = row.get_by_test_id("protection-flag").count() == 1
        banner = self.page.get_by_text(re.compile("máy có sự kiện bảo vệ agent trong 7 ngày qua"))
        case.checks.append((flagged and banner.count() == 1,
                            f"Máy trạm: {MACHINE} flagged ({flagged}), banner "
                            f"\"{banner.first.inner_text() if banner.count() else '-'}\""))
        self.shot("endpoints-protection-flag.png")

        self.page.get_by_role("button", name="Xem các máy này").click()
        self.page.wait_for_url(lambda url: "protection=recent" in url)
        expect(row).to_have_count(1)
        case.checks.append((True, "filter \"Có sự kiện trong 7 ngày\" (from the banner) keeps the machine"))

        self.page.get_by_role("button", name=MACHINE, exact=True).click()
        drawer = self.page.get_by_role("region", name="Chi tiết máy trạm")
        items = drawer.get_by_role("listitem")
        expect(items).to_have_count(2, timeout=15000)
        chips = [items.nth(i).inner_text().splitlines()[0] for i in range(2)]
        case.checks.append((sorted(chips) == sorted(EVENT_CHIPS.values()),
                            f"drawer \"Sự kiện bảo vệ agent\": {chips}"))
        self.shot("endpoint-drawer-protection-events.png", full_page=False)
        drawer.get_by_role("button", name="Đóng").click()
        expect(drawer).to_be_hidden()

    def package_msi_carries_hash(self, case: CaseResult):
        self.check_msi(case, self.hash.ljust(MSI_HASH_LENGTH, "_"),
                       f"the heartbeat hash then '_' to {MSI_HASH_LENGTH}")

    def console_change_password(self, case: CaseResult):
        for body, what in (({"password": None}, "null"), ({"password": ""}, "empty")):
            status, text = self.refused("PUT", PASSWORD_PATH, body)
            case.checks.append((status == 400 and "cannot be removed" in text and _password_set(self.api),
                                f"PUT uninstall-password {what} -> {text} (want 400, password still set)"))

        self.console.open(PACKAGES_PAGE)
        remove_buttons = self.page.get_by_role("button", name=re.compile("Xóa mật khẩu")).count()
        self.page.get_by_role("button", name="Đổi mật khẩu", exact=True).click()
        dialog, new, confirm = self.password_form("Đổi mật khẩu gỡ agent")
        remove_buttons += dialog.get_by_role("button", name=re.compile("Xóa mật khẩu")).count()
        case.checks.append((remove_buttons == 0, f"\"Xóa mật khẩu\" buttons on the page and in the dialog: {remove_buttons} (want 0)"))
        answer, _ = self.save_password(dialog, new, confirm, self.new_password)
        reminder = dialog.get_by_role("alert").filter(has_text="hãy Tạo lại các bộ cài")
        expect(reminder).to_have_count(1)
        case.checks.append((answer.status == 200, f"PUT uninstall-password (change) -> {answer.status}; the dialog stays "
                                                  "open with the reminder about offline machines and old packages"))
        self.shot("agent-packages-password-changed.png", full_page=False)
        dialog.get_by_role("button", name="Đóng", exact=True).click()
        expect(dialog).to_be_hidden()

        old_hash, self.hash = self.hash, self.heartbeat_hash_for(self.new_password)
        case.checks.append((self.hash != old_hash and _pbkdf2_matches(self.hash, self.new_password),
                            f"heartbeat sends a new hash ({self.hash != old_hash}) that matches the new password"))
        self.check_msi(case, old_hash.ljust(MSI_HASH_LENGTH, "_"), "still the old hash: built before the change")
        rebuilt = self.api.call("POST", f"{PACKAGES_PATH}/{self.package['id']}/regenerate")
        case.checks.append((rebuilt["package"]["status"] == "ready", f"package regenerated: {rebuilt['package']['status']}"))
        self.check_msi(case, self.hash.ljust(MSI_HASH_LENGTH, "_"), "the new hash after Tạo lại")

    def cleanup(self, case: CaseResult):
        if self.agent:
            self.agent.close()
        removed, problems = remove_leftovers(self.api, self.settings)
        self.removed, self.problems = removed, problems
        case.checks.append((not problems, "removed: " + ("; ".join(removed) or "nothing")
                            + ("; problems: " + "; ".join(problems) if problems else "")))
        left = ([f"unit {u['id']}" for u in self.api.items("/api/org-units")
                 if u["id"] == UNIT_ID or (u.get("display_name") or "").startswith(self.settings.prefix)]
                + [f"machine {m['name']}" for m in self.api.items("/api/machines")
                   if m["name"].upper().startswith("UAT")]
                + [f"package {p['id']}" for p in _uat_packages(self.api)])
        case.checks.append((not left, "nothing named UAT left: " + (", ".join(left) or "none")))
        restored = _stored_hash(self.settings) == self.original_hash
        case.checks.append((restored, f"the stack's uninstall password hash is back as before the run ({restored})"))

    # ── Run ─────────────────────────────────────────────────────────────────
    def run(self, browser: Browser, keep: bool, sequential: bool, report) -> SuiteResult:
        """`sequential` does not apply. With `keep` the cleanup case is skipped."""
        started = time.monotonic()
        result = SuiteResult(self.name)
        self.failure_shot.unlink(missing_ok=True)
        page = browser.new_page(viewport=VIEWPORT, locale="vi-VN")
        self.console = AdminConsole(page, self.settings.base_url)
        aborted = False
        try:
            self.console.login(self.settings.admin_user, self.settings.admin_password)
            for spec in self.cases:
                if spec["check"] == "cleanup" and keep:
                    continue
                case = CaseResult(spec["name"])
                result.cases.append(case)
                if aborted and spec["check"] != "cleanup":
                    case.error = "not run: the suite was aborted"
                    continue
                try:
                    getattr(self, spec["check"])(case)
                    if not case.passed:
                        self.console.screenshot(self.failure_shot)
                except SuiteAborted as error:
                    result.error, aborted = f"aborted: {error}", True
                except (Exception, grpc.RpcError) as error:  # noqa: BLE001 — report it, go on
                    case.error = f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"
                    self.console.screenshot(self.failure_shot)
        except Exception as error:  # noqa: BLE001
            result.error = f"{type(error).__name__}: {error}"
            if not keep:
                self.removed, self.problems = remove_leftovers(self.api, self.settings)
        finally:
            page.close()
        result.cleanup = "; ".join(self.removed + self.problems)
        result.cleanup_ok = not self.problems
        result.seconds = time.monotonic() - started
        report(result)
        return result
