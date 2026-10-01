---
name: error-handling
description: "Applies when a change adds or reviews error paths in AVP: a new ProblemError subclass or /problems/<slug> in apps/api, a retry, a fail-open or fail-closed decision, what gets logged on failure, or how apps/web shows an ApiProblem or renders an error route. Does not apply to generic Express/Node/Go error patterns, and not for adding an error-tracking service (a founder decision)."
---

# Error handling in AVP

Facts were checked against the repo on 2026-10-01. Re-read the named file before relying on a detail.

## 1. One error shape: RFC 9457 problem+json

- Every API error leaves as `application/problem+json` with `{ type, title, status, detail, instance }` plus extension members. `apps/api/src/avp_api/errors.py` owns it. Do not invent an envelope such as `{ error: { code, message } }`: it would churn OpenAPI, `packages/shared-types` and `apps/web/src/lib/api.ts`.
- To add an error, subclass `ProblemError` with `status_code`, `problem_type` (a slug, served as `/problems/<slug>`) and `title`. Raise it; `register_error_handlers` renders it. Do not build a `JSONResponse` in a route.
- Prefer a specific slug over `conflict` when a client must branch on it (see `CheckoutInProgress`, `SubscriptionAlreadyActive`, `SeatLimitReached`). Machine-readable extras go in `**extra` (`scansUsed`, `scanLimit`, `errors[]`), not in `detail` prose.
- Enumeration-safe failures stay blurred: `InvalidResetToken`, `InvalidInvitation` and `InvalidGoogleTicket` are one class and one 400 for every cause, so neither status nor `type` tells a prober which guess was close. Do not split them. `InvalidWebhookSignature` is deliberately plain, because only Stripe calls it. Decide which case you are in and write the reason in the docstring.
- Config-missing is 503, not 500 (`BillingNotConfigured`, `GoogleSignInNotConfigured`). A missing secret refuses; it never falls back to trusting unverified input.
- New or changed slug: add it to the problem table in `docs/api-contracts.md` (the scribe does that; list it in your report), then regenerate OpenAPI and shared-types (commands in CLAUDE.md).
- Check the handlers before assuming coverage: `ProblemError`, Redis `ConnectionError`/`TimeoutError`, `RequestValidationError` and Starlette `HTTPException` are mapped. There is no catch-all `Exception` handler in `errors.py`, so an unexpected exception takes the framework's default 500 (not problem+json). Adding a catch-all is a behaviour change: propose it, do not slip it in.
- Redis outage: the handler maps connection and timeout errors to 503 `/problems/service-unavailable` with a fixed detail. `AuthenticationError` and `AuthorizationError` subclass `ConnectionError` in redis-py and are re-raised on purpose: a rotated password is a config fault, not an outage.

## 2. Fail closed on security paths, open only where documented

- Login, sign-up and change-password rate limits fail closed: Redis down gives 503, because failing open would switch the guard off exactly when it is needed (`apps/api/src/avp_api/services/rate_limit.py`, top docstring). Note that `rate_limit.enforce` (about line 212) catches every `RedisError`/`OSError`, so a rotated Redis password or a Lua `ResponseError` is also a 503 on login, sign-up and change-password, and a silent fail-open on reset-request. That differs from the app-wide handler, which re-raises auth errors.
- Sessions live in Redis (`apps/api/src/avp_api/sessions.py`, wired in `deps.py`); a Redis error there reaches the app-wide handler and becomes the 503. Never catch it and "treat as signed in".
- The only documented fail-open is reset-request, whose handler has no other Redis dependency (its limiter does use Redis, and skips itself on a Redis error) and which replies identically either way. A new fail-open needs the same written reason in the docstring and a founder decision, because it is a user-visible behaviour.
- Never write `except Exception: pass`. Each `except` handles, re-raises as a `ProblemError`, or logs with the class name.

## 3. What goes in an error message or log line

- Facts only: an error or log line carries ids, counts, status codes and exception class names, never third-party raw text (AI answer text, page HTML, competitor copy, model reasoning). `apps/api/tests/test_facts_only.py` guards storage; apply the same rule to messages.
- No email, secret, token or URL with credentials in a log or a `detail`. Log a user or agency id, not an address. Redis and HTTP client exception text can carry a host or URL, so log `error=type(exc).__name__` (as the Redis handler does) rather than `str(exc)`.
- `logger.exception(...)` and `exc_info=True` print `str(exc)` plus a traceback, and SQLAlchemy errors include `[parameters: (...)]` (`apps/api/src/avp_api/db.py` sets no `hide_parameters`). So use neither where emails, tokens or URLs are in scope; log `error=type(exc).__name__`. Open item for the founder, a separate code fix and not part of this skill: `hide_parameters=True` on the engine, and review existing calls such as `routers/auth.py:581-582`.
- Use structlog (`structlog.get_logger(__name__)`) with keyword fields and a dotted event name, e.g. `logger.warning("redis.unavailable", error=type(exc).__name__)`. `apps/api/src/avp_api/logs.py` sets the level and redacts secret-bearing request paths (legacy share tokens, Google `code`/`state`/`ticket`). It does not scrub arbitrary event fields, so the caller is responsible for what it passes.
- Keep secrets out of URLs so they cannot reach an access log: bodies and URL fragments, `x-goog-api-key`-style headers rather than `?key=`.
- Check your change with `apps/api/tests/test_secrets_stay_out_of_logs.py`; add a case there for a new path that touches a token or an address.
- Web: `apps/web` logs nothing. Do not add `console.log` of an error object, a response body or a user field.

## 4. Retries: an allow-list, not a default

- Never `retryIf = () => true`. Retry only operations that are idempotent or provably safe, and only on transient causes: connection or timeout errors, 429, 502/503/504. Never 4xx other than 429, never auth failures, never a validation error.
- Honour `Retry-After` when the vendor sends it, inside a hard ceiling, and record the call as failed if the wait would exceed it. The working example is the Perplexity adapter in `apps/api/src/avp_api/services/engines.py` (`PERPLEXITY_RETRY_AFTER_MAX`, one retry, inside `ENGINE_CALL_CEILING`).
- Backoff always has jitter, so concurrent tasks do not retry in lockstep. Celery: `apps/workers/src/avp_workers/celery_app.py` (`task_max_retries=3`, `task_retry_backoff`, `task_retry_jitter`). Late acks mean tasks must be idempotent; do not add a task that is not.
- Stripe: the client has a 15 s timeout and one retry. Checkout creation is not blindly retryable: it is guarded by a row lock and a check for a live subscription. Do not wrap billing calls in a generic retry.
- The webhook (`POST /billing/webhook`) relies on Stripe's own retries and the `stripe_webhook_events` idempotency table. Return a non-2xx only when you want Stripe to retry.
- Every outbound call has an explicit timeout. A retry that can exceed the caller's overall budget is a bug.
- Do not add a retry or circuit-breaker library. A new dependency is a founder decision plus `providers.test.ts`, `/privacy` and `apps/api/scripts/license_audit.py`.

## 5. Web: what the user sees

- `apps/web/src/lib/api.ts` throws `ApiProblem` for every non-2xx, for JSON and binary calls alike. An unhandled 500 comes from Starlette's `ServerErrorMiddleware`, which sits outside `CORSMiddleware`, so that response probably lacks CORS headers and the browser's `fetch` would throw a `TypeError`, not an `ApiProblem` (inferred, untested). Callers must therefore handle non-`ApiProblem` errors too. Branch on `err instanceof ApiProblem` and `err.type` (or `err.status`), not on message text. `err.fieldErrors()` maps a validation problem to `{ field: message }`.
- Show failures with the design-system `ErrorState` (title as a statement, never a bare status code; `detail` often the API's own detail; `code` small and last; an `action` when there is a way out). Callers choose the copy, because a 404 can mean "not yours" or "withdrawn".
- Copy for a new user-visible error is a founder decision. Do not rewrite `/privacy`, `/terms` or `/crawler`.
- Next 15 error routes: at the last check there was NO `error.tsx`, `global-error.tsx` or `not-found.tsx` anywhere under `apps/web/src/app`, so a render error or an unknown URL gets the framework default. Adding them is a user-visible change: propose them. If approved, they use `ErrorState`, tokens and `@avp/design-system` only (no stock colours, no raw hex), `error.tsx` and `global-error.tsx` are client components (`'use client'`, `reset` prop), and they must not print `error.message` or a stack, which can carry server detail. Show `error.digest` at most, as the quotable code. Token routes keep `Referrer-Policy: no-referrer`; a new error page must not break `apps/web/src/app/referrerPolicy.test.ts`.
- Do not hand-roll a class `ErrorBoundary`; the App Router error files are the boundary.

## 6. Tests

- New slug or handler: a test in `apps/api/tests` asserting status, `type`, `content-type` and the extension members. A fail-closed path: a test that simulates the Redis failure and expects 503, as `tests/test_redis_timeouts.py` and `tests/test_password_change_revocation_order.py:71` do. Never stop the shared dev Redis. Web: a vitest for the `ApiProblem` branch.
- Counts come from `infra/ci/assert_count.py`, not coverage percentages; bump the floor in `infra/ci/floors.json` in the same change.
- Never pipe test or lint output. Redirect to a file, read the tool's own exit code, then read the file:

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( cd apps/api || exit 1; rc=0; uv run pytest -q -p no:cacheprovider tests/test_x.py > "$LOG/x.log" 2>&1 || rc=$?; echo "pytest rc=$rc" )
```

## Checklist

- [ ] Errors are `ProblemError` subclasses with a documented `/problems/<slug>`; no second envelope
- [ ] Token/reset/invite-style failures stay one blurred class
- [ ] Security paths fail closed; any fail-open is documented and approved
- [ ] Logs carry ids and exception class names; no email, token, secret, credentialed URL or third-party text
- [ ] Retries: allow-list of safe operations and causes, jitter, timeout, `Retry-After` inside a ceiling
- [ ] Web branches on `ApiProblem.type`, renders `ErrorState`, no raw message or stack shown
- [ ] No new dependency or outbound host
