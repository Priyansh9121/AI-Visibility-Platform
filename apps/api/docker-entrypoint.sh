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

# The Stripe SDK PRINTS every request it makes (method and URL), every error Stripe answers with
# (its message), and at `debug` the request and response bodies, to stderr when STRIPE_LOG is
# `info` or `debug`, past every logger level, and the platform keeps stderr. It reads the variable
# ONCE, as it is imported, so it cannot be switched off from Python: it has to be gone before
# anything here starts. Whatever the host sets, the API never prints those lines.
# (tests/test_secrets_stay_out_of_logs.py runs this script to prove it; logs.py keeps the SDK's
# own logger quiet.)
unset STRIPE_LOG

# `PYDANTIC_SETTINGS_DEBUG`, set to a true value, makes pydantic-settings write what each source
# it reads supplied for a setting (the environment, a `.env` file), unmasked, to its own logger
# at DEBUG: the provider keys, the Stripe key, the session secret, both URLs. Wherever that
# logger reaches a handler, a stderr one included, it is printed, and the platform keeps stderr.
# The API never needs it, so it is gone before anything starts, as STRIPE_LOG is.
# (tests/test_secrets_stay_out_of_logs.py runs this script to prove it.)
unset PYDANTIC_SETTINGS_DEBUG

cd /app/infra/db
echo "entrypoint: alembic upgrade head"
alembic upgrade head

cd /app/apps/api
echo "entrypoint: serving on :${PORT:-8000}"
# ONE worker, on purpose: scans run inside this process and the browser slot
# (services/browser_slot.py) and the one-run-per-agency count are per process, so a
# second worker would be a second browser and a second count. The flag is what pins
# it: uvicorn reads WEB_CONCURRENCY only when --workers is absent, so a host that
# sets that variable would otherwise start more of them without anyone asking.
#
# NO `Server: uvicorn` header on any response (launch checklist 3.1): it tells a
# stranger what runs here. The web app turns off X-Powered-By for the same reason.
exec uvicorn avp_api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --proxy-headers --forwarded-allow-ips='*' --no-server-header
