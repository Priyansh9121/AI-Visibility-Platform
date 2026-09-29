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
  agency, lifetime, started only by a user with a linked Google identity
  (2026-09-29, below). Viewing reports, share links and PDFs stays free
  forever.
* **Paid** (`services/billing.is_active`: `active` or `trialing`):
  `PAID_SCANS_PER_MONTH` per agency per calendar month, UTC.
* A `canceled`, `past_due`, `unpaid` or `paused` subscription is free.
* Existing agencies are grandfathered nothing: completed scans already in the
  table count, so an agency that has scanned is at "free scan used". That is
  correct — it has had its free scan.

WHAT COUNTS AS A SCAN
---------------------
A scan that finished with something to show — SUCCEEDED or PARTIAL — counts
for good. A scan that ended FAILED or CANCELLED never counts: a failure must
never burn the free allowance.

A scan still QUEUED or RUNNING counts **only while it is plausibly still
alive** — `created_at` within `IN_FLIGHT_WINDOW`. An in-flight scan has to
count for the window it is in flight, or the gate is a fiction: with only
finished scans counted, two requests at the cap would both see room and both
start. But counting it forever is the other failure, and it is the one that
was here until 2026-09-28. A scan can stay QUEUED or RUNNING and never
finish:

* **Lost executor.** The executor is a per-request `BackgroundTasks` job and
  Render restarts the process on every deploy. A scan queued seconds before a
  restart is never picked up. `scan_executor.reap_stale_scans` rescues the
  RUNNING half of that — but only rows holding an expired lease, and it never
  touches QUEUED at all, deliberately (see its docstring).
* **Detection-only scans.** Epic 3's competitor detection leaves an open
  QUEUED scan for a later run to adopt. A free agency that ran detection on
  client A and then starts a scan on client B was counted for A's row and
  refused `SCAN_LIMIT_FREE_USED` without one scan ever having run.

Either way the one free scan was burned by a scan that produced nothing,
which breaks the rule the whole gate is built on. So the slot is taken when
the scan starts, handed back if it fails, and handed back anyway once the
window closes on a scan that never reported in.

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

`IN_FLIGHT_WINDOW` does not weaken it. Two requests racing for the last slot
are by definition milliseconds apart, so the loser counts the winner's row
while it is minutes inside the window.

THE FREE SCAN NEEDS A GOOGLE SIGN-IN (2026-09-29)
-------------------------------------------------
The free scan is per agency, and an agency costs nothing to make. Password
sign-up (`services/auth.sign_up_agency`) accepts any address, real or not,
and never verifies it. So the free tier was a farm: sign up, take one scan,
sign up again. At about $1.70 and 6 SerpApi searches a scan, some 41 fake
sign-ups spend SerpApi's month, and competitor detection then stops for
every customer at once.

So the free scan starts only for a requesting user with a linked Google
identity, `User.google_sub`. Anyone else gets a 402 with
`SCAN_LIMIT_VERIFY_REQUIRED`. It is the one verified identity fact this
system already holds, and the operator exemption below rests on the same
fact. It needs no new infrastructure: email verification would need Resend
configured and a confirmation flow, which is a later decision.

* **The requesting user, not the agency.** The person spending the free scan
  is the one who must be verified. A password-only teammate in a free agency
  whose owner is Google-linked is still refused.
* **Before the count is consulted, and under the same row lock.** A
  password-only user gets this code, not `SCAN_LIMIT_FREE_USED`, whatever the
  count says. The count is not a record of scans actually run: a
  detection-only QUEUED row holds a slot for its window, and a password-only
  agency that never scanned would otherwise be told its free scan was used.
  One consequence, accepted: where the free scan really is spent, a
  password-only user is asked to sign in with Google first, and hears that
  it is used only after doing so.
* **Paid agencies and exempt operators are untouched.** A payment is
  identity enough, so the forty a month applies to any seated user, password
  or Google. An exempt operator has a `google_sub` by definition.
* **Everything else stays open to a password-only user**: signing up,
  adding clients, competitor detection, and viewing, sharing and exporting
  reports. The way through already exists: Continue with Google on the same
  address links the account by its Google-verified email
  (`services/google_oauth.resolve_identity`).

**One Google account, one free scan** — and that is why this closes the
farm rather than raising its price. `google_sub` is UNIQUE and a user
belongs to exactly one agency, so a Google account is the verified identity
of one user in one agency. While it is linked it cannot start a second
agency's free scan, and the only thing that unlinks it — re-issuing that
seat's invitation (`services/invitations.invite_seat`) — leaves the row, and
its unique address, in the same agency. The farm's input used to be a typed
string, free and unlimited; it is now a Google account, which anyone can
make, but not without real friction, and the friction is the point. The one
route round the uniqueness runs through Google itself: unlink a seat, then
change that Google account's own address to a new one Google verifies. That
is an inbox per scan, which is the price email verification would have
charged anyway.

THE PLATFORM OPERATOR IS EXEMPT
-------------------------------
`Settings.scan_allowance_exempt_emails` names the sign-ins the gate does not
apply to, so the person who runs the platform can scan without paying the
platform. Two conditions, both required (2026-09-28):

1. the requesting USER's email is in the list, lower-cased; **and**
2. that user has a linked Google identity (`User.google_sub`).

The second condition is the point. An email on `User` is a string somebody
typed: password sign-up (`services/auth.sign_up_agency`) creates an ACTIVE
account with no proof the address is theirs — there is no verification step
and no `email_verified` column. Matching on the string alone meant that on
any database where the founder's row does not already exist — a new
environment, a restore, a deleted account — the first person to sign up by
password with that address would be handed unlimited scans at about $1.70
each. `google_sub` is set only by `services/google_oauth`, off a token
Google signed, so it is the one credential here that says an outside party
vouched for this address. A password-only account on a listed address is
gated like anyone else.

Per user, not per agency: a teammate seated in the same agency is gated like
anyone else. An exempt user's billing response reads `scan_limit: null`, the
same shape an ungated deployment has, so nothing on screen shows a count
that does not apply.

Configuration, not a hard-coded branch, and **the default is empty**. The
address used to be a code default; a personal address does not belong in
source, and an empty default means a new environment exempts nobody until
somebody decides otherwise. The live value is
`SCAN_ALLOWANCE_EXEMPT_EMAILS` in the Render API service environment.

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
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import ScanLimitReached
from ..models.scan import Scan, ScanStatus
from ..models.tenancy import Agency, User
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

# A scan that finished with something to show. Holds its slot for good.
SETTLED_STATUSES = (ScanStatus.SUCCEEDED, ScanStatus.PARTIAL)

# A scan that has not reported an outcome. Holds a slot only inside
# `IN_FLIGHT_WINDOW` — see below and the module docstring.
IN_FLIGHT_STATUSES = (ScanStatus.QUEUED, ScanStatus.RUNNING)

# Every status that can hold a slot at all. FAILED and CANCELLED are absent
# and that is the rule the gate is built on: a failure never burns the
# allowance.
COUNTED_STATUSES = IN_FLIGHT_STATUSES + SETTLED_STATUSES

# How long a QUEUED or RUNNING scan keeps its slot before it is treated as
# stranded and stops being counted.
#
# Fifteen minutes is three times the 300-second scan budget, the one duration
# this product commits to. Three times, not one, so an unusually slow but
# perfectly live scan is never written off while it is still working — and the
# margin is real rather than nominal, because the budget is currently MISSED:
# the best measured run is 361.3s (north-star.md), so the window is still
# ~2.5x the slowest scan actually observed.
#
# Bounded at all because a scan CAN sit in these states forever. The executor
# is a per-request `BackgroundTasks` job and Render restarts on every deploy,
# and `reap_stale_scans` covers only RUNNING rows holding an expired lease —
# never QUEUED, by design. Nothing else ever clears them.
#
# It does not weaken the race protection: two requests contending for the last
# slot arrive milliseconds apart, deep inside the window.
IN_FLIGHT_WINDOW = timedelta(minutes=15)

CODE_FREE_USED = "SCAN_LIMIT_FREE_USED"
CODE_MONTHLY = "SCAN_LIMIT_MONTHLY"
CODE_VERIFY_REQUIRED = "SCAN_LIMIT_VERIFY_REQUIRED"


def gate_enabled(settings: Settings) -> bool:
    """On when billing is configured; off — deliberately — when it is not."""
    return bool(settings.stripe_secret_key and settings.stripe_price_id)


def has_verified_identity(user: User | None) -> bool:
    """Whether an outside party has vouched for this sign-in: a linked Google identity.

    `User.google_sub` is written only by `services/google_oauth`, against a
    token Google signed, and it is UNIQUE. Nothing else on `User` proves
    anything — the email is a string somebody typed. The free scan and the
    operator exemption both rest on this and on nothing weaker.
    """
    return user is not None and user.google_sub is not None


def is_exempt(settings: Settings, user: User | None) -> bool:
    """Whether this sign-in is a platform operator the gate does not apply to.

    Listed address AND a linked Google identity, both. The email on a `User`
    is an unverified string — password sign-up never proves it — so on its own
    it would hand the exemption to whoever registered the address first on a
    fresh database. `google_sub` is written only against a token Google
    signed. See the module docstring.
    """
    if user is None or not user.email:
        return False
    if not has_verified_identity(user):
        return False
    return user.email.strip().lower() in settings.scan_allowance_exempt_emails


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

    `free_scan_requires_google` is true exactly when the gate is on, the
    agency is free, and the requesting user has no linked Google identity —
    the one case `assert_can_start` refuses with `SCAN_LIMIT_VERIFY_REQUIRED`.
    One field, read by that refusal and by the billing response alike, so
    the screen and the 402 cannot disagree about who needs to sign in.
    """

    scans_used: int
    scan_limit: int | None
    period_resets_at: datetime | None
    is_paid: bool
    free_scan_requires_google: bool

    @property
    def exhausted(self) -> bool:
        return self.scan_limit is not None and self.scans_used >= self.scan_limit


async def count_scans(
    db: AsyncSession,
    agency_id: str,
    *,
    now: datetime,
    since: datetime | None = None,
    exclude_scan_id: str | None = None,
) -> int:
    """How many scans hold a slot for this agency as of `now`.

    `since` bounds the period for a paid agency (the UTC calendar month);
    a free allowance is lifetime and passes None. `now` is not optional,
    because the in-flight half of the predicate is a statement about time
    and a default would let a caller silently measure it against the wrong
    clock — the tests and `allowance` both drive it explicitly.
    """
    holds_a_slot = or_(
        Scan.status.in_(SETTLED_STATUSES),
        and_(
            Scan.status.in_(IN_FLIGHT_STATUSES),
            Scan.created_at >= now - IN_FLIGHT_WINDOW,
        ),
    )
    stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.agency_id == agency_id, holds_a_slot)
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
    user: User,
) -> Allowance:
    """The agency's allowance as of `now`, for `user`. Pure read; no lock.

    `exclude_scan_id` is the open QUEUED scan a run is about to adopt rather
    than create (`get_or_create_scan` reuses it): it is the scan being started,
    not a scan already started, so it must not count against starting it.
    `user` is the requesting sign-in — the whole row, not its email, because
    the exemption and the free scan both ask whether Google vouched for it.
    Required since 2026-09-29: an allowance is always somebody's.
    """
    now = now or datetime.now(UTC)
    paid = billing.is_active(agency)
    if not gate_enabled(settings) or is_exempt(settings, user):
        used = await count_scans(db, agency.id, now=now, exclude_scan_id=exclude_scan_id)
        return Allowance(
            scans_used=used,
            scan_limit=None,
            period_resets_at=None,
            is_paid=paid,
            free_scan_requires_google=False,
        )
    if paid:
        start, next_start = period_bounds(now)
        used = await count_scans(
            db, agency.id, now=now, since=start, exclude_scan_id=exclude_scan_id
        )
        return Allowance(
            scans_used=used,
            scan_limit=PAID_SCANS_PER_MONTH,
            period_resets_at=next_start,
            is_paid=True,
            free_scan_requires_google=False,
        )
    used = await count_scans(db, agency.id, now=now, exclude_scan_id=exclude_scan_id)
    return Allowance(
        scans_used=used,
        scan_limit=FREE_SCAN_ALLOWANCE,
        period_resets_at=None,
        is_paid=False,
        free_scan_requires_google=not has_verified_identity(user),
    )


async def assert_can_start(
    db: AsyncSession,
    agency_id: str,
    settings: Settings,
    *,
    exclude_scan_id: str | None = None,
    now: datetime | None = None,
    user: User,
) -> Allowance:
    """Lock the agency row, count, and refuse with 402 if the allowance is spent.

    The lock is released by the caller's COMMIT (or rollback), so the caller
    must insert its scan and commit in the same transaction — that is the
    whole mechanism. Re-reads the agency under the lock rather than trusting
    the principal's copy, so a subscription that changed between the request
    starting and this point is seen.

    The free scan's Google requirement is refused first, before the count is
    consulted, and under this same lock — one lock path for every refusal.
    """
    agency = (
        await db.execute(select(Agency).where(Agency.id == agency_id).with_for_update())
    ).scalar_one()
    current = await allowance(
        db, agency, settings, now=now, exclude_scan_id=exclude_scan_id, user=user
    )
    if current.free_scan_requires_google:
        # Whatever the count says — see the module docstring for why a count
        # is not proof that this agency ever ran a scan.
        raise ScanLimitReached(
            detail=(
                "The free scan needs a Google sign-in, so that one person gets one. "
                "Continue with Google using this address, or subscribe."
            ),
            code=CODE_VERIFY_REQUIRED,
            scansUsed=current.scans_used,
            scanLimit=current.scan_limit,
            periodResetsAt=None,
        )
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
