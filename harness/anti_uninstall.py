"""The anti-uninstall suite (laptop part): the admin sets and clears the agent uninstall password in
the Console; an agent played over gRPC enrolls from a UAT package, gets the hash on heartbeat and
reports the two machine events; the package's MSI carries the hash. Installing and uninstalling the
real agent is the VM's part. The cases build on each other, so they run in order and all together."""
import base64
import hashlib
import hmac
import io
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
SETTINGS_PATH = "/api/system/settings"
ALERT_ACTION = "tamper_protection"
EVENT_CHIPS = {"uninstall_denied": "Gỡ agent bị từ chối", "agent_unclean_stop": "Agent dừng bất thường"}
NOTIFICATIONS = {"uninstall_denied": "Việc gỡ agent trên", "agent_unclean_stop": "đã dừng bất thường"}
VIEWPORT = {"width": 1440, "height": 900}


class SuiteAborted(RuntimeError):
    """A precondition failed: nothing may be changed."""


def _password_set(api: Api) -> bool:
    return bool(api.get(SETTINGS_PATH)["agent"].get("uninstall_password_set"))


def _uat_machines(api: Api) -> list[dict]:
    return [m for m in api.items("/api/machines") if m.get("department") == UNIT_ID]


def _uat_packages(api: Api) -> list[dict]:
    return [p for p in api.items("/api/agent-packages") if p["organization_unit_id"] == UNIT_ID]


def _uat_unit_exists(api: Api) -> bool:
    return any(unit["id"] == UNIT_ID for unit in api.items("/api/org-units"))


def _delete_machine_rows(settings: Settings):
    """The API only revokes a machine; its row (events cascade) goes with SQL, as UAT_PSQL says."""
    sql = f"DELETE FROM machines WHERE department = '{UNIT_ID}'"
    result = subprocess.run(shlex.split(settings.psql, posix=False) + ["-c", sql], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.strip() or result.stdout.strip()}")


def remove_leftovers(api: Api, settings: Settings, clear_password: bool | None = None) -> tuple[list[str], list[str]]:
    """Removes the suite's package, machine and unit. The password is cleared when `clear_password`
    is True, or when None and this suite's unit was still there (an aborted run set it).
    Returns (removed, problems)."""
    removed, problems = [], []
    unit_was_there = _uat_unit_exists(api)
    if clear_password or (clear_password is None and unit_was_there):
        try:
            if _password_set(api):
                api.call("PUT", PASSWORD_PATH, {"password": None})
                removed.append("uninstall password")
        except ApiError as error:
            problems.append(f"uninstall password left: {error}")
    try:
        for package in _uat_packages(api):
            api.call("DELETE", f"/api/agent-packages/{package['id']}")
            removed.append(f"package {package['id']}")
        machines = _uat_machines(api)
        for machine in machines:
            if machine.get("status") != "revoked":
                api.call("PATCH", f"/api/machines/{machine['id']}/revoke")
        if machines:
            _delete_machine_rows(settings)
            removed += [f"machine {m['name']}" for m in machines]
        if unit_was_there:
            api.call("DELETE", f"/api/org-units/{UNIT_ID}")
            removed.append(f"unit {UNIT_ID}")
    except (ApiError, RuntimeError) as error:
        problems.append(f"{UNIT_NAME} objects left: {error}")
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
        self.password_set_by_run = False
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

    def open_password_block(self):
        self.console.open("/system?section=agent")
        new = self.page.get_by_label("Mật khẩu mới", exact=True)
        form = self.page.locator("form").filter(has=new)
        return form, new, form.get_by_label("Nhập lại mật khẩu", exact=True)

    def chip(self, text: str):
        return self.page.get_by_text(text, exact=True)

    def storage_text(self) -> str:
        return self.page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})")

    def download_msi(self, folder: Path) -> Path:
        """The package's installer; the download is a zip (MSI + helper script) when the template has one."""
        request = urllib.request.Request(f"{self.api.base_url}/api/agent-packages/{self.package['id']}/download")
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

    def check_msi(self, case: CaseResult, expected: str, what: str):
        with tempfile.TemporaryDirectory(prefix="uat-msi-") as folder:
            value = _msi_property(self.download_msi(Path(folder)), MSI_PROPERTY)
        shown = "absent" if value is None else f"{len(value)} chars, starts {value[:21]!r}, ends {value[-4:]!r}"
        case.checks.append((value == expected, f"MSI {MSI_PROPERTY}: {shown} (want {what})"))

    # ── Cases ───────────────────────────────────────────────────────────────
    def precondition_no_password(self, case: CaseResult):
        already = _password_set(self.api)
        case.checks.append((not already, f"agent.uninstall_password_set before the run: {already} (want false)"))
        if already:
            raise SuiteAborted("an uninstall password is already set; its hash cannot be restored, so nothing is changed")
        removed, problems = remove_leftovers(self.api, self.settings, clear_password=False)
        case.checks.append((not problems, "an earlier run's leftovers: " + ("; ".join(removed + problems) or "none")))
        if problems:
            raise SuiteAborted("an earlier run's objects could not be removed")

    def console_set_password(self, case: CaseResult):
        answers = ApiAnswers(self.page, PASSWORD_PATH)
        form, new, confirm = self.open_password_block()
        expect(self.chip("Chưa đặt")).to_be_visible()
        case.checks.append((True, "System page shows \"Mật khẩu gỡ agent\" with chip \"Chưa đặt\""))
        self.shot("system-uninstall-password-unset.png")

        save = form.get_by_role("button", name="Lưu", exact=True)
        for value, helper, what, shot in (("   ", "Mật khẩu không được để trống.", "whitespace only",
                                           "system-uninstall-password-blank-error.png"),
                                          ("Mật khẩ", "Mật khẩu cần ít nhất 8 ký tự.", "7 characters", None)):
            since = len(answers.answers)
            new.fill(value)
            confirm.fill(value)
            confirm.press("Enter")
            self.page.wait_for_timeout(1000)
            helper_shown = form.get_by_text(helper, exact=True).count() == 1
            sent = answers.last("PUT", since)
            case.checks.append((helper_shown and save.is_disabled() and sent is None,
                                f"{what}: helper \"{helper}\" shown ({helper_shown}), Lưu disabled "
                                f"({save.is_disabled()}), request sent: {sent or 'none'}"))
            if shot:
                self.shot(shot)

        new.fill(self.password)
        confirm.fill(self.password)
        is_put = lambda r: r.url.endswith(PASSWORD_PATH) and r.request.method == "PUT"  # noqa: E731
        is_get = lambda r: r.url.endswith(SETTINGS_PATH) and r.request.method == "GET"  # noqa: E731
        with self.page.expect_response(is_get) as reloaded, self.page.expect_response(is_put) as answer:
            save.click()
        self.password_set_by_run = True
        put_body, get_body = answer.value.text(), reloaded.value.text()
        case.checks.append((answer.value.status == 200 and '"uninstall_password_set":true' in put_body
                            and "pbkdf2" not in put_body,
                            f"PUT uninstall-password -> {answer.value.status} {put_body.strip()}"))
        expect(self.chip("Đã đặt")).to_be_visible()
        empty = new.input_value() == "" and confirm.input_value() == ""
        case.checks.append((empty, f"chip \"Đã đặt\" shown, both fields empty ({empty})"))
        leaks = [where for where, text in (("GET settings", get_body), ("browser storage", self.storage_text()))
                 if self.password in text or "pbkdf2" in text]
        case.checks.append(('"uninstall_password_set":true' in get_body and not leaks,
                            "GET settings has uninstall_password_set=true; password or pbkdf2 found in: "
                            + (", ".join(leaks) or "nowhere")))
        self.shot("system-uninstall-password-set.png")

    def heartbeat_carries_hash(self, case: CaseResult):
        parent = self.api.get(f"/api/org-units/{self.settings.department_code}")["parent_id"]
        self.api.call("POST", "/api/org-units", {"id": UNIT_ID, "display_name": UNIT_NAME,
                                                 "unit_type": "Phòng", "parent_id": parent})
        policy = next(p for p in self.api.items("/api/agent-policies") if p.get("is_active"))
        created = self.api.call("POST", "/api/agent-packages",
                                {"organization_unit_id": UNIT_ID, "agent_policy_id": policy["id"], "max_enrollments": 1})
        self.package = created["package"]
        case.checks.append((self.package["status"] == "ready",
                            f"package {self.package['id']} for {UNIT_NAME}: {self.package['status']}"))
        self.agent = SimulatedAgent(self.settings.grpc_target, self.settings.backend_proto_dir, MACHINE)
        self.agent.enroll(self.package["id"], created["bootstrap_token"])
        case.checks.append((bool(self.agent.machine_id and self.agent.api_key),
                            f"{MACHINE} enrolled as {self.agent.machine_id}"))

        answer = self.agent.heartbeat()
        present = answer.HasField("uninstall_password_hash")
        self.hash = answer.uninstall_password_hash
        case.checks.append((present and self.hash.startswith(HASH_PREFIX),
                            f"heartbeat uninstall_password_hash present ({present}), starts {self.hash[:21]!r}, "
                            f"{len(self.hash)} chars"))
        case.checks.append((_pbkdf2_matches(self.hash, self.password),
                            "hashlib PBKDF2-HMAC-SHA256 of the NFC UTF-8 password matches the hash"))

    def machine_events_in_console(self, case: CaseResult):
        with self.page.expect_response(lambda r: "/api/console/stream" in r.url, timeout=20000):
            self.console.open("/classification-events")
        self.page.wait_for_timeout(1000)
        answer = self.agent.push_machine_events(list(EVENT_CHIPS), ALERT_ACTION)
        case.checks.append((answer.accepted_count == 2,
                            f"BatchSync of uninstall_denied + agent_unclean_stop without file fields: "
                            f"accepted {answer.accepted_count} (want 2)"))

        rows = self.page.locator(".MuiDataGrid-row").filter(has_text=MACHINE)
        expect(rows).to_have_count(2, timeout=45000)
        for event_type, chip in EVENT_CHIPS.items():
            row = rows.filter(has_text=chip)
            cells = [row.locator(f"[data-field={field}]").inner_text().strip() if row.count() == 1 else "-"
                     for field in ("file_name", "secrecy_label")]
            case.checks.append((row.count() == 1 and cells == ["—", "—"],
                                f"events page: {event_type} row with chip \"{chip}\", document and secrecy "
                                f"cells {cells} (want —)"))
        self.shot("events-uninstall-denied.png")

        rows.filter(has_text=EVENT_CHIPS["uninstall_denied"]).click()
        drawer = self.page.get_by_role("region", name="Chi tiết sự kiện")
        button = drawer.get_by_role("link", name="Mở máy trạm")
        expect(button).to_be_visible()
        href = button.get_attribute("href") or ""
        no_file_rows = drawer.get_by_text("Đường dẫn", exact=True).count() == 0
        case.checks.append((href.endswith(f"/endpoints?q={MACHINE}") and no_file_rows,
                            f"drawer: button \"Mở máy trạm\" -> {href}, no file rows ({no_file_rows})"))
        self.shot("event-drawer-machine-event.png", full_page=False)
        self.page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()

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
            self.page.wait_for_url(lambda url: "/endpoints" in url)
            query = urllib.parse.urlparse(self.page.url).query
            case.checks.append((shown and urllib.parse.parse_qs(query).get("q") == [MACHINE],
                                f"bell: \"{EVENT_CHIPS[event_type]}\" for {MACHINE} (message ok: {shown}) "
                                f"opens {urllib.parse.urlparse(self.page.url).path}?{query}"))
            self.page.go_back()
            self.page.wait_for_timeout(500)

    def package_msi_carries_hash(self, case: CaseResult):
        self.check_msi(case, self.hash.ljust(MSI_HASH_LENGTH, "_"),
                       f"the heartbeat hash then '_' to {MSI_HASH_LENGTH}")

    def console_clear_password(self, case: CaseResult):
        form, _, _ = self.open_password_block()
        form.get_by_role("button", name="Xóa mật khẩu", exact=True).click()
        dialog = self.console.dialog()
        expect(dialog).to_be_visible()
        text = dialog.inner_text()
        case.checks.append(("mọi máy trạm" in text, "confirm dialog says protection turns off on \"mọi máy trạm\""))
        self.shot("system-uninstall-password-clear-dialog.png", full_page=False)
        is_put = lambda r: r.url.endswith(PASSWORD_PATH) and r.request.method == "PUT"  # noqa: E731
        with self.page.expect_response(is_put) as answer:
            dialog.get_by_role("button", name="Xóa mật khẩu", exact=True).click()
        case.checks.append((answer.value.status == 200 and '"uninstall_password_set":false' in answer.value.text(),
                            f"PUT uninstall-password null -> {answer.value.status} {answer.value.text().strip()}"))
        expect(self.chip("Chưa đặt")).to_be_visible()
        stored = _password_set(self.api)
        case.checks.append((not stored, f"chip \"Chưa đặt\" shown, uninstall_password_set={stored}"))
        if not stored:
            self.password_set_by_run = False

        heartbeat = self.agent.heartbeat()
        present = heartbeat.HasField("uninstall_password_hash")
        case.checks.append((present and heartbeat.uninstall_password_hash == "",
                            f"heartbeat uninstall_password_hash present ({present}), "
                            f"value {heartbeat.uninstall_password_hash!r} (want '')"))
        rebuilt = self.api.call("POST", f"/api/agent-packages/{self.package['id']}/regenerate")
        case.checks.append((rebuilt["package"]["status"] == "ready", f"package regenerated: {rebuilt['package']['status']}"))
        self.check_msi(case, "_" * MSI_HASH_LENGTH, f"{MSI_HASH_LENGTH} '_'")

    def cleanup(self, case: CaseResult):
        if self.agent:
            self.agent.close()
        removed, problems = remove_leftovers(self.api, self.settings, clear_password=self.password_set_by_run)
        self.removed, self.problems = removed, problems
        case.checks.append((not problems, "removed: " + ("; ".join(removed) or "nothing")
                            + ("; problems: " + "; ".join(problems) if problems else "")))
        left = ([f"unit {u['id']}" for u in self.api.items("/api/org-units")
                 if u["id"] == UNIT_ID or (u.get("display_name") or "").startswith(self.settings.prefix)]
                + [f"machine {m['name']}" for m in self.api.items("/api/machines")
                   if m["name"].upper().startswith("UAT")]
                + [f"package {p['id']}" for p in _uat_packages(self.api)])
        case.checks.append((not left, "nothing named UAT left: " + (", ".join(left) or "none")))
        password_left = _password_set(self.api)
        case.checks.append((not password_left, f"uninstall_password_set after the run: {password_left} (want false)"))

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
                self.removed, self.problems = remove_leftovers(self.api, self.settings, self.password_set_by_run)
        finally:
            page.close()
        result.cleanup = "; ".join(self.removed + self.problems)
        result.cleanup_ok = not self.problems
        result.seconds = time.monotonic() - started
        report(result)
        return result
