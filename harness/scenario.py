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
    def __init__(self, folder: Path, settings: Settings, api: Api, run_id: str, report_dir: Path):
        self.name = folder.name
        self.spec = yaml.safe_load((folder / "scenario.yaml").read_text(encoding="utf-8"))
        self.settings = settings
        self.api = api
        self.data = settings.data_dir / self.name
        self.root = settings.work_root / run_id / self.name
        self.report_dir = report_dir / self.name
        self.result = Result(self.name, self.spec.get("title", ""))
        self.policy_version_before = None

    # ── Steps ────────────────────────────────────────────────────────────────
    def run(self, browser: Browser, keep: bool) -> Result:
        started = time.monotonic()
        console = None
        try:
            self.check_source_files()
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            console = AdminConsole(page, self.settings.base_url, self.report_dir)
            console.login(self.settings.admin_user, self.settings.admin_password)
            self.setup(console)
            self.wait_for_policy()
            self.drop_files()
            documents = self.wait_for_documents()
            self.verify(console, documents)
            self.result.passed = all(ok for ok, _ in self.result.checks)
        except Exception as error:  # noqa: BLE001 — every failure must still reach cleanup and the report
            self.result.error = f"{type(error).__name__}: {error}"
            if console:
                try:
                    console.shot("failure")
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if not keep:
                self.cleanup()
            self.result.passed = not self.result.error and all(ok for ok, _ in self.result.checks)
            self.result.seconds = time.monotonic() - started
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
        leftovers = self.api.delete_uat_objects(settings.prefix)
        if leftovers:
            self.result.check(True, "removed leftovers of an earlier run: " + ", ".join(leftovers))
            self.wait_for_agent_policy(machine["policy_version"], "the agent to drop the leftovers")
        real = [f"{p['name']} v{p['version']}{'' if p.get('is_active') else ' (inactive)'}"
                for p in self.api.department_policies(settings.department_code)]
        self.result.check(True, f"real policies in {settings.department_code}: {', '.join(real) or 'none'}")
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
        self.result.check(True, "agent pulled the merged policy (real + UAT)")
        time.sleep(5)  # let the watcher on the new data source start

    def drop_files(self):
        for name in self.spec["expected"]:
            shutil.copy2(self.data / name, self.root / name)

    def wait_for_documents(self) -> dict[str, dict]:
        root = str(self.root).lower()

        def classified():
            found = {
                Path(d["file_path"]).name: d
                for d in self.api.documents(self.settings.department_code)
                if d["file_path"].lower().startswith(root) and d["status"] != "removed"
            }
            return found if set(found) >= set(self.spec["expected"]) else None
        return wait_until(classified, self.settings.classify_timeout_s, what="the files to be classified")

    def verify(self, console: AdminConsole, documents: dict[str, dict]):
        rows = console.document_rows(str(self.root))
        type_names = {t["code"]: t["name"] for t in self.spec.get("document_types", [])}
        for name, expected in self.spec["expected"].items():
            want_secrecy = expected["secrecy"]
            want_domains = sorted(expected.get("domains", []))

            document = documents[name]
            got_domains = sorted(document.get("domain_label") or [])
            self.result.check(document["secrecy_label"] == want_secrecy,
                              f"{name}: backend secrecy {document['secrecy_label']} (want {want_secrecy})")
            self.result.check(got_domains == want_domains,
                              f"{name}: backend domains {got_domains} (want {want_domains})")

            row = rows.get(name, "")
            self.result.check(bool(row), f"{name}: listed on the Documents page")
            if row:
                self.result.check(want_secrecy in row.lower(), f"{name}: UI shows secrecy {want_secrecy}")
                for code in want_domains:
                    label = type_names.get(code, code)
                    self.result.check(label in row, f"{name}: UI shows document type {label}")

            label = embedded_label(self.root / name)
            self.result.check(label is not None and label["secrecy"] == want_secrecy,
                              f"{name}: label in file {label} (want secrecy {want_secrecy})")
            if label:
                self.result.check(label["domains"] == want_domains,
                                  f"{name}: domains in file {label['domains']} (want {want_domains})")

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def cleanup(self):
        notes = []
        try:
            # Delete while the agent still watches the folder, so the inventory marks them removed.
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)
                time.sleep(5)
            if self.root.parent.exists() and not any(self.root.parent.iterdir()):
                self.root.parent.rmdir()
            version_with_uat = self.api.machine(self.settings.machine)["policy_version"]
            removed = self.api.delete_uat_objects(self.settings.prefix)
            notes += removed
            if removed:
                self.wait_for_agent_policy(version_with_uat, "the agent to go back to the real policy")
                notes.append("agent back on the real policy only")
        except Exception as error:  # noqa: BLE001
            self.result.error += f" | cleanup failed: {error}"
        self.result.check(True, "cleanup: " + (", ".join(notes) or "nothing to remove"))

        # No label may outlive the run: the copies are gone and the originals never got one.
        self.result.check(not self.root.exists(), f"cleanup: work folder {self.root} removed")
        labeled = [name for name in self.spec["expected"]
                   if (self.data / name).suffix in (".docx", ".xlsx") and embedded_label(self.data / name)]
        self.result.check(not labeled, f"cleanup: originals in data/ carry no label {labeled or ''}".rstrip())
