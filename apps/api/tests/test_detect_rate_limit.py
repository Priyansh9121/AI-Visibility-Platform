"""Competitor detection from the button: once per client per day — launch checklist 3.53.

`POST /clients/{clientId}/competitors/detect` makes about six paid SerpApi searches, up
to twelve with retries, out of a monthly quota the whole platform shares. The founder's
decision (2026-10-03): once per client per day from the button, and always allowed as
part of a scan. A scan's own detection never goes through that route
(`ensure_set_for_scan`), so the limit sits on the route and a scan cannot be refused by
it.

What is pinned here, each by a test that fails without it:

* the second press for a client is refused in plain words, with `Retry-After`, and runs
  no search and opens no scan;
* the window is a real 24-hour expiry, and when it passes the route runs again;
* the check comes after the client is found: another agency's client is a 404 and spends
  nothing;
* a client's day is its own: another client is unaffected;
* a scan's detection runs whether or not the day is spent, and does not spend it;
* a run that then fails still counts, and Redis down fails closed;
* the refusal's one log line carries the client id, the agency id and the seconds left, and
  nothing else.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError as RedisResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api import redis_client as redis_module
from avp_api.config import Settings
from avp_api.errors import RateLimited
from avp_api.models import CompetitorSet, Scan
from avp_api.services import competitors as detection
from avp_api.services import rate_limit, scan_runner
from avp_api.services.serp import SerpResult

BASE = "/api/v1"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
# What a refusal says for a client that pressed the button a moment ago.
REFUSED = (
    "Competitor detection already ran for this client in the last 24 hours. "
    "It can run again in about 24 hours."
)
UNAVAILABLE = "This is temporarily unavailable. Please try again shortly."


async def _sign_up(
    client: AsyncClient,
    email: str = "owner@detect-limit.example",
    agency: str = "Detect Limit Agency",
) -> str:
    """Sign an agency up; returns its id."""
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": agency,
            "fullName": "Operator",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["agency"]["id"]


async def _make_client(client: AsyncClient, domain: str = "helpscout.com") -> str:
    resp = await client.post(f"{BASE}/clients", json={"url": domain, "classify": False})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _detect(client: AsyncClient, cid: str):  # noqa: ANN202
    return await client.post(f"{BASE}/clients/{cid}/competitors/detect")


async def _day_keys(settings: Settings) -> list[str]:
    """Every client's detection key in Redis."""
    redis = redis_module.get_redis(settings)
    return [key async for key in redis.scan_iter("rl:detect_competitors:*")]


async def _until_gone(settings: Settings, key: str) -> None:
    """Wait for Redis to expire a key it has been told to expire."""
    redis = redis_module.get_redis(settings)
    async with asyncio.timeout(5):
        while await redis.exists(key):
            await asyncio.sleep(0.005)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """What detection actually did, counted at the two places a refused press must not reach.

    `searches` has one entry per run: a run is one batch of SerpApi queries, so one entry
    is six paid searches. `scans` has one entry per `get_or_create_scan`, the first thing
    the route does after the check. Both wrap the suite's own fakes (conftest's autouse
    `stub_chain_externals`), so nothing leaves the process. `search_down` makes the
    SerpApi side answer with a failure, the way an outage would.
    """
    seen = SimpleNamespace(searches=[], scans=[], search_down=False)
    search_many = detection.serp_service.search_many
    get_or_create_scan = detection.get_or_create_scan

    async def counting_search(queries, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.searches.append(list(queries))
        if seen.search_down:
            return [SerpResult(query=q, ok=False, error_code="SERP_TIMEOUT") for q in queries]
        return await search_many(queries, **kwargs)

    async def counting_scan(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.scans.append(1)
        return await get_or_create_scan(*args, **kwargs)

    monkeypatch.setattr(detection.serp_service, "search_many", counting_search)
    monkeypatch.setattr(detection, "get_or_create_scan", counting_scan)
    return seen


class TestOncePerClientPerDay:
    async def test_a_second_press_is_refused_in_plain_words_and_runs_nothing(
        self, client: AsyncClient, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        first = await _detect(client, cid)
        assert first.status_code == 201, first.text
        assert len(runs.searches) == 1
        assert len(runs.scans) == 1

        second = await _detect(client, cid)
        assert second.status_code == 429, second.text
        assert second.headers["content-type"].startswith("application/problem+json")
        body = second.json()
        assert body["type"].endswith("/rate-limited")
        assert body["status"] == 429
        # Plain words: no code, no key, no id. The whole sentence is pinned, so a
        # change to what the founder's customers read has to change this test.
        assert body["detail"] == REFUSED
        assert 1 <= int(second.headers["retry-after"]) <= 86_400
        # Refused before any work: no search, no scan opened.
        assert len(runs.searches) == 1
        assert len(runs.scans) == 1

    async def test_the_day_is_a_real_24_hour_expiry_on_one_key_per_client(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await _detect(client, cid)).status_code == 201

        [key] = await _day_keys(settings)
        pttl = await redis_module.get_redis(settings).pttl(key)
        assert DAY_MS - 60_000 < pttl <= DAY_MS, pttl

    async def test_the_key_holds_a_digest_and_never_the_clients_id(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await _detect(client, cid)).status_code == 201

        [key] = await _day_keys(settings)
        prefix, digest = key.rsplit(":", 1)
        assert prefix == "rl:detect_competitors:client"
        assert len(digest) == 22 and "=" not in digest
        redis = redis_module.get_redis(settings)
        every_key = [k async for k in redis.scan_iter("rl:*")]
        assert not any(cid in k or cid.split("_", 1)[-1] in k for k in every_key)

    async def test_the_next_day_it_runs_again(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await _detect(client, cid)).status_code == 201
        assert (await _detect(client, cid)).status_code == 429
        assert len(runs.searches) == 1

        # The last millisecond of the day, then real expiry: no sleeping for a day.
        [key] = await _day_keys(settings)
        await redis_module.get_redis(settings).pexpire(key, 1)
        await _until_gone(settings, key)

        again = await _detect(client, cid)
        assert again.status_code == 201, again.text
        assert len(runs.searches) == 2
        # And the new day is spent like the old one.
        assert (await _detect(client, cid)).status_code == 429
        assert len(runs.searches) == 2

    async def test_a_key_that_somehow_has_no_expiry_is_given_one_not_kept_forever(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        assert (await _detect(client, cid)).status_code == 201
        [key] = await _day_keys(settings)
        redis = redis_module.get_redis(settings)
        await redis.persist(key)
        assert await redis.pttl(key) == -1

        refused = await _detect(client, cid)
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "86400"
        assert 0 < await redis.pttl(key) <= DAY_MS

    async def test_five_presses_at_once_run_detection_once(
        self, client: AsyncClient, runs: SimpleNamespace
    ) -> None:
        """What a burst of presses looks like from outside. It does not prove atomicity
        by itself: requests reach the check staggered by their database reads, so a
        read followed by a write would get through this too. The next test is the proof."""
        await _sign_up(client)
        cid = await _make_client(client)

        responses = await asyncio.gather(*(_detect(client, cid) for _ in range(5)))

        assert sorted(r.status_code for r in responses) == [201, 429, 429, 429, 429]
        assert len(runs.searches) == 1

    async def test_the_claim_is_one_atomic_step_not_a_read_and_then_a_write(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        """Twenty claims started in the same instant: exactly one takes the day.

        Called directly, so nothing staggers them. A check that read the key and then
        wrote it would let several through. (`client` is requested for its Redis wiring.)
        """
        cid, agency_id = ids.new_id(ids.CLIENT), ids.new_id(ids.AGENCY)
        redis = redis_module.get_redis(settings)

        outcomes = await asyncio.gather(
            *(
                rate_limit.claim_detection(redis, settings, client_id=cid, agency_id=agency_id)
                for _ in range(20)
            ),
            return_exceptions=True,
        )

        assert sum(outcome is None for outcome in outcomes) == 1
        assert all(isinstance(o, RateLimited) for o in outcomes if o is not None)
        assert len(await _day_keys(settings)) == 1


class TestARunThatFailsStillCounts:
    """It is counted when it starts, because its searches may already be spent."""

    async def test_a_run_whose_searches_all_fail_counts(
        self, client: AsyncClient, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)
        runs.search_down = True

        first = await _detect(client, cid)
        assert first.status_code == 201, first.text
        assert len(runs.searches) == 1

        second = await _detect(client, cid)
        assert second.status_code == 429
        assert len(runs.searches) == 1

    async def test_a_run_that_raises_counts(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        async def breaks(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise RuntimeError("the database went away mid-run")

        monkeypatch.setattr(detection, "persist_detection", breaks)
        with pytest.raises(RuntimeError, match="went away"):
            await _detect(client, cid)
        assert len(runs.searches) == 1
        assert len(await _day_keys(settings)) == 1, "a failed run gave its day back"

        second = await _detect(client, cid)
        assert second.status_code == 429
        assert len(runs.searches) == 1


class TestWhoseDayItIs:
    async def test_another_client_of_the_same_agency_is_not_limited(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        first = await _make_client(client, "helpscout.com")
        other = await _make_client(client, "zendesk.com")

        assert (await _detect(client, first)).status_code == 201
        assert (await _detect(client, other)).status_code == 201
        assert len(runs.searches) == 2
        assert len(await _day_keys(settings)) == 2

        assert (await _detect(client, first)).status_code == 429
        assert (await _detect(client, other)).status_code == 429
        assert len(runs.searches) == 2

    async def test_another_agencys_client_is_404_and_spends_nothing(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            await _sign_up(other, "intruder@detect-limit.example", "Other Agency")
            assert (await _detect(other, cid)).status_code == 404
            # An id that is nobody's is the same answer, and costs nobody anything.
            assert (await _detect(other, ids.new_id(ids.CLIENT))).status_code == 404

        assert await _day_keys(settings) == []
        assert runs.searches == []
        assert runs.scans == []
        # The owner's day was never touched.
        assert (await _detect(client, cid)).status_code == 201
        assert len(runs.searches) == 1


class TestAScanIsNeverLimited:
    """A scan's own detection goes through `ensure_set_for_scan`, never the route."""

    @staticmethod
    async def _scan(
        client: AsyncClient, cid: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Start a scan and let the chain run to its end inline, minus the engine phase.

        The competitor-set phase is the one under test and it comes first; the engine
        loop would otherwise call the real providers.
        """

        async def no_engines(*args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG001
            return None

        monkeypatch.setattr(scan_runner, "run_scan", no_engines)
        resp = await client.post(f"{BASE}/clients/{cid}/scans", json={})
        assert resp.status_code == 202, resp.text

    async def test_a_scan_detects_though_the_clients_day_is_spent(
        self, client: AsyncClient, session: AsyncSession, settings: Settings,
        runs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)
        # The day is spent and the client has no set: what a button run that failed
        # before it wrote anything leaves behind.
        redis = redis_module.get_redis(settings)
        await rate_limit.claim_detection(redis, settings, client_id=cid, agency_id=agency_id)
        [key] = await _day_keys(settings)
        await redis.pexpire(key, 600_000)

        await self._scan(client, cid, monkeypatch)

        assert len(runs.searches) == 1, "the scan did not run detection"
        sets = (
            await session.execute(
                select(CompetitorSet)
                .join(Scan, Scan.id == CompetitorSet.scan_id)
                .where(Scan.client_id == cid)
            )
        ).scalars().all()
        assert len(sets) == 1, "the scan has no competitor set"
        # Neither renewed nor replaced: the scan did not touch the day.
        assert 0 < await redis.pttl(key) <= 600_000

    async def test_a_scan_does_not_spend_the_clients_day(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        await self._scan(client, cid, monkeypatch)

        assert len(runs.searches) == 1, "the scan did not run detection"
        assert await _day_keys(settings) == []
        # So the button is still there, once, today.
        assert (await _detect(client, cid)).status_code == 201
        assert len(runs.searches) == 2
        assert (await _detect(client, cid)).status_code == 429


class TestRedisDown:
    @pytest.mark.parametrize(
        "failure",
        [RedisConnectionError, RedisTimeoutError, RedisResponseError, OSError],
        ids=["connection", "timeout", "script-error", "os-error"],
    )
    async def test_it_fails_closed_and_runs_nothing(
        self, client: AsyncClient, settings: Settings, runs: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, failure: type[Exception],
    ) -> None:
        await _sign_up(client)
        cid = await _make_client(client)

        async def down(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
            raise failure("SENTINEL-redis-host:6379 is down")

        # Only the limiter's own call: the session lookup still reaches Redis, so the
        # request gets as far as the check, which is where this has to stop it.
        monkeypatch.setattr(rate_limit, "_eval", down)
        with structlog.testing.capture_logs() as logs:
            resp = await _detect(client, cid)

        assert resp.status_code == 503, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/service-unavailable"
        assert body["detail"] == UNAVAILABLE
        assert "SENTINEL" not in resp.text
        assert runs.searches == []
        assert runs.scans == []
        assert await _day_keys(settings) == []
        warnings = [e for e in logs if e["event"] == "competitors.detect.rate_limit_unavailable"]
        assert len(warnings) == 1
        assert set(warnings[0]) == {"event", "log_level"}
        assert "SENTINEL" not in repr(logs)


class TestWhatIsLogged:
    async def test_a_refusal_logs_the_client_the_agency_and_the_seconds_left_and_nothing_else(
        self, client: AsyncClient
    ) -> None:
        agency_id = await _sign_up(client)
        cid = await _make_client(client)

        with structlog.testing.capture_logs() as logs:
            first = await _detect(client, cid)
            second = await _detect(client, cid)

        assert (first.status_code, second.status_code) == (201, 429)
        refusals = [e for e in logs if e["event"] == "competitors.detect.rate_limited"]
        assert refusals == [
            {
                "event": "competitors.detect.rate_limited",
                "log_level": "info",
                "client_id": cid,
                "agency_id": agency_id,
                "retry_after_s": int(second.headers["retry-after"]),
            }
        ]
        # An allowed run adds no line of its own.
        assert [e for e in logs if e["event"].startswith("competitors.detect.")] == refusals

    async def test_a_burst_across_one_agencys_clients_shows_as_one_agency_in_the_log(
        self, client: AsyncClient
    ) -> None:
        """The limit is per client, so the agency id is what makes a burst across many
        clients of one agency visible: each refusal names its own client and the same agency.
        Another agency's refusal carries that agency's id, not this one's."""
        agency_id = await _sign_up(client)
        clients = [await _make_client(client, f"site{i}.com") for i in range(3)]
        transport = ASGITransport(app=client._transport.app)  # noqa: SLF001
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Origin": client.headers["Origin"]},
        ) as other:
            other_agency_id = await _sign_up(other, "other@detect-limit.example", "Other Agency")
            other_cid = await _make_client(other, "elsewhere.com")

            with structlog.testing.capture_logs() as logs:
                for cid in clients:
                    assert (await _detect(client, cid)).status_code == 201
                    assert (await _detect(client, cid)).status_code == 429
                assert (await _detect(other, other_cid)).status_code == 201
                assert (await _detect(other, other_cid)).status_code == 429

        refusals = [e for e in logs if e["event"] == "competitors.detect.rate_limited"]
        assert [(e["client_id"], e["agency_id"]) for e in refusals] == [
            *((cid, agency_id) for cid in clients),
            (other_cid, other_agency_id),
        ]
        assert agency_id != other_agency_id


class TestTheWaitIsRoundedUp:
    """"In about N hours" never sends anyone back before it would work.

    The seconds are rounded up too, and never below one: a day with a fraction of a
    second left is "retry in 1", not "retry in 0".
    """

    @pytest.mark.parametrize(
        ("ms_left", "wait"),
        [
            (1, "in under an hour"),
            (59 * 60_000, "in under an hour"),
            (HOUR_MS - 1, "in under an hour"),
            (HOUR_MS, "in about an hour"),
            (HOUR_MS + 1, "in about 2 hours"),
            (2 * HOUR_MS, "in about 2 hours"),
            (2 * HOUR_MS + 1, "in about 3 hours"),
            (23 * HOUR_MS + 1, "in about 24 hours"),
            (DAY_MS, "in about 24 hours"),
        ],
    )
    def test_the_sentence_and_the_retry_after(self, ms_left: int, wait: str) -> None:
        refusal = rate_limit._detect_refused(ms_left)  # noqa: SLF001
        assert refusal.status_code == 429
        assert refusal.problem_type == "rate-limited"
        assert refusal.detail == (
            f"Competitor detection already ran for this client in the last 24 hours. "
            f"It can run again {wait}."
        )
        assert refusal.headers == {"Retry-After": str(-(-ms_left // 1000))}

    @pytest.mark.parametrize(("ms_left", "seconds"), [(0, "1"), (1, "1"), (999, "1"), (1001, "2")])
    def test_retry_after_is_whole_seconds_and_at_least_one(
        self, ms_left: int, seconds: str
    ) -> None:
        refusal = rate_limit._detect_refused(ms_left)  # noqa: SLF001
        assert refusal.headers == {"Retry-After": seconds}

    def test_the_sentence_says_the_window_the_key_uses(self) -> None:
        """The words and the expiry are one number, not two that can drift."""
        assert rate_limit.DETECT_WINDOW_MS == DAY_MS
        assert "24 hours" in rate_limit._detect_refused(DAY_MS).detail  # noqa: SLF001
