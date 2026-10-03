"""Public report sharing — Epic 9.8, the Epic 9 send path.

Epic 9's acceptance is that a pilot agency can "generate and **send** at least
one real prospect report." Every report surface before this one is
cookie-authenticated and agency-scoped, so there has been no way to put a
report in front of the person it is about. This module mints the capability
token that `POST /api/v1/reports/shared` (and `/reports/shared/pdf`) reads
from its JSON body.

**The token IS the credential.** There is no second factor and no account
behind it; whoever holds the URL can read that one report. That is the point —
a prospect must not need an account — but it means the token has to be as hard
to guess as the session cookie it stands in for, which is why it comes from
`security.new_share_token` (256 bits, OS CSPRNG) rather than from the scan id,
a counter, or the clock.

EXPIRY AND REVOCATION — Epic 9.21
---------------------------------
Epic 9.8 shipped this module with neither, and said so rather than letting it
be mistaken for finished: "the first agency to send a link to the wrong address
has no way to take it back." Both now exist, and they answer different
questions.

* **Revocation** (`revoke_share_token`) is deliberate and immediate. It is for
  a link that went to the wrong address, and it is the only one of the two that
  a human ever invokes.
* **Expiry** (`SHARE_TTL`, stamped at mint) is automatic. It is for the link
  nobody remembers sending — the larger population, and the one no amount of
  UI will ever catch, because forgetting is the failure mode.

Neither reaches a copy already downloaded. A PDF on someone's disk cannot be
recalled by anything this module does, and `routers/report.py` says so on the
route that serves it.

A FAILED SCAN'S LINK — checklist 3.29
-------------------------------------
A failed scan cannot be shared (3.24), and a link minted before the scan failed
is refused like a withdrawn one: `scan_for_share_token` answers None for it, so
both public routes 404 with nothing to tell it from an unknown token. The token
itself stays on the row. It is the agency's to withdraw, and its end date is
still on the signed-in report so the screen can offer Withdraw.

WHAT THE DASHBOARD COUNTS — checklist 3.42
------------------------------------------
The dashboard's "Share a report" step counts the links that would open, and no
others: an expired link and a failed scan's are not links to anyone. It asks the
public lookup's own question. `link_would_open` is that question written once as
SQL, and both `scan_for_share_token` and `count_links_that_open` put it in their
WHERE clause, so the number on the dashboard and what a stranger holding the
address can open cannot drift apart.

THIS MODULE AND THE LEASE WRITE THE SAME ROW — checked 2026-09-08
-----------------------------------------------------------------
Both functions below take `SELECT ... FOR UPDATE` on a `Scan`. The lease
(`services/scan_executor.py`) writes `lease_expires_at` and `status` on that
same row from three places, one of them on a 30-second clock. Raised as a
lock-ordering question when branding shipped, and the premise turned out to be
true rather than hypothetical: **the share routes have no status guard, so a
RUNNING scan can be shared and revoked while its executor is still working.**
That is deliberate — an operator watching a scan run may send the link before
it finishes — so the two really do contend.

The overlap is benign, and ONE INVARIANT IS WHAT MAKES IT SO:

    every writer of the `scans` row takes exactly that one row, and holds it
    across a flush and a commit with no network call in between.

Both halves matter. One resource means a deadlock has no second lock to form a
cycle with, however the two interleave. No network call in the critical section
means the worst case is serialisation measured in milliseconds — nowhere near
`LEASE`, which is six heartbeat intervals precisely so a renewal can be delayed
and lose nothing. `scan_runner.run_scan` had to commit before its engine loop
to keep the second half true (build-log, "the row lock that every version of
its design was resting on"), which is the same invariant arrived at from the
other side.

**So the thing to preserve is the invariant, not a lock order.** If a future
share path ever needs to call out to something — an email send, an audit log,
a link shortener — do it after the commit, not inside the lock. The tests in
`test_share_link.py::TestSharingAndTheLeaseShareOneRow` run a share and a
renewal concurrently against one row and would not catch that on their own;
this comment is the part that has to be read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import FailedScanNotShareable
from ..models import Scan, ScanStatus
from ..security import new_share_token

logger = structlog.get_logger(__name__)

# What a refused mint says (checklist 3.24). One sentence in one place: the web shows the
# API's own detail as it comes, and the route's check before the lock and this module's
# check under it must not drift into two wordings.
FAILED_SCAN_NOT_SHAREABLE_DETAIL = "This scan failed, so its report cannot be shared."

# --- how long a share link lives ---------------------------------------------
# Thirty days, and there is no prior number in this product to anchor to — so
# the reasoning rather than the precedent:
#
# * **It must outlive the conversation it was sent for.** A prospect report is
#   read, forwarded to a colleague, and put in front of whoever holds the
#   budget. That runs days to a couple of weeks. A link dying mid-thread is the
#   agency's embarrassment, not the prospect's, so the floor is set by the
#   worst realistic sales cycle rather than the median one.
# * **It must not outlive the data.** The report is a snapshot of one scan.
#   A month on, a re-scan would show different numbers, and a link still
#   serving the old ones is quietly making a stale claim about a live business.
#   That is the ceiling, and it is close to the floor — which is why the range
#   worth arguing over is narrow.
# * **It is the blunt instrument, not the sharp one.** Revocation handles "sent
#   to the wrong person, now". Expiry only has to bound the forgotten link, so
#   it can afford to be generous where a security control alone would not.
#
# Thirty days sits where those meet, and it matches the month the rest of the
# product already thinks in (billing periods, per-agency call ceilings).
SHARE_TTL = timedelta(days=30)


def link_is_live(token: str | None, expires_at: datetime | None, now: datetime) -> bool:
    """Whether a share link is live at `now`: a token is set and its end is after `now`.

    **The one Python statement of a rule that is also written as SQL**, in
    `link_would_open` below (`share_token IS NOT NULL`, `share_expires_at IS NOT NULL`,
    `share_expires_at > now`, and a status test that this leaves out). The two have to
    agree to the microsecond, or a link would be live to one and over to the other:
    handed back by a press that the lookup refuses, or replaced by a press while the
    lookup still serves it. A query cannot call this, so the SQL carries a comment back
    to it instead, and `TestExpiry` in `test_share_link.py` stands on the edge of
    both. This is what `get_or_create_share_token` asks, and what
    `services/report.py` asks for the signed-in report's `shareExpiresAt`.

    **Strictly after.** A link whose end is exactly `now` is over.

    **A missing end is not "never expires".** A token with no end is not live, and
    neither is an end with no token. The CHECK on `scans` keeps both unwritable,
    but the read path treats them as dead, so this does too.

    **A scan's status is not part of it.** `link_would_open` adds that test itself
    (a FAILED scan's link is refused, 3.29), a press refuses a failed scan before it
    asks this, and the signed-in report still reports a failed scan's link so the
    screen can offer Withdraw.
    """
    return token is not None and expires_at is not None and expires_at > now


def link_would_open(now: datetime) -> ColumnElement[bool]:
    """SQL: this scan's share link would open at `now` — checklist 3.29 and 3.42.

    **The one place the rule is written as SQL, and two queries ask it.**
    `scan_for_share_token` (the public lookup, which adds `share_token == token`) and
    `count_links_that_open` (the dashboard's "Share a report" step) both put this in
    their WHERE clause. What the dashboard calls a live link and what a stranger
    holding the address can open are therefore one expression, and a change to when a
    link opens is a change here that both follow. Neither query restates a part of it.
    `test_dashboard_shared_count.py` pins that from two sides: a table of cases the two
    must answer alike, and a patched expression that both must read.

    **Never use this alone: it is half of a check.** It says a link would open, not whose
    link it is. It names no token and no agency, so by itself it matches every agency's
    live links at once. A lookup that used it without the token would read across
    tenants, and a count that used it without the agency would add up the whole product's
    links. The name reads like a complete test and is not one. Pair it with the scope that
    fits the caller: `Scan.share_token == token` (the public lookup, so only the holder of
    that address reaches that scan) or `Scan.agency_id == agency_id` (the dashboard's
    count, so only the caller's own agency is counted). A new caller adds its own scope the
    same way.

    A link would open when `link_is_live` says it is live (a token is set, an end is
    set, and the end is strictly after `now`) and the scan has not FAILED. The status
    test is the one thing `link_is_live` leaves out.

    **A CANCELLED scan's link opens, so it is counted.** Nothing writes CANCELLED today,
    but the lookup serves a cancelled scan's link and so does this (it refuses only
    FAILED). Leaving a cancelled scan out of the count alone would have the dashboard
    call dead a link a stranger can open. If cancelled scans' links should stop
    opening, that is a change to this expression, and the lookup and the count both
    follow it.

    `now` is passed in, not read here, so each caller states its own clock.
    """
    return and_(
        # `link_is_live`, written as SQL: a token is set, its end is set, and the end is
        # after `now`. Change one and change the other. (In the lookup, `share_token ==
        # token` already implies the first; the count has no other test for it.)
        Scan.share_token.is_not(None),
        Scan.share_expires_at.is_not(None),
        Scan.share_expires_at > now,
        # The one thing `link_is_live` leaves out: a failed scan's link is refused.
        Scan.status != ScanStatus.FAILED,
    )


async def count_links_that_open(
    session: AsyncSession, agency_id: str, *, now: datetime | None = None
) -> int:
    """How many of this agency's scans have a share link that would open at `now`.

    This is the dashboard's `sharedScanCount`, behind its "Share a report" step. A scan
    has at most one link at a time, so it counts scans and links alike. A withdrawn
    link, an expired one and a failed scan's are not counted: none of them opens for
    anyone. The test is `link_would_open`, the public lookup's own, and not a column
    that merely says a token was once set.

    **Scoped to the agency.** Another agency's links are never in this number.

    **The count describes the account as it is, not a history.** Minting a link adds
    to it. Withdrawing one takes it off, and so does a link reaching its end or its scan
    failing, because then the link opens nothing. That is why an agency's last link
    running out un-does the step, as withdrawing it does.

    One count in one query, filtered by the agency and by `link_would_open`: the shape
    it had when it asked only for a token (the agency, and a token set), with the end
    and the status as two more tests on the rows it already reads. No new index, and
    no schema change. `now` is the clock, and only a test passes it.
    """
    now = now or datetime.now(UTC)
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Scan)
                .where(Scan.agency_id == agency_id, link_would_open(now))
            )
        ).scalar_one()
    )


async def get_or_create_share_token(
    session: AsyncSession, scan: Scan, *, now: datetime | None = None
) -> tuple[str, datetime]:
    """Return this scan's live share link as `(token, expires_at)`, making one only if none is live.

    **A live link is returned exactly as it is** (checklist 3.28, the founder's
    decision). Live is `link_is_live`: a token is set and its end is still in the
    future. A press on one returns that token and that end and writes nothing: no new
    token and no new end. The share bar tells an agency that links last 30 days
    from when they are created, and "The link works until ..." beside it names the
    end the link was given. A press that moved the end would make both untrue of
    the link already sent, and it would do so silently, because "Get shareable
    link" is also how a signed-in report gets the URL back (the report says when
    a link ends, never what it is). Epic 9.21 did move it, to 30 days from the
    press, and revived an expired token while it was there; neither happens now.

    **Only when no link is live is one made, and it is a new one.** That is a
    scan never shared, a withdrawn link, or a link that has ended. The mint is a
    fresh token with a fresh 30 days (`SHARE_TTL`). An ended token is replaced,
    not revived: it is overwritten on the row, so the address that stopped
    working stays dead on both public routes, as a withdrawn one does. An agency
    that wants a link to run past its end gets a new one and sends the new
    address.

    **Idempotent for a live link.** Asking twice returns the same link, so an
    operator who clicks "Get shareable link" again gets the URL they already
    sent rather than a second live link to the same report. Every extra token is
    a URL nobody is tracking, so a click does not mint one while one is live.

    **Serialised with `FOR UPDATE`, and that is load-bearing rather than
    defensive.** The unique index cannot arbitrate this race: two concurrent
    calls would generate two *different* tokens and issue two UPDATEs against
    the *same row*, so there is no unique violation to catch — the second
    write simply wins and the first caller walks away holding a URL that
    404s. Locking the row makes the loser read the winner's token instead.

    **A FAILED scan is refused here too, under the lock** (checklist 3.29,
    security review). `create_share_link` reads the scan and refuses a failed
    one before it calls this, but that read is not under this lock: a scan the
    reaper or a deadline failed in between used to get a link from a request
    that had seen it healthy, or be handed a live one that no longer opens
    anything. So the status is read again from the locked row, not from the
    `scan` handed in, which may be exactly as stale as that route's read, and
    the route's own 409 is raised before anything is written. The request's
    session rolls back on the way out, which releases the lock.

    **`now` is the clock, and only a test passes it** (as `reap_stale_scans`'s is),
    so that the edge of "live" can be stood on exactly.
    """
    locked = (
        await session.execute(
            select(Scan)
            .where(Scan.id == scan.id)
            .with_for_update()
            # Overwrite the identity-mapped copy loaded before the lock: without
            # this the locked read can return a stale `share_token`.
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    # Nothing has been written yet, so refusing here leaves the row as it was.
    if locked.status is ScanStatus.FAILED:
        raise FailedScanNotShareable(detail=FAILED_SCAN_NOT_SHAREABLE_DETAIL)

    now = now or datetime.now(UTC)
    # A live link: returned as it is. Nothing is flushed, so the caller's commit
    # only ends the transaction and releases the lock.
    if link_is_live(locked.share_token, locked.share_expires_at, now):
        # `link_is_live` has just established both are set; this tells the type checker.
        assert locked.share_token is not None and locked.share_expires_at is not None
        return locked.share_token, locked.share_expires_at

    # No link, or one that has ended. A new token goes over any old one, so an
    # ended address is not found again by `scan_for_share_token` and stays dead.
    token = new_share_token()
    expires_at = now + SHARE_TTL
    locked.share_token = token
    locked.share_expires_at = expires_at
    await session.flush()
    # The token itself is never logged. It is a credential, and a log line
    # carrying it is a share link sitting in whatever ships the logs — the
    # same reason `sessions.py` logs a session's user rather than its cookie.
    logger.info("share.token.minted", scan_id=scan.id)
    return token, expires_at


async def revoke_share_token(session: AsyncSession, scan: Scan) -> bool:
    """Un-share a report. True if a live link was actually taken down.

    **Clears both columns**, which the CHECK constraint on `scans` requires and
    which is also the honest state: a token with no expiry is not a link, and
    an expiry with no token is a date about nothing.

    **Idempotent, and it reports rather than complains.** Revoking a scan that
    was never shared is not an error — the caller's intent ("this must not be
    readable") is already satisfied, and a 404 or a 409 would only tell them
    something they did not ask about. The return value is there so a log line
    can say whether anything changed, not so a route can branch into an error.

    Locked with `FOR UPDATE` for the same reason the mint is: a revoke racing a
    mint on one row has to be serialised with it, so that whichever comes second
    acts on the link the first one left, not on a copy it read earlier. A revoke
    working from a stale "no token" would report that there was nothing to take
    down while the link another transaction had just made stayed live
    (`test_share_lock_refresh.py` is that case).

    **What this cannot reach:** a PDF already downloaded. Revocation takes the
    LINK down; it does not recall a file. `routers/report.py` states that on
    the route that serves one.
    """
    locked = (
        await session.execute(
            select(Scan)
            .where(Scan.id == scan.id)
            .with_for_update()
            # Overwrite the identity-mapped copy loaded before the lock: without
            # this the locked read can return a stale `share_token`.
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    if locked.share_token is None:
        return False

    locked.share_token = None
    locked.share_expires_at = None
    await session.flush()
    logger.info("share.token.revoked", scan_id=scan.id)
    return True


async def scan_for_share_token(
    session: AsyncSession, token: str, *, now: datetime | None = None
) -> Scan | None:
    """The scan a public token points at, or None.

    **One lookup shape for every rejection.** A malformed token, an unknown
    token, an empty token, a REVOKED token, an EXPIRED one and one whose scan
    FAILED all take this same path and all return None, so the caller has
    exactly one thing to say (404) and no branch that could leak which kind of
    wrong a guess was. There is no shape check in this function: the POST
    body's `token` is bounded 1..512 characters by Pydantic before it gets
    here, and anything outside that gets a 422 identical for a real token and a
    made-up one, so the bound reveals nothing about which tokens exist. Within
    the bound, no charset or length test runs ahead of the lookup, which would
    answer "was that even a plausible token?" faster than "does it exist?" (a
    timing oracle).

    Revocation, expiry and a failed scan are NOT special cases of that rule —
    they are the same rule, which is why the predicate lives in this one query
    (through `link_would_open`) rather than in either route. A revoked token has
    no row to find at all; an expired one and a failed scan's are found by the
    token's index and dropped by the rest of the predicate. All of them get the
    answer a token that never existed gets.

    **A FAILED scan's link is dead, and the token is kept** (checklist 3.29,
    the founder's decision). A failed scan's score is never shown (3.24), so
    there is no report worth sending, and a link minted while the scan was still
    running used to keep serving it without a score. It is refused now, as a
    withdrawn one is. The token is NOT cleared: the agency's own report still
    says when it ends (`shareExpiresAt`), so "Withdraw link" stays reachable,
    and withdrawing is what removes it. The status test belongs in this WHERE
    clause for the reason the expiry test does: the refusal costs what an
    expired token costs, one indexed lookup that finds the row and drops it on
    the rest of the predicate, with no second query. That is a row fetch more
    than a token nobody was ever given costs, and this does not claim
    otherwise; what is the same for every kind of miss is the answer. A check
    made after the lookup would be a further step, taken only for a token that
    exists and whose scan failed, and a second place for a route to disagree
    with the other.

    **A missing expiry is not "never expires".** The comparison in
    `link_would_open` is strict, so NULL never matches, and a token that somehow
    reached the table without one serves nothing. Fail-closed, for a credential.

    **Everything but the token's match is `link_would_open`,** which the dashboard's
    count of live links asks as well (3.42), so what a stranger can open and what the
    dashboard counts are one expression. Its "live" half also has a second statement in
    Python, `link_is_live`, which a press asks (and the signed-in report's
    `shareExpiresAt`); the edge test in `test_share_link.py::TestExpiry` keeps those two
    in step. `now` is the clock, and only a test passes it.
    """
    now = now or datetime.now(UTC)
    return (
        await session.execute(
            select(Scan).where(
                Scan.share_token == token,
                # The end, and a failed scan: the rule that decides whether a link
                # opens, shared with the dashboard's count and written once.
                link_would_open(now),
            )
        )
    ).scalar_one_or_none()
