"""How many stored domains fail the hostname check? Counts only; writes nothing (launch checklist 3.81).

3.67 added a hostname check at client intake (`intake.parse_domain`). Clients stored before it, and
competitors typed or model-proposed before 3.81 put the same check on those paths, were never
checked. This reads `clients.domain` and `competitors.domain` and counts, per column, the values
that fail the SAME check, by kind:

  not_a_hostname       the name is not one DNS carries (a space, a quote, an empty label, ...)
  no_public_domain     a well-formed name with no public domain in it (an IP address, `co.uk`)
  not_in_checked_form  passes, but is not the form intake would store (upper case, a `www.` or
                       other subdomain, a scheme or path)

The first two are failures of the check; the third is a stored value the check would have
rewritten. Empty competitor domains are counted as `empty` and are not a failure.

IT PRINTS COUNTS ONLY: never a domain, a name or an id. A production client's domain must not reach
a terminal log, a ticket or a commit. Nothing is changed: a non-zero count is reported to whoever
decides what to do with those rows.

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \\
        uv run python scripts/count_unchecked_domains.py

With no `DATABASE_URL`, the API's own database setting is read (on Render, the Shell has it).
The transaction is declared READ ONLY before anything is read, and rolled back at the end, so
even a bug here could not write. Every row counts: clients that were soft-deleted, and the sets
of past scans, as well as the live ones.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from avp_api.config import Settings  # noqa: E402
from avp_api.models import Client, Competitor  # noqa: E402
from avp_api.services import intake  # noqa: E402

KINDS = ("not_a_hostname", "no_public_domain", "not_in_checked_form")


def classify(stored: str) -> str | None:
    """The kind of failure of one stored domain, or None if it is as intake would store it."""
    try:
        _url, domain = intake.parse_domain(stored)
    except intake.InvalidUrl as exc:
        return "no_public_domain" if exc.detail == intake._NO_PUBLIC_DOMAIN else "not_a_hostname"  # noqa: SLF001
    return None if domain == stored else "not_in_checked_form"


async def count_unchecked(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Counts per column: `checked` (rows read), `empty`, and one count per kind of failure."""
    # First statement of the transaction: the database itself refuses any write after it.
    await session.execute(text("SET TRANSACTION READ ONLY"))
    out: dict[str, dict[str, int]] = {}
    for label, column in (("clients.domain", Client.domain), ("competitors.domain", Competitor.domain)):
        counts = {"checked": 0, "empty": 0, **dict.fromkeys(KINDS, 0)}
        for stored in (await session.execute(select(column))).scalars():
            if stored is None or not stored.strip():
                counts["empty"] += 1
                continue
            counts["checked"] += 1
            kind = classify(stored)
            if kind:
                counts[kind] += 1
        out[label] = counts
    return out


def render(counts: dict[str, dict[str, int]]) -> list[str]:
    lines = []
    for label, c in counts.items():
        lines.append(f"{label}: " + " ".join(f"{k}={v}" for k, v in c.items()))
    failing = sum(c["not_a_hostname"] + c["no_public_domain"] for c in counts.values())
    rewritten = sum(c["not_in_checked_form"] for c in counts.values())
    lines.append(f"failing the check: {failing}; would be rewritten: {rewritten}")
    return lines


async def main() -> int:
    engine = create_async_engine(os.environ.get("DATABASE_URL", str(Settings().database_url)))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            counts = await count_unchecked(session)
            await session.rollback()
    finally:
        await engine.dispose()
    for line in render(counts):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
