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

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from avp_api import db as db_module
from avp_api import redis_client as redis_module
from avp_api.config import Settings, get_settings
from avp_api.deps import db_session
from avp_api.main import create_app
from avp_api.models import Base
from avp_api.services import competitors as detection
from avp_api.services.cocitation import CoCitationHit, CoCitationResult
from avp_api.services.serp import SerpHit, SerpResult

TEST_DATABASE_URL = os.environ.get(
    "AVP_TEST_DATABASE_URL", "postgresql+asyncpg://avp@127.0.0.1:55433/avp_test"
)
TEST_REDIS_URL = os.environ.get("AVP_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        app_secret="test-secret-not-used-in-any-real-environment",
        # Argon2 at production cost makes a suite that logs in dozens of times
        # unbearably slow. These are the library minimums; every test that
        # cares about hashing correctness still exercises the real algorithm.
        argon2_time_cost=1,
        argon2_memory_cost_kib=8,
        argon2_parallelism=1,
        default_seat_limit=3,
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


@pytest_asyncio.fixture
async def client(engine, settings: Settings) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    """An ASGI client wired to the test datastores."""
    get_settings.cache_clear()
    db_module._engine = engine  # noqa: SLF001 - test wiring
    db_module._sessionmaker = async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False
    )
    redis_module._pool = None  # noqa: SLF001

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

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
        transport=transport, base_url="http://testserver", follow_redirects=True
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
def signup_payload() -> dict[str, str]:
    return {
        "agencyName": "Meridian Search Partners",
        "fullName": "Dana Whitfield",
        "email": "dana@meridiansearch.example",
        "password": "correct-horse-battery-staple",
    }
