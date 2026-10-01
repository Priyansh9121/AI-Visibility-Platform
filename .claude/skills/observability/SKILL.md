---
name: observability
description: Guides adding or reviewing a log event in AVP's API or workers (structlog, dotted event names, redaction in apps/api/src/avp_api/logs.py, tests with capture_logs). Use when adding a log line around retries, webhooks, rate limits, Redis or provider calls, or asking what to log. Not for diagnosing a live failure (use systematic-debugging) or for choosing a metrics or tracing vendor (founder decision).
---

# Observability in AVP

AVP's telemetry is structured logs and nothing else. Logs go to stdout, which Render keeps and shows under each service's Logs tab. The web app logs nothing (no `console.*` in `apps/web/src` at 2026-10-01). There is no metrics backend, no tracing, no APM and no alerting rule in the repo. Do not add any as part of a feature.

## 1. Ask the on-call questions first

Before adding a line, write 2-4 questions the founder will ask in Render's Logs about this feature ("did the webhook apply or skip? which agency? why did Redis fail?"). Each event must answer one. No question, no line.

## 2. Event naming

`logger.<level>("dotted.event.name", field=value)` with a `structlog.get_logger(__name__)` logger. The name is a stable string, lower case, `area.thing.outcome`. Real ones to match:

- `stripe.webhook.second_subscription` (`services/billing.py`)
- `auth.rate_limit.ip_header_missing` (`services/rate_limit.py`)
- `redis.unavailable` (`errors.py`)
- `password_reset.confirm.succeeded`, `alerts.generated`, `billing.customer_created`

Never build the message with an f-string or `%s`: the fields carry the variable parts. (`main.py` still has one `%s` line, `api starting`; it is stdlib, not a pattern to copy.) Search Render's Logs by the event name, so renaming an event breaks the runbook: `docs/runbook.md` names `stripe.webhook.applied`, `auth.rate_limit.ip_header_missing` and the second-subscription event. Grep `docs/runbook.md` before renaming.

Levels: `error` = an invariant broke, someone acts; `warning` = degraded but handled (Redis down and answered 503, backstop signal fired); `info` = a business event; `debug` = detail, off in staging and production by default.

## 3. Fields: allowed and forbidden

Allowed: internal ids (`agency_id`, `user_id`, `scan_id`, Stripe event, customer and subscription ids), counts, booleans, positions, status names, and exception class names (`error=type(exc).__name__`). Allowlist fields one by one; never log a whole request body, Stripe event, Pydantic model or ORM row.

Forbidden, even at debug:
- emails, full names, IP addresses, passwords, session ids, reset/invitation/share/Google tickets or any token, API keys, `Authorization` headers;
- URLs that can carry a credential or token (links built for reset/invite/share; callback query strings);
- `logger.exception(...)` and `exc_info=True`: they render the traceback, which includes `str(exc)`. SQLAlchemy errors carry `[parameters: (...)]` (`db.py` sets no `hide_parameters`), so emails or hashes can reach Render's log. Log `error=type(exc).__name__` instead. Existing calls not yet reviewed (unconfirmed exposure, an open item): `routers/auth.py:582`, `services/scan_executor.py:222, 321, 336, 500, 612`.
- exception text when it may carry a host, key or address: log the class only, as `redis.unavailable` does;
- third-party raw text: AI answers, page HTML, competitor copy, model-written reasoning about client sites. Facts-only applies to logs too. The 2026-09-29 note in `logs.py` records that DEBUG lines `classify.result` and `fixes.reasoning` once printed such reasoning in production; the fix was a level filter, so do not log new text of that kind at any level.

If the only thing that would make a line useful is one of the forbidden fields, log an id that lets you look it up instead.

## 4. What the repo already redacts, and what it does not

`apps/api/src/avp_api/logs.py`: `configure_logging` applies `Settings.effective_log_level` (`LOG_LEVEL`, else INFO in staging/production, DEBUG in local/test), and `RedactSecretPaths` rewrites uvicorn access-log paths (legacy `/reports/<token>`, Google `callback`/`pending` query strings). It does not scan structlog event fields: a token you pass as a field is logged as you passed it. Redaction is a backstop, not permission.

Correlation: the API does not set a request id or bind contextvars (none found in `apps/api/src`). Correlate by ids in the fields (`scan_id`, `agency_id`). Adding a request-id middleware is a new behaviour; raise it as a founder question rather than slipping it into a feature.

Workers (`apps/workers`) declare structlog but had no logger calls at 2026-10-01. Celery retry behaviour lives in `apps/workers/src/avp_workers/celery_app.py`.

## 5. Add a new event, with a test

1. Emit it with ids only (section 3).
2. Test it with `structlog.testing.capture_logs()`, as `apps/api/tests/test_billing.py` does for the second-subscription event:

```python
with structlog.testing.capture_logs() as events:
    resp = await _deliver(client, payload)
warned = [e for e in events if e.get("log_level") == "warning" and e["event"] == "my.new.event"]
assert len(warned) == 1, events
assert warned[0]["agency_id"] == agency_id
blob = json.dumps(warned[0], default=str)
assert "@" not in blob
assert SENTINEL_TOKEN not in blob  # a known sentinel, as test_secrets_stay_out_of_logs.py:36-37
```

3. Also test the negative: the same path without the trigger emits no warning (see `test_the_same_subscription_id_does_not_warn`).
4. If the path touches a token, email or key, extend the guard `apps/api/tests/test_secrets_stay_out_of_logs.py`. It searches `capture_logs` output, `capsys` stdout and `caplog` text for sentinel secrets. Note inside `capture_logs` structlog renders nothing (it drops the event), so neither it nor `capsys` sees a structlog traceback; that test wraps its run in `capture_logs`, so no structlog traceback is checked anywhere, and only stdlib tracebacks reach `caplog.text`. To test a structlog traceback, emit it outside `capture_logs` and read `capsys`. Do not claim the guard proves a traceback is clean.
5. Negative control: change the field to something forbidden (for example the raw email), see the test fail, restore the file with `cp` from a saved copy and `cmp`.
6. If the event is something the founder should look for on Render, add its name to `docs/runbook.md` via the scribe agent.

Run, never piped:

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( case "$LOG" in /*) ;; *) echo "LOG must be absolute" >&2; exit 1;; esac; cd apps/api || exit 1; rc=0; uv run pytest -q -p no:cacheprovider tests/test_billing.py tests/test_secrets_stay_out_of_logs.py > "$LOG/obs.log" 2>&1 || rc=$?; echo "pytest rc=$rc" )
```
Then read `$LOG/obs.log`, run `cd apps/api && uv run ruff check src tests` to a file the same way, and assert the floor with `python3 infra/ci/assert_count.py api-pytest <log>` on a full run. Bump `infra/ci/floors.json` when tests are added. API tests need Postgres `avp_test` on 55433 and Redis db 15.

## 6. Metrics, tracing, alerting: not without the founder

Rate/error/duration metrics, distributed tracing and paging are good practice in general, but every vendor or SDK is a new runtime dependency and usually a new outbound host. Under `CLAUDE.md` that means: classify it in `apps/web/src/components/legal/providers.test.ts`, name it on `/privacy`, pass `apps/api/scripts/license_audit.py` (and `js_license_audit.py` for JS), and re-lock both `apps/api` and `apps/workers` with `uv lock`. This skill does not propose a vendor. If a change seems to need one, stop and return the question (what to measure, which vendor, what data leaves the system). Until then, `GET /api/v1/health` and `GET /api/v1/ready` (`routers/health.py`) and Render's log stream are the signals.

## Red flags

- An f-string or `%s` message where fields belong
- An email, token, key, link, or exception text in a field
- Model output, page text or competitor copy logged "just for debugging"
- A log line with no test
- A new logging, metrics or tracing package in `pyproject.toml` or `package.json`
- `logger.exception(...)` or `exc_info=True` on a path that touches the database, tokens or emails (the traceback prints `str(exc)`)
- Renaming an event that `docs/runbook.md` names
