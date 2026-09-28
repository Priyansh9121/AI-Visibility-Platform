"""The scan allowance — one free scan, then forty a month on the plan (2026-09-28).

WHY A GATE EXISTS AT ALL
------------------------
Until this date nothing in the product was behind the plan, and the pricing
card said so. Stripe is wired to a $160 Price; no rational agency pays for
what it already gets free. And a scan is not free to run: about **$1.70 in
engine calls** each, plus 6 of the **250 SerpApi searches** the Free tier
allows per month — about 41 scans platform-wide before every user's
competitor detection stops at once. An unmetered free tier is an open cost
line with a shared fuse. So: the plan gates STARTING a scan, and only that.

THE ALLOWANCE
-------------
* **Free** (no active subscription): `FREE_SCAN_ALLOWANCE` completed scans per
  agency, lifetime. Viewing reports, share links and PDFs stays free forever.
* **Paid** (`services/billing.is_active`: `active` or `trialing`):
  `PAID_SCANS_PER_MONTH` per agency per calendar month, UTC.
* A `canceled`, `past_due`, `unpaid` or `paused` subscription is free.
* Existing agencies are grandfathered nothing: completed scans already in the
  table count, so an agency that has scanned is at "free scan used". That is
  correct — it has had its free scan.

WHAT COUNTS AS A SCAN
---------------------
Every scan the agency has started that has not FAILED or been CANCELLED —
queued and running included. Two reasons. A scan that ends `failed` must
never burn the free allowance, so terminal failures are out. And a scan in
flight must count while it is in flight, or the gate is a fiction: with only
finished scans counted, two requests at the cap would both see room and both
start. The slot is taken when the scan starts and handed back if it fails.

Prompt RE-RUNS (`services/prompt_runs`) are not scans, create no `Scan` row,
and stay governed by their own 30-an-hour throttle only.

HOW THE RACE IS CLOSED
----------------------
`assert_can_start` takes `SELECT ... FOR UPDATE` on the agency row before it
counts, and the caller inserts and commits inside the same transaction. A
second concurrent request blocks on the lock, then counts the first request's
committed scan, then gets its 402. Counting without the lock — even inside one
transaction — is a check-then-act at READ COMMITTED, which is exactly the
shape Epic 9.6 found and closed for the per-client case.

FAILED-OPEN, DELIBERATELY, WHEN BILLING IS NOT CONFIGURED
---------------------------------------------------------
With `STRIPE_SECRET_KEY` or `STRIPE_PRICE_ID` unset — local development and
CI — the gate is OFF: `gate_enabled` is false and `assert_can_start` is never
called. That matches how checkout already answers `503 billing-not-configured`
rather than breaking the app: nobody can subscribe on such a deployment, so a
gate there would be a cap nobody could lift. With both set, the gate is ON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import ScanLimitReached
from ..models.scan import Scan, ScanStatus
from ..models.tenancy import Agency
from . import billing

# One full scan and report, free, per agency, for the life of the agency. One
# is the number that lets a prospect judge the product on a business they know
# and is not a number worth building a second agency to get again.
FREE_SCAN_ALLOWANCE = 1

# Forty a month on the $160 plan. The arithmetic: a scan costs about $1.70 in
# engine calls (measured, build log 2026-09-12: 20 prompts × 4 engines plus
# sentiment), so 40 × $1.70 ≈ $68 of engine cost against $160 — about 57%
# gross margin before SerpApi. SerpApi's Free tier is 250 searches a month and
# detection spends 6 per NEW client, so forty scans of forty new clients would
# be 240 searches; forty re-scans of existing clients spend none. The cap is
# per calendar month, UTC, because Stripe's billing period and a calendar
# month differ by at most a few days and a month is what the card can say.
PAID_SCANS_PER_MONTH = 40

# What holds a slot. Everything that has not ended in failure or cancellation:
# a queued or running scan is counted while it runs (see the module docstring).
COUNTED_STATUSES = (
    ScanStatus.QUEUED,
    ScanStatus.RUNNING,
    ScanStatus.SUCCEEDED,
    ScanStatus.PARTIAL,
)

CODE_FREE_USED = "SCAN_LIMIT_FREE_USED"
CODE_MONTHLY = "SCAN_LIMIT_MONTHLY"


def gate_enabled(settings: Settings) -> bool:
    """On when billing is configured; off — deliberately — when it is not."""
    return bool(settings.stripe_secret_key and settings.stripe_price_id)


def period_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The UTC calendar month containing `now`: its first instant and the next month's."""
    now = now.astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_start = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, next_start


@dataclass(frozen=True, slots=True)
class Allowance:
    """What the agency may start, and how much of it is used.

    `scan_limit` is None when the gate is off (billing not configured) — the
    honest representation of "no limit applies here", never a large number.
    `period_resets_at` is set for a paid agency only; a free allowance is
    lifetime and does not reset.
    """

    scans_used: int
    scan_limit: int | None
    period_resets_at: datetime | None
    is_paid: bool

    @property
    def exhausted(self) -> bool:
        return self.scan_limit is not None and self.scans_used >= self.scan_limit


async def count_scans(
    db: AsyncSession,
    agency_id: str,
    *,
    since: datetime | None = None,
    exclude_scan_id: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.agency_id == agency_id, Scan.status.in_(COUNTED_STATUSES))
    )
    if since is not None:
        stmt = stmt.where(Scan.created_at >= since)
    if exclude_scan_id is not None:
        stmt = stmt.where(Scan.id != exclude_scan_id)
    return int((await db.execute(stmt)).scalar_one())


async def allowance(
    db: AsyncSession,
    agency: Agency,
    settings: Settings,
    *,
    now: datetime | None = None,
    exclude_scan_id: str | None = None,
) -> Allowance:
    """The agency's allowance as of `now`. Pure read; takes no lock.

    `exclude_scan_id` is the open QUEUED scan a run is about to adopt rather
    than create (`get_or_create_scan` reuses it): it is the scan being started,
    not a scan already started, so it must not count against starting it.
    """
    now = now or datetime.now(UTC)
    paid = billing.is_active(agency)
    if not gate_enabled(settings):
        used = await count_scans(db, agency.id, exclude_scan_id=exclude_scan_id)
        return Allowance(scans_used=used, scan_limit=None, period_resets_at=None, is_paid=paid)
    if paid:
        start, next_start = period_bounds(now)
        used = await count_scans(db, agency.id, since=start, exclude_scan_id=exclude_scan_id)
        return Allowance(
            scans_used=used,
            scan_limit=PAID_SCANS_PER_MONTH,
            period_resets_at=next_start,
            is_paid=True,
        )
    used = await count_scans(db, agency.id, exclude_scan_id=exclude_scan_id)
    return Allowance(
        scans_used=used, scan_limit=FREE_SCAN_ALLOWANCE, period_resets_at=None, is_paid=False
    )


async def assert_can_start(
    db: AsyncSession,
    agency_id: str,
    settings: Settings,
    *,
    exclude_scan_id: str | None = None,
    now: datetime | None = None,
) -> Allowance:
    """Lock the agency row, count, and refuse with 402 if the allowance is spent.

    The lock is released by the caller's COMMIT (or rollback), so the caller
    must insert its scan and commit in the same transaction — that is the
    whole mechanism. Re-reads the agency under the lock rather than trusting
    the principal's copy, so a subscription that changed between the request
    starting and this point is seen.
    """
    agency = (
        await db.execute(select(Agency).where(Agency.id == agency_id).with_for_update())
    ).scalar_one()
    current = await allowance(db, agency, settings, now=now, exclude_scan_id=exclude_scan_id)
    if not current.exhausted:
        return current
    assert current.scan_limit is not None
    if current.is_paid:
        assert current.period_resets_at is not None
        raise ScanLimitReached(
            detail=(
                f"This agency has started {current.scans_used} of its "
                f"{current.scan_limit} scans this month. The allowance resets on "
                f"{current.period_resets_at:%d %b %Y} (UTC)."
            ),
            code=CODE_MONTHLY,
            scansUsed=current.scans_used,
            scanLimit=current.scan_limit,
            periodResetsAt=current.period_resets_at.isoformat().replace("+00:00", "Z"),
        )
    raise ScanLimitReached(
        detail=(
            "This agency has used its free scan. Subscribe to keep scanning — "
            "viewing, sharing and exporting the reports you already have stays free."
        ),
        code=CODE_FREE_USED,
        scansUsed=current.scans_used,
        scanLimit=current.scan_limit,
        periodResetsAt=None,
    )
