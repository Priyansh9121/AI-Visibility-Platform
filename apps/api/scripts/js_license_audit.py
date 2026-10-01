"""JS dependency licence audit for the pnpm workspace.

Reads the output of `pnpm licenses list --json` and applies the SAME policy as
`license_audit.py` (ALLOWED, BLOCKING, NOT_COPYLEFT are imported from it by file
path, so there is one policy). Stdlib only; runs under plain `python3`.

Verdicts: ok / REVIEW (neither allowed nor blocked; listed, does not fail) /
BLOCK (copyleft, source-available or undeclared; fails).

SPDX: parentheses are stripped; `A OR B` passes if any alternative passes;
`A AND B` needs every part to pass.

infra/ci/licence-pending-decisions.json lists findings the founder has not yet
decided. It is NOT an allow-list: an entry is printed on every run, and holds a
package only while that package is BLOCK and both the name pattern and the exact
licence string match. An entry that matches nothing, or matches something that
is not blocking, fails the run, so the file cannot go stale or silence more.

Run:  python3 apps/api/scripts/js_license_audit.py <pnpm-licenses.json>
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_PENDING = _HERE.parents[2] / "infra" / "ci" / "licence-pending-decisions.json"


def _load_policy():
    spec = importlib.util.spec_from_file_location("license_audit", _HERE / "license_audit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_policy = _load_policy()
ALLOWED = _policy.ALLOWED
BLOCKING = _policy.BLOCKING
NOT_COPYLEFT = _policy.NOT_COPYLEFT
normalise = _policy.normalise

UNDECLARED = {"", "unknown", "unlicensed"}


def _allowed(part: str) -> bool:
    return part in ALLOWED or part.rstrip(".") in ALLOWED


def _blocking(part: str) -> bool:
    return bool(BLOCKING.search(part) and not NOT_COPYLEFT.search(part))


def verdict_of(licence: str) -> str:
    lic = (licence or "").strip()
    if lic.lower() in UNDECLARED:
        return "BLOCK"
    text = lic.replace("(", " ").replace(")", " ")
    # Alternatives: split on OR; each alternative is a set of AND parts.
    alternatives = [
        [p for p in re.split(r"\s+AND\s+", alt.strip()) if p.strip()]
        for alt in re.split(r"\s+OR\s+|\|", text)
        if alt.strip()
    ]
    if any(all(_allowed(p.strip()) for p in parts) for parts in alternatives):
        return "ok"
    if all(any(_blocking(p) for p in parts) for parts in alternatives):
        return "BLOCK"
    return "REVIEW"


def load_packages(path: Path) -> list[tuple[str, str, str]]:
    data = json.loads(path.read_text())
    rows: list[tuple[str, str, str]] = []
    for entries in data.values():
        for e in entries:
            lic = e.get("license")
            lic = lic if isinstance(lic, str) else ""
            for version in e.get("versions") or ["?"]:
                rows.append((e["name"], version, lic))
    return sorted(set(rows), key=lambda r: (r[0].lower(), r[1]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("licences_json", type=Path)
    ap.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    args = ap.parse_args(argv)

    rows = [(n, v, lic, verdict_of(lic)) for n, v, lic in load_packages(args.licences_json)]
    pending = json.loads(args.pending.read_text())

    failures: list[str] = []
    held: set[tuple[str, str]] = set()
    pending_lines: list[str] = []
    for entry in pending:
        pattern, lic = entry["package"], entry["licence"]
        by_name = [r for r in rows if fnmatch.fnmatchcase(r[0], pattern) and r[2] == lic]
        pending_lines.append(
            f"PENDING FOUNDER DECISION: {pattern} ({lic}), raised {entry['raised']}: "
            f"{entry['question']}"
        )
        if not by_name:
            failures.append(f"pending entry {pattern} ({lic}) matches no package; remove it")
            continue
        for n, v, _lic, verdict in by_name:
            if verdict == "BLOCK":
                held.add((n, v))
            else:
                failures.append(
                    f"pending entry {pattern} ({lic}) matches {n}@{v}, which is not "
                    "blocking; remove the entry"
                )

    width = max((len(r[0]) for r in rows), default=10)
    counts: dict[str, int] = {}
    for name, version, lic, verdict in rows:
        is_held = (name, version) in held
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict == "BLOCK" and is_held:
            print(f"  {'PENDING':<7} {name:<{width}} {version:<12} {lic}")
        elif verdict != "ok":
            print(f"  {verdict:<7} {name:<{width}} {version:<12} {lic}")
            if verdict == "BLOCK":
                failures.append(f"{name}@{version} {lic}")

    print(f"\n{len(rows)} packages audited")
    for verdict in ("ok", "REVIEW", "BLOCK"):
        if verdict in counts:
            print(f"  {verdict:<7} {counts[verdict]}")
    print(f"  held pending founder decision: {len(held)}")
    for line in pending_lines:
        print(line)

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  {f}")
        return 1
    print("\nPASS: no unheld copyleft, source-available, or undeclared licences.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
