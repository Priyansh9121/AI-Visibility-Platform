"""The Supabase Data API roles hold nothing of AVP's (revision f45983cfa5e3).

`anon` and `authenticated` are the roles Supabase's Data API acts as. On a Supabase project
they are granted every table, sequence and function in `public`, and the default privileges
of `postgres` grant them each new one. The revision takes all of that away. A local or CI
Postgres has none of the three roles (`anon`, `authenticated`, `postgres`), so the tests
build the Supabase state themselves and run the revision against it.

Roles are cluster-wide, so a test must never leave one behind. Every test that creates a
role does it inside ONE transaction on ONE connection that is always rolled back (CREATE
ROLE, GRANT and ALTER DEFAULT PRIVILEGES are all transactional), and the `txn` fixture then
asks a second connection whether any role outlived it.

What is checked
  - the effective privileges, asked of Postgres (`has_table_privilege` and friends), not
    read back from the ACL the revision wrote, and the ACL entries named for either role;
  - that each app table in `Base.metadata` is individually detected before the revision and
    clear after it, so the check cannot pass by looking at nothing;
  - the default privileges of `postgres` and of the migration user in `public`, and what
    `postgres` creates afterwards;
  - the shape on Supabase (`postgres` owns everything and is not a superuser), an object
    `postgres` cannot act as, and that the ACL entries of every other grantee do not move;
  - what the revision does where roles are missing, or the migration user cannot act as
    `postgres`, and that it says so where the deploy log looks;
  - that the queries in docs/runbook.md find the grants and then find none.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from avp_api.models import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = REPO_ROOT / "infra" / "db" / "migrations" / "versions"
RUNBOOK = REPO_ROOT / "docs" / "runbook.md"
REVISION_ID = "f45983cfa5e3"
MIGRATION_LOGGER = "alembic.runtime.migration"  # the logger the revision warns through

DATA_API_ROLES = ("anon", "authenticated")
OWNER_ROLE = "postgres"
SERVICE_ROLE = "service_role"  # stands in for the role of Supabase's server-side key
OTHER_OWNER = "d1_grants_other_owner"  # stands in for supabase_admin
TESTER_ROLE = "d1_grants_tester"  # a migration user that is not postgres
STRANGER_ROLE = "d1_grants_stranger"  # owns objects that postgres cannot act as
ROLE_NAMES = (*DATA_API_ROLES, OWNER_ROLE, SERVICE_ROLE, OTHER_OWNER, TESTER_ROLE, STRANGER_ROLE)

PROBE_SEQUENCE = "d1_grants_probe_seq"
PROBE_FUNCTION = "d1_grants_probe_fn"
PROBE_PROCEDURE = "d1_grants_probe_proc"
STRANGER_TABLE = "d1_grants_stranger_t"
STRANGER_SEQUENCE = "d1_grants_stranger_seq"
STRANGER_FUNCTION = "d1_grants_stranger_fn"
STRANGER_PROCEDURE = "d1_grants_stranger_proc"

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
MAINTAIN_FROM_SERVER_VERSION = 170000  # the MAINTAIN privilege does not exist before Postgres 17
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
RELATION_KINDS = "'r', 'p', 'v', 'm', 'f'"  # what `ALL TABLES IN SCHEMA` covers


def _load_revision() -> ModuleType:
    (path,) = VERSIONS.glob(f"*_{REVISION_ID}_*.py")
    spec = importlib.util.spec_from_file_location(f"revision_{REVISION_ID}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def revision() -> ModuleType:
    return _load_revision()


def _run_revision(sync_conn: Connection, module: ModuleType, direction: str) -> None:
    """Run `upgrade` or `downgrade` the way `alembic upgrade` does: through `op`."""
    with Operations.context(MigrationContext.configure(sync_conn)):
        getattr(module, direction)()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What the revision said through Alembic's logger, which the deploy log shows."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno >= logging.WARNING
    ]


@contextlib.asynccontextmanager
async def _server_messages(conn: AsyncConnection) -> AsyncIterator[list[str]]:
    """The NOTICEs and WARNINGs the server sends while the block runs. asyncpg drops them
    unless a listener is registered, which is why the revision logs its own warning."""
    messages: list[str] = []

    def listener(_connection: object, message: object) -> None:
        messages.append(str(getattr(message, "message", message)))

    raw = await conn.get_raw_connection()
    raw.driver_connection.add_log_listener(listener)
    try:
        yield messages
        await asyncio.sleep(0)  # let the callbacks asyncpg scheduled run
    finally:
        raw.driver_connection.remove_log_listener(listener)


# --- the queries in docs/runbook.md ------------------------------------------------------


class RunbookQueries(NamedTuple):
    before_deploy: str
    functions: str
    named: str
    defaults: str


def _runbook_queries() -> RunbookQueries:
    body = RUNBOOK.read_text()
    section = body.split("\n## Supabase grants check\n", 1)[1].split("\n## ", 1)[0]
    queries = re.findall(r"```sql\n(.*?)```", section, flags=re.DOTALL)
    assert len(queries) == 4
    return RunbookQueries(*(query.strip().rstrip(";").strip() for query in queries))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


# --- small database helpers -----------------------------------------------------------


async def _rows(conn: AsyncConnection, sql: str) -> list[dict[str, object]]:
    result = await conn.exec_driver_sql(sql)
    return [dict(row) for row in result.mappings().all()]


async def _scalar(conn: AsyncConnection, sql: str) -> object:
    return (await conn.exec_driver_sql(sql)).scalar_one()


def _quoted(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


async def _existing_roles(conn: AsyncConnection) -> set[str]:
    rows = await _rows(
        conn, f"select rolname from pg_roles where rolname in ({_quoted(ROLE_NAMES)})"
    )
    return {str(row["rolname"]) for row in rows}


async def _committed_roles(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return await _existing_roles(conn)


# --- the Supabase state, built inside the transaction ----------------------------------


async def _create_roles(conn: AsyncConnection, names: Iterable[str]) -> None:
    present = await _existing_roles(conn)
    for name in names:
        if name not in present:
            await conn.exec_driver_sql(f"CREATE ROLE {name} NOLOGIN")


async def _create_probe_objects(conn: AsyncConnection) -> None:
    """A sequence, a function and a procedure, so none of those checks looks at nothing."""
    await conn.exec_driver_sql(f"CREATE SEQUENCE public.{PROBE_SEQUENCE}")
    await conn.exec_driver_sql(
        f"CREATE FUNCTION public.{PROBE_FUNCTION}() RETURNS integer LANGUAGE sql AS 'select 1'"
    )
    await conn.exec_driver_sql(
        f"CREATE PROCEDURE public.{PROBE_PROCEDURE}() LANGUAGE sql AS 'select 1'"
    )


async def _hand_everything_to(conn: AsyncConnection, role: str) -> None:
    """Make `role` own every table in `public` and the probes: the shape on Supabase, where
    `postgres` owns what Alembic made. Superusers may do this to any object."""
    tables = await _rows(
        conn,
        "select relname from pg_class "
        "where relnamespace = 'public'::regnamespace and relkind in ('r', 'p')",
    )
    for row in tables:
        await conn.exec_driver_sql(f'ALTER TABLE public."{row["relname"]}" OWNER TO {role}')
    await conn.exec_driver_sql(f"ALTER SEQUENCE public.{PROBE_SEQUENCE} OWNER TO {role}")
    await conn.exec_driver_sql(f"ALTER FUNCTION public.{PROBE_FUNCTION}() OWNER TO {role}")
    await conn.exec_driver_sql(f"ALTER PROCEDURE public.{PROBE_PROCEDURE}() OWNER TO {role}")


async def _create_stranger_objects(conn: AsyncConnection) -> None:
    """A table, a sequence, a function and a procedure owned by a role nobody can act as."""
    stranger = STRANGER_ROLE
    await conn.exec_driver_sql(f"CREATE TABLE public.{STRANGER_TABLE} (id integer)")
    await conn.exec_driver_sql(f"ALTER TABLE public.{STRANGER_TABLE} OWNER TO {stranger}")
    await conn.exec_driver_sql(f"CREATE SEQUENCE public.{STRANGER_SEQUENCE}")
    await conn.exec_driver_sql(f"ALTER SEQUENCE public.{STRANGER_SEQUENCE} OWNER TO {stranger}")
    await conn.exec_driver_sql(
        f"CREATE FUNCTION public.{STRANGER_FUNCTION}() RETURNS integer "
        "LANGUAGE sql AS 'select 1'"
    )
    await conn.exec_driver_sql(f"ALTER FUNCTION public.{STRANGER_FUNCTION}() OWNER TO {stranger}")
    await conn.exec_driver_sql(
        f"CREATE PROCEDURE public.{STRANGER_PROCEDURE}() LANGUAGE sql AS 'select 1'"
    )
    await conn.exec_driver_sql(
        f"ALTER PROCEDURE public.{STRANGER_PROCEDURE}() OWNER TO {stranger}"
    )


async def _grant_like_supabase(
    conn: AsyncConnection, roles: Sequence[str], *, defaults_of_postgres: bool
) -> None:
    """Grant `roles` what a Supabase project grants: everything, now and for what comes next.
    `service_role` gets the same, as it does there, so the tests can show it is left alone."""
    await _create_roles(conn, (SERVICE_ROLE,))
    grantees = ", ".join((*roles, SERVICE_ROLE))
    for kind in ("TABLES", "SEQUENCES", "ROUTINES"):
        await conn.exec_driver_sql(f"GRANT ALL ON ALL {kind} IN SCHEMA public TO {grantees}")
    # A grant on one column, which REVOKE ... ON TABLE must also remove.
    table = Base.metadata.sorted_tables[0]
    column = table.columns.keys()[0]
    await conn.exec_driver_sql(f'GRANT SELECT ("{column}") ON public."{table.name}" TO {roles[0]}')
    if defaults_of_postgres:
        for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            await conn.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
                f"GRANT ALL ON {kind} TO {grantees}"
            )


async def _let_act_as_the_owners(conn: AsyncConnection, role: str) -> None:
    """Make `role` a member of whoever owns the objects in `public`, as the migration user
    is on Supabase. It is a member of nothing else, `postgres` included."""
    owners = await _rows(
        conn,
        "select distinct pg_get_userbyid(relowner) as owner from pg_class "
        "where relnamespace = 'public'::regnamespace and relkind in ('r', 'p', 'v', 'm', 'f', 'S')",
    )
    for owner in owners:
        await conn.exec_driver_sql(f'GRANT "{owner["owner"]}" TO {role}')


# --- what the roles hold ---------------------------------------------------------------


async def _default_acl_entries(
    conn: AsyncConnection, owner: str, roles: Sequence[str] = DATA_API_ROLES
) -> list[str]:
    """The default privileges `owner` has set for `public` that name any of `roles`."""
    if not roles:
        return []
    rows = await _rows(
        conn,
        "select 'default ' || d.defaclobjtype::text || ' ' || a.privilege_type || ' ' "
        "|| r.rolname as what "
        "from pg_default_acl d cross join lateral aclexplode(d.defaclacl) a "
        "join pg_roles r on r.oid = a.grantee "
        "where d.defaclnamespace = 'public'::regnamespace "
        f"and d.defaclrole = (select oid from pg_roles where rolname = '{owner}') "
        f"and r.rolname in ({_quoted(roles)})",
    )
    return sorted(str(row["what"]) for row in rows)


async def _held(
    conn: AsyncConnection,
    roles: Sequence[str] = DATA_API_ROLES,
    *,
    default_owners: Sequence[str] = (OWNER_ROLE,),
) -> list[str]:
    """Everything `roles` can do, or are named for, in `public`. Empty means they hold nothing.

    `has_*_privilege` answers the question that matters (could the role do it), and counts
    what a role inherits. The ACL entries named for a role are read separately, so a privilege
    missing from the lists at the top of this file cannot hide. Routines are in the second
    group only: a role keeps PUBLIC's EXECUTE on a function whatever is revoked from it by name.
    The default privileges looked at are those of `default_owners`.
    """
    if not roles:
        return []
    role_list = _quoted(roles)
    server_version = int(str(await _scalar(conn, "select current_setting('server_version_num')")))
    table_privileges = list(TABLE_PRIVILEGES)
    if server_version >= MAINTAIN_FROM_SERVER_VERSION:
        table_privileges.append("MAINTAIN")
    queries = {
        "table": (
            f"select 'table ' || c.relname || ' ' || p.priv || ' ' || r.rolname as what "
            f"from pg_class c "
            f"cross join unnest(array[{role_list}]) as r(rolname) "
            f"cross join unnest(array[{_quoted(table_privileges)}]) as p(priv) "
            f"where c.relnamespace = 'public'::regnamespace and c.relkind in ({RELATION_KINDS}) "
            f"and has_table_privilege(cast(r.rolname as name), c.oid, p.priv)"
        ),
        "sequence": (
            f"select 'sequence ' || c.relname || ' ' || p.priv || ' ' || r.rolname as what "
            f"from pg_class c "
            f"cross join unnest(array[{role_list}]) as r(rolname) "
            f"cross join unnest(array[{_quoted(SEQUENCE_PRIVILEGES)}]) as p(priv) "
            f"where c.relnamespace = 'public'::regnamespace and c.relkind = 'S' "
            f"and has_sequence_privilege(cast(r.rolname as name), c.oid, p.priv)"
        ),
        "column": (
            f"select 'column ' || c.relname || ' ' || p.priv || ' ' || r.rolname as what "
            f"from pg_class c "
            f"cross join unnest(array[{role_list}]) as r(rolname) "
            f"cross join unnest(array[{_quoted(COLUMN_PRIVILEGES)}]) as p(priv) "
            f"where c.relnamespace = 'public'::regnamespace and c.relkind in ({RELATION_KINDS}) "
            f"and has_any_column_privilege(cast(r.rolname as name), c.oid, p.priv)"
        ),
        "named relation": (
            f"select 'named relation ' || c.relname || ' ' || a.privilege_type || ' ' || r.rolname "
            f"as what from pg_class c cross join lateral aclexplode(c.relacl) a "
            f"join pg_roles r on r.oid = a.grantee "
            f"where c.relnamespace = 'public'::regnamespace and r.rolname in ({role_list})"
        ),
        "named column": (
            f"select 'named column ' || c.relname || '.' || att.attname || ' ' "
            f"|| a.privilege_type || ' ' || r.rolname as what "
            f"from pg_attribute att join pg_class c on c.oid = att.attrelid "
            f"cross join lateral aclexplode(att.attacl) a join pg_roles r on r.oid = a.grantee "
            f"where c.relnamespace = 'public'::regnamespace and r.rolname in ({role_list})"
        ),
        "named routine": (
            f"select 'named routine ' || p.proname || ' ' || a.privilege_type || ' ' || r.rolname "
            f"as what from pg_proc p cross join lateral aclexplode(p.proacl) a "
            f"join pg_roles r on r.oid = a.grantee "
            f"where p.pronamespace = 'public'::regnamespace and r.rolname in ({role_list})"
        ),
    }
    held: list[str] = []
    for sql in queries.values():
        held.extend(str(row["what"]) for row in await _rows(conn, sql))
    for owner in default_owners:
        held.extend(await _default_acl_entries(conn, owner, roles))
    return sorted(held)


async def _other_grantees(conn: AsyncConnection) -> list[str]:
    """Every ACL entry in `public` held by anyone but the two Data API roles: the owner, PUBLIC,
    `service_role`, whoever else. A NULL ACL is read as the default it stands for, because a
    REVOKE writes the default out in full. The default privileges of every owner are included.
    """
    skip = _quoted(DATA_API_ROLES)
    grantee = "coalesce(r.rolname, 'PUBLIC')"
    rows = await _rows(
        conn,
        f"""
select 'relation ' || c.relname || ' ' || {grantee} || ' ' || a.privilege_type as what
  from pg_class c
 cross join lateral aclexplode(coalesce(c.relacl, acldefault(
       cast(case c.relkind when 'S' then 's' else 'r' end as "char"), c.relowner))) a
  left join pg_roles r on r.oid = a.grantee
 where c.relnamespace = 'public'::regnamespace and c.relkind in ({RELATION_KINDS}, 'S')
   and {grantee} not in ({skip})
union all
select 'column ' || c.relname || '.' || att.attname || ' ' || {grantee} || ' ' || a.privilege_type
  from pg_attribute att join pg_class c on c.oid = att.attrelid
 cross join lateral aclexplode(att.attacl) a
  left join pg_roles r on r.oid = a.grantee
 where c.relnamespace = 'public'::regnamespace and {grantee} not in ({skip})
union all
select 'routine ' || p.oid::regprocedure::text || ' ' || {grantee} || ' ' || a.privilege_type
  from pg_proc p
 cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
  left join pg_roles r on r.oid = a.grantee
 where p.pronamespace = 'public'::regnamespace and {grantee} not in ({skip})
union all
select 'default ' || pg_get_userbyid(d.defaclrole) || ' ' || d.defaclnamespace::text || ' '
       || d.defaclobjtype::text || ' ' || {grantee} || ' ' || a.privilege_type
  from pg_default_acl d
 cross join lateral aclexplode(d.defaclacl) a
  left join pg_roles r on r.oid = a.grantee
 where {grantee} not in ({skip})
""",
    )
    return sorted(str(row["what"]) for row in rows)


ACL_SNAPSHOT_SQL = """
select 'relation' as what, c.relname::text as name, c.relacl::text as acl
  from pg_class c where c.relnamespace = 'public'::regnamespace
union all
select 'column', c.relname::text || '.' || att.attname, att.attacl::text
  from pg_attribute att join pg_class c on c.oid = att.attrelid
 where c.relnamespace = 'public'::regnamespace and att.attacl is not null
union all
select 'routine', p.oid::regprocedure::text, p.proacl::text
  from pg_proc p where p.pronamespace = 'public'::regnamespace
union all
select 'default', pg_get_userbyid(d.defaclrole) || ' ' || d.defaclobjtype::text || ' ' ||
       d.defaclnamespace::text, d.defaclacl::text
  from pg_default_acl d
 order by 1, 2
"""


@pytest_asyncio.fixture
async def txn(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection inside one transaction that is always rolled back.

    Nothing a test does here outlives it, and that includes roles, which belong to the whole
    cluster. After the rollback a second connection, which sees only what was committed,
    checks that the roles are as they were.
    """
    roles_before = await _committed_roles(engine)
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            is_superuser = await _scalar(
                conn, "select rolsuper from pg_roles where rolname = current_user"
            )
            if not is_superuser:
                pytest.skip("creates roles and grants on tables, which needs a superuser")
            await conn.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
            yield conn
        finally:
            await trans.rollback()
    roles_after = await _committed_roles(engine)
    assert roles_after == roles_before, f"a role outlived its test: {roles_after - roles_before}"


def _require_absent(present: set[str], wanted_absent: Iterable[str]) -> None:
    in_the_way = present & set(wanted_absent)
    if in_the_way:
        pytest.skip(f"this case needs {sorted(in_the_way)} absent from the cluster")


# --- the revision does its job -----------------------------------------------------------


async def test_upgrade_leaves_both_roles_nothing_in_public(
    txn: AsyncConnection, revision: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)

    # The simulation took: each kind of grant is there, and every app table is seen
    # individually, so the empty answer below is not the answer to an empty question.
    before = await _held(txn)
    for prefix in (
        "table ",
        "sequence ",
        "column ",
        "named relation ",
        "named column ",
        "named routine ",
        "default r ",
        "default S ",
        "default f ",
    ):
        assert any(item.startswith(prefix) for item in before), f"no {prefix!r} in {before[:6]}"
    for table in Base.metadata.tables:
        for role in DATA_API_ROLES:
            assert f"table {table} SELECT {role}" in before, f"{role} was not seen on {table}"
    assert f"sequence {PROBE_SEQUENCE} USAGE anon" in before

    with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
        await txn.run_sync(_run_revision, revision, "upgrade")

    assert await _held(txn) == []
    assert await _scalar(txn, "show lock_timeout") == "5s"
    assert _warnings(caplog) == []


async def test_the_sql_constant_alone_revokes_everything(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """The constant the other tests run, without `upgrade` around it, through `text()`."""
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    assert await _held(txn)

    await txn.execute(text(revision.REVOKE_DATA_API_GRANTS_SQL))

    assert await _held(txn) == []


@pytest.mark.parametrize(
    "present",
    [
        pytest.param(("anon",), id="only-anon"),
        pytest.param(("authenticated",), id="only-authenticated"),
        pytest.param(("anon", "authenticated"), id="both-and-no-postgres"),
        pytest.param(("anon", "authenticated", OWNER_ROLE), id="both-and-postgres"),
    ],
)
async def test_it_touches_only_the_roles_that_exist(
    txn: AsyncConnection, revision: ModuleType, present: tuple[str, ...]
) -> None:
    _require_absent(await _existing_roles(txn), set(DATA_API_ROLES + (OWNER_ROLE,)) - set(present))
    await _create_roles(txn, present)
    await _create_probe_objects(txn)
    data_api_roles = [role for role in present if role in DATA_API_ROLES]
    await _grant_like_supabase(
        txn, data_api_roles, defaults_of_postgres=OWNER_ROLE in present
    )
    assert await _held(txn, data_api_roles)

    await txn.execute(text(revision.REVOKE_DATA_API_GRANTS_SQL))

    assert await _held(txn, data_api_roles) == []


async def test_with_none_of_the_roles_it_changes_nothing(
    txn: AsyncConnection, revision: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """A local or CI Postgres: every statement is skipped, no ACL moves, nothing is logged."""
    _require_absent(await _existing_roles(txn), (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    before = await _rows(txn, ACL_SNAPSHOT_SQL)
    assert any(row["what"] == "relation" for row in before)

    with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
        await txn.run_sync(_run_revision, revision, "upgrade")

    assert await _rows(txn, ACL_SNAPSHOT_SQL) == before
    assert _warnings(caplog) == []


async def test_a_user_who_cannot_act_as_postgres_skips_only_the_default_privileges(
    txn: AsyncConnection, revision: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """The migration user is not `postgres` and not a member of it: say so, twice, and carry on."""
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE, TESTER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    # The tester has default privileges of its own as well, which any role may change.
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        await txn.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {TESTER_ROLE} IN SCHEMA public "
            f"GRANT ALL ON {kind} TO anon, authenticated"
        )
    await _let_act_as_the_owners(txn, TESTER_ROLE)
    assert not await _scalar(txn, f"select pg_has_role('{TESTER_ROLE}', '{OWNER_ROLE}', 'MEMBER')")
    assert await _default_acl_entries(txn, TESTER_ROLE)

    await txn.exec_driver_sql(f"SET LOCAL ROLE {TESTER_ROLE}")
    async with _server_messages(txn) as notices:
        with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
            await txn.run_sync(_run_revision, revision, "upgrade")
    await txn.exec_driver_sql("RESET ROLE")

    left = await _held(txn)
    assert [item for item in left if not item.startswith("default ")] == []
    assert [item for item in left if item.startswith("default ")], "the defaults were not left"
    assert await _default_acl_entries(txn, TESTER_ROLE) == []
    assert len(notices) == 1 and "not a member of role postgres" in notices[0], notices
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("revoke_data_api_grants")
    assert "not a member of role postgres" in warnings[0]


async def test_there_is_no_warning_when_the_skipped_step_had_nothing_to_do(
    txn: AsyncConnection, revision: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """`postgres` exists and this user cannot act as it, but neither Data API role exists."""
    _require_absent(await _existing_roles(txn), DATA_API_ROLES)
    await _create_roles(txn, (OWNER_ROLE, TESTER_ROLE))
    await txn.exec_driver_sql(f"SET LOCAL ROLE {TESTER_ROLE}")

    with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
        await txn.run_sync(_run_revision, revision, "upgrade")
    await txn.exec_driver_sql("RESET ROLE")

    assert _warnings(caplog) == []


async def _create_as_postgres(conn: AsyncConnection, tag: str) -> None:
    """What a later revision does: `postgres` creates a table (and its sequence), a function
    and a procedure."""
    await conn.exec_driver_sql(f"SET LOCAL ROLE {OWNER_ROLE}")
    await conn.exec_driver_sql(f"CREATE TABLE public.d1_grants_new_{tag} (id serial primary key)")
    await conn.exec_driver_sql(
        f"CREATE FUNCTION public.d1_grants_new_fn_{tag}() RETURNS integer "
        "LANGUAGE sql AS 'select 1'"
    )
    await conn.exec_driver_sql(
        f"CREATE PROCEDURE public.d1_grants_new_proc_{tag}() LANGUAGE sql AS 'select 1'"
    )
    await conn.exec_driver_sql("RESET ROLE")


async def test_what_postgres_creates_after_the_revision_is_not_granted(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """Why the default privileges are revoked too: revoking from what exists is not enough."""
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await txn.exec_driver_sql(f"GRANT CREATE ON SCHEMA public TO {OWNER_ROLE}")
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)

    # Before the revision the defaults hand each new object to both roles, a procedure and
    # the sequence behind a serial column included.
    await _create_as_postgres(txn, "before")
    before = await _held(txn)
    for expected in (
        "table d1_grants_new_before SELECT anon",
        "sequence d1_grants_new_before_id_seq USAGE authenticated",
        "named routine d1_grants_new_fn_before EXECUTE anon",
        "named routine d1_grants_new_proc_before EXECUTE authenticated",
    ):
        assert expected in before, f"the defaults did not grant {expected!r}"

    await txn.run_sync(_run_revision, revision, "upgrade")
    await _create_as_postgres(txn, "after")

    assert await _held(txn) == []


async def test_it_also_revokes_the_migration_users_own_default_privileges(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """Any role may change its own default privileges, so this cannot fail, and a table the
    migration user creates afterwards is not handed to the two roles even when that user is
    not `postgres` (here there is no `postgres` at all)."""
    await _create_roles(txn, DATA_API_ROLES)
    me = str(await _scalar(txn, "select current_user"))
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        await txn.exec_driver_sql(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON {kind} TO anon, authenticated"
        )
    assert await _default_acl_entries(txn, me)
    await txn.exec_driver_sql("CREATE TABLE public.d1_grants_own_before (id serial primary key)")
    assert "table d1_grants_own_before SELECT anon" in await _held(txn, default_owners=(me,))

    await txn.run_sync(_run_revision, revision, "upgrade")
    await txn.exec_driver_sql("CREATE TABLE public.d1_grants_own_after (id serial primary key)")
    await txn.exec_driver_sql(
        "CREATE FUNCTION public.d1_grants_own_after_fn() RETURNS integer "
        "LANGUAGE sql AS 'select 1'"
    )

    assert await _default_acl_entries(txn, me) == []
    assert await _held(txn, default_owners=(me,)) == []


async def test_it_leaves_everyone_elses_privileges_alone(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """Only the two roles lose anything: the owner, PUBLIC, `service_role`, and the default
    privileges of another owner role (`supabase_admin` on Supabase) all stay as they were."""
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE, OTHER_OWNER))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    await txn.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OTHER_OWNER} IN SCHEMA public "
        "GRANT ALL ON TABLES TO anon, authenticated"
    )
    owner = await _scalar(txn, "select current_user")
    others_before = await _other_grantees(txn)
    for who in (SERVICE_ROLE, "PUBLIC", owner):
        assert any(f" {who} " in item for item in others_before), f"nothing seen for {who}"
    other_owner_before = await _default_acl_entries(txn, OTHER_OWNER)
    assert other_owner_before

    await txn.run_sync(_run_revision, revision, "upgrade")

    assert await _other_grantees(txn) == others_before
    assert await _default_acl_entries(txn, OTHER_OWNER) == other_owner_before


async def test_the_production_shape_postgres_owns_everything_and_is_not_a_superuser(
    txn: AsyncConnection, revision: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    """The shape on Supabase: the migrations run as `postgres`, which owns every table and is
    not a superuser. The revision must work from there, and touch nothing but the two roles."""
    queries = _runbook_queries()
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _hand_everything_to(txn, OWNER_ROLE)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    postgres_is_superuser = await _scalar(
        txn, f"select rolsuper from pg_roles where rolname = '{OWNER_ROLE}'"
    )
    assert postgres_is_superuser is False
    assert await _rows(txn, queries.before_deploy) == []
    assert await _held(txn)
    others_before = await _other_grantees(txn)

    await txn.exec_driver_sql(f"SET LOCAL ROLE {OWNER_ROLE}")
    assert await _scalar(txn, "select current_user") == OWNER_ROLE
    async with _server_messages(txn) as messages:
        with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
            await txn.run_sync(_run_revision, revision, "upgrade")
    await txn.exec_driver_sql("RESET ROLE")

    assert await _held(txn) == []
    assert await _other_grantees(txn) == others_before
    assert _warnings(caplog) == []
    assert messages == []


async def test_an_object_postgres_cannot_act_as_is_skipped_and_reported(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """An object in `public` that is not AVP's, owned by a role `postgres` cannot act as: a
    REVOKE on it would fail the deploy, so the revision skips it, and the before-the-deploy
    query is how anyone knows."""
    queries = _runbook_queries()
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE, STRANGER_ROLE))
    await _create_probe_objects(txn)
    await _hand_everything_to(txn, OWNER_ROLE)
    await _create_stranger_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    strangers = {
        (STRANGER_TABLE, "r"),
        (STRANGER_SEQUENCE, "S"),
        (f"{STRANGER_FUNCTION}()", "routine"),
        (f"{STRANGER_PROCEDURE}()", "routine"),
    }

    async def reported() -> set[tuple[object, object]]:
        rows = await _rows(txn, queries.before_deploy)
        assert {row["owner"] for row in rows} <= {STRANGER_ROLE}
        return {(row["object"], row["kind"]) for row in rows}

    assert await reported() == strangers

    await txn.exec_driver_sql(f"SET LOCAL ROLE {OWNER_ROLE}")
    async with _server_messages(txn) as messages:
        await txn.run_sync(_run_revision, revision, "upgrade")  # raises if it tries them
    await txn.exec_driver_sql("RESET ROLE")

    # A REVOKE on a routine nobody here can act as only draws a warning, so a revision that
    # tried one would be heard here, though it would change nothing.
    assert messages == []
    held = await _held(txn)
    assert held, "the skipped objects should keep their grants"
    assert all("d1_grants_stranger" in item for item in held), held
    named = await _rows(txn, queries.named)
    assert {str(row["object"]).split("(")[0] for row in named} <= {
        STRANGER_TABLE,
        STRANGER_SEQUENCE,
        STRANGER_FUNCTION,
        STRANGER_PROCEDURE,
    }
    assert await reported() == strangers


async def test_downgrade_does_not_give_the_grants_back(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    await txn.run_sync(_run_revision, revision, "upgrade")
    assert await _held(txn) == []

    await txn.run_sync(_run_revision, revision, "downgrade")

    assert await _held(txn) == []


async def test_functions_keep_publics_execute(txn: AsyncConnection, revision: ModuleType) -> None:
    """The docstring's claim, as a fact: the revision leaves PUBLIC's EXECUTE alone, so a role
    can still run a function exactly when PUBLIC can.

    Revoking from `anon` takes away only what was granted to `anon`. What it inherits from
    PUBLIC stays, which is why the founder's function query lists every function and does not
    expect its `can_execute` columns to be false.
    """

    async def public_may_execute() -> object:
        return await _scalar(
            txn,
            "select exists (select 1 from pg_proc p "
            "cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
            f"where p.proname = '{PROBE_FUNCTION}' and a.grantee = 0 "
            "and a.privilege_type = 'EXECUTE')",
        )

    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    before = await public_may_execute()
    if not before:
        pytest.skip("this cluster does not grant EXECUTE to PUBLIC on a new function")

    await txn.run_sync(_run_revision, revision, "upgrade")

    assert await public_may_execute() == before
    for role in DATA_API_ROLES:
        can_execute = await _scalar(
            txn, f"select has_function_privilege('{role}', 'public.{PROBE_FUNCTION}()', 'EXECUTE')"
        )
        assert can_execute is True


def test_the_offline_render_carries_the_sql_and_asks_no_database(revision: ModuleType) -> None:
    """`alembic upgrade --sql` has no database: the SQL is written out whole, and the
    revision's question about the migration user is not asked."""
    buffer = io.StringIO()
    # As `infra/db/migrations/env.py` configures an offline run: named parameters, so a `%`
    # in the SQL is written out as it is.
    context = MigrationContext.configure(
        dialect_name="postgresql",
        dialect_opts={"paramstyle": "named"},
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )

    with Operations.context(context):
        revision.upgrade()

    rendered = buffer.getvalue()
    assert "SET LOCAL lock_timeout = '5s';" in rendered
    assert revision.REVOKE_DATA_API_GRANTS_SQL.strip() in rendered
    assert revision.POSTGRES_DEFAULTS_SKIPPED_SQL.strip() not in rendered


# --- the founder's check queries -----------------------------------------------------------


def test_the_runbook_and_the_revision_carry_the_same_queries(revision: ModuleType) -> None:
    docstring = _squash(revision.__doc__)
    for query in _runbook_queries():
        assert _squash(query) in docstring, f"not in the revision's docstring: {query[:60]}"


async def test_the_runbook_queries_find_the_grants_and_then_find_none(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    queries = _runbook_queries()
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)

    # Before: they see what is there, so an empty answer later means something.
    named = await _rows(txn, queries.named)
    assert {row["grantee"] for row in named} == set(DATA_API_ROLES)
    assert {row["kind"] for row in named} >= {"r", "S", "column", "routine"}
    seen_tables = {row["object"] for row in named if row["kind"] == "r"}
    assert seen_tables >= set(Base.metadata.tables)
    defaults = await _rows(txn, queries.defaults)
    ours = [
        row for row in defaults if (row["owner_role"], row["schema"]) == (OWNER_ROLE, "public")
    ]
    assert {row["object_type"] for row in ours} == {"r", "S", "f"}
    assert {row["grantee"] for row in ours} == set(DATA_API_ROLES)
    functions = {str(row["routine"]): row for row in await _rows(txn, queries.functions)}
    probes = [name for name in functions if PROBE_FUNCTION in name or PROBE_PROCEDURE in name]
    assert len(probes) == 2
    assert all(functions[name]["anon_can_execute"] for name in probes)
    assert all(functions[name]["from_extension"] is None for name in probes)

    await txn.run_sync(_run_revision, revision, "upgrade")

    assert await _rows(txn, queries.named) == []
    after = await _rows(txn, queries.defaults)
    assert [row for row in after if row["owner_role"] == OWNER_ROLE] == []
    assert len(await _rows(txn, queries.functions)) == len(functions)  # still listed, as promised


async def test_query_2_also_reports_grants_to_public_and_the_revision_leaves_them(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    queries = _runbook_queries()
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await _create_probe_objects(txn)
    await _grant_like_supabase(txn, DATA_API_ROLES, defaults_of_postgres=True)
    table = Base.metadata.sorted_tables[0]
    column = table.columns.keys()[0]
    await txn.exec_driver_sql(f'GRANT SELECT ON public."{table.name}" TO PUBLIC')
    await txn.exec_driver_sql(f'GRANT SELECT ("{column}") ON public."{table.name}" TO PUBLIC')

    rows = await _rows(txn, queries.named)
    public_rows = {
        (row["object"], row["kind"]) for row in rows if row["grantee"] == "PUBLIC"
    }
    assert public_rows == {(table.name, "r"), (table.name, "column")}

    await txn.run_sync(_run_revision, revision, "upgrade")

    after = await _rows(txn, queries.named)
    assert {row["grantee"] for row in after} == {"PUBLIC"}
    assert {(row["object"], row["kind"]) for row in after} == public_rows


async def test_a_default_for_every_schema_is_reported_by_query_3_and_left_alone(
    txn: AsyncConnection, revision: ModuleType
) -> None:
    """A per-schema revoke cannot remove a default that applies to every schema. The runbook
    says so, and tells the founder that removing it is a decision for a revision of its own."""
    queries = _runbook_queries()
    await _create_roles(txn, (*DATA_API_ROLES, OWNER_ROLE))
    await txn.exec_driver_sql(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} GRANT ALL ON TABLES TO anon"
    )

    async def global_rows() -> list[dict[str, object]]:
        return [
            row
            for row in await _rows(txn, queries.defaults)
            if (row["owner_role"], row["schema"], row["grantee"])
            == (OWNER_ROLE, "(every schema)", "anon")
        ]

    assert await global_rows()

    await txn.run_sync(_run_revision, revision, "upgrade")

    assert await global_rows()


# --- the test database, as it is -----------------------------------------------------------


async def _assert_the_roles_hold_nothing(conn: AsyncConnection) -> None:
    """Whichever of `anon` and `authenticated` exist hold nothing in `public`, and neither
    appears in the default privileges of `postgres`. Where neither exists, as on a local or CI
    Postgres, there is nothing to hold and it passes."""
    tables = {
        str(row["relname"])
        for row in await _rows(
            conn,
            "select relname from pg_class "
            "where relnamespace = 'public'::regnamespace and relkind in ('r', 'p')",
        )
    }
    assert set(Base.metadata.tables) <= tables, "the app tables are not in the database"
    existing = await _existing_roles(conn)
    present = [role for role in DATA_API_ROLES if role in existing]
    held = await _held(conn, present)
    assert held == [], f"a Data API role holds something in public: {held}"


async def test_the_test_database_grants_nothing_to_either_role(engine: AsyncEngine) -> None:
    """The standing check, on the database the suite built, as it is."""
    async with engine.connect() as conn:
        await _assert_the_roles_hold_nothing(conn)


async def test_the_standing_check_fails_when_a_role_holds_something(txn: AsyncConnection) -> None:
    """The standing check can fail: given a role that holds one privilege, it does."""
    await _create_roles(txn, ("anon",))
    table = Base.metadata.sorted_tables[0].name
    await txn.exec_driver_sql(f'GRANT SELECT ON public."{table}" TO anon')

    with pytest.raises(AssertionError) as failure:
        await _assert_the_roles_hold_nothing(txn)
    assert f"table {table} SELECT anon" in str(failure.value)
