---
name: redis-patterns
description: Reviews or designs Redis use in AVP's API and workers (apps/api sessions, rate limits, Google sign-in tickets, the Celery broker and result backend). Use when adding or changing a Redis key, Lua script, TTL, pool or timeout, or when a Redis outage or eviction question comes up. Not for Postgres, Stripe idempotency, or general caching ideas.
---

# Redis patterns for AVP

Redis is on Render Key Value (`infra/deploy/README.md:29`). It holds sessions, rate-limit counters, Google sign-in state and tickets, and the Celery broker and result backend. There is no application cache today. Facts only: store ids, digests, counts and timestamps, never AI answer text, page HTML or competitor copy.

Never print `REDIS_URL` (it can carry a password). Presence check only: `[ -n "${REDIS_URL:+x}" ] && echo set || echo unset`. Never read `apps/api/.env`.

## The client (`apps/api/src/avp_api/redis_client.py`)
- `redis.asyncio` only. A sync `redis.Redis` in an async route blocks the event loop.
- One shared `ConnectionPool.from_url(settings.redis_url, decode_responses=True, ...)`, `Redis(connection_pool=_pool)` per request, closed by `close_redis()`.
- Timeouts: `socket_connect_timeout` and `socket_timeout` from `redis_connect_timeout_seconds` / `redis_socket_timeout_seconds`, both 2.0 s (`config.py:80-88`). `health_check_interval=30` pings idle connections before reuse.
- **No retries.** A retried rate-limit `EVAL` could double-count (comment in `redis_client.py`). This holds only because the client is built on a `ConnectionPool`; a bare redis-py 6.4.0 `Redis(...)` retries 3 times by default (`asyncio/client.py:233`). A dead connection fails that request; the next opens a fresh one.
- Nothing on the API pool blocks on purpose (`config.py:81-86`): no `BLPOP`, `XREAD BLOCK`, pub/sub, or `KEYS`. A 2 s read timeout would cut a legitimate wait. Use `SCAN` for any enumeration; `FLUSHDB` only on test db 15. Never `FLUSHALL` (it wipes dbs 0-2 locally too).
- Do not add Cluster or Sentinel code: Render Key Value is a single instance.

## Failures map to status codes (`apps/api/src/avp_api/errors.py:288-305`)
- `redis.exceptions.ConnectionError` and `TimeoutError` -> 503 `/problems/service-unavailable` (fail closed), logging only the exception class (its text can carry the host).
- `AuthenticationError` / `AuthorizationError` subclass `ConnectionError` in redis-py but are re-raised: a rotated password is a config fault, a 500, not an outage. Keep that `isinstance` check when touching the handler. Exception: on login, sign-up and change-password the limiter's own `except (RedisError, OSError)` (`rate_limit.py:212`) catches an `AuthenticationError` first and answers 503, not 500.
- Other `RedisError`s (a Lua bug) are not mapped: they must surface.
- Code that catches Redis errors itself (`rate_limit.py`) catches `(RedisError, OSError)` and logs a fixed event name, never the exception text.

## Sessions (`apps/api/src/avp_api/sessions.py`)
- `sess:{sha256 digest}` holds the record as JSON (a plain `SET`, not a hash); only the digest is stored, never the cookie token. Its TTL is the idle window `session_idle_seconds` (72 h), refreshed by `current_principal` through `touch` at most once per `session_touch_interval_seconds` (1 h), and never past the session's absolute end: the stored `expires_at` or `created_at` plus the current `session_ttl_seconds` (14 days, also the cookie's `Max-Age`), whichever is first (A3 part 1).
- `user-sessions:{user_id}` is a SET of digests for bulk revoke; its TTL is set to `session_ttl_seconds` at each create (never by a refresh), so it outlives every session in it (digests of idle-expired sessions linger until then, harmlessly). `touch` writes with one `SET ... PX ... XX`, so a session revoked between its read and its write stays revoked: keep the `XX` (a GET-then-SET without it would resurrect the session, outside the index).
- **The orphaned-session race:** `revoke_all_for_user` does `SMEMBERS`, deletes those `sess:` keys, then `SREM key *digests` (only what it read), never `DEL` of the whole index. A session created between the read and the write stays indexed and is caught by the next revoke. Do not "simplify" this to deleting the set.
- Password change/reset revoke other sessions before and after the DB commit (see `docs/build-log.md`).

## Rate limits (`apps/api/src/avp_api/services/rate_limit.py`)
- Fixed window, one Lua script per decision so the count is atomic: `INCR`, `PEXPIRE` on the first hit, repair a key that lacks a TTL (`PTTL < 0`). The IP key is counted first and the email key only if the IP is within its limit, so a refused IP cannot fill Redis with keys for invented addresses.
- Keys are `rl:{action}:{kind}:{digest}`, digest = truncated HMAC-SHA256 keyed from `APP_SECRET`. **Never put a raw email, IP or token in a key**: keys show in `MONITOR`, `SCAN` output, dumps and logs.
- Login and change-password count failures only and give the attempt back on success (floor 0). Login, sign-up, change-password fail closed (503); reset-request fails open.
- Call scripts through `_eval` (`redis.eval`, awaited). With `decode_responses=True`, list results are integers as returned by the script.

## Google sign-in flow (`apps/api/src/avp_api/services/google_oauth.py`)
- `google-state:{digest}` and `google-ticket:{digest}`, both `SET ... EX google_sign_in_ttl_seconds`, keyed by digest of the token.
- One-shot reads use `GET` + `DEL` in one MULTI/EXEC pipeline (`_take`) so two racing callbacks cannot both win. `peek_ticket` reads without consuming.

## Celery (`apps/workers/src/avp_workers/celery_app.py`, `config.py:43-60`)
- Broker is db 1, result backend db 2 (derived from `REDIS_URL` by `_redis_db`); sessions and limits are db 0. Separation means flushing a wedged queue does not sign users out (`redis_client.py` docstring, `infra/deploy/README.md:47`). Databases on one instance share its memory and eviction policy; they are not isolation from eviction. `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` override the derived URLs (`apps/workers/src/avp_workers/config.py:58-59`), so they may point at other instances. Celery currently runs only `avp.health.*` (`apps/workers/src/avp_workers/tasks/health.py:14-27`); scans run in the API process via `BackgroundTasks` (`apps/api/src/avp_api/services/scan_executor.py:618-619`).
- Only `socket_connect_timeout` (2 s) on the broker: kombu's pub/sub listener idles on its socket by design and a read timeout would tear it down (`celery_app.py:60-66`). Do not add `socket_timeout` to the broker.
- Blocking commands are fine in kombu, never on the API pool.

## Tests
- API tests use Redis db 15 (`AVP_TEST_REDIS_URL`, default `redis://127.0.0.1:6379/15`, `apps/api/tests/conftest.py:8,39`); the fixture flushes that db. Never point tests at db 0-2. Run them as the verification bar says: redirect to `"$LOG/x.log"` (`LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"`), read the tool's own exit code, then `python3 infra/ci/assert_count.py api-pytest "$LOG/x.log"`. Never pipe.
- If Redis is down, say so; do not start or reconfigure it.

## Key naming and TTL rules
- Prefix by purpose, colon-separated: `sess:`, `user-sessions:`, `rl:`, `google-state:`, `google-ticket:`. New features get a new prefix.
- Identify subjects by digest (SHA-256 for bearer tokens, keyed HMAC for emails/IPs), never the raw value.
- Every key gets a TTL at creation (`SET ... EX`, or `PEXPIRE` inside the same Lua). Index sets get a TTL at least as long as their members. Match limiter TTL to the window.
- Values are small JSON of ids, timestamps, counts. No blobs, no third-party text.
- Prefer atomic units: Lua for read-modify-write, MULTI/EXEC pipeline for get-and-delete. No distributed lock exists today; add one only with a brief, using `SET NX PX` with a random token and a compare-and-delete release script, TTL longer than the job.

## Eviction: a founder/infra decision, not a code change
The repo does not set a `maxmemory-policy` anywhere (searched `docs`, `infra`, `apps/*/src`). The policy lives in the Render Key Value settings and is unverified here. Whether sessions, limits and the broker share one instance is also unverified: `infra/deploy/README.md:47` allows one instance or three, and the choice is in the Render dashboard. Do not change it yourself. The trade-off to put to the founder:
- **Most dangerous: losing the session index.** Under `allkeys-lru` or any `volatile-*` policy (the index has a TTL), Redis can evict `user-sessions:{uid}`. Only create, revoke and revoke-all touch it, while each `sess:` key is read on every request (`deps.py:119`), so LRU picks the index first. Then `revoke_all_for_user` finds nothing and returns 0 (`sessions.py:124-126`), and password change, reset and logout-all (`routers/auth.py:191,266,284,404,406`) sign nobody out. A stolen cookie keeps working for up to 14 days (`session_ttl_seconds`). Seat removal stays safe because `current_principal` checks `deleted_at`. The durable fix, `users.sessions_valid_after` checked in `current_principal` (`routers/auth.py:282`), is not built.
- Under `allkeys-lru` / `volatile-*`, limiter counters can also be evicted, which silently resets limits, and live sessions can be evicted, which signs users out.
- Queued Celery tasks are not the main risk today: scans do not use Celery, and kombu queue lists carry no TTL, so `volatile-*` would evict the TTL'd auth keys instead.
- `noeviction` turns memory pressure into write errors but loses nothing silently; `DEL` and `SREM` still run, so revocation keeps working. Those write errors are `OutOfMemoryError` (a `ResponseError`), which `errors.py:288-289` does not map: Google `begin` / `issue_ticket` and session `SET` return 500, and only the limiter (`rate_limit.py:212`) returns 503.
- Recommendation for the founder's decision: `noeviction` plus memory alerting, if Render Key Value offers it (unverified). A separate cache instance is only needed if an application cache is introduced; none exists.
