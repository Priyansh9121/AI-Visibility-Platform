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

SIGNED OUT EVERYWHERE FIRST — 2026-10-15
----------------------------------------
Before it mints, the script ends every session of the account
(`SessionStore.revoke_all_for_user`, the call a reset confirm makes), so someone
who has taken the account over is out when the owner's link exists. The order
matters: the sessions go first, so if Redis cannot be reached nothing is minted
and the script says so (exit 3). If the mint then fails, the account is signed
out and has no link: run the script again. An address with no account, a Google
account or an inactive one is refused before Redis is touched. The output says
how many session-list entries went and on which Redis host and db, never
which sessions and never the URL.

THE CREDENTIAL VERSION TOO — A3 part 3
--------------------------------------
After the Redis revoke and the mint, and last before the commit, the script sets the
account's `sessions_valid_after` (`services.auth.session_cutoff_value`), and the API
refuses every session signed in before it. So the sign-out no longer depends on the
Redis list the revoke reads (an evicted list, a session made while the script ran): a
session the list missed is a 401 at its next request. It is committed with the mint,
and also when the mint is refused (the sessions HAVE ended; the refusal says so). With
Redis unreachable the script has written nothing at all.

The user row is written AFTER the reset-link rows, never before: `confirm_reset` locks
the link row and then the user row, and a script that took the user row first could
deadlock against a link being redeemed (the security review's L2;
`services/password_reset.py` says why that order matters).

THE PASSWORD TOO — A5 part 3 (M2, option B), 2026-10-23
-------------------------------------------------------
Ending the sessions is not enough: whoever knows the old password could sign straight back
in while the owner waits for the link. So the script also REPLACES the password, with the
hash of a random secret (`hash_a_secret_nobody_knows`). The secret is made with `secrets`,
hashed by the app's own `hash_password_async` at the configured Argon2 parameters, and is
gone when that function returns: never printed, logged, written or kept. Nobody knows the
new password, the operator included; the owner chooses one with the link. That function is
the only one in the operator scripts that hashes, and `tests/test_password_policy_a4.py`
names it and pins how (a secret made inside it, never an argument or an input).

ONE TRANSACTION, in this order: (1) the refusals above; (2) the hash is made, before the
Redis revoke, because it is the one step after the refusals that can fail for want of a
hashing slot, and nothing has been changed yet; (3) the Redis revoke; (4) `request_reset`
(every old link retired, the new one inserted); (5) the user row is locked (`FOR NO KEY
UPDATE`), the cutoff instant is taken under that lock, and ONE UPDATE of the row carries the
new hash AND the cutoff; (6) the commit; (7) only then, the link or the refusal is printed,
so nothing is announced that did not commit.

THE INSTANT IS TAKEN UNDER THE ROW'S LOCK (the security review's L1). The script's only Redis
revoke is step 3, so what ends a session made after it is the cutoff, and a password sign-in
that checked the old hash can still be minting one. Its read-back is a `FOR SHARE` read of the
user row. Were the instant taken before the row is locked, that sign-in could mint after the
instant and read the row back before this UPDATE had it, see the old hash, and keep a session
newer than the cutoff. With the lock held first, a read-back that arrives later waits for the
commit and sees the new hash, and a session made earlier is older than the instant. The lock is
the one the UPDATE takes anyway, and it comes after the link rows, so the lock order and the
number of UPDATEs are unchanged.

The replacement cannot retire the link this script issues. A reset link is bound to its
user, digest, expiry and `used_at`, and `_redeemable` asks only whether the account has a
password at all, never which one; links are retired by statements of their own (a request,
a password change, a re-invite, a seat removal, a Google link), and a write to
`password_hash` is none of them. The write comes after the link rows for the lock order
above, not for the link's sake.

The hash is replaced only while the account still has one: the UPDATE tests `password_hash IS
NOT NULL` itself, and does not compare it with what was read. A password changed meanwhile is
replaced too, which is the point. A Google link that cleared it meanwhile is not undone: the
account stays without a password (checklist 3.119: a Google-only account stays Google-only,
and a reset never reaches an account with no password). The cutoff is written either way. In
that case the new link is dead, since `_redeemable` refuses an account with no password, so
the script does not print it: it refuses as it does for a Google account it finds at the
start (exit 2), and the sessions HAVE ended.

When the mint is refused (another reset being issued at that moment, or the account removed
meanwhile) the password is replaced there too, committed with the cutoff, and the refusal says
so.

Unlike the public route, this one does say "no such user". Anti-enumeration
protects the public form from strangers; only someone with a shell on the API
service can run this.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from redis.exceptions import RedisError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import case, select, update  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from avp_api.config import Settings, get_settings  # noqa: E402
from avp_api.logs import configure_logging  # noqa: E402
from avp_api.models import User, UserStatus  # noqa: E402
from avp_api.redis_client import close_redis, get_redis  # noqa: E402
from avp_api.security import hash_password_async  # noqa: E402
from avp_api.services import password_reset as reset_service  # noqa: E402
from avp_api.services.auth import normalise_email, session_cutoff_value  # noqa: E402
from avp_api.sessions import SessionStore  # noqa: E402

ISSUED = 0
NO_SUCH_USER = 1
REFUSED = 2
REDIS_UNREACHABLE = 3

# 256 bits, the size of a session token: the random secret whose hash replaces the password.
SECRET_BYTES = 32

# The same sentence for a Google account found at the start and for one that became so while
# the script ran.
GOOGLE_ONLY_REFUSAL = (
    "Refused: this account signs in with Google and has no password to reset. "
    "They should use Sign in with Google."
)

# The founder's line (2026-10-23, item j), verbatim: the password has been replaced on this
# branch too.
MINT_REFUSED = (
    "Refused: another reset was being issued for this account at that moment, "
    "or the account was removed. Its sessions have ended, its password has been replaced, "
    "and no link was issued. Run it again."
)


def redis_target(url: str) -> str:
    """Name the Redis host and db index of a URL: where the sessions were looked for.

    Never the URL itself, a username or a password, so an operator can see a
    wrong target without the line becoming a leak.
    """
    parts = urlsplit(url)
    path = parts.path.lstrip("/")
    return f"Redis {parts.hostname or 'unknown'} db {path if path.isdigit() else '0'}"


async def hash_a_secret_nobody_knows(settings: Settings) -> str:
    """The Argon2 hash of a random secret that exists only inside this call.

    Hashes a random secret nobody knows; sets no chosen password. It is the only function in the
    operator scripts that hashes, and `tests/test_password_policy_a4.py` names it and pins how:
    the secret is made here, by `secrets`, and used for nothing but the hash. It is not an
    argument, is read from nowhere, and is never printed, logged, written or returned; it is gone
    when this returns. The password rule and the breached-password check are about a password a
    person chose, and this is not one.
    """
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return await hash_password_async(secret, settings)


async def replace_password_and_set_cutoff(
    session: AsyncSession, *, user_id: str, new_hash: str
) -> bool:
    """The one write to the user row: the password's new hash and the sign-out cutoff, together.

    Returns whether the account has a password after the write. False means a Google link cleared
    it since the script began, and it stays cleared.

    THE ROW IS LOCKED FIRST AND THE INSTANT TAKEN AFTER (the security review's L1). The lock is
    `FOR NO KEY UPDATE`, the one the UPDATE below takes, taken earlier; the cutoff instant is
    taken only once it is held. Taken before, a password sign-in that had checked the old hash
    could make its session after the instant and read its row back (`FOR SHARE`) before this
    UPDATE had the row, see the old hash, and keep the session: the script's one Redis revoke is
    over by then, and Postgres lets a share lock in ahead of a writer queued for the row. Under
    the lock, a read-back that comes later waits for the commit and sees the new hash, and a
    session made earlier is older than the instant. The link rows are still written before the
    row (the order `confirm_reset` takes them in); the caller has done that.

    The hash is replaced only while the account still has one: the CASE tests `password_hash IS NOT
    NULL` in the database at the moment of the write, not "equal to the value read", so a password
    changed meanwhile is replaced too (the point), and a Google-only account never gets a
    password back (checklist 3.119). The cutoff is written either way, as
    `services.auth.set_session_cutoff` would write it: the same expression, in this one UPDATE.
    No commit here.
    """
    await session.execute(
        select(User.id).where(User.id == user_id).with_for_update(key_share=True)
    )
    cutoff = session_cutoff_value()  # the instant, under the lock
    has_password = (
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                password_hash=case(
                    (User.password_hash.is_not(None), new_hash), else_=User.password_hash
                ),
                sessions_valid_after=cutoff,
            )
            .returning(User.password_hash.is_not(None))
            .execution_options(synchronize_session=False)
        )
    ).scalar_one()
    return bool(has_password)


async def issue(
    session: AsyncSession,
    *,
    email: str,
    settings: Settings,
    store: SessionStore,
    out: TextIO,
    err: TextIO,
) -> int:
    """Replace the account's password, sign it out everywhere, mint a reset link and print it.

    The link goes to `out`. The module docstring has the order and the reasons.

    Returns the exit code.
    """
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
        print(GOOGLE_ONLY_REFUSAL, file=err)
        return REFUSED

    # The new password, made now: after the refusals, so a refused account is never hashed for, and
    # before the Redis revoke, which cannot be taken back. Hashing is the one step from here on that
    # can fail for want of a hashing slot, and if it does nothing at all has been changed.
    replacement = await hash_a_secret_nobody_knows(settings)

    # Sessions first, then the mint: with Redis down nothing is minted.
    try:
        ended = await store.revoke_all_for_user(user.id)
    except (RedisError, OSError):
        print(
            "Refused: Redis could not be reached, so the account may not have been "
            "signed out, and no link was issued. Run it again when Redis is up.",
            file=err,
        )
        return REDIS_UNREACHABLE
    print(
        f"Signed the account out everywhere: {ended} session list entry(ies) removed "
        f"on {redis_target(settings.redis_url)}.",
        file=out,
    )

    minted = await reset_service.request_reset(session, email=email, settings=settings)
    # The user row is locked and then written ONCE, after the reset-link rows (the order
    # `confirm_reset` takes them in; see the module docstring), with the new password's hash and
    # the credential version (A3 part 3, the cutoff a reset confirm sets too): any session the list
    # above did not hold is refused at its next request. The cutoff instant is taken under the
    # row's lock. Committed on every branch below, the refused mint included, since the sessions
    # have ended on each of them. The link or the refusal is printed only after the commit.
    has_password = await replace_password_and_set_cutoff(
        session, user_id=user.id, new_hash=replacement
    )
    await session.commit()
    if not has_password:
        # A Google link took the password away while the script ran. The link just minted is
        # dead (`_redeemable` refuses an account with no password), so it is not printed.
        print(GOOGLE_ONLY_REFUSAL, file=err)
        return REFUSED
    if minted is None:
        # `request_reset` re-reads in the same session without refreshing the row, so
        # this fires only if another reset was being issued for this account at that
        # moment (its advisory lock) or the account was removed meanwhile.
        print(MINT_REFUSED, file=err)
        return REFUSED

    _user, token = minted
    minutes = settings.password_reset_ttl_seconds // 60
    print(f"Reset link for {user.id} — it works once and expires in {minutes} minutes:", file=out)
    print(reset_service.reset_url(settings, token), file=out)
    return ISSUED


async def _run(email: str, settings: Settings) -> int:
    # Its own engine rather than the API's pooled one: one short session, then
    # nothing left open when the script exits.
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    redis = get_redis(settings)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            return await issue(
                session,
                email=email,
                settings=settings,
                store=SessionStore(redis, settings),
                out=sys.stdout,
                err=sys.stderr,
            )
    finally:
        await close_redis()
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
