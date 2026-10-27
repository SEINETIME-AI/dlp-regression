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


class ApiAnswers:
    """The status of every console request whose path contains `marker`."""

    def __init__(self, page: Page, marker: str):
        self.answers: list[tuple[str, str, int]] = []
        page.on("response", lambda r: self.answers.append((r.request.method, r.url, r.status))
                if marker in r.url else None)

    def last(self, method: str) -> tuple[str, int] | None:
        for answer_method, url, status in reversed(self.answers):
            if answer_method == method:
                return urllib.parse.unquote(url.split("/api/", 1)[-1]), status
        return None


def _taxonomy_path(code: str) -> str:
    return f"/api/taxonomy/document-types/{urllib.parse.quote(code, safe='')}"


def _remove_document_types(api: Api, codes: list[str]) -> tuple[list[str], list[str]]:
    """Deactivates and deletes the given types, children first. Returns (removed, problems)."""
    removed, problems = [], []
    existing = {t["code"] for t in api.items("/api/taxonomy/document-types")}
    for code in codes:
        if code not in existing:
            continue
        try:
            try:
                api.call("PATCH", _taxonomy_path(code) + "/deactivate")
            except ApiError as error:
                if error.status != 409:  # 409: already inactive
                    raise
            api.call("DELETE", _taxonomy_path(code))
            removed.append(f"document type {code}")
        except ApiError as error:
            problems.append(f"document type {code} left: {error}")
    return removed, problems


def child_document_type_actions(console: AdminConsole, api: Api, case: CaseResult) -> tuple[list[str], list[str]]:
    """Tester note on TC-A-01 (bug 69): a child type whose code contains "/" got 404 on deactivate.
    The console sends the code as %2F; edit, deactivate and delete must all reach the type."""
    parent, child = ("uat-cha", "UAT Cha"), ("uat-cha/con", "UAT Con")
    renamed = "UAT Con đã sửa"
    answers = ApiAnswers(console.page, "/api/taxonomy/document-types/")
    _, leftovers = _remove_document_types(api, [child[0], parent[0]])  # an earlier run that failed
    if leftovers:
        case.checks.append((False, "an earlier run's types could not be removed: " + "; ".join(leftovers)))
        return [], leftovers
    try:
        console.create_document_type(*parent)
        console.create_child_document_type(parent[1], *child)
        created = next((t for t in api.items("/api/taxonomy/document-types") if t["code"] == child[0]), None)
        case.checks.append((bool(created and created.get("parent_code") == parent[0]),
                            f"child {child[0]} created under {parent[0]}"))

        # Each step needs the one before it, so the first failure ends the case.
        for action, method, expected, run in (
            ("edit", "PUT", 200, lambda: console.rename_document_type(child[1], renamed)),
            ("deactivate", "PATCH", 200, lambda: console.deactivate_document_type(renamed)),
            ("delete", "DELETE", 204, lambda: console.delete_document_type(renamed)),
        ):
            failure = ""
            try:
                run()
            except Exception as error:  # noqa: BLE001 — the console did not finish the action
                failure = f", console stuck: {str(error).splitlines()[0]}"
            answer = answers.last(method)
            ok = not failure and answer is not None and answer[1] == expected
            case.checks.append((ok, f"{action}: {method} {answer[0] if answer else '(no request)'} -> "
                                    f"{answer[1] if answer else '-'} (want {expected}){failure}"))
            if not ok:
                return _remove_document_types(api, [child[0], parent[0]])
            if action == "deactivate":
                case.checks.append((console.document_type_row(renamed).get_by_text("Ngừng dùng").count() == 1,
                                    "deactivated child shows Ngừng dùng"))
        console.open_document_types()
        case.checks.append((console.document_type_row(renamed).count() == 0, "deleted child is gone from the tree"))
    except Exception:
        _remove_document_types(api, [child[0], parent[0]])
        raise
    return _remove_document_types(api, [child[0], parent[0]])


CHECKS = {"child_document_type_actions": child_document_type_actions}


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
