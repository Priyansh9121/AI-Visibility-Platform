"""Migrations must run clean from zero and match the models.

Epic 1 acceptance: "Migrations run clean from zero on a throwaway database."

These tests create a genuinely fresh database, run `alembic upgrade head`, and
then assert that autogenerate finds nothing left to do — which is the only
reliable way to catch a model change that nobody wrote a migration for.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from avp_api.models import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "infra" / "db"
ALEMBIC = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "alembic"

TEST_DB_URL = os.environ.get(
    "AVP_TEST_DATABASE_URL", "postgresql+asyncpg://avp@127.0.0.1:55433/avp_test"
)
FRESH_DB = "avp_migration_check"


def _admin_url() -> str:
    parts = urlsplit(TEST_DB_URL)
    return f"{parts.scheme}://{parts.netloc}/postgres"


def _fresh_url() -> str:
    parts = urlsplit(TEST_DB_URL)
    return f"{parts.scheme}://{parts.netloc}/{FRESH_DB}"


@pytest.fixture
async def fresh_database():  # noqa: ANN201
    """A database created from nothing, dropped afterwards."""
    admin = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{FRESH_DB}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{FRESH_DB}"'))
    await admin.dispose()

    yield _fresh_url()

    admin = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{FRESH_DB}" WITH (FORCE)'))
    await admin.dispose()


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ALEMBIC), *args],
        cwd=MIGRATIONS_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_migrations_run_clean_from_zero(fresh_database: str) -> None:
    result = _alembic(["upgrade", "head"], fresh_database)
    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"

    engine = create_async_engine(fresh_database)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        created = {r[0] for r in rows}
    await engine.dispose()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert created == expected, f"missing: {expected - created}, unexpected: {created - expected}"


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_no_model_drift_against_head(fresh_database: str) -> None:
    """After upgrading, autogenerate must find nothing to do.

    This is the test that catches "someone changed a model and forgot the
    migration" — the single most common way a schema and its migrations
    silently diverge.
    """
    assert _alembic(["upgrade", "head"], fresh_database).returncode == 0

    check = _alembic(["check"], fresh_database)
    assert check.returncode == 0, (
        "Models and migrations have diverged. Run:\n"
        "  cd infra/db && alembic revision --autogenerate -m 'describe change'\n\n"
        f"{check.stdout}\n{check.stderr}"
    )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_downgrade_to_base_removes_everything(fresh_database: str) -> None:
    """A migration that cannot be reversed cannot be safely deployed."""
    assert _alembic(["upgrade", "head"], fresh_database).returncode == 0

    result = _alembic(["downgrade", "base"], fresh_database)
    assert result.returncode == 0, f"downgrade failed:\n{result.stdout}\n{result.stderr}"

    engine = create_async_engine(fresh_database)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        remaining = {r[0] for r in rows}
    await engine.dispose()

    # alembic_version is bookkeeping and legitimately survives a downgrade.
    assert remaining <= {"alembic_version"}, f"downgrade left tables behind: {remaining}"
