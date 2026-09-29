"""Issue a password-reset link by hand, for the operator — 2026-09-29.

Email is off in production (no verified sending domain yet), and the
forgot-password screen tells a password user to email the founder "and we'll
reset it by hand". This is the hand.

    cd /app/apps/api && python scripts/issue_password_reset.py --email person@example.com

(the Render shell on the API service; `docs/runbook.md` has the steps). Locally:

    DATABASE_URL=postgresql+asyncpg://avp@127.0.0.1:55433/avp_dev \\
        uv run python scripts/issue_password_reset.py --email person@example.com

ONE TOKEN PATH
--------------
The token is minted by `services/password_reset.request_reset` — the function
`POST /auth/reset-password/request` calls — so it has the same digest-only
storage, the same one-hour expiry, and the same retiring of any link issued
before it. The link comes from `password_reset.reset_url`, which the route's
email uses too. Nothing here writes a `PasswordResetToken` itself.

THE LINK GOES TO THE TERMINAL, AND NOWHERE ELSE
-----------------------------------------------
It is printed once, to this script's stdout, for the operator to pass on. It is
a live credential, so no log line carries it, nor the token, nor the address:
the service logs the event by user id only (`tests/test_issue_password_reset.py`
captures every log line a run writes and checks).

Unlike the public route, this one does say "no such user". Anti-enumeration
protects the public form from strangers; only someone with a shell on the API
service can run this.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from avp_api.config import Settings, get_settings  # noqa: E402
from avp_api.logs import configure_logging  # noqa: E402
from avp_api.models import User, UserStatus  # noqa: E402
from avp_api.services import password_reset as reset_service  # noqa: E402
from avp_api.services.auth import normalise_email  # noqa: E402

ISSUED = 0
NO_SUCH_USER = 1
REFUSED = 2


async def issue(
    session: AsyncSession,
    *,
    email: str,
    settings: Settings,
    out: TextIO,
    err: TextIO,
) -> int:
    """Mint a reset link for `email` and print it to `out`. Returns the exit code."""
    user = (
        await session.execute(
            select(User).where(User.email == normalise_email(email), User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()

    if user is None:
        print("No such user.", file=err)
        return NO_SUCH_USER
    if user.status is not UserStatus.ACTIVE:
        print(
            f"Refused: that account is {user.status.value}, not active, so no link was issued.",
            file=err,
        )
        return REFUSED
    if user.password_hash is None:
        # Active with no password means Google (the users table's check
        # constraint allows nothing else). A reset link would ADD a password
        # to an account that has never had one — not what was asked for.
        print(
            "Refused: this account signs in with Google and has no password to reset. "
            "They should use Sign in with Google.",
            file=err,
        )
        return REFUSED

    minted = await reset_service.request_reset(session, email=email, settings=settings)
    if minted is None:
        # Changed between the two reads; `request_reset` re-checks and wins.
        print("Refused: the account changed while this ran. Run it again.", file=err)
        return REFUSED
    await session.commit()

    _user, token = minted
    minutes = settings.password_reset_ttl_seconds // 60
    print(f"Reset link for {user.id} — it works once and expires in {minutes} minutes:", file=out)
    print(reset_service.reset_url(settings, token), file=out)
    return ISSUED


async def _run(email: str, settings: Settings) -> int:
    # Its own engine rather than the API's pooled one: one short session, then
    # nothing left open when the script exits.
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            return await issue(
                session, email=email, settings=settings, out=sys.stdout, err=sys.stderr
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a password-reset link for one user, to this terminal only."
    )
    parser.add_argument("--email", required=True, help="the account's email address")
    args = parser.parse_args(argv)

    settings = get_settings()
    # The API's own level and format, so this prints what the service would.
    configure_logging(settings)
    return asyncio.run(_run(args.email, settings))


if __name__ == "__main__":
    raise SystemExit(main())
