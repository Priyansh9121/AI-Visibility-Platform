# Changes from upstream (skills/redis-patterns/SKILL.md)

- Replaced the sync `redis.Redis(host='localhost', ...)` examples (:46, :278-292) with AVP's real `redis.asyncio` pool, 2 s timeouts, no retries -> removes a sync client blocking the event loop; retried rate-limit EVAL double-count.
- Removed cache-aside, write-through, tag invalidation, stampede sections (:40-91, :343-369) -> AVP has no app cache; they used a psycopg-style `db.query("... %s")` (:55) and a sync client.
- Replaced generic Session Storage (:93-117) with AVP's digest-keyed `sess:`/`user-sessions:` design and the SREM-only-what-was-read revoke rule -> upstream stored raw session ids and had no revoke race handling.
- Replaced fixed/sliding-window examples (:119-164) with AVP's Lua limiter and HMAC-hashed keys -> upstream keyed on raw ids; raw emails/IPs in keys are banned.
- Shortened Distributed Locks (:166-199) to a rule; dropped the `redlock-py` pointer (:199) -> no lock exists; no new dependency.
- Removed Pub/Sub and Streams examples (:201-241), incl. `xreadgroup(block=2000)` -> blocking commands must not run on the API pool; Celery/kombu owns the broker.
- Removed Cluster and Sentinel sections (:294-317) -> Render Key Value is a single instance; audit verdict says drop.
- Replaced the eviction table and "maxmemory-policy allkeys-lru" (:319-329) with a founder/infra decision note -> allkeys-lru or volatile-* can evict the session index, sessions and limiter counters (Celery holds only health tasks today).
- Replaced TTL table and naming conventions (:243-272) with AVP's actual prefixes and TTL rules.
- Added error mapping (503 / AuthenticationError 500), Google one-shot ticket take, Celery db split with connect-only timeout, test db 15, never-print-REDIS_URL -> AVP-specific and secret safety.
- Removed Examples, Quick Reference, Related (:371-404) -> generic; referenced skills (postgres-patterns, backend-patterns, database-migrations, django-patterns, database-reviewer) do not exist here.
- New frontmatter description, dropped `metadata.origin` (:3-5) -> narrow trigger naming AVP surfaces.
- Test commands follow the no-pipe / assert_count rules.

## Security review fixes (2026-10-01)
- Eviction section rewritten; leads with eviction of `user-sessions:{uid}` making revoke-all a no-op (`sessions.py:124-126`, `deps.py:119`) -> a stolen cookie would survive password change/reset for up to 14 days; cites unbuilt `sessions_valid_after` (`routers/auth.py:282`).
- Removed "queued Celery tasks ... scans lost" -> scans run via `BackgroundTasks` (`scan_executor.py:618-619`), Celery has only `avp.health.*`, kombu lists have no TTL.
- Noted `noeviction` OOM is an unmapped `OutOfMemoryError` (500 except limiter 503) -> `errors.py:288-289`, `rate_limit.py:212`.
- "Share one instance" downgraded to unverified; added the `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` overrides -> `infra/deploy/README.md:47`, workers `config.py:58-59`.
- Noted `touch` has no caller and its GET-then-SET can resurrect a revoked session; use `set(..., xx=True)` -> `sessions.py:139-153`.
- Noted limiter's `except RedisError` turns `AuthenticationError` into 503 on login/sign-up/change-password -> `rate_limit.py:212`.
- `FLUSHDB` on db 15 only; `FLUSHALL` banned; errors.py citation corrected to 288-305.
- Dropped the citation of untracked `docs/skills-audit-2026-09-30/`.
- Added the ConnectionPool-only no-retry caveat (redis-py `asyncio/client.py:233`), full paths for health.py and scan_executor.py, and marked memory alerting as unverified on Render.
