#!/usr/bin/env bash
# Migrations, then serve — Epic 18.2.
#
# `alembic upgrade head` runs here, as the first thing the container does,
# rather than as a host "release phase", because that is the one mechanism
# every persistent-process host has: a container that starts. Alembic takes a
# lock on its version table, so two replicas starting at once serialise on it
# rather than racing. If the migration fails the container exits non-zero
# and never serves a request against a schema it does not understand.
set -euo pipefail

cd /app/infra/db
echo "entrypoint: alembic upgrade head"
alembic upgrade head

cd /app/apps/api
echo "entrypoint: serving on :${PORT:-8000}"
exec uvicorn avp_api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
