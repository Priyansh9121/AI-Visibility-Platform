"""F4 and A6 part 2: `GET /auth/google/start` is limited before it writes anything.

It is unauthenticated and each call used to park one `google-state:` key on the one Redis
that also holds every session, so a flood could fill it. What is pinned:

Per address (F4):

* the 31st start from one IP inside the window is a 302 to `/?google=error&reason=rate-limited`,
  not a problem document, and writes no `google-state:` key (counted before and after);
* starts below the limit still redirect to Google, and another IP has its own budget;
* Redis down: refused with the `unavailable` reason, nothing minted (fails closed);
* the only new key is the limiter's own, and the refusal log line carries no IP.

For the whole deployment (A6 part 2, launch checklist 3.120, decided 2026-10-11):

* one ceiling of 300 ADMITTED starts per window (a setting), in one key that names no address;
* it is read FIRST: at the ceiling a start from ANY address (one never seen, or one already past
  its own limit) is a 302 to `reason=busy` and writes nothing, no IP key, no state, no cookie,
  and leaves the count where it was;
* it counts admitted starts only: one address's 31 starts leave it at 30, and a start that
  passes counts once in each; the 300th passes and the 301st is busy;
* its window is the address's window, fixed from the first start, and a ceiling that lost its
  TTL is given one again, on the refusal path and on the admitted one (or it would refuse for
  ever); when the window ends, starts are admitted again;
* password sign-in is not touched; the refusal's log line carries counts and no address.

Fix round (security review M1, L1, L3):

* only a person's own navigation is counted: a request whose Fetch Metadata headers show that it is
  not a top-level navigation (cross-site, or a mode other than `navigate`, or a destination other
  than `document`) is a 302 to the front door with no reason word, and writes nothing at all (no
  ceiling count, no IP key, no state, no cookie), so a page the product does not control cannot
  spend the deployment's slots; its log line carries one word from a closed set and no header
  value. A navigation (same-origin, same-site, none) and a request with no such headers are
  admitted as before;
* a refusal at the ceiling leaves its window alone (it does not renew it);
* the ceiling may not be set below twice the per-address limit (the API will not start).
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
import structlog
from httpx import AsyncClient, Response
from pydantic import ValidationError
from redis.asyncio import Redis

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client
from avp_api.services.google_oauth import STATE_PREFIX
from test_google_sign_in import (  # noqa: F401
    BASE,
    WEB,
    FakeGoogleProvider,
    flow_cookie_header,
    google,
    sign_up_with_password,
)

IP_HEADER = "X-Test-Client-IP"
IP = "203.0.113.50"
OTHER_IP = "198.51.100.9"
LIMIT = 30
CEILING = 300
# The one key the deployment-wide count lives in. A literal on purpose: the runbook names it, so a
# rename has to come through here.
GLOBAL_KEY = "rl:google_start:global"
# How far under its full window a key written a moment ago may already be.
FRESH_MS = 30_000
GOOGLE = "https://accounts.google.com/"
# What a browser sends when a person presses the button on the web app's own page.
NAVIGATION = {
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}


@pytest.fixture(autouse=True)
def _header(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)


async def _start(  # noqa: ANN202
    client: AsyncClient, ip: str = IP, headers: dict[str, str] | None = None
):
    return await client.get(
        f"{BASE}/auth/google/start",
        headers={IP_HEADER: ip, **(headers or {})},
        follow_redirects=False,
    )


async def _count(redis: Redis, prefix: str) -> int:
    return len([k async for k in redis.scan_iter(f"{prefix}*")])


async def _keys(redis: Redis) -> set[str]:
    return {k async for k in redis.scan_iter("*")}


async def _fill_the_ceiling(redis: Redis, settings: Settings, count: int = CEILING) -> None:
    """Set the deployment-wide count where `count` admitted starts would have left it, with the
    window (so a test does not make 300 real starts)."""
    await redis.set(GLOBAL_KEY, count, px=settings.google_start_window_seconds * 1000)


def _reason(resp: Response) -> str:
    """The reason word of a 302 back to the front door; fails on anything else."""
    assert resp.status_code == 302, resp.text
    where = urlparse(resp.headers["location"])
    assert where.path == "/", resp.headers["location"]
    query = parse_qs(where.query)
    assert query["google"] == ["error"]
    (reason,) = query["reason"]
    return reason


def test_default_settings() -> None:
    s = Settings(_env_file=None)
    assert (s.google_start_limit_per_ip, s.google_start_window_seconds) == (30, 900)


def test_the_ceiling_defaults_to_300_in_the_same_window() -> None:
    """Read off the fields and not off a `Settings` built from the environment, which a shell or
    CI that set GOOGLE_START_LIMIT_GLOBAL would change."""
    fields = Settings.model_fields
    assert fields["google_start_limit_global"].default == CEILING
    assert fields["google_start_limit_per_ip"].default == LIMIT
    assert fields["google_start_window_seconds"].default == 15 * 60


@pytest.mark.parametrize("bad", [0, -1])
def test_the_ceiling_must_be_at_least_one(bad: int) -> None:
    with pytest.raises(ValidationError, match="google_start_limit_global"):
        Settings(_env_file=None, google_start_limit_global=bad)


async def test_the_31st_start_is_a_redirect_with_a_reason_and_writes_no_state(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    for i in range(LIMIT):
        resp = await _start(client)
        assert resp.status_code == 302, (i, resp.text)
        assert resp.headers["location"].startswith("https://accounts.google.com/")
    before = await _count(redis, STATE_PREFIX)
    assert before == LIMIT
    with structlog.testing.capture_logs() as logs:
        refused = await _start(client)
    assert refused.status_code == 302
    where = urlparse(refused.headers["location"])
    assert where.path == "/"
    assert parse_qs(where.query) == {"google": ["error"], "reason": ["rate-limited"]}
    assert "set-cookie" not in refused.headers
    assert await _count(redis, STATE_PREFIX) == before
    limited = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert limited == [
        {
            "event": "auth.rate_limited",
            "log_level": "warning",
            "action": "google_start",
            "kind": "ip",
        }
    ]
    assert IP not in repr(logs)


async def test_the_only_other_key_is_the_limiters_and_another_ip_has_its_own_budget(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    for _ in range(LIMIT + 1):
        await _start(client)
    assert (await _start(client)).headers["location"].endswith("reason=rate-limited")
    keys = {k async for k in redis.scan_iter("*")}
    assert len([k for k in keys if k.startswith("rl:google_start:ip:")]) == 1
    # A6 part 2: the deployment-wide count is the one other limiter key a start may leave. This
    # line allowed `rl:google_start:ip:` alone before, and now allows exactly the global key too.
    assert GLOBAL_KEY in keys
    assert not [
        k
        for k in keys
        if k.startswith("rl:") and not k.startswith("rl:google_start:ip:") and k != GLOBAL_KEY
    ]
    assert IP not in "".join(keys)
    other = await _start(client, ip="198.51.100.9")
    assert other.headers["location"].startswith("https://accounts.google.com/")


async def test_redis_down_is_refused_closed_with_no_state(
    client: AsyncClient, google: FakeGoogleProvider  # noqa: F811
) -> None:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
    app.dependency_overrides[redis_client] = lambda: dead
    try:
        with structlog.testing.capture_logs() as logs:
            resp = await _start(client)
    finally:
        await dead.aclose()
    assert resp.status_code == 302
    assert parse_qs(urlparse(resp.headers["location"]).query)["reason"] == ["unavailable"]
    assert google.last_state is None
    assert [e for e in logs if e["event"] == "auth.rate_limit.unavailable"] == [
        {"event": "auth.rate_limit.unavailable", "log_level": "warning"}
    ]


# --- the ceiling over every address (A6 part 2) --------------------------------------------


async def test_with_the_ceiling_reached_a_start_is_busy_and_writes_nothing(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings)

    refused = await _start(client, ip=OTHER_IP)  # an address this Redis has never seen

    assert _reason(refused) == "busy"
    assert "set-cookie" not in refused.headers
    assert google.last_state is None, "a flow was minted for a refused start"
    assert await _count(redis, "rl:google_start:ip:") == 0, "the refusal wrote an IP key"
    assert await _count(redis, STATE_PREFIX) == 0
    assert await redis.get(GLOBAL_KEY) == str(CEILING), "the refusal changed the count"
    assert await _keys(redis) == {GLOBAL_KEY}


async def test_the_ceiling_is_read_before_the_address_even_for_one_past_its_own_limit(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    for _ in range(LIMIT + 1):
        last = await _start(client)
    assert _reason(last) == "rate-limited"
    (ip_key,) = [k async for k in redis.scan_iter("rl:google_start:ip:*")]
    assert await redis.get(ip_key) == str(LIMIT + 1)  # refused attempts still count

    await _fill_the_ceiling(redis, settings)
    refused = await _start(client)  # the same address, past its own limit AND at the ceiling

    # Busy and not rate-limited: the ceiling is looked at first. And the address was not counted.
    assert _reason(refused) == "busy"
    assert await redis.get(ip_key) == str(LIMIT + 1)


async def test_one_addresses_refused_starts_do_not_use_up_the_ceiling(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    for _ in range(LIMIT + 10):
        last = await _start(client)
    assert _reason(last) == "rate-limited"
    # 30 were admitted; the 10 + 1 refused after them were not counted against everyone.
    assert await redis.get(GLOBAL_KEY) == str(LIMIT)

    other = await _start(client, ip=OTHER_IP)
    assert other.headers["location"].startswith(GOOGLE)
    assert await redis.get(GLOBAL_KEY) == str(LIMIT + 1)


async def test_a_start_that_passes_counts_once_in_each(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    for ip in (IP, OTHER_IP):
        passed = await _start(client, ip=ip)
        assert passed.headers["location"].startswith(GOOGLE)

    assert await redis.get(GLOBAL_KEY) == "2"
    ip_keys = sorted([k async for k in redis.scan_iter("rl:google_start:ip:*")])
    assert len(ip_keys) == 2
    assert [await redis.get(k) for k in ip_keys] == ["1", "1"]
    assert await _count(redis, STATE_PREFIX) == 2


async def test_the_last_start_under_the_ceiling_passes_and_the_next_one_is_busy(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings, count=CEILING - 1)

    last = await _start(client)
    assert last.headers["location"].startswith(GOOGLE)
    assert await redis.get(GLOBAL_KEY) == str(CEILING)

    before = await _keys(redis)
    over = await _start(client, ip=OTHER_IP)
    assert _reason(over) == "busy"
    assert await _keys(redis) == before, "the 301st start wrote a key"
    assert await redis.get(GLOBAL_KEY) == str(CEILING)


async def test_the_ceilings_window_is_the_windows_and_a_later_start_does_not_renew_it(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    window_ms = settings.google_start_window_seconds * 1000
    await _start(client)
    assert window_ms - FRESH_MS <= await redis.pttl(GLOBAL_KEY) <= window_ms

    await redis.pexpire(GLOBAL_KEY, 5_000)  # most of the window has gone
    await _start(client, ip=OTHER_IP)

    assert await redis.get(GLOBAL_KEY) == "2"
    assert 0 < await redis.pttl(GLOBAL_KEY) <= 5_000, "a start gave the ceiling a new window"


async def test_a_ceiling_at_its_limit_that_lost_its_ttl_is_given_one_again_by_the_next_start(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    """Else it would refuse for ever: nothing else would ever end it."""
    redis = redis_module.get_redis(settings)
    window_ms = settings.google_start_window_seconds * 1000
    await redis.set(GLOBAL_KEY, CEILING)  # no expiry
    assert await redis.pttl(GLOBAL_KEY) == -1

    refused = await _start(client, ip=OTHER_IP)

    assert _reason(refused) == "busy"
    assert window_ms - FRESH_MS <= await redis.pttl(GLOBAL_KEY) <= window_ms
    assert await redis.get(GLOBAL_KEY) == str(CEILING)
    assert await _keys(redis) == {GLOBAL_KEY}


async def test_a_counter_under_its_limit_that_lost_its_ttl_is_given_one_again_by_the_next_start(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    """Both of them: the ceiling, and the address's own."""
    redis = redis_module.get_redis(settings)
    window_ms = settings.google_start_window_seconds * 1000
    await _start(client)
    (ip_key,) = [k async for k in redis.scan_iter("rl:google_start:ip:*")]
    await redis.persist(GLOBAL_KEY)
    await redis.persist(ip_key)
    assert await redis.pttl(GLOBAL_KEY) == -1 and await redis.pttl(ip_key) == -1

    again = await _start(client)

    assert again.headers["location"].startswith(GOOGLE)
    for key in (GLOBAL_KEY, ip_key):
        assert window_ms - FRESH_MS <= await redis.pttl(key) <= window_ms, key
    assert await redis.get(GLOBAL_KEY) == "2"


async def test_when_the_window_ends_starts_are_admitted_again(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings)
    assert _reason(await _start(client, ip=OTHER_IP)) == "busy"

    await redis.pexpire(GLOBAL_KEY, 1)  # the window runs out
    await asyncio.sleep(0.05)
    assert await redis.exists(GLOBAL_KEY) == 0

    again = await _start(client, ip=OTHER_IP)
    assert again.headers["location"].startswith(GOOGLE)
    assert await redis.get(GLOBAL_KEY) == "1"


@pytest.fixture
def a_ceiling_of_three(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The setting, lowered, and the per-address limit with it: a ceiling below twice that limit is
    refused at start (L3), so the pair is built through `Settings` first to be sure it is a legal
    one. A test asks for this BEFORE `google` in its arguments: `google` copies the settings when
    it is made."""
    Settings(_env_file=None, google_start_limit_global=3, google_start_limit_per_ip=1)
    monkeypatch.setattr(settings, "google_start_limit_global", 3)
    monkeypatch.setattr(settings, "google_start_limit_per_ip", 1)


async def test_the_ceiling_is_the_setting_and_the_log_line_says_so(
    a_ceiling_of_three: None,
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    settings: Settings,
) -> None:
    redis = redis_module.get_redis(settings)
    for n, ip in enumerate(("192.0.2.1", "192.0.2.2", "192.0.2.3"), start=1):
        assert (await _start(client, ip=ip)).headers["location"].startswith(GOOGLE)
        assert await redis.get(GLOBAL_KEY) == str(n)

    with structlog.testing.capture_logs() as logs:
        refused = await _start(client, ip="192.0.2.4")

    assert _reason(refused) == "busy"
    (line,) = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert (line["count"], line["limit"]) == (3, 3)


async def test_the_busy_refusal_logs_its_counts_and_no_address(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings)

    with structlog.testing.capture_logs() as logs:
        refused = await _start(client, ip=OTHER_IP)

    assert _reason(refused) == "busy"
    assert [e for e in logs if e["event"].startswith("auth.rate_limit")] == [
        {
            "event": "auth.rate_limited",
            "log_level": "warning",
            "action": "google_start",
            "kind": "global",
            "count": CEILING,
            "limit": CEILING,
        }
    ]
    flat = repr(logs)
    assert OTHER_IP not in flat
    assert "rl:" not in flat, "a key name is in the log"


async def test_password_sign_in_is_not_touched_by_a_full_ceiling(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    email = "dana@northlight.example"  # the sign-up validator refuses a .test address
    await sign_up_with_password(client, email)
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings)

    signed_in = await client.post(
        f"{BASE}/auth/login", json={"email": email, "password": "correct-horse-battery-staple"}
    )

    assert signed_in.status_code == 200, signed_in.text
    assert (await client.get(f"{BASE}/auth/me")).status_code == 200
    # Google is still busy, and the sign-in neither counted against the ceiling nor gave back.
    assert _reason(await _start(client, ip=OTHER_IP)) == "busy"
    assert await redis.get(GLOBAL_KEY) == str(CEILING)


async def test_an_unconfigured_deployment_counts_nothing(
    client: AsyncClient, settings: Settings
) -> None:
    """The 503 is answered before the limiter, so a deployment with no OAuth client cannot use up
    the ceiling (or write any limiter key) by being asked."""
    resp = await _start(client)
    assert resp.status_code == 503, resp.text
    assert await _count(redis_module.get_redis(settings), "rl:") == 0


# --- a refusal at the ceiling leaves its window alone (fix round, security review L1) -------


async def test_a_refusal_at_the_ceiling_leaves_its_window_alone(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    """Else every `busy` start would push the window out again, and people retrying as the sentence
    tells them to would keep it busy for as long as they tried. Only a ceiling with no TTL at all is
    given one (above); one with time left keeps exactly that."""
    redis = redis_module.get_redis(settings)
    await redis.set(GLOBAL_KEY, CEILING, px=5_000)

    refused = await _start(client, ip=OTHER_IP)

    assert _reason(refused) == "busy"
    assert 0 < await redis.pttl(GLOBAL_KEY) <= 5_000, "a refusal renewed the window"
    assert await redis.get(GLOBAL_KEY) == str(CEILING)


# --- the ceiling may not be set below twice the per-address limit (security review L3) ------


@pytest.mark.parametrize(
    ("ceiling", "per_ip", "accepted"),
    [
        pytest.param(59, 30, False, id="one under twice the per-address limit"),
        pytest.param(60, 30, True, id="twice the per-address limit"),
        pytest.param(300, 30, True, id="the defaults"),
        pytest.param(1, 1, False, id="a ceiling equal to the limit"),
        pytest.param(2, 1, True, id="the smallest legal pair"),
    ],
)
def test_the_ceiling_may_not_be_below_twice_the_per_address_limit(
    ceiling: int, per_ip: int, accepted: bool
) -> None:
    def build() -> Settings:
        return Settings(
            _env_file=None, google_start_limit_global=ceiling, google_start_limit_per_ip=per_ip
        )

    if accepted:
        built = build()
        pair = (built.google_start_limit_global, built.google_start_limit_per_ip)
        assert pair == (ceiling, per_ip)
        return
    with pytest.raises(ValidationError, match="GOOGLE_START_LIMIT_GLOBAL") as refused:
        build()
    (error,) = refused.value.errors()
    assert "GOOGLE_START_LIMIT_PER_IP" in error["msg"], "the message must name both settings"
    assert not any(ch.isdigit() for ch in error["msg"]), "the message carries a value"


# --- only a person's own navigation is counted (fix round, security review M1) ---------------

# (the Fetch Metadata a browser sent, the cause the log names). `Sec-Fetch-Site` is left out where a
# case is about the mode or the destination alone. Where a request has two reasons, the first of
# site, mode, destination is its cause.
NOT_A_NAVIGATION = [
    pytest.param(
        {**NAVIGATION, "Sec-Fetch-Site": "cross-site"},
        "cross-site",
        id="another site's page sends the browser here",
    ),
    pytest.param(
        {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}, "not-navigate", id="an image"
    ),
    pytest.param(
        {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}, "not-navigate", id="a fetch"
    ),
    pytest.param(
        {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}, "not-document", id="a frame"
    ),
    pytest.param(
        {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"},
        "not-navigate",
        id="the web app's own fetch (a prefetch, say)",
    ),
    pytest.param(
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"},
        "cross-site",
        id="two reasons: the site comes first",
    ),
    pytest.param(
        {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "iframe"},
        "not-navigate",
        id="two reasons: the mode comes before the destination",
    ),
    pytest.param({"Sec-Fetch-Site": "Cross-Site"}, "cross-site", id="letter case does not hide it"),
]


@pytest.mark.parametrize(("headers", "cause"), NOT_A_NAVIGATION)
async def test_a_request_that_is_not_a_navigation_goes_to_the_front_door_and_counts_for_nothing(
    headers: dict[str, str],
    cause: str,
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    settings: Settings,
) -> None:
    redis = redis_module.get_redis(settings)

    with structlog.testing.capture_logs() as logs:
        refused = await _start(client, ip=OTHER_IP, headers=headers)

    assert refused.status_code == 302
    assert refused.headers["location"] == f"{WEB}/", "a reason word or a query came with it"
    assert "set-cookie" not in refused.headers
    assert google.last_state is None, "a flow was minted"
    # Nothing at all: no ceiling count, no `rl:` key for the address, no `google-state:` key.
    assert await _keys(redis) == set()
    # One line, carrying one word from the closed set and no header value; no limiter line.
    assert [e for e in logs if e["event"].startswith("auth.")] == [
        {"event": "auth.google_start.not_navigation", "log_level": "warning", "cause": cause}
    ]
    assert OTHER_IP not in repr(logs)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no Fetch Metadata at all (an older browser, a script)"),
        pytest.param(NAVIGATION, id="the web app's own button"),
        pytest.param({**NAVIGATION, "Sec-Fetch-Site": "same-site"}, id="a sibling subdomain"),
        pytest.param({**NAVIGATION, "Sec-Fetch-Site": "none"}, id="an address typed or bookmarked"),
        pytest.param({"Sec-Fetch-Site": "same-origin"}, id="only the site header"),
        pytest.param({"Sec-Fetch-Mode": "navigate"}, id="only the mode header"),
        pytest.param({"Sec-Fetch-Dest": "document"}, id="only the destination header"),
        pytest.param(
            {"Sec-Fetch-Site": "", "Sec-Fetch-Mode": "", "Sec-Fetch-Dest": ""},
            id="present but empty is no evidence",
        ),
    ],
)
async def test_a_navigation_or_a_request_with_no_metadata_is_admitted_as_before(
    headers: dict[str, str],
    client: AsyncClient,
    google: FakeGoogleProvider,  # noqa: F811
    settings: Settings,
) -> None:
    redis = redis_module.get_redis(settings)

    with structlog.testing.capture_logs() as logs:
        resp = await _start(client, headers=headers)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith(GOOGLE)
    flow_cookie_header(resp)  # the flow cookie is set, once
    assert google.last_state is not None
    assert await redis.get(GLOBAL_KEY) == "1"
    assert await _count(redis, "rl:google_start:ip:") == 1
    assert await _count(redis, STATE_PREFIX) == 1
    assert [e for e in logs if e["event"] == "auth.google_start.not_navigation"] == []


async def test_requests_that_are_not_navigations_spend_neither_the_ceiling_nor_an_address(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    """Security review M1: pages the product does not control make visitors' browsers send requests
    here, and each used to take one of the deployment's slots (and one of the visitor's own)."""
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings, count=CEILING - 1)
    from_another_site = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "image",
    }

    for _ in range(LIMIT + 5):  # more than one address's whole allowance
        refused = await _start(client, headers=from_another_site)
        assert refused.headers["location"] == f"{WEB}/"

    assert await _keys(redis) == {GLOBAL_KEY}, "a refused request wrote a key"
    assert await redis.get(GLOBAL_KEY) == str(CEILING - 1)
    # The last slot, and the address's whole allowance, are still there for a person's press.
    pressed = await _start(client, headers=NAVIGATION)
    assert pressed.headers["location"].startswith(GOOGLE)
    assert await redis.get(GLOBAL_KEY) == str(CEILING)


async def test_with_the_ceiling_reached_a_request_that_is_not_a_navigation_gets_no_sentence(
    client: AsyncClient, google: FakeGoogleProvider, settings: Settings  # noqa: F811
) -> None:
    """The gate is before the ceiling: nobody pressed anything, so there is nobody to tell it is
    busy, and the `busy` verdict is not computed for it."""
    redis = redis_module.get_redis(settings)
    await _fill_the_ceiling(redis, settings)

    refused = await _start(client, ip=OTHER_IP, headers={"Sec-Fetch-Site": "cross-site"})

    assert refused.status_code == 302
    assert refused.headers["location"] == f"{WEB}/"
    assert await _keys(redis) == {GLOBAL_KEY}
    assert await redis.get(GLOBAL_KEY) == str(CEILING)
