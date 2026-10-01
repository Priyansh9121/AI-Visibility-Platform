---
name: supabase-ops
description: Use when reviewing or hardening AVP's production Supabase-hosted Postgres at launch (exposure of Alembic-created tables through the Data API, key and token hygiene, connection-pooler behaviour with asyncpg) or when diagnosing a Supabase-side database error. Do not use for AVP's own sign-in or session work (Google sign-in, `apps/api/src/avp_api/sessions.py`), for writing migrations (use Alembic), or for Postgres query and index tuning (use supabase-postgres-best-practices).
---

# supabase-ops (AVP)

AVP uses Supabase only as a hosted Postgres. The API talks to it through
SQLAlchemy async + asyncpg (`apps/api/src/avp_api/db.py`); Celery workers use the
same database. There is no `supabase-js`, no Supabase Auth and no Storage in
the repo (checked: `apps/web/package.json` has no `@supabase` package; no
migration mentions Supabase or enables RLS). AVP's own auth is first-party
sessions and Google sign-in in the API.

## Hard rules for AVP

1. **Schema changes go through Alembic only** (`infra/db/migrations/versions/`).
   Never use Supabase migrations, `supabase db push`, or the dashboard SQL
   editor for DDL. Use the `alembic-migrations` library skill; hand-edited DDL
   drifts production from the models and is invisible to `alembic check`.
2. **No Supabase MCP server against production.** The upstream plugin's MCP
   config had no `read_only` flag and no `project_ref`, so it gives
   account-wide write access. Do not configure one, do not create `.mcp.json`.
3. **Any production query is the founder's, run read-only.** Prepare the SQL and
   hand it over; do not run it, and do not ask for the connection string. Never
   print a secret, database URL or token.
4. **RLS is not AVP's tenancy mechanism.** Tenant isolation is `agency_id`
   scoping in the API (`apps/api/src/avp_api/deps.py`: principal's `agency_id`,
   `assert_own_agency`). Do not "fix" a review finding by adding `auth.uid()`
   policies: AVP has no Supabase Auth users, so `auth.uid()` is always null.
   RLS would matter only as defence in depth (see checklist item 1).
5. **No outbound host or dependency added** without classifying it in
   `apps/web/src/components/legal/providers.test.ts` and naming it on `/privacy`.

## Launch checklist (kept from upstream, narrowed to AVP)

1. **Data API exposure.** Supabase's auto-generated REST API can expose tables
   in exposed schemas (`public` by default) to the `anon` and `authenticated`
   roles, and by default, depending on project settings, Supabase's default privileges
   grant those roles SELECT, INSERT, UPDATE and DELETE on tables created later
   (the `pg_default_acl` query below shows it), including by Alembic as
   `postgres`. AVP does not use the Data API. Ask the founder to check in the
   dashboard (Data API settings) whether it is on. If it is, that is a launch
   blocker: the worst exposures are plaintext `scans.share_token`
   (`models/scan.py:124`) and `users.password_hash` (`models/tenancy.py:265`),
   readable and possibly writable with the public key.
   - **Preferred fix (founder action):** turn the Data API off, or remove
     `public` from the exposed schemas. This covers tables created later.
   - **If the founder revokes grants instead:** revoking covers only today's
     tables. They must also check and revoke `pg_default_acl` entries for
     `anon` and `authenticated` (tables, sequences, functions), or every new
     table is re-exposed.
   - **Read-only founder queries** (re-run after every revision that creates a
     table):
     ```sql
     select table_schema, table_name, grantee, privilege_type
       from information_schema.role_table_grants
      where grantee in ('anon','authenticated','PUBLIC') and table_schema = 'public';
     select defaclrole::regrole, defaclnamespace::regnamespace, defaclobjtype, defaclacl
       from pg_default_acl;
     ```
   - **RLS with no policies as defence in depth** (Alembic revision) works only if
     the app's database role owns the tables or has `BYPASSRLS`, and `FORCE ROW
     LEVEL SECURITY` is not set; otherwise the API itself gets zero rows. The
     founder must confirm the role first. `avp_test` passes either way, so tests
     will not catch it.
   - Grants, RLS and policies changed by hand are invisible to `alembic check`
     (CI checks a fresh DB and autogenerate ignores them), so record them in an
     Alembic revision and re-run the queries above. Revokes stay a founder runbook
     step, not an Alembic revision: CI runs stock postgres:17 (`ci.yml:69`) with no
     anon/authenticated roles. If one is ever put in a revision, guard it with
     `IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon')`.
   Upstream: a new SQL-created table may or may not be auto-exposed depending on
   project settings (source `SKILL.md:24`); RLS is separate from table access
   (`SKILL.md:26`). This item is the main reason this skill exists.
2. **Keys.** Never put the `service_role`/secret key or the database password in
   a client bundle, a log line, a repo file or a chat. Any `NEXT_PUBLIC_*`
   variable is sent to the browser (`SKILL.md:42`). AVP needs none of the
   Supabase API keys; if the project has them, the founder can rotate or ignore
   them. The database URL lives only in Render environment variables.
3. **Scoped tokens.** A classic Supabase personal access token carries full
   account access on every organisation and project (`SKILL.md:43`). If the
   founder ever needs a token for a script, it must be scoped to this project
   and the permissions required. Do not request one for agent use.
4. **Views and functions.** If a future Alembic revision adds a view or a
   `SECURITY DEFINER` function in an exposed schema: views bypass RLS unless
   `security_invoker = true` (Postgres 15+), and `SECURITY DEFINER` functions in
   `public` are executable by `anon` and `authenticated` by default
   (`SKILL.md:46, 67-68`). Prefer `SECURITY INVOKER`; keep definer functions in a
   non-exposed schema. AVP has none today (no RLS/functions found in migrations;
   re-check before relying on this).
5. **Supply chain.** Pin versions and commit lockfiles (`SKILL.md:74`); AVP already
   runs `uv lock --check` in CI, and the licence audits run in CI on branch launch-readiness and later (not yet on main).

The upstream Supabase-Auth/RLS policy traps (`user_metadata` claims, `auth.role()`,
`TO authenticated` without an ownership predicate, UPDATE needing SELECT, Storage
upsert) apply only if AVP ever adopts Supabase Auth, Storage or exposes tables
through the Data API with RLS. They are not AVP's model; consult upstream docs
then (see CHANGES.md for where they were removed).

## Connection pooling (asyncpg)

What the repo shows: one async engine, `pool_size` from settings (default 10),
`pool_pre_ping=True`, asyncpg driver (`db.py`, `config.py:78`). Which Supabase
endpoint production uses (direct, session pooler or transaction pooler) is not
visible in the repo and must be read by the founder from the Render environment
variable names and the dashboard; do not ask for the value.

Unverified, check against current Supabase docs before acting:
- Transaction-mode poolers (PgBouncer-style) do not support session-level
  prepared statements; asyncpg and SQLAlchemy's asyncpg dialect use them by
  default, which shows up as errors like "prepared statement ... does not
  exist" or "already exists". Commonly cited remedies are the session pooler or
  direct connection, or disabling the statement cache (asyncpg
  `statement_cache_size=0`, and SQLAlchemy's prepared-statement cache settings).
  AVP sets none of these in `db.py` today.
- Combined client pool (API pool_size + overflow, times instances, plus Celery
  workers) must stay under the project's connection limit; the limit depends on
  the Supabase plan and is unverified here.
- Checkout runs `SET LOCAL lock_timeout` inside a transaction (`services/billing.py`). `SET LOCAL` is transaction-scoped, so it should be safe behind a transaction pooler; unverified.

If a pooler error appears in production, the fix is a config change in `db.py`
or the URL: a founder-approved, separately scoped change, not something to try
live.

## Debugging pointers

For an error that looks Supabase-side (Postgres error code, "permission denied",
timeouts, connection drops, slow queries), start from the read-only signals
and Supabase's own Monitoring and Debugging guide
(https://supabase.com/docs/guides/monitoring-and-debugging.md, upstream `SKILL.md:150`):
1. `GET /api/v1/ready` (`apps/api/src/avp_api/routers/health.py`) says whether
   Postgres and Redis answer.
2. Render service logs for the asyncpg/SQLAlchemy
   exception class. Distinguish "cannot connect / too many connections" (limits,
   pooler) from "permission denied" (a role or grant changed outside Alembic).
3. The Supabase dashboard's Logs Explorer and Query Performance views are the
   founder's to read. Ask only for the exception class, SQLSTATE and constraint
   name, never the full error text or credentials: `logs.py` only rewrites
   access-log paths and `db.py` sets no `hide_parameters`, so an error message
   can carry bound parameter values.
4. For slow queries, missing indexes and locking, use `supabase-postgres-best-practices`
   and `systematic-debugging`.
5. Two failed attempts means stop and reconsider; do not retry the same thing,
   and never "fix" production by editing data or schema by hand.

## Not in this skill

Supabase CLI, MCP server setup, Edge Functions, Realtime, Vectors, Cron, Queues,
declarative schemas, `supabase db pull`/`advisors`. Not used by AVP.
