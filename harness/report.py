import datetime
from pathlib import Path


def write_log(reports_dir: Path, result) -> Path:
    """Writes reports/<suite>.log with each case's result, replacing the last run's."""
    lines = [f"{result.name}  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}"]
    lines += [f"  [WARN] {warning}" for warning in result.warnings]
    for case in result.cases:
        lines.append(f"  {'PASS' if case.passed else 'FAIL'}  {case.name}")
        lines += [f"        {'ok  ' if ok else 'BAD '} {message}" for ok, message in case.checks]
        if case.error:
            lines.append(f"        ERROR {case.error}")
    if result.error:
        lines.append(f"  [ERROR] {result.error}")
    passed = sum(case.passed for case in result.cases)
    lines.append(f"  => {'PASSED' if result.passed else 'FAILED'} "
                 f"({passed}/{len(result.cases)} case(s)) in {result.seconds:.0f}s")
    reports_dir.mkdir(parents=True, exist_ok=True)
    log = reports_dir / f"{result.name}.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def append_cleanup(log: Path, result) -> str:
    line = f"  cleanup {'ok' if result.cleanup_ok else 'FAILED'}: {result.cleanup or 'nothing to remove'}\n"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return line
