"""Migrations must run clean from zero and match the models.

Epic 1 acceptance: "Migrations run clean from zero on a throwaway database."

These tests create a genuinely fresh database, run `alembic upgrade head`, and
then assert that autogenerate finds nothing left to do — which is the only
reliable way to catch a model change that nobody wrote a migration for.

Two further things are held here.

  - Every run gets a scratch database under a name of its own (the `scratch_databases` fixture
    in conftest.py says why, and what a killed run leaves behind), dropped in a `finally`, so
    a failed test leaves nothing behind. `test_enum_constraints.py` uses the same fixture.
  - What a migration makes the server say (`RAISE NOTICE`, `RAISE WARNING`) reaches the deploy
    log. asyncpg drops a server message unless a listener is registered; `env.py` registers
    one and passes each message to Alembic's own logger. The tests run the REAL `env.py`, as
    `alembic upgrade head` does on Render, over a one-revision script directory of their own.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from avp_api.models import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "infra" / "db"
ALEMBIC = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "alembic"

# A scratch database is `<prefix><8 random hex digits>`. Every earlier run shared the one name
# `avp_migration_check`, with no suffix.
SCRATCH_PREFIX = "avp_migration_check_"


@pytest.fixture
async def fresh_database(scratch_databases) -> AsyncIterator[str]:  # noqa: ANN001
    """A database created from nothing, dropped afterwards."""
    async with scratch_databases.database(SCRATCH_PREFIX) as url:
        yield url


def _alembic(
    args: list[str], db_url: str, *, config: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real `alembic`. Without `config`, the repo's own `infra/db/alembic.ini`."""
    return subprocess.run(
        [str(ALEMBIC), *(["-c", str(config)] if config else []), *args],
        cwd=config.parent if config else MIGRATIONS_DIR,
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
async def test_no_routine_in_public_is_executable_by_public(fresh_database: str) -> None:
    """A guard for later revisions (see `..._revoke_data_api_grants.py`).

    Postgres grants EXECUTE on a new function to PUBLIC, and the Supabase Data API roles
    inherit it, so revoking from them by name does not remove it. Nothing creates a function in
    `public` today. The revision that does must take EXECUTE from PUBLIC on it, or keep it out
    of `public`, and this fails until it does.
    """
    assert _alembic(["upgrade", "head"], fresh_database).returncode == 0

    engine = create_async_engine(fresh_database)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "select p.oid::regprocedure::text from pg_proc p "
                "cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                "where p.pronamespace = 'public'::regnamespace "
                "and a.grantee = 0 and a.privilege_type = 'EXECUTE' order by 1"
            )
        )
        executable_by_public = [r[0] for r in rows]
    await engine.dispose()

    assert executable_by_public == [], (
        f"PUBLIC may execute {executable_by_public}; revoke EXECUTE from PUBLIC on each, "
        "or create it outside `public`"
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


# --- the scratch database: a name of its own, dropped when the run ends -------------------


async def test_each_run_gets_a_scratch_database_of_its_own(scratch_databases) -> None:  # noqa: ANN001
    names: list[str] = []
    for _ in range(2):
        async with scratch_databases.database(SCRATCH_PREFIX) as url:
            name = make_url(url).database
            names.append(name)
            assert re.fullmatch(rf"{SCRATCH_PREFIX}[0-9a-f]{{8}}", name), name
            assert await scratch_databases.exists(name)
        assert not await scratch_databases.exists(name), f"{name} was left behind"

    assert names[0] != names[1]


async def test_a_second_run_does_not_drop_the_database_a_first_run_is_using(
    scratch_databases,  # noqa: ANN001
) -> None:
    """The collision a fixed name allowed. A run that began while another was mid-test dropped
    the shared name `WITH (FORCE)`, which ends the other run's connections and deletes what it
    had built. Here the first run keeps a connection and a table while a second comes and goes."""
    async with scratch_databases.database(SCRATCH_PREFIX) as first_url:
        first = create_async_engine(first_url)
        try:
            async with first.connect() as conn:
                await conn.execute(text("CREATE TABLE marker (id integer)"))
                await conn.commit()

                async with scratch_databases.database(SCRATCH_PREFIX) as second_url:
                    assert second_url != first_url

                still_there = await conn.execute(text("SELECT count(*) FROM marker"))
                assert still_there.scalar_one() == 0
        finally:
            await first.dispose()


async def test_the_scratch_database_is_dropped_when_the_run_fails(
    scratch_databases,  # noqa: ANN001
) -> None:
    names: list[str] = []
    with pytest.raises(RuntimeError, match="the test failed"):
        async with scratch_databases.database(SCRATCH_PREFIX) as url:
            names.append(make_url(url).database)
            assert await scratch_databases.exists(names[0])
            raise RuntimeError("the test failed")

    assert not await scratch_databases.exists(names[0]), f"{names[0]} was left behind"


# --- what a migration makes the server say reaches the deploy log ------------------------

REVISION_TEMPLATE = '''\
"""A revision whose only job is to make the server say something (test_migrations.py)."""
from alembic import op

revision = "aaaaaaaaaaaa"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute({sql!r})


def downgrade() -> None:
    pass
'''

# A line of the deploy log is `%(levelname)-5.5s [%(name)s] %(message)s` (alembic.ini), so the
# level is cut to five characters: INFO, WARNI.
_MIGRATION_LOG_LINE = re.compile(r"^(\w+)\s+\[alembic\.runtime\.migration\] (.*)$")


def _throwaway_environment(tmp_path: Path, sql: str) -> Path:
    """An Alembic script directory of its own: the REAL `env.py` (a symlink; it finds the repo
    through the link's target) and one revision that runs `sql`. Returns its `alembic.ini`, the
    repo's own with only `script_location` changed, so the logging is the deploy's."""
    script_dir = tmp_path / "migrations"
    (script_dir / "versions").mkdir(parents=True)
    (script_dir / "env.py").symlink_to(MIGRATIONS_DIR / "migrations" / "env.py")
    (script_dir / "versions" / "aaaaaaaaaaaa_make_the_server_talk.py").write_text(
        REVISION_TEMPLATE.format(sql=sql)
    )
    repo_ini = (MIGRATIONS_DIR / "alembic.ini").read_text()
    ini = repo_ini.replace("script_location = migrations", f"script_location = {script_dir}")
    assert ini != repo_ini, "alembic.ini no longer says `script_location = migrations`"
    config = tmp_path / "alembic.ini"
    config.write_text(ini)
    return config


def _migration_log(stderr: str) -> list[tuple[str, str]]:
    """What Alembic's logger wrote, in order, as (level, text)."""
    return [
        (match[1], match[2])
        for line in stderr.splitlines()
        if (match := _MIGRATION_LOG_LINE.match(line))
    ]


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_what_a_migration_makes_the_server_say_reaches_the_log(
    tmp_path: Path, fresh_database: str
) -> None:
    """A NOTICE is logged at INFO and a WARNING at WARNING, each with its severity and text, on
    the logger Alembic reports through. asyncpg drops both unless `env.py` registers a listener."""
    config = _throwaway_environment(
        tmp_path,
        "DO $$ BEGIN "
        "RAISE NOTICE 'a notice from the migration'; "
        "RAISE WARNING 'a warning from the migration'; "
        "RAISE INFO 'an info from the migration'; "
        "END $$",
    )

    result = _alembic(["upgrade", "head"], fresh_database, config=config)

    assert result.returncode == 0, f"upgrade failed:\n{result.stdout}\n{result.stderr}"
    assert "Traceback" not in result.stderr
    log = _migration_log(result.stderr)
    expected = [
        ("INFO", "Postgres NOTICE: a notice from the migration"),
        ("WARNI", "Postgres WARNING: a warning from the migration"),
        ("INFO", "Postgres INFO: an info from the migration"),
    ]
    # Each once, in the order the migration raised them.
    assert [entry for entry in log if entry in expected] == expected, result.stderr
    # And under the revision's own "Running upgrade" line, where a reader looks for them.
    running = next(i for i, (_, line) in enumerate(log) if line.startswith("Running upgrade"))
    assert log.index(expected[0]) > running


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_a_warning_before_a_failure_still_reaches_the_log(
    tmp_path: Path, fresh_database: str
) -> None:
    """The deploy log matters most when the migration fails, so what the server said before the
    error is there too."""
    config = _throwaway_environment(
        tmp_path,
        "DO $$ BEGIN "
        "RAISE WARNING 'a warning before the failure'; "
        "RAISE EXCEPTION 'the failure'; "
        "END $$",
    )

    result = _alembic(["upgrade", "head"], fresh_database, config=config)

    assert result.returncode != 0
    assert "the failure" in result.stderr
    assert ("WARNI", "Postgres WARNING: a warning before the failure") in _migration_log(
        result.stderr
    )
