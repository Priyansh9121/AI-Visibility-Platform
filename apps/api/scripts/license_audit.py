"""Dependency licence audit — enforces ip-safety.md constraint 6.

Walks every installed distribution in the active environment, resolves its
licence from packaging metadata, and classifies it. Exits non-zero if anything
copyleft, source-available, or undeclared is present.

Run:  uv run python scripts/license_audit.py
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import distributions

# ip-safety.md "Operational notes": allowed without asking.
ALLOWED = {
    "MIT", "MIT License", "Expat",
    "Apache-2.0", "Apache 2.0", "Apache Software License", "Apache License 2.0",
    "BSD-2-Clause", "BSD-3-Clause", "BSD License", "BSD",
    "ISC", "ISC License (ISCL)",
    "0BSD", "Unlicense", "CC0-1.0", "CC0 1.0 Universal",
    "PSF-2.0", "Python Software Foundation License",
}

# MPL-2.0 is deliberately NOT in ALLOWED. It is weak (file-level) copyleft, so it
# does not infect a proprietary codebase when used unmodified — but ip-safety.md
# says "MIT/Apache-2.0/BSD only", and MPL is none of those. It surfaces as REVIEW
# so a human decides, rather than being silently waved through.

# Stop-and-ask list from ip-safety.md.
BLOCKING = re.compile(
    r"\b(GPL|AGPL|LGPL|SSPL|BUSL|Business Source|Elastic License|Commons.?Clause|"
    r"source.available|Prosperity|Polyform)\b",
    re.IGNORECASE,
)
# "GPL" inside "LGPL" etc. is caught above; these are false-positive guards.
NOT_COPYLEFT = re.compile(r"GPL.?compatible|with GPL|GPL exception", re.IGNORECASE)


def licence_of(dist) -> str:  # noqa: ANN001
    meta = dist.metadata
    # PEP 639 first: License-Expression is the modern, unambiguous field.
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " | ".join(c.rsplit(" :: ", 1)[-1] for c in classifiers)
    raw = (meta.get("License") or "").strip()
    if raw and len(raw) < 200 and "\n" not in raw:
        return raw
    if raw:
        return "UNDECLARED (licence text inlined, not an identifier)"
    return "UNDECLARED"


def normalise(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s+(?:OR|AND)\s+|\|", text) if p.strip()]


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    for dist in sorted(distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name")
        if not name:
            continue
        lic = licence_of(dist)
        parts = normalise(lic)
        if any(BLOCKING.search(p) and not NOT_COPYLEFT.search(p) for p in parts):
            verdict = "BLOCK"
        elif any(p in ALLOWED or p.rstrip(".") in ALLOWED for p in parts):
            verdict = "ok"
        elif "UNDECLARED" in lic:
            verdict = "BLOCK"
        else:
            verdict = "REVIEW"
        rows.append((name, dist.version or "?", lic, verdict))

    width = max(len(r[0]) for r in rows) if rows else 10
    counts: dict[str, int] = {}
    for name, version, lic, verdict in rows:
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "ok":
            print(f"  {verdict:<7} {name:<{width}} {version:<12} {lic}")

    print(f"\n{len(rows)} distributions audited")
    for verdict in ("ok", "REVIEW", "BLOCK"):
        if verdict in counts:
            print(f"  {verdict:<7} {counts[verdict]}")

    by_licence: dict[str, int] = {}
    for _, _, lic, _ in rows:
        by_licence[lic] = by_licence.get(lic, 0) + 1
    print("\nLicence distribution:")
    for lic, n in sorted(by_licence.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {lic}")

    if counts.get("BLOCK"):
        print("\nFAIL: blocking or undeclared licences present (ip-safety.md #6).")
        return 1
    print("\nPASS: no copyleft, source-available, or undeclared licences.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
