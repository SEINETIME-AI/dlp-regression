# dlp-regression — UAT suites for NextDLP

Workspace rules and the task pipeline: `C:\dev\CLAUDE.md` (`dlp-flow`). Running UAT as a pipeline
stage, with the monitor dashboard and the baseline check, is the skill `dlp-uat` in this repo.

## Setup and run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
git lfs pull
.\.venv\Scripts\python.exe run.py --suite classification            # one suite
.\.venv\Scripts\python.exe run.py --suite classification --case 48-autocad-drawing
.\.venv\Scripts\python.exe run.py --all                             # every suite
.\.venv\Scripts\python.exe run.py --cleanup                         # leftovers of an aborted run
```

- Suites: `classification`, `mode`, `stamp`, `stamp-issuer`, `lifecycle`, `offline`, `console`, `smb`,
  `anti-uninstall` (README "Suites"). `reports/<suite>.log` is replaced by each run; a failure leaves `<suite>-failure.png`.
- Settings are environment variables (`harness/config.py`): `UAT_BASE_URL` (default the dev Console
  `http://localhost:5173`), `UAT_ADMIN_USER`, `UAT_ADMIN_PASS`, `UAT_MACHINE`, `UAT_WORK_ROOT`. The
  password is typed by the user into the shell; it is never written into a file, a script or a commit.
- From Git Bash set `PYTHONIOENCODING=utf-8`, or Vietnamese output breaks the run.
- The dev stack (`C:\dev\start-dev.ps1`) or another stack must be running and this machine's agent
  enrolled and online. Testing an agent build that is not installed: README "Run" (swap the service exe
  as administrator, put the backup back afterwards).

## Rules

- Commits go straight to `main`; push only when the user says so.
- Test data is synthetic, never two files with the same content, never a file that already carries a
  NextDLP label; binaries go through Git LFS (`.gitattributes`). Full list: README "Rules for test data".
- Vietnamese payloads and file names are written from UTF-8 Python files, never typed through the
  shell or curl (that corrupted names on 2026-11-09).
- A run only adds objects named `UAT …` and removes them at the end; never touch the real policy.
- The HTTPS test stack (`C:\nextdlp-test`, own CA) also needs `UAT_IGNORE_HTTPS_ERRORS=1` and
  `SSL_CERT_FILE=C:\nextdlp-test\certs\ca.crt` (once the harness change reading them is committed).
  Any helper proxy binds to `127.0.0.1` only.
