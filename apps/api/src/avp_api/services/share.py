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
from sqlalchemy import select
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


async def get_or_create_share_token(session: AsyncSession, scan: Scan) -> str:
    """Return this scan's public share token, minting one if it has none.

    **Idempotent.** Asking twice returns the same token, so an operator who
    clicks "Get shareable link" again gets the URL they already sent rather
    than a second live link to the same report. Every token minted is a URL
    that works forever (there is no revocation), so minting one per click
    would quietly accumulate live links nobody is tracking.

    **Serialised with `FOR UPDATE`, and that is load-bearing rather than
    defensive.** The unique index cannot arbitrate this race: two concurrent
    calls would generate two *different* tokens and issue two UPDATEs against
    the *same row*, so there is no unique violation to catch — the second
    write simply wins and the first caller walks away holding a URL that
    404s. Locking the row makes the loser read the winner's token instead.

    **A re-mint refreshes the expiry, and idempotence is about the TOKEN.**
    Two readings were available and this one is forced by a functional gap in
    the other: without a refresh there is no way to extend a link at all
    without changing its URL, because the only alternative is revoke-and-mint,
    which issues a NEW token and kills every copy already sent. So an agency
    re-sharing on day 29 would have to break the link they are re-sending. The
    second call still returns the same token and still creates nothing — what
    it does is restate the decision to share, which is exactly the signal an
    expiry should listen to.

    **A FAILED scan is refused here too, under the lock** (checklist 3.29,
    security review). `create_share_link` reads the scan and refuses a failed
    one before it calls this, but that read is not under this lock: a scan the
    reaper or a deadline failed in between used to get a link, or have a live
    link's end pushed out, from a request that had seen it healthy. So the
    status is read again from the locked row, not from the `scan` handed in,
    which may be exactly as stale as that route's read, and the route's own 409
    is raised before anything is written. The request's session rolls back on
    the way out, which releases the lock.
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

    # Nothing has been written yet, so refusing here leaves the row as it was: no
    # token minted, no live link's expiry moved.
    if locked.status is ScanStatus.FAILED:
        raise FailedScanNotShareable(detail=FAILED_SCAN_NOT_SHAREABLE_DETAIL)

    expires_at = datetime.now(UTC) + SHARE_TTL
    if locked.share_token is not None:
        locked.share_expires_at = expires_at
        await session.flush()
        logger.info("share.token.refreshed", scan_id=scan.id)
        return locked.share_token, expires_at

    token = new_share_token()
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
    re-mint on one row must not interleave into "token cleared, expiry
    refreshed", which is precisely the half-set state the CHECK forbids.

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


async def scan_for_share_token(session: AsyncSession, token: str) -> Scan | None:
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
    rather than in either route. A revoked token has no row to find at all; an
    expired one and a failed scan's are found by the token's index and dropped
    by the rest of the predicate. All of them get the answer a token that never
    existed gets.

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

    **A missing expiry is not "never expires".** The comparison below is
    strict, so NULL never matches, and a token that somehow reached the table
    without one serves nothing. Fail-closed, for a credential.
    """
    return (
        await session.execute(
            select(Scan).where(
                Scan.share_token == token,
                Scan.share_expires_at.is_not(None),
                Scan.share_expires_at > datetime.now(UTC),
                Scan.status != ScanStatus.FAILED,
            )
        )
    ).scalar_one_or_none()
