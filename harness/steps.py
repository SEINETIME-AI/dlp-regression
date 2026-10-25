"""What a user does to a file after the agent has labelled it, and what the label
must look like afterwards. A case lists them under `steps:`; each step is one action
and an optional `expect` on the file it leaves behind:

  - rename: {from: a.docx, to: b.docx}
  - move:   {from: b.docx, to: archive/b.docx}
  - copy:   {from: b.docx, to: b-copy.docx}
  - edit:   {file: b.docx, append: "text"}        # or replace: ["paragraph", ...]
  - drop:   c.docx                                # a file from the case's data folder, added now
  - backend: stop | start                         # the agent loses / regains its server
  - check:  {file: c.docx}                        # only wait for and verify `expect`

Step keys next to the action:
  expect:          {secrecy, domains}  the result on the target file
  check_in:        [file, backend]     where to look (default both; only file while the backend is down)
  same_document_as: a.docx            the backend keeps that original file's doc_id (history follows the file)
  known_issue:     "…"                 a failure is reported as known instead of failing the case

A new action is one function in ACTIONS."""
import datetime
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from harness.files import LABELED_SUFFIXES, embedded_label

_BODY_END = "</w:body>"


def edit_docx(path: Path, append: str | None = None, replace: list[str] | None = None):
    """Rewrites word/document.xml like a save in Word: every other part, including the
    NextDLP custom properties, is kept as it was."""
    with zipfile.ZipFile(path) as archive:
        parts = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    document = parts["word/document.xml"].decode("utf-8")
    paragraph = lambda text: f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    if replace is not None:
        document = re.sub(r"<w:body>.*</w:body>", "<w:body>" + "".join(map(paragraph, replace)) + _BODY_END,
                          document, flags=re.S)
    if append:
        document = document.replace(_BODY_END, paragraph(append) + _BODY_END)
    parts["word/document.xml"] = document.encode("utf-8")
    temp = path.with_name(path.name + ".edit")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    temp.replace(path)


def _rename(ctx, spec):
    ctx.root.joinpath(spec["to"]).parent.mkdir(parents=True, exist_ok=True)
    ctx.retry(lambda: ctx.root.joinpath(spec["from"]).rename(ctx.root / spec["to"]))
    return spec["to"]


def _copy(ctx, spec):
    ctx.root.joinpath(spec["to"]).parent.mkdir(parents=True, exist_ok=True)
    ctx.retry(lambda: shutil.copy2(ctx.root / spec["from"], ctx.root / spec["to"]))
    return spec["to"]


def _edit(ctx, spec):
    path = ctx.root / spec["file"]
    if path.suffix.lower() != ".docx":
        raise ValueError(f"edit supports .docx only: {spec['file']}")
    ctx.retry(lambda: edit_docx(path, spec.get("append"), spec.get("replace")))
    return spec["file"]


def _drop(ctx, name):
    shutil.copy2(ctx.data / name, ctx.root / name)
    return name


def _backend(ctx, state):
    ctx.set_backend(state)
    return None


def _check(ctx, spec):
    return spec["file"]


# name -> function(ctx, spec) returning the file the step leaves behind (None: no file)
ACTIONS = {"rename": _rename, "move": _rename, "copy": _copy, "edit": _edit,
           "drop": _drop, "backend": _backend, "check": _check}


def action_of(step: dict) -> str:
    names = [key for key in step if key in ACTIONS]
    if len(names) != 1:
        raise ValueError(f"a step needs exactly one action of {sorted(ACTIONS)}: {step}")
    return names[0]


def dropped_later(steps: list[dict]) -> list[str]:
    return [step["drop"] for step in steps if action_of(step) == "drop"]


def touched_names(steps: list[dict]) -> set[str]:
    """Every file name a case's steps create, for cleanup."""
    names = set()
    for step in steps:
        spec = step[action_of(step)]
        if isinstance(spec, dict):
            names |= {Path(spec[key]).name for key in ("to", "file") if key in spec}
        elif action_of(step) == "drop":
            names.add(Path(spec).name)
    return names


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StepRunner:
    """Runs one case's steps against its folder; `suite` gives the backend lookups."""

    def __init__(self, case, suite):
        self.case = case
        self.suite = suite
        self.root = case.root
        self.data = case.data
        self.timeout_s = suite.settings.classify_timeout_s

    def retry(self, action, attempts: int = 10):
        # The agent may still hold the file open while it writes its label.
        for attempt in range(attempts):
            try:
                return action()
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(1)

    def set_backend(self, state: str):
        self.suite.set_backend(state)

    def run(self):
        # The doc_id each file had before any step, for `same_document_as`.
        originals = {name: (self.suite.backend_document(self.root / name) or {}).get("doc_id")
                     for name in self.case.expected}
        for number, step in enumerate(self.case.steps, start=1):
            action = action_of(step)
            started = _utc_now()
            target = ACTIONS[action](self, step[action])
            label = f"step {number} {action} {target or step[action]}"
            if "expect" not in step:
                self.case.result.checks.append((True, label))
                continue
            ok = self.verify(label, step, self.root / target, started, action == "edit")
            if ok and (original := step.get("same_document_as")):
                document = self.suite.backend_document(self.root / target) or {}
                self.case.record(document.get("doc_id") == originals.get(original),
                                 f"{label}: doc_id {document.get('doc_id')} (as {original}: {originals.get(original)})",
                                 step.get("known_issue"))

    def verify(self, label: str, step: dict, path: Path, started: str, fresh: bool) -> bool:
        want = (step["expect"]["secrecy"], sorted(step["expect"].get("domains") or []))
        places = step.get("check_in") or (["file"] if not self.suite.backend_up else ["file", "backend"])
        last = {}

        def observed():
            last.clear()
            if "file" in places and path.suffix.lower() in LABELED_SUFFIXES:
                try:
                    found = embedded_label(path)
                except Exception:  # noqa: BLE001 — the agent may be rewriting the file right now
                    return False
                got = (found["secrecy"], found["domains"]) if found else ("unlabelled", [])
                last["file"] = got
                # After an edit the old label is still in the file until the agent has rewritten it.
                if fresh and "backend" not in places and found and found["classified_at"] < started:
                    return False
                if got != want:
                    return False
            if "backend" in places:
                document = self.suite.backend_document(path)
                if not document:
                    last["backend"] = "no row"
                    return False
                got = (document["secrecy_label"], sorted(d for d in document.get("domain_label") or [] if d != "general"))
                last["backend"] = got
                if fresh and document["updated_at"] < started:
                    return False
                if got != want:
                    return False
            return True

        deadline = time.monotonic() + self.timeout_s
        ok = False
        while time.monotonic() < deadline:
            try:
                if observed():
                    ok = True
                    break
            except Exception:  # noqa: BLE001 — backend restarting; retry until the deadline
                pass
            time.sleep(2)
        detail = ", ".join(f"{place} {value}" for place, value in last.items())
        message = f"{label}: {detail or 'nothing observed'}" + ("" if ok else f" (want {want[0]} {want[1]})")
        # The step's known issue is about the document identity when it checks that.
        self.case.record(ok, message, None if step.get("same_document_as") else step.get("known_issue"))
        return ok


def run_command(command: str):
    subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
