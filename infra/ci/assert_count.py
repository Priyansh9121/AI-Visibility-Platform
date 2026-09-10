#!/usr/bin/env python3
"""Assert a suite's summary line against `floors.json` — Epic 18.

    assert_count.py <key> <log-file>

Reads the log a suite wrote, finds its summary line, and fails if the count is
below the floor (or, for a ceiling key, above it). The log must be produced by
redirecting the tool's output to a file and checking the tool's OWN exit
status first — never by piping through `tail`, which is the exact mistake
north-star.md §4.3 records. This script is the second gate, not the first.

Summary formats recognised:
    pytest       "1194 passed in 59.48s"      (any "N passed" on the last lines)
    vitest       "Tests  606 passed (606)"
    node --test  "# pass 53"
    mypy         "Found 50 errors in 29 files" or "Success: no issues found"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "api-pytest": ("floor", re.compile(r"(\d+) passed")),
    "workers-pytest": ("floor", re.compile(r"(\d+) passed")),
    "shared-types-node-test": ("floor", re.compile(r"^# pass (\d+)", re.M)),
    "design-system-vitest": ("floor", re.compile(r"Tests\s+(\d+) passed")),
    "web-vitest": ("floor", re.compile(r"Tests\s+(\d+) passed")),
    "api-mypy-errors": ("ceiling", re.compile(r"Found (\d+) errors?")),
}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    key, log_path = argv[1], Path(argv[2])
    if key not in PATTERNS:
        print(f"unknown key {key!r}; known: {', '.join(PATTERNS)}", file=sys.stderr)
        return 2
    kind, pattern = PATTERNS[key]
    config = json.loads((HERE / "floors.json").read_text())
    bound = config["floors" if kind == "floor" else "ceilings"][key]
    # vitest colours its summary when it thinks it has a TTY (it does on a
    # GitHub runner): "Tests \x1b[22m \x1b[1m\x1b[32m606 passed". Strip escapes
    # before matching so the count is read from the words, not the paint.
    text = ANSI.sub("", log_path.read_text(errors="replace"))

    matches = pattern.findall(text)
    if not matches:
        if kind == "ceiling" and "Success: no issues found" in text:
            count = 0
        else:
            print(f"{key}: no summary line found in {log_path} — the suite did not run to completion")
            print(text[-1500:])
            return 1
    else:
        count = int(matches[-1])

    if kind == "floor" and count < bound:
        print(f"{key}: {count} passed, floor is {bound} — the suite silently shrank. FAIL")
        return 1
    if kind == "ceiling" and count > bound:
        print(f"{key}: {count} errors, ceiling is {bound} — new type errors were introduced. FAIL")
        return 1
    print(f"{key}: {count} ({kind} {bound}) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
