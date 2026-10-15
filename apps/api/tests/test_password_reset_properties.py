"""Password reset, proven property by property (hardening item A5).

The reset token has at least 128 bits of entropy, is stored hashed, is single use, expires, is
bound to the user it was issued to, and is retired by a newer one. Redeeming it ends every
session of that user and signs nobody in. The link carries it to nobody but the inbox.

Every test below works on a token the ROUTE issued: the link is read where the route hands it to
the email sender, and the token is what follows the `#`. The older reset tests write a row by
hand, which proves what the confirm step does with a row and not that the token a request issues
is the one that redeems.

A property an older test already proves exactly is not repeated here. They are listed so that all
of them can be found in one place:

- single use, one confirm after another:
  test_password_reset.py::TestConfirm::test_a_token_works_exactly_once
- the link holds the token only after the `#` (every builder, and `reset_url`):
  test_token_links.py::test_every_builder_puts_the_token_after_a_hash
  test_token_links.py::test_reset_url_uses_the_fragment_form
  and the emailed body, end to end:
  test_secrets_stay_out_of_logs.py::TestNothingSecretIsLogged::test_with_email_configured
- the reset REQUEST writes no log line with the token, email on and off (a sentinel token):
  test_secrets_stay_out_of_logs.py::TestNothingSecretIsLogged
- the web sends no Referer from the legacy token route and from the fragment route:
  apps/web/src/app/referrerPolicy.test.ts ("/reset-password/:token does",
  "the fragment route /reset-password does, and exists on disk"), and that the rule is not
  overridden by the site-wide one: apps/web/src/app/securityHeaders.test.ts

Two confirms of one token at once are tested here as well as in
test_session_races.py::test_two_concurrent_confirms_of_one_link_one_wins. That older test used to
pass with the row lock taken out of `confirm_reset`: run cold, the two requests answered 200 and
400 without the lock, and with the connection pool warmed first (four connections opened and given
back) the same race answered 200 and 200. It now warms the pool. The test here warms it too, and
also holds both requests between their read of the token and their write with a slow hash.

The confirm step waits for its hash outside the row lock and without a database connection
(launch checklist 3.129): it looks at the link without a lock, ends that transaction, hashes, then
locks the row and decides again. Section 3b parks a request in the hash and looks at the database
from outside while it waits, changes the link or the user under it, and lets two requests race for
the lock. Section 3c is the claim that keeps N requests with one link to one hash: it is taken in
Redis before the link is read, released however the request ends, and ends by itself if its holder
dies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from avp_api import ids, security
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.errors import ServiceUnavailable
from avp_api.models import Base, PasswordResetToken, User, UserRole, UserStatus
from avp_api.security import hash_password, token_digest
from avp_api.services import email as email_service
from avp_api.services import password_reset as reset_service
from avp_api.services import rate_limit
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OTHER_PASSWORD = "a-third-and-quite-different-passphrase"
OWNER = "owner@reset-properties.example"
MEMBER = "member@reset-properties.example"
INVALID_TOKEN = "/problems/invalid-reset-token"
# The two sentences a refused link can get (A5 part 2): the founder's, for a link already used or
# being redeemed right now, and the older one for a link that is unknown, expired or orphaned.
USED = (
    "This link has already been used. If you just set a new password, sign in with it. "
    "Otherwise, request a new link."
)
NOT_VALID = "That reset link is not valid. Links work once and expire after an hour."


# --- what the route sends, and small calls through the real routes ----------------------------


class Mailbox:
    """What the reset route handed to the email sender: the recipient and the link."""

    def __init__(self, settings: Settings) -> None:
        self.sent: list[tuple[str, str]] = []
        # The page that redeems a token, up to and including the `#`.
        self.prefix = reset_service.reset_url(settings, "")

    def token_in(self, link: str) -> str:
        """The token a link carries: everything after the `#`, on the reset page's address."""
        page, hash_mark, token = link.partition("#")
        assert hash_mark and page + hash_mark == self.prefix, "the token is not after a `#`"
        return token


@pytest.fixture
def mailbox(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Mailbox:
    """Record each link the route sends. Email is switched on, and the spy REPLACES the sender.

    The route mints and sends only when email is configured (hardening A1: with it off, a request
    writes nothing), so these tests configure a key. The spy does not call the real sender, so no
    request leaves the process.
    """
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    box = Mailbox(settings)

    async def spy(to: str, reset_url: str, *, user_id: str, settings: Settings) -> None:
        box.sent.append((to, reset_url))

    monkeypatch.setattr(email_service, "send_password_reset", spy)
    return box


async def _sign_up(client: AsyncClient, email: str) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Reset Properties Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str) -> Response:
    return await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


async def _ask(client: AsyncClient, mailbox: Mailbox, email: str) -> str:
    """Ask for a reset link through the route. The token the emailed link carries."""
    before = len(mailbox.sent)
    resp = await client.post(f"{BASE}/auth/reset-password/request", json={"email": email})
    assert resp.status_code == 200, resp.text
    assert len(mailbox.sent) == before + 1, "the route did not send a link"
    recipient, link = mailbox.sent[-1]
    assert recipient == email
    return mailbox.token_in(link)


async def _confirm(client: AsyncClient, token: str, password: str = NEW_PASSWORD) -> Response:
    return await client.post(
        f"{BASE}/auth/reset-password/confirm", json={"token": token, "newPassword": password}
    )


def _cookie(client: AsyncClient, settings: Settings) -> str:
    """The session cookie the client holds right now."""
    value = client.cookies.get(settings.session_cookie_key)
    assert value, "the client holds no session cookie"
    return value


async def _me(client: AsyncClient, settings: Settings, cookie: str) -> Response:
    """`/auth/me` presenting exactly this session cookie and no other."""
    client.cookies.clear()
    return await client.get(
        f"{BASE}/auth/me", headers={"Cookie": f"{settings.session_cookie_key}={cookie}"}
    )


# --- the database -------------------------------------------------------------------------------


def _db(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _row_for(engine: AsyncEngine, token: str) -> PasswordResetToken:
    async with _db(engine)() as s:
        return (
            await s.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_digest == token_digest(token)
                )
            )
        ).scalar_one()


async def _set_expiry(engine: AsyncEngine, token: str, when: datetime) -> None:
    async with _db(engine)() as s:
        await s.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.token_digest == token_digest(token))
            .values(expires_at=when)
        )
        await s.commit()


async def _every_cell(engine: AsyncEngine) -> list[tuple[str, str]]:
    """Every value in every table, as text, with the `table.column` it sits in."""
    cells: list[tuple[str, str]] = []
    async with engine.connect() as conn:
        for table in Base.metadata.tables:
            result = await conn.execute(text(f'SELECT * FROM "{table}"'))
            names = list(result.keys())
            for row in result.fetchall():
                cells.extend(
                    (f"{table}.{name}", str(value)) for name, value in zip(names, row, strict=True)
                )
    return cells


async def _add_member(
    engine: AsyncEngine, settings: Settings, agency_id: str, email: str
) -> None:
    """A second seat in the same agency, with a password of its own."""
    async with _db(engine)() as s:
        s.add(
            User(
                id=ids.new_id(ids.USER),
                agency_id=agency_id,
                email=email,
                full_name="Member",
                role=UserRole.MEMBER,
                password_hash=hash_password(PASSWORD, settings),
            )
        )
        await s.commit()


async def _add_token(engine: AsyncEngine, user_id: str, raw: str) -> None:
    """A live reset link written by hand, as a request still in flight would have committed it."""
    async with _db(engine)() as s:
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user_id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()


async def _warm_the_pool(engine: AsyncEngine) -> None:
    """Open four connections at once and give them back, so a race starts with idle connections.

    Run cold, the second of two requests waits to open a connection of its own and the first is
    done by then: a test of a race passed with the lock it tests taken out.
    """

    async def hold_a_connection() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_sleep(0.3)"))

    await asyncio.gather(*(hold_a_connection() for _ in range(4)))


# --- 1. entropy ---------------------------------------------------------------------------------


async def test_reset_token_has_at_least_128_bits(client: AsyncClient, mailbox: Mailbox) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)

    # URL-safe base64 and nothing else, so the decode below counts every character.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token), "the token is not URL-safe base64"
    decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(decoded) >= 16, f"the token decodes to {len(decoded) * 8} bits"


async def test_reset_token_is_drawn_from_secrets_with_at_least_16_bytes(
    client: AsyncClient, mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sign_up(client, OWNER)

    # A long token says nothing about where it came from: a weak generator can fill 43 characters.
    # So the call to the CSPRNG is watched. The emailed token must be exactly what
    # `secrets.token_urlsafe` returned, and it must have been asked for at least 16 bytes.
    real = secrets.token_urlsafe
    drawn: list[tuple[int, str]] = []

    def spy(nbytes: int | None = None) -> str:
        value = real(nbytes)
        drawn.append((secrets.DEFAULT_ENTROPY if nbytes is None else nbytes, value))
        return value

    monkeypatch.setattr(secrets, "token_urlsafe", spy)
    token = await _ask(client, mailbox, OWNER)

    sizes = [nbytes for nbytes, value in drawn if value == token]
    assert len(sizes) == 1, "the emailed token is not what secrets.token_urlsafe returned"
    assert sizes[0] >= 16, f"the token was drawn with {sizes[0]} bytes"


async def test_two_issued_reset_tokens_differ(client: AsyncClient, mailbox: Mailbox) -> None:
    await _sign_up(client, OWNER)
    first = await _ask(client, mailbox, OWNER)
    second = await _ask(client, mailbox, OWNER)
    assert first != second


# --- 2. stored hashed ---------------------------------------------------------------------------


async def test_reset_token_is_stored_only_as_its_digest(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)

    async with _db(engine)() as s:
        rows = list((await s.execute(select(PasswordResetToken))).scalars())
    assert len(rows) == 1
    assert rows[0].user_id == user_id
    assert rows[0].token_digest == token_digest(token)
    assert rows[0].token_digest != token

    # No column of any row in any table holds the token. The scan reaches the row that holds the
    # digest, so finding nothing means something.
    cells = await _every_cell(engine)
    assert ("password_reset_tokens.token_digest", token_digest(token)) in cells
    holders = [where for where, value in cells if token in value]
    assert holders == [], f"the token itself is stored in {holders}"


# --- 3. single use (the second confirm: test_password_reset.py; both at once: here) -----------


async def test_two_concurrent_confirms_of_one_token_give_exactly_one_success(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    # Both requests get a connection at once, so the second one's read lands inside the window the
    # slow hash opens and does not wait on a cold connect.
    await _warm_the_pool(engine)

    # A slow hash holds the winner, and the claim (section 3c) refuses the other request while it
    # waits: this is the end-to-end "exactly one of two succeeds", not a proof of the row lock. The
    # last test of section 3b proves the lock, with the claim out of the way.
    real_hash = security.hash_password

    def slow_hash(password: str, settings: Settings | None = None) -> str:
        threading.Event().wait(0.3)
        return real_hash(password, settings)

    monkeypatch.setattr(security, "hash_password", slow_hash)
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def confirm(password: str) -> Response:
        # Its own client: its own connection and no shared cookie jar.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await _confirm(other, token, password)

    first, second = await asyncio.gather(confirm(NEW_PASSWORD), confirm(OTHER_PASSWORD))
    assert sorted([first.status_code, second.status_code]) == [200, 400], (first.text, second.text)
    if first.status_code == 200:
        winner, loser, refused = NEW_PASSWORD, OTHER_PASSWORD, second
    else:
        winner, loser, refused = OTHER_PASSWORD, NEW_PASSWORD, first
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED

    # One password change took effect: the winner's. The loser's did not, nor did the old one stay.
    assert (await _login(client, OWNER, winner)).status_code == 200
    assert (await _login(client, OWNER, loser)).status_code == 401
    assert (await _login(client, OWNER, PASSWORD)).status_code == 401


# --- 3b. the hash waits outside the row lock and holds no connection (checklist 3.129) -----------
#
# The confirm waits for a hashing slot for up to ARGON2_QUEUE_TIMEOUT_SECONDS, and used to wait
# with the token row locked and a pooled connection checked out. These tests park a request in the
# hash (the fake below stands in for `hash_password_async` in the service) and look at the database
# from outside while it waits.

LOCK_NOT_AVAILABLE = "55P03"  # what Postgres answers when `FOR UPDATE NOWAIT` finds the row held


class HashGate:
    """`hash_password_async` for one service module, parking every caller until it is let go.

    A request parked here is where `confirm_reset` used to wait for a hashing slot. Once let go,
    the real hash runs, so what the request goes on to do is the real thing.
    """

    def __init__(self) -> None:
        self.parked = 0
        self._go = asyncio.Event()

    async def __call__(self, password: str, settings: Settings | None = None) -> str:
        self.parked += 1
        await self._go.wait()
        return await security.hash_password_async(password, settings)

    async def until_parked(self, count: int = 1) -> None:
        async with asyncio.timeout(10):
            while self.parked < count:
                await asyncio.sleep(0.01)

    def let_go(self) -> None:
        self._go.set()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[HashGate]:
    parked = HashGate()
    monkeypatch.setattr(reset_service, "hash_password_async", parked)
    yield parked
    parked.let_go()  # a test that failed before it did leaves nothing waiting


async def _row_is_free(engine: AsyncEngine, table: str, column: str, value: str) -> bool:
    """Can another connection lock this row at once? `FOR UPDATE NOWAIT` says no if it is held."""
    async with engine.connect() as conn:
        try:
            result = await conn.execute(
                text(f"SELECT 1 FROM {table} WHERE {column} = :value FOR UPDATE NOWAIT"),
                {"value": value},
            )
            result.scalar_one()  # the row exists: a wrong name would otherwise look like a free row
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
                return False
            raise
    return True


async def _commit_elsewhere(engine: AsyncEngine, *statements: Any) -> None:
    """Commit these statements from another connection, as another request would.

    With a lock timeout: a row the parked request still held would otherwise hang this call, and
    now fails it after two seconds.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '2s'"))
        for statement in statements:
            await conn.execute(statement)


async def _stored(engine: AsyncEngine, table: str, row_id: str) -> tuple[Any, ...]:
    """The whole stored row, every column: a second look shows whether anything wrote to it."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(text(f"SELECT * FROM {table} WHERE id = :id"), {"id": row_id})
        ).one()
    return tuple(row)


async def _claims(settings: Settings) -> list[str]:
    """The keys of the claims on links in Redis right now, of both kinds."""
    redis = redis_module.get_redis(settings)
    return sorted([key async for key in redis.scan_iter("rl:redeem_*")])


async def test_a_confirm_waiting_for_the_hash_holds_no_row_lock_and_no_connection(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox, settings: Settings, gate: HashGate
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)

    confirm = asyncio.create_task(_confirm(client, token))
    try:
        await gate.until_parked()
        assert not confirm.done(), "the request did not wait for the hash"
        # Another connection can lock the link's row and the user's row at once ...
        digest = token_digest(token)
        assert await _row_is_free(engine, "password_reset_tokens", "token_digest", digest)
        assert await _row_is_free(engine, "users", "id", user_id)
        # ... and this request holds no connection while it waits.
        assert engine.pool.checkedout() == 0
    finally:
        gate.let_go()
        # If an assertion above failed, the request still ends before the test does.
        await asyncio.gather(confirm, return_exceptions=True)

    redeemed = confirm.result()
    assert redeemed.status_code == 200, redeemed.text
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200
    assert (await _login(client, OWNER, PASSWORD)).status_code == 401
    assert await _claims(settings) == []


async def test_the_confirm_takes_no_lock_before_the_hash_and_the_link_before_the_user_after_it(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox, gate: HashGate
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)

    # Every statement the confirm sends, in order, split at the moment it parks in the hash.
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        confirm = asyncio.create_task(_confirm(client, token))
        try:
            await gate.until_parked()
            parked_after = len(statements)
        finally:
            gate.let_go()
            await asyncio.gather(confirm, return_exceptions=True)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert confirm.result().status_code == 200

    # Nothing is locked before the hash ...
    assert [s for s in statements[:parked_after] if "FOR UPDATE" in s] == []
    # ... and after it the link's row is locked before the user's row is written, which is the
    # order every other path keeps (a user row first was measured to deadlock against a confirm).
    after = statements[parked_after:]
    locks = [i for i, s in enumerate(after) if "FOR UPDATE" in s]
    assert len(locks) == 1 and "FROM password_reset_tokens" in after[locks[0]], after
    user_writes = [i for i, s in enumerate(after) if s.startswith("UPDATE users")]
    assert user_writes and locks[0] < user_writes[0], after


@pytest.mark.parametrize(
    "meanwhile", ["used", "expired", "retired", "password_gone", "suspended", "removed"]
)
async def test_a_link_that_stops_being_good_during_the_hash_is_refused_after_it(
    meanwhile: str,
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    gate: HashGate,
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)
    digest = token_digest(token)
    token_id = (await _row_for(engine, token)).id
    now = datetime.now(UTC)
    this_link = PasswordResetToken.token_digest == digest

    confirm = asyncio.create_task(_confirm(client, token))
    fresh = ""
    try:
        await gate.until_parked()
        # The link was good when the request looked at it. Now, committed from elsewhere while the
        # request waits for its hash, it stops being good in one of the ways it can.
        if meanwhile == "used":
            await _commit_elsewhere(
                engine, update(PasswordResetToken).where(this_link).values(used_at=now)
            )
        elif meanwhile == "expired":
            await _commit_elsewhere(
                engine,
                update(PasswordResetToken)
                .where(this_link)
                .values(expires_at=now - timedelta(seconds=1)),
            )
        elif meanwhile == "retired":
            # A newer link is asked for through the route, which retires this one.
            fresh = await asyncio.wait_for(_ask(client, mailbox, OWNER), 5)
        elif meanwhile == "password_gone":
            # What a Google link leaves on the row. (The link retires the user's reset links too;
            # this leaves the link alone, so only the check on the user can refuse it.)
            await _commit_elsewhere(
                engine,
                update(User)
                .where(User.id == user_id)
                .values(password_hash=None, google_sub="sub-linked-meanwhile"),
            )
        elif meanwhile == "suspended":
            await _commit_elsewhere(
                engine, update(User).where(User.id == user_id).values(status=UserStatus.SUSPENDED)
            )
        else:
            await _commit_elsewhere(
                engine, update(User).where(User.id == user_id).values(deleted_at=now)
            )
        link_before = await _stored(engine, "password_reset_tokens", token_id)
        user_before = await _stored(engine, "users", user_id)
    finally:
        gate.let_go()
        # If anything above failed, the request still ends before the test does.
        await asyncio.gather(confirm, return_exceptions=True)

    refused = confirm.result()
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    if meanwhile in ("used", "retired"):
        # A link that was used or retired while the hash waited says so, and says what a link
        # used a moment earlier says.
        assert refused.json()["detail"] == USED
        again = await _confirm(client, token)
        assert again.status_code == 400
        assert again.text == refused.text
    else:
        # Expired, orphaned: the same refusal as a link that never existed.
        unknown = await _confirm(client, "never-minted-" + "n" * 30)
        assert refused.json() == unknown.json()
        assert refused.json()["detail"] == NOT_VALID
    # And nothing was written: not one column of the link's row or the user's.
    assert await _stored(engine, "password_reset_tokens", token_id) == link_before
    assert await _stored(engine, "users", user_id) == user_before
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 401
    assert await _claims(settings) == []  # a refusal after the hash gives the claim up too
    if meanwhile == "retired":
        # The newer link was not touched by the refusal: it still works.
        assert (await _confirm(client, fresh, OTHER_PASSWORD)).status_code == 200
        assert (await _login(client, OWNER, OTHER_PASSWORD)).status_code == 200


@pytest.mark.parametrize("kind", ["unknown", "expired", "used", "google_only"])
async def test_a_link_that_was_never_good_is_refused_before_the_hash(
    kind: str,
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    gate: HashGate,
) -> None:
    # The route needs no session, so a junk link must cost no hashing slot: it is refused on the
    # first look, and the hash is never asked for.
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)
    this_link = PasswordResetToken.token_digest == token_digest(token)
    if kind == "unknown":
        token = "never-minted-" + "n" * 30
    elif kind == "expired":
        await _set_expiry(engine, token, datetime.now(UTC) - timedelta(seconds=1))
    elif kind == "used":
        await _commit_elsewhere(
            engine, update(PasswordResetToken).where(this_link).values(used_at=datetime.now(UTC))
        )
    else:
        await _commit_elsewhere(
            engine,
            update(User)
            .where(User.id == user_id)
            .values(password_hash=None, google_sub="sub-google-only"),
        )

    try:
        refused = await asyncio.wait_for(_confirm(client, token), 5)
    finally:
        gate.let_go()
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == (USED if kind == "used" else NOT_VALID)
    assert gate.parked == 0, "a link that was never good reached the hash"
    assert await _claims(settings) == []  # a refusal on the first look gives the claim up too


async def test_two_confirms_that_both_hold_a_claim_still_give_exactly_one_success(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The claim of section 3c keeps a second request out of the hash. It is not what makes a link
    # single use: a claim has a lifetime, and a holder that outlives it meets a request with a
    # claim of its own. So every claim is granted here, as two expired ones would be, and the row
    # lock and the decision under it have to give exactly one success on their own.
    async def everyone_holds_a_claim(*args: Any, **kwargs: Any) -> str:
        return "a-claim-that-has-already-ended"

    monkeypatch.setattr(rate_limit, "claim_redemption", everyone_holds_a_claim)
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    # Both requests get a connection at once, so the second one's decision lands inside the window
    # the winner holds open and does not wait on a cold connect.
    await _warm_the_pool(engine)
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def confirm(password: str) -> Response:
        # Its own client: its own connection and no shared cookie jar.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await _confirm(other, token, password)

    real_commit = AsyncSession.commit

    async def slow_commit(self: AsyncSession) -> None:
        await asyncio.sleep(0.3)
        await real_commit(self)

    first = asyncio.create_task(confirm(NEW_PASSWORD))
    second = asyncio.create_task(confirm(OTHER_PASSWORD))
    try:
        # Both have looked at the link while it was good, and both wait in the hash.
        await gate.until_parked(2)
        # They are let go together, and the commit of whichever locks first is held for 0.3 s with
        # the row still locked. The other can only be refused by the decision it takes under the
        # lock: a decision taken without the lock reads the unspent link, and both would succeed.
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", slow_commit)
            gate.let_go()
            await asyncio.gather(first, second, return_exceptions=True)
    finally:
        gate.let_go()
        # If anything above failed, both requests still end before the test does.
        await asyncio.gather(first, second, return_exceptions=True)

    first_response, second_response = first.result(), second.result()
    codes = sorted([first_response.status_code, second_response.status_code])
    assert codes == [200, 400], (first_response.text, second_response.text)
    if first_response.status_code == 200:
        winner, loser, refused = NEW_PASSWORD, OTHER_PASSWORD, second_response
    else:
        winner, loser, refused = OTHER_PASSWORD, NEW_PASSWORD, first_response
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED
    assert (await _login(client, OWNER, winner)).status_code == 200
    assert (await _login(client, OWNER, loser)).status_code == 401
    assert (await _login(client, OWNER, PASSWORD)).status_code == 401


# --- 3c. one hash per link in flight: the claim in Redis (checklist 3.129) -----------------------
#
# The route has no limit of its own, and every request that passed the first look used to hash. A
# request now claims the link in Redis before it reads it: one gets the claim and goes on, and the
# others are refused at once as a used link is, having read and hashed nothing. The claim is
# released however the request ends, and has a lifetime if its holder dies.


async def test_five_confirms_at_once_with_one_link_hash_exactly_once_and_one_succeeds(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox, settings: Settings, gate: HashGate
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    await _warm_the_pool(engine)
    redis = redis_module.get_redis(settings)
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def confirm(password: str) -> Response:
        # Its own client: its own connection and no shared cookie jar.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await _confirm(other, token, password)

    passwords = [f"{NEW_PASSWORD}-{n}" for n in range(5)]
    requests = [asyncio.create_task(confirm(password)) for password in passwords]
    try:
        # One request got the claim and waits in the hash. The other four are refused without
        # reaching it: they end, and the hash has been asked for once.
        await gate.until_parked()
        async with asyncio.timeout(10):
            while sum(request.done() for request in requests) < 4 and gate.parked < 2:
                await asyncio.sleep(0.01)
        assert gate.parked == 1, f"{gate.parked} hashes ran for one link"
        # While it waits the claim is held, with a lifetime.
        (held,) = await _claims(settings)
        assert 0 < await redis.pttl(held) <= rate_limit.redeem_claim_ttl_ms(settings)
    finally:
        gate.let_go()
        await asyncio.gather(*requests, return_exceptions=True)

    responses = [request.result() for request in requests]
    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400, 400]
    # Every other request got the body of a link that has been used, byte for byte: the link is
    # redeemed now, so it is asked again to see what a used link answers.
    used = await _confirm(client, token)
    assert used.status_code == 400
    assert used.json()["detail"] == USED
    for refused in (r for r in responses if r.status_code == 400):
        assert refused.text == used.text
    assert gate.parked == 1
    assert await _claims(settings) == []  # and the claim is gone
    # One password took effect: the winner's.
    winner = next(
        password
        for password, response in zip(passwords, responses, strict=True)
        if response.status_code == 200
    )
    for password in passwords:
        expected = 200 if password == winner else 401
        assert (await _login(client, OWNER, password)).status_code == expected, password


async def test_a_claim_left_by_a_holder_that_died_ends_at_its_lifetime_and_not_before(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)
    redis = redis_module.get_redis(settings)
    # The lifetime is the queue timeout plus the margin: shortened, so the test can wait it out.
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 0.25)
    monkeypatch.setattr(settings, "redeem_claim_margin_seconds", 0.25)
    assert rate_limit.redeem_claim_ttl_ms(settings) == 500

    # A request took the claim and died: nothing will ever release it.
    held = await rate_limit.claim_redemption(
        redis, settings, kind="reset", digest=token_digest(token)
    )
    assert held is not None
    (key,) = await _claims(settings)
    assert 0 < await redis.pttl(key) <= 500

    # Until it ends, the link is refused as a used one is, and nothing is read, hashed or written:
    # not one statement reaches the database, because the claim comes before the first look.
    link_id = (await _row_for(engine, token)).id
    link_before = await _stored(engine, "password_reset_tokens", link_id)
    user_before = await _stored(engine, "users", user_id)
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        # (Bounded: a request that got past the claim would wait in the hash, not answer.)
        refused = await asyncio.wait_for(_confirm(client, token), 5)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        gate.let_go()
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED
    assert statements == []
    assert gate.parked == 0
    assert await _stored(engine, "password_reset_tokens", link_id) == link_before
    assert await _stored(engine, "users", user_id) == user_before

    # Then it ends on its own, and the same link works.
    await asyncio.sleep(0.7)
    assert await _claims(settings) == []
    redeemed = await _confirm(client, token)
    assert redeemed.status_code == 200, redeemed.text
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200


async def test_with_redis_down_a_confirm_is_a_503_that_reads_hashes_and_writes_nothing(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)
    link_id = (await _row_for(engine, token)).id
    link_before = await _stored(engine, "password_reset_tokens", link_id)
    user_before = await _stored(engine, "users", user_id)

    async def redis_is_away(self: Redis, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("Redis is away")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Redis, "eval", redis_is_away)
            # (Bounded: a request that got past the claim would wait in the hash, not answer.)
            refused = await asyncio.wait_for(_confirm(client, token), 5)
    finally:
        gate.let_go()

    # What `errors.py` answers for a Redis that cannot be reached: a 503, not the link's refusal.
    assert refused.status_code == 503, refused.text
    assert refused.json()["type"] == "/problems/service-unavailable"
    assert refused.json()["detail"] == "This is temporarily unavailable. Please try again shortly."
    assert gate.parked == 0
    assert await _stored(engine, "password_reset_tokens", link_id) == link_before
    assert await _stored(engine, "users", user_id) == user_before
    assert (await _login(client, OWNER, PASSWORD)).status_code == 200
    # The link was not spent, and works once Redis is back.
    assert (await _confirm(client, token)).status_code == 200


async def test_a_claim_that_cannot_be_released_does_not_fail_the_confirm(
    client: AsyncClient,
    mailbox: Mailbox,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    redis = redis_module.get_redis(settings)
    real_eval = rate_limit._eval  # noqa: SLF001

    async def release_fails(
        redis: Redis, script: str, keys: list[str], args: list[str]
    ) -> list[int]:
        if script is rate_limit._RELEASE_IF:  # noqa: SLF001
            raise RedisConnectionError("Redis is away")
        return await real_eval(redis, script, keys, args)

    monkeypatch.setattr(rate_limit, "_eval", release_fails)
    with structlog.testing.capture_logs() as events:
        redeemed = await _confirm(client, token)

    # The outcome was decided before the release, so it stands; the claim ends by itself.
    assert redeemed.status_code == 200, redeemed.text
    (left,) = await _claims(settings)
    assert 0 < await redis.pttl(left) <= rate_limit.redeem_claim_ttl_ms(settings)
    # One warning, naming the kind and nothing else: not the token, not its digest.
    warned = [e for e in events if e["event"] == "redeem.claim.release_unavailable"]
    assert len(warned) == 1 and warned[0]["kind"] == "reset"
    logged = json.dumps(events, default=str)
    assert token not in logged and token_digest(token) not in logged


async def test_a_hash_that_gets_no_slot_releases_the_claim_and_the_link_still_works(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    token = await _ask(client, mailbox, OWNER)
    link_id = (await _row_for(engine, token)).id
    link_before = await _stored(engine, "password_reset_tokens", link_id)
    user_before = await _stored(engine, "users", user_id)

    async def no_slot(password: str, settings: Settings | None = None) -> str:
        raise ServiceUnavailable(detail="This is temporarily unavailable. Try again shortly.")

    with monkeypatch.context() as patch:
        patch.setattr(reset_service, "hash_password_async", no_slot)
        refused = await _confirm(client, token)

    assert refused.status_code == 503, refused.text
    # Given up on the way out, so the person can try again at once; nothing was written.
    assert await _claims(settings) == []
    assert await _stored(engine, "password_reset_tokens", link_id) == link_before
    assert await _stored(engine, "users", user_id) == user_before
    assert (await _confirm(client, token)).status_code == 200


async def test_a_release_frees_only_the_claim_that_is_its_own(
    client: AsyncClient, settings: Settings
) -> None:
    redis = redis_module.get_redis(settings)
    digest = "d" * 64
    mine = await rate_limit.claim_redemption(redis, settings, kind="reset", digest=digest)
    assert mine is not None
    # One holder at a time, per link and per kind: a second claim on the link is refused, and an
    # invitation with the same digest is another key.
    assert await rate_limit.claim_redemption(redis, settings, kind="reset", digest=digest) is None
    invite = await rate_limit.claim_redemption(redis, settings, kind="invite", digest=digest)
    assert invite is not None
    (reset_key, _) = (await _claims(settings))[::-1]  # redeem_reset sorts after redeem_invite

    # Mine ran out and another request took the link: the key holds ITS claim now.
    await redis.set(reset_key, "the-other-requests-claim", px=5000)
    await rate_limit.release_redemption(redis, settings, kind="reset", digest=digest, claim=mine)
    assert await redis.get(reset_key) == "the-other-requests-claim"
    # Its own release frees it, and leaves the invitation's key alone.
    await rate_limit.release_redemption(
        redis, settings, kind="reset", digest=digest, claim="the-other-requests-claim"
    )
    assert await redis.get(reset_key) is None
    assert len(await _claims(settings)) == 1


# --- 3d. the sentence for a link already used (A5 part 2) ---------------------------------------


async def test_a_link_that_was_redeemed_says_it_was_used(
    client: AsyncClient, mailbox: Mailbox
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    assert (await _confirm(client, token)).status_code == 200

    again = await _confirm(client, token, OTHER_PASSWORD)
    assert again.status_code == 400, again.text
    body = again.json()
    assert body["type"] == INVALID_TOKEN
    assert body["title"] == "That reset link is not valid"
    assert body["detail"] == USED
    # And it set nothing.
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200
    assert (await _login(client, OWNER, OTHER_PASSWORD)).status_code == 401


async def test_unknown_and_expired_links_keep_the_older_sentence(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    await _set_expiry(engine, token, datetime.now(UTC) - timedelta(seconds=1))

    expired = await _confirm(client, token)
    unknown = await _confirm(client, "never-minted-" + "n" * 30)
    for refused in (expired, unknown):
        assert refused.status_code == 400, refused.text
        assert refused.json()["type"] == INVALID_TOKEN
        assert refused.json()["detail"] == NOT_VALID
    assert expired.text == unknown.text


async def test_a_link_another_request_is_redeeming_is_refused_as_a_used_one_is(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox, settings: Settings
) -> None:
    # The claim's refusal and a used link's refusal are the same response in every byte: status,
    # type, title and detail, so the race cannot be told from a second use.
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    redis = redis_module.get_redis(settings)
    digest = token_digest(token)

    held = await rate_limit.claim_redemption(redis, settings, kind="reset", digest=digest)
    assert held is not None
    in_flight = await _confirm(client, token)
    await rate_limit.release_redemption(redis, settings, kind="reset", digest=digest, claim=held)

    assert (await _confirm(client, token)).status_code == 200
    used = await _confirm(client, token)

    assert in_flight.status_code == used.status_code == 400
    assert in_flight.json()["type"] == used.json()["type"] == INVALID_TOKEN
    assert in_flight.json()["title"] == used.json()["title"]
    assert in_flight.json()["detail"] == used.json()["detail"] == USED
    assert in_flight.text == used.text
    assert in_flight.headers["content-type"] == used.headers["content-type"]


@pytest.mark.parametrize("retire", ["new_request", "password_change"])
async def test_an_expired_unused_link_stays_not_valid_after_a_retire(
    retire: str, client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox
) -> None:
    # A retire stamps the links that are still live. An expired link nobody used is left as it
    # was, so it keeps answering "not valid" and does not turn into "used".
    await _sign_up(client, OWNER)
    old = await _ask(client, mailbox, OWNER)
    await _set_expiry(engine, old, datetime.now(UTC) - timedelta(seconds=1))
    if retire == "new_request":
        await _ask(client, mailbox, OWNER)
    else:
        changed = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )
        assert changed.status_code == 200, changed.text

    assert (await _row_for(engine, old)).used_at is None
    refused = await _confirm(client, old, OTHER_PASSWORD)
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == NOT_VALID


# --- 4. expiry ----------------------------------------------------------------------------------


async def test_reset_token_is_refused_after_expires_at_and_works_before_it(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)

    # Past its expiry: refused, and nothing changed.
    await _set_expiry(engine, token, datetime.now(UTC) - timedelta(seconds=1))
    refused = await _confirm(client, token)
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert (await _login(client, OWNER, PASSWORD)).status_code == 200

    # The same token and the same row, with only the expiry moved to before it: it works.
    await _set_expiry(engine, token, datetime.now(UTC) + timedelta(seconds=60))
    redeemed = await _confirm(client, token)
    assert redeemed.status_code == 200, redeemed.text
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200


async def test_reset_token_lifetime_is_the_password_reset_ttl_setting(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `password_reset_ttl_seconds`, set by PASSWORD_RESET_TTL_SECONDS: one hour unless set. The
    # email and the refusal both say "an hour".
    assert Settings.model_fields["password_reset_ttl_seconds"].default == 3600

    monkeypatch.setattr(settings, "password_reset_ttl_seconds", 120)
    await _sign_up(client, OWNER)
    before = datetime.now(UTC)
    token = await _ask(client, mailbox, OWNER)
    after = datetime.now(UTC)

    row = await _row_for(engine, token)
    ttl = timedelta(seconds=120)
    assert before + ttl <= row.expires_at <= after + ttl


# --- 5. bound to the user ------------------------------------------------------------------------


async def test_reset_changes_only_the_issuing_users_password(
    client: AsyncClient, engine: AsyncEngine, mailbox: Mailbox, settings: Settings
) -> None:
    agency_id = (await _sign_up(client, OWNER))["agency"]["id"]
    owner_session = _cookie(client, settings)
    await _add_member(engine, settings, agency_id, MEMBER)
    assert (await _login(client, MEMBER, PASSWORD)).status_code == 200
    member_session = _cookie(client, settings)
    assert member_session != owner_session

    # The member has a link of their own, asked for first and never used. Then the owner asks for
    # one and redeems it.
    member_token = await _ask(client, mailbox, MEMBER)
    owner_token = await _ask(client, mailbox, OWNER)
    client.cookies.clear()
    assert (await _confirm(client, owner_token)).status_code == 200

    # The owner's password changed and the owner's session is gone.
    assert (await _login(client, OWNER, PASSWORD)).status_code == 401
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200
    assert (await _me(client, settings, owner_session)).status_code == 401
    # The member's password, session and own link are as they were.
    assert (await _login(client, MEMBER, PASSWORD)).status_code == 200
    assert (await _login(client, MEMBER, NEW_PASSWORD)).status_code == 401
    assert (await _me(client, settings, member_session)).status_code == 200
    assert (await _confirm(client, member_token, OTHER_PASSWORD)).status_code == 200
    # And each redemption touched its own user alone.
    assert (await _login(client, MEMBER, OTHER_PASSWORD)).status_code == 200
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200


# --- 6. a newer token retires the older ---------------------------------------------------------


async def test_a_newer_reset_token_retires_the_older(
    client: AsyncClient, mailbox: Mailbox
) -> None:
    await _sign_up(client, OWNER)
    first = await _ask(client, mailbox, OWNER)
    second = await _ask(client, mailbox, OWNER)

    refused = await _confirm(client, first)
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED  # retired by the newer request, so used
    assert (await _login(client, OWNER, PASSWORD)).status_code == 200  # the refusal changed nothing

    redeemed = await _confirm(client, second)
    assert redeemed.status_code == 200, redeemed.text
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200


async def test_two_concurrent_reset_requests_for_one_address_leave_one_live_token(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]

    # Two things make the overlap real and not lucky. The pool is warmed first, so both requests
    # have a connection at once. And each commit is held for 0.2 s, so both requests are inside
    # their transaction together, while the first holds the lock and has inserted its row.
    await _warm_the_pool(engine)

    real_commit = AsyncSession.commit

    async def slow_commit(self: AsyncSession) -> None:
        await asyncio.sleep(0.2)
        await real_commit(self)

    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def ask() -> Response:
        # Its own client: its own connection and no shared cookie jar.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await other.post(f"{BASE}/auth/reset-password/request", json={"email": OWNER})

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", slow_commit)
        first, second = await asyncio.gather(ask(), ask())

    # Both are answered alike. The request that found the other holding the lock does not wait and
    # mints nothing, so ONE link reached the sender, one row exists, and it is the live one.
    assert first.status_code == second.status_code == 200
    assert first.text == second.text
    assert len(mailbox.sent) == 1
    token = mailbox.token_in(mailbox.sent[0][1])
    async with _db(engine)() as s:
        rows = list(
            (
                await s.execute(
                    select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
                )
            ).scalars()
        )
    assert len(rows) == 1
    live = [row.token_digest for row in rows if row.used_at is None]
    assert live == [token_digest(token)], "not exactly one live link, or not the one sent"

    # And the one link works.
    assert (await _confirm(client, token)).status_code == 200
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200


# --- 6b. other things that retire a link: a password change, a seat issued again ----------------


async def test_changing_the_password_while_signed_in_retires_a_reset_link(
    client: AsyncClient, mailbox: Mailbox
) -> None:
    await _sign_up(client, OWNER)  # and signed in
    leaked = await _ask(client, mailbox, OWNER)

    changed = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text

    # The link asked for before the change is refused and sets nothing: the changed password still
    # works, and the one the link tried to set does not.
    refused = await _confirm(client, leaked, OTHER_PASSWORD)
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED
    client.cookies.clear()
    assert (await _login(client, OWNER, NEW_PASSWORD)).status_code == 200
    assert (await _login(client, OWNER, OTHER_PASSWORD)).status_code == 401

    # Only the links that existed died: one asked for after the change works.
    fresh = await _ask(client, mailbox, OWNER)
    assert (await _confirm(client, fresh, OTHER_PASSWORD)).status_code == 200


async def test_a_password_change_and_a_redemption_at_once_do_not_deadlock(
    client: AsyncClient,
    engine: AsyncEngine,
    mailbox: Mailbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    await _warm_the_pool(engine)

    # The redemption holds its token row (locked) while the change runs. The change retires links
    # before it writes the password: the same order as the redemption, which locks the token row
    # and then writes the user row. Retiring after the write took the user row first, the two
    # waited on each other, and Postgres aborted the change after a second.
    #
    # Until checklist 3.129 the hold was the redemption's slow hash, which ran with the row locked.
    # The hash now waits before the lock is taken, so the hold is put where the lock is: in the
    # flush that writes the user row, after the redemption has locked and checked the token. (The
    # redemption is the only flush in this test with a dirty link in its session.)
    redeemed_password = "the-redemption-passphrase"
    holding = asyncio.Event()
    real_flush = AsyncSession.flush

    async def hold_the_redemption(self: AsyncSession, objects: Any = None) -> None:
        if any(isinstance(obj, PasswordResetToken) for obj in self.dirty):
            holding.set()
            await asyncio.sleep(0.6)
        await real_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", hold_the_redemption)
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def redeem() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await _confirm(other, token, redeemed_password)

    async def change() -> Response:
        # Starts once the redemption holds the link's row, and runs inside its 0.6 s hold.
        await asyncio.wait_for(holding.wait(), 10)
        return await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        )

    # A deadlock would not return here: the aborted request raises out of the gather.
    redeemed, changed = await asyncio.gather(redeem(), change())
    assert redeemed.status_code == 200, redeemed.text
    # The change waited for the redemption and was refused cleanly: the password it checked is gone.
    # The wrong-password 401, not a missing session's.
    assert changed.status_code == 401, changed.text
    assert changed.json()["type"] == "/problems/invalid-credentials"
    client.cookies.clear()
    assert (await _login(client, OWNER, redeemed_password)).status_code == 200


async def test_removing_a_seat_leaves_its_expired_unused_link_unstamped(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    # The removal retires the member's live links. One that had already expired and was never used
    # stays unstamped, so it keeps answering "not valid" and not "used".
    agency_id = (await _sign_up(client, OWNER))["agency"]["id"]
    await _add_member(engine, settings, agency_id, MEMBER)
    async with _db(engine)() as s:
        member_id = (await s.execute(select(User.id).where(User.email == MEMBER))).scalar_one()
    old = "expired-seat-link-" + "e" * 32
    await _add_token(engine, member_id, old)
    await _set_expiry(engine, old, datetime.now(UTC) - timedelta(seconds=1))

    assert (await client.delete(f"{BASE}/users/{member_id}")).status_code == 204

    assert (await _row_for(engine, old)).used_at is None
    refused = await _confirm(client, old)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == NOT_VALID


async def test_a_reset_link_is_retired_when_a_removed_seat_is_invited_again(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    agency_id = (await _sign_up(client, OWNER))["agency"]["id"]

    async def invite() -> str:
        resp = await client.post(
            f"{BASE}/agencies/{agency_id}/invitations", json={"email": MEMBER, "role": "member"}
        )
        assert resp.status_code == 201, resp.text
        accept_url = resp.json()["acceptUrl"]  # given back while no email provider is configured
        assert accept_url, "the invitation link was not returned"
        return accept_url.partition("#")[2]

    async def accept(token: str, password: str) -> Response:
        return await client.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "Member", "password": password},
        )

    joined = await accept(await invite(), PASSWORD)
    assert joined.status_code == 200, joined.text
    member_id = joined.json()["user"]["id"]
    assert (await _login(client, OWNER, PASSWORD)).status_code == 200  # back to the owner
    assert (await client.delete(f"{BASE}/users/{member_id}")).status_code == 204

    # A link committed by a request that was in flight at the removal, after the removal had
    # retired the links that existed. Written by hand, as that request would have written it.
    stale = "removed-seat-link-" + "z" * 32
    await _add_token(engine, member_id, stale)

    # The seat is invited again and accepted: the row is active again, with a password of its own.
    rejoined = await accept(await invite(), OTHER_PASSWORD)
    assert rejoined.status_code == 200, rejoined.text

    refused = await _confirm(client, stale, NEW_PASSWORD)
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == INVALID_TOKEN
    assert refused.json()["detail"] == USED
    client.cookies.clear()
    assert (await _login(client, MEMBER, OTHER_PASSWORD)).status_code == 200
    assert (await _login(client, MEMBER, NEW_PASSWORD)).status_code == 401


# --- 7. the reset ends every session ------------------------------------------------------------


async def test_reset_ends_every_session_and_sets_no_session_cookie(
    client: AsyncClient, mailbox: Mailbox, settings: Settings
) -> None:
    user_id = (await _sign_up(client, OWNER))["user"]["id"]
    first = _cookie(client, settings)
    token = await _ask(client, mailbox, OWNER)
    # A second session, signed in after the link was asked for.
    assert (await _login(client, OWNER, PASSWORD)).status_code == 200
    second = _cookie(client, settings)
    assert second != first
    for cookie in (first, second):
        assert (await _me(client, settings, cookie)).status_code == 200
    redis = redis_module.get_redis(settings)
    assert len(await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}")) == 2

    client.cookies.clear()
    confirm = await _confirm(client, token)
    assert confirm.status_code == 200, confirm.text

    # No session is installed: a Set-Cookie for the session cookie can only be its deletion.
    for header in confirm.headers.get_list("set-cookie"):
        name, _, rest = header.partition("=")
        if name.strip() == settings.session_cookie_key:
            assert rest.partition(";")[0].strip('" ') == "", "the response installs a session"
            assert "max-age=0" in header.lower()
    assert client.cookies.get(settings.session_cookie_key) is None
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401

    # Both cookies are dead, and so is every session the user has.
    for cookie in (first, second):
        assert (await _me(client, settings, cookie)).status_code == 401
    assert await redis.smembers(f"{USER_SESSIONS_PREFIX}{user_id}") == set()
    assert [key async for key in redis.scan_iter(f"{SESSION_PREFIX}*")] == []


# --- 8. no token to a third party ---------------------------------------------------------------


async def test_reset_request_and_confirm_write_no_log_line_with_the_token(
    client: AsyncClient,
    mailbox: Mailbox,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _sign_up(client, OWNER)
    capsys.readouterr()  # only what the reset writes: sign-up's own output is dropped
    caplog.clear()
    caplog.set_level("DEBUG")

    with structlog.testing.capture_logs() as events:
        token = await _ask(client, mailbox, OWNER)
        # Every way a confirm with the token in its body can end: a body that fails validation,
        # a redemption, and a link used twice.
        assert (await _confirm(client, token, "short")).status_code == 422
        assert (await _confirm(client, token)).status_code == 200
        assert (await _confirm(client, token)).status_code == 400

    everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
    assert token not in everything, "the token reached a log"
    # The events that say what happened are there, so the search had something to read.
    kinds = {event["event"] for event in events}
    assert {"password_reset.request.minted", "password_reset.confirm.succeeded"} <= kinds
