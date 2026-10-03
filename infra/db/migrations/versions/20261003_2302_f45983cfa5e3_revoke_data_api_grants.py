"""revoke data api grants

Takes `anon` and `authenticated`, the two roles Supabase's Data API acts as, off
everything AVP keeps in schema `public`: the tables, sequences and routines that
exist, and the default privileges that would hand them the next table.

What Supabase grants. The Data API runs a request as `anon` (no sign-in) or
`authenticated` (signed in). On a stock project the default privileges of the
role `postgres` in schema `public` give those two roles, and `service_role`,
every privilege on each table, sequence and function created there afterwards.
AVP does not use the Data API. On 2026-10-03 it was off, the two roles held
only REFERENCES, TRIGGER and TRUNCATE (and MAINTAIN) on the 25 tables, and Row
Level Security was on. This revision makes the database itself hold nothing for
them, so that switching the Data API back on does not expose AVP's tables.

Why revoking alone is not enough. `REVOKE ... ON ALL TABLES IN SCHEMA public`
acts on the tables that exist when it runs. A table a later revision creates is
granted to both roles again at creation by the default privileges of the role
that creates it, so this revision also revokes from those defaults (`ALTER
DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ...`, and the same
for the migration user's own defaults, which any role may change). `postgres` is
the owner role whose defaults the founder's check on 2026-10-03 found at work;
if the migrations ever run as another role, that role's own defaults are what a
new table gets, and check 3 below lists the defaults of every owner role. A
default that applies to every schema, not only `public`, is not removed by a
per-schema revoke: check 3 shows such a row, and removing it would be a revision
of its own, the founder's call.

Which objects it revokes on. A REVOKE on a table or sequence whose owner the
migration user cannot act as fails with a permission error, and with it the
deploy, unless that user holds some privilege on it; on a routine, which PUBLIC
may execute, it only warns and revokes nothing. AVP owns no such object, but
`public` may hold one that Supabase or an extension put there. So the revision
lists the tables, views, sequences and routines in `public` and revokes only on
those whose owner the migration user can act as. It catches no error, and it
says nothing about an object it skips: that object keeps whatever it was
granted. The before-the-deploy query below lists every such object, and checks 1
and 2 report one afterwards. Each revoke takes ACCESS SHARE on its object until
the migration transaction ends (checked on PG18), so `lock_timeout` bounds a
wait behind another session that holds ACCESS EXCLUSIVE on it.

Functions keep PUBLIC's default EXECUTE. Postgres grants EXECUTE on every new
function to PUBLIC, which every role, `anon` and `authenticated` included,
inherits. Revoking from `anon` removes only what was granted to `anon` by name,
so after this revision both roles can still run any function in `public` that
has not had EXECUTE taken from PUBLIC. That does not matter for AVP's own
objects: no revision and nothing under `apps/api/src` creates a function,
procedure, trigger, view or extension in `public` (searched 2026-10-03), so
there is none to run. It matters only for a function Supabase or an extension
put there, and check 1 below lists every one with what each role can do with it.
The Data API is off, so none is reachable through it today. A future revision
that adds a function to `public` must itself take EXECUTE from PUBLIC on it, or
keep it out of `public`. Revoking from PUBLIC here would change what every other
role may run, so this revision does not.

Routines are revoked one by one as `ROUTINE`, which covers functions, aggregates
and procedures; `ON ALL FUNCTIONS` would leave a procedure's grant in place.
`ALTER DEFAULT PRIVILEGES ... ON FUNCTIONS` already covers procedures.

What it leaves alone: `service_role` (the role of the server-side key), PUBLIC,
other schemas, and the default privileges of any owner role other than
`postgres` and the migration user (`supabase_admin`, say, may have its own;
Alembic creates nothing as that role).

Where it runs. Every statement is skipped for a role that does not exist, and a
local or CI Postgres has none of `anon`, `authenticated` and `postgres`, so
there it does nothing. The `postgres` default-privilege step also needs the
migration user to be `postgres` or a member of it. If it is not, that step is
skipped, the rest still runs, and the deploy says so twice: a NOTICE from the
server, which asyncpg drops because `env.py` registers no listener for it, and
a WARNING that `upgrade()` logs through Alembic's own logger, which the deploy
log does show. Check 3 below is the other way to know.

`downgrade()` does nothing, on purpose: giving the Data API roles their grants
back is never wanted, and production is forward-only in practice.

Before the deploy (founder, read-only, in Supabase's SQL editor; the same four
queries are in docs/runbook.md, "Supabase grants check"). Confirm that the user
part of the API service's DATABASE_URL on Render is `postgres`, or
`postgres.<project-ref>` through the pooler: the revision acts as that user.
Then run this. It lists every table, view, sequence and routine in `public`
whose owner `postgres` cannot act as. Expect no rows.

    select c.oid::regclass::text as object, c.relkind::text as kind,
           pg_get_userbyid(c.relowner) as owner
      from pg_class c
     where c.relnamespace = 'public'::regnamespace
       and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
       and not pg_has_role('postgres', c.relowner, 'USAGE')
    union all
    select p.oid::regprocedure::text, 'routine', pg_get_userbyid(p.proowner)
      from pg_proc p
     where p.pronamespace = 'public'::regnamespace
       and not pg_has_role('postgres', p.proowner, 'USAGE')
     order by 1;

Check after the deploy (the same, read-only).

1. Every function and procedure in `public`, and whether each role can run it.
   Expect no rows. A row whose `from_extension` is filled is not ours, and
   neither is a row whose `owner` is not `postgres`. `true` in the two
   `can_execute` columns is PUBLIC's default (above), not a grant to the role.

    select p.oid::regprocedure                                       as routine,
           pg_get_userbyid(p.proowner)                               as owner,
           p.prosecdef                                               as security_definer,
           has_function_privilege('anon', p.oid, 'EXECUTE')          as anon_can_execute,
           has_function_privilege('authenticated', p.oid, 'EXECUTE') as authenticated_can_execute,
           (select e.extname
              from pg_depend d
              join pg_extension e on e.oid = d.refobjid
             where d.classid = 'pg_proc'::regclass and d.objid = p.oid
               and d.deptype = 'e')                                  as from_extension
      from pg_proc p
     where p.pronamespace = 'public'::regnamespace
     order by p.proname, p.oid;

2. Every privilege either role still holds, by name, on a table, view, sequence,
   column or routine in `public`, and every grant to PUBLIC on a relation or
   column (`kind` is `r` table, `v` view, `S` sequence, `column` or `routine`;
   `grantee` is `PUBLIC` for those). Expect no rows.

    select c.relname::text as object, c.relkind::text as kind, null::name as column_name,
           a.privilege_type, coalesce(r.rolname, 'PUBLIC') as grantee
      from pg_class c
     cross join lateral aclexplode(c.relacl) a
      left join pg_roles r on r.oid = a.grantee
     where c.relnamespace = 'public'::regnamespace
       and (a.grantee = 0 or r.rolname in ('anon', 'authenticated'))
    union all
    select c.relname::text, 'column', att.attname, a.privilege_type, coalesce(r.rolname, 'PUBLIC')
      from pg_attribute att
      join pg_class c on c.oid = att.attrelid
     cross join lateral aclexplode(att.attacl) a
      left join pg_roles r on r.oid = a.grantee
     where c.relnamespace = 'public'::regnamespace
       and (a.grantee = 0 or r.rolname in ('anon', 'authenticated'))
    union all
    select p.oid::regprocedure::text, 'routine', null, a.privilege_type, r.rolname
      from pg_proc p
     cross join lateral aclexplode(p.proacl) a
      join pg_roles r on r.oid = a.grantee
     where p.pronamespace = 'public'::regnamespace
       and r.rolname in ('anon', 'authenticated')
     order by 1, 3, 4, 5;

3. Every default privilege that still names either role, for any owner role and
   any schema. Expect no row with `owner_role` `postgres` whose `schema` is
   `public` or `(every schema)`. A row for another owner role (`supabase_admin`,
   say) may appear and is not ours.

    select pg_get_userbyid(d.defaclrole) as owner_role,
           case d.defaclnamespace when 0 then '(every schema)'
                else d.defaclnamespace::regnamespace::text end as schema,
           d.defaclobjtype::text as object_type,
           a.privilege_type, r.rolname as grantee
      from pg_default_acl d
     cross join lateral aclexplode(d.defaclacl) a
      join pg_roles r on r.oid = a.grantee
     where r.rolname in ('anon', 'authenticated')
     order by 1, 2, 3, 4, 5;

Revision ID: f45983cfa5e3
Revises: 3e8a5b7c9d14
Created: 2026-10-03 23:02
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f45983cfa5e3'
down_revision: str | None = '3e8a5b7c9d14'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One DO block, kept whole so tests/test_supabase_grants.py can run exactly what
# upgrade() runs. No colon is followed directly by a word character and there is
# no backslash: SQLAlchemy's text() would read the first as a bind parameter.
REVOKE_DATA_API_GRANTS_SQL = """
DO $$
DECLARE
    role_name text;
    target text;
    can_alter_defaults boolean := false;
BEGIN
    -- The default privileges of postgres can be changed only by a member of it.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
        IF pg_has_role(current_user, 'postgres', 'MEMBER') THEN
            can_alter_defaults := true;
        ELSE
            RAISE NOTICE 'revoke_data_api_grants: this user is not a member of role postgres, '
                'so its default privileges in schema public were left as they are';
        END IF;
    END IF;

    -- Everything below runs only for a role that exists in this cluster.
    FOR role_name IN
        SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') ORDER BY rolname
    LOOP
        -- Existing objects, only those whose owner this user can act as. A REVOKE on any other
        -- would fail the deploy, and no such object is AVP's. Nothing is caught.
        FOR target IN
            SELECT CASE c.relkind WHEN 'S' THEN 'SEQUENCE ' ELSE 'TABLE ' END
                   || format('public.%I', c.relname)
              FROM pg_class c
             WHERE c.relnamespace = CAST('public' AS regnamespace)
               AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
               AND pg_has_role(current_user, c.relowner, 'USAGE')
            UNION ALL
            SELECT 'ROUTINE ' || CAST(CAST(p.oid AS regprocedure) AS text)
              FROM pg_proc p
             WHERE p.pronamespace = CAST('public' AS regnamespace)
               AND pg_has_role(current_user, p.proowner, 'USAGE')
        LOOP
            EXECUTE format('REVOKE ALL ON %s FROM %I CASCADE', target, role_name);
        END LOOP;

        -- The default privileges of whoever runs this: any role may change its own.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON TABLES FROM %I CASCADE', role_name);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON SEQUENCES FROM %I CASCADE', role_name);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON FUNCTIONS FROM %I CASCADE', role_name);

        IF can_alter_defaults THEN
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                'REVOKE ALL ON TABLES FROM %I CASCADE', role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                'REVOKE ALL ON SEQUENCES FROM %I CASCADE', role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                'REVOKE ALL ON FUNCTIONS FROM %I CASCADE', role_name);
        END IF;
    END LOOP;
END
$$
"""

# True when there was something for the postgres default-privilege step to do (the
# role exists, and so does one of the two) and this user cannot do it.
POSTGRES_DEFAULTS_SKIPPED_SQL = """
SELECT CASE
         WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN false
         WHEN NOT EXISTS (
           SELECT 1 FROM pg_roles WHERE rolname IN ('anon', 'authenticated')) THEN false
         ELSE NOT pg_has_role(current_user, 'postgres', 'MEMBER')
       END
"""


def upgrade() -> None:
    # The house habit, and it bounds the one wait there is: each REVOKE on a named object takes
    # ACCESS SHARE (checked on PG18), so only another session's ACCESS EXCLUSIVE holds it up.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(REVOKE_DATA_API_GRANTS_SQL)
    # The server's NOTICE never reaches the deploy log, so say it where the log does look.
    # Online only: an offline render (`--sql`) has no database to ask.
    if (
        not op.get_context().as_sql
        and op.get_bind().execute(sa.text(POSTGRES_DEFAULTS_SKIPPED_SQL)).scalar()
    ):
        logging.getLogger("alembic.runtime.migration").warning(
            "revoke_data_api_grants: this database user is not a member of role postgres, "
            "so the default privileges of postgres in schema public were NOT revoked from "
            "anon and authenticated. Check 3 of the Supabase grants check in "
            "docs/runbook.md shows what is left."
        )


def downgrade() -> None:
    # Deliberately nothing: re-granting the Data API roles is never wanted (see the docstring).
    pass
