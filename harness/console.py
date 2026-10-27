"""A console suite: admin actions checked in the console alone, without the agent.
Each case names one check below; a check records every API answer the console
got and what the screen shows, and removes what it created."""
import time
import urllib.parse

import yaml
from playwright.sync_api import Browser, Page

from harness.api import Api, ApiError
from harness.config import Settings
from harness.suite import CaseResult, SuiteResult
from harness.ui import AdminConsole

DOC_TYPES, SECRECY = "document-types", "secrecy-labels"  # API kinds
TAB = {DOC_TYPES: "Loại tài liệu", SECRECY: "Mức bảo mật"}


class ApiAnswers:
    """The status of every console request whose path contains `marker`."""

    def __init__(self, page: Page, marker: str):
        self.answers: list[tuple[str, str, int]] = []
        page.on("response", lambda r: self.answers.append((r.request.method, r.url, r.status))
                if marker in r.url else None)

    def last(self, method: str, since: int = 0) -> tuple[str, int] | None:
        """The newest answer to `method` among those after the first `since` answers."""
        for answer_method, url, status in reversed(self.answers[since:]):
            if answer_method == method:
                return urllib.parse.unquote(url.split("/api/", 1)[-1]), status
        return None


def _taxonomy_path(kind: str, code: str) -> str:
    return f"/api/taxonomy/{kind}/{urllib.parse.quote(code, safe='')}"


def _stored(api: Api, kind: str, code: str) -> dict | None:
    return next((t for t in api.items(f"/api/taxonomy/{kind}") if t["code"] == code), None)


def _remove_taxonomy(api: Api, kind: str, codes: list[str]) -> tuple[list[str], list[str]]:
    """Deactivates and deletes the given items, children first. Returns (removed, problems)."""
    removed, problems = [], []
    existing = {t["code"] for t in api.items(f"/api/taxonomy/{kind}")}
    for code in codes:
        if code not in existing:
            continue
        try:
            try:
                api.call("PATCH", _taxonomy_path(kind, code) + "/deactivate")
            except ApiError as error:
                if error.status != 409:  # 409: already inactive
                    raise
            api.call("DELETE", _taxonomy_path(kind, code))
            removed.append(f"{kind[:-1]} {code}")
        except ApiError as error:
            problems.append(f"{kind[:-1]} {code} left: {error}")
    return removed, problems


def _step(case: CaseResult, answers: ApiAnswers, action: str, method: str, expected: int, run) -> bool:
    """Runs one console action and records the answer to its request; False ends the case."""
    failure = ""
    since = len(answers.answers)
    try:
        run()
    except Exception as error:  # noqa: BLE001 — the console did not finish the action
        failure = f", console stuck: {str(error).splitlines()[0]}"
    answer = answers.last(method, since)
    ok = not failure and answer is not None and answer[1] == expected
    case.checks.append((ok, f"{action}: {method} {answer[0] if answer else '(no request)'} -> "
                            f"{answer[1] if answer else '-'} (want {expected}){failure}"))
    return ok


def _guarded(api: Api, case: CaseResult, kind: str, codes: list[str], body) -> tuple[list[str], list[str]]:
    """Removes leftovers of an earlier failed run, runs `body`, then removes `codes` again."""
    _, leftovers = _remove_taxonomy(api, kind, codes)
    if leftovers:
        case.checks.append((False, "an earlier run's items could not be removed: " + "; ".join(leftovers)))
        return [], leftovers
    try:
        body()
    finally:
        cleanup = _remove_taxonomy(api, kind, codes)
    return cleanup


def child_document_type_actions(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Tester note on TC-A-01 (bug 69): a child type whose code contains "/" got 404 on deactivate.
    The console sends the code as %2F; edit, deactivate and delete must all reach the type."""
    parent, child = ("uat-cha", "UAT Cha"), ("uat-cha/con", "UAT Con")
    renamed = "UAT Con đã sửa"
    answers = ApiAnswers(console.page, f"/api/taxonomy/{DOC_TYPES}/")

    def body():
        console.create_document_type(*parent)
        console.create_child_document_type(parent[1], *child)
        created = _stored(api, DOC_TYPES, child[0])
        case.checks.append((bool(created and created.get("parent_code") == parent[0]),
                            f"child {child[0]} created under {parent[0]}"))
        # Each step needs the one before it, so the first failure ends the case.
        if not _step(case, answers, "edit", "PUT", 200, lambda: console.rename_document_type(child[1], renamed)):
            return
        if not _step(case, answers, "deactivate", "PATCH", 200, lambda: console.deactivate_document_type(renamed)):
            return
        case.checks.append((console.document_type_row(renamed).get_by_text("Ngừng dùng").count() == 1,
                            "deactivated child shows Ngừng dùng"))
        if not _step(case, answers, "delete", "DELETE", 204, lambda: console.delete_document_type(renamed)):
            return
        console.open_document_types()
        case.checks.append((console.document_type_row(renamed).count() == 0, "deleted child is gone from the tree"))

    return _guarded(api, case, DOC_TYPES, [child[0], parent[0]], body)


def secrecy_label_reactivate(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Tester note on TC-A-01 (bug 70): a deactivated label (not a system one) could not be
    switched back on, and saving it in the edit dialog switched it on without asking."""
    code, name, renamed = "uat_tam", "UAT Tạm", "UAT Tạm đã sửa"
    tab = TAB[SECRECY]
    answers = ApiAnswers(console.page, f"/api/taxonomy/{SECRECY}/")

    def state(expected: bool, after: str):
        stored = _stored(api, SECRECY, code)
        console.open_secrecy_labels()
        chip = "Đang dùng" if expected else "Ngừng dùng"
        case.checks.append((bool(stored) and stored["active"] is expected
                            and console.secrecy_label_row(code).get_by_text(chip, exact=True).count() == 1,
                            f"after {after}: {'in use' if expected else 'not in use'} (stored active="
                            f"{stored['active'] if stored else '-'}, shows {chip})"))

    def body():
        console.create_secrecy_label(code, name, more_secret_than="public")
        if not _step(case, answers, "deactivate", "PATCH", 200, lambda: console.taxonomy_action(tab, "Ngừng sử dụng", name)):
            return
        state(False, "deactivate")
        if not _step(case, answers, "rename while not in use", "PUT", 200,
                     lambda: console.rename_taxonomy_item(tab, name, renamed)):
            return
        state(False, "rename")
        if not _step(case, answers, "use again", "PATCH", 200, lambda: console.taxonomy_action(tab, "Sử dụng lại", renamed)):
            return
        state(True, "use again")

    return _guarded(api, case, SECRECY, [code], body)


def confirm_before_deactivate(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Tester note on TC-A-01 (bug 72): deactivating or deleting acted on the first click.
    Now a dialog asks first; "Hủy" must send nothing and leave the item as it was."""
    code, name = "uat-xac-nhan", "UAT Xác nhận"
    tab = TAB[DOC_TYPES]
    answers = ApiAnswers(console.page, f"/api/taxonomy/{DOC_TYPES}/")

    def body():
        console.create_document_type(code, name)
        since = len(answers.answers)
        console.taxonomy_action(tab, "Ngừng sử dụng", name, confirm=False)
        stored = _stored(api, DOC_TYPES, code)
        case.checks.append((answers.last("PATCH", since) is None and bool(stored and stored["active"]),
                            "Hủy: no request sent, still in use"))
        if not _step(case, answers, "confirmed deactivate", "PATCH", 200,
                     lambda: console.taxonomy_action(tab, "Ngừng sử dụng", name)):
            return
        since = len(answers.answers)
        console.taxonomy_action(tab, "Xóa", name, confirm=False)
        case.checks.append((answers.last("DELETE", since) is None and _stored(api, DOC_TYPES, code) is not None,
                            "Hủy on delete: no request sent, still there"))
        _step(case, answers, "confirmed delete", "DELETE", 204, lambda: console.taxonomy_action(tab, "Xóa", name))

    return _guarded(api, case, DOC_TYPES, [code], body)


UAT_UNIT, MANAGER, MANAGER_PASSWORD = "uat-phong-tam", "uat.truongphong", "Uat-TruongPhong-2026"
REAL_UNIT = "software-dev"  # the UAT department; its documents and machines are the "other unit"


def _remove_manager(api: Api) -> tuple[list[str], list[str]]:
    """Deletes the UAT manager, then the UAT unit. Returns (removed, problems)."""
    removed, problems = [], []
    try:
        for user in api.items("/api/users"):
            if user["username"].lower() == MANAGER:
                api.call("DELETE", f"/api/users/{user['id']}")
                removed.append(f"user {MANAGER}")
        if any(unit["id"] == UAT_UNIT for unit in api.items("/api/org-units")):
            api.call("DELETE", f"/api/org-units/{UAT_UNIT}")
            removed.append(f"unit {UAT_UNIT}")
    except ApiError as error:
        problems.append(f"manager or unit left: {error}")
    return removed, problems


def manager_sees_own_units(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Backlog (bug 73; documents/endpoints since "fix bug No 25"): a department manager saw
    the dashboard figures, documents and endpoints of every unit. A manager of an empty UAT
    unit must see none of them, and gets the real unit's once it is added to their scope."""
    admin_stats = api.get("/api/dashboard/summary")["stats"]
    foreign_doc = next(iter(api.items("/api/documents", department=REAL_UNIT, limit=1)), None)
    real_machines = api.items("/api/machines", unit=REAL_UNIT)
    if not (admin_stats["total_documents"] and foreign_doc and real_machines):
        case.checks.append((False, f"prerequisite: {REAL_UNIT} needs documents and a machine"))
        return [], []
    _, leftovers = _remove_manager(api)
    if leftovers:
        case.checks.append((False, "an earlier run's manager could not be removed: " + "; ".join(leftovers)))
        return [], leftovers

    parent = api.get(f"/api/org-units/{REAL_UNIT}")["parent_id"]
    context = console.page.context.browser.new_context(viewport={"width": 1500, "height": 1000})
    page = context.new_page()
    manager = AdminConsole(page, console.base_url)

    def summary_on_screen() -> dict:
        with page.expect_response(lambda r: "/api/dashboard/summary" in r.url) as answer:
            manager.open("/dashboard")
        return answer.value.json()["stats"]

    def status(path: str) -> int:
        """A request with the manager's session, as a link to another unit's item sends it."""
        return page.request.get(console.base_url + path).status

    try:
        api.call("POST", "/api/org-units", {"id": UAT_UNIT, "display_name": "UAT Phòng tạm",
                                            "unit_type": "Phòng", "parent_id": parent})
        user = api.call("POST", "/api/users", {
            "username": MANAGER, "display_name": "UAT Trưởng phòng", "role": "department_manager",
            "unit_ids": [UAT_UNIT], "password": MANAGER_PASSWORD, "force_password_change": False})
        manager.login(MANAGER, MANAGER_PASSWORD)

        stats = summary_on_screen()
        case.checks.append((stats["total_documents"] == 0 and stats["sensitive_documents"] == 0
                            and stats["total_endpoints"] == 0,
                            f"dashboard of an empty unit: {stats['total_documents']} documents, "
                            f"{stats['sensitive_documents']} sensitive, {stats['total_endpoints']} endpoints "
                            f"(want 0; admin sees {admin_stats['total_documents']} documents)"))
        with page.expect_response(lambda r: "/api/documents?" in r.url) as answer:
            manager.open("/documents")
        case.checks.append((answer.value.json()["total"] == 0,
                            f"documents page lists {answer.value.json()['total']} (want 0)"))
        doc_path = "/api/documents/" + urllib.parse.quote(foreign_doc["doc_id"], safe="")
        machine_path = f"/api/machines/{real_machines[0]['id']}"
        case.checks.append((status(doc_path) == 404, f"{REAL_UNIT} document by id -> {status(doc_path)} (want 404)"))
        case.checks.append((status(machine_path) == 404,
                            f"{REAL_UNIT} endpoint by id -> {status(machine_path)} (want 404)"))

        # Same manager with the real unit added: its figures and items come back.
        api.call("PUT", f"/api/users/{user['id']}", {
            "display_name": "UAT Trưởng phòng", "role": "department_manager",
            "unit_ids": [UAT_UNIT, REAL_UNIT], "active": True, "force_password_change": False})
        manager.login(MANAGER, MANAGER_PASSWORD)
        stats = summary_on_screen()
        case.checks.append((stats["total_documents"] > 0 and stats["total_endpoints"] == len(real_machines),
                            f"with {REAL_UNIT} in scope: {stats['total_documents']} documents, "
                            f"{stats['total_endpoints']} endpoints (want >0 and {len(real_machines)})"))
        case.checks.append((status(doc_path) == 200, f"{REAL_UNIT} document by id -> {status(doc_path)} (want 200)"))
    finally:
        context.close()
        cleanup = _remove_manager(api)
    return cleanup


POLICY_KEEPING_LABELS = "UAT Policy giữ nhãn"


def _remove_policy(api: Api, name: str) -> list[str]:
    removed = []
    for policy in api.items("/api/policies"):
        if policy["name"] == name:
            if policy.get("is_active"):
                api.call("PATCH", f"/api/policies/{policy['id']}/deactivate")
            api.call("DELETE", f"/api/policies/{policy['id']}")
            removed.append(f"policy {name}")
    return removed


def _policy_using(domain: str | None, level: str | None) -> dict:
    """An inactive policy (the agent never gets it) whose keyword rules assign `domain` and `level`."""
    matchers = [{"kind": "keyword", "value": "uat-giu-nhan"}]
    return {
        "name": POLICY_KEEPING_LABELS, "department": REAL_UNIT, "is_active": False,
        "keyword_domain_rules": [{"id": "uat-giu-loai", "domain": domain, "matchers": matchers}] if domain else [],
        "keyword_secrecy_rules": [{"id": "uat-giu-muc", "level": level, "matchers": matchers}] if level else [],
    }


def label_used_by_policy(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Product rule (2026-10-27, bug 77): a document type or secrecy label that a policy uses
    cannot be switched off, and the console names the policy. Once no policy uses it, it can be
    switched off, no policy can pick it any more, and it can be deleted."""
    type_code, type_name = "uat-giu-loai", "UAT Loại trong policy"
    label_code, label_name = "uat_giu_muc", "UAT Mức trong policy"
    labels = [(DOC_TYPES, type_code, type_name), (SECRECY, label_code, label_name)]
    answers = {kind: ApiAnswers(console.page, f"/api/taxonomy/{kind}/") for kind in (DOC_TYPES, SECRECY)}

    def cleanup() -> tuple[list[str], list[str]]:
        try:
            removed = _remove_policy(api, POLICY_KEEPING_LABELS)
        except ApiError as error:
            return [], [f"policy left: {error}"]
        for kind, code, _ in labels:
            kind_removed, kind_problems = _remove_taxonomy(api, kind, [code])
            removed += kind_removed
            if kind_problems:
                return removed, kind_problems
        return removed, []

    def rejected(body: dict) -> int:
        try:
            api.call("POST", "/api/policies", body)
            _remove_policy(api, POLICY_KEEPING_LABELS)
            return 201
        except ApiError as error:
            return error.status

    _, leftovers = cleanup()
    if leftovers:
        case.checks.append((False, "an earlier run's items could not be removed: " + "; ".join(leftovers)))
        return [], leftovers
    try:
        console.create_document_type(type_code, type_name)
        console.create_secrecy_label(label_code, label_name, more_secret_than="public")
        api.call("POST", "/api/policies", _policy_using(type_code, label_code))

        for kind, code, name in labels:
            if not _step(case, answers[kind], f"switch off {name} while the policy uses it", "PATCH", 409,
                         lambda: console.taxonomy_action(TAB[kind], "Ngừng sử dụng", name)):
                return cleanup()
            shown = console.page.get_by_role("alert").filter(has_text=POLICY_KEEPING_LABELS).count() == 1
            stored = _stored(api, kind, code)
            case.checks.append((shown and bool(stored and stored["active"]),
                                f"{name}: console names \"{POLICY_KEEPING_LABELS}\" ({shown}), still in use "
                                f"({stored['active'] if stored else '-'})"))

        _remove_policy(api, POLICY_KEEPING_LABELS)
        for kind, code, name in labels:
            if not _step(case, answers[kind], f"switch off {name} once no policy uses it", "PATCH", 200,
                         lambda: console.taxonomy_action(TAB[kind], "Ngừng sử dụng", name)):
                return cleanup()
        status = rejected(_policy_using(type_code, None))
        case.checks.append((status == 400, f"a policy picking the switched-off type -> {status} (want 400)"))
        status = rejected(_policy_using(None, label_code))
        case.checks.append((status == 400, f"a policy picking the switched-off label -> {status} (want 400)"))
        for kind, code, name in labels:
            _step(case, answers[kind], f"delete {name}", "DELETE", 204,
                  lambda: console.taxonomy_action(TAB[kind], "Xóa", name))
    finally:
        result = cleanup()
    return result


CHECKS = {
    "child_document_type_actions": child_document_type_actions,
    "secrecy_label_reactivate": secrecy_label_reactivate,
    "confirm_before_deactivate": confirm_before_deactivate,
    "manager_sees_own_units": manager_sees_own_units,
    "label_used_by_policy": label_used_by_policy,
}


class ConsoleSuite:
    def __init__(self, folder, settings: Settings, api: Api, only: list[str] | None = None):
        self.name = folder.name
        spec = yaml.safe_load((folder / "console.yaml").read_text(encoding="utf-8"))
        self.cases = [c for c in spec["cases"] if not only or c["name"] in only]
        if only and (unknown := set(only) - {c["name"] for c in self.cases}):
            raise ValueError(f"unknown case(s) in {self.name}: {', '.join(sorted(unknown))}")
        if unknown_checks := {c["check"] for c in self.cases} - CHECKS.keys():
            raise ValueError(f"unknown check(s) in {self.name}: {', '.join(sorted(unknown_checks))}")
        self.settings = settings
        self.api = api
        self.failure_shot = settings.reports_dir / f"{self.name}-failure.png"

    def run(self, browser: Browser, keep: bool, sequential: bool, report) -> SuiteResult:
        """`keep` and `sequential` do not apply: each check cleans up after itself."""
        started = time.monotonic()
        result = SuiteResult(self.name)
        self.failure_shot.unlink(missing_ok=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        console = AdminConsole(page, self.settings.base_url)
        removed, problems = [], []
        try:
            console.login(self.settings.admin_user, self.settings.admin_password)
            for spec in self.cases:
                case = CaseResult(spec["name"])
                result.cases.append(case)
                try:
                    case_removed, case_problems = CHECKS[spec["check"]](console, self.api, case)
                    removed += case_removed
                    problems += case_problems
                    if not case.passed:
                        console.screenshot(self.failure_shot)
                except Exception as error:  # noqa: BLE001 — report it, go on with the next case
                    case.error = f"{type(error).__name__}: {error}"
                    console.screenshot(self.failure_shot)
        except Exception as error:  # noqa: BLE001
            result.error = f"{type(error).__name__}: {error}"
        finally:
            page.close()
        result.cleanup = "; ".join(removed + problems)
        result.cleanup_ok = not problems
        result.seconds = time.monotonic() - started
        report(result)
        return result
