---
name: avp-security-review
description: AVP-specific security checklist for reviewing or writing code in apps/api (FastAPI routers, auth, sessions, rate limits, Stripe billing webhook, crawler), apps/workers and apps/web (headers, token routes). Use it alongside the built-in security-review, which is generic; use this one when the question is whether an AVP change keeps tenancy, billing, crawling and logging safe. Not for pure UI styling, performance work or legal-page copy.
---

# AVP security review

The built-in `security-review` covers security in general. This skill is the AVP checklist: it names AVP's real files and the defects AVP's own reviews found in the week to 2026-10-01. Run it on a diff, then hand auth/session/billing/tenancy/logging diffs to the `security-reviewer` agent (`.claude/agents/`). Report findings; do not fix unrelated issues inside the same change.

Stack: FastAPI, Pydantic v2, async SQLAlchemy 2 / asyncpg, Celery workers, Postgres (Supabase in production), Redis, Next 15 App Router, hosted on Render. Render auto-deploys `main` before CI finishes: never push to `main` without the founder's explicit OK.

Line numbers and "not present" claims below were checked read-only on 2026-10-01. Re-check against the tree before relying on them.

## 1. Secrets

- [ ] No key, token or password in source, tests, docs or fixtures. Keys come from settings (`apps/api/src/avp_api/config.py`), set on Render, never in the repo.
- [ ] Stripe live keys are the founder's to set on Render. Never in the repo, never pasted in chat.
- [ ] Check a variable by presence only: `[ -n "${VAR:+x}" ] && echo "VAR: set" || echo "VAR: unset"`. Never `env | grep`, `echo $VAR`, `printenv`, `set -x`, `print(settings)`.
- [ ] Never open `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `dump.rdb`, `.devdata/`.
- [ ] Missing required secret fails closed (see Stripe, section 7), not "log and continue". An unset `RESEND_API_KEY` logging instead of sending is a documented dev mode; do not copy that shape for anything that authorises.

## 2. Input validation (Pydantic v2)

Validate at the boundary with a model; `Annotated` constraints, no `...` defaults.

```python
from typing import Annotated
from pydantic import BaseModel, EmailStr, Field, HttpUrl

class CreateClient(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    domain: Annotated[str, Field(min_length=3, max_length=253)]
```

- [ ] Every request body, query and path param is a typed model or `Annotated[..., Query/Path(...)]` with bounds.
- [ ] Whitelist (enums, `Literal`) rather than blacklist.
- [ ] Response models filter output; never return an ORM row or a dict that might carry a hash, token or another tenant's field.
- [ ] Error bodies are RFC 7807 problems from `apps/api/src/avp_api/errors.py`: no stack, no SQL, no "that email exists" on login or reset paths.
- [ ] File uploads (if any are added): size cap, content-type and extension whitelist, never trust the client filename.

## 3. SQL (async SQLAlchemy 2)

```python
# Safe: bound parameters, tenant predicate in the query
stmt = select(Client).where(Client.agency_id == principal.agency.id, Client.id == client_id)
row = (await session.execute(stmt)).scalar_one_or_none()

# Raw SQL is still bound, never formatted
await session.execute(text("SELECT id FROM clients WHERE domain = :domain"), {"domain": domain})
```

- [ ] No f-string, `%` or `.format` building SQL. `text()` only with named bound params.
- [ ] Every query on tenant data filters by the caller's agency (section 4); a lookup by id alone is a leak.
- [ ] Schema changes go through Alembic (`infra/db/migrations/versions/`). Use `supabase-postgres-best-practices` for indexes and RLS questions. AVP's API connects with its own role and enforces tenancy in code; do not assume Supabase RLS protects an API query.

## 4. Authentication, sessions and tenancy

Sessions are opaque tokens in Redis (`apps/api/src/avp_api/sessions.py`), passwords are Argon2id (`security.py`).

- [ ] **Tenancy first.** Any route with an agency id in the path calls `assert_own_agency(principal.agency.id, agency_id)` (`deps.py`) before it reads or reveals state. It answers 404, not 403: 403 confirms the agency exists. A second copy of this rule is a chance to relax it; reuse the one helper.
- [ ] Child resources (clients, scans, reports, prompt runs) are looked up through the agency, not by bare id.
- [ ] Role gates use `require_role(...)`; check the role before the side effect.
- [ ] **Rate limit before the expensive step.** Login, sign-up, reset and change-password are limited through `services/rate_limit.py` and the limiter runs BEFORE Argon2 verification or any provider call. A limiter after the hash is a CPU denial-of-service. Failed attempts should also be counted, successful ones released.
- [ ] **Whose IP is trusted?** The per-IP bucket keys on the client address. `apps/api/docker-entrypoint.sh:18` runs uvicorn with `--forwarded-allow-ips='*'`, and `CLIENT_IP_HEADER` defaults to empty (`config.py:133-144`), so the key is the leftmost `X-Forwarded-For` entry, which may be client-controlled depending on whether Render replaces or appends the header; the runbook probe (`docs/runbook.md:118-160`) decides. If it is client-controlled, rotating that header sidesteps the per-IP limit. Never set `CLIENT_IP_HEADER` to `X-Forwarded-For`; `CF-Connecting-IP` only after confirming every request arrives through Cloudflare (`docs/runbook.md:151` has the probe). Check any new limiter for the same question.
- [ ] **Session revocation around password changes.** Password change and reset revoke the user's other sessions, before and after the commit, so a session created between the two is caught. `revoke_all_for_user` must remove only what it read (no blind delete of a session created mid-flight).
- [ ] Login failure is indistinguishable for unknown user and wrong password (dummy hash, same timing path).
- [ ] Constant-time comparison for any token or signature comparison (`hmac.compare_digest`).
- [ ] `Host` and `Origin` headers are attacker-controlled: never build a redirect, checkout or reset URL from them. Use configured settings.
- [ ] CORS origins come from `settings.cors_allow_origins`, an explicit list; never `*` with credentials.
- [ ] CSRF: the session is a cookie `avp_session` (`config.py:96`), `HttpOnly` (`routers/auth.py:66`), `Secure` forced when deployed (`config.py:345`), `SameSite` default `lax` (`config.py:98`), read at `deps.py:115`. No Origin or CSRF-token check exists in `apps/api` (`config.py:93` mentions CSRF tokens; none exist). Open item: confirm the production `SameSite` value on Render; if it is `none`, body-less state-changing POST routes can be forged cross-site. Do not add a cookie-authenticated mutation without an Origin check.

## 5. Redis failure is a 503, not a crash or a leak

- [ ] A Redis connection or timeout error on a request path becomes `503 /problems/service-unavailable` (`errors.py`), not a 500 with a traceback and not a silent allow. The Redis pool has 2 s connect/read timeouts; keep them.
- [ ] **No Redis URL in any log line or error body.** A connection string carries a password. Log the exception type, not `str(exc)` or `repr(exc)` when it embeds the URL.
- [ ] Login, sign-up and change-password limiters fail closed on Redis loss: the response is `503 /problems/service-unavailable` (`errors.py:262`); `auth.rate_limit.unavailable` is the log event (`rate_limit.py:213`). The exception is reset-request, which fails open by design: it does not use Redis and answers identically either way (`services/rate_limit.py:22-24`, `:212-215`). Name any new exception and say why; session reads fail closed too.

## 6. Logging and sensitive data

The source skill taught logging `{ email, userId }` as the correct form. AVP inverts that: **log ids, never emails.**

```python
# WRONG: identifiers that are secrets or personal data
logger.info("login", email=email, password=password)
logger.info("login", email=email, user_id=user_id)   # email is still a leak
logger.info("reset", token=token)

# RIGHT: opaque ids and outcomes only
logger.info("auth.login.ok", user_id=user_id, agency_id=agency_id)
logger.warning("stripe.webhook.rejected", reason=type(exc).__name__)
```

- [ ] No email, password, session token, reset/invite/share token, Stripe secret, Redis URL, `Authorization` header or full request body in a log line. `apps/api/src/avp_api/logs.py` redacts, but redaction is the last line of defence, not the plan.
- [ ] `apps/api/tests/test_secrets_stay_out_of_logs.py` covers the new path; add a case when a new field could carry one.
- [ ] Tokens in a URL end up in access logs and Referer headers. Render's request log records paths and query strings: a credential in either is in a log you do not control.
- [ ] Web logs nothing sensitive either; no `console.log` of user objects.

## 7. Stripe: webhook signature and idempotency

`POST /billing/webhook` has no session; its only authentication is the signature (`routers/billing.py`, `services/billing.py`).

- [ ] Verify with `stripe.Webhook.construct_event(payload, signature, secret)` via `verify_webhook`. Never hand-roll HMAC, never parse the body before verifying, never read `request.json()` first: verify the raw bytes.
- [ ] **Fail closed.** Missing header, bad signature, malformed body AND an unset `STRIPE_WEBHOOK_SECRET` all refuse. No "verification is optional in this environment" branch.
- [ ] The SDK's replay tolerance (default 300 s) stays on; do not pass a huge tolerance.
- [ ] **Idempotent.** Stripe delivers at least once, out of order. The event id is inserted into `stripe_webhook_events` with `INSERT ... ON CONFLICT (id) DO NOTHING` in the SAME transaction as the state change (`apply_event`): a handler that raises rolls the ledger row back so Stripe's retry is processed; concurrent duplicates serialise on the primary key. Duplicate and stale events are acknowledged (2xx) and not re-applied.
- [ ] Stale check: an older `event.created` than the agency's stored state does not overwrite it (a late `updated: active` after `deleted` must not revive a cancelled agency).
- [ ] The ledger is facts-only: event id, type, Stripe timestamp, agency id, outcome code. No customer, email or amount.
- [ ] Checkout (`services/billing.py`): refuse when a subscription is live (409), lock the agency row with a bounded `lock_timeout`, bounded Stripe client timeout and retry. Success/cancel URLs come from settings, not from request headers.
- [ ] Never mark an agency subscribed from a browser redirect; only the signed webhook does.

## 8. SSRF through crawler-supplied URLs

AVP fetches URLs a user chose: a client's domain, links found on its pages, `/sitemap.xml`, redirects (`services/crawl.py`, `technical_audit.py`). A user can point these at `localhost`, a Render private-network service, or a cloud metadata address.

State as of 2026-10-01 (read-only check): `RobotsGate` in `crawl.py` caps redirects at 5 and checks robots.txt before each page hop, but nothing checks that an address is public, and intake rejects only IP literals (`crawl.py:309-323`). Treat "no private-address guard" as an open finding to raise, not as a pass. Any change that adds or widens a fetch must show the checks below, and must not claim SSRF is closed by an application-level IP check alone, because these paths bypass one:

- The robots.txt fetch follows redirects automatically with no per-hop check (`crawl.py:183`).
- Chromium resolves DNS itself and follows its own redirects, JS navigations and subresources (`crawl.py:488`, `technical_audit.py:384-395`); only images and fonts are aborted (`crawl.py:408-411`).
- `gate.allows(page.url)` runs after the load, so the request has already been made (`crawl.py:426`, `technical_audit.py:403`).
- No IPv6 handling anywhere; and DNS rebinding defeats any resolve-then-fetch check.
- Chromium also launches with `--no-sandbox` (`crawl.py:398`, `technical_audit.py:331`), which removes Chromium's sandbox, so a renderer exploit becomes code execution in the API container.

The fix to recommend is a network-level egress guard: an egress proxy that refuses non-global IPs, passed to `chromium.launch(proxy=...)` and used by httpx too. That is a founder/infra decision, not a code tweak in the skill's scope.

Application-level checks worth having in addition (defence in depth, not the fix):

- [ ] Scheme is `http` or `https` only; reject `file:`, `gopher:` and the rest.
- [ ] Port is 80/443 (or an explicit allow-list); no userinfo (`user@host`).
- [ ] The hostname is resolved, and EVERY resolved address is refused if loopback, private (RFC 1918), link-local (169.254.0.0/16 incl. 169.254.169.254), unique-local (fc00::/7), multicast or unspecified. Python: `ipaddress.ip_address(a).is_global` as the allow test. Connect to the validated IP (or re-validate at connect) so DNS rebinding between check and use cannot swap the address.
- [ ] Every redirect hop the code follows by hand is re-validated with the same rules. This covers httpx hops only, not the four paths above.
- [ ] Response size and time are bounded; only facts are stored (status, counts, types, URLs). Never store page HTML or text.
- [ ] Redirect targets and link URLs from a page are data, not commands: they never choose a scheme or host the user's own domain did not.
- [ ] Honest crawling stays: `AVPScanBot` user agent, robots.txt obeyed, no stealth or spoofed UA. Security hardening must not add evasion.

## 9. Web headers (`apps/web/next.config.mjs`)

As of 2026-10-01 `next.config.mjs` sets `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex, nofollow` on token routes only; I found none of HSTS, CSP, nosniff or frame options anywhere in `apps/web` or the API. Whether Render or a proxy adds them is unverified. Treat the list below as the launch target; adding CSP changes how the page loads, so a rollout choice (report-only first) is the founder's call.

```js
// inside async headers(): one catch-all entry, alongside the token-route entry
{
  source: '/:path*',
  headers: [
    { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains' },
    { key: 'X-Content-Type-Options', value: 'nosniff' },
    { key: 'X-Frame-Options', value: 'DENY' },            // legacy twin of frame-ancestors
    { key: 'Content-Security-Policy', value: "default-src 'self'; frame-ancestors 'none'; ..." },
  ],
},
```

- [ ] HSTS: only once every subdomain is HTTPS; add `preload` only with the founder's OK (hard to undo).
- [ ] `X-Content-Type-Options: nosniff`.
- [ ] Clickjacking: `frame-ancestors 'none'` in CSP, plus `X-Frame-Options: DENY`. If the shared report is ever meant to be embeddable, that is a product decision: ask.
- [ ] CSP: no `unsafe-eval`; `unsafe-inline` for scripts only if Next's inline bootstrap forces it, and then prefer nonces. List each outbound host the page really uses (API origin, fonts). Every new outbound host must also be classified in `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`.
- [ ] Next merges matching `headers()` entries; confirm the catch-all does not override the token-route `Referrer-Policy`/`X-Robots-Tag`, and that `apps/web/src/app/referrerPolicy.test.ts` still passes.
- [ ] XSS: no `dangerouslySetInnerHTML` with user or third-party text. React escapes by default. Stored third-party raw text is also barred by the facts-only rule.

## 10. Token routes

Share, reset-password, invite and Google sign-up links are credentials.

- [ ] A new credential travels in the URL fragment (`#...`), never in a path or query: fragments are not sent to servers, so they miss the web host's request log and Referer. Share, invite and reset links are already minted as fragments (`services/links.py:14-28`, since d2fe29a) and redeemed from POST bodies (`routers/report.py:315`, reset confirm, invitation accept); the `[token]` routes only forward to the fragment form (`apps/web/src/components/routes/FragmentRedirect.tsx:21-23`). Real residual: a legacy path link sent before d2fe29a puts its token in the web host's request log once, until the token's TTL expires. The header mitigations below cover the rest.
- [ ] Every token route sends `Referrer-Policy: no-referrer` AND `X-Robots-Tag: noindex, nofollow` from the one list in `next.config.mjs` (so the two cannot drift). A new token route is added to that list in the same change.
- [ ] Links out of those pages carry `rel="noreferrer"`.
- [ ] Tokens are random, single-purpose, expiring where they grant a change, stored hashed where feasible, and revocable.
- [ ] `apps/web/src/app/robots.ts` keeps token paths out; do not rely on robots.txt as access control.
- [ ] A Google sign-up ticket is minted into the fragment (`routers/auth.py:626`) and read from the JSON body of `POST /auth/google/pending` (`auth.py:636,648`, read without consuming) and `POST /auth/google/complete` (`auth.py:660,678`, consumes it). The API never accepts the ticket in a query (GET is 405); the web app's one-release legacy read of `?ticket=` clears it from the address bar (`apps/web/src/lib/googleTicket.ts:40-44`).

## 11. Dependencies and licences

- [ ] No new runtime dependency or outbound host without saying so. Classify it in `providers.test.ts`, name it on `/privacy`, and pass `apps/api/scripts/license_audit.py`. No copyleft, source-available or undeclared licences; psycopg, non-Anthropic LLM SDKs and PDF libraries are deliberately absent.
- [ ] After any `pyproject.toml` change: `uv lock` in BOTH `apps/api` and `apps/workers`, then `uv lock --check` in both.
- [ ] JS via `corepack pnpm` only; lockfile committed.
- [ ] Vulnerability scan if a tool is available; do not install one as part of the review.

## 12. Verification

Counts, not only exit codes. Use the project gates (`infra/ci/floors.json`, `infra/ci/assert_count.py`); do not invent a coverage percentage. Never pipe test output. Write a log outside the repo:

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
( cd apps/api && rc=0; uv run pytest -q -p no:cacheprovider tests/test_secrets_stay_out_of_logs.py > "$LOG/sec.log" 2>&1 || rc=$?; echo "pytest exit: $rc" )
( cd apps/api && uv run ruff check src tests > "$LOG/ruff.log" 2>&1; echo "ruff exit: $?" )
```

Then read the log files. Add a test for each behaviour you changed; where it proves something, break the code, watch the test fail, and restore the file with `cp` from a backup in `$LOG` followed by `cmp` (no `git checkout`, `restore` or `stash`). API tests need Postgres `avp_test` on port 55433 and Redis db 15; if down, say so.

## Pre-merge checklist

- [ ] Tenancy: `assert_own_agency` before any state is read or revealed; 404 not 403
- [ ] Rate limit before Argon2 or provider calls; fails closed on Redis loss (503) except reset-request; per-IP key not client-controlled
- [ ] Password change/reset revoke other sessions before and after commit
- [ ] No secret, token, email or Redis URL in a log line; test covers it
- [ ] Stripe: signed, raw body, fails closed, ledger row in same transaction
- [ ] Crawler fetches: scheme, public-IP and per-hop checks; facts only stored
- [ ] Web headers as in section 9; token routes noindex + no-referrer; new credentials in fragments
- [ ] Dependencies and hosts classified; both uv locks refreshed
- [ ] Facts-only: no third-party raw text stored or republished
- [ ] Gates run with counts recorded; nothing pushed to `main` without the founder

## Resources

- OWASP Top 10: https://owasp.org/www-project-top-ten/
- OWASP SSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Stripe webhooks: https://docs.stripe.com/webhooks
- Next.js headers: https://nextjs.org/docs/app/api-reference/config/next-config-js/headers
- Related skills: `fastapi`, `supabase-postgres-best-practices`, `verification-before-completion` (installed); library: `production-audit`, `secret-scan`, `dependency-audit`.
