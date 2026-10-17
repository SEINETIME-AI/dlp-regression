# dlp-regression

End-to-end regression tests for NextDLP, run like a UAT: a browser drives the
admin console, the backend hands the policy to the real Windows agent installed
on the machine, the agent scans the files in `data/`, and the results are checked
in the console, through the API and inside the files themselves.

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
.\.venv\Scripts\python.exe run.py --all                          # every scenario
.\.venv\Scripts\python.exe run.py --scenario 01-keyword-secrecy  # one scenario
.\.venv\Scripts\python.exe run.py --all --headed                 # watch the browser
.\.venv\Scripts\python.exe run.py --all --keep                   # keep what the run created
.\.venv\Scripts\python.exe run.py --cleanup                      # remove leftovers
```

Each scenario writes `reports/<scenario>.log` — PASS/FAIL per file, then one cleanup
line — replacing the previous run's log; a failing scenario also leaves
`reports/<scenario>-failure.png`.

## What a scenario does

The UAT lives in a fixed place: Lac Hong Software Co., Ltd. → Software Development
Department, where this machine is enrolled. Its real policy and data sources stay as
they are; the agent classifies with the merged policy (real + UAT), so scenario
documents avoid the real policy's triggers. A run only adds objects named `UAT …`.

1. As admin in the console: creates the scenario's document types, a `UAT …` data source
   for a fresh folder under `C:\dlp-uat\<run-id>\`, and an active `UAT …`
   classification policy with the scenario's conditions.
2. Waits for the agent to pull the merged policy.
3. Copies the scenario's files from `data/` into the data source folder.
4. Reads the label the agent writes into each file right after classifying it and
   compares its secrecy level and document types with `expected` — this is the
   result, logged as soon as it is known.
5. Cleans up even when a step failed: deletes the copied (labeled) files, removes the
   `UAT …` data source and policy, waits for the agent to go back to the real policy,
   purges the run's inventory rows (`DELETE /api/documents?path_prefix=…`, system
   admin only) and deletes the scenario's document types. It then checks that no
   labeled file, inventory row or document type is left and that the originals in
   `data/` are unlabeled.

## Layout

```
run.py                     entry point
harness/ui.py              admin console actions (Playwright)
harness/api.py             API client for waits, checks and cleanup
harness/scenario.py        setup, wait, verify, cleanup
scenarios/<name>/          scenario.yaml: document types, policy, expected labels
data/<name>/               the scenario's documents (Git LFS)
tools/make_data.py         regenerates the synthetic documents
reports/                   run output (not committed)
```

## Rules for test data

- Synthetic content only: no customer or real personal documents.
- Files in `data/` must not carry a NextDLP label; the run refuses labeled inputs.
