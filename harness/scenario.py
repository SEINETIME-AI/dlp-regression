"""One UAT scenario: the admin sets it up in the UI, the agent classifies the
files, the result is checked in the UI, the API and the files, then
everything the run created is removed."""
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from playwright.sync_api import Browser

from harness.api import Api
from harness.config import Settings
from harness.files import embedded_label
from harness.ui import AdminConsole


@dataclass
class Result:
    name: str
    title: str = ""
    passed: bool = False
    checks: list[tuple[bool, str]] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0
    cleanup: str = ""
    cleanup_ok: bool = True

    def check(self, ok: bool, message: str):
        self.checks.append((ok, message))


def wait_until(predicate, timeout_s: int, interval_s: float = 3.0, what: str = "condition"):
    deadline = time.monotonic() + timeout_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out after {timeout_s}s waiting for {what}")
        time.sleep(interval_s)


class Scenario:
    def __init__(self, folder: Path, settings: Settings, api: Api, run_id: str):
        self.name = folder.name
        self.spec = yaml.safe_load((folder / "scenario.yaml").read_text(encoding="utf-8"))
        self.settings = settings
        self.api = api
        self.data = settings.data_dir / self.name
        self.root = settings.work_root / run_id / self.name
        self.failure_shot = settings.reports_dir / f"{self.name}-failure.png"
        self.result = Result(self.name, self.spec.get("title", ""))
        self.policy_version_before = None

    # ── Steps ────────────────────────────────────────────────────────────────
    def run(self, browser: Browser, keep: bool, report) -> Result:
        """Reports the classification result as soon as it is known, then cleans up."""
        started = time.monotonic()
        console = None
        self.failure_shot.unlink(missing_ok=True)
        try:
            self.check_source_files()
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            console = AdminConsole(page, self.settings.base_url)
            console.login(self.settings.admin_user, self.settings.admin_password)
            self.setup(console)
            self.wait_for_policy()
            self.drop_files()
            self.verify(self.wait_for_labels())
        except Exception as error:  # noqa: BLE001 — every failure must still reach cleanup and the report
            self.result.error = f"{type(error).__name__}: {error}"
            if console:
                try:
                    console.screenshot(self.failure_shot)
                except Exception:  # noqa: BLE001
                    pass
        self.result.passed = not self.result.error and all(ok for ok, _ in self.result.checks)
        self.result.seconds = time.monotonic() - started
        report(self.result)
        if not keep:
            self.cleanup()
        return self.result

    def check_source_files(self):
        for name in self.spec["expected"]:
            source = self.data / name
            if not source.exists():
                raise FileNotFoundError(f"missing test file {source}")
            if source.suffix in (".docx", ".xlsx") and embedded_label(source):
                raise RuntimeError(f"{source} already carries a NextDLP label; regenerate data/")

    def setup(self, console: AdminConsole):
        settings = self.settings
        machine = self.api.machine(settings.machine)
        if machine["department"] != settings.department_code:
            raise RuntimeError(f"{machine['name']} is in {machine['department']}, not {settings.department_code}")
        if self.api.delete_uat_objects(settings.prefix):  # leftovers of an interrupted run
            self.wait_for_agent_policy(machine["policy_version"], "the agent to drop the leftovers")
        self.policy_version_before = self.api.machine(settings.machine)["policy_version"]

        for doc_type in self.spec.get("document_types", []):
            if not self.api.document_type(doc_type["code"]):
                console.create_document_type(doc_type["code"], doc_type["name"])

        self.root.mkdir(parents=True, exist_ok=True)
        console.create_data_source(f"{settings.prefix}{self.name}", str(self.root),
                                   settings.company_name, settings.department_name)

        secrecy_names = {s["code"]: s["display_name"] for s in self.api.items("/api/taxonomy/secrecy-labels")}
        type_names = {t["code"]: t["name"] for t in self.spec.get("document_types", [])}
        policy = self.spec["policy"]
        console.create_policy(policy["name"], settings.company_name, settings.department_name,
                              policy["conditions"], secrecy_names, type_names)

    def wait_for_agent_policy(self, version_before, what: str):
        def pulled():
            machine = self.api.machine(self.settings.machine)
            return not machine["policy_dirty"] and machine["policy_version"] != version_before
        wait_until(pulled, self.settings.policy_timeout_s, what=what)

    def wait_for_policy(self):
        self.wait_for_agent_policy(self.policy_version_before, "the agent to pull the UAT policy")
        time.sleep(5)  # let the watcher on the new data source start

    def drop_files(self):
        for name in self.spec["expected"]:
            shutil.copy2(self.data / name, self.root / name)

    def run_documents(self) -> list[dict]:
        root = str(self.root).lower()
        return [d for d in self.api.documents(self.settings.department_code)
                if d["file_path"].lower().startswith(root)]

    def wait_for_labels(self) -> dict[str, dict]:
        """The agent writes its decision into each file right after classifying it."""
        def labeled():
            labels = {name: embedded_label(self.root / name) for name in self.spec["expected"]}
            return labels if all(labels.values()) else None
        return wait_until(labeled, self.settings.classify_timeout_s, interval_s=1.0,
                          what="the agent to classify the files")

    def verify(self, labels: dict[str, dict]):
        for name, expected in self.spec["expected"].items():
            want = (expected["secrecy"], sorted(expected.get("domains", [])))
            got = (labels[name]["secrecy"], labels[name]["domains"])
            message = f"{name}: {got[0]} {got[1]}"
            self.result.check(got == want, message if got == want else f"{message} (want {want[0]} {want[1]})")

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def cleanup(self):
        notes, problems = [], []
        try:
            # Delete while the agent still watches the folder, so the inventory marks them removed.
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)
                time.sleep(5)
            if self.root.parent.exists() and not any(self.root.parent.iterdir()):
                self.root.parent.rmdir()
            # Purging before the agent has reported the files (and their deletion) would let a
            # late report recreate rows, so wait until every file shows up as removed.
            expected = set(self.spec["expected"])
            wait_until(lambda: {Path(d["file_path"]).name for d in self.run_documents()
                                if d["status"] == "removed"} >= expected,
                       self.settings.classify_timeout_s, what="the agent to report the deleted files")
            version_with_uat = self.api.machine(self.settings.machine)["policy_version"]
            removed = self.api.delete_uat_objects(self.settings.prefix)
            notes += removed
            if removed:
                self.wait_for_agent_policy(version_with_uat, "the agent to go back to the real policy")
                notes.append("agent back on the real policy only")
            notes.append(f"{self.api.purge_documents(str(self.root))} inventory row(s) purged")
            for doc_type in self.spec.get("document_types", []):
                if self.api.document_type(doc_type["code"]):
                    self.api.delete_document_type(doc_type["code"])
                    notes.append(f"document type {doc_type['code']}")
        except Exception as error:  # noqa: BLE001
            problems.append(str(error))

        # Nothing of the run may outlive it: no labeled file, no inventory row, no document type.
        if self.root.exists():
            problems.append(f"work folder {self.root} still exists")
        if rows := [d["file_path"] for d in self.run_documents()]:
            problems.append(f"inventory rows left: {rows}")
        if types := [t["code"] for t in self.spec.get("document_types", []) if self.api.document_type(t["code"])]:
            problems.append(f"document types left: {types}")
        if labeled := [n for n in self.spec["expected"]
                       if (self.data / n).suffix in (".docx", ".xlsx") and embedded_label(self.data / n)]:
            problems.append(f"originals in data/ got a label: {labeled}")
        self.result.cleanup_ok = not problems
        self.result.cleanup = "; ".join(problems) if problems else ", ".join(notes)
