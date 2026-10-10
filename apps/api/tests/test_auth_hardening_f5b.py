"""F5b: a login success refunds only the email bucket; login outcomes are logged by id;
Argon2 runs off the loop in a bounded pool; the reset email goes out after the response;
two concurrent invitation accepts of one token give exactly one success."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import redis_client as redis_module
from avp_api import security
from avp_api.config import Settings
from avp_api.models import Invitation, User
from avp_api.security import token_digest
from avp_api.services import email as email_service

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
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # A slow hash holds both requests between their read and their write, so only a row
    # lock (not luck in the timing) can make the second one see the first's commit.
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
                        other, token=token, full_name="Pw", password=PASSWORD, settings=settings
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
