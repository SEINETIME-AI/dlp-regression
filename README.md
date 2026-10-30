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
The `offline` suite also needs `UAT_BACKEND_STOP` and `UAT_BACKEND_START`, the
commands that stop and start the backend, for the release stack:

```powershell
$env:UAT_BACKEND_STOP  = "docker stop nextdlp-release-dlp-server-1"
$env:UAT_BACKEND_START = "docker start nextdlp-release-dlp-server-1"
```

`UAT_MAX_AGENT_RSS_MB` (3072) and `UAT_MAX_AGENT_LOG_MB` (64) are the agent health
limits every suite checks (see below).

## Run

```powershell
.\.venv\Scripts\python.exe run.py --all                                  # every suite
.\.venv\Scripts\python.exe run.py --suite classification                 # one suite
.\.venv\Scripts\python.exe run.py --suite classification --case 02-multi-domain
.\.venv\Scripts\python.exe run.py --all --sequential                     # one case at a time
.\.venv\Scripts\python.exe run.py --all --headed                         # watch the browser
.\.venv\Scripts\python.exe run.py --all --keep                           # keep what the run created
.\.venv\Scripts\python.exe run.py --cleanup                              # remove leftovers (also the smb suite's share and account)
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
| `classification` | Every technique on, as in a real policy (39 cases): keyword secrecy and document types, AND of two keywords, `min_count`, regex; semantic rules with their own thresholds; weighted dictionary (thresholds, repeated and distant terms, no synonyms); hybrid rules; the file formats (PDF text and scanned, PNG, DOCX with an embedded image, PPTX, XLSX, password-protected and corrupt DOCX, both unlabelled); a red stamp on a scanned page; 12 MB and 22 MB text files with the trigger past the first 10 MB, and 22 MB text and PDF files whose dictionary or hybrid rule needs a signal from each half; a file that starts with an unknown secrecy code; a secrecy label the admin creates above secret. Grouped in `cases/keyword`, `cases/semantic`, `cases/dictionary`, `cases/hybrid` and `cases/format`. | TC-A-01..06, TC-B-01..04, TC-C-01..06, TC-E-01..08, TC-F-04/05, TC-H-01..05 |
| `mode` | The classification techniques a policy runs: two policies with different techniques, each with keyword, dictionary and semantic conditions that all match the same file. Only the selected techniques may assign a type; a switched-off keyword secrecy rule must not run. | TC-D-01..03 |
| `stamp` | Stamp detection over a sample set: round stamps in many sizes, angles, inks and formats (scanned PDF, PNG, Word), a rectangular stamp, pages without a stamp and red decoys. Reports how many stamped pages were detected and how many stamp-free pages were flagged. A sample is one more line in `STAMP_SAMPLES` (`tools/make_data.py`). | TC-H-01 |
| `lifecycle` | What users do after a file is labelled: rename, move into a subfolder (Word and PDF), copy, and edits that raise the label, remove its trigger or change nothing. After every step the label in the file and the backend's row are checked; a rename must also keep the document's identity. | — |
| `offline` | The backend is stopped, a labelled file is edited and a new one is added, then the backend is started: both files are labelled while it is down, and their results reach the backend after it is back. | — |
| `console` | Admin actions checked in the console alone, without the agent. A child document type whose code contains `/` (`uat-cha/con`) is created, renamed, deactivated and deleted through the UI; every request must succeed (bug 69: the console sends `%2F` and the backend answered 404). A secrecy label is deactivated, renamed while off (it must stay off) and switched back on with "Sử dụng lại" (bug 70). Deactivate and delete must ask first (bug 72). A temporary manager of an empty `uat-phong-tam` unit logs in and must see no documents, endpoints or dashboard figures of `software-dev`, and gets 404 for its document and endpoint by id; with `software-dev` added to the scope they come back (bug 73). A document type and a secrecy label used by an inactive `UAT Policy giữ nhãn` cannot be switched off (409, the console names the policy); once the policy is gone they are switched off, a policy can no longer pick them (400), and they are deleted (bug 77). | TC-A-01 (tester notes) |
| `smb` | A network data source: the run shares its work folder on this machine (a hidden share and a local account, both removed afterwards), the admin adds it as an SMB data source with this machine as scanner, and the agent signs in, classifies a file and writes its label back through the share; a second file that matches nothing stays unlabelled. Needs an elevated prompt, and the backend needs `SOURCE_SECRETS_KEY` to store the share's password. | — |

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
suites/<suite>/setup.yaml                  document types, secrecy labels + policies, set up once per run
suites/<suite>/cases/[<topic>/]<case>.yaml expected result per file
data/<suite>/<case>/                       the case's documents (Git LFS)
```

`setup.yaml`:

```yaml
document_types:                  # created for the run, deleted after it
  - {code: uat-finance, name: UAT Tài chính}
secrecy_labels:                  # created for the run, deleted after it
  - {code: uat_toi_mat, name: UAT Tối mật, more_secret_than: secret}
negative_document_types: []      # types a correct agent never assigns (see coverage check)
negative_secrecy: []             # same for secrecy levels
data_source:
  extra_extensions: [.png, .pptx]  # the console form starts with .pdf/.docx/.xlsx
  smb: true                        # optional: reach the work folder through an SMB share (run as administrator)
policies:                        # names must start with "UAT "
  - name: UAT example
    classification_methods: [keyword, dictionary]   # optional; default every technique
    conditions:
      - name: UAT phiếu chi
        secrecy: confidential            # and/or document_type: uat-finance
        keyword: phiếu chi
        min_count: 2                     # optional: the keyword must appear at least twice
      - name: UAT mã nhân viên
        document_type: uat-finance
        regex: '\bNV-\d{5}\b'           # a regex matcher instead of a keyword
      - name: UAT từ điển
        document_type: uat-finance
        dictionary: {terms: {phiếu xuất kho: 0.4, biên nhận: 0.4}, threshold: 0.8}  # threshold optional
      - name: UAT ngữ nghĩa
        document_type: uat-finance
        embedding: {descriptions: ["…"], threshold: 0.6}
      - name: UAT dấu mộc
        secrecy: secret
        stamp: {threshold: 0.8}          # one stamp rule reaches the agent: the highest threshold wins the merge
      - name: UAT hybrid
        document_type: uat-finance
        hybrid:
          combinator: all                # or any
          conditions: [{keyword: mã dự án}, {data_pattern: 'DA-[A-Z]{4}'}, {wordlist: [a, b]}]
```

Optional in `setup.yaml`:

```yaml
metrics:
  positive_secrecy: secret         # report how many files expecting it got it, and false alarms
health:
  max_rss_mb: 1500                 # this suite's own agent health limits
  max_avg_cpu_percent: 10
```

A case:

```yaml
title: What the case shows
expected:
  file-name.docx:
    secrecy: confidential
    domains: [uat-finance]
    result_from: backend   # optional: for a file the agent cannot label (encrypted, broken)
    starts_labelled: true  # optional: the file carries a NextDLP label before the run (TC-E-07)
    known_issue: "…"       # optional: a failure is reported KNOWN and does not fail the case
```

A case whose files all share one result lists none of them; every file in
`data/<suite>/<case>/` is checked, so adding a sample means adding a file:

```yaml
title: A round company stamp is found in any format
expect_all: {secrecy: secret, domains: [uat-stamp]}
```

A case can go on after the first result with `steps:`, things a user does to the
file, each optionally followed by the result it must have afterwards. The label in
the file and the backend's row are both checked; after an edit, only a report newer
than the edit counts. The actions (`rename`, `move`, `copy`, `edit`, `drop`,
`backend: stop|start`, `check`) are described in `harness/steps.py`, and a new one is
one function there.

```yaml
steps:
  - rename: {from: a.docx, to: b.docx}
    expect: {secrecy: confidential, domains: [uat-x]}
    same_document_as: a.docx         # the backend keeps a.docx's doc_id
  - edit: {file: b.docx, append: "…tuyệt mật…"}
    expect: {secrecy: secret, domains: [uat-x]}
```

A check marked `known_issue` shows as KNOWN while it fails and as FIXED once it
passes, which is the cue to remove the marker.

The result is read from the label the agent writes into the file (OOXML custom
properties, PDF Info dictionary); for images and files with `result_from: backend`
it is read from the backend's inventory instead.

A file no secrecy rule matches is `unlabelled`: the agent writes no `NextDLP_Secrecy`
into it (and removes a stale one), so its result is read from the backend and the
run checks that the file carries no `NextDLP_Secrecy` afterwards.

For an embedded label the run also checks that `NextDLP_ClassifiedAt` is an
ISO-8601 UTC time.

On a full-suite run the harness checks coverage: every document type and secrecy
level the setup can assign must be expected by some case, unless it is listed as
negative. A rule no case exercises therefore cannot slip in unnoticed.

A console suite has `console.yaml` instead of `setup.yaml`: each case names a check
function in `harness/console.py`, which drives the console, records the status of
every API request it makes, and removes what it created (also leftovers of an
earlier failed run).

```yaml
cases:
  - name: child-document-type-actions
    title: A child document type whose code contains "/" can be edited, deactivated and deleted
    check: child_document_type_actions
```

## Agent health

Every suite run also watches `nextdlp.exe`: it samples CPU and memory every 2 s,
and at the end checks that the process did not restart, that the service is still
running, the memory peak, and the size of the log folder (the agent keeps up to six
log files of 10 MB). The result is the extra `agent-health` case in each report,
and each report also gives the time from dropping a file to its result.

## What a suite run does

The UAT lives in a fixed place: Lac Hong Software Co., Ltd. → Software Development
Department, where this machine is enrolled. Its real policy and data sources stay as
they are; a run only adds objects named `UAT …`.

1. As admin in the console: creates the suite's document types and secrecy labels, one `UAT <suite>` data
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
   and deletes the suite's document types and secrecy labels (the purge took the
   label history that used them). It then checks that no labeled file, inventory
   row, document type or secrecy label is left and that the originals in `data/` are
   unlabeled.

## Layout

```
run.py                     entry point
harness/ui.py              admin console actions (Playwright)
harness/api.py             API client for waits and cleanup
harness/suite.py           setup, classify, verify, coverage check, cleanup
harness/console.py         console-only suites: checks that drive the console without the agent
harness/files.py           reads the label embedded in OOXML and PDF files
harness/steps.py           what a case does to its files after the first result
harness/health.py          agent CPU, memory, restarts and log size during a run
harness/report.py          the per-suite log
harness/config.py          settings (environment variables)
tools/make_data.py         regenerates the synthetic documents (--force to rewrite all)
tools/make_docx.py         minimal .docx, optionally with embedded images
tools/make_pptx.py         .pptx with slides, a table and speaker notes (python-pptx)
tools/make_xlsx.py         minimal .xlsx (one sheet, shared strings)
tools/make_media.py        PNG images, text / scanned PDFs, a synthetic red stamp on a scanned page
tools/make_broken.py       password-protected and corrupt .docx
tools/make_large.py        large plain-text files (tens of MB), written at run time
tools/make_corpus.py       performance corpus in data/perf-corpus/ (git-ignored), see "Performance corpus"
```

## Performance corpus

The corpus is for timing the agent on large documents, one file at a time, before
and after a change. It is not a suite: nothing is checked automatically, you read
the numbers.

### 1. Write the files

```powershell
cd C:\dev\dlp-regression
python tools/make_corpus.py --sizes 1M,20M,200M      # about 1.5 GB; without --sizes it adds 2 GB files (about 13 GB)
```

The files go to `data/perf-corpus/`, which git ignores. The content is synthetic and
the same seed always gives the same files, so every machine writes its own copy and
the numbers stay comparable. Existing files are kept; delete one to rewrite it.

| Files | What they exercise | Expected result with the default policy |
|---|---|---|
| `text-1mb.txt` | normal path: whole text in memory, rules, embedding | `secret` (rules `vn-cccd`, `vn-state-secret-ho-so-mat`: the filler has CCCD numbers and "hồ sơ mật") |
| `text-20mb.txt`, `text-200mb.txt` | two-pointer path (over 20 MiB): read in 5 MiB chunks from both ends | same; no domain (embedding refuses texts over 128 chunks) |
| `docx-*`, `xlsx-*` | OOXML: no chunked reader, the whole document is read at any size | `secret`, same rules |
| `pdf-text-*` | PDF with a text layer; pages are also rendered to look for stamps | slow: the 1 MB file (234 pages) takes minutes |
| `pdf-scan-*` | PDF with images only: every page is OCR'd | `confidential` (`vn-cccd` from the OCR text); the short OCR text also gets embedding domains |
| `pdf-stamp-*` | scanned contract pages, every other one with a red round stamp at 0°, 12° or -20° | `stamp: yes`, secrecy `secret` (the policy's stamp rule) |
| `boundary-25mb.txt` | matches placed across the 5 MiB chunk seams; `boundary-25mb.expect.txt` lists each one | every probe must be found; the filler also has CCCD numbers, so check it with a policy that only has the probe as a rule |
| `huge-50gb-sparse.txt` | the service's size limit (`max_file_mb`): not for `classify`, which would read 50 GB of zeros | the service skips it |

### 2. Time a file

Run the agent from `C:\dev\dlp-win\agent`, so a dev build reads
`configs\agent.conf` and `configs\default_policy.yaml` (8 secrecy rules, 2 keyword
and 2 dictionary domain rules, 4 embedding descriptions, the stamp rule). An
installed agent's `nextdlp.exe` needs an elevated prompt to read its own
`agent.conf`.

```powershell
cd C:\dev\dlp-win\agent
build\x64-release\Release\nextdlp.exe classify C:\dev\dlp-regression\data\perf-corpus\text-20mb.txt --timing --repeat 5
```

Use a Release build: a Debug build is many times slower and its numbers say little.
`--repeat 5` loads the models once and classifies the file five times; the first
run is cold (the file is not in the Windows cache yet), so compare the median of
the other four. Use `--repeat 1` for the 200 MB files and every PDF, which take
minutes per run.

The last line is the timing:

```
timing: {"setup_ms": {"config": 4, "rules_compile": 1, "ocr_init": 197, "yolo_init": 80, "embedding_init": 2134},
         "runs": [{"extract_ms": 2.7, "classify_ms": 406.9, "embed_ms": 402.1, "embed_calls": 1,
                   "total_ms": 410.3, "text_chars": 1048672, "two_pointer": false, "used_ocr": false}, ...]}
```

- `setup_ms`: loading the policy and the models, once per process.
- `extract_ms`: reading the text, OCR included. On the two-pointer path reading is
  inside `classify_ms` and `extract_ms` / `text_chars` stay 0.
- `classify_ms`: rules, with `embed_ms` (the embedding model) inside it.
- `total_ms`: one classification of the file.

Lines above it give the result (`secrecy`, rules that fired, `stamp`, `signature`).

### 3. Compare two builds

Time the same files on the build before the change and on the build after it, on
the same machine, with the same apps open (Docker, a browser and an IDE all take
CPU), and on mains power. Save each `timing:` line, e.g.
`... --timing --repeat 5 > before-text-20mb.txt`. A change counts when the median
moves well beyond the spread between the warm runs.

Numbers seen on the developer laptop (Ryzen 5 5600H, Release, main 56d4c3b):

| File | Median | Where the time goes |
|---|---|---|
| `text-1mb.txt` | 0.4 s | embedding 0.4 s |
| `text-20mb.txt` | 4.2 s | embedding 4.15 s: each half's first 5 MiB is tokenized, then refused for being over 128 chunks |
| `pdf-stamp-1mb.pdf` (5 pages) | 10.6 s with setup | render, stamp detection and OCR per page |
| `pdf-text-1mb.pdf` (234 pages) | over 20 s | page rendering for stamps |

## Rules for test data

- Synthetic content only: no customer or real personal documents.
- No two files share content: the agent caches results by content, so a duplicate
  would be answered from the cache (the run warns about it).
- Files in `data/` must not carry a NextDLP label; the run refuses labeled inputs,
  except files a case marks `starts_labelled`.
- Files too large to commit (`nhat-ky-*mb.txt`, `*-22mb.pdf`) are git-ignored; the run writes
  them with `tools/make_data.py` when they are missing.
- Trigger words avoid the department's real policy (hóa đơn, thuế, phí, lệ phí,
  số tiền, "contract", "quý 1"/"quý 2", 9-, 10- and 12-digit numbers) and every
  other rule of the suite. That is why the customer tests' own wording (hóa đơn,
  CCCD 001099012345…) is replaced by other words with the same meaning for the test.
- Semantic rules get their own threshold, chosen from measured similarities: run
  `nextdlp classify` with the rule's description in a local `default_policy.yaml`
  over every file of the suite, and keep a clear gap between the target files and
  all others.
