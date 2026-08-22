#!/usr/bin/env bash
# Throwaway Postgres + Redis for local development and tests.
#
#   ./infra/db/scripts/dev_cluster.sh up      # create (if needed) and start
#   ./infra/db/scripts/dev_cluster.sh down    # stop, keep the data
#   ./infra/db/scripts/dev_cluster.sh destroy # stop and delete the data
#   ./infra/db/scripts/dev_cluster.sh status
#
# Runs on port 55433 so it cannot collide with a system Postgres on 5432, and
# keeps its data in .devdata/ (gitignored) rather than a temp directory — a
# scratchpad or /tmp cluster is silently deleted by OS cleanup, which then
# looks like a mysterious test failure rather than a missing database.
set -euo pipefail

PORT=55433
ROLE=avp
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PGDATA="$ROOT/.devdata/pgdata"
LOGFILE="$ROOT/.devdata/postgres.log"
REDIS_PORT=6379
REDIS_LOG="$ROOT/.devdata/redis.log"

# Locate a COMPLETE server installation — one bin directory holding every
# binary below, `postgres` included. Searching per-binary on PATH is wrong here:
# Homebrew's libpq ships client tools (initdb, psql) with no server, so a
# per-binary lookup finds an initdb that then fails with
# "program \"postgres\" is needed by initdb but was not found".
find_pgbin() {
  local required="postgres initdb pg_ctl psql pg_isready"
  local candidates=()
  command -v pg_ctl >/dev/null 2>&1 && candidates+=("$(dirname "$(command -v pg_ctl)")")
  for dir in /Library/PostgreSQL/*/bin /opt/homebrew/opt/postgresql@*/bin \
             /opt/homebrew/opt/postgresql/bin /usr/local/opt/postgresql@*/bin \
             /usr/lib/postgresql/*/bin; do
    [ -d "$dir" ] && candidates+=("$dir")
  done
  for dir in "${candidates[@]}"; do
    local ok=1
    for bin in $required; do [ -x "$dir/$bin" ] || { ok=0; break; }; done
    [ "$ok" = 1 ] && { echo "$dir"; return; }
  done
  echo "error: no complete PostgreSQL server install found (need postgres, initdb," >&2
  echo "       pg_ctl, psql, pg_isready in one directory). Install PostgreSQL 15+." >&2
  exit 1
}

PGBIN="$(find_pgbin)"
INITDB="$PGBIN/initdb"
PGCTL="$PGBIN/pg_ctl"
PSQL="$PGBIN/psql"
ISREADY="$PGBIN/pg_isready"

up() {
  mkdir -p "$ROOT/.devdata"
  if [ ! -d "$PGDATA" ]; then
    echo "initialising cluster at $PGDATA"
    "$INITDB" -D "$PGDATA" -U "$ROLE" --auth=trust >/dev/null
  fi
  if "$ISREADY" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1; then
    echo "already running on port $PORT"
  else
    "$PGCTL" -D "$PGDATA" -l "$LOGFILE" \
      -o "-p $PORT -k /tmp -h 127.0.0.1" start >/dev/null
    for _ in $(seq 1 60); do
      "$ISREADY" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1 && break
    done
    echo "started on port $PORT"
  fi
  redis_up
  for db in avp_test avp_dev; do
    if ! "$PSQL" -h 127.0.0.1 -p "$PORT" -U "$ROLE" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1; then
      "$PSQL" -h 127.0.0.1 -p "$PORT" -U "$ROLE" -d postgres -qc "CREATE DATABASE $db;"
      echo "created database $db"
    fi
  done
  status
}

# Redis is started only if nothing already answers on the port, so a Redis the
# developer manages themselves (Homebrew services, Docker) is never displaced —
# and `down` leaves it alone for the same reason.
redis_up() {
  if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    echo "redis: already running on port $REDIS_PORT"
    return
  fi
  if ! command -v redis-server >/dev/null 2>&1; then
    echo "redis: NOT RUNNING and redis-server not installed — the API suite needs it" >&2
    return
  fi
  mkdir -p "$ROOT/.devdata"
  redis-server --port "$REDIS_PORT" --daemonize yes \
    --logfile "$REDIS_LOG" --dir "$ROOT/.devdata" --save ''
  for _ in $(seq 1 60); do
    redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 && break
  done
  echo "redis: started on port $REDIS_PORT"
}

down()    { "$PGCTL" -D "$PGDATA" stop >/dev/null 2>&1 || true; echo "stopped postgres (redis left alone)"; }
destroy() { down; rm -rf "$PGDATA" "$LOGFILE"; echo "destroyed $PGDATA"; }
status()  {
  if "$ISREADY" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1; then
    echo "cluster: UP on 127.0.0.1:$PORT"
    "$PSQL" -h 127.0.0.1 -p "$PORT" -U "$ROLE" -d postgres -tAc \
      "SELECT '  db: '||datname FROM pg_database WHERE datname LIKE 'avp%' ORDER BY 1"
  else
    echo "cluster: DOWN (port $PORT)"
  fi
  if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    echo "redis:   UP on 127.0.0.1:$REDIS_PORT"
  else
    echo "redis:   DOWN (port $REDIS_PORT)"
  fi
}

case "${1:-up}" in
  up) up ;; down) down ;; destroy) destroy ;; status) status ;;
  *) echo "usage: $0 {up|down|destroy|status}" >&2; exit 2 ;;
esac
