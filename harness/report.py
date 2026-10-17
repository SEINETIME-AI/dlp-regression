from pathlib import Path


def write_report(report_dir: Path, run_id: str, results) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(r.passed for r in results)
    lines = [f"# UAT run {run_id}", "", f"{passed}/{len(results)} scenario(s) passed.", ""]
    for result in results:
        lines += [f"## {'PASS' if result.passed else 'FAIL'} — {result.name}", "",
                  f"{result.title} ({result.seconds:.0f}s)", ""]
        lines += [f"- {'✅' if ok else '❌'} {message}" for ok, message in result.checks]
        if result.error:
            lines += ["", f"**Error:** {result.error}"]
        lines.append("")
    summary = report_dir / "summary.md"
    summary.write_text("\n".join(lines), encoding="utf-8")
    return summary
