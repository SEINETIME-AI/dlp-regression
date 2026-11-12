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
    # Agent health limits for every suite (a suite's `health:` overrides them). The log
    # folder holds up to 6 files of 10 MB (the current one and 5 rotations). The
    # classification suite's 22 MB files take the agent to ~2.6 GB (2026-10-25).
    health_limits: dict = field(default_factory=lambda: {
        "max_rss_mb": int(os.environ.get("UAT_MAX_AGENT_RSS_MB", "3072")),
        "max_log_mb": int(os.environ.get("UAT_MAX_AGENT_LOG_MB", "64")),
    })
    # How a suite with `backend: stop/start` steps takes the server away from the agent,
    # e.g. "docker stop nextdlp-release-dlp-server-1"; such a suite fails without them.
    backend_stop: str = os.environ.get("UAT_BACKEND_STOP", "")
    backend_start: str = os.environ.get("UAT_BACKEND_START", "")
    # The anti-uninstall suite plays an agent over gRPC with stubs built from the backend's protos at run time.
    grpc_target: str = os.environ.get("UAT_GRPC_TARGET", "localhost:9091")
    backend_proto_dir: Path = Path(os.environ.get("UAT_BACKEND_PROTO_DIR", r"C:\dev\dlp-ext-backend\proto"))
    # The API cannot delete a machine, so the suite removes its simulated one with SQL (dev stack container).
    psql: str = os.environ.get("UAT_PSQL", "docker exec -i dlp-ext-dev-postgres psql -U postgres -d dlp -v ON_ERROR_STOP=1")
    # Where the anti-uninstall suite saves its Console screenshots (for the wiki).
    screenshot_dir: Path = Path(os.environ.get("UAT_SCREENSHOT_DIR", str(REPO_ROOT / "reports" / "screenshots")))
    policy_timeout_s: int = 240
    classify_timeout_s: int = 240
    # A suite stops waiting for labels once the agent has classified no file for this long (or restarted):
    # longer than the slowest single file (a 22 MB text under the agent's CPU cap), far shorter than the
    # classify timeout. UAT_STALL_S overrides it.
    stall_timeout_s: int = int(os.environ.get("UAT_STALL_S", "120"))
    # How often a waiting suite prints how far it got.
    progress_every_s: int = 30
    data_dir: Path = field(default=REPO_ROOT / "data")
    suites_dir: Path = field(default=REPO_ROOT / "suites")
    reports_dir: Path = field(default=REPO_ROOT / "reports")
