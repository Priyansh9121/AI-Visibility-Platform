"""Login is slowed per address, never locked (hardening A2, the founder's D1 as changed).

"After 5 failures for one email in 15 min, slow that email to one attempt per minute, doubling on
repeats up to 15 min. Same 'Email or password is incorrect.' message and timing. Google sign-in
unaffected; per-IP limits stay. All values configurable."

What these prove, in the order of the file:

1. THE SCHEDULE. Four failures leave the fifth attempt evaluated; the fifth failure starts a wait of
   `login_slowdown_first_wait_seconds`; an attempt that FAILS at an open slot doubles the next wait
   (60, 120, 240, 480, 900, 900); a success, or a successful password reset, clears the address.
2. DURING A WAIT the password is not checked, even when it is right, and the answer cannot be told
   from a wrong password's: the same status, body and headers (no `Retry-After`, no 429), and the
   same work (one dummy Argon2 verification, no real one). An attempt during a wait writes
   nothing, so a flood neither extends nor doubles it.
3. NO ORACLE. An address with no account walks through the same states as one with an account.
4. ATOMICITY. N requests at once for one address get at most `login_slowdown_after_failures`
   evaluations before the first wait, and of any number at one open slot exactly one. The wait
   starts at the COUNTED attempt, not at its verdict.
5. WHAT STAYS: Google sign-in, the per-IP 429 (which writes no address key), the 503 when Redis is
   down; the settings, their defaults and the way an old `LOGIN_LIMIT_PER_EMAIL` is treated.

The equal-work proof for a refusal during a wait (one SELECT, one Argon2 operation at the
configured cost, the same Redis calls as a wrong password) is in `test_uniform_auth_a1.py`, beside
the refusals it extends.

There is no clock seam in `rate_limit.py`: the limiters are TTL-based. Time moves here by setting a
key's PTTL or by deleting the wait (what its expiry does). The one real expiry waits 50 ms.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import structlog
from argon2 import PasswordHasher
from httpx import AsyncClient, Response
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from avp_api import ids, security
from avp_api import redis_client as redis_module
from avp_api.config import (
    LOGIN_SLOWDOWN_FAILURES_RANGE,
    LOGIN_SLOWDOWN_SECONDS_RANGE,
    Settings,
    get_settings,
)
from avp_api.deps import google_provider, redis_client
from avp_api.models import PasswordResetToken, User
from avp_api.security import token_digest
from avp_api.services import auth as auth_service
from avp_api.services import rate_limit
from avp_api.services.google_oauth import GoogleIdentity

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
WRONG = "not-the-password-at-all"
SENTENCE = "Email or password is incorrect."
IP_HEADER = "X-Test-Client-IP"

KNOWN = "known@slowdown.example"
GHOST = "ghost@slowdown.example"  # no account

SECOND = 1000


@pytest.fixture(autouse=True)
def _ip_header(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller's IP comes from a header, so each attempt can say where it came from."""
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)


# One address per attempt by default, so that the per-IP window (30) never decides a test that is
# about the per-address slow-down. The tests about the IP window name theirs.
_ADDRESSES = itertools.count(1)


def _next_ip() -> str:
    n = next(_ADDRESSES)
    return f"10.{(n // 250) % 250}.{n % 250}.1"


# --- helpers --------------------------------------------------------------------------------


async def _account(client: AsyncClient, email: str = KNOWN) -> str:
    """An account with a password, signed out again. Returns its user id."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Slowdown Agency", "fullName": "Owner", "email": email,
              "password": PASSWORD},
        headers={IP_HEADER: "192.0.2.200"},
    )
    assert resp.status_code == 201, resp.text
    await client.post(f"{BASE}/auth/logout")
    return str(resp.json()["user"]["id"])


def _login(client: AsyncClient, email: str, password: str, ip: str | None = None) -> Any:
    return client.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        headers={IP_HEADER: ip or _next_ip()},
    )


async def _fail(client: AsyncClient, email: str, times: int = 1) -> None:
    for _ in range(times):
        assert (await _login(client, email, WRONG)).status_code == 401


def _shape(resp: Response) -> tuple[int, bytes, dict[str, str]]:
    """Everything a stranger can read off a response, bar the clock."""
    return (
        resp.status_code,
        resp.content,
        {k.lower(): v for k, v in resp.headers.items() if k.lower() != "date"},
    )


def _redis(settings: Settings) -> Redis:
    return redis_module.get_redis(settings)


def _keys_of(settings: Settings, email: str) -> tuple[str, str]:
    """(the address's failure count, its wait): the two keys the slow-down keeps."""
    subject = email.strip().lower()
    return (
        rate_limit._key(settings, "login", "email", subject),  # noqa: SLF001
        rate_limit._key(settings, "login", "wait", subject),  # noqa: SLF001
    )


async def _state(settings: Settings, email: str) -> dict[str, int]:
    """Failures counted, and the TTL (ms) of the count and of the wait; -2 is "no such key"."""
    r = _redis(settings)
    count_key, wait_key = _keys_of(settings, email)
    raw = await r.get(count_key)
    return {
        "failures": int(raw) if raw is not None else 0,
        "count_ttl_ms": await r.pttl(count_key),
        "wait_ttl_ms": await r.pttl(wait_key),
    }


GONE = {"failures": 0, "count_ttl_ms": -2, "wait_ttl_ms": -2}


async def _end_the_wait(settings: Settings, email: str) -> None:
    """The wait is over: what its TTL running out does."""
    await _redis(settings).delete(_keys_of(settings, email)[1])


def _within(ms: int, seconds: int, slack_ms: int = 5 * SECOND) -> bool:
    """`ms` left of a `seconds` TTL that was set a moment ago."""
    return seconds * SECOND - slack_ms <= ms <= seconds * SECOND


@pytest.fixture
def work(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Argon2 verification the app runs from here on: "verify" (a real hash) or "dummy"."""
    dummy = security._dummy_hash(settings)  # noqa: SLF001
    seen: list[str] = []
    real_verify = PasswordHasher.verify

    def spy(self: PasswordHasher, stored: str, password: str) -> bool:
        seen.append("dummy" if stored == dummy else "verify")
        return real_verify(self, stored, password)

    monkeypatch.setattr(PasswordHasher, "verify", spy)
    return seen


async def _slowed(client: AsyncClient, settings: Settings, email: str = KNOWN) -> None:
    """Fail an address until its first wait is running."""
    await _fail(client, email, settings.login_slowdown_after_failures)
    state = await _state(settings, email)
    assert state["failures"] == settings.login_slowdown_after_failures
    assert 0 < state["wait_ttl_ms"] <= settings.login_slowdown_first_wait_seconds * SECOND


# --- 1. the schedule --------------------------------------------------------------------------


class TestTheSchedule:
    async def test_four_failures_leave_the_fifth_attempt_evaluated(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 4)
        assert work == ["verify"] * 4
        state = await _state(settings, KNOWN)
        assert state["failures"] == 4 and state["wait_ttl_ms"] == -2, "four must not start a wait"

        work.clear()
        ok = await _login(client, KNOWN, PASSWORD)

        assert ok.status_code == 200, ok.text
        assert work == ["verify"], "the fifth attempt was not evaluated"
        assert settings.session_cookie_key in ok.headers["set-cookie"]
        assert await _state(settings, KNOWN) == GONE, "a success must clear the address"

    async def test_the_fifth_failure_starts_the_first_wait(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 4)
        assert (await _state(settings, KNOWN))["wait_ttl_ms"] == -2
        await _fail(client, KNOWN)
        state = await _state(settings, KNOWN)
        assert state["failures"] == 5
        assert _within(state["wait_ttl_ms"], 60), state

    async def test_the_waits_double_at_each_open_slot_up_to_the_longest(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        window_ms = settings.login_slowdown_window_seconds * SECOND
        seen: list[dict[str, int]] = [await _state(settings, KNOWN)]
        for _ in range(5):
            await _end_the_wait(settings, KNOWN)
            await _fail(client, KNOWN)
            seen.append(await _state(settings, KNOWN))

        assert [s["failures"] for s in seen] == [5, 6, 7, 8, 9, 10]
        wanted = [60, 120, 240, 480, 900, 900]
        for step, (state, seconds) in enumerate(zip(seen, wanted, strict=True)):
            assert _within(state["wait_ttl_ms"], seconds), (step, seconds, state)
            # The failure count outlives the wait it started by a whole window: it is never
            # shorter than the wait it guards, and the escalation survives the longest wait.
            gap = state["count_ttl_ms"] - state["wait_ttl_ms"]
            assert window_ms <= gap <= window_ms + 2 * SECOND, (step, state)

    async def test_a_success_at_an_open_slot_clears_the_address(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        for _ in range(4):  # four more, one at each open slot
            await _end_the_wait(settings, KNOWN)
            await _fail(client, KNOWN)
        assert (await _state(settings, KNOWN))["failures"] == 9
        await _end_the_wait(settings, KNOWN)
        work.clear()

        ok = await _login(client, KNOWN, PASSWORD)

        assert ok.status_code == 200 and work == ["verify"]
        assert await _state(settings, KNOWN) == GONE
        # Cleared means cleared: the next failures start from one, not from nine.
        await _fail(client, KNOWN, 4)
        assert (await _state(settings, KNOWN))["wait_ttl_ms"] == -2

    async def test_a_count_with_no_failure_for_a_window_is_forgotten(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        """The state ends by itself: a real expiry, not a delete."""
        await _account(client)
        await _fail(client, KNOWN, 4)
        count_key, _wait = _keys_of(settings, KNOWN)
        await _redis(settings).pexpire(count_key, 20)
        await asyncio.sleep(0.05)
        assert await _state(settings, KNOWN) == GONE
        await _fail(client, KNOWN, 4)  # four again: still nothing to wait for
        assert (await _state(settings, KNOWN))["wait_ttl_ms"] == -2

    async def test_every_counted_attempt_renews_the_window_and_a_refused_one_does_not(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        window_ms = settings.login_slowdown_window_seconds * SECOND
        await _fail(client, KNOWN, 2)
        count_key, _wait = _keys_of(settings, KNOWN)
        await _redis(settings).pexpire(count_key, 10 * SECOND)  # most of the window has passed
        await _fail(client, KNOWN)
        renewed = (await _state(settings, KNOWN))["count_ttl_ms"]
        assert window_ms - 5 * SECOND <= renewed <= window_ms, "a counted failure renews it"

        await _fail(client, KNOWN, 2)  # five in all: the wait is running
        await _redis(settings).pexpire(count_key, 10 * SECOND)
        for _ in range(3):  # refused during the wait
            assert (await _login(client, KNOWN, WRONG)).status_code == 401
        assert (await _state(settings, KNOWN))["count_ttl_ms"] <= 10 * SECOND, (
            "a refused attempt renewed the count"
        )

    async def test_the_count_outlives_the_wait_it_started_by_a_window(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        """When the wait is over the count is still there: a guesser who waits it out is not
        forgiven at the instant the slot opens, only a whole window later."""
        await _account(client)
        await _fail(client, KNOWN, 5)
        await _end_the_wait(settings, KNOWN)  # the first wait is over
        state = await _state(settings, KNOWN)
        assert state["failures"] == 5 and state["wait_ttl_ms"] == -2
        assert state["count_ttl_ms"] > settings.login_slowdown_window_seconds * SECOND

    async def test_a_huge_count_is_capped_without_looping_through_it(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        count_key, _wait = _keys_of(settings, KNOWN)
        await _redis(settings).set(count_key, "1000000", px=60 * SECOND)
        await _fail(client, KNOWN)
        state = await _state(settings, KNOWN)
        assert state["failures"] == 1_000_001
        assert _within(state["wait_ttl_ms"], settings.login_slowdown_max_wait_seconds)


# --- 2. during a wait --------------------------------------------------------------------------


class TestDuringAWait:
    async def test_the_correct_password_is_refused_exactly_as_a_wrong_one_is(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        reference = await _login(client, KNOWN, WRONG)  # the first failure: a plain wrong password
        assert reference.status_code == 401
        await _fail(client, KNOWN, 4)  # five: the wait is running
        work.clear()

        right = await _login(client, KNOWN, PASSWORD)

        assert _shape(right) == _shape(reference), "the wait can be told from a wrong password"
        assert right.json()["type"] == "/problems/invalid-credentials"
        assert right.json()["detail"] == SENTENCE
        assert "retry-after" not in right.headers and "set-cookie" not in right.headers
        assert work == ["dummy"], "the password was checked, or no Argon2 work was done"
        # And no session was minted: the cookie jar is still empty.
        me = await client.get(f"{BASE}/auth/me")
        assert me.status_code == 401

    async def test_attempts_during_a_wait_change_nothing(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        r = _redis(settings)
        count_key, wait_key = _keys_of(settings, KNOWN)
        await r.pexpire(wait_key, 30 * SECOND)  # half the first wait has passed
        # And the count has little left, so a refusal that renewed it would show (the count is
        # never shorter than the wait it guards, which is why it is 40 s and not 10).
        await r.pexpire(count_key, 40 * SECOND)
        before = await _state(settings, KNOWN)
        assert before["failures"] == 5 and _within(before["wait_ttl_ms"], 30)
        assert _within(before["count_ttl_ms"], 40)

        for i in range(8):  # right and wrong, from eight places
            resp = await _login(client, KNOWN, PASSWORD if i % 2 else WRONG)
            assert resp.status_code == 401

        after = await _state(settings, KNOWN)
        assert after["failures"] == 5, "a refused attempt was counted"
        assert 0 < after["wait_ttl_ms"] <= before["wait_ttl_ms"], "a refused attempt extended it"
        assert 0 < after["count_ttl_ms"] <= before["count_ttl_ms"], "a refused attempt renewed it"
        assert await r.get(wait_key) == "1" and await r.get(count_key) == "5"
        # The next slot opens when it would have, and the wait after it is the SECOND (120 s):
        # eight refusals did not double anything.
        await _end_the_wait(settings, KNOWN)
        await _fail(client, KNOWN)
        later = await _state(settings, KNOWN)
        assert later["failures"] == 6 and _within(later["wait_ttl_ms"], 120), later

    async def test_a_flood_cannot_hold_an_address_longer_than_the_schedule(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        results = await asyncio.gather(*(_login(client, KNOWN, WRONG) for _ in range(25)))
        assert {r.status_code for r in results} == {401}
        state = await _state(settings, KNOWN)
        assert state["failures"] == 5
        assert state["wait_ttl_ms"] <= 60 * SECOND

    async def test_a_slowed_refusal_still_counts_against_the_ip_bucket(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        """IP first: the per-IP window counts every attempt, a refused one included."""
        await _account(client)
        await _fail(client, KNOWN, 5)
        ip = "203.0.113.77"
        r = _redis(settings)
        ip_key = rate_limit._key(settings, "login", "ip", ip)  # noqa: SLF001
        for _ in range(3):
            assert (await _login(client, KNOWN, PASSWORD, ip=ip)).status_code == 401
        assert await r.get(ip_key) == "3"

    async def test_a_slowed_refusal_logs_one_event_with_ids_and_counts_only(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        user_id = await _account(client)
        for email, expected_id in ((KNOWN, user_id), (GHOST, None)):
            await _fail(client, email, 5)
            ip = "203.0.113.31"
            with structlog.testing.capture_logs() as events:
                resp = await _login(client, email, PASSWORD, ip=ip)
            assert resp.status_code == 401
            login_events = [e for e in events if str(e["event"]).startswith("auth.login")]
            assert [e["event"] for e in login_events] == ["auth.login.slowed"], login_events
            (slowed,) = login_events
            assert set(slowed) == {
                "event", "log_level", "user_id", "wait_left_seconds", "failures",
            }
            assert slowed["user_id"] == expected_id and slowed["log_level"] == "info"
            assert isinstance(slowed["wait_left_seconds"], int)
            assert 0 < slowed["wait_left_seconds"] <= 60
            assert slowed["failures"] == 5
            blob = json.dumps(events, default=str)
            digests = [k.rsplit(":", 1)[1] for k in _keys_of(settings, email)]
            assert email not in blob and ip not in blob and "@" not in blob
            assert all(d not in blob for d in digests), "a digest reached a log"

    async def test_an_evaluated_failure_still_logs_auth_login_failed(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        user_id = await _account(client)
        with structlog.testing.capture_logs() as events:
            await _login(client, KNOWN, WRONG)
        failed = [e for e in events if e["event"] == "auth.login.failed"]
        assert [(e["user_id"], e["reason"]) for e in failed] == [(user_id, "invalid_credentials")]
        assert not [e for e in events if e["event"] == "auth.login.slowed"]


# --- 3. no oracle ------------------------------------------------------------------------------


async def _walk(
    client: AsyncClient, settings: Settings, email: str
) -> list[tuple[str, int, int, int, int]]:
    """Take one address through the schedule, noting what is left in Redis after each step.

    TTLs are rounded UP to ten seconds, so that a few milliseconds between two runs cannot
    separate them.
    """
    def bucket(ms: int) -> int:
        return math.ceil(ms / (10 * SECOND)) * 10 if ms > 0 else ms

    steps: list[tuple[str, int, int, int, int]] = []

    async def step(label: str, password: str) -> None:
        resp = await _login(client, email, password)
        state = await _state(settings, email)
        steps.append(
            (label, resp.status_code, state["failures"],
             bucket(state["count_ttl_ms"]), bucket(state["wait_ttl_ms"]))
        )

    for _ in range(4):
        await step("failure", WRONG)
    await step("fifth", WRONG)
    await step("during, right password", PASSWORD)
    await step("during, wrong password", WRONG)
    await _end_the_wait(settings, email)
    await step("second slot", WRONG)
    await step("during again", PASSWORD)
    await _end_the_wait(settings, email)
    await step("third slot", WRONG)
    return steps


class TestNoOracle:
    async def test_an_address_with_no_account_goes_through_the_same_states(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        known = await _walk(client, settings, KNOWN)
        ghost = await _walk(client, settings, GHOST)
        assert known == ghost
        # And the walk is what the schedule says, not two equal nothings.
        assert [s[2] for s in known] == [1, 2, 3, 4, 5, 5, 5, 6, 6, 7]
        assert known[4][4] == 60 and known[7][4] == 120 and known[9][4] == 240
        assert {s[1] for s in known} == {401}

    async def test_every_refusal_of_either_kind_has_the_same_shape(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        reference = _shape(await _login(client, KNOWN, WRONG))
        await _fail(client, KNOWN, 4)
        await _fail(client, GHOST, 5)
        shapes = [
            _shape(await _login(client, KNOWN, PASSWORD)),  # in a wait, the right password
            _shape(await _login(client, KNOWN, WRONG)),  # in a wait, a wrong one
            _shape(await _login(client, GHOST, PASSWORD)),  # in a wait, no account
            _shape(await _login(client, "never-seen@slowdown.example", WRONG)),  # not in a wait
        ]
        assert all(s == reference for s in shapes)


# --- 4. atomicity ------------------------------------------------------------------------------


class TestAtomicity:
    async def test_a_burst_before_the_slowdown_gets_at_most_the_threshold_evaluations(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        work.clear()

        results = await asyncio.gather(*(_login(client, KNOWN, WRONG) for _ in range(12)))

        assert {r.status_code for r in results} == {401}
        assert len({r.content for r in results}) == 1
        assert work.count("verify") == settings.login_slowdown_after_failures == 5
        assert work.count("dummy") == 7
        state = await _state(settings, KNOWN)
        assert state["failures"] == 5 and 0 < state["wait_ttl_ms"] <= 60 * SECOND

    async def test_the_same_burst_for_an_address_with_no_account_is_counted_the_same(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        results = await asyncio.gather(*(_login(client, GHOST, WRONG) for _ in range(12)))
        assert {r.status_code for r in results} == {401}
        assert work == ["dummy"] * 12
        state = await _state(settings, GHOST)
        assert state["failures"] == 5, "twelve at once were counted as more than five"
        assert 0 < state["wait_ttl_ms"] <= 60 * SECOND

    @pytest.mark.parametrize("n", [2, 8])
    async def test_of_any_number_at_one_open_slot_exactly_one_is_evaluated(
        self, client: AsyncClient, settings: Settings, work: list[str], n: int
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        await _end_the_wait(settings, KNOWN)
        work.clear()

        results = await asyncio.gather(*(_login(client, KNOWN, WRONG) for _ in range(n)))

        assert {r.status_code for r in results} == {401}
        assert work.count("verify") == 1 and work.count("dummy") == n - 1
        state = await _state(settings, KNOWN)
        assert state["failures"] == 6, "more than one attempt was counted at the slot"
        assert _within(state["wait_ttl_ms"], 120), "the wait doubled more than once"

    async def test_the_right_password_cannot_be_tried_twice_at_one_slot(
        self, client: AsyncClient, settings: Settings, work: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two requests carrying the RIGHT password reach one open slot together. Exactly one is
        evaluated, and it is held inside its verify until the other has been answered, so the
        other cannot be merely the one that came later: it is refused as a wrong password, with
        the same bytes, without its password being checked. Bounded: a limiter that let both
        through puts both in the held verify, and this test then fails after its timeout and
        does not hang the run."""
        await _account(client)
        reference = await _login(client, KNOWN, WRONG)  # failure 1: a plain wrong-password 401
        assert reference.status_code == 401
        await _fail(client, KNOWN, 4)  # five: the wait is running
        await _end_the_wait(settings, KNOWN)  # and it is over: one open slot
        work.clear()
        inside, release = asyncio.Event(), asyncio.Event()
        entered: list[int] = []
        real = auth_service.verify_password_async

        async def held(*args: Any, **kwargs: Any) -> bool:
            entered.append(1)
            inside.set()
            await release.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(auth_service, "verify_password_async", held)
        tasks = [asyncio.create_task(_login(client, KNOWN, PASSWORD)) for _ in range(2)]
        try:
            await asyncio.wait_for(inside.wait(), 10)
            # One request is inside its verify. The other must be answered while it is held.
            done, _pending = await asyncio.wait(
                tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED
            )
            assert len(done) == 1, "the second request was not refused while the first was held"
            (refused,) = (task.result() for task in done)
            assert _shape(refused) == _shape(reference), "the refusal is not a wrong password's"
            assert entered == [1], "both requests had their password checked"
        finally:
            release.set()
            await asyncio.wait(tasks, timeout=10)
            for task in tasks:
                if not task.done():
                    task.cancel()

        assert sorted(task.result().status_code for task in tasks) == [200, 401]
        assert work.count("verify") == 1 and work.count("dummy") == 1, work
        # The one that was evaluated was the right password: the address is clear.
        assert await _state(settings, KNOWN) == GONE

    async def test_the_wait_starts_when_the_attempt_is_counted_not_when_its_verdict_comes(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 4)
        inside, release = asyncio.Event(), asyncio.Event()
        entered: list[int] = []
        real = auth_service.verify_password_async

        async def held(*args: Any, **kwargs: Any) -> bool:
            entered.append(1)
            inside.set()
            await release.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(auth_service, "verify_password_async", held)
        fifth = asyncio.create_task(_login(client, KNOWN, WRONG))
        try:
            await asyncio.wait_for(inside.wait(), 10)

            # The fifth attempt is counted and its password is still being checked: its wait is
            # already running, and the request that arrives now is refused without a check. (If it
            # were not, the sixth would wait in `held` too, so it is bounded: a broken limiter
            # fails this test and does not hang it.)
            state = await _state(settings, KNOWN)
            assert state["failures"] == 5 and 0 < state["wait_ttl_ms"] <= 60 * SECOND
            sixth = await asyncio.wait_for(_login(client, KNOWN, PASSWORD), 10)
            assert sixth.status_code == 401
            assert entered == [1], "the sixth attempt's password was checked"
        finally:
            release.set()
            finished, _pending = await asyncio.wait([fifth], timeout=10)
            if not finished:
                fifth.cancel()
        assert finished, "the fifth attempt did not finish"
        assert fifth.result().status_code == 401


# --- 5. what stays, and what resets it -------------------------------------------------------


class _FakeGoogle:
    """Hands back KNOWN's address as a verified Google identity, echoing the nonce asked for."""

    def __init__(self) -> None:
        self.nonce: str | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.nonce = nonce
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&fake=1"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        return GoogleIdentity(
            sub="google-sub-slowdown", email=KNOWN, email_verified=True, name="Known Person",
            nonce=self.nonce,
        )


class TestWhatStays:
    async def test_google_sign_in_still_works_for_a_slowed_address(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        user_id = await _account(client)
        await _fail(client, KNOWN, 5)
        before = await _state(settings, KNOWN)
        app = client._transport.app  # noqa: SLF001
        configured = settings.model_copy(
            update={
                "google_oauth_client_id": "test-client-id.apps.googleusercontent.com",
                "google_oauth_client_secret": "not-a-real-secret",
                "google_oauth_redirect_url": "http://localhost:8000/api/v1/auth/google/callback",
            }
        )
        app.dependency_overrides[get_settings] = lambda: configured
        fake = _FakeGoogle()  # one object: it remembers the nonce the start asked for
        app.dependency_overrides[google_provider] = lambda: fake

        started = await client.get(
            f"{BASE}/auth/google/start", follow_redirects=False, headers={IP_HEADER: _next_ip()}
        )
        assert started.status_code == 302, started.text
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        arrived = await client.get(
            f"{BASE}/auth/google/callback", params={"code": "a-code", "state": state},
            follow_redirects=False,
        )
        assert arrived.status_code == 302, arrived.text
        landed = urlparse(arrived.headers["location"])
        assert landed.path == "/dashboard", landed.query

        me = await client.get(f"{BASE}/auth/me")
        assert me.status_code == 200 and me.json()["user"]["id"] == user_id
        # The slow-down is the password route's: Google neither reads it nor clears it.
        after = await _state(settings, KNOWN)
        assert after["failures"] == before["failures"] == 5
        assert 0 < after["wait_ttl_ms"] <= before["wait_ttl_ms"]

    async def test_the_per_ip_limit_still_answers_429_and_writes_no_address_key(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)  # slowed, from other places
        slowed_before = await _state(settings, KNOWN)
        monkeypatch.setattr(settings, "login_limit_per_ip", 3)
        ip = "203.0.113.50"
        for i in range(3):
            seed = await _login(client, f"seed{i}@slowdown.example", WRONG, ip=ip)
            assert seed.status_code == 401

        fresh = await _login(client, "fresh@slowdown.example", WRONG, ip=ip)
        known = await _login(client, KNOWN, PASSWORD, ip=ip)

        for refused in (fresh, known):
            assert refused.status_code == 429
            assert refused.headers["content-type"].startswith("application/problem+json")
            assert int(refused.headers["retry-after"]) > 0
            assert refused.json()["type"] == "/problems/rate-limited"
            assert refused.json()["detail"].startswith("Too many attempts. Try again in ")
        # Refused on IP first: nothing was written for either address.
        assert await _state(settings, "fresh@slowdown.example") == GONE
        slowed_after = await _state(settings, KNOWN)
        assert slowed_after["failures"] == slowed_before["failures"]
        assert 0 < slowed_after["wait_ttl_ms"] <= slowed_before["wait_ttl_ms"]
        assert 0 < slowed_after["count_ttl_ms"] <= slowed_before["count_ttl_ms"]

    async def test_the_ip_limit_is_logged_as_it_always_was(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "login_limit_per_ip", 1)
        ip = "203.0.113.51"
        await _login(client, KNOWN, WRONG, ip=ip)
        with structlog.testing.capture_logs() as events:
            assert (await _login(client, KNOWN, WRONG, ip=ip)).status_code == 429
        tripped = [e for e in events if e["event"] == "auth.rate_limited"]
        assert tripped == [
            {"event": "auth.rate_limited", "log_level": "warning", "action": "login", "kind": "ip"}
        ]

    async def test_a_successful_password_reset_clears_the_slowdown(
        self, client: AsyncClient, settings: Settings, engine: AsyncEngine
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        assert (await _login(client, KNOWN, PASSWORD)).status_code == 401  # the wait is running

        raw = "slowdown-reset-token-" + "z" * 32
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as s:
            user = (await s.execute(select(User).where(User.email == KNOWN))).scalar_one()
            s.add(
                PasswordResetToken(
                    id=ids.new_id(ids.PASSWORD_RESET), user_id=user.id,
                    token_digest=token_digest(raw),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await s.commit()
        reset = await client.post(
            f"{BASE}/auth/reset-password/confirm",
            json={"token": raw, "newPassword": "an-entirely-different-passphrase"},
        )
        assert reset.status_code == 200, reset.text

        assert await _state(settings, KNOWN) == GONE, "the reset left the address slowed"
        ok = await _login(client, KNOWN, "an-entirely-different-passphrase")
        assert ok.status_code == 200, "the first sign-in after a reset was still refused"

    async def test_clear_login_forgets_both_slowdown_keys_and_the_change_password_count(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        r = _redis(settings)
        count_key, wait_key = _keys_of(settings, KNOWN)
        change_key = rate_limit._key(settings, "change_password", "email", KNOWN)  # noqa: SLF001
        for key in (count_key, wait_key, change_key):
            await r.set(key, "3", px=60 * SECOND)
        await rate_limit.clear_login(r, settings, email=" KNOWN@slowdown.example ")
        assert [await r.exists(k) for k in (count_key, wait_key, change_key)] == [0, 0, 0]

    async def test_a_success_gives_the_ip_its_attempt_back_and_clears_only_its_own_address(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _account(client)
        await _fail(client, "other@slowdown.example", 3)
        r = _redis(settings)
        ip = "203.0.113.60"
        assert (await _login(client, KNOWN, PASSWORD, ip=ip)).status_code == 200
        ip_key = rate_limit._key(settings, "login", "ip", ip)  # noqa: SLF001
        assert await r.get(ip_key) == "0" and await r.pttl(ip_key) > 0
        assert (await _state(settings, "other@slowdown.example"))["failures"] == 3

    async def test_redis_down_is_a_503_and_the_password_is_not_checked(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        app = client._transport.app  # noqa: SLF001
        dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
        app.dependency_overrides[redis_client] = lambda: dead
        work.clear()
        try:
            with structlog.testing.capture_logs() as events:
                resp = await _login(client, KNOWN, PASSWORD)
        finally:
            await dead.aclose()
        assert resp.status_code == 503, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/service-unavailable"
        assert KNOWN not in resp.text
        assert work == [], "the password was checked with Redis down"
        warnings = [e for e in events if e["event"] == "auth.rate_limit.unavailable"]
        assert len(warnings) == 1 and set(warnings[0]) == {"event", "log_level"}
        assert KNOWN not in json.dumps(events, default=str)

    async def test_a_redis_error_while_clearing_after_a_success_leaves_the_sign_in_standing(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _account(client)
        real = rate_limit._eval  # noqa: SLF001

        async def flaky(redis: Redis, script: str, keys: list[str], args: list[str]) -> list[int]:
            if script is rate_limit._LOGIN_CLEAR:  # noqa: SLF001
                raise RedisError("gone away")
            return await real(redis, script, keys, args)

        monkeypatch.setattr(rate_limit, "_eval", flaky)
        with structlog.testing.capture_logs() as events:
            ok = await _login(client, KNOWN, PASSWORD)
        assert ok.status_code == 200, ok.text
        warnings = [e for e in events if e["event"] == "auth.rate_limit.unavailable"]
        assert len(warnings) == 1
        # Best effort, like every release: the state is left to end at its TTL.
        assert (await _state(settings, KNOWN))["failures"] == 1


class TestTheSlowedVerdictIsRequired:
    """`authenticate` has no default for `slowed`: no caller can skip the slow-down by omission."""

    def test_it_is_a_required_keyword_with_no_default(self) -> None:
        parameter = inspect.signature(auth_service.authenticate).parameters["slowed"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    async def test_a_caller_that_leaves_it_out_is_a_type_error_and_checks_nothing(self) -> None:
        with pytest.raises(TypeError):
            await auth_service.authenticate(  # type: ignore[call-arg]
                None,  # type: ignore[arg-type]
                email=KNOWN,
                password=PASSWORD,
            )


class TestTheSettings:
    def test_the_defaults_are_the_founders_numbers(self) -> None:
        s = Settings(_env_file=None)
        assert (
            s.login_slowdown_after_failures,
            s.login_slowdown_window_seconds,
            s.login_slowdown_first_wait_seconds,
            s.login_slowdown_max_wait_seconds,
        ) == (5, 15 * 60, 60, 15 * 60)

    def test_the_ranges_are_the_ones_documented(self) -> None:
        assert LOGIN_SLOWDOWN_FAILURES_RANGE == (1, 20)
        assert LOGIN_SLOWDOWN_SECONDS_RANGE == (1, 86_400)

    @pytest.mark.parametrize(
        ("name", "low", "high"),
        [
            ("login_slowdown_after_failures", *LOGIN_SLOWDOWN_FAILURES_RANGE),
            ("login_slowdown_window_seconds", *LOGIN_SLOWDOWN_SECONDS_RANGE),
            ("login_slowdown_first_wait_seconds", *LOGIN_SLOWDOWN_SECONDS_RANGE),
            ("login_slowdown_max_wait_seconds", *LOGIN_SLOWDOWN_SECONDS_RANGE),
        ],
    )
    def test_each_bound_is_accepted_and_one_past_it_stops_the_start_naming_the_setting(
        self, name: str, low: int, high: int
    ) -> None:
        for good in (low, high):
            assert getattr(Settings(_env_file=None, **{name: good}), name) == good
        for bad in (low - 1, high + 1):
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None, **{name: bad})
            assert name in str(refused.value), (name, bad)

    def test_the_start_refuses_a_slip_of_units_naming_the_variable_and_not_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As a deploy sees it: the environment variable, and the message names the setting."""
        for variable, value in (
            ("LOGIN_SLOWDOWN_MAX_WAIT_SECONDS", "900000"),  # milliseconds typed as seconds
            ("LOGIN_SLOWDOWN_AFTER_FAILURES", "500"),  # the per-address guard, switched off
            ("LOGIN_SLOWDOWN_AFTER_FAILURES", "21"),
            ("LOGIN_SLOWDOWN_WINDOW_SECONDS", "86401"),
            ("LOGIN_SLOWDOWN_FIRST_WAIT_SECONDS", "0"),
            ("LOGIN_SLOWDOWN_FIRST_WAIT_SECONDS", "soon"),
        ):
            monkeypatch.setenv(variable, value)
            with pytest.raises(ValidationError) as refused:
                Settings(_env_file=None)
            said = str(refused.value)
            assert variable.lower() in said.lower(), (variable, said)
            if len(value) > 2:
                assert value not in said, (variable, "the value was printed")
            monkeypatch.delenv(variable)

    def test_a_first_wait_above_the_longest_is_a_pair_that_is_cut_and_not_a_range_error(
        self,
    ) -> None:
        pair = Settings(
            _env_file=None,
            login_slowdown_first_wait_seconds=600,
            login_slowdown_max_wait_seconds=30,
        )
        assert (pair.login_slowdown_first_wait_seconds, pair.login_slowdown_max_wait_seconds) == (
            600, 30,
        )

    def test_a_leftover_per_email_figure_on_a_deployed_service_is_ignored_not_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert "login_limit_per_email" not in Settings.model_fields
        monkeypatch.setenv("LOGIN_LIMIT_PER_EMAIL", "3")
        s = Settings(_env_file=None)  # does not raise
        assert not hasattr(s, "login_limit_per_email")
        assert s.login_slowdown_after_failures == 5
        # Passed by name, too (`extra="ignore"`).
        assert Settings(_env_file=None, login_limit_per_email=3).login_limit_per_ip == 30

    async def test_fewer_failures_and_a_shorter_first_wait_change_the_behaviour(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch,
        work: list[str],
    ) -> None:
        monkeypatch.setattr(settings, "login_slowdown_after_failures", 2)
        monkeypatch.setattr(settings, "login_slowdown_first_wait_seconds", 10)
        monkeypatch.setattr(settings, "login_slowdown_max_wait_seconds", 25)
        monkeypatch.setattr(settings, "login_slowdown_window_seconds", 300)
        await _account(client)

        await _fail(client, KNOWN)
        state = await _state(settings, KNOWN)
        assert state["failures"] == 1 and state["wait_ttl_ms"] == -2
        assert _within(state["count_ttl_ms"], 300)
        await _fail(client, KNOWN)  # the second failure is the one that slows it
        state = await _state(settings, KNOWN)
        assert _within(state["wait_ttl_ms"], 10, 3 * SECOND), state
        assert _within(state["count_ttl_ms"], 300 + 10, 3 * SECOND), state

        work.clear()
        assert (await _login(client, KNOWN, PASSWORD)).status_code == 401
        assert work == ["dummy"]

        waits = []
        for _ in range(3):
            await _end_the_wait(settings, KNOWN)
            await _fail(client, KNOWN)
            waits.append((await _state(settings, KNOWN))["wait_ttl_ms"])
        assert _within(waits[0], 20, 3 * SECOND)
        assert _within(waits[1], 25, 3 * SECOND), "the longest wait was not applied"
        assert _within(waits[2], 25, 3 * SECOND)

    async def test_a_first_wait_above_the_longest_is_cut_to_it(
        self, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "login_slowdown_after_failures", 1)
        monkeypatch.setattr(settings, "login_slowdown_first_wait_seconds", 100)
        monkeypatch.setattr(settings, "login_slowdown_max_wait_seconds", 30)
        await _account(client)
        await _fail(client, KNOWN)
        assert _within((await _state(settings, KNOWN))["wait_ttl_ms"], 30, 3 * SECOND)

    async def test_a_wait_that_somehow_has_no_ttl_is_given_one_and_still_refuses(
        self, client: AsyncClient, settings: Settings, work: list[str]
    ) -> None:
        await _account(client)
        await _fail(client, KNOWN, 5)
        r = _redis(settings)
        _count, wait_key = _keys_of(settings, KNOWN)
        await r.persist(wait_key)
        assert await r.pttl(wait_key) == -1
        work.clear()

        resp = await _login(client, KNOWN, PASSWORD)

        assert resp.status_code == 401 and work == ["dummy"]
        assert _within(await r.pttl(wait_key), 60), "the wait kept no TTL, so it would never end"
