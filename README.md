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
git lfs pull   # the documents in data/ are stored with Git LFS
```

The browser is the installed Microsoft Edge (`channel="msedge"`), so no
Playwright browser download is needed. The dev stack must be running
(`C:\dev\start-dev.ps1`) and the machine's agent service enrolled and online.

Settings come from environment variables, with defaults for the dev stack:
`UAT_BASE_URL` (`http://localhost:5173`), `UAT_ADMIN_USER` / `UAT_ADMIN_PASS`
(`admin` / `admin`), `UAT_MACHINE` (this computer's name) and `UAT_WORK_ROOT`
(`C:\dlp-uat`). The company and department are fixed in `harness/config.py`.

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

`--case` takes the case name (file name without `.yaml`), wherever the case sits
under `cases/`. Each suite writes `reports/<suite>.log` — PASS/FAIL per case, then
one cleanup line — replacing the previous run's log; a failing suite also leaves
`reports/<suite>-failure.png`.

To test an agent build that is not installed yet, stop the `nextdlp` service,
back up `C:\Program Files\DlpExtension\nextdlp.exe`, copy the new build over it,
start the service, run the suites, then put the backup back (all as administrator).

## Suites

| Suite | What it covers | Customer tests |
|---|---|---|
| `classification` | Every technique on, as in a real policy: keyword / regex secrecy and document types, the file formats (PDF text and scanned, PNG, DOCX with an embedded image, PPTX, password-protected and corrupt DOCX), weighted dictionary thresholds, hybrid rules (keyword AND data pattern, wordlist). Cases are grouped in `cases/keyword`, `cases/format`, `cases/dictionary` and `cases/hybrid`. | TC-C-05, TC-E-03, TC-F-04, TC-F-05 |
| `mode` | The classification techniques a policy runs: two policies with different techniques, each with keyword, dictionary and semantic conditions that all match the same file. Only the selected techniques may assign a type; a switched-off keyword secrecy rule must not run. | TC-D-01..03 |

Setting rules up and tearing them down is the slow part (console work plus the
agent's 20 s heartbeat), so a suite does it once and then checks all its cases
together. All rules of a suite are active at the same time, next to the
department's real policy, so every rule has its own trigger words and each case
only contains the triggers it is about. A case checks its exact result, so a rule
that also fires on another case's file fails that case instead of hiding.
Anything that needs a different policy setup (a policy-level setting, rules that
are meant to collide) belongs in a suite of its own, like `mode`.

## Writing a suite

```
suites/<suite>/setup.yaml                  document types + policies, set up once per run
suites/<suite>/cases/[<topic>/]<case>.yaml expected result per file
data/<suite>/<case>/                       the case's documents (Git LFS)
```

`setup.yaml`:

```yaml
document_types:                  # created for the run, deleted after it
  - {code: uat-finance, name: UAT Tài chính}
negative_document_types: []      # types a correct agent never assigns (see coverage check)
negative_secrecy: []             # same for secrecy levels
data_source:
  extra_extensions: [.png, .pptx]  # the console form starts with .pdf/.docx/.xlsx
policies:                        # names must start with "UAT "
  - name: UAT example
    classification_methods: [keyword, dictionary]   # optional; default every technique
    conditions:
      - name: UAT phiếu chi
        secrecy: confidential            # and/or document_type: uat-finance
        keyword: phiếu chi
      - name: UAT từ điển
        document_type: uat-finance
        dictionary: {terms: {phiếu xuất kho: 0.4, biên nhận: 0.4}, threshold: 0.8}  # threshold optional
      - name: UAT ngữ nghĩa
        document_type: uat-finance
        embedding: {descriptions: ["…"], threshold: 0.6}
      - name: UAT hybrid
        document_type: uat-finance
        hybrid:
          combinator: all                # or any
          conditions: [{keyword: mã dự án}, {data_pattern: 'DA-[A-Z]{4}'}, {wordlist: [a, b]}]
```

A case:

```yaml
title: What the case shows
expected:
  file-name.docx:
    secrecy: confidential
    domains: [uat-finance]
    result_from: backend   # optional: for a file the agent cannot label (encrypted, broken)
```

The result is read from the label the agent writes into the file (OOXML custom
properties, PDF Info dictionary); for images and files with `result_from: backend`
it is read from the backend's inventory instead.

On a full-suite run the harness checks coverage: every document type and secrecy
level the setup can assign must be expected by some case, unless it is listed as
negative. A rule no case exercises therefore cannot slip in unnoticed.

## What a suite run does

The UAT lives in a fixed place: Lac Hong Software Co., Ltd. → Software Development
Department, where this machine is enrolled. Its real policy and data sources stay as
they are; a run only adds objects named `UAT …`.

1. As admin in the console: creates the suite's document types, one `UAT <suite>` data
   source for a fresh folder under `C:\dlp-uat\<run-id>\`, and the suite's active
   `UAT …` policies. Waits for the agent to pull them.
2. Copies every case's files into its own subfolder (all at once, or one case at a
   time with `--sequential`).
3. Reads each file's result as soon as the agent has classified it and compares its
   secrecy level and document types with the case's `expected`. The result is
   logged as soon as it is known.
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
harness/suite.py           setup, classify, verify, coverage check, cleanup
harness/files.py           reads the label embedded in OOXML and PDF files
harness/report.py          the per-suite log
harness/config.py          settings (environment variables)
tools/make_data.py         regenerates the synthetic documents (--force to rewrite all)
tools/make_docx.py         minimal .docx, optionally with embedded images
tools/make_pptx.py         .pptx with slides, a table and speaker notes (python-pptx)
tools/make_media.py        PNG images and text / scanned PDFs
tools/make_broken.py       password-protected and corrupt .docx
```

## Rules for test data

- Synthetic content only: no customer or real personal documents.
- No two files share content: the agent caches results by content, so a duplicate
  would be answered from the cache (the run warns about it).
- Files in `data/` must not carry a NextDLP label; the run refuses labeled inputs.
- Trigger words avoid the department's real policy (hóa đơn, thuế, phí, số tiền,
  "quý 1"/"quý 2", long digit runs) and every other rule of the suite.
