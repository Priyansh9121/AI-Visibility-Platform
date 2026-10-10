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
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from avp_api import ids, security
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.models import Base, PasswordResetToken, User, UserRole
from avp_api.security import hash_password, token_digest
from avp_api.services import email as email_service
from avp_api.services import password_reset as reset_service
from avp_api.sessions import SESSION_PREFIX, USER_SESSIONS_PREFIX

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "an-entirely-different-passphrase"
OTHER_PASSWORD = "a-third-and-quite-different-passphrase"
OWNER = "owner@reset-properties.example"
MEMBER = "member@reset-properties.example"
INVALID_TOKEN = "/problems/invalid-reset-token"


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
    """Record each link the route sends. The real sender still runs behind the spy.

    The suite configures no email provider, so the real sender logs that nothing was sent and
    returns; no request leaves the process.
    """
    box = Mailbox(settings)
    real = email_service.send_password_reset

    async def spy(to: str, reset_url: str, *, user_id: str, settings: Settings) -> None:
        box.sent.append((to, reset_url))
        await real(to, reset_url, user_id=user_id, settings=settings)

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

    # A slow hash holds both requests between their read of the token and their write, so only the
    # row lock (and not luck in the timing) can make the second one see the first one's commit.
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

    # One password change took effect: the winner's. The loser's did not, nor did the old one stay.
    assert (await _login(client, OWNER, winner)).status_code == 200
    assert (await _login(client, OWNER, loser)).status_code == 401
    assert (await _login(client, OWNER, PASSWORD)).status_code == 401


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
    settings: Settings,
) -> None:
    # Two hashing threads at least: with one, the change's hash would queue behind the slow
    # redemption, the two would never overlap, and this would pass whatever the lock order.
    assert settings.argon2_max_concurrency >= 2
    await _sign_up(client, OWNER)
    token = await _ask(client, mailbox, OWNER)
    await _warm_the_pool(engine)

    # The redemption hashes slowly, so it holds its token row (locked) while the change runs. The
    # change retires links before it writes the password: the same order as the redemption, which
    # locks the token row and then writes the user row. Retiring after the write took the user row
    # first, the two waited on each other, and Postgres aborted the change after a second.
    slow_password = "the-slow-redemption-passphrase"
    real_hash = security.hash_password

    def slow_hash(password: str, settings: Settings | None = None) -> str:
        if password == slow_password:
            threading.Event().wait(0.6)
        return real_hash(password, settings)

    monkeypatch.setattr(security, "hash_password", slow_hash)
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def redeem() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            return await _confirm(other, token, slow_password)

    async def change() -> Response:
        await asyncio.sleep(0.15)
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
    assert (await _login(client, OWNER, slow_password)).status_code == 200


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
