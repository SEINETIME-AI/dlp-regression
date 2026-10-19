"""Large plain-text files, written at run time instead of being committed (tens of MB).

Neutral log lines fill the file up to `size_mb`; the trigger line goes in after
`trigger_after_mb` megabytes (or right at the end when that is None). `first_line`,
when given, opens the file, so a rule needing two signals sees one in each half.
"""
MB = 1024 * 1024


def write_large_text(path: str, size_mb: float, trigger: str, trigger_after_mb: float | None, seed: str,
                     first_line: str | None = None):
    target = int(size_mb * MB)
    trigger_at = target - len(trigger.encode("utf-8")) - 200 if trigger_after_mb is None else int(trigger_after_mb * MB)
    written = 0
    placed = False
    line_number = 0
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        if first_line:
            out.write(f"{first_line}\n")
            written += len(first_line.encode("utf-8")) + 1
        while written < target:
            if not placed and written >= trigger_at:
                line = f"{trigger}\n"
                placed = True
            else:
                line_number += 1
                line = f"[{seed}] dòng nhật ký {line_number}: tác vụ sao lưu định kỳ hoàn tất, không có cảnh báo.\n"
            out.write(line)
            written += len(line.encode("utf-8"))
