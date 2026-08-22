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

import enum
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

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

MIGRATION_DB = "avp_migrationcheck"
INFRA_DB_DIR = Path(__file__).resolve().parents[3] / "infra" / "db"


def _admin_url(base_url: str) -> str:
    parsed = urlparse(base_url.replace("postgresql+asyncpg://", "postgresql://"))
    return f"postgresql+asyncpg://{parsed.netloc}/postgres"


def _target_url(base_url: str) -> str:
    parsed = urlparse(base_url.replace("postgresql+asyncpg://", "postgresql://"))
    return f"postgresql+asyncpg://{parsed.netloc}/{MIGRATION_DB}"


@pytest_asyncio.fixture(scope="session")
async def migrated_engine(settings) -> AsyncIterator[object]:  # noqa: ANN001
    """A database built by running the real migrations from base to head."""
    base_url = str(settings.database_url)
    admin = create_async_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{MIGRATION_DB}"'))
    except Exception as exc:  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"cannot create the migration-check database: {exc}")
    await admin.dispose()

    target = _target_url(base_url)
    result = subprocess.run(  # noqa: S603
        [str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "alembic"), "upgrade", "head"],
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
    try:
        yield engine
    finally:
        await engine.dispose()
        admin = create_async_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
        await admin.dispose()


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
