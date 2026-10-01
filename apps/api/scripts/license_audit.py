"""Dependency licence audit — enforces the dependency-licensing rule.

Walks every installed distribution in the active environment, resolves its
licence from packaging metadata, and classifies it. Exits non-zero if anything
copyleft, source-available, or undeclared is present. MPL-2.0 used unmodified
is its own passing category (ALLOWED_UNMODIFIED, verdict "ok-unmodified").

Run:  uv run python scripts/license_audit.py
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import distributions

# Allowed without asking.
ALLOWED = {
    "MIT", "MIT License", "Expat",
    "Apache-2.0", "Apache 2.0", "Apache Software License", "Apache License 2.0",
    "BSD-2-Clause", "BSD-3-Clause", "BSD License", "BSD",
    "ISC", "ISC License (ISCL)",
    "0BSD", "Unlicense", "CC0-1.0", "CC0 1.0 Universal",
    "PSF-2.0", "Python Software Foundation License",
    # MIT No Attribution — MIT with the attribution clause removed, so it is
    # strictly more permissive than MIT. `cffi`, which `cryptography` (PyJWT's
    # `crypto` extra, Epic 20) depends on, moved to it; it sat on the REVIEW
    # list from Epic 20 until the 2026-09-21 audit made the decision.
    "MIT-0",
}

# Allowed for UNMODIFIED use only. A category of its own, deliberately NOT part
# of ALLOWED (founder decision 2, 2026-10-01).
#
# MPL-2.0 is file-level copyleft. It is allowed here because AVP uses these
# packages (today certifi and pathspec) unmodified and does not distribute
# modified source (hosted SaaS). Modifying an MPL-licensed file would oblige us
# to publish that file, so vendoring or patching an MPL package is NOT allowed
# without the founder. The verdict is "ok-unmodified": it does not fail the run
# and is printed apart from plain "ok" so it stays visible.
#
# Only MPL 2.0 is covered. MPL-1.1 and any other MPL version are not: they fall
# to REVIEW (listed, does not fail), because their terms differ and nobody has
# decided them. GPL/LGPL/AGPL are unaffected and still BLOCK.
ALLOWED_UNMODIFIED = {
    "MPL-2.0",
    "MPL 2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
}

# Stop-and-ask list.
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


def verdict_of(lic: str) -> str:
    """ok / ok-unmodified / REVIEW / BLOCK for one licence string.

    SPDX: parentheses are stripped; `A OR B` (and the `|` that joins several
    classifiers) passes if any alternative passes; `A AND B` needs every part
    to pass. Plain "ok" wins over "ok-unmodified" when both are available.
    """
    whole = lic.strip()
    if whole in ALLOWED:
        return "ok"
    if whole in ALLOWED_UNMODIFIED:  # e.g. the classifier "... (MPL 2.0)" has its own parentheses
        return "ok-unmodified"
    text = lic.replace("(", " ").replace(")", " ")
    alternatives = [
        [p.strip() for p in re.split(r"\s+AND\s+", alt) if p.strip()]
        for alt in re.split(r"\s+OR\s+|\|", text)
        if alt.strip()
    ]

    def permitted(p: str, pool: set[str]) -> bool:
        return p in pool or p.rstrip(".") in pool

    if any(all(permitted(p, ALLOWED) for p in parts) for parts in alternatives):
        return "ok"
    either = ALLOWED | ALLOWED_UNMODIFIED
    if any(all(permitted(p, either) for p in parts) for parts in alternatives):
        return "ok-unmodified"

    def blocking(p: str) -> bool:
        return bool(BLOCKING.search(p) and not NOT_COPYLEFT.search(p))

    if all(any(blocking(p) for p in parts) for parts in alternatives):
        return "BLOCK"
    if "UNDECLARED" in lic:
        return "BLOCK"
    return "REVIEW"


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    for dist in sorted(distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name")
        if not name:
            continue
        lic = licence_of(dist)
        verdict = verdict_of(lic)
        rows.append((name, dist.version or "?", lic, verdict))

    width = max(len(r[0]) for r in rows) if rows else 10
    counts: dict[str, int] = {}
    for name, version, lic, verdict in rows:
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "ok":
            print(f"  {verdict:<13} {name:<{width}} {version:<12} {lic}")

    print(f"\n{len(rows)} distributions audited")
    for verdict in ("ok", "ok-unmodified", "REVIEW", "BLOCK"):
        if verdict in counts:
            print(f"  {verdict:<13} {counts[verdict]}")

    by_licence: dict[str, int] = {}
    for _, _, lic, _ in rows:
        by_licence[lic] = by_licence.get(lic, 0) + 1
    print("\nLicence distribution:")
    for lic, n in sorted(by_licence.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {lic}")

    if counts.get("BLOCK"):
        print("\nFAIL: blocking or undeclared licences present (the dependency-licensing rule).")
        return 1
    print("\nPASS: no copyleft, source-available, or undeclared licences.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
