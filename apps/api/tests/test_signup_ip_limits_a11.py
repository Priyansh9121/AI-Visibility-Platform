"""A11 (the founder's D11): sign-up is limited per IP, 5 an hour and 20 a day, for every route that
makes a new agency, as one shared budget.

The two routes that make an agency are `POST /auth/sign-up` and `POST /auth/google/complete`.
Nothing else does: an invitation's accept and Google's join take a seat in an agency that exists,
Google's decline frees one, and the Google callback parks a ticket and creates nothing. What is
pinned here:

* the defaults are the founder's numbers and each is a setting an operator can change;
* the 6th sign-up from one IP in an hour is a 429, and the 21st in a day is a 429 even when no hour
  held more than 5 (time is controlled through the keys, never slept);
* different IPs are independent, and the per-address window (5 an hour, password sign-up only)
  stays;
* sign-up and Google complete count in the SAME two IP keys, so three sign-ups and two completions
  use the budget of five, and the next of either kind is refused;
* IP first, in one step: a refusal on either IP window writes no address key, and a Google
  completion never writes one;
* the hour counts every attempt, a refused one included, but the DAY counts only the attempts the
  hour let through (fix round, security review L1). So a burst uses at most one hour's 5 of the
  day's 20 and cannot by itself close a bucket for a day: a burst of 21 from a bucket that has used
  nothing closes it for the rest of its hour and leaves 15 of its day (a bucket that has already
  used most of its day IS closed for the rest of it by the hour's 5, and that is pinned too). A day
  closes only after 20 attempts the hour admitted, across several hours. When the hour refuses, the
  day is only READ: never counted, never created, its TTL given back if it was lost, and a full day
  is told in the wait;
* a refused Google completion leaves its ticket unspent (and its flow cookie where it was), because
  the limit is checked before the ticket is read;
* Redis down is a 503 on both routes (fail closed), from the limiter, with nothing created;
* `Retry-After` is the larger wait of the IP windows that refused, and the sentence is the
  founder's existing one, unchanged (so a day-long wait reads in minutes);
* the keys live their windows, are fixed (a later attempt does not renew them), and the log line
  names the window that refused without naming an address, an IP or a key;
* a request the schema refuses (a 422) is never counted.

The schema-only input checks on both routes are in `test_signup_input_checks_a11.py`. The sweep that
every key expires is `test_redis_expiry.py`, which holds the new `rl:signup:ip_day:` family.
"""

from __future__ import annotations

import asyncio

import pytest
import structlog
from httpx import AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client
from avp_api.models import Agency, User
from avp_api.security import token_digest
from avp_api.services import rate_limit
from avp_api.services.google_oauth import (
    TICKET_PREFIX,
    GoogleFlowStore,
    GoogleIdentity,
    InvitationOffer,
)
from avp_api.services.rate_limit import SIGNUP_GLOBAL_KEY

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
IP_HEADER = "X-Test-Client-IP"
IP = "203.0.113.10"
OTHER_IP = "198.51.100.20"
HOUR_S = 60 * 60
DAY_S = 24 * HOUR_S
# How far under its full window a key written a moment ago may already be.
FRESH_MS = 30_000
# The founder's sentence (`rate_limit._too_many`), unchanged by A11. A wait is told in minutes, and
# one over 60 minutes in hours, rounded up (founder's rule k), so the day's reads "24 hours".
HOUR_SENTENCE = "Too many attempts. Try again in 60 minutes."
DAY_SENTENCE = "Too many attempts. Try again in 24 hours."
BUSY = "Sign-up is busy right now. Try again in "  # the founder's sentence (A11, i), verbatim


@pytest.fixture(autouse=True)
def _limits(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The founder's numbers and the header the limiter reads, set here so that these tests say
    what they test whatever the environment holds."""
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)
    monkeypatch.setattr(settings, "signup_limit_per_ip", 5)
    monkeypatch.setattr(settings, "signup_limit_per_ip_per_day", 20)
    monkeypatch.setattr(settings, "signup_limit_per_email", 5)
    monkeypatch.setattr(settings, "signup_window_seconds", HOUR_S)
    monkeypatch.setattr(settings, "signup_day_window_seconds", DAY_S)
    monkeypatch.setattr(settings, "signup_limit_global", 100)


# --- helpers --------------------------------------------------------------------------------------


def _email(i: int) -> str:
    return f"person{i}@signup-limits.example"


async def _sign_up(client: AsyncClient, i: int, *, ip: str = IP, email: str | None = None):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": f"Limits Agency {i}",
            "fullName": "Operator",
            "email": email or _email(i),
            "password": PASSWORD,
        },
        headers={IP_HEADER: ip},
    )


async def _ticket(
    client: AsyncClient,
    settings: Settings,
    n: int,
    *,
    binding: str | None = None,
    offer: InvitationOffer | None = None,
) -> str:
    """A ticket for a NEW Google address, bound to this client's flow cookie (which is set here).

    Parked the way the callback parks one, so the completion route finds exactly what it finds in
    use; no provider is involved. Tickets made with the same `binding` can be spent together by
    one client, which holds one flow cookie. With an `offer` it is an invited address's ticket.
    """
    binding = binding or f"binding-{n}-" + "b" * 32
    client.cookies.set(settings.google_flow_cookie_key, binding)
    identity = GoogleIdentity(
        sub=f"google-sub-{n}",
        email=f"gina{n}@signup-limits.example",
        email_verified=True,
        name="Gina Google",
        nonce=None,
    )
    flow = GoogleFlowStore(redis_module.get_redis(settings), settings)
    return await flow.issue_ticket(identity, token_digest(binding), offer)


async def _complete(client: AsyncClient, ticket: str, *, ip: str = IP):  # noqa: ANN202
    return await client.post(
        f"{BASE}/auth/google/complete",
        json={"ticket": ticket, "agencyName": "Gina Agency", "fullName": "Gina"},
        headers={IP_HEADER: ip},
    )


async def _attempt(  # noqa: ANN202
    client: AsyncClient, settings: Settings, route: str, i: int, *, ip: str = IP
):
    """One attempt to make an agency by `route` ("sign-up" or "google"); `i` makes it a new person.

    The two routes share one budget, so the fix-round tests below run on both.
    """
    if route == "sign-up":
        return await _sign_up(client, i, ip=ip)
    return await _complete(client, await _ticket(client, settings, i), ip=ip)


def _ip_keys(settings: Settings, ip: str = IP) -> tuple[str, str]:
    """The IP's hour key and day key, worked out with the module's own key function."""
    return (
        rate_limit._key(settings, "signup", "ip", ip),  # noqa: SLF001
        rate_limit._key(settings, "signup", "ip_day", ip),  # noqa: SLF001
    )


async def _prime(
    redis: Redis,
    settings: Settings,
    *,
    hour: tuple[int, int] | None = None,
    day: tuple[int, int] | None = None,
    ip: str = IP,
) -> None:
    """Put the IP's counters where a run of attempts would have left them: (count, ms left).

    This is how time is controlled here: through the keys' own values and TTLs, never a sleep.
    """
    hour_key, day_key = _ip_keys(settings, ip)
    if hour is not None:
        await redis.set(hour_key, hour[0], px=hour[1])
    if day is not None:
        await redis.set(day_key, day[0], px=day[1])


async def _keys(redis: Redis, pattern: str = "rl:signup:*") -> list[str]:
    return sorted([k async for k in redis.scan_iter(pattern)])


def _redis(settings: Settings) -> Redis:
    return redis_module.get_redis(settings)


async def _count(engine, model) -> int:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


def _assert_rate_limited(resp: Response, detail: str | None = None) -> None:
    assert resp.status_code == 429, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/rate-limited"
    assert body["detail"].startswith("Too many attempts. Try again in ")
    assert int(resp.headers["retry-after"]) > 0
    if detail is not None:
        assert body["detail"] == detail


# --- the settings ---------------------------------------------------------------------------------


def test_the_defaults_are_the_founders_numbers() -> None:
    """Read off the fields and not off a `Settings` built from the environment, which a shell or CI
    that set SIGNUP_LIMIT_PER_IP would change."""
    fields = Settings.model_fields
    assert fields["signup_limit_per_ip"].default == 5
    assert fields["signup_window_seconds"].default == HOUR_S
    assert fields["signup_limit_per_ip_per_day"].default == 20
    assert fields["signup_day_window_seconds"].default == DAY_S
    assert fields["signup_limit_per_email"].default == 5
    assert fields["signup_limit_global"].default == 100


def test_every_figure_is_configurable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The names an operator sets on Render, as `.env.example` lists them. A value left set from
    before wins over the new default: SIGNUP_LIMIT_PER_IP=10 on Render stays 10 an hour."""
    monkeypatch.setenv("SIGNUP_LIMIT_PER_IP", "10")
    monkeypatch.setenv("SIGNUP_WINDOW_SECONDS", "1800")
    monkeypatch.setenv("SIGNUP_LIMIT_PER_IP_PER_DAY", "7")
    monkeypatch.setenv("SIGNUP_DAY_WINDOW_SECONDS", "43200")
    monkeypatch.setenv("SIGNUP_LIMIT_PER_EMAIL", "2")
    monkeypatch.setenv("SIGNUP_LIMIT_GLOBAL", "40")
    s = Settings(_env_file=None)
    assert s.signup_limit_per_ip == 10
    assert s.signup_window_seconds == 1800
    assert s.signup_limit_per_ip_per_day == 7
    assert s.signup_day_window_seconds == 43200
    assert s.signup_limit_per_email == 2
    assert s.signup_limit_global == 40


# --- the hour -------------------------------------------------------------------------------------


async def test_the_sixth_sign_up_from_one_ip_in_an_hour_is_a_429(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    for i in range(5):
        resp = await _sign_up(client, i)
        assert resp.status_code == 201, (i, resp.text)

    with structlog.testing.capture_logs() as logs:
        sixth = await _sign_up(client, 5)

    _assert_rate_limited(sixth, HOUR_SENTENCE)
    assert HOUR_S - 30 <= int(sixth.headers["retry-after"]) <= HOUR_S
    assert [(e["action"], e["kind"]) for e in logs if e["event"] == "auth.rate_limited"] == [
        ("signup", "ip")
    ]
    # Nothing was made for the refused one; the five that passed made five agencies.
    assert await _count(engine, User) == 5
    assert await _count(engine, Agency) == 5


async def test_another_ip_has_its_own_budget(client: AsyncClient) -> None:
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    assert (await _sign_up(client, 5)).status_code == 429

    other = await _sign_up(client, 6, ip=OTHER_IP)

    assert other.status_code == 201, other.text


async def test_the_hour_ends_and_sign_ups_are_admitted_again(
    client: AsyncClient, settings: Settings
) -> None:
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    assert (await _sign_up(client, 5)).status_code == 429
    hour_key, _ = _ip_keys(settings)

    await _redis(settings).delete(hour_key)  # what the hour's end does to the key

    assert (await _sign_up(client, 6)).status_code == 201


# --- the day --------------------------------------------------------------------------------------


async def test_the_21st_sign_up_in_a_day_is_a_429_though_no_hour_held_more_than_five(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    n = 0
    for _batch in range(4):
        for _ in range(5):
            resp = await _sign_up(client, n)
            assert resp.status_code == 201, (n, resp.text)
            n += 1
        # The hour passes (what its end does to the key). The day's key is not touched.
        await redis.delete(hour_key)
    assert await redis.get(day_key) == "20"

    with structlog.testing.capture_logs() as logs:
        refused = await _sign_up(client, n)

    _assert_rate_limited(refused, DAY_SENTENCE)
    # It is the day that said no: the wait is the day's. The hour's key had ended, and a refused
    # attempt never creates a key (round 3), so it is still not there.
    assert DAY_S - 30 <= int(refused.headers["retry-after"]) <= DAY_S
    assert await redis.exists(hour_key) == 0
    assert [(e["action"], e["kind"]) for e in logs if e["event"] == "auth.rate_limited"] == [
        ("signup", "ip_day")
    ]

    # Another address is untouched by this one's day, and this one's day ends by itself.
    assert (await _sign_up(client, n + 1, ip=OTHER_IP)).status_code == 201
    await redis.delete(day_key)  # what the day's end does to the key
    assert (await _sign_up(client, n + 2)).status_code == 201


async def test_a_refused_attempt_counts_in_the_hour_and_not_in_the_day(
    client: AsyncClient, settings: Settings
) -> None:
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    for i in range(5, 8):
        assert (await _sign_up(client, i)).status_code == 429

    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    assert await redis.get(hour_key) == "8", "a refused attempt is counted in the hour"
    assert await redis.get(day_key) == "5", "the day counted an attempt the hour refused"


# --- the day counts only what the hour lets through (fix round, security review L1) -------------


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_a_burst_of_21_closes_only_the_hour(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)

    with structlog.testing.capture_logs() as logs:
        results = [await _attempt(client, settings, route, i) for i in range(21)]

    refused = [r for r in results if r.status_code == 429]
    assert len(refused) == 16, [r.status_code for r in results]
    # They were told the hour's wait, never a day's, and it was the hour that refused them.
    assert all(int(r.headers["retry-after"]) <= HOUR_S for r in refused)
    assert {e["kind"] for e in logs if e["event"] == "auth.rate_limited"} == {"ip"}
    assert await redis.get(hour_key) == "21"
    assert await redis.get(day_key) == "5"
    # The hour ends (what its key's expiry does) and the bucket is admitted again, with 15 of its
    # day left: the burst did not close it for a day.
    await redis.delete(hour_key)
    again = await _attempt(client, settings, route, 99)
    assert again.status_code != 429, again.text
    assert await redis.get(day_key) == "6"


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_the_hours_five_can_close_a_bucket_that_already_used_most_of_its_day(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    """The other half of the burst rule. A burst uses at most one hour's 5 of the day's 20, so it
    cannot by itself close a bucket for a day; but a bucket that has already used 16 of its 20 is
    closed for the rest of the day by the hour's 5 (the 5th is the one the day refuses)."""
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    await _prime(redis, settings, day=(16, 86_000_000))

    results = [await _attempt(client, settings, route, i) for i in range(10)]

    # 17, 18, 19 and 20 of the day were let through; the 5th the hour admits is the 21st of the day,
    # and the five after it are the hour's.
    assert [r.status_code == 429 for r in results] == [False] * 4 + [True] * 6
    assert await redis.get(hour_key) == "10"
    assert await redis.get(day_key) == "21", "the hour's refusals counted in the day"
    # The hour passes. The day is still full: the bucket stays closed, told a day's wait.
    await redis.delete(hour_key)
    with structlog.testing.capture_logs() as logs:
        closed = await _attempt(client, settings, route, 99)
    _assert_rate_limited(closed)
    assert 85_900 <= int(closed.headers["retry-after"]) <= 86_000
    assert [e["kind"] for e in logs if e["event"] == "auth.rate_limited"] == ["ip_day"]


async def test_a_day_closes_only_after_20_attempts_the_hour_let_through_however_many_it_refused(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    n = 0
    refused_in: list[list[Response]] = []

    with structlog.testing.capture_logs() as logs:
        for _window in range(4):
            for _ in range(5):  # the hour lets five through...
                resp = await _sign_up(client, n)
                assert resp.status_code == 201, (n, resp.text)
                n += 1
            refused_in.append([])
            for _ in range(3):  # ...and refuses the next three, which are counted in the hour
                resp = await _sign_up(client, n)
                assert resp.status_code == 429
                refused_in[-1].append(resp)
                n += 1
            await redis.delete(hour_key)  # the hour passes; the day's key is not touched

    # 32 attempts: 20 were let through, and none of the 12 refused ones counted in the day.
    # Had they, the day would have been full at the 21st attempt of all, in the third hour.
    assert n == 32
    assert await redis.get(day_key) == "20"
    # In the first three hours the day had room, so the hour alone refused, and said its own wait.
    for refused in refused_in[:3]:
        assert all(int(r.headers["retry-after"]) <= HOUR_S for r in refused)
    # In the fourth the fifth admitted attempt had just filled the day: the hour's refusals now find
    # it full, and say so (`ip_day`, a day's wait: coming back in an hour would be refused again).
    assert all(DAY_S - 30 <= int(r.headers["retry-after"]) <= DAY_S for r in refused_in[3])
    kinds = [e["kind"] for e in logs if e["event"] == "auth.rate_limited"]
    assert kinds == ["ip"] * 9 + ["ip_day"] * 3
    # And the 21st attempt the hour lets through is the one the day refuses.
    with structlog.testing.capture_logs() as day_logs:
        refused_by_day = await _sign_up(client, n)
    _assert_rate_limited(refused_by_day, DAY_SENTENCE)
    assert [e["kind"] for e in day_logs if e["event"] == "auth.rate_limited"] == ["ip_day"]


@pytest.mark.parametrize("route", ["sign-up", "google"])
@pytest.mark.parametrize(
    ("day", "wait", "kind"),
    [
        # No day key: the wait is the hour's, and the read must not create one.
        pytest.param(None, 3000, "ip", id="no-day-key"),
        # 19 counted: the next COUNTED attempt would be the 20th, which the day admits. The day has
        # room, so the wait is the hour's though the day has far longer to run.
        pytest.param((19, 86_000_000), 3000, "ip", id="day-with-room"),
        # 20 counted: the next counted attempt would be the 21st. The day is full too: its wait.
        pytest.param((20, 86_000_000), 86000, "ip_day", id="day-full"),
        # Over its limit (the limit was lowered): full as well, and the larger wait is the hour's.
        pytest.param((25, 1_200_000), 3000, "ip_day", id="day-over-with-less-to-run"),
    ],
)
async def test_when_the_hour_refuses_the_day_is_read_and_never_counted(
    client: AsyncClient,
    settings: Settings,
    route: str,
    day: tuple[int, int] | None,
    wait: int,
    kind: str,
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    await _prime(redis, settings, hour=(5, 3_000_000), day=day)
    day_before = await redis.get(day_key)

    with structlog.testing.capture_logs() as logs:
        refused = await _attempt(client, settings, route, 1)

    _assert_rate_limited(refused)
    assert refused.headers["retry-after"] == str(wait)
    assert [e["kind"] for e in logs if e["event"] == "auth.rate_limited"] == [kind]
    assert await redis.get(hour_key) == "6", "the refused attempt is counted in the hour"
    assert await redis.get(day_key) == day_before, "the hour's refusal counted in the day"
    assert await redis.exists(day_key) == (0 if day is None else 1), "the read made a day key"
    assert await _keys(redis, "rl:signup:email:*") == [], "a refusal on IP wrote an email key"


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_an_hour_refusal_gives_a_day_key_that_lost_its_ttl_its_window_again(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    """A counter that lost its TTL would never be forgotten, and a full one would refuse for ever.
    The read repairs it, as `_HIT_GOOGLE_START` repairs the ceiling it only reads."""
    redis = _redis(settings)
    _, day_key = _ip_keys(settings)
    await _prime(redis, settings, hour=(5, 3_000_000), day=(3, 86_000_000))
    await redis.persist(day_key)
    assert await redis.pttl(day_key) == -1

    refused = await _attempt(client, settings, route, 1)

    _assert_rate_limited(refused)
    assert DAY_S * 1000 - FRESH_MS <= await redis.pttl(day_key) <= DAY_S * 1000
    assert await redis.get(day_key) == "3", "the repair counted an attempt"


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_a_full_day_key_that_lost_its_ttl_is_repaired_and_told_in_the_wait(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    redis = _redis(settings)
    _, day_key = _ip_keys(settings)
    await _prime(redis, settings, hour=(5, 3_000_000), day=(20, 86_000_000))
    await redis.persist(day_key)

    with structlog.testing.capture_logs() as logs:
        refused = await _attempt(client, settings, route, 1)

    _assert_rate_limited(refused)
    # The day is full and, with its window given back, it is the longer wait: a day, and not the
    # hour's 3000 s that a key with no TTL would have been reported with.
    assert DAY_S - 30 <= int(refused.headers["retry-after"]) <= DAY_S
    assert [e["kind"] for e in logs if e["event"] == "auth.rate_limited"] == ["ip_day"]
    assert DAY_S * 1000 - FRESH_MS <= await redis.pttl(day_key) <= DAY_S * 1000
    assert await redis.get(day_key) == "20"


# --- IP first: a refusal on IP writes no address key ---------------------------------------------


@pytest.mark.parametrize("window", ["hour", "day"])
async def test_a_refusal_on_either_ip_window_writes_no_email_key(
    client: AsyncClient, settings: Settings, window: str
) -> None:
    redis = _redis(settings)
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    hour_key, day_key = _ip_keys(settings)
    if window == "day":
        # Make the DAY the window that refuses: the hour has passed, and the day holds its 20.
        await redis.delete(hour_key)
        await redis.set(day_key, 20, keepttl=True)
    before = await _keys(redis, "rl:signup:email:*")
    assert len(before) == 5

    for i in range(10, 20):
        assert (await _sign_up(client, i)).status_code == 429

    assert await _keys(redis, "rl:signup:email:*") == before, "a refusal on IP wrote an email key"


async def test_the_address_is_counted_only_when_both_ip_windows_pass(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    await _prime(redis, settings, hour=(4, 3_000_000), day=(19, 80_000_000))
    ok = await _sign_up(client, 1)  # the 5th this hour and the 20th this day: admitted
    assert ok.status_code == 201
    assert len(await _keys(redis, "rl:signup:email:*")) == 1
    over = await _sign_up(client, 2)  # the 6th and the 21st
    assert over.status_code == 429
    assert len(await _keys(redis, "rl:signup:email:*")) == 1


async def test_a_sign_up_leaves_exactly_four_keys_and_they_name_no_address_or_ip(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    assert (await _sign_up(client, 1)).status_code == 201

    keys = await _keys(redis, "rl:*")

    # The address's, the ceiling's (one key for the whole deployment, no token), the hour's and
    # the day's.
    assert [k.rsplit(":", 1)[0] for k in keys] == [
        "rl:signup:email",
        "rl:signup",
        "rl:signup:ip",
        "rl:signup:ip_day",
    ]
    assert SIGNUP_GLOBAL_KEY in keys
    for key in keys:
        assert IP not in key and "person1" not in key and "signup-limits" not in key
    hour_key, day_key = _ip_keys(settings)
    assert {hour_key, day_key} <= set(keys)


# --- the keys' windows ----------------------------------------------------------------------------


async def test_the_keys_live_their_windows(client: AsyncClient, settings: Settings) -> None:
    redis = _redis(settings)
    assert (await _sign_up(client, 1)).status_code == 201
    hour_key, day_key = _ip_keys(settings)
    (email_key,) = await _keys(redis, "rl:signup:email:*")

    assert HOUR_S * 1000 - FRESH_MS <= await redis.pttl(hour_key) <= HOUR_S * 1000
    assert DAY_S * 1000 - FRESH_MS <= await redis.pttl(day_key) <= DAY_S * 1000
    assert HOUR_S * 1000 - FRESH_MS <= await redis.pttl(email_key) <= HOUR_S * 1000
    # The ceiling shares the hour's window (`signup_window_seconds`).
    assert HOUR_S * 1000 - FRESH_MS <= await redis.pttl(SIGNUP_GLOBAL_KEY) <= HOUR_S * 1000


async def test_the_windows_are_fixed_and_a_later_attempt_does_not_renew_them(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    await _prime(redis, settings, hour=(1, 5_000), day=(1, 9_000))

    assert (await _sign_up(client, 1)).status_code == 201

    hour_key, day_key = _ip_keys(settings)
    assert 0 < await redis.pttl(hour_key) <= 5_000, "an attempt gave the hour a new window"
    assert 0 < await redis.pttl(day_key) <= 9_000, "an attempt gave the day a new window"


# --- Retry-After: the larger wait of the windows that refused -------------------------------------


@pytest.mark.parametrize(
    ("hour", "day", "wait", "kind"),
    [
        # The hour refuses alone: its wait, though the day has far longer to run.
        ((5, 3_000_000), (0, 86_000_000), 3000, "ip"),
        # The day refuses alone: its wait, though the hour has longer to run.
        ((0, 3_000_000), (20, 1_200_000), 1200, "ip_day"),
        # Both refuse, the hour's wait the longer (the day is nearly over): the hour's.
        ((5, 3_000_000), (20, 1_200_000), 3000, "ip_day"),
        # Both refuse, the day's wait the longer: the day's.
        ((5, 1_200_000), (20, 86_000_000), 86000, "ip_day"),
    ],
    ids=["hour-alone", "day-alone", "both-hour-longer", "both-day-longer"],
)
async def test_retry_after_is_the_larger_wait_of_the_windows_that_refused(
    client: AsyncClient,
    settings: Settings,
    hour: tuple[int, int],
    day: tuple[int, int],
    wait: int,
    kind: str,
) -> None:
    await _prime(_redis(settings), settings, hour=hour, day=day)

    with structlog.testing.capture_logs() as logs:
        refused = await _sign_up(client, 1)

    _assert_rate_limited(refused)
    assert refused.headers["retry-after"] == str(wait)
    assert [e["kind"] for e in logs if e["event"] == "auth.rate_limited"] == [kind]


async def test_the_google_completion_answers_with_the_same_larger_wait(
    client: AsyncClient, settings: Settings
) -> None:
    await _prime(_redis(settings), settings, hour=(5, 3_000_000), day=(20, 1_200_000))
    ticket = await _ticket(client, settings, 1)

    refused = await _complete(client, ticket)

    _assert_rate_limited(refused)
    assert refused.headers["retry-after"] == "3000"


async def test_the_per_address_window_answers_with_its_own_wait(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    email = "five-times@signup-limits.example"
    email_key = rate_limit._key(settings, "signup", "email", email)  # noqa: SLF001
    await redis.set(email_key, 5, px=1_800_000)

    with structlog.testing.capture_logs() as logs:
        refused = await _sign_up(client, 1, email=email)

    _assert_rate_limited(refused)
    assert refused.headers["retry-after"] == "1800"
    assert [e["kind"] for e in logs if e["event"] == "auth.rate_limited"] == ["email"]


# --- the per-address window stays ----------------------------------------------------------------


async def test_the_per_address_window_stays_five_an_hour_whatever_the_ip(
    client: AsyncClient,
) -> None:
    email = "same-person@signup-limits.example"
    codes = [
        (await _sign_up(client, i, ip=f"198.51.100.{i + 1}", email=email)).status_code
        for i in range(6)
    ]
    # One account is made; the others are the neutral 409, counted; the sixth is over the address's
    # five an hour, from an IP that has spent one attempt of its five.
    assert codes == [201, 409, 409, 409, 409, 429]


# --- Google complete shares the budget ------------------------------------------------------------


@pytest.mark.parametrize("sixth", ["sign-up", "google"])
@pytest.mark.parametrize(("sign_ups", "completions"), [(3, 2), (0, 5), (5, 0)])
async def test_sign_up_and_google_complete_share_one_budget(
    client: AsyncClient, settings: Settings, sign_ups: int, completions: int, sixth: str
) -> None:
    n = 0
    for _ in range(sign_ups):
        assert (await _sign_up(client, n)).status_code == 201
        n += 1
    for _ in range(completions):
        done = await _complete(client, await _ticket(client, settings, n))
        assert done.status_code == 201, done.text
        n += 1

    if sixth == "sign-up":
        resp = await _sign_up(client, n)
    else:
        resp = await _complete(client, await _ticket(client, settings, n))

    _assert_rate_limited(resp, HOUR_SENTENCE)
    # And both counted in the same two keys: the hour saw the five that passed and this one that
    # did not; the day saw the five the hour let through.
    hour_key, day_key = _ip_keys(settings)
    redis = _redis(settings)
    assert await redis.get(hour_key) == "6"
    assert await redis.get(day_key) == "5"


async def test_a_google_complete_counts_the_ip_only_and_never_writes_an_email_key(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    done = await _complete(client, await _ticket(client, settings, 1))
    assert done.status_code == 201, done.text

    keys = await _keys(redis, "rl:*")

    assert sorted(keys) == sorted([*_ip_keys(settings), SIGNUP_GLOBAL_KEY])
    assert [await redis.get(k) for k in keys] == ["1", "1", "1"]


async def test_a_google_complete_is_refused_by_the_day_window_too(
    client: AsyncClient, settings: Settings
) -> None:
    await _prime(_redis(settings), settings, hour=(0, 3_000_000), day=(20, 86_000_000))
    ticket = await _ticket(client, settings, 1)

    with structlog.testing.capture_logs() as logs:
        refused = await _complete(client, ticket)

    _assert_rate_limited(refused)
    assert refused.headers["retry-after"] == "86000"
    assert [(e["action"], e["kind"]) for e in logs if e["event"] == "auth.rate_limited"] == [
        ("signup", "ip_day")
    ]


TICKET_REFUSAL = "That Google sign-in can no longer be used. Start again from the sign-in page."


async def _ticket_that_cannot_make_an_agency(
    client: AsyncClient, settings: Settings, kind: str
) -> str:
    """A ticket `POST /auth/google/complete` refuses: one that is nothing, another browser's, or an
    invited address's (which is answered at `/join` or `/decline`)."""
    if kind == "nothing":
        return "not-a-ticket-" + "x" * 30
    if kind == "another-browser":
        ticket = await _ticket(client, settings, 1)
        client.cookies.set(settings.google_flow_cookie_key, "someone-elses-binding-" + "y" * 20)
        return ticket
    offer = InvitationOffer(invitation_id="invt_x", user_id="user_x", agency_id="agcy_x")
    return await _ticket(client, settings, 1, offer=offer)


@pytest.mark.parametrize("kind", ["nothing", "another-browser", "invitation"])
async def test_a_google_complete_with_a_ticket_that_cannot_make_an_agency_counts_nothing(
    client: AsyncClient, settings: Settings, kind: str
) -> None:
    """Only a ticket that could make an agency spends the budget (round 4, N2). Any other is
    refused exactly as before and writes no counter, so it cannot be used to spend the ceiling over
    every address with no Google sign-in behind it."""
    redis = _redis(settings)
    ticket = await _ticket_that_cannot_make_an_agency(client, settings, kind)
    # The budget is at its edge on every window, so that a counted attempt would show at once.
    await _prime(redis, settings, hour=(4, 3_000_000), day=(19, 86_000_000))
    await _prime_global(redis, 99)
    counters = {k: await redis.get(k) for k in await _keys(redis, "rl:*")}
    ticket_key = f"{TICKET_PREFIX}{token_digest(ticket)}"

    for _ in range(10):
        resp = await _complete(client, ticket)
        # Today's refusal, whichever the ticket: the same problem, the same sentence, never a 429.
        assert resp.status_code == 400, resp.text
        assert resp.json()["type"].endswith("/invalid-google-ticket")
        assert resp.json()["detail"] == TICKET_REFUSAL

    assert {k: await redis.get(k) for k in await _keys(redis, "rl:*")} == counters
    assert await _keys(redis, "rl:signup:email:*") == []
    # Spent by the first refusal where it exists to be spent, as the route has always done; the
    # flow cookie is cleared on the answer actually sent.
    assert await redis.exists(ticket_key) == 0
    cookie_key = settings.google_flow_cookie_key
    cleared = [h for h in resp.headers.get_list("set-cookie") if h.startswith(cookie_key)]
    assert cleared and "max-age=0" in cleared[0].lower()


async def test_a_google_complete_with_a_ticket_that_is_nothing_never_reaches_a_429(
    client: AsyncClient, settings: Settings
) -> None:
    """The opposite of what this file said before round 4 (six made-up tickets and the sixth was a
    429): ten of them, from one address, are ten refusals and no key."""
    codes = [(await _complete(client, f"not-a-ticket-{i}")).status_code for i in range(10)]

    assert codes == [400] * 10
    assert await _keys(_redis(settings), "rl:*") == []


async def test_a_real_ticket_still_counts_in_the_hour_the_day_and_the_ceiling(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)

    done = await _complete(client, await _ticket(client, settings, 1))

    assert done.status_code == 201, done.text
    assert await redis.get(hour_key) == "1"
    assert await redis.get(day_key) == "1"
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "1"


async def test_two_completions_with_one_real_ticket_make_one_agency(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    """Both can pass the peek and the count; the ticket is spent once (read and delete in one
    step), so the second is refused as before and only one agency is made."""
    ticket = await _ticket(client, settings, 1)

    results = await asyncio.gather(_complete(client, ticket), _complete(client, ticket))

    assert sorted(r.status_code for r in results) == [201, 400]
    assert await _count(engine, Agency) == 1
    assert await _count(engine, User) == 1
    # One attempt, or two if both read the ticket before either spent it; never more.
    assert await _redis(settings).get(SIGNUP_GLOBAL_KEY) in ("1", "2")


async def test_a_refused_google_complete_leaves_its_ticket_unspent(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    ticket = await _ticket(client, settings, 1)
    redis = _redis(settings)
    ticket_key = f"{TICKET_PREFIX}{token_digest(ticket)}"
    users_before = await _count(engine, User)

    refused = await _complete(client, ticket)

    _assert_rate_limited(refused)
    # The ticket is where it was, nothing was made, and the flow cookie it is bound to is not
    # cleared (a refusal that cleared it would leave the person with a ticket they cannot use).
    assert await redis.exists(ticket_key) == 1
    assert await _count(engine, User) == users_before
    assert refused.headers.get_list("set-cookie") == []
    # So the same ticket completes once there is budget: here, from an address that has some.
    done = await _complete(client, ticket, ip=OTHER_IP)
    assert done.status_code == 201, done.text
    assert await redis.exists(ticket_key) == 0


# --- a burst: the limit is taken in one step, so exactly the limit gets through ------------------


async def test_a_burst_of_sign_ups_admits_exactly_the_limit(client: AsyncClient) -> None:
    """Twelve requests at once from one IP: exactly five get through, however they interleave.

    This is what the ONE Lua step is for. Counting in Python (read the hour, decide, then count)
    lets every request read the same figure before any of them counts, and then more than the limit
    pass: a negative control that does that fails this test every time, while the one-at-a-time
    tests above still pass it (fix round, security review I6).
    """
    results = await asyncio.gather(*[_sign_up(client, i) for i in range(12)])

    assert sorted(r.status_code for r in results) == [201] * 5 + [429] * 7


async def test_a_burst_across_both_routes_admits_exactly_the_limit_between_them(
    client: AsyncClient, settings: Settings
) -> None:
    """Six sign-ups and six completions at once: five in all get through, not five on each route.

    The same control as above breaks it, and a limiter that counted the two routes in separate
    keys would too (see `test_sign_up_and_google_complete_share_one_budget` for the keys).
    """
    shared = "binding-for-a-burst-" + "b" * 32
    tickets = [await _ticket(client, settings, n, binding=shared) for n in range(6)]

    results = await asyncio.gather(
        *[_sign_up(client, i) for i in range(6)], *[_complete(client, t) for t in tickets]
    )

    assert sorted(r.status_code for r in results) == [201] * 5 + [429] * 7


# --- Redis down: both routes fail closed ----------------------------------------------------------


async def test_redis_down_is_a_503_on_both_routes_and_nothing_is_created(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    ticket = await _ticket(client, settings, 1)  # parked while Redis still answers
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
    app.dependency_overrides[redis_client] = lambda: dead
    try:
        with structlog.testing.capture_logs() as logs:
            sign_up = await _sign_up(client, 1)
            complete = await _complete(client, ticket)
    finally:
        await dead.aclose()

    for resp in (sign_up, complete):
        assert resp.status_code == 503, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["type"] == "/problems/service-unavailable"
    # It is the limiter that said so (once per request, with no field), and not Redis failing
    # later in the handler: the limit comes first.
    unavailable = [e for e in logs if e["event"] == "auth.rate_limit.unavailable"]
    assert unavailable == [{"event": "auth.rate_limit.unavailable", "log_level": "warning"}] * 2
    assert not [e for e in logs if e["event"] == "redis.unavailable"]
    # Nothing was created, and the ticket was not spent.
    assert await _count(engine, User) == 0
    assert await _redis(settings).exists(f"{TICKET_PREFIX}{token_digest(ticket)}") == 1


# --- logs ----------------------------------------------------------------------------------------


async def test_the_log_line_names_the_window_and_nothing_else(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    address = "leaky.person@signup-limits.example"
    ip_a, ip_b, ip_c = "203.0.113.31", "203.0.113.32", "203.0.113.33"
    await _prime(redis, settings, hour=(5, 3_000_000), ip=ip_a)  # the hour refuses
    await _prime(redis, settings, day=(20, 86_000_000), ip=ip_b)  # the day refuses
    email_key = rate_limit._key(settings, "signup", "email", address)  # noqa: SLF001
    await redis.set(email_key, 5, px=1_800_000)  # the address refuses

    with structlog.testing.capture_logs() as logs:
        assert (await _sign_up(client, 1, ip=ip_a)).status_code == 429
        assert (await _sign_up(client, 2, ip=ip_b)).status_code == 429
        assert (await _sign_up(client, 3, ip=ip_c, email=address)).status_code == 429

    tripped = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert all(set(e) == {"event", "log_level", "action", "kind"} for e in tripped)
    assert [(e["action"], e["kind"]) for e in tripped] == [
        ("signup", "ip"),
        ("signup", "ip_day"),
        ("signup", "email"),
    ]
    blob = repr(logs)
    for secret in (address, "leaky", ip_a, ip_b, ip_c, "person"):
        assert secret not in blob


# --- a request the schema refuses is never counted -----------------------------------------------


async def test_a_request_the_schema_refuses_is_not_counted_on_either_route(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    for i in range(10):
        short = await client.post(
            f"{BASE}/auth/sign-up",
            json={"agencyName": "A Name", "fullName": "A", "email": _email(i), "password": "short"},
            headers={IP_HEADER: IP},
        )
        assert short.status_code == 422
        nameless = await client.post(
            f"{BASE}/auth/google/complete",
            json={"ticket": "t", "agencyName": "N", "fullName": "A"},
            headers={IP_HEADER: IP},
        )
        assert nameless.status_code == 422
    assert await _keys(redis, "rl:*") == [], "a 422 was counted"

    for i in range(5):  # so the whole budget is still there
        assert (await _sign_up(client, i)).status_code == 201


# --- the ceiling over every address (A11, the founder's answer h) --------------------------------
#
# SIGNUP_LIMIT_GLOBAL (100) admitted attempts per signup_window_seconds, for both routes, read FIRST
# and counted only when every other window admits. Time and counts are controlled through the key.


async def _prime_global(redis: Redis, count: int, ms: int = 3_000_000) -> None:
    await redis.set(SIGNUP_GLOBAL_KEY, count, px=ms)


async def _all_keys(redis: Redis) -> set[str]:
    return {k async for k in redis.scan_iter("*")}


async def _prepared(client: AsyncClient, settings: Settings, route: str, i: int, *, ip: str):  # noqa: ANN202
    """An attempt on `route`, made ready so that a snapshot of Redis can be taken before it is sent
    (a Google completion needs its ticket parked first). Returns a coroutine function."""
    if route == "sign-up":
        return lambda: _sign_up(client, i, ip=ip)
    # One binding for every ticket made here: the client holds one flow cookie.
    ticket = await _ticket(client, settings, i, binding="prepared-binding-" + "b" * 32)
    return lambda: _complete(client, ticket, ip=ip)


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_the_101st_admitted_attempt_in_the_window_is_refused_whoever_sends_it(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    redis = _redis(settings)
    await _prime_global(redis, 99)  # 99 admitted attempts already, from addresses of all kinds

    last = await _attempt(client, settings, route, 1, ip="203.0.113.71")  # the 100th: admitted
    assert last.status_code != 429, last.text
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "100"

    with structlog.testing.capture_logs() as logs:
        busy = await _attempt(client, settings, route, 2, ip="203.0.113.72")  # never seen before

    assert busy.status_code == 429, busy.text
    assert busy.headers["content-type"].startswith("application/problem+json")
    body = busy.json()
    assert body["type"] == "/problems/rate-limited"
    # The founder's sentence, verbatim, with the ceiling key's own wait (about 50 minutes here).
    assert body["detail"] == BUSY + "50 minutes."
    assert busy.headers["retry-after"] == "3000"
    # One log event: the integers `count` and `limit`, and nothing else.
    tripped = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert tripped == [
        {"event": "auth.rate_limited", "log_level": "warning", "action": "signup",
         "kind": "global", "count": 100, "limit": 100}
    ]


@pytest.mark.parametrize("route", ["sign-up", "google"])
async def test_a_global_refusal_writes_no_key_of_any_kind(
    client: AsyncClient, settings: Settings, route: str
) -> None:
    redis = _redis(settings)
    await _prime_global(redis, 100)
    sends = [
        await _prepared(client, settings, route, i, ip=f"203.0.113.{80 + i}") for i in range(3)
    ]
    before = await _all_keys(redis)

    for send in sends:
        refused = await send()
        assert refused.status_code == 429
        assert refused.json()["detail"].startswith(BUSY)

    assert await _all_keys(redis) == before, "a refusal by the ceiling wrote a key"
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "100", "a refusal moved the ceiling's count"


async def test_a_ceiling_that_lost_its_ttl_is_given_its_window_again_and_still_refuses(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    await redis.set(SIGNUP_GLOBAL_KEY, 100)  # no expiry: it would refuse for ever
    assert await redis.pttl(SIGNUP_GLOBAL_KEY) == -1

    refused = await _sign_up(client, 1)

    assert refused.status_code == 429
    assert HOUR_S * 1000 - FRESH_MS <= await redis.pttl(SIGNUP_GLOBAL_KEY) <= HOUR_S * 1000
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "100"


async def test_the_ceiling_counts_admitted_attempts_only_so_one_address_cannot_use_it_up(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    # One address: 5 admitted by the hour, then 15 the hour refuses, and the 20-per-day is not
    # reached. Only the 5 count against everyone.
    for i in range(20):
        await _sign_up(client, i)
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "5"
    # An attempt the address window refuses is not counted either.
    email_key = rate_limit._key(settings, "signup", "email", "full@signup-limits.example")  # noqa: SLF001
    await redis.set(email_key, 5, px=1_800_000)
    refused = await _sign_up(client, 50, ip=OTHER_IP, email="full@signup-limits.example")
    assert refused.status_code == 429
    assert refused.json()["detail"].startswith("Too many attempts.")
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "5"


@pytest.mark.parametrize(
    ("route", "reason"),
    [
        (route, reason)
        for route in ("sign-up", "google")
        # The Google route has no address window, so it has no refusal "by the email".
        for reason in ("hour", "day", "global") + (("email",) if route == "sign-up" else ())
    ],
)
async def test_a_refusal_creates_no_key_in_any_family(
    client: AsyncClient, settings: Settings, route: str, reason: str
) -> None:
    """The founder's rule: new keys can never outnumber admitted attempts. A new address that a
    window refuses must not leave a key behind, in any family."""
    redis = _redis(settings)
    new_ip = "203.0.113.91"
    hour_key, day_key = _ip_keys(settings, new_ip)
    if reason == "hour":
        await redis.set(hour_key, 5, px=3_000_000)
    elif reason == "day":
        await redis.set(day_key, 20, px=86_000_000)  # the hour's key does not exist
    elif reason == "global":
        await _prime_global(redis, 100)
    else:
        email_key = rate_limit._key(settings, "signup", "email", _email(7))  # noqa: SLF001
        await redis.set(email_key, 5, px=1_800_000)
    send = await _prepared(client, settings, route, 7, ip=new_ip)
    before = await _all_keys(redis)

    refused = await send()

    assert refused.status_code == 429, refused.text
    after = await _all_keys(redis)
    assert after - before == set(), f"a refusal by the {reason} created {after - before}"
    assert before - after == set()


async def test_a_refusal_still_counts_in_a_window_whose_key_exists(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    hour_key, day_key = _ip_keys(settings)
    for i in range(5):
        assert (await _sign_up(client, i)).status_code == 201
    assert (await _sign_up(client, 5)).status_code == 429  # refused by the hour: counted there
    assert await redis.get(hour_key) == "6"
    assert await redis.get(day_key) == "5"
    # The address window too: an address already at its limit keeps counting its refusals.
    email_key = rate_limit._key(settings, "signup", "email", "again@signup-limits.example")  # noqa: SLF001
    await redis.set(email_key, 5, px=1_800_000)
    again = await _sign_up(client, 6, ip=OTHER_IP, email="again@signup-limits.example")
    assert again.status_code == 429
    assert await redis.get(email_key) == "6"


async def test_sign_up_and_google_complete_share_the_ceiling(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    await _prime_global(redis, 98)

    assert (await _sign_up(client, 1, ip="203.0.113.101")).status_code == 201
    done = await _complete(client, await _ticket(client, settings, 2), ip="203.0.113.102")
    assert done.status_code == 201
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "100"

    password = await _sign_up(client, 3, ip="203.0.113.103")
    ticket = await _ticket(client, settings, 4)
    google = await _complete(client, ticket, ip="203.0.113.104")
    for refused in (password, google):
        assert refused.status_code == 429
        assert refused.json()["detail"].startswith(BUSY)
    # The refused completion left its ticket unspent, as every refusal before it does.
    assert await redis.exists(f"{TICKET_PREFIX}{token_digest(ticket)}") == 1


async def test_a_burst_at_the_ceilings_last_slots_admits_exactly_the_limit(
    client: AsyncClient, settings: Settings
) -> None:
    redis = _redis(settings)
    await _prime_global(redis, 95)  # five slots left, twelve requests at once from twelve addresses

    results = await asyncio.gather(
        *[_sign_up(client, i, ip=f"198.51.100.{i + 1}") for i in range(12)]
    )

    assert sorted(r.status_code for r in results) == [201] * 5 + [429] * 7
    assert await redis.get(SIGNUP_GLOBAL_KEY) == "100"
    # Seven refused attempts made no keys: five buckets, five hour, day and address keys.
    assert len(await _keys(redis, "rl:signup:ip:*")) == 5
    assert len(await _keys(redis, "rl:signup:email:*")) == 5


# --- the wait in words: the shared rendering (founder's rule k) ---------------------------------


@pytest.mark.parametrize(
    ("seconds", "words"),
    [
        (1, "1 minute"),
        (60, "1 minute"),
        (61, "2 minutes"),
        (3000, "50 minutes"),
        (3600, "60 minutes"),  # 60 minutes stays in minutes
        (3601, "2 hours"),  # just past an hour rounds up: "1 hour" cannot come from a wait over 60
        (3660, "2 hours"),  # 61 minutes
        (7200, "2 hours"),
        (7201, "3 hours"),
        (86_399, "24 hours"),
        (86_400, "24 hours"),  # the sign-up day
    ],
)
def test_a_wait_is_in_minutes_up_to_sixty_and_in_hours_after(seconds: int, words: str) -> None:
    refused = rate_limit._too_many(seconds)  # noqa: SLF001
    busy = rate_limit._signup_busy(seconds)  # noqa: SLF001

    assert refused.detail == f"Too many attempts. Try again in {words}."
    assert busy.detail == f"Sign-up is busy right now. Try again in {words}."
    assert refused.headers == busy.headers == {"Retry-After": str(seconds)}  # still seconds


def test_sixty_minutes_stays_in_minutes_and_one_minute_is_singular() -> None:
    # (Renamed from `test_the_singular_hour_is_kept_in_the_code`, which never tested an hour.) The
    # code keeps "1 hour" because the founder's wording names it, but no wait over 60 minutes can
    # reach it: it rounds up to 2 hours. The singular branch is unreachable and has no test.
    assert rate_limit._wait_words(3600) == "60 minutes"  # noqa: SLF001
    assert rate_limit._wait_words(1) == "1 minute"  # noqa: SLF001
