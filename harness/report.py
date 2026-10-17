import datetime
from pathlib import Path


def write_log(reports_dir: Path, result) -> Path:
    """Writes reports/<scenario>.log, replacing the previous run's log."""
    lines = [f"{result.name}  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}"]
    lines += [f"  [{'PASS' if ok else 'FAIL'}] {message}" for ok, message in result.checks]
    if result.error:
        lines.append(f"  [ERROR] {result.error}")
    lines.append(f"  => {'PASSED' if result.passed else 'FAILED'} in {result.seconds:.0f}s")
    reports_dir.mkdir(parents=True, exist_ok=True)
    log = reports_dir / f"{result.name}.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log
