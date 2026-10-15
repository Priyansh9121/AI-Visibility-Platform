"""Uniform auth messages and timing (hardening item A1, decision D9).

Three refusals that must not tell a stranger anything, and must not take a different time to say it:

1. SIGN-IN. One sentence, "Email or password is incorrect.", for an unknown address, a wrong
   password, an account with no password (Google-only) and an account that is not active. Each of
   those paths runs exactly ONE Argon2 verification at the configured cost, after the same single
   database read. The proof is structural (a spy on the verify and on the dummy verify, and on the
   statements the database was sent), because a wall-clock comparison of microsecond-scale work
   flakes. See the module notes at the end for what the structure cannot show.
2. RESET REQUEST. With email off it does nothing at all, for every address: no lookup, no mint, no
   retire (so a link the operator issued by hand survives anybody pressing "Forgot password").
   With email on, nothing that depends on the address happens before the response; the lookup,
   the mint and the send run in one background task after it.
3. SIGN-UP for an address that has an account: one neutral 409 of a type that does not name
   registration, from an insert that the unique index refuses (no pre-check), so two sign-ups
   for one address at once also answer 409 and never 500. The attempted agency leaves nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from argon2 import PasswordHasher, extract_parameters
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from avp_api import ids, security
from avp_api.config import Settings
from avp_api.errors import SIGN_UP_NOT_POSSIBLE, InvalidCredentials, SignUpNotPossible
from avp_api.models import Agency, PasswordResetToken, User, UserStatus
from avp_api.routers import auth as auth_router
from avp_api.security import hash_password, token_digest
from avp_api.services import auth as auth_service
from avp_api.services import email as email_service
from avp_api.services import password_reset as reset_service

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
SENTENCE = "Email or password is incorrect."
EMAIL_OFF = "Password reset by email is not available on this service yet, so no link was sent."
EMAIL_ON = "If an account exists for that email, we've sent a link."

KNOWN = "known@a1.example"
GOOGLE = "google-only@a1.example"
SUSPENDED = "suspended@a1.example"
UNKNOWN = "nobody@a1.example"


# --- helpers --------------------------------------------------------------------------------


def _factory(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def _sign_up(
    client: AsyncClient, email: str, agency: str = "A1 Agency"
) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Owner", "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@contextmanager
def _statements(engine: AsyncEngine) -> Iterator[list[str]]:
    """Every statement the database is sent while the block runs, whitespace collapsed."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        seen.append(" ".join(statement.split()))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def _accounts(client: AsyncClient, engine: AsyncEngine) -> None:
    """A password account, a Google-only one and a suspended one."""
    await _sign_up(client, KNOWN, "Known Agency")
    await _sign_up(client, GOOGLE, "Google Agency")
    await _sign_up(client, SUSPENDED, "Suspended Agency")
    async with _factory(engine)() as s:
        await s.execute(
            update(User)
            .where(User.email == GOOGLE)
            .values(password_hash=None, google_sub="google-subject-a1")
        )
        await s.execute(
            update(User).where(User.email == SUSPENDED).values(status=UserStatus.SUSPENDED)
        )
        await s.commit()
    await client.post(f"{BASE}/auth/logout")


def _login(client: AsyncClient, email: str, password: str):  # noqa: ANN202
    return client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


# The four refusals: (label, email, password). The suspended account is given its RIGHT
# password, which is the path that verifies successfully and only then refuses.
REFUSALS = [
    ("unknown-address", UNKNOWN, "not-the-password-at-all"),
    ("wrong-password", KNOWN, "not-the-password-at-all"),
    ("google-only", GOOGLE, PASSWORD),
    ("suspended-right-password", SUSPENDED, PASSWORD),
]


# --- 1. sign-in -----------------------------------------------------------------------------


class TestSignInRefusal:
    async def test_every_refusal_is_the_one_sentence_with_the_same_body(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        await _accounts(client, engine)
        bodies = []
        for _label, email, password in REFUSALS:
            resp = await _login(client, email, password)
            assert resp.status_code == 401, resp.text
            doc = resp.json()
            assert doc["type"] == "/problems/invalid-credentials"
            assert doc["title"] == SENTENCE
            assert doc["detail"] == SENTENCE
            bodies.append(resp.content)
        assert len(set(bodies)) == 1, "the refusals differ"

    async def test_each_refusal_runs_one_argon2_verify_at_the_configured_cost(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One verification, same cost, whatever the cause: the structural timing proof."""
        await _accounts(client, engine)
        work: list[tuple[str, tuple[int, int, int]]] = []
        hashed: list[int] = []

        def cost(stored: str) -> tuple[int, int, int]:
            p = extract_parameters(stored)
            return (p.time_cost, p.memory_cost, p.parallelism)

        # The dummy is built once (as the start-up does) so that a refusal only verifies.
        dummy = security._dummy_hash(settings)  # noqa: SLF001
        real_verify, real_hash = PasswordHasher.verify, PasswordHasher.hash

        def spy_verify(self: PasswordHasher, stored: str, password: str) -> bool:
            work.append(("dummy" if stored == dummy else "verify", cost(stored)))
            return real_verify(self, stored, password)

        def spy_hash(self: PasswordHasher, password: str, **kw: Any) -> str:
            hashed.append(1)
            return real_hash(self, password, **kw)

        monkeypatch.setattr(PasswordHasher, "verify", spy_verify)
        monkeypatch.setattr(PasswordHasher, "hash", spy_hash)

        configured = (
            settings.argon2_time_cost,
            settings.argon2_memory_cost_kib,
            settings.argon2_parallelism,
        )
        kinds: dict[str, str] = {}
        for label, email, password in REFUSALS:
            work.clear()
            hashed.clear()
            assert (await _login(client, email, password)).status_code == 401
            assert len(work) == 1, f"{label}: {work}"
            assert hashed == [], f"{label}: a refusal hashed something"
            assert work[0][1] == configured, f"{label}: cost {work[0][1]} is not {configured}"
            kinds[label] = work[0][0]
        # No hash to check (unknown, Google-only) burns the dummy; the others check the real one.
        assert kinds == {
            "unknown-address": "dummy",
            "wrong-password": "verify",
            "google-only": "dummy",
            "suspended-right-password": "verify",
        }

    async def test_each_refusal_sends_the_database_the_same_statements(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        await _accounts(client, engine)
        seen: dict[str, list[str]] = {}
        for label, email, password in REFUSALS:
            with _statements(engine) as sql:
                assert (await _login(client, email, password)).status_code == 401
            seen[label] = list(sql)
        reference = seen["unknown-address"]
        assert reference, "no statement was recorded, so this proves nothing"
        assert len(reference) == 1 and reference[0].startswith("SELECT users.")
        for label, statements in seen.items():
            assert statements == reference, f"{label} sent the database something else"

    async def test_the_dummy_hash_costs_what_the_settings_say(
        self, settings: Settings
    ) -> None:
        """Change an Argon2 parameter and the dummy changes with it (it is cached per set)."""
        for time_cost, memory, parallelism in ((1, 8, 1), (2, 16, 1), (3, 32, 2)):
            tuned = settings.model_copy(
                update={
                    "argon2_time_cost": time_cost,
                    "argon2_memory_cost_kib": memory,
                    "argon2_parallelism": parallelism,
                }
            )
            p = extract_parameters(security._dummy_hash(tuned))  # noqa: SLF001
            assert (p.time_cost, p.memory_cost, p.parallelism) == (time_cost, memory, parallelism)

    async def test_a_hash_from_before_a_parameter_raise_verifies_cheaper_than_the_dummy(
        self, settings: Settings
    ) -> None:
        """A residual the structure cannot close, written down so a change to it is seen.

        A stored hash carries the cost it was made with. After the cost is raised, a user who has
        not signed in since still verifies at the OLD cost, which is cheaper than the dummy's
        (made at the new cost), so that user's wrong password can answer faster than an unknown
        address does. `needs_rehash` is true for it, and a successful sign-in rewrites the hash
        (`authenticate`), so the gap closes per user at their next successful sign-in.
        """
        old = hash_password("anything-at-all", settings)  # made at the test cost (1, 8, 1)
        raised = settings.model_copy(
            update={"argon2_time_cost": 3, "argon2_memory_cost_kib": 32}
        )
        assert security.needs_rehash(old, raised) is True
        stored = extract_parameters(old)
        dummy = extract_parameters(security._dummy_hash(raised))  # noqa: SLF001
        assert (stored.time_cost, stored.memory_cost) == (1, 8)
        assert (dummy.time_cost, dummy.memory_cost) == (3, 32)

    async def test_the_change_password_refusal_keeps_its_own_detail(
        self, client: AsyncClient
    ) -> None:
        """Only the title and default detail changed: the signed-in caller is told which field."""
        await _sign_up(client, KNOWN)
        resp = await client.post(
            f"{BASE}/auth/change-password",
            json={"currentPassword": "not-it-at-all-wrong", "newPassword": "a-brand-new-phrase-1"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "That is not your current password."

    def test_the_class_carries_the_sentence(self) -> None:
        assert InvalidCredentials.title == SENTENCE
        assert InvalidCredentials().detail == SENTENCE


# --- 2. the reset request --------------------------------------------------------------------


@pytest.fixture
def mailbox(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Email on, and a sender that records the recipients and sends nothing."""
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test_key_never_sent"))
    sent: list[str] = []

    async def record(to: str, reset_url: str, *, user_id: str, settings: Settings) -> None:
        sent.append(to)

    monkeypatch.setattr(email_service, "send_password_reset", record)
    return sent


async def _drive(
    app: Any, email: str, engine: AsyncEngine, origin: str
) -> tuple[int, bytes, int, int]:
    """POST the reset request straight at the ASGI app, so the moment the response is out is seen.

    Returns (status, body, statements sent BEFORE the last body chunk, statements sent in all).
    `ASGITransport` waits for background tasks, which would hide the difference.
    """
    path = f"{BASE}/auth/reset-password/request"
    raw = json.dumps({"email": email}).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode()),
                    (b"origin", origin.encode())],
    }
    fed = False
    out: dict[str, Any] = {"status": None, "body": b"", "before": None}
    count = 0

    def record(*_: Any) -> None:
        nonlocal count
        count += 1

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
                out["before"] = count

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await asyncio.wait_for(_app_call(app, scope, receive, send), 10)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return out["status"], out["body"], out["before"], count


async def _app_call(app: Any, scope: Any, receive: Any, send: Any) -> None:
    await app(scope, receive, send)


async def _tokens(engine: AsyncEngine) -> list[PasswordResetToken]:
    async with _factory(engine)() as s:
        return list(
            (await s.execute(select(PasswordResetToken).order_by(PasswordResetToken.id)))
            .scalars()
            .all()
        )


async def _manual_link(engine: AsyncEngine, email: str, raw: str, *, expired: bool = False) -> None:
    async with _factory(engine)() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET),
                user_id=user.id,
                token_digest=token_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=-1 if expired else 1),
            )
        )
        await s.commit()


class TestResetRequestWithEmailOff:
    async def test_it_does_nothing_for_any_address(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        await _sign_up(client, KNOWN)
        app = client._transport.app  # noqa: SLF001
        answers = []
        for address in (KNOWN, UNKNOWN):
            origin = settings.cors_allow_origins[0]
            status, body, before, total = await _drive(app, address, engine, origin)
            assert status == 200
            assert before == 0 and total == 0, "the database was sent a statement"
            answers.append(body)
        assert answers[0] == answers[1]
        doc = json.loads(answers[0])
        assert doc["detail"] == EMAIL_OFF and doc["emailConfigured"] is False
        assert await _tokens(engine) == []

    async def test_the_operators_link_survives_a_forgot_password(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """Anyone knowing an address used to be able to retire the link issued by hand."""
        await _sign_up(client, KNOWN)
        await client.post(f"{BASE}/auth/logout")
        raw = "operator-issued-" + "k" * 32
        await _manual_link(engine, KNOWN, raw)

        resp = await client.post(f"{BASE}/auth/reset-password/request", json={"email": KNOWN})
        assert resp.status_code == 200

        rows = await _tokens(engine)
        assert len(rows) == 1 and rows[0].used_at is None
        confirm = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": "an-entirely-different-passphrase"},
        )
        assert confirm.status_code == 200, confirm.text

    async def test_the_service_still_mints_for_the_operator_script(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        """`scripts/issue_password_reset.py` calls the service, not the route: email off or on."""
        await _sign_up(client, KNOWN)
        async with _factory(engine)() as s:
            minted = await reset_service.request_reset(s, email=KNOWN, settings=settings)
            await s.commit()
        assert minted is not None
        assert len(await _tokens(engine)) == 1


class TestResetRequestWithEmailOn:
    async def test_nothing_that_depends_on_the_address_happens_before_the_response(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings,
        mailbox: list[str],
    ) -> None:
        await _sign_up(client, KNOWN)
        app = client._transport.app  # noqa: SLF001
        seen = {}
        for address in (KNOWN, UNKNOWN):
            seen[address] = await _drive(app, address, engine, settings.cors_allow_origins[0])
        (k_status, k_body, k_before, k_total) = seen[KNOWN]
        (u_status, u_body, u_before, u_total) = seen[UNKNOWN]
        assert k_status == u_status == 200
        assert k_body == u_body
        doc = json.loads(k_body)
        assert doc["detail"] == EMAIL_ON and doc["emailConfigured"] is True
        # The response path sent the database nothing, for either address ...
        assert k_before == 0 and u_before == 0
        # ... and the work happened afterwards: a lookup for a stranger, the mint for a known one.
        assert 0 < u_total < k_total
        assert mailbox == [KNOWN]
        assert len(await _tokens(engine)) == 1

    async def test_the_background_mint_retires_only_live_links_and_keeps_one_live(
        self, client: AsyncClient, engine: AsyncEngine, mailbox: list[str]
    ) -> None:
        await _sign_up(client, KNOWN)
        await _manual_link(engine, KNOWN, "live-link-" + "a" * 32)
        await _manual_link(engine, KNOWN, "expired-link-" + "b" * 32, expired=True)
        for _ in range(2):
            resp = await client.post(
                f"{BASE}/auth/reset-password/request", json={"email": KNOWN}
            )
            assert resp.status_code == 200
        rows = await _tokens(engine)
        assert len(rows) == 4
        live = [r for r in rows if r.used_at is None and r.expires_at > datetime.now(UTC)]
        assert len(live) == 1, "more than one live link for one user"
        expired = [r for r in rows if r.expires_at <= datetime.now(UTC)]
        assert len(expired) == 1 and expired[0].used_at is None, "an expired link was stamped"

    async def test_a_failure_in_the_background_changes_nothing_the_caller_sees(
        self, client: AsyncClient, engine: AsyncEngine, mailbox: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client, KNOWN)

        async def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError(f"driver says {KNOWN}")

        good = await client.post(f"{BASE}/auth/reset-password/request", json={"email": UNKNOWN})
        monkeypatch.setattr(reset_service, "request_reset", boom)
        with structlog.testing.capture_logs() as events:
            bad = await client.post(f"{BASE}/auth/reset-password/request", json={"email": KNOWN})
        assert bad.status_code == 200 and bad.content == good.content
        failed = [e for e in events if e["event"] == "password_reset.request.background_failed"]
        assert len(failed) == 1 and failed[0]["error"] == "RuntimeError"
        assert KNOWN not in json.dumps(events, default=str)

    def test_the_route_no_longer_opens_a_database_session(self) -> None:
        import inspect

        params = inspect.signature(auth_router.request_password_reset).parameters
        assert "db" not in params


# --- 3. sign-up with an address that has an account ---------------------------------------------


def _signup_body(email: str, agency: str) -> dict[str, str]:
    return {"agencyName": agency, "fullName": "Owner", "email": email, "password": PASSWORD}


async def _counts(engine: AsyncEngine) -> tuple[int, int]:
    async with _factory(engine)() as s:
        agencies = (await s.execute(select(func.count()).select_from(Agency))).scalar_one()
        users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
    return agencies, users


class TestSignUpRefusal:
    async def test_the_refusal_is_the_neutral_409_and_leaves_nothing(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        await _sign_up(client, KNOWN, "First Agency")
        before = await _counts(engine)
        resp = await client.post(
            f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Attempted Second Agency")
        )
        assert resp.status_code == 409
        assert resp.headers["content-type"].startswith("application/problem+json")
        doc = resp.json()
        assert doc["type"] == "/problems/sign-up-not-possible"
        assert doc["title"] == doc["detail"] == SIGN_UP_NOT_POSSIBLE
        assert doc["detail"] == (
            "We couldn't create an account with that email. If you already have one, "
            "sign in or reset your password."
        )
        assert "registered" not in resp.text and "already exists" not in resp.text
        assert await _counts(engine) == before
        async with _factory(engine)() as s:
            names = (await s.execute(select(Agency.name))).scalars().all()
        assert "Attempted Second Agency" not in names

    async def test_the_case_and_spacing_of_the_address_do_not_get_round_it(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        await _sign_up(client, KNOWN, "First Agency")
        resp = await client.post(
            f"{BASE}/auth/sign-up", json=_signup_body("  KNOWN@A1.example ", "Other Agency")
        )
        assert resp.status_code == 409
        assert resp.json()["type"] == "/problems/sign-up-not-possible"

    async def test_a_refused_sign_up_sends_the_database_what_a_real_one_does(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        """Same statements, in the same order, up to and including the user insert."""
        await _sign_up(client, KNOWN, "First Agency")
        with _statements(engine) as real:
            ok = await client.post(
                f"{BASE}/auth/sign-up", json=_signup_body("fresh@a1.example", "Real Agency")
            )
        assert ok.status_code == 201
        with _statements(engine) as refused:
            no = await client.post(
                f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Refused Agency")
            )
        assert no.status_code == 409

        def through_user_insert(statements: list[str]) -> list[str]:
            hit = next(i for i, s in enumerate(statements) if s.startswith("INSERT INTO users"))
            return statements[: hit + 1]

        assert through_user_insert(refused) == through_user_insert(real)
        assert len(through_user_insert(refused)) >= 4  # slug lookup, agency, user, + savepoint

    async def test_each_runs_one_hash(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _sign_up(client, KNOWN, "First Agency")
        calls: list[int] = []
        real = security.hash_password

        def spy(password: str, s: Settings | None = None) -> str:
            calls.append(1)
            return real(password, s)

        monkeypatch.setattr(security, "hash_password", spy)
        for email, agency, want in (
            ("fresh@a1.example", "Real Agency", 201),
            (KNOWN, "Refused Agency", 409),
        ):
            calls.clear()
            resp = await client.post(f"{BASE}/auth/sign-up", json=_signup_body(email, agency))
            assert resp.status_code == want
            assert calls == [1], f"{want}: {calls}"

    async def test_two_sign_ups_for_one_address_at_once_answer_201_and_409(
        self, client: AsyncClient, engine: AsyncEngine, capsys, caplog  # noqa: ANN001
    ) -> None:
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as events:
            first, second = await asyncio.gather(
                client.post(f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Agency One")),
                client.post(f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Agency Two")),
            )
        assert sorted([first.status_code, second.status_code]) == [201, 409]
        loser = first if first.status_code == 409 else second
        assert loser.json()["type"] == "/problems/sign-up-not-possible"
        assert KNOWN not in loser.text
        everything = json.dumps(events, default=str) + capsys.readouterr().out + caplog.text
        assert KNOWN not in everything, "the address reached a log"
        assert await _counts(engine) == (1, 1)

    async def test_the_second_insert_waits_for_the_first_then_is_refused_cleanly(
        self, engine: AsyncEngine, settings: Settings
    ) -> None:
        """The race held open: the first sign-up has inserted and not committed."""
        factory = _factory(engine)
        async with factory() as a, factory() as b:
            await auth_service.sign_up_agency(
                a, agency_name="Holder", full_name="A", email=KNOWN, password=PASSWORD,
                settings=settings,
            )
            waiting = asyncio.create_task(
                auth_service.sign_up_agency(
                    b, agency_name="Loser", full_name="B", email=KNOWN, password=PASSWORD,
                    settings=settings,
                )
            )
            await asyncio.sleep(0.5)
            assert not waiting.done(), "the second insert did not wait for the unique index"
            await a.commit()
            with pytest.raises(SignUpNotPossible):
                await waiting
            # The session is usable afterwards and holds nothing of the attempt.
            await b.rollback()
        assert await _counts(engine) == (1, 1)

    async def test_two_sign_ups_with_one_agency_name_at_once_both_succeed_with_distinct_slugs(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        first, second = await asyncio.gather(
            client.post(f"{BASE}/auth/sign-up", json=_signup_body("one@a1.example", "Same Name")),
            client.post(f"{BASE}/auth/sign-up", json=_signup_body("two@a1.example", "Same Name")),
        )
        assert first.status_code == second.status_code == 201, (first.text, second.text)
        slugs = {first.json()["agency"]["slug"], second.json()["agency"]["slug"]}
        assert len(slugs) == 2 and all(x.startswith("same-name") for x in slugs)

    async def test_a_slug_taken_in_the_gap_is_retried_with_the_next_suffix(
        self, engine: AsyncEngine, settings: Settings
    ) -> None:
        """The race held open: the first sign-up has inserted and not committed its slug."""
        factory = _factory(engine)
        async with factory() as a, factory() as b:
            first, _ = await auth_service.sign_up_agency(
                a, agency_name="Same Name", full_name="A", email="one@a1.example",
                password=PASSWORD, settings=settings,
            )
            waiting = asyncio.create_task(
                auth_service.sign_up_agency(
                    b, agency_name="Same Name", full_name="B", email="two@a1.example",
                    password=PASSWORD, settings=settings,
                )
            )
            await asyncio.sleep(0.5)
            assert not waiting.done(), "the second insert did not wait for the first slug"
            await a.commit()
            second, _ = await waiting
            await b.commit()
        assert (first.slug, second.slug) == ("same-name", "same-name-1")

    async def test_a_duplicate_address_racing_a_same_name_sign_up_gets_the_neutral_409(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        results = await asyncio.gather(
            client.post(f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Same Name")),
            client.post(f"{BASE}/auth/sign-up", json=_signup_body(KNOWN, "Same Name")),
            client.post(f"{BASE}/auth/sign-up", json=_signup_body("other@a1.example", "Same Name")),
        )
        assert sorted(r.status_code for r in results) == [201, 201, 409]
        refused = next(r for r in results if r.status_code == 409)
        assert refused.json()["type"] == "/problems/sign-up-not-possible"
        assert await _counts(engine) == (2, 2)

    async def test_the_held_race_for_one_address_and_one_name_is_the_neutral_refusal(
        self, engine: AsyncEngine, settings: Settings
    ) -> None:
        """Slug retried, then the email refuses: the answer is still the neutral one."""
        factory = _factory(engine)
        async with factory() as a, factory() as b:
            await auth_service.sign_up_agency(
                a, agency_name="Same Name", full_name="A", email=KNOWN, password=PASSWORD,
                settings=settings,
            )
            waiting = asyncio.create_task(
                auth_service.sign_up_agency(
                    b, agency_name="Same Name", full_name="B", email=KNOWN, password=PASSWORD,
                    settings=settings,
                )
            )
            await asyncio.sleep(0.5)
            await a.commit()
            with pytest.raises(SignUpNotPossible) as caught:
                await waiting
            await b.rollback()
        assert caught.value.__context__ is None and caught.value.__cause__ is None
        assert await _counts(engine) == (1, 1)

    async def test_any_other_integrity_error_is_a_generic_failure_with_no_driver_message(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A user id that exists: some other constraint refuses. Name and SQLSTATE only."""
        me = await _sign_up(client, KNOWN, "First Agency")
        taken_id = me["user"]["id"]
        real_new_id = ids.new_id
        monkeypatch.setattr(
            ids, "new_id", lambda prefix: taken_id if prefix == ids.USER else real_new_id(prefix)
        )
        with structlog.testing.capture_logs() as events:
            async with _factory(engine)() as s:
                with pytest.raises(auth_service.SignUpFailedError) as caught:
                    await auth_service.sign_up_agency(
                        s, agency_name="Other Agency", full_name="B", email="other@a1.example",
                        password=PASSWORD, settings=settings,
                    )
        assert caught.value.__context__ is None and caught.value.__cause__ is None
        logged = [e for e in events if e["event"] == "auth.sign_up.integrity_error"]
        assert len(logged) == 1
        assert logged[0]["constraint"] in ("pk_users", "users_pkey")
        assert logged[0]["sqlstate"] == "23505"
        assert "other@a1.example" not in json.dumps(events, default=str)

    async def test_the_refusal_carries_no_driver_error(
        self, client: AsyncClient, engine: AsyncEngine, settings: Settings
    ) -> None:
        await _sign_up(client, KNOWN, "First Agency")
        async with _factory(engine)() as s:
            with pytest.raises(SignUpNotPossible) as caught:
                await auth_service.sign_up_agency(
                    s, agency_name="Second Agency", full_name="B", email=KNOWN,
                    password=PASSWORD, settings=settings,
                )
        assert caught.value.__context__ is None and caught.value.__cause__ is None


# --- start-up warms the dummy hash ------------------------------------------------------------


class TestTheDummyHashIsBuiltAtStartUp:
    async def test_the_first_unknown_address_after_start_up_does_one_argon2_operation(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from avp_api import main, redis_watch

        async def nothing(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(main, "dispose_engine", nothing)
        monkeypatch.setattr(main, "close_redis", nothing)
        monkeypatch.setattr(redis_watch, "start", nothing)
        security._dummy_hash_for.cache_clear()  # noqa: SLF001 - a process that has just started

        ops: list[str] = []
        real_verify, real_hash = PasswordHasher.verify, PasswordHasher.hash

        def spy_verify(self: PasswordHasher, stored: str, password: str) -> bool:
            ops.append("verify")
            return real_verify(self, stored, password)

        def spy_hash(self: PasswordHasher, password: str, **kw: Any) -> str:
            ops.append("hash")
            return real_hash(self, password, **kw)

        monkeypatch.setattr(PasswordHasher, "verify", spy_verify)
        monkeypatch.setattr(PasswordHasher, "hash", spy_hash)

        app = main.create_app(settings)
        async with main.lifespan(app):
            assert ops, "start-up did not build the dummy hash"
            ops.clear()
            assert (await _login(client, UNKNOWN, "not-the-password-at-all")).status_code == 401
        assert ops == ["verify"], ops
