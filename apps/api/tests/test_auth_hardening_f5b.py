"""F5b: a login success refunds only the email bucket; login outcomes are logged by id;
Argon2 runs off the loop in a bounded pool; the reset email goes out after the response;
two concurrent invitation accepts of one token give exactly one success.

The last section (launch checklist 3.129) is the accept's own hash: it waits outside the row locks
and without a database connection, as the login and the change of password already do, and N
requests with one invitation hash once, because the invitation is claimed in Redis first. The same
for the reset confirm is in test_password_reset_properties.py, sections 3b and 3c."""

from __future__ import annotations

import asyncio
import json
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

from avp_api import redis_client as redis_module
from avp_api import security
from avp_api.config import Settings
from avp_api.errors import ServiceUnavailable
from avp_api.models import Invitation, User, UserStatus
from avp_api.security import token_digest
from avp_api.services import email as email_service
from avp_api.services import invitations as invitation_service
from avp_api.services import rate_limit

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
IP_HEADER = "X-Test-Client-IP"
TEST_ORIGIN = "http://localhost:3000"  # conftest.TEST_ORIGIN


@pytest.fixture(autouse=True)
def _limits(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)
    monkeypatch.setattr(settings, "login_limit_per_email", 3)
    monkeypatch.setattr(settings, "login_limit_per_ip", 4)


async def _sign_up(client: AsyncClient, email: str) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "F5b Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
        headers={IP_HEADER: "192.0.2.200"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str, ip: str = "203.0.113.9"):
    return await client.post(
        f"{BASE}/auth/login", json={"email": email, "password": password},
        headers={IP_HEADER: ip},
    )


# --- item 1 (reverted): a success still gives its hit back to both buckets -------


async def test_a_success_leaves_the_ip_where_it_was_before_it(
    client: AsyncClient, settings: Settings
) -> None:
    await _sign_up(client, "real@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    r = redis_module.get_redis(settings)
    for i in range(3):
        assert (await _login(client, f"nobody{i}@f5b.example", "wrong-password")).status_code == 401
    ip_key = [k async for k in r.scan_iter("rl:login:ip:*")][0]
    before = int(await r.get(ip_key))
    assert (await _login(client, "real@f5b.example", PASSWORD)).status_code == 200
    assert int(await r.get(ip_key)) == before


# --- item 2 -------------------------------------------------------------------


async def test_login_outcomes_are_logged_by_id_only(client: AsyncClient) -> None:
    me = await _sign_up(client, "logged@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    user_id, agency_id = me["user"]["id"], me["agency"]["id"]
    with structlog.testing.capture_logs() as events:
        known = await _login(client, "logged@f5b.example", "wrong-password")
        unknown = await _login(client, "ghost@f5b.example", "wrong-password")
        ok = await _login(client, "logged@f5b.example", PASSWORD)
    assert (known.status_code, unknown.status_code, ok.status_code) == (401, 401, 200)
    failed = [e for e in events if e["event"] == "auth.login.failed"]
    assert [e["user_id"] for e in failed] == [user_id, None]
    assert all(e["reason"] == "invalid_credentials" and e["log_level"] == "info" for e in failed)
    won = [e for e in events if e["event"] == "auth.login.succeeded"]
    assert len(won) == 1
    assert won[0]["user_id"] == user_id and won[0]["agency_id"] == agency_id
    blob = json.dumps(events, default=str)
    assert "@" not in blob and "203.0.113.9" not in blob and "wrong-password" not in blob


# --- item 3 -------------------------------------------------------------------


async def test_hash_runs_off_the_loop_thread(settings: Settings) -> None:
    seen: list[int] = []
    real = security.hash_password

    def spy(pw: str, s: Settings | None = None) -> str:
        seen.append(threading.get_ident())
        return real(pw, s)

    security.hash_password = spy  # restored below
    try:
        await security.hash_password_async("a-password-of-length", settings)
    finally:
        security.hash_password = real
    assert seen and seen[0] != threading.get_ident()


async def test_ten_parallel_verifies_never_run_more_than_the_limit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "argon2_max_concurrency", 2)
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "calls": 0}

    def slow(pw: str, h: str, s: Settings | None = None) -> bool:
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        threading.Event().wait(0.05)
        with lock:
            state["now"] -= 1
        return True

    monkeypatch.setattr(security, "verify_password", slow)
    await asyncio.gather(
        *(security.verify_password_async("p", "h", settings) for _ in range(10))
    )
    assert state["calls"] == 10
    assert state["peak"] == 2


async def test_unknown_email_still_runs_a_dummy_verify(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    real = security.dummy_verify

    def spy(s: Settings | None = None) -> None:
        calls.append(threading.get_ident())
        real(s)

    monkeypatch.setattr(security, "dummy_verify", spy)
    assert (await _login(client, "ghost2@f5b.example", "whatever-password")).status_code == 401
    assert len(calls) == 1 and calls[0] != threading.get_ident()


# --- item 4 -------------------------------------------------------------------


async def _raw_post(app: Any, path: str, body: dict[str, str], origin: str) -> tuple[
    asyncio.Event, asyncio.Task[None], dict[str, Any]
]:
    """Drive the ASGI app directly: ASGITransport waits for background tasks, which would
    hide whether the response was sent first. `sent` is set when the body's last chunk goes."""
    raw = json.dumps(body).encode()
    sent = asyncio.Event()
    out: dict[str, Any] = {"body": b"", "status": None}
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode()), (b"origin", origin.encode())],
    }
    fed = False

    async def receive() -> dict[str, Any]:
        nonlocal fed
        if not fed:
            fed = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
            if not msg.get("more_body"):
                sent.set()

    return sent, asyncio.create_task(app(scope, receive, send)), out


async def test_reset_email_goes_after_the_response_and_bodies_match(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Email on: with it off a request does nothing at all (hardening A1).
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    await _sign_up(client, "known@f5b.example")
    app = client._transport.app  # noqa: SLF001
    release = threading.Event()
    started = asyncio.Event()
    sent_to: list[str] = []

    async def blocked_send(to: str, url: str, *, user_id: str, settings: Settings) -> None:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        sent_to.append(to)

    monkeypatch.setattr(email_service, "send_password_reset", blocked_send)
    path = f"{BASE}/auth/reset-password/request"
    sent, task, known = await _raw_post(app, path, {"email": "known@f5b.example"}, TEST_ORIGIN)
    await asyncio.wait_for(sent.wait(), 10)  # the response is out ...
    assert not release.is_set() and sent_to == []  # ... while the send has not finished
    release.set()
    await asyncio.wait_for(task, 10)
    assert sent_to == ["known@f5b.example"]

    sent2, task2, unknown = await _raw_post(
        app, path, {"email": "nobody@f5b.example"}, TEST_ORIGIN
    )
    await asyncio.wait_for(task2, 10)
    assert known["status"] == unknown["status"] == 200
    assert known["body"] == unknown["body"]
    assert sent_to == ["known@f5b.example"]  # nothing sent for the unknown address


# --- item 5 -------------------------------------------------------------------


async def test_two_concurrent_accepts_of_one_token_give_one_success(
    client: AsyncClient, engine, settings: Settings,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A slow hash holds the winner, and the claim (the 3.129 section at the end of the file)
    # refuses the other request while it waits: this is the end-to-end "exactly one of two
    # succeeds", not a proof of the row locks. The backstop test in that section proves the locks,
    # with the claim out of the way.
    real_hash = security.hash_password

    def slow_hash(pw: str, s: Settings | None = None) -> str:
        threading.Event().wait(0.3)
        return real_hash(pw, s)

    monkeypatch.setattr(security, "hash_password", slow_hash)
    agency_id = (await _sign_up(client, "boss@f5b.example"))["agency"]["id"]
    invited = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations",
        json={"email": "seat@f5b.example", "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    token = "f5b-invite-" + "q" * 32
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        row = (
            await s.execute(select(Invitation).where(Invitation.agency_id == agency_id,
                                                     Invitation.accepted_at.is_(None)))
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()
    await client.post(f"{BASE}/auth/logout")

    r = redis_module.get_redis(settings)
    before = {k async for k in r.scan_iter("sess:*")}
    app = client._transport.app  # noqa: SLF001

    async def accept() -> Any:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
            headers={"Origin": TEST_ORIGIN},
        ) as c:
            return await c.post(
                f"{BASE}/auth/invitations/accept",
                json={"token": token, "fullName": "Jo", "password": PASSWORD},
            )

    results = await asyncio.gather(accept(), accept())
    codes = sorted(x.status_code for x in results)
    assert codes == [200, 400], [x.text for x in results]
    after = {k async for k in r.scan_iter("sess:*")}
    assert len(after - before) == 1
    async with factory() as s:
        n = len((await s.execute(select(User).where(User.email == "seat@f5b.example"))).all())
    assert n == 1


# --- review follow-ups: M1, L1, L2, L5 ------------------------------------------


async def _blocked_pool(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> threading.Event:
    """A pool of one whose verifies all wait on the returned event."""
    monkeypatch.setattr(settings, "argon2_max_concurrency", 1)
    release = threading.Event()

    def blocked(pw: str, h: str, s: Settings | None = None) -> bool:
        release.wait(10)
        return True

    monkeypatch.setattr(security, "verify_password", blocked)
    return release


async def test_a_hash_that_cannot_get_the_pool_in_time_is_a_503(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from avp_api.errors import ServiceUnavailable  # noqa: PLC0415

    await _sign_up(client, "busy@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    release = await _blocked_pool(monkeypatch, settings)
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 0.2)
    holder = asyncio.create_task(security.verify_password_async("p", "h", settings))
    await asyncio.sleep(0.1)
    try:
        with pytest.raises(ServiceUnavailable):
            await security.hash_password_async("another-password", settings)
        resp = await _login(client, "busy@f5b.example", PASSWORD)
        assert resp.status_code == 503, resp.text
        assert resp.json()["type"].endswith("service-unavailable")
    finally:
        release.set()
        await holder


async def test_a_login_waiting_for_the_pool_holds_no_database_connection(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    await _sign_up(client, "queued@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    release = await _blocked_pool(monkeypatch, settings)
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 30)
    logins = [
        asyncio.create_task(_login(client, "queued@f5b.example", PASSWORD, ip=f"198.51.100.{i}"))
        for i in range(1, 4)
    ]
    await asyncio.sleep(0.6)  # one is hashing, two are queued for the slot
    try:
        assert not any(t.done() for t in logins)
        assert engine.pool.checkedout() == 0
    finally:
        release.set()
    assert [r.status_code for r in await asyncio.gather(*logins)] == [200, 200, 200]


async def test_a_failed_login_logs_the_matched_id_without_a_second_lookup(
    client: AsyncClient, engine  # noqa: ANN001
) -> None:
    from sqlalchemy import event  # noqa: PLC0415

    me = await _sign_up(client, "once@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    statements: list[str] = []

    def count(conn, cursor, statement, *a):  # noqa: ANN001, ANN002, ANN202
        if statement.lstrip().upper().startswith("SELECT") and "FROM users" in statement:
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        with structlog.testing.capture_logs() as events:
            resp = await _login(client, "once@f5b.example", "wrong-password")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert resp.status_code == 401
    assert [e["user_id"] for e in events if e["event"] == "auth.login.failed"] == [me["user"]["id"]]
    assert len(statements) == 1, statements
    assert "user_id" not in resp.text
    assert me["user"]["id"] not in resp.text


async def test_resending_an_invitation_revokes_before_it_writes_the_user(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from avp_api.models import UserRole  # noqa: PLC0415
    from avp_api.services import invitations as inv  # noqa: PLC0415

    owner = await _sign_up(client, "own@f5b.example")
    agency_id, owner_id = owner["agency"]["id"], owner["user"]["id"]
    first = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations",
        json={"email": "re@f5b.example", "role": "member"},
    )
    assert first.status_code == 201, first.text

    order: list[str] = []
    real_revoke = inv._revoke_outstanding  # noqa: SLF001

    async def spy_revoke(*a: Any, **k: Any) -> None:
        order.append("revoke")
        await real_revoke(*a, **k)

    monkeypatch.setattr(inv, "_revoke_outstanding", spy_revoke)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        real_flush = s.flush

        async def spy_flush(*a: Any, **k: Any) -> None:
            order.append("flush")
            await real_flush(*a, **k)

        s.flush = spy_flush  # type: ignore[method-assign]
        await inv.invite_seat(
            s, agency_id=agency_id, invited_by_user_id=owner_id, inviter_role=UserRole.OWNER,
            email="re@f5b.example", role=UserRole.MEMBER, settings=settings,
        )
        await s.rollback()
    assert order[:2] == ["revoke", "flush"], order


async def test_a_password_accept_landing_before_googles_write_leaves_no_password(
    client: AsyncClient, engine, settings: Settings  # noqa: ANN001
) -> None:
    from avp_api.models import UserStatus  # noqa: PLC0415
    from avp_api.services import google_oauth  # noqa: PLC0415
    from avp_api.services import invitations as inv  # noqa: PLC0415

    agency_id = (await _sign_up(client, "g-owner@f5b.example"))["agency"]["id"]
    assert (
        await client.post(
            f"{BASE}/agencies/{agency_id}/invitations",
            json={"email": "g-seat@f5b.example", "role": "member"},
        )
    ).status_code == 201
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    token = "f5b-google-" + "w" * 32
    async with factory() as s:
        row = (
            await s.execute(select(Invitation).where(Invitation.agency_id == agency_id,
                                                     Invitation.accepted_at.is_(None)))
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()

    class AcceptLandsFirst:
        """Before the first locking SELECT, a password accept commits from another session."""

        def __init__(self, inner: Any) -> None:
            self.inner, self.fired = inner, False

        async def execute(self, stmt: Any, *a: Any, **k: Any) -> Any:
            if not self.fired and getattr(stmt, "_for_update_arg", None) is not None:
                self.fired = True
                async with factory() as other:
                    await inv.accept_invitation(
                        other,
                        redis=redis_module.get_redis(settings),
                        token=token,
                        full_name="Pw",
                        password=PASSWORD,
                        settings=settings,
                    )
                    await other.commit()
            return await self.inner.execute(stmt, *a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    identity = google_oauth.GoogleIdentity(
        sub="sub-g-seat", email="g-seat@f5b.example", email_verified=True, name="G", nonce="n"
    )
    async with factory() as s:
        racing = AcceptLandsFirst(s)
        resolved = await google_oauth.resolve_identity(racing, identity, settings=settings)  # type: ignore[arg-type]
        await s.commit()
    assert racing.fired and resolved is not None and resolved.linked
    assert resolved.password_cleared
    async with factory() as s:
        u = (await s.execute(select(User).where(User.email == "g-seat@f5b.example"))).scalar_one()
    assert u.google_sub == "sub-g-seat" and u.password_hash is None
    assert u.status is UserStatus.ACTIVE


# --- second re-check: read-backs, pool waits, removed seats' reset links -----------


async def _invited_with_token(
    client: AsyncClient, factory: Any, agency_id: str, email: str, token: str
) -> None:
    resp = await client.post(
        f"{BASE}/agencies/{agency_id}/invitations", json={"email": email, "role": "member"}
    )
    assert resp.status_code == 201, resp.text
    async with factory() as s:
        row = (
            await s.execute(
                select(Invitation).where(
                    Invitation.agency_id == agency_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        row.token_digest = token_digest(token)
        await s.commit()


async def test_a_google_link_after_the_accepts_commit_leaves_it_no_session(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from avp_api.sessions import SessionStore  # noqa: PLC0415

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    agency_id = (await _sign_up(client, "la-owner@f5b.example"))["agency"]["id"]
    token = "f5b-la-" + "e" * 32
    await _invited_with_token(client, factory, agency_id, "la-seat@f5b.example", token)
    await client.post(f"{BASE}/auth/logout")

    minted: list[str] = []
    real_create = SessionStore.create

    async def create_then_link(self: SessionStore, **kw: Any) -> Any:
        out = await real_create(self, **kw)
        minted.append(out[0])
        async with factory() as other:  # the Google link commits after the accept's commit
            row = (
                await other.execute(select(User).where(User.email == "la-seat@f5b.example"))
            ).scalar_one()
            row.google_sub = "sub-la"
            row.password_hash = None
            await other.commit()
        return out

    monkeypatch.setattr(SessionStore, "create", create_then_link)
    resp = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": token, "fullName": "Jo", "password": PASSWORD},
    )
    assert resp.status_code == 400, resp.text
    assert "set-cookie" not in resp.headers
    assert len(minted) == 1
    client.cookies.set(settings.session_cookie_key, minted[0])
    assert (await client.get(f"{BASE}/auth/me")).status_code == 401


async def test_a_seat_removed_while_the_login_waits_for_the_pool_gets_no_session(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    me = await _sign_up(client, "lc@f5b.example")
    await client.post(f"{BASE}/auth/logout")
    r = redis_module.get_redis(settings)
    before = {k async for k in r.scan_iter("sess:*")}
    release = await _blocked_pool(monkeypatch, settings)
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 30)
    task = asyncio.create_task(_login(client, "lc@f5b.example", PASSWORD))
    await asyncio.sleep(0.4)
    async with factory() as s:
        row = (await s.execute(select(User).where(User.id == me["user"]["id"]))).scalar_one()
        row.deleted_at = datetime.now(UTC)
        await s.commit()
    release.set()
    resp = await task
    assert resp.status_code == 401, resp.text
    assert {k async for k in r.scan_iter("sess:*")} == before


async def test_a_signup_and_a_change_password_waiting_for_the_pool_hold_no_connection(
    client: AsyncClient, engine, settings: Settings, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    await _sign_up(client, "cp@f5b.example")  # signed in as this user
    monkeypatch.setattr(settings, "argon2_max_concurrency", 1)
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 30)
    release = threading.Event()

    def blocked_hash(pw: str, s: Settings | None = None) -> str:
        release.wait(10)
        return "unused"

    def blocked_verify(pw: str, h: str, s: Settings | None = None) -> bool:
        release.wait(10)
        return False

    monkeypatch.setattr(security, "hash_password", blocked_hash)
    monkeypatch.setattr(security, "verify_password", blocked_verify)
    signups = [
        asyncio.create_task(
            client.post(
                f"{BASE}/auth/sign-up",
                json={"agencyName": "Agency", "fullName": "Person", "email": f"w{i}@f5b.example",
                      "password": PASSWORD},
                headers={IP_HEADER: f"198.51.100.{i}"},
            )
        )
        for i in range(1, 3)
    ]
    changes = [
        asyncio.create_task(
            client.post(
                f"{BASE}/auth/change-password",
                json={"currentPassword": PASSWORD, "newPassword": "a-different-passphrase-1"},
            )
        )
        for _ in range(2)
    ]
    await asyncio.sleep(0.6)
    try:
        assert not any(t.done() for t in signups + changes), [
            t.result().text for t in signups + changes if t.done()
        ]
        assert engine.pool.checkedout() == 0
    finally:
        release.set()
    await asyncio.gather(*signups, *changes, return_exceptions=True)


async def test_a_reset_link_minted_before_a_removal_is_refused_after_the_re_invite(
    client: AsyncClient, engine, settings: Settings  # noqa: ANN001
) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from avp_api import ids  # noqa: PLC0415
    from avp_api.models import PasswordResetToken  # noqa: PLC0415
    from avp_api.services import invitations as inv  # noqa: PLC0415

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    agency_id = (await _sign_up(client, "rs-owner@f5b.example"))["agency"]["id"]
    email = "rs-seat@f5b.example"
    await _invited_with_token(client, factory, agency_id, email, "f5b-rs1-" + "a" * 32)
    await client.post(f"{BASE}/auth/logout")
    first = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": "f5b-rs1-" + "a" * 32, "fullName": "Jo", "password": PASSWORD},
    )
    assert first.status_code == 200, first.text
    raw = "f5b-reset-" + "r" * 32
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(PasswordResetToken(
            id=ids.new_id(ids.PASSWORD_RESET), user_id=user.id, token_digest=token_digest(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await s.commit()
        await inv.release_seat(s, agency_id=agency_id, user=user)
        await s.commit()
    # The owner signs back in to re-invite; the person accepts with a fresh password.
    assert (await _login(client, "rs-owner@f5b.example", PASSWORD)).status_code == 200
    await _invited_with_token(client, factory, agency_id, email, "f5b-rs2-" + "b" * 32)
    await client.post(f"{BASE}/auth/logout")
    second = await client.post(
        f"{BASE}/auth/invitations/accept",
        json={"token": "f5b-rs2-" + "b" * 32, "fullName": "Jo", "password": PASSWORD},
    )
    assert second.status_code == 200, second.text
    confirm = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": raw, "newPassword": "an-entirely-different-passphrase"},
    )
    assert confirm.status_code == 400, confirm.text


# --- 3.129: the accept's hash waits outside its row locks and holds no connection ----------------
#
# The accept waits for a hashing slot for up to ARGON2_QUEUE_TIMEOUT_SECONDS, and used to wait with
# the invitation and the user locked and a pooled connection checked out. These tests park a
# request in the hash (the fake below stands in for `hash_password_async` in the service) and look
# at the database from outside while it waits.

LOCK_NOT_AVAILABLE = "55P03"  # what Postgres answers when `FOR UPDATE NOWAIT` finds the row held
REFUSAL = (
    "That invitation link is not valid. Links work once and expire "
    "after seven days. Ask whoever invited you to send a new one."
)


class HashGate:
    """`hash_password_async` for one service module, parking every caller until it is let go.

    A request parked here is where `accept_invitation` used to wait for a hashing slot. Once let
    go, the real hash runs, so what the request goes on to do is the real thing.
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
    monkeypatch.setattr(invitation_service, "hash_password_async", parked)
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


async def _pending_seat(
    client: AsyncClient, engine: AsyncEngine, name: str, token: str
) -> tuple[str, str, str]:
    """An owner signed in on `client`, and one seat invited with this token.

    Returns the agency's, the seat's and the invitation's ids.
    """
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    agency_id = (await _sign_up(client, f"{name}-owner@f5b.example"))["agency"]["id"]
    email = f"{name}-seat@f5b.example"
    await _invited_with_token(client, factory, agency_id, email, token)
    async with factory() as s:
        user_id = (await s.execute(select(User.id).where(User.email == email))).scalar_one()
        invitation_id = (
            await s.execute(
                select(Invitation.id).where(Invitation.token_digest == token_digest(token))
            )
        ).scalar_one()
    return agency_id, user_id, invitation_id


async def _accept(client: AsyncClient, token: str, password: str = PASSWORD) -> Response:
    """The person holding the link, in a browser of their own: no cookies, the allowed origin."""
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app),  # type: ignore[attr-defined]  # noqa: SLF001
        base_url="http://testserver",
        headers={"Origin": TEST_ORIGIN},
    ) as visitor:
        return await visitor.post(
            f"{BASE}/auth/invitations/accept",
            json={"token": token, "fullName": "Jo", "password": password},
        )


async def test_an_accept_waiting_for_the_hash_holds_no_row_lock_and_no_connection(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: HashGate
) -> None:
    token = "f5b-hold-" + "h" * 32
    _, user_id, _ = await _pending_seat(client, engine, "hold", token)

    accepting = asyncio.create_task(_accept(client, token))
    try:
        await gate.until_parked()
        assert not accepting.done(), "the request did not wait for the hash"
        # Another connection can lock the invitation's row and the seat's row at once ...
        assert await _row_is_free(engine, "invitations", "token_digest", token_digest(token))
        assert await _row_is_free(engine, "users", "id", user_id)
        # ... and this request holds no connection while it waits.
        assert engine.pool.checkedout() == 0
    finally:
        gate.let_go()
        # If an assertion above failed, the request still ends before the test does.
        await asyncio.gather(accepting, return_exceptions=True)

    accepted = accepting.result()
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["status"] == "active"
    assert settings.session_cookie_key in accepted.cookies, "the new seat is not signed in"
    assert await _claims(settings) == []


async def test_the_accept_takes_no_lock_before_the_hash_and_the_invitation_before_the_seat(
    client: AsyncClient, engine: AsyncEngine, gate: HashGate
) -> None:
    token = "f5b-order-" + "o" * 32
    await _pending_seat(client, engine, "order", token)

    # Every statement the accept sends, in order, split at the moment it parks in the hash.
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        accepting = asyncio.create_task(_accept(client, token))
        try:
            await gate.until_parked()
            parked_after = len(statements)
        finally:
            gate.let_go()
            await asyncio.gather(accepting, return_exceptions=True)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert accepting.result().status_code == 200

    # Nothing is locked before the hash ...
    assert [s for s in statements[:parked_after] if "FOR UPDATE" in s] == []
    # ... and after it the invitation's row is locked, then the seat's, then the seat is written:
    # the order F5b settled, which `invite_seat`, `release_seat` and the Google sign-in keep too.
    after = statements[parked_after:]
    locks = [i for i, s in enumerate(after) if "FOR UPDATE" in s]
    assert len(locks) == 2, after
    assert "FROM invitations" in after[locks[0]] and "FROM users" in after[locks[1]], after
    user_writes = [i for i, s in enumerate(after) if s.startswith("UPDATE users")]
    assert user_writes and locks[1] < user_writes[0], after


@pytest.mark.parametrize(
    "meanwhile", ["accepted", "revoked", "expired", "re_sent", "taken_by_google", "removed"]
)
async def test_an_invitation_that_stops_being_good_during_the_hash_is_refused_after_it(
    meanwhile: str, client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: HashGate
) -> None:
    token = "f5b-meanwhile-" + "m" * 32
    agency_id, user_id, invitation_id = await _pending_seat(client, engine, "meanwhile", token)
    this_link = Invitation.token_digest == token_digest(token)
    now = datetime.now(UTC)

    accepting = asyncio.create_task(_accept(client, token))
    fresh = ""
    try:
        await gate.until_parked()
        # The invitation was good when the request looked at it. Now, committed from elsewhere
        # while the request waits for its hash, it stops being good in one of the ways it can.
        if meanwhile == "accepted":
            await _commit_elsewhere(
                engine, update(Invitation).where(this_link).values(accepted_at=now)
            )
        elif meanwhile == "revoked":
            await _commit_elsewhere(
                engine, update(Invitation).where(this_link).values(revoked_at=now)
            )
        elif meanwhile == "expired":
            await _commit_elsewhere(
                engine,
                update(Invitation).where(this_link).values(expires_at=now - timedelta(seconds=1)),
            )
        elif meanwhile == "re_sent":
            # The owner sends the invitation again, which revokes this link and mints another.
            resent = await asyncio.wait_for(
                client.post(
                    f"{BASE}/agencies/{agency_id}/invitations",
                    json={"email": "meanwhile-seat@f5b.example", "role": "member"},
                ),
                5,
            )
            assert resent.status_code == 201, resent.text
            fresh = resent.json()["acceptUrl"].partition("#")[2]
            assert fresh and fresh != token
        elif meanwhile == "taken_by_google":
            # What a Google sign-in does to the seat. (It marks the invitation accepted too; this
            # leaves the invitation alone, so only the check on the seat can refuse it.)
            await _commit_elsewhere(
                engine,
                update(User)
                .where(User.id == user_id)
                .values(status=UserStatus.ACTIVE, password_hash=None, google_sub="sub-meanwhile"),
            )
        else:
            await _commit_elsewhere(
                engine, update(User).where(User.id == user_id).values(deleted_at=now)
            )
        invitation_before = await _stored(engine, "invitations", invitation_id)
        user_before = await _stored(engine, "users", user_id)
    finally:
        gate.let_go()
        # If anything above failed, the request still ends before the test does.
        await asyncio.gather(accepting, return_exceptions=True)

    refused = accepting.result()
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == "/problems/invalid-invitation"
    assert refused.json()["detail"] == REFUSAL
    assert "set-cookie" not in refused.headers
    # The one refusal, the same as for a link that never existed.
    unknown = await _accept(client, "never-minted-" + "n" * 30)
    assert refused.json() == unknown.json()
    # And nothing was written: not one column of the invitation's row or the seat's.
    assert await _stored(engine, "invitations", invitation_id) == invitation_before
    assert await _stored(engine, "users", user_id) == user_before
    assert await _claims(settings) == []  # a refusal after the hash gives the claim up too
    if meanwhile == "re_sent":
        # The new link was not touched by the refusal: it still works.
        assert (await _accept(client, fresh)).status_code == 200


@pytest.mark.parametrize("kind", ["unknown", "expired", "revoked", "removed"])
async def test_an_invitation_that_was_never_good_is_refused_before_the_hash(
    kind: str, client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: HashGate
) -> None:
    # The route needs no session, so a junk link must cost no hashing slot: it is refused on the
    # first look, and the hash is never asked for.
    token = "f5b-never-" + "v" * 32
    _, user_id, _ = await _pending_seat(client, engine, "never", token)
    this_link = Invitation.token_digest == token_digest(token)
    now = datetime.now(UTC)
    if kind == "unknown":
        token = "never-minted-" + "n" * 30
    elif kind == "expired":
        past = now - timedelta(seconds=1)
        await _commit_elsewhere(
            engine, update(Invitation).where(this_link).values(expires_at=past)
        )
    elif kind == "revoked":
        await _commit_elsewhere(
            engine, update(Invitation).where(this_link).values(revoked_at=now)
        )
    else:
        await _commit_elsewhere(
            engine, update(User).where(User.id == user_id).values(deleted_at=now)
        )

    try:
        refused = await asyncio.wait_for(_accept(client, token), 5)
    finally:
        gate.let_go()
    assert refused.status_code == 400, refused.text
    assert refused.json()["type"] == "/problems/invalid-invitation"
    assert gate.parked == 0, "an invitation that was never good reached the hash"
    assert await _claims(settings) == []  # a refusal on the first look gives the claim up too


async def test_two_accepts_that_both_hold_a_claim_still_give_exactly_one_success(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The claim keeps a second request out of the hash. It is not what makes an invitation single
    # use: a claim has a lifetime, and a holder that outlives it meets a request with a claim of
    # its own. So every claim is granted here, as two expired ones would be, and the row locks and
    # the decision under them have to give exactly one success on their own.
    async def everyone_holds_a_claim(*args: Any, **kwargs: Any) -> str:
        return "a-claim-that-has-already-ended"

    monkeypatch.setattr(rate_limit, "claim_redemption", everyone_holds_a_claim)
    token = "f5b-race-" + "r" * 32
    await _pending_seat(client, engine, "race", token)
    redis = redis_module.get_redis(settings)
    sessions_before = {k async for k in redis.scan_iter("sess:*")}

    # Both requests get a connection at once, so the second one's decision lands inside the window
    # the winner holds open and does not wait on a cold connect.
    async def hold_a_connection() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_sleep(0.3)"))

    await asyncio.gather(*(hold_a_connection() for _ in range(4)))

    real_commit = AsyncSession.commit

    async def slow_commit(self: AsyncSession) -> None:
        await asyncio.sleep(0.3)
        await real_commit(self)

    first = asyncio.create_task(_accept(client, token, PASSWORD))
    second = asyncio.create_task(_accept(client, token, "a-different-passphrase-1"))
    try:
        # Both have looked at the invitation while it was good, and both wait in the hash.
        await gate.until_parked(2)
        # They are let go together, and the commit of whichever locks first is held for 0.3 s
        # with the rows still locked. The other can only be refused by the decision it takes under
        # the locks: a decision taken without them reads the open invitation, and both succeed.
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", slow_commit)
            gate.let_go()
            await asyncio.gather(first, second, return_exceptions=True)
    finally:
        gate.let_go()
        # If anything above failed, both requests still end before the test does.
        await asyncio.gather(first, second, return_exceptions=True)

    results = [first.result(), second.result()]
    assert sorted(r.status_code for r in results) == [200, 400], [r.text for r in results]
    refused = next(r for r in results if r.status_code == 400)
    assert refused.json()["type"] == "/problems/invalid-invitation"
    assert refused.json()["detail"] == REFUSAL
    # One seat was filled, once: one session was minted, and the seat has one password.
    assert len({k async for k in redis.scan_iter("sess:*")} - sessions_before) == 1
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        seats = (
            await s.execute(select(User).where(User.email == "race-seat@f5b.example"))
        ).scalars().all()
    assert len(seats) == 1 and seats[0].status is UserStatus.ACTIVE


# --- 3.129: one hash per invitation in flight: the claim in Redis --------------------------------
#
# The route has no limit of its own, and every request that passed the first look used to hash. A
# request now claims the invitation in Redis before it reads it: one gets the claim and goes on,
# and the others are refused at once as an accepted invitation is, having read and hashed nothing.
# The claim is released however the request ends, and has a lifetime if its holder dies.


async def test_five_accepts_at_once_with_one_invitation_hash_exactly_once_and_one_succeeds(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, gate: HashGate
) -> None:
    token = "f5b-five-" + "f" * 32
    await _pending_seat(client, engine, "five", token)
    redis = redis_module.get_redis(settings)
    sessions_before = {k async for k in redis.scan_iter("sess:*")}

    # Four connections opened and given back, so the winner starts with an idle one.
    async def hold_a_connection() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("select pg_sleep(0.3)"))

    await asyncio.gather(*(hold_a_connection() for _ in range(4)))

    passwords = [f"a-different-passphrase-{n}" for n in range(5)]
    requests = [asyncio.create_task(_accept(client, token, password)) for password in passwords]
    try:
        # One request got the claim and waits in the hash. The other four are refused without
        # reaching it: they end, and the hash has been asked for once.
        await gate.until_parked()
        async with asyncio.timeout(10):
            while sum(request.done() for request in requests) < 4 and gate.parked < 2:
                await asyncio.sleep(0.01)
        assert gate.parked == 1, f"{gate.parked} hashes ran for one invitation"
        # While it waits the claim is held, with a lifetime.
        (held,) = await _claims(settings)
        assert 0 < await redis.pttl(held) <= rate_limit.redeem_claim_ttl_ms(settings)
    finally:
        gate.let_go()
        await asyncio.gather(*requests, return_exceptions=True)

    responses = [request.result() for request in requests]
    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400, 400]
    # Every other request got the body of an invitation that was never issued.
    unknown = await _accept(client, "never-minted-" + "n" * 30)
    assert unknown.status_code == 400
    for refused in (r for r in responses if r.status_code == 400):
        assert refused.json() == unknown.json()
        assert refused.json()["detail"] == REFUSAL
        assert "set-cookie" not in refused.headers
    assert gate.parked == 1
    assert await _claims(settings) == []  # and the claim is gone
    # One seat was filled, once: one session was minted, and the seat has one password.
    assert len({k async for k in redis.scan_iter("sess:*")} - sessions_before) == 1
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        seats = (
            await s.execute(select(User).where(User.email == "five-seat@f5b.example"))
        ).scalars().all()
    assert len(seats) == 1 and seats[0].status is UserStatus.ACTIVE


async def test_a_claim_left_by_a_holder_that_died_ends_at_its_lifetime_and_not_before(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "f5b-dead-" + "d" * 32
    _, user_id, invitation_id = await _pending_seat(client, engine, "dead", token)
    redis = redis_module.get_redis(settings)
    # The lifetime is the queue timeout plus the margin: shortened, so the test can wait it out.
    monkeypatch.setattr(settings, "argon2_queue_timeout_seconds", 0.25)
    monkeypatch.setattr(settings, "redeem_claim_margin_seconds", 0.25)
    assert rate_limit.redeem_claim_ttl_ms(settings) == 500

    # A request took the claim and died: nothing will ever release it.
    held = await rate_limit.claim_redemption(
        redis, settings, kind="invite", digest=token_digest(token)
    )
    assert held is not None
    (key,) = await _claims(settings)
    assert key.startswith("rl:redeem_invite:token:")
    assert 0 < await redis.pttl(key) <= 500

    # Until it ends, the invitation is refused as an accepted one is, and nothing is read, hashed
    # or written: not one statement reaches the database, because the claim comes before the
    # first look.
    invitation_before = await _stored(engine, "invitations", invitation_id)
    user_before = await _stored(engine, "users", user_id)
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        # (Bounded: a request that got past the claim would wait in the hash, not answer.)
        refused = await asyncio.wait_for(_accept(client, token), 5)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        gate.let_go()
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == REFUSAL
    assert statements == []
    assert gate.parked == 0
    assert await _stored(engine, "invitations", invitation_id) == invitation_before
    assert await _stored(engine, "users", user_id) == user_before

    # Then it ends on its own, and the same invitation can be accepted.
    await asyncio.sleep(0.7)
    assert await _claims(settings) == []
    accepted = await _accept(client, token)
    assert accepted.status_code == 200, accepted.text


async def test_with_redis_down_an_accept_is_a_503_that_reads_hashes_and_writes_nothing(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    gate: HashGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "f5b-down-" + "w" * 32
    _, user_id, invitation_id = await _pending_seat(client, engine, "down", token)
    redis = redis_module.get_redis(settings)
    sessions_before = {k async for k in redis.scan_iter("sess:*")}
    invitation_before = await _stored(engine, "invitations", invitation_id)
    user_before = await _stored(engine, "users", user_id)

    async def redis_is_away(self: Redis, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("Redis is away")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Redis, "eval", redis_is_away)
            # (Bounded: a request that got past the claim would wait in the hash, not answer.)
            refused = await asyncio.wait_for(_accept(client, token), 5)
    finally:
        gate.let_go()

    # What `errors.py` answers for a Redis that cannot be reached: a 503, not the link's refusal.
    assert refused.status_code == 503, refused.text
    assert refused.json()["type"] == "/problems/service-unavailable"
    assert refused.json()["detail"] == "This is temporarily unavailable. Please try again shortly."
    assert "set-cookie" not in refused.headers
    assert gate.parked == 0
    assert await _stored(engine, "invitations", invitation_id) == invitation_before
    assert await _stored(engine, "users", user_id) == user_before
    assert {k async for k in redis.scan_iter("sess:*")} == sessions_before
    # The invitation was not spent, and works once Redis is back.
    assert (await _accept(client, token)).status_code == 200


async def test_a_claim_that_cannot_be_released_does_not_fail_the_accept(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "f5b-stuck-" + "s" * 32
    await _pending_seat(client, engine, "stuck", token)
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
        accepted = await _accept(client, token)

    # The outcome was decided before the release, so it stands; the claim ends by itself.
    assert accepted.status_code == 200, accepted.text
    (left,) = await _claims(settings)
    assert 0 < await redis.pttl(left) <= rate_limit.redeem_claim_ttl_ms(settings)
    # One warning, naming the kind and nothing else: not the token, not its digest.
    warned = [e for e in events if e["event"] == "redeem.claim.release_unavailable"]
    assert len(warned) == 1 and warned[0]["kind"] == "invite"
    logged = json.dumps(events, default=str)
    assert token not in logged and token_digest(token) not in logged


async def test_a_hash_that_gets_no_slot_releases_the_claim_and_the_invitation_still_works(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "f5b-noslot-" + "n" * 32
    _, user_id, invitation_id = await _pending_seat(client, engine, "noslot", token)
    invitation_before = await _stored(engine, "invitations", invitation_id)
    user_before = await _stored(engine, "users", user_id)

    async def no_slot(password: str, settings: Settings | None = None) -> str:
        raise ServiceUnavailable(detail="This is temporarily unavailable. Try again shortly.")

    with monkeypatch.context() as patch:
        patch.setattr(invitation_service, "hash_password_async", no_slot)
        refused = await _accept(client, token)

    assert refused.status_code == 503, refused.text
    # Given up on the way out, so the person can try again at once; nothing was written.
    assert await _claims(settings) == []
    assert await _stored(engine, "invitations", invitation_id) == invitation_before
    assert await _stored(engine, "users", user_id) == user_before
    assert (await _accept(client, token)).status_code == 200
