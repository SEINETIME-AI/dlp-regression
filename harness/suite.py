"""A suite: the admin sets up its rules once in the console, the agent classifies
every case's files, each case is checked against the label the agent wrote into
its files, then everything the run created is removed."""
import hashlib
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
class CaseResult:
    name: str
    checks: list[tuple[bool, str]] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and bool(self.checks) and all(ok for ok, _ in self.checks)


@dataclass
class SuiteResult:
    name: str
    cases: list[CaseResult] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    cleanup: str = ""
    cleanup_ok: bool = True

    @property
    def passed(self) -> bool:
        return not self.error and all(case.passed for case in self.cases)


def wait_until(predicate, timeout_s: int, interval_s: float = 3.0, what: str = "condition"):
    deadline = time.monotonic() + timeout_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out after {timeout_s}s waiting for {what}")
        time.sleep(interval_s)


class Case:
    def __init__(self, spec: Path, data: Path, root: Path):
        self.name = spec.stem
        self.expected = yaml.safe_load(spec.read_text(encoding="utf-8"))["expected"]
        self.data = data / self.name
        self.root = root / self.name
        self.result = CaseResult(self.name)

    def sources(self) -> list[Path]:
        return [self.data / name for name in self.expected]

    def drop_files(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for source in self.sources():
            shutil.copy2(source, self.root / source.name)

    def labels(self) -> dict | None:
        labels = {name: embedded_label(self.root / name) for name in self.expected}
        return labels if all(labels.values()) else None

    def verify(self, labels: dict):
        for name, expected in self.expected.items():
            want = (expected["secrecy"], sorted(expected.get("domains") or []))
            got = (labels[name]["secrecy"], labels[name]["domains"])
            message = f"{name}: {got[0]} {got[1]}"
            self.result.checks.append((got == want, message if got == want else f"{message} (want {want[0]} {want[1]})"))


class Suite:
    def __init__(self, folder: Path, settings: Settings, api: Api, run_id: str, only: list[str] | None = None):
        self.name = folder.name
        self.setup_spec = yaml.safe_load((folder / "setup.yaml").read_text(encoding="utf-8"))
        self.settings = settings
        self.api = api
        self.root = settings.work_root / run_id / self.name
        self.failure_shot = settings.reports_dir / f"{self.name}-failure.png"
        case_specs = sorted((folder / "cases").glob("*.yaml"))
        if only:
            unknown = set(only) - {p.stem for p in case_specs}
            if unknown:
                raise ValueError(f"unknown case(s) in {self.name}: {', '.join(sorted(unknown))}")
            case_specs = [p for p in case_specs if p.stem in only]
        self.cases = [Case(p, settings.data_dir / self.name, self.root) for p in case_specs]
        self.result = SuiteResult(self.name)
        self.policy_version_before = None

    # ── Run ──────────────────────────────────────────────────────────────────
    def run(self, browser: Browser, keep: bool, sequential: bool, report) -> SuiteResult:
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
            self.wait_for_agent_policy(self.policy_version_before, "the agent to pull the suite's policy")
            time.sleep(5)  # let the watcher on the new data source start
            self.classify(sequential)
        except Exception as error:  # noqa: BLE001 — every failure must still reach cleanup and the report
            self.result.error = f"{type(error).__name__}: {error}"
            if console:
                try:
                    console.screenshot(self.failure_shot)
                except Exception:  # noqa: BLE001
                    pass
        self.result.cases = [case.result for case in self.cases]
        self.result.seconds = time.monotonic() - started
        report(self.result)
        if not keep:
            self.cleanup()
        return self.result

    def check_source_files(self):
        seen = {}
        for case in self.cases:
            for source in case.sources():
                if not source.exists():
                    raise FileNotFoundError(f"missing test file {source}")
                if source.suffix in (".docx", ".xlsx") and embedded_label(source):
                    raise RuntimeError(f"{source} already carries a NextDLP label; regenerate data/")
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if digest in seen:
                    self.result.warnings.append(
                        f"{source.relative_to(self.settings.data_dir)} has the same content as {seen[digest]}; "
                        "the agent answers the second one from its cache")
                seen[digest] = source.relative_to(self.settings.data_dir)

    def setup(self, console: AdminConsole):
        settings = self.settings
        machine = self.api.machine(settings.machine)
        if machine["department"] != settings.department_code:
            raise RuntimeError(f"{machine['name']} is in {machine['department']}, not {settings.department_code}")
        if self.api.delete_uat_objects(settings.prefix):  # leftovers of an interrupted run
            self.wait_for_agent_policy(machine["policy_version"], "the agent to drop the leftovers")
        self.policy_version_before = self.api.machine(settings.machine)["policy_version"]

        for doc_type in self.setup_spec.get("document_types", []):
            if not self.api.document_type(doc_type["code"]):
                console.create_document_type(doc_type["code"], doc_type["name"])

        self.root.mkdir(parents=True, exist_ok=True)
        console.create_data_source(f"{settings.prefix}{self.name}", str(self.root),
                                   settings.company_name, settings.department_name)

        secrecy_names = {s["code"]: s["display_name"] for s in self.api.items("/api/taxonomy/secrecy-labels")}
        type_names = {t["code"]: t["name"] for t in self.setup_spec.get("document_types", [])}
        for policy in self.setup_spec["policies"]:
            if not policy["name"].startswith(settings.prefix):
                raise ValueError(f"policy name must start with {settings.prefix!r}: {policy['name']}")
            console.create_policy(policy["name"], settings.company_name, settings.department_name,
                                  policy["conditions"], secrecy_names, type_names)

    def wait_for_agent_policy(self, version_before, what: str):
        def pulled():
            machine = self.api.machine(self.settings.machine)
            return not machine["policy_dirty"] and machine["policy_version"] != version_before
        wait_until(pulled, self.settings.policy_timeout_s, what=what)

    def classify(self, sequential: bool):
        """The agent writes its decision into each file right after classifying it."""
        batches = [[case] for case in self.cases] if sequential else [self.cases]
        for batch in batches:
            for case in batch:
                case.drop_files()
            pending = list(batch)
            deadline = time.monotonic() + self.settings.classify_timeout_s
            while pending and time.monotonic() < deadline:
                for case in list(pending):
                    if labels := case.labels():
                        case.verify(labels)
                        pending.remove(case)
                time.sleep(1)
            for case in pending:
                case.result.error = f"no label after {self.settings.classify_timeout_s}s"

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def run_documents(self) -> list[dict]:
        root = str(self.root).lower()
        return [d for d in self.api.documents(self.settings.department_code)
                if d["file_path"].lower().startswith(root)]

    def cleanup(self):
        notes, problems = [], []
        try:
            dropped = {name for case in self.cases if case.root.exists() for name in case.expected}
            # Delete while the agent still watches the folder, so it reports the deletions.
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)
            if self.root.parent.exists() and not any(self.root.parent.iterdir()):
                self.root.parent.rmdir()
            # Purging before the agent has reported the files (and their deletion) would
            # let a late report recreate rows, so wait until every file shows as removed.
            if dropped:
                wait_until(lambda: {Path(d["file_path"]).name for d in self.run_documents()
                                    if d["status"] == "removed"} >= dropped,
                           self.settings.classify_timeout_s, what="the agent to report the deleted files")
            version_with_suite = self.api.machine(self.settings.machine)["policy_version"]
            removed = self.api.delete_uat_objects(self.settings.prefix)
            notes += removed
            if removed:
                self.wait_for_agent_policy(version_with_suite, "the agent to go back to the real policy")
                notes.append("agent back on the real policy only")
            notes.append(f"{self.api.purge_documents(str(self.root))} inventory row(s) purged")
            for doc_type in self.setup_spec.get("document_types", []):
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
        if types := [t["code"] for t in self.setup_spec.get("document_types", []) if self.api.document_type(t["code"])]:
            problems.append(f"document types left: {types}")
        if labeled := [str(s) for case in self.cases for s in case.sources()
                       if s.suffix in (".docx", ".xlsx") and embedded_label(s)]:
            problems.append(f"originals in data/ got a label: {labeled}")
        self.result.cleanup_ok = not problems
        self.result.cleanup = "; ".join(problems) if problems else ", ".join(notes)
