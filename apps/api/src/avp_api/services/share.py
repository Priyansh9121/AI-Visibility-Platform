"""Public report sharing — Epic 9.8, the Epic 9 send path.

Epic 9's acceptance is that a pilot agency can "generate and **send** at least
one real prospect report." Every report surface before this one is
cookie-authenticated and agency-scoped, so there has been no way to put a
report in front of the person it is about. This module mints the capability
token that `GET /api/v1/reports/{token}` reads.

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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Scan
from ..security import new_share_token

logger = structlog.get_logger(__name__)

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
    """
    locked = (
        await session.execute(select(Scan).where(Scan.id == scan.id).with_for_update())
    ).scalar_one()

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
        await session.execute(select(Scan).where(Scan.id == scan.id).with_for_update())
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
    token, an empty token, a REVOKED token and an EXPIRED one all take this
    same path and all return None, so the caller has exactly one thing to say
    (404) and no branch that could leak which kind of wrong a guess was. There
    is deliberately no length or charset pre-check: validating the shape first
    would answer "was that even a plausible token?" faster than it answers
    "does it exist?", which is a timing oracle that makes enumeration cheaper.

    Revocation and expiry are NOT special cases of that rule — they are the
    same rule, which is why the predicate lives in this one query rather than
    in either route. A revoked token has no row to find at all; an expired one
    is filtered here. Neither can be told from a token that never existed.

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
            )
        )
    ).scalar_one_or_none()
