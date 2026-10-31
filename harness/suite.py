"""A suite: the admin sets up its rules once in the console, the agent classifies
every case's files, each case is checked against the label the agent wrote into
its files, then everything the run created is removed."""
import hashlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from playwright.sync_api import Browser

from harness.api import Api
from harness.config import Settings
from harness.files import LABELED_SUFFIXES, embedded_label
from harness.health import AgentMonitor
from harness.smb import SmbShare, remove_leftovers as remove_smb_leftovers
from harness.steps import StepRunner, dropped_later, run_command, touched_names
from harness.ui import AdminConsole

# A check is ok (True), failed (False), or a failure the case marks as a known issue:
# KNOWN while it still fails, FIXED once it passes (time to drop the known_issue).
KNOWN, FIXED = "known", "fixed"


@dataclass
class CaseResult:
    name: str
    checks: list[tuple[bool | str, str]] = field(default_factory=list)
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
    metrics: list[str] = field(default_factory=list)

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


ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


class Case:
    def __init__(self, spec: Path, data: Path, root: Path, backend_result):
        self.name = spec.stem
        content = yaml.safe_load(spec.read_text(encoding="utf-8"))
        self.data = data / self.name
        self.root = root / self.name
        self.steps = content.get("steps") or []
        later = set(dropped_later(self.steps))
        if "expect_all" in content:
            # Every file in the case's data folder shares one expectation: a new sample is
            # just a new file there.
            self.expected = {f.name: dict(content["expect_all"]) for f in sorted(self.data.iterdir())
                             if f.is_file() and f.name not in later} if self.data.exists() else {}
        else:
            self.expected = content["expected"]
        self.later = sorted(later)
        self.backend_result = backend_result  # for files that cannot carry a label (images)
        self.result = CaseResult(self.name)
        self.dropped_at = 0.0
        self.seen_at: dict[str, float] = {}  # file -> monotonic time its result showed up
        self.observed: dict[str, dict] = {}

    def sources(self) -> list[Path]:
        return [self.data / name for name in [*self.expected, *self.later]]

    def drop_files(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.dropped_at = time.monotonic()
        for name in self.expected:
            shutil.copy2(self.data / name, self.root / name)

    def record(self, ok: bool, message: str, known_issue: str | None = None):
        if known_issue:
            ok, message = (FIXED, f"{message} — known issue no longer reproduces, remove it: {known_issue}") if ok \
                else (KNOWN, f"{message} — known issue: {known_issue}")
        self.result.checks.append((ok, message))

    def labels(self) -> dict | None:
        labels = {}
        for name, expected in self.expected.items():
            path = self.root / name
            # `result_from: backend` for a file the agent cannot label (encrypted, broken); an
            # unlabelled file carries no NextDLP_Secrecy, so only the backend tells it is done.
            from_backend = (expected.get("result_from") == "backend" or expected["secrecy"] == "unlabelled"
                            or path.suffix.lower() not in LABELED_SUFFIXES)
            try:
                labels[name] = self.backend_result(path) if from_backend else embedded_label(path)
                if not labels[name] and not from_backend:
                    # Expected a label but the agent found nothing: only the backend says it is done.
                    found = self.backend_result(path)
                    labels[name] = found if found and found["secrecy"] == "unlabelled" else None
            except Exception:  # noqa: BLE001 — the agent may be rewriting the file right now
                labels[name] = None
            if labels[name] and name not in self.seen_at:
                self.seen_at[name] = time.monotonic()
        return labels if all(labels.values()) else None

    def verify(self, labels: dict):
        self.observed = labels
        for name, expected in self.expected.items():
            want = (expected["secrecy"], sorted(expected.get("domains") or []))
            got = (labels[name]["secrecy"], labels[name]["domains"])
            message = f"{name}: {got[0]} {got[1]}"
            self.record(got == want, message if got == want else f"{message} (want {want[0]} {want[1]})",
                        expected.get("known_issue"))
            # How much of the file the label rests on, as the backend keeps it (full | partial | metadata_only).
            if "coverage" in expected:
                coverage = labels[name].get("coverage") or (self.backend_result(self.root / name) or {}).get("coverage")
                self.record(coverage == expected["coverage"],
                            f"{name}: coverage {coverage}" + ("" if coverage == expected["coverage"]
                                                              else f" (want {expected['coverage']})"),
                            expected.get("known_issue"))
            # Unlabelled is written into no file: a stale or unknown NextDLP_Secrecy is removed (TC-E-07/E-08).
            path = self.root / name
            if want[0] == "unlabelled" and path.suffix.lower() in LABELED_SUFFIXES and path.exists():
                try:
                    leftover = embedded_label(path)
                except Exception:  # noqa: BLE001 — encrypted or broken files cannot carry one
                    leftover = None
                if leftover:
                    self.record(False, f"{name}: still carries NextDLP_Secrecy={leftover['secrecy']}",
                                expected.get("known_issue"))
            # An embedded label also carries when it was written, as ISO-8601 UTC (TC-E-06).
            if "classified_at" in labels[name] and not ISO_UTC.fullmatch(labels[name]["classified_at"]):
                self.result.checks.append((False, f"{name}: NextDLP_ClassifiedAt "
                                                  f"{labels[name]['classified_at']!r} is not ISO-8601 UTC"))


class Suite:
    def __init__(self, folder: Path, settings: Settings, api: Api, run_id: str, only: list[str] | None = None):
        self.name = folder.name
        self.setup_spec = yaml.safe_load((folder / "setup.yaml").read_text(encoding="utf-8"))
        self.settings = settings
        self.api = api
        self.root = settings.work_root / run_id / self.name
        self.failure_shot = settings.reports_dir / f"{self.name}-failure.png"
        # Cases may sit in topic subfolders; a case name (file stem) is unique per suite.
        case_specs = sorted((folder / "cases").rglob("*.yaml"), key=lambda p: p.stem)
        stems = [p.stem for p in case_specs]
        if duplicates := sorted({stem for stem in stems if stems.count(stem) > 1}):
            raise ValueError(f"duplicate case names in {self.name}: {', '.join(duplicates)}")
        if only:
            unknown = set(only) - {p.stem for p in case_specs}
            if unknown:
                raise ValueError(f"unknown case(s) in {self.name}: {', '.join(sorted(unknown))}")
            case_specs = [p for p in case_specs if p.stem in only]
        self.only = only
        self.cases = [Case(p, settings.data_dir / self.name, self.root, self.backend_result) for p in case_specs]
        self.result = SuiteResult(self.name)
        self.policy_version_before = None
        self.backend_up = True
        # A network data source: the agent reaches the work folder through a share on this
        # machine. The share is the run folder, so the data source is a folder below its root.
        self.smb = (SmbShare(settings.machine, self.name, self.root.parent)
                    if self.setup_spec.get("data_source", {}).get("smb") else None)

    def agent_path(self, local: Path) -> str:
        """How the agent and the backend name a file the run dropped locally."""
        return self.smb.to_unc(local) if self.smb else str(local)

    # ── Run ──────────────────────────────────────────────────────────────────
    def run(self, browser: Browser, keep: bool, sequential: bool, report) -> SuiteResult:
        """Reports the classification result as soon as it is known, then cleans up."""
        started = time.monotonic()
        console = None
        self.failure_shot.unlink(missing_ok=True)
        monitor = AgentMonitor(self.settings.agent_log.parent,
                               {**self.settings.health_limits, **(self.setup_spec.get("health") or {})}).start()
        try:
            self.check_source_files()
            if not self.only:
                self.check_coverage()
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            console = AdminConsole(page, self.settings.base_url)
            console.login(self.settings.admin_user, self.settings.admin_password)
            self.setup(console)
            self.wait_for_agent_policy(self.policy_version_before, "the agent to pull the suite's policy")
            time.sleep(5)  # let the watcher on the new data source start
            self.classify(sequential)
            self.check_event_findings()
            self.run_steps()
        except Exception as error:  # noqa: BLE001 — every failure must still reach cleanup and the report
            self.result.error = f"{type(error).__name__}: {error}"
            if console:
                try:
                    console.screenshot(self.failure_shot)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if not self.backend_up:  # a failed step must not leave the stack without its server
                try:
                    self.set_backend("start")
                except Exception as error:  # noqa: BLE001
                    self.result.error += f"; backend not restarted: {error}"
        health = CaseResult("agent-health")
        for ok, message in monitor.stop():
            health.checks.append((ok, message))
        self.result.cases = [case.result for case in self.cases] + [health]
        self.result.metrics = self.metrics()
        self.result.seconds = time.monotonic() - started
        report(self.result)
        if not keep:
            self.cleanup()
        return self.result

    def check_coverage(self):
        """Every document type and secrecy level the setup can assign shows up in some case,
        so a rule no case exercises cannot slip in. Run on the whole suite only."""
        expectations = [e for case in self.cases for e in case.expected.values()]
        expectations += [s["expect"] for case in self.cases for s in case.steps if "expect" in s]
        expected_types = {t for e in expectations for t in e.get("domains") or []}
        expected_secrecy = {e["secrecy"] for e in expectations}
        negatives = set(self.setup_spec.get("negative_document_types", []))
        types = {t["code"] for t in self.setup_spec.get("document_types", [])} - negatives
        secrecy = {c["secrecy"] for p in self.setup_spec["policies"] for c in p["conditions"] if c.get("secrecy")}
        secrecy -= set(self.setup_spec.get("negative_secrecy", []))
        if missing := sorted((types - expected_types) | (secrecy - expected_secrecy)):
            raise ValueError(f"{self.name}: no case expects {', '.join(missing)}; add a case or list it as negative")

    def check_source_files(self):
        # Files too large to commit are written on first use.
        if any(not source.exists() for case in self.cases for source in case.sources()):
            subprocess.run([sys.executable, "make_data.py"], cwd=self.settings.data_dir.parent / "tools", check=True)
        seen = {}
        for case in self.cases:
            if not case.expected:
                raise ValueError(f"case {case.name} has no files (expected, or expect_all over {case.data})")
            for source in case.sources():
                if not source.exists():
                    raise FileNotFoundError(f"missing test file {source}")
                starts_labelled = case.expected.get(source.name, {}).get("starts_labelled", False)
                if source.suffix.lower() in LABELED_SUFFIXES and not starts_labelled and embedded_label(source):
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
        for label in self.setup_spec.get("secrecy_labels", []):
            existing = self.api.secrecy_label(label["code"])
            if existing and not existing["active"]:
                raise RuntimeError(f"secrecy label {label['code']} exists but is not in use; delete it first")
            if not existing:
                console.create_secrecy_label(label["code"], label["name"], label["more_secret_than"])

        self.root.mkdir(parents=True, exist_ok=True)
        smb = None
        if self.smb:
            self.smb.create()
            smb = {"scanner": settings.machine, "username": self.smb.username, "password": self.smb.password}
        console.create_data_source(f"{settings.prefix}{self.name}", self.agent_path(self.root),
                                   settings.company_name, settings.department_name,
                                   self.setup_spec.get("data_source", {}).get("extra_extensions", []), smb)

        secrecy_names = {s["code"]: s["display_name"] for s in self.api.items("/api/taxonomy/secrecy-labels")}
        type_names = {t["code"]: t["name"] for t in self.setup_spec.get("document_types", [])}
        for policy in self.setup_spec["policies"]:
            if not policy["name"].startswith(settings.prefix):
                raise ValueError(f"policy name must start with {settings.prefix!r}: {policy['name']}")
            console.create_policy(policy["name"], settings.company_name, settings.department_name,
                                  policy["conditions"], secrecy_names, type_names,
                                  policy.get("classification_methods"))

    def wait_for_agent_policy(self, version_before, what: str):
        def pulled():
            machine = self.api.machine(self.settings.machine)
            if machine["policy_dirty"]:
                return False
            if machine["policy_version"] != version_before:
                return True
            # With no active policy left (a fresh install has no real one), the agent
            # runs an empty policy and the backend keeps the machine's last version.
            return not any(p.get("is_active") for p in self.api.department_policies(self.settings.department_code))
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

    def check_event_findings(self):
        """Every classification event the backend got for a case file explains itself: the
        agent sends the findings of the scan that raised it, even after a rescan or a new
        policy. A cache hit reuses an earlier result and has none of its own."""
        for case in self.cases:
            if case.result.error:
                continue
            for name in case.expected:
                path = self.agent_path(case.root / name)

                def arrived():
                    return self.api.items("/api/agent-events", event_type="file_classified", file_path=path, limit=50) or None
                try:
                    events = wait_until(arrived, self.settings.classify_timeout_s, what=f"the classification event of {name}")
                except TimeoutError as error:
                    case.result.checks.append((False, f"{name}: {error}"))
                    continue
                events = [e for e in events if e.get("action") != "cache_hit"]
                if missing := [e["id"] for e in events if not e.get("findings")]:
                    case.result.checks.append((False, f"{name}: event(s) {missing} carry no findings"))
                else:
                    case.result.checks.append((True, f"{name}: {len(events)} event(s) with findings"))

    def backend_result(self, path: Path) -> dict | None:
        """What the backend recorded for one file (the agent reports within ~30 s)."""
        for document in self.run_documents():
            if document["file_path"].lower() == self.agent_path(path).lower() and document["status"] != "removed":
                domains = [d for d in document.get("domain_label") or [] if d != "general"]
                return {"secrecy": document["secrecy_label"], "domains": sorted(domains),
                        "coverage": document.get("coverage", "full")}
        return None

    def backend_document(self, path: Path) -> dict | None:
        """The backend's live inventory row for one file, as it is."""
        target = self.agent_path(path).lower()
        return next((d for d in self.run_documents()
                     if d["file_path"].lower() == target and d["status"] != "removed"), None)

    # ── After classification ─────────────────────────────────────────────────
    def run_steps(self):
        """What users do to labelled files (rename, copy, edit, …), one case at a time."""
        for case in self.cases:
            if case.steps and not case.result.error:
                StepRunner(case, self).run()

    def set_backend(self, state: str):
        command = {"stop": self.settings.backend_stop, "start": self.settings.backend_start}[state]
        if not command:
            raise RuntimeError(f"set UAT_BACKEND_{state.upper()} to the command that {state}s the backend")
        run_command(command)
        if state == "start":
            def answers():
                try:
                    return self.api.items("/api/machines") is not None
                except Exception:  # noqa: BLE001 — still starting
                    return False
            wait_until(answers, self.settings.policy_timeout_s, what="the backend to answer again")
        self.backend_up = state == "start"

    def metrics(self) -> list[str]:
        """Time from dropping a file to its result, for every suite; with `metrics:
        {positive_secrecy: …}` in setup.yaml also how well that level is detected."""
        rows = [(case, name, expected) for case in self.cases for name, expected in case.expected.items()
                if name in case.seen_at]
        if not rows:
            return []
        times = sorted((case.seen_at[name] - case.dropped_at, name) for case, name, _ in rows)
        lines = [f"{len(rows)} file(s): result after {times[len(times) // 2][0]:.0f}s median, "
                 f"{times[-1][0]:.0f}s max ({times[-1][1]})"]
        positive = (self.setup_spec.get("metrics") or {}).get("positive_secrecy")
        if positive:
            counts = {"hit": [], "missed": [], "false alarm": [], "rejected": []}
            for case, name, expected in rows:
                got = case.observed.get(name, {}).get("secrecy")
                want_positive, got_positive = expected["secrecy"] == positive, got == positive
                key = ("hit" if got_positive else "missed") if want_positive else \
                      ("false alarm" if got_positive else "rejected")
                counts[key].append(name)
            positives = len(counts["hit"]) + len(counts["missed"])
            negatives = len(counts["false alarm"]) + len(counts["rejected"])
            lines.append(f"{positive}: detected {len(counts['hit'])}/{positives}, "
                         f"false alarms {len(counts['false alarm'])}/{negatives}")
            lines += [f"{key}: {', '.join(sorted(names))}" for key in ("missed", "false alarm")
                      if (names := counts[key])]
        return lines

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def run_documents(self) -> list[dict]:
        root = self.agent_path(self.root).lower()
        return [d for d in self.api.documents(self.settings.department_code)
                if d["file_path"].lower().startswith(root)]

    def cleanup(self):
        notes, problems = [], []
        try:
            dropped = {name for case in self.cases if case.root.exists()
                       for name in [*case.expected, *touched_names(case.steps)]}
            # Delete while the agent still watches the folder, so it reports the deletions.
            # A shared folder stays until the agent has dropped the data source.
            if self.root.exists():
                for child in (self.root.iterdir() if self.smb else [self.root]):
                    shutil.rmtree(child, ignore_errors=True)
            if not self.smb:
                self.remove_work_folder()
            # Purging before the agent has reported the files (and their deletion) would
            # let a late report recreate rows, so wait until every file shows as removed.
            if dropped:
                wait_until(lambda: {Path(d["file_path"]).name for d in self.run_documents()
                                    if d["status"] == "removed"} >= dropped,
                           self.settings.classify_timeout_s, what="the agent to report the deleted files")
            version_with_suite = self.api.machine(self.settings.machine)["policy_version"]
            removed = self.api.delete_uat_objects(self.settings.prefix)
            notes += removed
            # Only a removed policy changes what the agent runs; a setup that failed
            # before creating one has nothing for the agent to drop.
            if any(item.startswith("policy ") for item in removed):
                self.wait_for_agent_policy(version_with_suite, "the agent to go back to the real policy")
                notes.append("agent back on the real policy only")
            if self.smb:
                notes += remove_smb_leftovers()
                self.remove_work_folder()
            notes.append(f"{self.api.purge_documents(self.agent_path(self.root))} inventory row(s) purged")
            for doc_type in self.setup_spec.get("document_types", []):
                if self.api.document_type(doc_type["code"]):
                    self.api.delete_document_type(doc_type["code"])
                    notes.append(f"document type {doc_type['code']}")
            # Deletable only now: the purge took the documents and label history that used it.
            if labels := [l["code"] for l in self.setup_spec.get("secrecy_labels", []) if self.api.secrecy_label(l["code"])]:
                version_with_labels = self.api.machine(self.settings.machine)["policy_version"]
                for code in labels:
                    self.api.delete_secrecy_label(code)
                    notes.append(f"secrecy label {code}")
                self.wait_for_agent_policy(version_with_labels, "the agent to drop the suite's secrecy labels")
        except Exception as error:  # noqa: BLE001
            problems.append(str(error))

        # Nothing of the run may outlive it: no labeled file, no inventory row, no document type or secrecy label.
        if self.root.exists():
            problems.append(f"work folder {self.root} still exists")
        if rows := [d["file_path"] for d in self.run_documents()]:
            problems.append(f"inventory rows left: {rows}")
        if types := [t["code"] for t in self.setup_spec.get("document_types", []) if self.api.document_type(t["code"])]:
            problems.append(f"document types left: {types}")
        if labels := [l["code"] for l in self.setup_spec.get("secrecy_labels", []) if self.api.secrecy_label(l["code"])]:
            problems.append(f"secrecy labels left: {labels}")
        if labeled := [str(s) for case in self.cases for s in case.sources()
                       if s.suffix.lower() in LABELED_SUFFIXES and embedded_label(s)
                       and not case.expected.get(s.name, {}).get("starts_labelled", False)]:
            problems.append(f"originals in data/ got a label: {labeled}")
        self.result.cleanup_ok = not problems
        self.result.cleanup = "; ".join(problems) if problems else ", ".join(notes)

    def remove_work_folder(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()
