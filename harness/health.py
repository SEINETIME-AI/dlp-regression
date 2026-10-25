"""Watches the agent while a suite runs: CPU and memory of nextdlp.exe, whether the
service restarted (a crash the SCM recovered from), and how much its log folder holds.
Limits come from Settings and can be overridden per suite under `health:` in setup.yaml."""
import threading
import time
from pathlib import Path

import psutil

PROCESS = "nextdlp.exe"
SERVICE = "nextdlp"


def _agent_process():
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() == PROCESS:
            return process
    return None


def _folder_mb(folder: Path) -> float:
    try:
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 2**20
    except OSError:
        return 0.0


class AgentMonitor:
    def __init__(self, log_dir: Path, limits: dict, interval_s: float = 2.0):
        self.log_dir = log_dir
        self.limits = limits
        self.interval_s = interval_s
        self.samples: list[tuple[float, float]] = []  # (cpu % of the whole machine, RSS MB)
        self.pids: list[int] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _sample(self):
        process = None
        cores = psutil.cpu_count() or 1
        while not self._stop.is_set():
            try:
                if process is None or not process.is_running():
                    process = _agent_process()
                    if process:
                        process.cpu_percent(None)  # the first call only starts the measurement
                if process:
                    if not self.pids or self.pids[-1] != process.pid:
                        self.pids.append(process.pid)
                    else:
                        self.samples.append((process.cpu_percent(None) / cores,
                                             process.memory_info().rss / 2**20))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process = None
            self._stop.wait(self.interval_s)

    def stop(self) -> list[tuple[bool, str]]:
        """Checks for the report: one (ok, message) per limit."""
        self._stop.set()
        self._thread.join()
        checks = []
        restarts = len(self.pids) - 1
        checks.append((restarts <= 0 and bool(self.pids),
                       "agent process not found" if not self.pids else f"agent restarted {restarts} time(s)"))
        try:
            status = psutil.win_service_get(SERVICE).status()
        except Exception as error:  # noqa: BLE001
            status = f"unknown ({error})"
        checks.append((status == "running", f"service {SERVICE} is {status}"))
        if self.samples:
            cpu = [c for c, _ in self.samples]
            rss = [m for _, m in self.samples]
            peak_rss, avg_cpu, peak_cpu = max(rss), sum(cpu) / len(cpu), max(cpu)
            limit = self.limits.get("max_rss_mb")
            checks.append((limit is None or peak_rss <= limit,
                           f"memory peak {peak_rss:.0f} MB" + (f" (limit {limit})" if limit else "")))
            limit = self.limits.get("max_avg_cpu_percent")
            checks.append((limit is None or avg_cpu <= limit,
                           f"CPU average {avg_cpu:.1f}% peak {peak_cpu:.1f}% of the machine over "
                           f"{len(cpu)} samples" + (f" (limit {limit}% average)" if limit else "")))
        logs = _folder_mb(self.log_dir)
        limit = self.limits.get("max_log_mb")
        checks.append((limit is None or logs <= limit,
                       f"log folder {logs:.1f} MB" + (f" (limit {limit})" if limit else "")))
        return checks
