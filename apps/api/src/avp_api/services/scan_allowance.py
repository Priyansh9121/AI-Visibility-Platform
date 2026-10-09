"""The scan allowance — one free scan, then forty a month on the plan (2026-09-28).

WHY A GATE EXISTS AT ALL
------------------------
Until this date nothing in the product was behind the plan, and the pricing
card said so. Stripe is wired to the plan's Price (`services/billing.py` holds the
amount); no rational agency pays for what it already gets free. And
a scan is not free to run: it pays for its engine calls (what they cost:
north-star §5.1), plus, on a
client's first scan, 6 of the **250 SerpApi searches** the Free tier allows per
month (up to 12 when searches are retried; a later scan carries the client's
competitor set forward and spends none) — about 41 new clients platform-wide,
about 20 if every search is retried, before every user's competitor detection
stops at once. An unmetered free tier is an open cost line with a shared fuse. So: the plan
gates STARTING a scan and, since 2026-10-04, pressing the competitor-detection
button (DETECTION FROM THE BUTTON, below), and nothing else.

THE ALLOWANCE
-------------
* **Free** (no active subscription): `FREE_SCAN_ALLOWANCE` completed scans per
  agency, lifetime, started only by a user with a linked Google identity
  (2026-09-29, below). Viewing reports, share links and PDFs stays free
  forever.
* **Paid** (`services/billing.is_active`: `active` or `trialing`):
  `PAID_SCANS_PER_MONTH` per agency per calendar month, UTC. **A free trial
  (2026-10-04) is paid for this purpose**: a `trialing` agency has the plan's
  whole forty, not a smaller one, and they are counted exactly as for `active`,
  by the calendar month, so a trial has forty in each calendar month it touches
  (up to three): a 30-day trial touches two, or three when it starts on 30 or 31
  January (or on 31 January in a leap year). Scans started before the trial in the
  same month count against that month's forty, as they would for an agency that
  had paid.
* A `canceled`, `past_due`, `unpaid` or `paused` subscription is free.
* Existing agencies are grandfathered nothing: completed scans already in the
  table count, so an agency that has scanned is at "free scan used". That is
  correct — it has had its free scan.

WHAT COUNTS AS A SCAN
---------------------
A scan that finished with something to show — SUCCEEDED or PARTIAL — counts
for good. A scan that ended FAILED or CANCELLED never counts: a failure must
never burn the free allowance. ONE failure is the exception (2026-10-09, founder): a scan
that ended FAILED with `PROMPTS_UNAVAILABLE_COUNTED` counts like a settled scan, for the
month it was submitted in and for the free scan. The executor writes that code instead of
`PROMPTS_UNAVAILABLE` when the agency already had `FREE_PROMPT_FAILURES_PER_DAY` such
failures in the rolling 24 hours before this one failed (`scan_executor._mark_failed`): a
failed prompt phase pays for the generator's call and, on a first scan, for competitor
detection, so it is free three times a day per agency and counts after that. The decision
is recorded on the row once; counting never recomputes it.

A scan still QUEUED or RUNNING counts **only while it is plausibly still
alive**. QUEUED: `coalesce(submitted_at, created_at)` within `IN_FLIGHT_WINDOW`
(2026-09-30; it was `created_at` alone). RUNNING: while its `lease_expires_at`
is in the future — a legitimate run can last `MAX_SCAN_DURATION` (40 minutes),
longer than the window, and the lease is what says it is alive — or, for an
unleased row, the same window as QUEUED. A RUNNING row whose lease has expired
holds nothing; the lease reaper fails it. The monthly period dates a scan by
the same `coalesce(submitted_at, created_at)`. `submitted_at` is set by
`routers/scans.py` when the scan is handed to an executor, in the transaction
that releases the allowance lock; detection never sets it, so a row nothing
has submitted is counted from its creation exactly as before. An in-flight scan has to
count for the window it is in flight, or the gate is a fiction: with only
finished scans counted, two requests at the cap would both see room and both
start. But counting it forever is the other failure, and it is the one that
was here until 2026-09-28. A scan can stay QUEUED or RUNNING and never
finish:

* **Lost executor.** The executor is a per-request `BackgroundTasks` job and
  Render restarts the process on every deploy. A scan queued seconds before a
  restart is never picked up. `scan_executor.reap_stale_scans` rescues the
  RUNNING half of that (an expired lease) and, since 2026-09-30, a QUEUED
  scan that was SUBMITTED more than `IN_FLIGHT_WINDOW` ago and never claimed
  (`EXECUTOR_NEVER_STARTED`). It still never touches a QUEUED row that was
  never submitted (see its docstring).
* **Detection-only scans.** Epic 3's competitor detection leaves an open
  QUEUED scan for a later run to adopt. A free agency that ran detection on
  client A and then starts a scan on client B was counted for A's row and
  refused `SCAN_LIMIT_FREE_USED` without one scan ever having run.

THE ADOPTION HOLE, CLOSED (2026-09-30). Adopting a detection row keeps its old
`created_at`, so counting by creation left an adopted scan uncounted while it
ran: at the cap, two quick starts on two detected clients both saw room and
both began. Counting from `submitted_at` closes it — the adopted scan holds its
slot from the moment it was submitted.

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
sign up again. At the engine cost of a scan and 6 SerpApi searches, some 41 fake
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
  adding clients, replacing a client's competitors by hand, and viewing,
  sharing and exporting reports. (The competitor-detection button is not on
  that list since 2026-10-04: it is for the plan, whoever presses it. See
  DETECTION FROM THE BUTTON below.) The way through already exists: Continue
  with Google on the same address links the account by its Google-verified
  email (`services/google_oauth.resolve_identity`).

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
password with that address would be handed unlimited scans at the engine
cost of each. `google_sub` is set only by `services/google_oauth`, off a token
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

DETECTION FROM THE BUTTON IS FOR THE PLAN (2026-10-04, launch checklist 3.61)
-----------------------------------------------------------------------------
`POST /clients/{clientId}/competitors/detect` runs a detection on its own: about six
SerpApi searches, out of the same 250 a month. 3.53 gave each client one run a day
(`rate_limit.claim_detection`), and that caps a client, not an agency: an account can add
clients without a classification call and detect once on each. So the route is for an
agency that is paid up and for the exempt operator, and `assert_may_run_detection` refuses
everyone else with `402 subscription-required` (the founder's decision). It is built from
the pieces above and adds no number of its own:

* **The scan gate's own switch.** With billing not configured `gate_enabled` is false and
  the route is open, for the reason above. It is that function, not a second reading of
  the two Stripe settings, so the two cannot disagree about what "configured" is.
* **Paid up** is `billing.is_active`: `active` or `trialing`. `past_due`, `unpaid`,
  `paused`, `incomplete`, `canceled` and a status nobody has decided about are refused.
  A payment is identity enough, so a paid agency's password-only user is let through.
* **The exemption is the scan's**, `is_exempt`: the listed address AND a linked Google
  identity, for that user and not for the agency's other seats.
* **One sentence for every case**, naming no status and no code, so a lapsed agency and one
  that never subscribed read the same words.
* **A scan's own detection is not this route.** `competitors.ensure_set_for_scan` runs
  inside a scan the allowance has already let start, whatever the plan, and detects for a
  client that has no competitor set to carry forward. It is neither refused here nor
  counted against the day.
* **No count, so no lock.** A subscription status is not a number to race: the check reads
  the agency `current_principal` loaded from Postgres at the start of the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..errors import ScanLimitReached, SubscriptionRequired
from ..models.scan import Scan, ScanStatus
from ..models.tenancy import Agency, User
from . import billing

logger = structlog.get_logger(__name__)

# One full scan and report, free, per agency, for the life of the agency. One
# is the number that lets a prospect judge the product on a business they know
# and is not a number worth building a second agency to get again.
FREE_SCAN_ALLOWANCE = 1

# Forty a month on the plan (founder decision; price in `services/billing.py`).
# The arithmetic of cost against the plan's price lives in north-star §5.1 and
# the build log (a scan is 20 prompts × 4 engines plus sentiment, measured
# 2026-09-12). SerpApi's Free tier is 250
# searches a month and detection spends 6 per NEW client, so forty scans of
# forty new clients would be 240 searches; forty re-scans of existing clients
# spend none. The cap is per calendar month, UTC, because Stripe's billing
# period and a calendar month differ by at most a few days and a month is what
# the card can say.
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

# The one FAILED ending that holds a slot: a prompt-phase failure past the daily free three.
# The code is the record of the decision; `count_scans` reads it and nothing recomputes it.
CODE_PROMPTS_UNAVAILABLE = "PROMPTS_UNAVAILABLE"
CODE_PROMPTS_UNAVAILABLE_COUNTED = "PROMPTS_UNAVAILABLE_COUNTED"

# How many prompt-phase failures an agency gets free within a rolling 24 hours (no timezone: the
# 24 hours before the failing scan's own failure time). Counted ones count toward the three too.
FREE_PROMPT_FAILURES_PER_DAY = 3
PROMPT_FAILURE_WINDOW = timedelta(hours=24)

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
# is a per-request `BackgroundTasks` job and Render restarts on every deploy.
# The window applies to QUEUED rows, and to RUNNING rows with no lease; a
# leased RUNNING row holds its slot for as long as the lease is live (a run may
# outlast the window), and one with an expired lease holds nothing. Failing
# those rows is `reap_stale_scans`' job (expired lease; submitted-then-never-
# claimed QUEUED). A QUEUED row nothing ever submitted (detection's) is never
# reaped; it just stops counting once the window closes.
#
# It does not weaken the race protection: two requests contending for the last
# slot arrive milliseconds apart, deep inside the window.
IN_FLIGHT_WINDOW = timedelta(minutes=15)

CODE_FREE_USED = "SCAN_LIMIT_FREE_USED"
CODE_MONTHLY = "SCAN_LIMIT_MONTHLY"
CODE_VERIFY_REQUIRED = "SCAN_LIMIT_VERIFY_REQUIRED"

# What `POST /clients/{clientId}/competitors/detect` says to an agency that is not paid up
# (launch checklist 3.61). One sentence for every case: it names no status and carries no
# code, because a lapsed agency and one that never subscribed are told the same thing. The
# second sentence is what `competitors.ensure_set_for_scan` does: a scan detects for a
# client that has no set yet, whatever the plan. It does not promise a scan is available:
# starting one is the allowance's own question and has its own answer.
DETECTION_NEEDS_PLAN = (
    "Running competitor detection on its own needs an active subscription. "
    "A scan still finds a client's competitors the first time it runs."
)


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
    # From SUBMISSION, falling back to creation for a row nothing has
    # submitted. An adopted detection row keeps its old `created_at`.
    dated = func.coalesce(Scan.submitted_at, Scan.created_at)
    holds_a_slot = or_(
        Scan.status.in_(SETTLED_STATUSES),
        # FAILED, but past the daily free three prompt-phase failures: it counts (see above).
        and_(
            Scan.status == ScanStatus.FAILED,
            Scan.error_code == CODE_PROMPTS_UNAVAILABLE_COUNTED,
        ),
        # QUEUED: nothing has claimed it, so only its age says it is alive.
        and_(Scan.status == ScanStatus.QUEUED, dated >= now - IN_FLIGHT_WINDOW),
        # RUNNING: alive while its lease is live, however long it has run (a
        # run may last MAX_SCAN_DURATION, longer than the window). An unleased
        # row (a script run) falls back to age. An expired lease is the
        # reaper's to fail and holds nothing.
        and_(
            Scan.status == ScanStatus.RUNNING,
            or_(
                Scan.lease_expires_at >= now,
                and_(Scan.lease_expires_at.is_(None), dated >= now - IN_FLIGHT_WINDOW),
            ),
        ),
    )
    stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.agency_id == agency_id, holds_a_slot)
    )
    if since is not None:
        # Dated by submission too: a detection row opened on the 30th and
        # scanned on the 1st belongs to the month it was scanned in.
        stmt = stmt.where(dated >= since)
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
                "Continue with Google using the same address you sign in with here, "
                "or subscribe."
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


def assert_may_run_detection(
    settings: Settings, *, agency: Agency, user: User, client_id: str
) -> None:
    """Let `POST /clients/{clientId}/competitors/detect` through, or refuse it with a 402.

    Allowed when any one holds: billing is not configured (`gate_enabled`, the scan gate's
    own switch); the agency is paid up (`billing.is_active`: `active` or `trialing`); the
    requesting user is the exempt operator (`is_exempt`: the listed address AND a linked
    Google identity, never the address alone). Anything else raises
    `SubscriptionRequired` with `DETECTION_NEEDS_PLAN`, and logs one `info` line,
    `competitors.detect.subscription_required`, with the two ids and nothing else.

    Called by the route after the client is found, so another agency's client is a `404`
    whatever this agency's plan, and before `rate_limit.claim_detection`, so a press the plan
    refuses spends none of the client's day. It touches no Redis key, opens no scan and makes
    no search. `client_id` goes to the log line and nowhere else.

    `agency` and `user` are the principal's: `current_principal` read both from Postgres at
    the start of the request, which is as fresh as this check needs. There is no count to
    race, so no row lock is taken (`assert_can_start` takes one because it counts).

    A scan's own detection (`competitors.ensure_set_for_scan`) never calls this: it runs
    inside a scan the allowance has already let start, and is not refused by the plan.
    """
    if not gate_enabled(settings) or billing.is_active(agency) or is_exempt(settings, user):
        return
    logger.info(
        "competitors.detect.subscription_required", client_id=client_id, agency_id=agency.id
    )
    raise SubscriptionRequired(detail=DETECTION_NEEDS_PLAN)
