# dlp-regression

End-to-end regression tests for NextDLP, run like a UAT: a browser drives the
admin console to set up classification rules, the backend hands them to the real
Windows agent installed on the machine, the agent classifies the files in `data/`,
and each file's result is checked against what the case expects.

The developer laptop running the dev stack is the UAT environment for now.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The browser is the installed Microsoft Edge (`channel="msedge"`), so no
Playwright browser download is needed. The dev stack must be running
(`C:\dev\start-dev.ps1`) and the machine's agent service enrolled and online.

## Run

```powershell
.\.venv\Scripts\python.exe run.py --all                                  # every suite
.\.venv\Scripts\python.exe run.py --suite classification                 # one suite
.\.venv\Scripts\python.exe run.py --suite classification --case 02-multi-domain
.\.venv\Scripts\python.exe run.py --all --sequential                     # one case at a time
.\.venv\Scripts\python.exe run.py --all --headed                         # watch the browser
.\.venv\Scripts\python.exe run.py --all --keep                           # keep what the run created
.\.venv\Scripts\python.exe run.py --cleanup                              # remove leftovers
```

Each suite writes `reports/<suite>.log` — PASS/FAIL per case, then one cleanup
line — replacing the previous run's log; a failing suite also leaves
`reports/<suite>-failure.png`.

## Suites and cases

```
suites/<suite>/setup.yaml              document types + policies, set up once per run
suites/<suite>/cases/<case>/expected.yaml   expected secrecy + document types per file
data/<suite>/<case>/                   the case's documents (Git LFS)
```

Setting rules up and tearing them down is the slow part (console work plus the
agent's 20 s heartbeat), so a suite does it once and then checks all its cases
together. All rules of a suite are active at the same time, next to the
department's real policy, so every rule has its own trigger words and each case
only contains the triggers it is about. Things that need conflicting or changing
rules (policy merge, editing a rule) belong in a suite of their own.

## What a suite run does

The UAT lives in a fixed place: Lac Hong Software Co., Ltd. → Software Development
Department, where this machine is enrolled. Its real policy and data sources stay as
they are; a run only adds objects named `UAT …`.

1. As admin in the console: creates the suite's document types, one `UAT <suite>` data
   source for a fresh folder under `C:\dlp-uat\<run-id>\`, and the suite's active
   `UAT …` policies. Waits for the agent to pull them.
2. Copies every case's files into its own subfolder (all at once, or one case at a
   time with `--sequential`).
3. Reads the label the agent writes into each file right after classifying it and
   compares its secrecy level and document types with the case's `expected.yaml`.
   The result is logged as soon as it is known.
4. Cleans up even when a step failed: deletes the copied (labeled) files, removes the
   `UAT …` data source and policies, waits for the agent to go back to the real
   policy, purges the run's inventory rows (`DELETE /api/documents?path_prefix=…`)
   and deletes the suite's document types. It then checks that no labeled file,
   inventory row or document type is left and that the originals in `data/` are
   unlabeled.

## Layout

```
run.py                     entry point
harness/ui.py              admin console actions (Playwright)
harness/api.py             API client for waits and cleanup
harness/suite.py           setup, classify, verify, cleanup
harness/report.py          the per-suite log
tools/make_data.py         regenerates the synthetic documents
```

## Rules for test data

- Synthetic content only: no customer or real personal documents.
- No two files share content: the agent caches results by content, so a duplicate
  would be answered from the cache (the run warns about it).
- Files in `data/` must not carry a NextDLP label; the run refuses labeled inputs.
