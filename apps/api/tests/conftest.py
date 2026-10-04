"""Test fixtures.

Requires a Postgres and a Redis. Both are addressed via environment variables
so the suite runs against a throwaway cluster locally and a service container
in CI, with no code change:

    AVP_TEST_DATABASE_URL   default postgresql+asyncpg://avp@127.0.0.1:55433/avp_test
    AVP_TEST_REDIS_URL      default redis://127.0.0.1:6379/15

Redis DB 15 is used deliberately — the suite flushes it between tests, and
doing that to DB 0 would sign out every local development session.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from avp_api import db as db_module
from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.deps import db_session, redis_client, scan_executor
from avp_api.main import create_app
from avp_api.models import Base
from avp_api.services import audit_runner, rate_limit
from avp_api.services import competitors as detection
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.scan_executor import InlineScanExecutor
from avp_api.services.serp import SerpHit, SerpResult

TEST_DATABASE_URL = os.environ.get(
    "AVP_TEST_DATABASE_URL", "postgresql+asyncpg://avp@127.0.0.1:55433/avp_test"
)
TEST_REDIS_URL = os.environ.get("AVP_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

# The one origin the suite's "browser" comes from. The `settings` fixture lists
# exactly this in CORS_ALLOW_ORIGINS and the `client` fixture sends it on every
# request, because the API refuses a write whose Origin is not a listed one, and
# a write that carries a session cookie and names none (`avp_api/origin_check.py`)
# — a real browser's writes name theirs, and a bare HTTP client's do not. A second
# client built in a test that signs in and then writes must send it too:
# `headers={"Origin": client.headers["Origin"]}`.
TEST_ORIGIN = "http://localhost:3000"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        # HERMETIC — Epic 18. `Settings` reads `.env` by default, so until the
        # CI gate ran without one, this fixture silently inherited whatever
        # keys the developer's `.env` held: fourteen tests passed locally only
        # because a real ANTHROPIC_API_KEY was present for a code path whose
        # provider call is mocked. The suite now supplies its own values and
        # reads no file, so it sees the same world on every machine — and a
        # test that needs a provider must mock the provider, never the key.
        _env_file=None,
        environment="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        app_secret="test-secret-not-used-in-any-real-environment",
        # Placeholders, so point-of-use checks pass and the mocks take over.
        # Any test that reaches a real provider with these fails loudly.
        anthropic_api_key="test-anthropic-key-not-real",
        openai_api_key="test-openai-key-not-real",
        serpapi_key="test-serpapi-key-not-real",
        # Billing is NOT configured by default — 2026-09-28. Since the scan
        # allowance gate is on exactly when `STRIPE_SECRET_KEY` and
        # `STRIPE_PRICE_ID` are both set, placeholder keys here would put every
        # second scan in the suite behind a 402 (they did: 28 failures the
        # first time). CI and local development are unconfigured deployments,
        # and the suite now sees the same world. `test_billing.py` and
        # `test_scan_allowance.py` set the keys themselves, per test, for the
        # paths that need billing on.
        stripe_secret_key=None,
        stripe_price_id=None,
        # Argon2 at production cost makes a suite that logs in dozens of times
        # unbearably slow. These are the library minimums; every test that
        # cares about hashing correctness still exercises the real algorithm.
        argon2_time_cost=1,
        argon2_memory_cost_kib=8,
        argon2_parallelism=1,
        default_seat_limit=3,
        # Pinned for the same hermetic reason as the rest: a CORS_ALLOW_ORIGINS
        # in the developer's shell would otherwise change which Origin the
        # suite's client is allowed to send.
        cors_allow_origins=[TEST_ORIGIN],
    )


@pytest_asyncio.fixture(scope="session")
async def engine(settings: Settings):  # noqa: ANN201
    eng = create_async_engine(settings.database_url, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:  # noqa: ANN001
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s


# Fixtures that mean a test actually touched a datastore. `client` and
# `session` both pull in `engine`, so asking for any of them puts `engine` in
# `request.fixturenames`.
_DATASTORE_FIXTURES = frozenset({"engine", "session", "client"})


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Truncate every table and flush Redis between tests that use them.

    TRUNCATE ... CASCADE rather than dropping and recreating the schema: two
    orders of magnitude faster, and it resets sequences too. Each test gets a
    genuinely empty database, so ordering between tests cannot matter.

    The datastore fixtures are resolved **lazily**, and only when the test
    actually requested one. Declaring `engine` as a parameter here would make
    this autouse fixture connect to Postgres and Redis for every test in the
    suite — including pure-filesystem ones like test_env_template.py, which
    would then fail with a connection error that has nothing to do with what
    they assert. That happened, and it cost real debugging time: a secret-scan
    test reported ConnectionRefused because a database it never touches was
    down.
    """
    from redis.asyncio import Redis
    from sqlalchemy import text

    yield

    if not (_DATASTORE_FIXTURES & set(request.fixturenames)):
        return

    engine = request.getfixturevalue("engine")
    settings: Settings = request.getfixturevalue("settings")

    table_names = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()



class EnginesOnlyScanExecutor:
    """Runs the engine phase and stops — the product's behaviour before 9.17.

    Epic 9.17 made a queued scan run the whole pipeline, which is the point of
    that epic. It also made "a scan with no score", "a scan with no audit" and
    "a scan with no fixes" unreachable through the endpoint, and those states
    are still real: a phase can fail, and the report has to render each absence
    honestly. Several tests exist to prove exactly that.

    So this exists to construct the degraded state DELIBERATELY rather than by
    omission — which is a better test anyway. A test using it is saying "a scan
    whose chained phases did not run", where before it said "a scan" and
    quietly relied on the product not doing its job.

    Install it by overriding `scan_executor_factory` in a test module or class.
    """

    async def submit(self, job, *, settings) -> None:  # noqa: ANN001
        from avp_api.db import get_sessionmaker
        from avp_api.models import Client, Scan
        from avp_api.services import scan_runner

        factory = get_sessionmaker(settings)
        async with factory() as session:
            scan = await session.get(Scan, job.scan_id)
            client = await session.get(Client, job.client_id)
            if scan is None or client is None:
                return
            await scan_runner.run_scan(
                session, scan, client,
                settings=settings, engines=job.engines, prompt_limit=job.prompt_limit,
            )


@pytest.fixture
def engines_only_executor():  # noqa: ANN201
    """`EnginesOnlyScanExecutor`, as a fixture so tests need no import.

    `tests/` is not a package, so `from tests.conftest import ...` does not
    resolve. A fixture is the route pytest actually supports.
    """
    return EnginesOnlyScanExecutor


@pytest.fixture
def scan_executor_factory():  # noqa: ANN201
    """How a queued scan runs in this test. Override to change it.

    Defaults to the full 9.17 chain, because that is what the product does and
    a test asserting otherwise should have to say so.
    """
    return InlineScanExecutor

@pytest_asyncio.fixture
async def client(  # noqa: ANN001
    engine, settings: Settings, scan_executor_factory
) -> AsyncIterator[AsyncClient]:
    """An ASGI client wired to the test datastores."""
    get_settings.cache_clear()
    db_module._engine = engine  # noqa: SLF001 - test wiring
    db_module._sessionmaker = async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False
    )
    redis_module._pool = None  # noqa: SLF001

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    # The session store must reach the TEST Redis, not the developer's.
    #
    # `redis_client.get_redis()` takes an optional Settings and falls back to
    # `get_settings()` when given none — and `get_settings` is an lru_cache
    # that has just been cleared, so the fallback rebuilds Settings from the
    # environment and `.env`. That is REDIS_URL=.../0 on a normal machine, so
    # every session this suite minted was landing in database 0 while
    # `_clean_state` dutifully flushed 15. The module docstring's promise —
    # "DB 15 deliberately, so a local dev session on DB 0 is never signed out"
    # — was not being kept, and sessions accumulated in the developer's own
    # Redis with no TTL sweep between tests.
    #
    # Found by Epic 9.14's seat-removal test, which reads Redis directly to
    # prove revocation happened and got an empty set from the wrong database.
    app.dependency_overrides[redis_client] = lambda: redis_module.get_redis(settings)
    # Scans execute INLINE in the suite — Epic 9.5.
    #
    # The endpoint is async in production: it returns 202 and the work happens
    # after the response. Tests that merely need a finished scan to assert
    # against should not each grow a poll loop, so the executor seam is
    # substituted for one that runs to completion before `submit` returns.
    # "The scan is done once the POST returns" therefore stays true here.
    #
    # It does NOT make the POST body report completion — the endpoint always
    # answers `queued` (see routers/scans.py). Completion is read with
    # `GET /scans/{scanId}`, exactly as a real caller would.
    #
    # Tests that need a scan to stay unexecuted override this again with their
    # own executor; see test_scan_endpoints.py's TestScanIsQueued.
    app.dependency_overrides[scan_executor] = lambda: scan_executor_factory()

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[db_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
        # What a browser on the web app sends with every write. Tests that need
        # a request with another Origin, or none, build a second client; see
        # `test_origin_check.py`.
        headers={"Origin": TEST_ORIGIN},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def stub_discovery(monkeypatch):  # noqa: ANN001, ANN201
    """Replace both discovery signals with deterministic stubs."""

    def _install(
        serp_domains: list[str] | None = None,
        cocit_brands: list[tuple[str, str | None]] | None = None,
        serp_ok: bool = True,
        cocit_ok: bool = True,
    ):
        async def fake_search_many(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            if not serp_ok:
                return [SerpResult(query=q, ok=False, error_code="SERP_TIMEOUT") for q in queries]
            # Two distinct queries, mirroring a real run. A single-query stub
            # would be gated out by MIN_SERP_QUERIES_FOR_UNCORROBORATED and
            # these tests would assert against an always-empty set.
            return [
                SerpResult(
                    query=q,
                    hits=[
                        SerpHit(domain=d, position=i, query=q)
                        for i, d in enumerate(serp_domains or [], 1)
                    ],
                )
                for q in queries[:2]
            ]

        async def fake_run_prompts(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            if not cocit_ok:
                return [
                    CoCitationResult(prompt=p, ok=False, error_code="PROVIDER_ERROR")
                    for p in prompts
                ]
            return [
                CoCitationResult(
                    prompt=prompts[0],
                    hits=[
                        CoCitationHit(name=n, domain=d, position=i, prompt=prompts[0])
                        for i, (n, d) in enumerate(cocit_brands or [], 1)
                    ],
                )
            ]

        monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_prompts)

    return _install


@pytest.fixture
def a_day_passes(settings: Settings):  # noqa: ANN201
    """Let every client's competitor-detection day run out, as it does after 24 hours.

    `POST /clients/{clientId}/competitors/detect` runs once per client per 24 hours
    (launch checklist 3.53). A test that re-detects a client on purpose (a corrected
    set, a struck rival, a changed result) is a test of a LATER day, so it says so with
    `await a_day_passes()` between the two runs, rather than the limit being switched
    off for the whole suite. Without it the second press is a 429 and the test is not
    testing what it names, and a test that does not check the second status can pass
    without the second run ever happening.

    Deleting the keys is what expiry does; `test_detect_rate_limit.py` lets a key expire
    for real.
    """

    async def _pass() -> None:
        redis = redis_module.get_redis(settings)
        keys = [k async for k in redis.scan_iter(f"rl:{rate_limit.DETECT_ACTION}:client:*")]
        if keys:
            await redis.delete(*keys)

    return _pass


@pytest.fixture
def signup_payload() -> dict[str, str]:
    return {
        "agencyName": "Meridian Search Partners",
        "fullName": "Dana Whitfield",
        "email": "dana@meridiansearch.example",
        "password": "correct-horse-battery-staple",
    }

# ---------------------------------------------------------------------------
# The chained scan's external boundaries — Epic 9.17
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_chain_externals(monkeypatch, request):  # noqa: ANN001, ANN201
    """Keep the chained scan phases off the network. **Autouse, deliberately.**

    Epic 9.17 made `POST /clients/{clientId}/scans` run the whole pipeline
    rather than stopping after engine execution, and `InlineScanExecutor` means
    the suite runs that chain synchronously inside the request. So three phases
    that no test used to reach are now reached by EVERY test that starts a
    scan: competitor detection (SerpApi + model calls), the technical audit
    (a real Playwright browser against a real domain), and fix generation
    (another model call).

    Without this the suite makes live third-party calls, spends real money, and
    hangs — which is exactly what it did the first time the chain was wired in,
    before this fixture existed.

    Autouse rather than opt-in for the reason Epic 9.15's Stripe guards are
    autouse: a test that forgets to stub does not fail loudly, it quietly bills
    somebody. The seams patched here are the same three `verify_e2e.py` wraps
    for timing, which is independent evidence they are the real boundaries.

    Individual tests still override these — `stub_discovery` replaces the
    detection stubs with its own, and the fixture is a no-op for any test that
    never starts a scan.
    """
    from avp_api.models.action_item import Effort, Priority
    from avp_api.services.ai_crawlers import evaluate_robots
    from avp_api.services.technical_audit import AuditSignals

    # --- Epic 3: detection, without SerpApi or the co-citation model calls ---
    async def fake_search_many(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
        return [
            SerpResult(
                query=q,
                hits=[
                    SerpHit(domain="zendesk.com", position=1, query=q),
                    SerpHit(domain="front.com", position=2, query=q),
                ],
            )
            for q in queries[:2]
        ]

    async def fake_run_seed_prompts(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
        return [
            CoCitationResult(
                prompt=prompts[0],
                hits=[
                    CoCitationHit(
                        name="Zendesk", domain="zendesk.com", position=1, prompt=prompts[0]
                    ),
                ],
            )
        ]

    monkeypatch.setattr(detection.serp_service, "search_many", fake_search_many)
    monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", fake_run_seed_prompts)

    # And SerpApi's account endpoint (U2, 2026-10-04). Reading it is free, but it is a real call
    # made with whatever key the settings hold, and every detection a test runs makes one. It
    # reads nothing here. `test_serp_quota.py` puts the real one back where it is the thing under
    # test, and checks that nothing else reaches it.
    async def fake_quota_left(settings=None):  # noqa: ANN001, ANN202, ARG001
        return None

    monkeypatch.setattr(detection.serp_service, "quota_left", fake_quota_left)

    # --- Epic 6: the audit, without launching a browser ---------------------
    #
    # A deliberately GOOD-ENOUGH site: indexable, one schema type, a sitemap.
    # Enough for `score_audit` to produce a real Technical Foundation, so
    # scoring includes the dimension instead of excluding it — which is the
    # thing the chain exists to make true.
    async def fake_audit_site(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
        domain = url.split("://", 1)[-1].split("/", 1)[0]
        return AuditSignals(
            url=url,
            domain=domain,
            http_status=200,
            ok=True,
            schema_types=["Organization"],
            has_organization_schema=True,
            has_title=True,
            has_meta_description=True,
            canonical_present=True,
            robots_txt_present=True,
            robots_allows_crawl=True,
            has_sitemap=True,
            is_indexable=True,
            h1_count=1,
            word_count=900,
            content_age_days=10,
            # Epic F. A realistic robots.txt rather than an empty policy: it
            # names ONE AI crawler and lets the rest through `*`, which is the
            # shape of the only real finding in `avp_dev` (Notion blocks
            # Amazonbot and nothing else). Parsed by the real parser, so the
            # chain persists rows the crawler-access endpoint can be tested
            # against end to end.
            #
            # Left EMPTY the stub would have made every scan-chain test assert
            # against a client with no policy at all, which is the one state
            # that reveals nothing.
            ai_crawler_access=evaluate_robots(
                "User-agent: *\nAllow: /\nDisallow: /admin/\n\n"
                "User-agent: CCBot\nDisallow: /\n"
            ),
        )

    monkeypatch.setattr(audit_runner, "audit_site", fake_audit_site)

    # --- Epic 8: fix generation, without the model ---------------------------
    #
    # Patched at the SDK boundary — `AsyncMessages.parse` — rather than at
    # `fix_generator.generate_fixes`, for two reasons.
    #
    # It is the same symbol `test_fix_generator.py::stub_model` patches, so a
    # test that wants control of the response simply patches it again and wins,
    # instead of being silently bypassed by a stub one level above it.
    #
    # And it leaves the REAL `generate_fixes` in the chain: the schema
    # validation, the banned-claim guard and `accept()`'s candidate matching all
    # still run, so what the chained tests exercise is production's code path
    # with production's guarantees, not a stub's idea of them.
    #
    # `messages.parse` is shared by five services (classify, co-citation,
    # sentiment, prompt generation, fixes), so this dispatches on
    # `output_format` and delegates everything that is not a fix set to the real
    # method — which the existing per-service stubs already cover.
    import anthropic

    from avp_api.services.fix_generator import GeneratedFix, GeneratedFixSet

    original_parse = anthropic.resources.messages.AsyncMessages.parse

    def _candidate_keys(kwargs) -> list[str]:  # noqa: ANN001
        content = kwargs["messages"][0]["content"]
        return [
            line.split(" — ")[0].removeprefix("- ")
            for line in content.splitlines()
            if line.startswith("- gap:") or line.startswith("- audit:")
        ]

    async def dispatching_parse(self, **kwargs):  # noqa: ANN001, ANN003
        if kwargs.get("output_format") is not GeneratedFixSet:
            return await original_parse(self, **kwargs)

        class _FakeResponse:
            stop_reason = "end_turn"
            parsed_output = GeneratedFixSet(
                fixes=[
                    GeneratedFix(
                        candidate_id=key,
                        # Numbered rather than keyed: quoting the candidate key
                        # back would put an internal identifier in customer
                        # copy, which the real guard rejects. A stub producing
                        # copy production would refuse tests the wrong thing.
                        title=f"Improve the site signal number {i}",
                        detail=(
                            "Add the missing markup to the page and confirm it "
                            "validates, so the engines can read it."
                        ),
                        priority=Priority.MEDIUM,
                        effort=Effort.M,
                        priority_reason="It moves a weighted dimension.",
                        effort_reason="A template edit and a redeploy.",
                    )
                    for i, key in enumerate(_candidate_keys(kwargs), start=1)
                ]
            )

        return _FakeResponse()

    # `test_call_bounds.py` needs the REAL parse: it proves a timeout bound by
    # faking the HTTP transport underneath the real client, and a response
    # stubbed in above the transport would never reach it. Opted out by a
    # marker rather than by file name, so the opt-out is visible where it is
    # used and registered in pyproject where it is defined.
    if request.node.get_closest_marker("real_parse") is None:
        monkeypatch.setattr(
            anthropic.resources.messages.AsyncMessages, "parse", dispatching_parse
        )


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):  # noqa: ANN001, ANN201
    """No test reaches the real resolver (2026-10-01, the egress guard).

    A test that exercises the crawler's fetching overrides it: `robots_web`
    with a `FakeWeb`, `test_egress.py` with its own table.
    """
    from avp_api.services import egress

    async def refuse(host, port):  # noqa: ANN001, ANN202, ARG001
        raise OSError("real DNS is not allowed in tests")

    monkeypatch.setattr(egress, "_getaddrinfo", refuse)


class _ChunkStream(httpx.AsyncByteStream):
    """A response body that is produced as it is read, so a test sees how much was pulled."""

    def __init__(self, chunks: Callable[[], AsyncIterator[bytes]]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._chunks():
            yield chunk


async def _never() -> httpx.Response:
    """An answer that does not come: what a handler returns to stall a request's headers."""
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def _later(seconds: float, response: httpx.Response) -> httpx.Response:
    """An answer that comes, slowly: what a handler returns to make a request take `seconds`."""
    await asyncio.sleep(seconds)
    return response


def _in_pieces(data: bytes, size: int = 65_536) -> Callable[[], AsyncIterator[bytes]]:
    """A body that arrives in network-sized pieces, as it does from a real server."""

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(data), size):
            yield data[start : start + size]

    return chunks


class FakeWeb:
    """The sites the robots gate talks to, served from memory — 2026-09-29.

    `serve(url, ...)` sets what a URL answers (any method); anything not served
    answers 404. Every request the gate makes is recorded in `requests` as
    `(method, url, user_agent)`, so a test can assert what was — and was not —
    fetched. `fail(url, exc)` makes a URL raise a transport error instead.

    DNS is faked too (2026-10-01, the egress guard): every host resolves to
    `PUBLIC_ADDRESS` unless `resolve_to(host, *addresses)` says otherwise —
    `dns_fails(host)` makes a name not resolve. Nothing here touches the real
    network, and `lookups` records every name asked for.

    Ways to make a server hard to read (launch checklist 3.41, rounds 2 and 3):
    `serve_stream(url, chunks)` answers with a body produced as it is read (a very long
    one, or one that never ends), `hang(url)` never answers at all, headers included, and
    `slow(url, seconds)` answers, but only after `seconds`.

    Every answer, `serve` too, is a STREAMED response, as a real client gets from a real
    server: the gate reads the raw stream (`Response.aiter_raw`), which a response that
    was built from a string and read already would refuse.
    """

    # A public address that is not in a documentation range (those are refused).
    PUBLIC_ADDRESS = "93.184.216.34"

    def __init__(self) -> None:
        self.resolutions: dict[str, list[str] | Exception] = {}
        self.lookups: list[str] = []
        self.routes: dict[str, tuple[int, str, dict[str, str]]] = {}
        self.streams: dict[
            str, tuple[int, dict[str, str], Callable[[], AsyncIterator[bytes]]]
        ] = {}
        self.hangs: set[str] = set()
        self.delays: dict[str, float] = {}
        self.failures: dict[str, Exception] = {}
        self.requests: list[tuple[str, str, str]] = []

    def serve(
        self, url: str, *, status: int = 200, body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.routes[url] = (status, body, headers or {"content-type": "text/plain"})

    def fail(self, url: str, exc: Exception) -> None:
        self.failures[url] = exc

    def serve_stream(
        self, url: str, chunks: Callable[[], AsyncIterator[bytes]], *, status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.streams[url] = (status, headers or {"content-type": "text/plain"}, chunks)

    def hang(self, url: str) -> None:
        self.hangs.add(url)

    def slow(self, url: str, seconds: float) -> None:
        self.delays[url] = seconds

    def resolve_to(self, host: str, *addresses: str) -> None:
        self.resolutions[host.lower()] = list(addresses)

    def dns_fails(self, host: str) -> None:
        self.resolutions[host.lower()] = OSError(f"{host} did not resolve")

    async def getaddrinfo(self, host: str, port: int) -> list[tuple]:
        import socket

        self.lookups.append(host)
        found = self.resolutions.get(host.lower(), [self.PUBLIC_ADDRESS])
        if isinstance(found, Exception):
            raise found
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM, 6, "", (address, port),
            )
            for address in found
        ]

    def handler(self, request):  # noqa: ANN001, ANN201
        import httpx

        url = str(request.url)
        self.requests.append((request.method, url, request.headers.get("user-agent", "")))
        if url in self.failures:
            raise self.failures[url]
        if url in self.hangs:
            return _never()  # MockTransport awaits what is not a Response
        if url in self.streams:
            status, headers, chunks = self.streams[url]
        else:
            status, body, headers = self.routes.get(url, (404, "", {}))
            chunks = _in_pieces(body.encode())
        response = httpx.Response(status, headers=headers, stream=_ChunkStream(chunks))
        if url in self.delays:
            return _later(self.delays[url], response)
        return response

    def fetched(self, method: str | None = None) -> list[str]:
        return [url for m, url, _ in self.requests if method is None or m == method]


@pytest.fixture
def robots_web(monkeypatch):  # noqa: ANN001, ANN201
    """Route the robots gate's HTTP — robots.txt, HEAD hops, sitemap — to a `FakeWeb`.

    Patched at `RobotsGate._client`, the one place the gate builds a client,
    so nothing else's traffic is touched. The user agent and redirect limit
    are the gate's own.
    """
    import httpx

    from avp_api.services import crawl, egress

    web = FakeWeb()
    real = httpx.AsyncClient

    def client(self):  # noqa: ANN001, ANN202
        return real(
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent},
            max_redirects=crawl.MAX_REDIRECTS,
            transport=httpx.MockTransport(web.handler),
        )

    monkeypatch.setattr(crawl.RobotsGate, "_client", client)
    # Every host resolves to a public address unless a test says otherwise, so
    # the gate's egress check never reaches the real resolver.
    monkeypatch.setattr(egress, "_getaddrinfo", web.getaddrinfo)
    return web
