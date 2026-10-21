"""Every key the API puts in Redis expires on its own — K1, Redis hardening.

Redis here is one small instance, and a key that never expires is a key that is never given
back: the sessions, the limiter windows and the Google sign-in state all live in it, and under
`noeviction` (the policy it runs with) a full Redis refuses writes instead of dropping keys.
So every writer gives every key a lifetime, and this file is the check that keeps it so.

The sweep drives each key family's REAL writer, through the API wherever there is a route (sign
up and login for the sessions and their index, one failed attempt for each limiter, the Google
start and callback, the competitor-detection button), then reads the whole Redis with SCAN and
asserts, for every key:

* it has a TTL (`PTTL > 0`), and one that matches the window it is for (not just any TTL: a
  session that expired in a second, or a window that never ends, is as wrong as no TTL);
* the one exception is the sentinel (`redis_watch.SENTINEL_KEY`), which has NO expiry on purpose,
  because its absence is the signal; it is allowed exactly no TTL, not "any".

A key the sweep does not know fails it, and so does a family it did not manage to write, so a
new key family cannot join Redis without being registered here, and registered with its window.
The new family's writer has to be reachable from a driver below, which a reviewer sees; and
`TestEveryWriterIsKnown` fails when a module that talks to Redis is added without anyone saying
whether it writes.

A session lasts 14 days from sign-in, and nothing slides it: `SessionStore.touch` has no caller,
and a request only reads the session. `TestASessionLastsFourteenDaysFromSignIn` pins both, so
wiring `touch` in (a product decision: the session then lasts as long as it is used) fails there
first.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

import pytest_asyncio
from httpx import AsyncClient
from redis.asyncio import Redis

import avp_api
from avp_api import redis_client as redis_module
from avp_api import redis_watch, sessions
from avp_api.config import Settings, get_settings
from avp_api.deps import google_provider
from avp_api.services import google_oauth, rate_limit, spend_caps
from avp_api.services.google_oauth import GoogleExchangeError, GoogleIdentity

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
EMAIL = "sweep@expiry.example"
# An address with no account, failed often enough to be slowed (A2): it leaves the failure count and
# the wait that the sweep checks. (`EMAIL`'s own state is cleared by its success.)
SLOWED = "slowed@expiry.example"
NEW_ADDRESS = "someone-new@expiry.example"
FOURTEEN_DAYS_S = 14 * 24 * 60 * 60
# How far under its full window a key just written may already be. Generous: every key here is
# written seconds before it is read.
FRESH_MS = 30_000

# Each limiter action's window setting (for login, the IP's: its per-address keys are the
# slow-down's, below). Adding an action to `rate_limit.Action` fails
# `test_every_limiter_action_has_a_window_here` until it is added, with its window, and then the
# sweep has to be made to write it.
WINDOW_SETTING = {
    "login": "login_window_seconds",
    "signup": "signup_window_seconds",
    "reset": "reset_window_seconds",
    "change_password": "change_password_window_seconds",
    "google_start": "google_start_window_seconds",
    "shared_report": "shared_window_seconds",
    "shared_pdf": "shared_window_seconds",
}
# The actions that count the source IP and nothing else: no email key exists for them.
IP_ONLY_ACTIONS = {"google_start", "shared_report", "shared_pdf"}


def _families(settings: Settings) -> dict[str, int | None]:
    """Key prefix -> the longest its TTL may be, in ms. None: no expiry at all (the sentinel)."""
    session_ms = settings.session_ttl_seconds * 1000
    flow_ms = settings.google_sign_in_ttl_seconds * 1000
    # Worked out here from the two settings, not asked of the code that sets the lifetime.
    claim_s = settings.argon2_queue_timeout_seconds + settings.redeem_claim_margin_seconds
    claim_ms = round(claim_s * 1000)
    families: dict[str, int | None] = {
        sessions.SESSION_PREFIX: session_ms,
        sessions.USER_SESSIONS_PREFIX: session_ms,
        google_oauth.STATE_PREFIX: flow_ms,
        google_oauth.TICKET_PREFIX: flow_ms,
        f"rl:{rate_limit.DETECT_ACTION}:client:": rate_limit.DETECT_WINDOW_MS,
        redis_watch.SENTINEL_KEY: None,
        # F3: an ad hoc question's hour per client, and a scan's fix-regeneration cooldown.
        f"rl:{rate_limit.PROMPT_RUN_ACTION}:client:": rate_limit.PROMPT_RUN_WINDOW_MS,
        f"rl:{rate_limit.FIX_REGENERATE_ACTION}:scan:": (
            settings.fix_regenerate_cooldown_seconds * 1000
        ),
        # 3.129: the claim on a reset link or an invitation being redeemed, which ends by itself
        # at the queue timeout plus a margin if its holder dies without releasing it.
        f"rl:{rate_limit.REDEEM_RESET_ACTION}:token:": claim_ms,
        f"rl:{rate_limit.REDEEM_INVITE_ACTION}:token:": claim_ms,
    }
    # The platform's daily caps (D2, and F3's three): one key per kind per UTC day, and F3's one
    # per agency per day for classifications, all kept 48 hours.
    for kind in (*spend_caps.KINDS, *(name for name, _s, _t in spend_caps.AGENCY_CAPS.values())):
        families[f"cap:{kind}:"] = spend_caps._TTL_MS  # noqa: SLF001 - the bound the module sets
    for action, setting in WINDOW_SETTING.items():
        # Google start and the two public share routes have no email, so only the IP key.
        for kind in ("ip",) if action in IP_ONLY_ACTIONS else ("ip", "email"):
            families[f"rl:{action}:{kind}:"] = getattr(settings, setting) * 1000
    # Login's per-address keys are the slow-down's, not a window (A2). The sweep makes an address
    # fail `login_slowdown_after_failures` times, which starts the FIRST wait, so: the wait lives
    # that long, and the failure count lives for its window PLUS that wait (never shorter than the
    # wait it guards, and outlasting it by a window). Both come from the settings.
    first_wait_ms = settings.login_slowdown_first_wait_seconds * 1000
    families["rl:login:email:"] = settings.login_slowdown_window_seconds * 1000 + first_wait_ms
    families["rl:login:wait:"] = first_wait_ms
    return families


class FakeGoogle:
    """Hands back one new address for any code, and echoes the nonce the start minted."""

    def __init__(self) -> None:
        self.nonce: str | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.nonce = nonce
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&fake=1"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        if code != "a-code":
            raise GoogleExchangeError
        return GoogleIdentity(
            sub="google-sub-new", email=NEW_ADDRESS, email_verified=True,
            name="Someone New", nonce=self.nonce,
        )


async def _sign_up(client: AsyncClient, email: str = EMAIL):  # noqa: ANN202
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Expiry Sweep Agency", "fullName": "Operator",
              "email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp


async def _keys(redis: Redis, prefix: str = "") -> list[str]:
    return sorted([key async for key in redis.scan_iter(f"{prefix}*")])


@pytest_asyncio.fixture
async def redis(client: AsyncClient, settings: Settings) -> Redis:
    """The suite's test Redis, emptied first: the sweep reads EVERY key in it."""
    # `client` is asked for so the app is wired to this Redis, and its teardown empties it again.
    r = redis_module.get_redis(settings)
    await r.flushdb()
    return r


async def _drive_every_writer(
    client: AsyncClient, settings: Settings, redis: Redis
) -> None:
    """Make each key family's real writer run once, the way the product runs it."""
    app = client._transport.app  # noqa: SLF001 - the ASGI app under test

    # Sessions and their index; the sign-up limiter. A second login makes a second session.
    await _sign_up(client)
    # The login limiter: one failure (stays counted), then a success (gives the IP's attempt back,
    # which must leave that key and its TTL in place, and clears the address's own).
    wrong = await client.post(
        f"{BASE}/auth/login", json={"email": EMAIL, "password": "not-the-password"}
    )
    assert wrong.status_code == 401, wrong.text
    right = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert right.status_code == 200, right.text
    # The slow-down (A2): an address failed until its first wait starts. The failure count and the
    # wait are the two keys this leaves.
    for _ in range(settings.login_slowdown_after_failures):
        refused = await client.post(
            f"{BASE}/auth/login", json={"email": SLOWED, "password": "not-the-password"}
        )
        assert refused.status_code == 401, refused.text
    # The reset limiter (counts every request).
    reset = await client.post(f"{BASE}/auth/reset-password/request", json={"email": EMAIL})
    assert reset.status_code == 200, reset.text
    # The change-password limiter: one wrong current password, counted.
    change = await client.post(
        f"{BASE}/auth/change-password",
        json={"currentPassword": "not-the-password", "newPassword": "an-entirely-new-passphrase"},
    )
    assert change.status_code == 401, change.text
    # The competitor-detection day, taken from the button.
    made = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert made.status_code == 201, made.text
    detect = await client.post(f"{BASE}/clients/{made.json()['id']}/competitors/detect")
    assert detect.status_code == 201, detect.text

    # Google: one sign-in state left waiting, and one ticket for an address that is new here.
    configured = settings.model_copy(
        update={
            "google_oauth_client_id": "test-client-id.apps.googleusercontent.com",
            "google_oauth_client_secret": "not-a-real-secret",
            "google_oauth_redirect_url": "http://localhost:8000/api/v1/auth/google/callback",
        }
    )
    fake = FakeGoogle()
    app.dependency_overrides[get_settings] = lambda: configured
    app.dependency_overrides[google_provider] = lambda: fake
    started = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert started.status_code == 302, started.text
    state = re.search(r"state=([^&]+)", started.headers["location"])
    assert state is not None
    arrived = await client.get(
        f"{BASE}/auth/google/callback",
        params={"code": "a-code", "state": state.group(1)},
        follow_redirects=False,
    )
    assert arrived.status_code == 302, arrived.text
    assert "/sign-up/google#ticket=" in arrived.headers["location"], arrived.headers["location"]
    waiting = await client.get(f"{BASE}/auth/google/start", follow_redirects=False)
    assert waiting.status_code == 302, waiting.text

    # F5c: the two public share routes' per-IP windows (an unknown token is a 404 after the count).
    for route in ("/reports/shared", "/reports/shared/pdf"):
        unknown = await client.post(f"{BASE}{route}", json={"token": "x" * 32})
        assert unknown.status_code == 404, unknown.text

    # The platform's daily caps, claimed as a scan start claims them. (A real start stops at the
    # engine check here, because the tests configure no provider key.)
    await spend_caps.claim(redis, settings, kinds=["scans", "free_scans"], agency_id="agy_sweep")
    # F3: the three kinds of paid AI call outside a scan. Their routes need a provider stub each,
    # which the claim tests drive end to end (`test_paid_calls_gated`); here the real writers run.
    # Each kind with its agency day (and the free share for classifications).
    await spend_caps.claim_for_agency(redis, settings, kind="prompt_runs", agency_id="agy_sweep")
    await spend_caps.claim_for_agency(redis, settings, kind="fix_runs", agency_id="agy_sweep")
    await spend_caps.claim_for_agency(
        redis, settings, kind="classifications", agency_id="agy_sweep", free=True
    )
    allowed, _ = await rate_limit.claim_prompt_run(
        redis, settings, client_id="clnt_sweep", agency_id="agy_sweep"
    )
    assert allowed
    await rate_limit.claim_fix_regeneration(
        redis, settings, scan_id="scan_sweep", agency_id="agy_sweep"
    )
    # 3.129: the routes release their claim as soon as they are done, so the real writer is driven
    # here and the claims are left held, as a request that died would leave them.
    for kind in ("reset", "invite"):
        held = await rate_limit.claim_redemption(redis, settings, kind=kind, digest="sweep" * 12)
        assert held is not None

    # The sentinel, written by the check the API runs at start and every minute.
    assert await redis_watch.check_sentinel(redis) is True


class TestEveryKeyExpiresOnItsOwn:
    async def test_every_key_has_a_ttl_that_matches_its_window_and_only_the_sentinel_has_none(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _drive_every_writer(client, settings, redis)
        families = _families(settings)
        keys = await _keys(redis)

        # Every family was written, so the sweep cannot pass for want of a key to look at...
        written = {prefix for prefix in families if any(k.startswith(prefix) for k in keys)}
        assert written == set(families), (
            f"the sweep did not write: {sorted(set(families) - written)}"
        )
        # ...and nothing is in Redis that the sweep does not know. A new family must be added to
        # `_families` with its window, and made to be written by `_drive_every_writer`.
        unknown = [k for k in keys if not any(k.startswith(prefix) for prefix in families)]
        assert unknown == [], f"keys no family here accounts for: {unknown}"

        for key in keys:
            prefix = next(p for p in families if key.startswith(p))
            longest = families[prefix]
            ttl = await redis.pttl(key)
            if longest is None:
                # The sentinel: no expiry, exactly. -1 is "exists, no TTL"; -2 would be gone.
                assert key == redis_watch.SENTINEL_KEY
                assert ttl == -1, (key, ttl)
            else:
                assert ttl > 0, f"{key} has no expiry (PTTL {ttl}): a key that is never given back"
                assert longest - FRESH_MS <= ttl <= longest, (
                    f"{key}: PTTL {ttl} ms is not its {longest} ms window"
                )

    async def test_the_limiter_keys_keep_their_ttl_when_a_success_gives_an_attempt_back(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """`release_login` DECRs the IP's counter, which may already be at its last count: not
        DEL, and not a rewrite without the window. The address's own state is not given a
        attempt back but forgotten (A2): both its keys are gone."""
        await _sign_up(client)
        await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": "wrong"})
        before = {k: await redis.pttl(k) for k in await _keys(redis, "rl:login:")}
        assert len(before) == 2 and all(ttl > 0 for ttl in before.values()), before

        ok = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert ok.status_code == 200, ok.text

        after = {k: await redis.pttl(k) for k in await _keys(redis, "rl:login:")}
        (ip_key,) = [k for k in before if k.startswith("rl:login:ip:")]
        assert set(after) == {ip_key}, "the address's failure count outlived its success"
        assert 0 < after[ip_key] <= before[ip_key], (before, after)
        assert await redis.get(ip_key) == "1"  # one failure, one success, 2 - 1

    async def test_a_slowdown_key_that_somehow_lost_its_ttl_is_given_one_again(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """The repair in `_LOGIN_HIT`: a wait with no TTL would refuse an address for ever, and a
        failure count with none would never be forgotten."""
        for _ in range(settings.login_slowdown_after_failures):
            await client.post(f"{BASE}/auth/login", json={"email": SLOWED, "password": "wrong"})
        (count_key,) = await _keys(redis, "rl:login:email:")
        (wait_key,) = await _keys(redis, "rl:login:wait:")
        first_ms = settings.login_slowdown_first_wait_seconds * 1000
        window_ms = settings.login_slowdown_window_seconds * 1000
        await redis.persist(wait_key)
        await redis.persist(count_key)
        assert await redis.pttl(wait_key) == -1 and await redis.pttl(count_key) == -1

        # The wait is repaired by the next attempt, which it refuses (it is still a wait)...
        refused = await client.post(
            f"{BASE}/auth/login", json={"email": SLOWED, "password": PASSWORD}
        )
        assert refused.status_code == 401, refused.text
        assert 0 < await redis.pttl(wait_key) <= first_ms
        assert await redis.pttl(count_key) == -1, "a refused attempt wrote the failure count"

        # ...and the failure count by the first attempt that is evaluated after the wait.
        await redis.delete(wait_key)
        again = await client.post(
            f"{BASE}/auth/login", json={"email": SLOWED, "password": "wrong"}
        )
        assert again.status_code == 401, again.text
        assert 0 < await redis.pttl(count_key) <= window_ms + 2 * first_ms

    async def test_a_limiter_key_that_somehow_lost_its_ttl_is_given_one_again(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """The repair in both Lua scripts: a counter with no TTL would otherwise lock for ever."""
        await _sign_up(client)
        keys = await _keys(redis, "rl:signup:")
        assert keys
        for key in keys:
            await redis.persist(key)
            assert await redis.pttl(key) == -1

        again = await client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "Second Agency", "fullName": "Op", "email": "second@expiry.example",
                  "password": PASSWORD},
        )
        assert again.status_code == 201, again.text

        # The IP key is the one both sign-ups share, and it is hit again.
        (ip_key,) = await _keys(redis, "rl:signup:ip:")
        assert 0 < await redis.pttl(ip_key) <= settings.signup_window_seconds * 1000

    async def test_a_detection_day_that_somehow_lost_its_ttl_is_given_one_again(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        await _sign_up(client)
        cid = (
            await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
        ).json()["id"]
        assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
        (key,) = await _keys(redis, f"rl:{rate_limit.DETECT_ACTION}:")
        await redis.persist(key)

        refused = await client.post(f"{BASE}/clients/{cid}/competitors/detect")

        assert refused.status_code == 429, refused.text
        assert 0 < await redis.pttl(key) <= rate_limit.DETECT_WINDOW_MS


class TestASessionLastsFourteenDaysFromSignIn:
    def test_the_default_is_fourteen_days(self) -> None:
        assert Settings(_env_file=None).session_ttl_seconds == FOURTEEN_DAYS_S

    async def test_the_session_and_its_cookie_both_last_the_configured_time(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        ttl_s = settings.session_ttl_seconds
        resp = await _sign_up(client)

        (session_key,) = await _keys(redis, sessions.SESSION_PREFIX)
        (index_key,) = await _keys(redis, sessions.USER_SESSIONS_PREFIX)
        for key in (session_key, index_key):
            assert ttl_s * 1000 - FRESH_MS <= await redis.pttl(key) <= ttl_s * 1000, key
        # The record says the same thing about itself.
        record = sessions.SessionRecord.from_json(await redis.get(session_key))
        assert (record.expires_at - record.created_at).total_seconds() == ttl_s
        # And so does the cookie the browser keeps.
        name = settings.session_cookie_key
        cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith(name))
        assert f"Max-Age={ttl_s}" in cookie, cookie

    async def test_using_the_session_never_extends_it(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """Cut the session's life to a minute, use it every way the API lets it be used, and it is
        still on that minute and not back on fourteen days. This is the test that a wired-in
        `SessionStore.touch`, or any request that re-wrote the key, would fail."""
        me = (await _sign_up(client)).json()
        (session_key,) = await _keys(redis, sessions.SESSION_PREFIX)
        (index_key,) = await _keys(redis, sessions.USER_SESSIONS_PREFIX)
        await redis.pexpire(session_key, 60_000)
        await redis.pexpire(index_key, 60_000)

        for method, path in (
            ("GET", "/auth/me"),
            ("GET", "/dashboard"),
            ("GET", "/clients"),
            ("GET", f"/agencies/{me['agency']['id']}/seats"),
            ("POST", "/dashboard/getting-started/dismiss"),
            ("GET", "/auth/me"),
        ):
            resp = await client.request(method, f"{BASE}{path}")
            assert resp.status_code in (200, 204), (path, resp.status_code, resp.text)

        assert 0 < await redis.pttl(session_key) <= 60_000
        assert 0 < await redis.pttl(index_key) <= 60_000

    async def test_a_second_sign_in_leaves_the_first_session_alone_and_renews_only_the_index(
        self, client: AsyncClient, settings: Settings, redis: Redis
    ) -> None:
        """The index is renewed whenever a session is added, so it outlives every session in it
        (a rule of the store, `sessions.create`); each session keeps its own 14 days."""
        await _sign_up(client)
        (first,) = await _keys(redis, sessions.SESSION_PREFIX)
        (index_key,) = await _keys(redis, sessions.USER_SESSIONS_PREFIX)
        await redis.pexpire(first, 60_000)
        await redis.pexpire(index_key, 60_000)

        ok = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert ok.status_code == 200, ok.text

        assert 0 < await redis.pttl(first) <= 60_000
        assert await redis.pttl(index_key) > settings.session_ttl_seconds * 1000 - FRESH_MS
        assert len(await _keys(redis, sessions.SESSION_PREFIX)) == 2


class TestTheSweepsOwnBounds:
    def test_every_limiter_action_has_a_window_here(self) -> None:
        """A new limiter action is a new key family: it has to be added to `WINDOW_SETTING`, with
        its window, and made to be written by `_drive_every_writer`."""
        assert set(WINDOW_SETTING) == set(get_args(rate_limit.Action))

    def test_every_window_names_a_real_setting(self, settings: Settings) -> None:
        for setting in WINDOW_SETTING.values():
            assert getattr(settings, setting) > 0


# What is imported from `redis` in the API package, and why each module is in this list. A
# module that does not appear here and imports `redis` fails the test below, so that whoever adds
# one says whether it writes. A writer has to give every key a TTL and be driven by
# `_drive_every_writer`; a reader or an error handler has nothing to add.
WRITERS = {
    "sessions.py": "sess:* and user-sessions:*",
    "services/rate_limit.py": "rl:*",
    "services/google_oauth.py": "google-state:* and google-ticket:*",
    "redis_watch.py": "the sentinel",
    "services/spend_caps.py": "cap:* (the platform's daily caps)",
}
NOT_WRITERS = {
    "redis_client.py": "builds the pool",
    "deps.py": "hands the client to routes",
    "errors.py": "maps a Redis outage to a 503",
    "services/scan_start.py": "hands the client to the platform's daily caps",
    "services/password_reset.py": "hands the client to the claim on a link (rate_limit.py)",
    "services/invitations.py": "hands the client to the claim on an invitation (rate_limit.py)",
}


def _modules_importing_redis() -> set[str]:
    root = Path(avp_api.__file__).resolve().parent
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "redis" or name.startswith("redis.") for name in names):
                found.add(path.relative_to(root).as_posix())
    return found


class TestEveryWriterIsKnown:
    def test_a_module_that_talks_to_redis_has_been_classified(self) -> None:
        """The sweep sees only what it drives: this is the tripwire for a writer it never met."""
        assert _modules_importing_redis() == set(WRITERS) | set(NOT_WRITERS), (
            "a module now imports redis (or one stopped): if it WRITES, give every key a TTL, "
            "add its key family to `_families` and drive it in `_drive_every_writer`; then list "
            "it in WRITERS here, or in NOT_WRITERS if it only reads or handles errors"
        )
