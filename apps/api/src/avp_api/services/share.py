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

WHAT THIS DELIBERATELY DOES NOT DO, stated so it is not mistaken for finished:

* **No expiry.** A minted link works until the scan row is deleted.
* **No revocation.** There is no way to un-share a report.

Both are real gaps, accepted to hit a pilot conversation this week and NOT
acceptable as a permanent design: the first agency to send a link to the wrong
address has no way to take it back. Revocation is the cheaper of the two and
is nearly free — clear the column — but it needs a UI and a route, and neither
is in this slice. See build-log Epic 9.8 and north-star.md §5.4.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Scan
from ..security import new_share_token

logger = structlog.get_logger(__name__)


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
    """
    locked = (
        await session.execute(select(Scan).where(Scan.id == scan.id).with_for_update())
    ).scalar_one()

    if locked.share_token is not None:
        return locked.share_token

    token = new_share_token()
    locked.share_token = token
    await session.flush()
    # The token itself is never logged. It is a credential, and a log line
    # carrying it is a share link sitting in whatever ships the logs — the
    # same reason `sessions.py` logs a session's user rather than its cookie.
    logger.info("share.token.minted", scan_id=scan.id)
    return token


async def scan_for_share_token(session: AsyncSession, token: str) -> Scan | None:
    """The scan a public token points at, or None.

    **One lookup shape for every rejection.** A malformed token, an unknown
    token and an empty token all take this same path and all return None, so
    the caller has exactly one thing to say (404) and no branch that could
    leak which kind of wrong a guess was. There is deliberately no length or
    charset pre-check: validating the shape first would answer "was that even
    a plausible token?" faster than it answers "does it exist?", which is a
    timing oracle that makes enumeration cheaper.
    """
    return (
        await session.execute(select(Scan).where(Scan.share_token == token))
    ).scalar_one_or_none()
