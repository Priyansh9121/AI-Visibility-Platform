"""Rate limits on login, sign-up and reset-request — Redis-backed, fixed window.

Counted per normalised email and per client IP, every attempt, before any
expensive work. Keys and logs never hold the email or the IP in the clear.

Login is the exception per address (A2): it has the IP window below and no per-email window.
An address is slowed instead, which `test_login_slowdown.py` covers; the figure that sets in
when it starts is `login_slowdown_after_failures`, lowered to 3 here so these tests stay small.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client
from avp_api.models import PasswordResetToken, User
from avp_api.security import token_digest
from avp_api.services import rate_limit

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"
IP_HEADER = "X-Test-Client-IP"

ROUTES = {
    "login": f"{BASE}/auth/login",
    "signup": f"{BASE}/auth/sign-up",
    "reset": f"{BASE}/auth/reset-password/request",
}


def _body(action: str, email: str) -> dict[str, str]:
    if action == "login":
        return {"email": email, "password": PASSWORD}
    if action == "signup":
        return {
            "agencyName": "Rate Agency",
            "fullName": "Operator",
            "email": email,
            "password": PASSWORD,
        }
    return {"email": email}


async def _post(client: AsyncClient, action: str, email: str, ip: str = "203.0.113.9"):
    return await client.post(
        ROUTES[action], json=_body(action, email), headers={IP_HEADER: ip}
    )


@pytest.fixture(autouse=True)
def _limits(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)
    for action in ("signup", "reset"):
        monkeypatch.setattr(settings, f"{action}_limit_per_email", 3)
    for action in ("login", "signup", "reset"):
        monkeypatch.setattr(settings, f"{action}_limit_per_ip", 4)
    monkeypatch.setattr(settings, "login_slowdown_after_failures", 3)


def test_default_limits() -> None:
    s = Settings(_env_file=None)
    assert (s.login_limit_per_ip, s.login_window_seconds) == (30, 900)
    assert "login_limit_per_email" not in Settings.model_fields
    # A11 (D11): 5 an hour per IP (it was 10) and 20 a day per IP; the per-address figure stays.
    assert (s.signup_limit_per_email, s.signup_limit_per_ip, s.signup_window_seconds) == (
        5, 5, 3600,
    )
    assert (s.signup_limit_per_ip_per_day, s.signup_day_window_seconds) == (20, 86400)
    assert (s.reset_limit_per_email, s.reset_limit_per_ip, s.reset_window_seconds) == (
        3, 10, 3600,
    )
    assert s.client_ip_header == ""


@pytest.mark.parametrize("action", ["signup", "reset"])
class TestPerEmailWindowsTrip:
    """Sign-up and reset-request count every attempt per address in a fixed window. Login does
    not (A2): its address is slowed, never answered 429."""

    async def test_per_email_across_ips(self, client: AsyncClient, action: str) -> None:
        for i in range(3):
            resp = await _post(client, action, "Same@Rate.example", ip=f"198.51.100.{i}")
            assert resp.status_code != 429, (i, resp.text)
        resp = await _post(client, action, " same@rate.example ", ip="198.51.100.77")
        assert resp.status_code == 429
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert int(resp.headers["retry-after"]) > 0
        assert resp.json()["type"] == "/problems/rate-limited"
        assert resp.json()["detail"].startswith("Too many attempts. Try again in ")

    async def test_window_resets_when_the_ttl_passes(
        self, client: AsyncClient, settings: Settings, action: str
    ) -> None:
        for _ in range(3):
            await _post(client, action, "ttl@rate.example", ip="198.51.100.1")
        tripped = await _post(client, action, "ttl@rate.example", ip="198.51.100.2")
        assert tripped.status_code == 429
        r = redis_module.get_redis(settings)
        keys = [k async for k in r.scan_iter(f"rl:{action}:email:*")]
        assert len(keys) == 1
        assert 0 < await r.ttl(keys[0]) <= getattr(settings, f"{action}_window_seconds")
        await r.delete(keys[0])  # what expiry does
        resp = await _post(client, action, "ttl@rate.example", ip="198.51.100.3")
        assert resp.status_code != 429


@pytest.mark.parametrize("action", ["login", "signup", "reset"])
async def test_per_ip_across_emails(client: AsyncClient, action: str) -> None:
    for i in range(4):
        resp = await _post(client, action, f"user{i}@rate.example")
        assert resp.status_code != 429, (i, resp.text)
    resp = await _post(client, action, "user99@rate.example")
    assert resp.status_code == 429
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert int(resp.headers["retry-after"]) > 0
    assert resp.json()["type"] == "/problems/rate-limited"
    assert resp.json()["detail"].startswith("Too many attempts. Try again in ")


async def test_reset_429_is_identical_for_known_and_unknown(
    client: AsyncClient, settings: Settings
) -> None:
    signup = await client.post(
        ROUTES["signup"], json=_body("signup", "known@rate.example"),
        headers={IP_HEADER: "192.0.2.50"},
    )
    assert signup.status_code == 201
    r = redis_module.get_redis(settings)
    await r.flushdb()  # the sign-up above must not colour the reset buckets

    oks: list[set[bytes]] = []
    blocks: list[tuple[dict[str, str], bytes]] = []
    for email, ip in (("known@rate.example", "192.0.2.1"), ("nobody@rate.example", "192.0.2.2")):
        bodies = set()
        for _ in range(3):
            ok = await _post(client, "reset", email, ip=ip)
            assert ok.status_code == 200
            bodies.add(ok.content)
        oks.append(bodies)
        # Pin the email bucket's remaining time, away from a second boundary,
        # so Retry-After is comparable between the two runs.
        async for k in r.scan_iter("rl:reset:email:*"):
            await r.pexpire(k, 2_999_500)
        blocked = await _post(client, "reset", email, ip=ip)
        assert blocked.status_code == 429
        headers = {k: v for k, v in blocked.headers.items() if k not in ("date",)}
        blocks.append((headers, blocked.content))
        await r.flushdb()
    assert oks[0] == oks[1] and len(oks[0]) == 1, "reset 200 must be byte-identical"
    assert blocks[0] == blocks[1], "reset 429 must be byte-identical"


async def test_below_the_limit_reset_answers_its_identical_200(client: AsyncClient) -> None:
    a = await _post(client, "reset", "a@rate.example", ip="192.0.2.10")
    b = await _post(client, "reset", "b@rate.example", ip="192.0.2.11")
    assert a.status_code == b.status_code == 200
    assert a.content == b.content


async def test_signup_limit_trips_before_the_409(client: AsyncClient) -> None:
    first = await _post(client, "signup", "dupe@rate.example", ip="192.0.2.20")
    assert first.status_code == 201
    codes = [
        (await _post(client, "signup", "dupe@rate.example", ip=f"192.0.2.{30 + i}")).status_code
        for i in range(3)
    ]
    assert codes == [409, 409, 429]


async def test_redis_down_login_and_signup_fail_closed_reset_fails_open(
    client: AsyncClient,
) -> None:
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1)
    app.dependency_overrides[redis_client] = lambda: dead
    try:
        with structlog.testing.capture_logs() as logs:
            for _ in range(6):
                resp = await _post(client, "reset", "down@rate.example")
                assert resp.status_code == 200
            for action in ("login", "signup"):
                resp = await _post(client, action, "down@rate.example")
                assert resp.status_code == 503, resp.text
                assert resp.headers["content-type"].startswith("application/problem+json")
                assert resp.json()["type"] == "/problems/service-unavailable"
                assert "down@rate.example" not in resp.text
    finally:
        await dead.aclose()
    warnings = [e for e in logs if e["event"] == "auth.rate_limit.unavailable"]
    assert len(warnings) == 8
    assert all(set(e) == {"event", "log_level"} for e in warnings)


async def test_no_email_or_ip_in_keys_or_logs(
    client: AsyncClient, settings: Settings
) -> None:
    email, ip = "leaky.person@rate.example", "203.0.113.77"
    with structlog.testing.capture_logs() as logs:
        for _ in range(4):
            await _post(client, "reset", email, ip=ip)
        await _post(client, "login", email, ip=ip)
    tripped = [e for e in logs if e["event"] == "auth.rate_limited"]
    assert tripped and all(set(e) == {"event", "log_level", "action", "kind"} for e in tripped)
    assert {e["kind"] for e in tripped} <= {"email", "ip"}
    blob = repr(logs)
    assert email not in blob and ip not in blob and "leaky" not in blob
    r = redis_module.get_redis(settings)
    keys = [k async for k in r.scan_iter("rl:*")]
    assert keys
    for k in keys:
        assert "leaky" not in k and ip not in k and "rate.example" not in k
        assert k.startswith(
            ("rl:reset:email:", "rl:reset:ip:", "rl:login:email:", "rl:login:ip:", "rl:login:wait:")
        )


async def test_client_ip_header_decides_the_ip_bucket(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set: that header's value is the bucket; other headers are ignored.
    for i in range(4):
        r = await client.post(
            ROUTES["reset"], json={"email": f"h{i}@rate.example"},
            headers={IP_HEADER: " 203.0.113.5 ", "X-Forwarded-For": f"9.9.9.{i}"},
        )
        assert r.status_code == 200
    r = await client.post(
        ROUTES["reset"], json={"email": "h9@rate.example"},
        headers={IP_HEADER: "203.0.113.5", "X-Forwarded-For": "8.8.8.8"},
    )
    assert r.status_code == 429
    other = await client.post(
        ROUTES["reset"], json={"email": "h10@rate.example"}, headers={IP_HEADER: "203.0.113.6"}
    )
    assert other.status_code == 200

    # Unset: the header is ignored; everyone shares the connection's host.
    await redis_module.get_redis(settings).flushdb()
    monkeypatch.setattr(settings, "client_ip_header", "")
    codes = [
        (
            await client.post(
                ROUTES["reset"], json={"email": f"u{i}@rate.example"},
                headers={IP_HEADER: f"203.0.113.{100 + i}"},
            )
        ).status_code
        for i in range(5)
    ]
    assert codes == [200, 200, 200, 200, 429]


# --- client IP parsing -------------------------------------------------------


def _req(headers: dict[str, str], host: str | None = "198.18.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 1234) if host else None,
        }
    )


def _ip(settings: Settings, headers: dict[str, str], host: str | None = "198.18.0.1") -> str:
    return rate_limit.client_ip(_req(headers, host), settings)


@pytest.mark.parametrize(
    "value",
    [
        "not-an-ip", "999.1.1.1", "1.2.3", "1.2.3.4, 5.6.7.8", "1.2.3.4:abc",
        "[2001:db8::1", "[2001:db8::1]x", "2001:db8::zz", "a" * 500, "\x00", "::ffff:1.2.3",
    ],
)
def test_garbage_ip_values_share_the_unparsed_bucket(settings: Settings, value: str) -> None:
    with structlog.testing.capture_logs() as logs:
        assert _ip(settings, {IP_HEADER: value}) == "unparsed"
    assert logs == [{"event": "auth.rate_limit.ip_unparsed", "log_level": "warning"}]


def test_unparseable_connection_host_is_unparsed_too(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "client_ip_header", "")
    assert _ip(settings, {}, host="unknown") == "unparsed"
    assert _ip(settings, {}, host=None) == "unparsed"


def test_missing_configured_header_uses_its_own_bucket_never_the_host(
    settings: Settings,
) -> None:
    with structlog.testing.capture_logs() as logs:
        got = _ip(settings, {"X-Forwarded-For": "9.9.9.9"}, host="9.9.9.9")
        blank = _ip(settings, {IP_HEADER: "  "}, host="9.9.9.9")
    assert got == blank == "missing-header"
    assert [e["event"] for e in logs] == ["auth.rate_limit.ip_header_missing"] * 2
    assert all(set(e) == {"event", "log_level"} for e in logs)


async def test_missing_header_requests_share_one_bucket_end_to_end(
    client: AsyncClient,
) -> None:
    codes = [
        (
            await client.post(
                ROUTES["reset"], json={"email": f"m{i}@rate.example"},
                headers={"X-Forwarded-For": f"9.9.9.{i}"},
            )
        ).status_code
        for i in range(5)
    ]
    assert codes == [200, 200, 200, 200, 429]


def test_ipv6_keys_on_its_slash_64(settings: Settings) -> None:
    a = _ip(settings, {IP_HEADER: "2001:db8:1:2::1"})
    b = _ip(settings, {IP_HEADER: "2001:0db8:0001:0002:ffff:ffff:ffff:9"})
    c = _ip(settings, {IP_HEADER: "2001:db8:1:3::1"})
    assert a == b == "2001:db8:1:2::/64"
    assert c != a
    assert _ip(settings, {IP_HEADER: "[2001:db8:1:2::7]:443"}) == a
    assert _ip(settings, {IP_HEADER: "fe80::1%eth0"}) == "fe80::/64"


def test_ipv4_mapped_ipv6_shares_the_ipv4_bucket(settings: Settings) -> None:
    assert _ip(settings, {IP_HEADER: "::ffff:1.2.3.4"}) == _ip(settings, {IP_HEADER: "1.2.3.4"})
    assert _ip(settings, {IP_HEADER: "1.2.3.4:8080"}) == "1.2.3.4"
    assert _ip(settings, {IP_HEADER: "1.2.3.5"}) != "1.2.3.4"


# --- refused requests write no email keys ------------------------------------


async def _email_keys(settings: Settings, action: str) -> list[str]:
    r = redis_module.get_redis(settings)
    return [k async for k in r.scan_iter(f"rl:{action}:email:*")]


async def _wait_keys(settings: Settings) -> list[str]:
    """The login slow-down's waits (A2): present only while an address is in one."""
    r = redis_module.get_redis(settings)
    return [k async for k in r.scan_iter("rl:login:wait:*")]


@pytest.mark.parametrize("action", ["login", "signup", "reset"])
async def test_ip_refusals_create_no_new_email_keys(
    client: AsyncClient, settings: Settings, action: str
) -> None:
    for i in range(4):
        await _post(client, action, f"seed{i}@rate.example")
    assert len(await _email_keys(settings, action)) == 4
    for i in range(10):
        resp = await _post(client, action, f"fresh{i}@rate.example")
        assert resp.status_code == 429
    assert len(await _email_keys(settings, action)) == 4


def test_key_digest_is_16_bytes_base64url_with_the_prefix(settings: Settings) -> None:
    key = rate_limit._key(settings, "login", "email", "a@b.example")  # noqa: SLF001
    prefix, token = key.rsplit(":", 1)
    assert prefix == "rl:login:email"
    assert len(token) == 22 and "=" not in token
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


# --- login counts failures only ----------------------------------------------


async def _signed_up(client: AsyncClient, email: str) -> None:
    resp = await _post(client, "signup", email, ip="192.0.2.200")
    assert resp.status_code == 201, resp.text
    await client.post(f"{BASE}/auth/logout")


async def _login(client: AsyncClient, email: str, password: str, ip: str = "203.0.113.9"):
    return await client.post(
        ROUTES["login"], json={"email": email, "password": password}, headers={IP_HEADER: ip}
    )


async def test_successful_logins_do_not_consume_the_budget(client: AsyncClient) -> None:
    await _signed_up(client, "ok@rate.example")
    for i in range(20):  # budgets are 3 per email, 4 per IP
        resp = await _login(client, "ok@rate.example", PASSWORD)
        assert resp.status_code == 200, (i, resp.text)


async def test_failed_logins_consume_the_ip_budget_and_a_success_gives_one_back(
    client: AsyncClient, settings: Settings
) -> None:
    await _signed_up(client, "mix@rate.example")
    for _ in range(2):
        assert (await _login(client, "mix@rate.example", "wrong-password-1")).status_code == 401
    assert (await _login(client, "mix@rate.example", PASSWORD)).status_code == 200
    # Two failures on the IP's books (three attempts, one given back): two more are allowed
    # (the budget is 4), the one after is refused. The address itself was cleared by the success,
    # so these two are its first failures again and it is nowhere near slowed.
    for _ in range(2):
        assert (await _login(client, "mix@rate.example", "wrong-password-2")).status_code == 401
    assert await _wait_keys(settings) == []
    refused = await _login(client, "mix@rate.example", PASSWORD)
    assert refused.status_code == 429


async def test_release_never_takes_a_counter_below_zero(
    client: AsyncClient, settings: Settings
) -> None:
    await _signed_up(client, "floor@rate.example")
    r = redis_module.get_redis(settings)
    for _ in range(3):
        assert (await _login(client, "floor@rate.example", PASSWORD)).status_code == 200
    for k in [k async for k in r.scan_iter("rl:login:*")]:
        assert int(await r.get(k)) == 0
    await r.flushdb()
    request = _req({IP_HEADER: "203.0.113.9"})
    await rate_limit.release_login(r, settings, request, email="floor@rate.example")
    assert [k async for k in r.scan_iter("rl:login:*")] == []


async def test_a_successful_reset_clears_that_emails_login_key(
    client: AsyncClient, settings: Settings, engine  # noqa: ANN001
) -> None:
    email = "locked@rate.example"
    await _signed_up(client, email)
    for i in range(3):
        resp = await _login(client, email, "wrong-password", ip=f"198.51.100.{i}")
        assert resp.status_code == 401
    # The third failure started a wait: even the right password is refused, as a wrong one (401,
    # not 429), without being checked.
    assert (await _login(client, email, PASSWORD, ip="198.51.100.9")).status_code == 401
    assert len(await _email_keys(settings, "login")) == 1
    assert len(await _wait_keys(settings)) == 1

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    raw = "rate-limit-token-" + "x" * 32
    async with factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        s.add(
            PasswordResetToken(
                id=ids.new_id(ids.PASSWORD_RESET), user_id=user.id,
                token_digest=token_digest(raw), expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
    resp = await client.post(
        f"{BASE}/auth/reset-password/confirm",
        json={"token": raw, "newPassword": "an-entirely-different-passphrase"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"
    assert await _email_keys(settings, "login") == []
    assert await _wait_keys(settings) == []
    assert (
        await _login(client, email, "an-entirely-different-passphrase", ip="198.51.100.10")
    ).status_code == 200


async def test_reset_confirm_is_not_rate_limited(client: AsyncClient) -> None:
    codes = {
        (
            await client.post(
                f"{BASE}/auth/reset-password/confirm",
                json={"token": f"bad-token-{i:040d}", "newPassword": PASSWORD},
                headers={IP_HEADER: "203.0.113.9"},
            )
        ).status_code
        for i in range(10)
    }
    assert codes == {400}


# --- settings ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        *(
            f"{a}_{k}"
            for a in ("signup", "reset")
            for k in ("limit_per_email", "limit_per_ip", "window_seconds")
        ),
        # A11: the sign-up day's own limit and window.
        "signup_limit_per_ip_per_day",
        "signup_day_window_seconds",
        "signup_limit_global",
        "login_limit_per_ip",
        "login_window_seconds",
        "login_slowdown_after_failures",
        "login_slowdown_window_seconds",
        "login_slowdown_first_wait_seconds",
        "login_slowdown_max_wait_seconds",
    ],
)
@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_or_window_below_one_is_rejected(name: str, bad: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: bad})


# --- the sign-up ceiling must be at least twice one address's hourly allowance (A11) -------------


@pytest.mark.parametrize(("per_ip", "ceiling"), [(5, 9), (5, 1), (7, 13)])
def test_a_sign_up_ceiling_below_twice_the_per_address_limit_stops_the_start(
    per_ip: int, ceiling: int
) -> None:
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None, signup_limit_per_ip=per_ip, signup_limit_global=ceiling)
    text = str(refused.value)
    assert "SIGNUP_LIMIT_GLOBAL" in text and "SIGNUP_LIMIT_PER_IP" in text
    assert str(ceiling) not in text.split("SIGNUP_LIMIT_PER_IP.")[0].split("must be")[-1]


@pytest.mark.parametrize(("per_ip", "ceiling"), [(5, 10), (7, 14), (5, 100)])
def test_exactly_twice_the_per_address_limit_starts(per_ip: int, ceiling: int) -> None:
    s = Settings(_env_file=None, signup_limit_per_ip=per_ip, signup_limit_global=ceiling)
    assert (s.signup_limit_per_ip, s.signup_limit_global) == (per_ip, ceiling)
