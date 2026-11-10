---
name: dlp-uat
description: Use when a NextDLP change needs UAT or regression runs against a live agent (the dlp-flow stage "UAT + dashboard"), or when the user asks to run dlp-regression suites
---

# dlp-uat

## Overview

Run the dlp-regression suites the change touches, against the agent build under test, with the
monitor dashboard on; compare with the suite's baseline; leave a report the next gate can show.
This skill never commits, pushes, re-enrolls an agent, stops a stack or touches the real policy.

## 1. Preconditions — check, then state them in one line

- **Stack.** UAT runs on the dev stack (`C:\dev\start-dev.ps1`, Console `http://localhost:5173`),
  the environment the harness is written for (user decision 2026-11-10; seven runs on the HTTPS test
  stack failed on harness and timing issues, not on the change). `start-dev.ps1` docker-stops whatever
  holds 9090/9091 (e.g. the test stack), so say so and get the user's ok before starting it.
- **Enrollment.** The agent service runs and is enrolled on that same stack, in the company and
  department fixed in `harness/config.py`. If not, halt and say what is wrong; do not re-enroll.
- **Model.** The suite's semantic thresholds are tuned for MiniLM, the model the MSI writes. An agent
  started with `run-console` and the repo's `agent/configs/agent.conf` runs Granite and fires the
  semantic rules on every file: give it a copy of that config with `model: minilm` (`DLP_CONFIG_PATH`).
- **Real sources.** UAT policies apply to the whole unit, so the unit's real sources are rescanned and
  can be labelled by UAT rules: move them out of the agent's reach for the run (e.g. rename the folder).
- **Build.** The installed `C:\Program Files\DlpExtension\nextdlp.exe` is the build under test
  (timestamp against the build). Swapping it: README "Run", as administrator, backup kept.
- **Shell environment.** `UAT_BASE_URL` = that stack's Console URL; `UAT_ADMIN_USER`;
  `PYTHONIOENCODING=utf-8`. `.venv` exists and `git lfs pull` was run.
- **Password.** On the dev stack the defaults work and Claude runs the suite. On any other stack
  Claude still runs it, in a visible window: a script in the scratchpad sets the other variables, reads
  the password with `Read-Host -AsSecureString` into `UAT_ADMIN_PASS` for that window only, then runs
  the suite; Claude opens it with `Start-Process powershell -NoExit -File <script>` and the user only
  types the password there. Save that script as UTF-8 **with a BOM** (Windows PowerShell 5.1 reads a
  BOM-less file as ANSI, so "Công ty Test" became "CÃ´ng ty Test" on 2026-11-10). Afterwards Claude
  reads `reports/<suite>.log`. The password is never
  written in a file, script, report, command line or message.
- **HTTPS stack with its own CA** (`C:\nextdlp-test`): also `UAT_IGNORE_HTTPS_ERRORS=1` and
  `SSL_CERT_FILE=C:\nextdlp-test\certs\ca.crt`. The harness reads them only once the pending change to
  `run.py` / `harness/console.py` is committed; check `git status` and halt if it is not.
- **Suite needs:** `offline` needs `UAT_BACKEND_STOP` and `UAT_BACKEND_START`; `smb` needs an elevated
  prompt and `SOURCE_SECRETS_KEY` on the backend. Missing: halt, do not route around.
- A run adds only `UAT …` objects and removes them at the end (`run.py --cleanup` after an abort), so
  running on a shared stack leaves nothing behind.

## 2. Suites

| The change touches | Suites |
|---|---|
| file reading, extractors, classification | `classification` (+ `lifecycle` when labels are written) |
| policy techniques | `mode` |
| stamps | `stamp`, `stamp-issuer` |
| offline / sync | `offline` |
| Console / backend admin only | `console` |
| network sources | `smb` |

`--all` when `dlp-flow` asks or the change is broad. Name the suites in the report.

## 3. Run with the dashboard

```powershell
# window 1 (stays open): the dashboard on http://localhost:8765
cd C:\dev\dlp-win\agent\scripts\monitor
powershell -File .\monitor.ps1 -Attach -NoBrowser -Label "<suite>-<commit>"
# window 2
cd C:\dev\dlp-regression
.\.venv\Scripts\python.exe run.py --suite <suite>
```

Claude opens the monitor in a visible window too (`Start-Process powershell -NoExit -File .\monitor.ps1
-Attach -NoBrowser -Label <suite>-<commit>`, working directory `scripts\monitor`); the user stops it there with Ctrl+C after the suite: that
writes `C:\perf\run-<date>-<time>.html`. Stopping its process from a tool skips the snapshot, so the
stage would have no dashboard evidence; if nobody can press Ctrl+C, halt and say so. The
harness adds its own `agent-health` case (CPU, memory peak, restarts, log size) to every report.
A `console`-only run does not start the monitor.

## 4. Baseline

`C:\perf\uat\<suite>\baseline.json`: commit, date, peak RAM MB and mean CPU of `nextdlp.exe` (dashboard),
median seconds from drop to result (`reports/<suite>.log`). The first run of a suite writes it and
says so. Any value more than 20 % worse halts the stage with both numbers. The baseline moves only
when the user accepts the new level.

## 5. Report and halts

`<work folder>\reports\uat-<suite>.md`: stack, build, suites, PASS/FAIL per case (from
`reports/<suite>.log`), agent-health, dashboard numbers against the baseline, snapshot path, failure
screenshots.

- A failed case: the stage is `failed` with a `Halt:` line. A re-run of one case is reported as a
  second run next to the first, never as a pass.
- New cases are their own change in this repo (commit to `main` at G3), not part of a UAT run.

## Red flags

| Thought | Reality |
|---|---|
| "I'll point the suite at whatever stack is up" | Ask which stack; the agent must be enrolled there. |
| "I'll put the password in a .env so it runs unattended" | The password is typed by the user, never stored. |
| "Monitor is optional, the harness checks memory" | The dashboard snapshot and baseline are the stage's evidence. |
| "Case 48 passed when I re-ran it" | Both runs go in the report; the stage is not done. |
| "I'll fix the failing case's YAML quickly" | Halt and report; a case change is a separate change. |
