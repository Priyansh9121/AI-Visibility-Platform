"""F4: `GET /auth/google/start` is limited per IP before it writes anything.

It is unauthenticated and each call used to park one `google-state:` key on the one Redis
that also holds every session, so a flood could fill it. What is pinned:

* the 31st start from one IP inside the window is a 302 to `/?google=error&reason=rate-limited`,
  not a problem document, and writes no `google-state:` key (counted before and after);
* starts below the limit still redirect to Google, and another IP has its own budget;
* Redis down: refused with the `unavailable` reason, nothing minted (fails closed);
* the only new key is the limiter's own, and the refusal log line carries no IP.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import structlog
from httpx import AsyncClient
from redis.asyncio import Redis

from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.deps import redis_client
from avp_api.services.google_oauth import STATE_PREFIX
from test_google_sign_in import BASE, FakeGoogleProvider, google  # noqa: F401

IP_HEADER = "X-Test-Client-IP"
IP = "203.0.113.50"
LIMIT = 30


@pytest.fixture(autouse=True)
def _header(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "client_ip_header", IP_HEADER)


async def _start(client: AsyncClient, ip: str = IP):  # noqa: ANN202
    return await client.get(
        f"{BASE}/auth/google/start", headers={IP_HEADER: ip}, follow_redirects=False
    )


async def _count(redis: Redis, prefix: str) -> int:
    return len([k async for k in redis.scan_iter(f"{prefix}*")])


def test_default_settings() -> None:
    s = Settings(_env_file=None)
    assert (s.google_start_limit_per_ip, s.google_start_window_seconds) == (30, 900)


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
    assert not [k for k in keys if k.startswith("rl:") and not k.startswith("rl:google_start:ip:")]
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
