"""Every Python enum member must be accepted by the database.

This exists because of a silent failure mode found in Epic 4.

Enums are mapped as `VARCHAR + CHECK` (`native_enum=False`). Alembic's
autogenerate does not diff the *contents* of a CHECK constraint — it sees an
unchanged VARCHAR column — so **adding a member to a Python enum produces an
empty migration**. `alembic check` reports no drift. Everything looks correct
until the first insert of the new value fails at runtime with a constraint
violation, in whichever code path happens to use it first.

Adding `Engine.CLAUDE_SEARCH` hit exactly this. These tests turn a runtime
constraint violation into a failing test, and they generalise: any future enum
member added without a hand-written migration fails here.
"""

from __future__ import annotations

import contextlib
import enum
import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from avp_api.models import Base

# ---------------------------------------------------------------------------
# These tests MUST run against a MIGRATED database, not the create_all() schema
# the rest of the suite uses.
#
# conftest.py builds the test schema with `Base.metadata.create_all()`, which
# generates every CHECK constraint from the *current* Python enum. Checking the
# Python enum against that database is tautological — it can never fail, which
# is exactly what happened when this guard was first written: a deliberately
# unmigrated enum member was injected and the test still passed.
#
# So this module stands up its own throwaway database and runs the real
# migrations against it. That also gives the suite its first actual execution
# coverage of the migration files: a broken revision now fails here rather than
# on someone's deploy.
# ---------------------------------------------------------------------------

# Each run's database is `<MIGRATION_DB_PREFIX><8 random hex digits>`, made by `scratch_databases`
# (conftest.py). It used to be one fixed name, `avp_migrationcheck`; the end of this module says
# what that allowed.
MIGRATION_DB_PREFIX = "avp_migrationcheck_"
INFRA_DB_DIR = Path(__file__).resolve().parents[3] / "infra" / "db"
ALEMBIC = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic"


@contextlib.asynccontextmanager
async def migrated_database(scratch_databases) -> AsyncIterator[AsyncEngine]:  # noqa: ANN001
    """A database built by running the real migrations from base to head, under a name of its own.

    It is dropped afterwards whatever happened: a test that failed, or an upgrade that did.
    """
    async with contextlib.AsyncExitStack() as stack:
        try:
            target = await stack.enter_async_context(
                scratch_databases.database(MIGRATION_DB_PREFIX)
            )
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"cannot create the migration-check database: {exc}")

        result = subprocess.run(  # noqa: S603
            [str(ALEMBIC), "upgrade", "head"],
            cwd=INFRA_DB_DIR,
            env={**os.environ, "DATABASE_URL": target},
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(
                "alembic upgrade head failed against a clean database:\n"
                + (result.stderr or result.stdout)[-2000:]
            )

        engine = create_async_engine(target)
        stack.push_async_callback(engine.dispose)  # last in, first out: before the database goes
        yield engine


@pytest_asyncio.fixture(scope="session")
async def migrated_engine(scratch_databases) -> AsyncIterator[AsyncEngine]:  # noqa: ANN001
    """A database built by running the real migrations from base to head."""
    async with migrated_database(scratch_databases) as engine:
        yield engine


def _enum_columns() -> list[tuple[str, str, type[enum.Enum]]]:
    """(table, column, python_enum) for every enum-backed column."""
    found: list[tuple[str, str, type[enum.Enum]]] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            enum_cls = getattr(column.type, "enum_class", None)
            if enum_cls is not None and issubclass(enum_cls, enum.Enum):
                found.append((table.name, column.name, enum_cls))
    return found


def test_there_are_enum_columns_to_check() -> None:
    """Guards the guard — a detection bug here would make every test vacuous."""
    columns = _enum_columns()
    assert columns, "no enum-backed columns detected; the introspection is broken"
    assert any(c == "engine" for _, c, _ in columns)


@pytest.mark.parametrize(
    ("table", "column", "enum_cls"),
    [(t, c, e) for t, c, e in _enum_columns()],
    ids=[f"{t}.{c}" for t, c, _ in _enum_columns()],
)
async def test_database_accepts_every_python_enum_member(
    migrated_engine, table: str, column: str, enum_cls: type[enum.Enum]  # noqa: ANN001
) -> None:
    """Every member must satisfy the column's CHECK constraint.

    Asserted against the MIGRATED database's live constraint, not the
    create_all() schema — see the module note. Reading the constraint rather
    than inserting a row keeps this fast and avoids foreign-key setup.
    """
    async with migrated_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(con.oid)
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    WHERE rel.relname = :table AND con.contype = 'c'
                    """
                ),
                {"table": table},
            )
        ).scalars().all()

    # The CHECK for this column names it; other CHECKs on the table do not.
    relevant = [d for d in rows if f"({column})::text" in d or f'"{column}"' in d]
    if not relevant:
        pytest.skip(f"{table}.{column} has no CHECK constraint to verify")

    definitions = " ".join(relevant)
    missing = [
        member.value
        for member in enum_cls
        if f"'{member.value}'" not in definitions
    ]
    assert not missing, (
        f"{table}.{column} rejects {missing}. The Python enum gained a member "
        f"without a migration — autogenerate cannot detect this, so the "
        f"migration must be hand-written (see the add_claude_search_engine "
        f"revision for the pattern, including op.f() on the constraint name)."
    )


# ---------------------------------------------------------------------------
# The database above is this run's own. Postgres allows one database per name in a cluster, and
# the fixed name this module used to have was shared by every run on the cluster: the run that
# dropped it `WITH (FORCE)` ended another run's connections and deleted its migrated schema (two
# worktrees, or two CI jobs on one server). `scratch_databases` (conftest.py) makes the name.
# ---------------------------------------------------------------------------


async def test_the_migration_check_database_has_a_name_of_its_own_per_run(
    migrated_engine, scratch_databases  # noqa: ANN001
) -> None:
    name = migrated_engine.url.database
    assert re.fullmatch(rf"{MIGRATION_DB_PREFIX}[0-9a-f]{{8}}", name), name
    assert await scratch_databases.exists(name)

    # Another run makes its own, under a name that is not this one.
    async with scratch_databases.database(MIGRATION_DB_PREFIX) as other_url:
        assert make_url(other_url).database != name


async def test_a_second_run_does_not_drop_the_migration_check_database_the_first_is_using(
    migrated_engine, scratch_databases  # noqa: ANN001
) -> None:
    async with migrated_engine.connect() as first_run:
        async with scratch_databases.database(MIGRATION_DB_PREFIX):
            pass  # a second run begins and ends

        applied = await first_run.execute(text("SELECT count(*) FROM alembic_version"))
        assert applied.scalar_one() == 1


async def test_the_migration_check_database_is_dropped_when_the_upgrade_fails(
    scratch_databases, monkeypatch  # noqa: ANN001
) -> None:
    """The case a random name makes matter: the old fixed name healed itself on the next run, a
    database left behind by a failed upgrade would now stay."""
    made: list[str] = []

    def failing_upgrade(command, **kwargs):  # noqa: ANN001, ANN202
        made.append(make_url(kwargs["env"]["DATABASE_URL"]).database)
        return subprocess.CompletedProcess(command, 1, "", "the upgrade failed")

    monkeypatch.setattr(subprocess, "run", failing_upgrade)
    with pytest.raises(pytest.fail.Exception, match="alembic upgrade head failed"):
        async with migrated_database(scratch_databases):
            raise AssertionError("not reached: the upgrade failed")

    assert not await scratch_databases.exists(made[0]), f"{made[0]} was left behind"


async def test_the_migration_check_database_is_dropped_when_the_test_using_it_fails(
    scratch_databases, monkeypatch  # noqa: ANN001
) -> None:
    made: list[str] = []

    def upgrade_that_does_nothing(command, **kwargs):  # noqa: ANN001, ANN202
        made.append(make_url(kwargs["env"]["DATABASE_URL"]).database)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", upgrade_that_does_nothing)
    with pytest.raises(RuntimeError, match="the test failed"):
        async with migrated_database(scratch_databases):
            raise RuntimeError("the test failed")

    assert not await scratch_databases.exists(made[0]), f"{made[0]} was left behind"
