"""Alembic environment.

Bridges §5.2's split layout: migrations live in /infra/db, the models they
reflect live in apps/api/src/avp_api/models.

**Why this runs async.** Alembic's default template uses a synchronous driver,
which in practice means psycopg2 or psycopg3 — and BOTH are LGPL-3.0, which
docs/ip-safety.md #6 puts on the stop-and-ask list. Rather than seek an
exception for a dependency used only by migrations, this environment drives
migrations through asyncpg (Apache-2.0), which the service already depends on.
One driver, one licence, no exception needed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# --- make avp_api importable from this directory ------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
API_SRC = REPO_ROOT / "apps" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

import sqlalchemy as sa  # noqa: E402

from avp_api.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _enum_check_constraint_names() -> set[str]:
    """Names of CHECK constraints that SQLAlchemy emits for VARCHAR-backed enums.

    Models use `Enum(..., native_enum=False, create_constraint=True)`, which
    generates a CHECK constraint at DDL time rather than exposing one in the
    metadata. Autogenerate therefore sees the constraint in the database, finds
    no counterpart in the models, and proposes dropping it — on every single
    run, for every enum column.

    Left unhandled, the first person to trust `--autogenerate` would ship a
    migration that silently strips enum validation from seventeen columns.
    Computing the names here and filtering them out keeps drift detection
    trustworthy for everything else.
    """
    names: set[str] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            type_ = column.type
            if isinstance(type_, sa.Enum) and not type_.native_enum and type_.name:
                names.add(f"ck_{table.name}_{type_.name}")
    return names


ENUM_CHECK_CONSTRAINTS = _enum_check_constraint_names()


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Autogenerate filter. See _enum_check_constraint_names."""
    if type_ == "check_constraint" and name in ENUM_CHECK_CONSTRAINTS:
        return False
    return True


def _database_url(*, force_async: bool) -> str:
    """Resolve the database URL.

    Precedence: `-x db_url=...` (used by the test harness against a throwaway
    cluster), then DATABASE_URL from the environment.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    url = x_args.get("db_url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No database URL. Set DATABASE_URL, or pass -x db_url=postgresql://..."
        )
    if force_async and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        # Without these, a changed column type or server default is silently
        # omitted from autogenerate output.
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        literal_binds=url is not None,
        dialect_opts={"paramstyle": "named"} if url is not None else {},
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — used for reviewable DDL diffs."""
    _configure(url=_database_url(force_async=False).replace("+asyncpg", ""))
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url(force_async=True)
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
