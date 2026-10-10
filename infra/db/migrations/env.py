"""Alembic environment.

Bridges §5.2's split layout: migrations live in /infra/db, the models they
reflect live in apps/api/src/avp_api/models.

**Why this runs async.** Alembic's default template uses a synchronous driver,
which in practice means psycopg2 or psycopg3 — and BOTH are LGPL-3.0, which
the dependency-licensing rule puts on the stop-and-ask list. Rather than seek an
exception for a dependency used only by migrations, this environment drives
migrations through asyncpg (Apache-2.0), which the service already depends on.
One driver, one licence, no exception needed.

**What the server says reaches the deploy log.** A migration can make Postgres talk:
`RAISE NOTICE`, `RAISE WARNING`, "relation already exists, skipping". asyncpg throws every
such message away unless a listener is registered on the connection, so a migration's own
warning used to vanish. `run_migrations_online` registers `_forward_server_message` on the
migration's connection, which hands each message to Alembic's own logger
(`alembic.runtime.migration`, the one that prints "Running upgrade ..."): a WARNING at WARNING,
anything else (NOTICE, INFO) at INFO, as `Postgres <SEVERITY>: <message text>`. The text is what
the migration wrote and the deploy log keeps it, so a migration must never RAISE a secret, a
token or an email address.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

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


# The logger Alembic reports through ("Running upgrade X -> Y"), which the deploy log shows.
MIGRATION_LOGGER = logging.getLogger("alembic.runtime.migration")


def _forward_server_message(_connection: object, message: Any) -> None:
    """Pass one message the server sent (a NOTICE, a WARNING, an INFO) to Alembic's logger.

    asyncpg calls this on the event loop, with its `PostgresLogMessage`, for each message the
    server sends outside a query's result: a migration's own `RAISE NOTICE`, the server's
    "already exists, skipping". A WARNING is logged at WARNING and every other severity at INFO,
    as `Postgres <severity>: <message text>`.

    The text is a migration's own words (or the server's) and the deploy log keeps it: a migration
    must never put a secret, a token or an email address in a RAISE. A message's DETAIL and HINT
    are not forwarded, because the server fills them from the data it was working on. The severity
    is the unlocalised one (`severity_en`), so `lc_messages` cannot change the label or the level.
    """
    severity = message.severity_en or message.severity
    level = logging.WARNING if severity == "WARNING" else logging.INFO
    MIGRATION_LOGGER.log(level, "Postgres %s: %s", severity, message.message)


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url(force_async=True)
    # hide_parameters: a failing migration's bound values stay out of the deploy log.
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", hide_parameters=True)

    async with connectable.connect() as connection:
        # asyncpg's own connection, under SQLAlchemy's adapter: the listener goes on that one.
        # It comes off again when the migrations end, so nothing that reuses the pooled
        # connection afterwards gets its messages logged as migration output.
        driver_connection: Any = (await connection.get_raw_connection()).driver_connection
        driver_connection.add_log_listener(_forward_server_message)
        try:
            await connection.run_sync(_do_run_migrations)
        finally:
            driver_connection.remove_log_listener(_forward_server_message)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
