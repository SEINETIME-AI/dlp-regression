import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # The frontend dev server proxies /api to the backend, so one origin serves both.
    base_url: str = os.environ.get("UAT_BASE_URL", "http://localhost:5173")
    admin_user: str = os.environ.get("UAT_ADMIN_USER", "admin")
    admin_password: str = os.environ.get("UAT_ADMIN_PASS", "admin")
    machine: str = os.environ.get("UAT_MACHINE", socket.gethostname()).upper()
    # Files are copied here per run; the data source points at the run folder.
    work_root: Path = Path(os.environ.get("UAT_WORK_ROOT", r"C:\dlp-uat"))
    agent_log: Path = Path(r"C:\ProgramData\DlpExtension\logs\nextdlp.log")
    # The UAT lives in this fixed department, next to its real policy; a run only
    # adds (and later removes) a data source and a policy whose names start with prefix.
    company_name: str = "Lac Hong Software Co., Ltd."
    department_code: str = "software-dev"
    department_name: str = "Software Development Department"
    prefix: str = "UAT "
    policy_timeout_s: int = 240
    classify_timeout_s: int = 240
    data_dir: Path = field(default=REPO_ROOT / "data")
    suites_dir: Path = field(default=REPO_ROOT / "suites")
    reports_dir: Path = field(default=REPO_ROOT / "reports")
