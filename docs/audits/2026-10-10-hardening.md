# Hardening audit — 2026-10-10

**Scope:** `origin/main` at `c8f4aca` (PR #14 merged). Read-only. API (`apps/api`), workers (`apps/workers`), web (`apps/web`), design system, CI (`.github/workflows`), infra (`infra/`), docs.

**Method:** seven evidence passes, one per area: auth; authorization and tenancy; input, injection and AI; rate limits and cost; crawler and webhooks; secrets, config and CI; data, ops and dependencies. Each finding carries a `file:line`. The orchestrator re-read the code behind every finding that changes what gets built (F1, F2, F3, F4, F5, F6, the IP-header trust, the session lifetime). Line numbers are as of `c8f4aca`.

**Not legal advice.** Rows that touch law (privacy, consumer law, defamation) are risk notes for the lawyer brief (Phase 6, `docs/legal/lawyer-brief.md`), not conclusions.

**Drift check (north-star §8):** Layer 0, the trust substrate (security, facts-only, honest crawling), with Layer 1 (delivery: web hardening) and §4 (deployment and operations). It serves the Activation phase: a paid launch (Epic 9). The programme is split into one branch per concern (`fix-first`, `hardening-auth`, `-api`, `-data`, `-web`, `-ops`, `-legal`). W11, the landing redesign with motion and media, is product and marketing work rather than hardening. It is flagged in the decisions for its own brief.

---

## Summary

- **Exploitable today, fixed first (branch `fix-first`):**
  - An admin can make an owner (F1).
  - Google sign-in links to a pre-registered password account and keeps that account's password and sessions alive (F2).
  - Three paid AI paths have no plan gate and no atomic throttle (F3).
  - An unauthenticated route writes one Redis key per call, with no limit, to the 25 MB `noeviction` instance that also holds sessions (F4).
  - Small items (F5) and CI permissions (F6).
- **No platform-wide spend cap exists** (D2). The only bounds are per agency, and per-agency bounds do not stop many free agencies.
- **Per-IP limits are only as good as checklist 1.3.** uvicorn trusts every `X-Forwarded-For` (`apps/api/docker-entrypoint.sh:51`, `--forwarded-allow-ips='*'`). `CLIENT_IP_HEADER` is empty by default (`config.py:183`). So unless the founder sets it to a header the edge controls, every per-IP bucket can be bypassed by sending a fake header.
- **A deploy without `ENVIRONMENT` silently runs as `local`** (`config.py:90`). In that case it:
  - serves the API docs;
  - logs at DEBUG;
  - accepts the dev `APP_SECRET`;
  - sets a session cookie without `Secure`.
- **Clean:**
  - secrets in the tree, git history and the JS bundle;
  - SQL injection;
  - deserialisation;
  - command injection;
  - uploads;
  - path traversal;
  - CORS;
  - CSRF (an Origin check);
  - Stripe webhook verification and idempotency;
  - CSV formula injection;
  - the PDF (hand-written, escaped, no links);
  - cross-agency reads (every id-bearing route is scoped).
- **Missing entirely:**
  - an audit log;
  - deletion and retention jobs;
  - a backup and restore runbook;
  - TLS enforcement to Postgres and Redis;
  - a generic 500 handler;
  - a least-privilege DB role;
  - alert rules;
  - a breached-password check;
  - an idle session timeout.

---

## Part A — Threat model

### Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Sessions and credentials | Redis `sess:{sha256}` (`sessions.py:35`), Argon2id hashes in `users.password_hash`, reset and invitation token digests in Postgres, share tokens **in plain text** on `scans.share_token` (`models/scan.py:175`) | Account takeover; a database read leaks every live share link |
| Tenant data | Postgres (Supabase): clients, scans, engine results (facts only), scores, reports | Cross-agency disclosure; agencies' client lists are commercially sensitive |
| Money | Provider keys (Anthropic, OpenAI, Gemini, Perplexity, SerpApi); Stripe subscription state | A scan costs about US$1.63 to $1.72 (memory, 2026-10-03); unbounded calls are unbounded spend |
| Secrets | Render env: `APP_SECRET`, `DATABASE_URL`, `REDIS_URL`, provider keys, Stripe keys and webhook secret | Each one is a full compromise of its system |
| Availability | One uvicorn worker per instance (`docker-entrypoint.sh:51`), scans in-process (BackgroundTasks), Redis 25 MB `noeviction` (runbook 481-497), Postgres pool 10+5 (`config.py:110-111`) | A full Redis or a blocked event loop takes sign-in down for every agency |
| Reputation and legal position | Reports naming third-party brands; the crawler's conduct (`/crawler`) | Defamation and consumer-law exposure; crawler complaints |

### Actors

| Actor | Capability | Main paths |
|---|---|---|
| Anonymous visitor | HTTP to every public route; can sign up | Sign-in flood, sign-up spam, `GET /auth/google/start` Redis fill, share-token guessing (256-bit; infeasible), shared PDF CPU |
| Signed-up attacker (own agency, free) | Every member route in their own agency | Paid AI calls with no plan gate (F3), classification loops, pre-registering someone else's email (no email verification) and keeping access after the owner links Google (F2) |
| Malicious member of another agency | A valid session elsewhere | IDOR on ids; every id route is agency-scoped and answers `404` (Appendix C) |
| Malicious member of the same agency (admin) | `RequireAdmin` routes | Mint an owner, then remove the real owner (F1); open the billing portal (owner or admin) |
| Malicious site being scanned | Controls HTML, redirects, robots.txt, DNS | SSRF (guarded at app level; network level open, 3.2), huge or looping pages, text injected into the classifier prompt |
| Compromised AI answer | Controls answer text and cited URLs | A `javascript:`/`data:` cited URL rendered as a link (no scheme allow-list, F5), sentiment manipulation, words in the fix generator (guarded) |
| Insider with a leaked key | A provider, Stripe, DB or Redis credential | Spend, data read, session forgery. The app's DB role is `postgres`-level (C1), and nothing forces TLS (C2) |

### Trust boundaries

```
browser ──HTTPS──▶ web (Next.js, Render) ──same-origin proxy /api/v1──▶ API (FastAPI, Render, 1 worker)
                                                                        │
             ┌──────────────────────┬──────────────────┬───────────────┼──────────────────────┐
             ▼                      ▼                  ▼               ▼                      ▼
   Postgres (Supabase)      Redis (Render KV)     AI providers     SerpApi, Stripe,     the open web
   no sslmode forced        25 MB noeviction      (paid)           Google, Resend       (crawler: httpx + Chromium,
   app role = postgres      sessions + limits                                            egress guard, --no-sandbox)
```

- **Browser to web.** Headers are set site-wide (`next.config.mjs`). The CSP is report-only. Token pages send `no-referrer` and `noindex`.
- **Web to API.** Same-origin proxy (`API_PROXY_TARGET`). The API trusts `X-Forwarded-For` from any peer.
- **API to Postgres and Redis.** Plaintext unless the URL says otherwise. Redis holds every session, so a full Redis means nobody can sign in.
- **API to providers.** Fixed hosts. The user-typed inputs that reach them are the ad hoc prompt (500 characters), competitor names (200), and the client URL after `parse_domain`.
- **API to the open web.** The crawler runs inside the API process. It is guarded at the app level (`services/egress.py`), but not at the network level.

---

## Part B — Register

Status: **done** (in place, with evidence) · **gap** (missing or partial; the evidence says which part exists) · **n.a.** (does not apply). "Who" is the person who closes the gap.

| # | Item | Status | Evidence | Fix | Branch | Who |
|---|---|---|---|---|---|---|
| 1 | Exposed DB credentials | done | No real DB password tracked or in history. Every URL with a password is the placeholder `avp:avp` or a test host (`config.py:106`, `apps/api/.env.example:23`, `ci.yml:56`). | Rotation steps in O4 | hardening-ops | Founder (rotation) |
| 2 | Public `.env` files | done | Only `apps/api/.env.example` is tracked (`.gitignore:15-17`). No `NEXT_PUBLIC_*` variables (`apps/web/src/nextConfig.test.ts:122-125`). | — | — | — |
| 3 | Hard-coded secrets | done | Key-shaped strings are test placeholders only. The default `APP_SECRET` (`config.py:124`) is refused in staging and production (`config.py:548-553`), but only when `ENVIRONMENT` is set (row 29). | F6 renames the key-shaped fixtures so scanners stay quiet | fix-first | Claude |
| 4 | Weak auth | gap | Argon2id t=2, m=19,456 KiB, p=1, rehash on sign-in (`security.py:31-64`, `config.py:142-144`, `services/auth.py:141`). Length 12 to 256 plus 5 distinct characters (`schemas/auth.py:17-33`). **Missing:** a breached-password check, per-account lockout, D9's wording. Hashing runs on the event loop. | A1, A2, A4; F5 moves hashing off the loop | hardening-auth, fix-first | Claude |
| 5 | Missing authz checks | gap | Every non-public route uses `PrincipalDep` or `RequireAdmin` (Appendix C). **The gap:** the role on invite. An admin can invite or re-invite as owner (`schemas/agency.py:30`; `services/invitations.py:160,187,198`). | F1; the A7 matrix makes it permanent | fix-first, hardening-auth | Claude |
| 6 | Cross-user / cross-agency access | done | `get_client` filters by agency (`services/intake.py:379-393`). Scans load with `Scan.agency_id` (e.g. `routers/report.py:101-104`). `assert_own_agency` (`deps.py:147`). Defence-in-depth gap: 3.116 (below). | A7, A9 | hardening-auth | Claude |
| 7 | Open DB permissions (Supabase roles, Data API, RLS) | done in code; state is founder-verified | Migration `f45983cfa5e3` revokes `anon` and `authenticated` (`infra/db/migrations/versions/20261003_2302_f45983cfa5e3_revoke_data_api_grants.py:184-266`). `tests/test_supabase_grants.py:455,490`. Checklist 3.3: Data API off, RLS on for 25 tables (founder, 2026-10-03). | C1 re-asserts it against the head migration | hardening-data | Founder confirms |
| 8 | Cloud misconfiguration (Render, Supabase, Redis, Cloudflare) | gap (not visible from the repo) | No IaC is tracked (no `render.yaml`). Known dependencies on settings: `ENVIRONMENT` (row 29), `CLIENT_IP_HEADER` (row 28, checklist 1.3), Redis `noeviction` (3.4, done), Supabase Data API (row 7). | F5 refuses an unsafe start; O4 lists the settings to check | fix-first, hardening-ops | Founder |
| 9 | Unprotected admin / owner routes | gap | Admin routes are gated (`deps.py:182`). `RequireOwner` is defined but used nowhere (`deps.py:181`). Billing checkout and portal are owner-or-admin (`routers/billing.py:209,290`), while the module docstring calls owner the billing role (`routers/billing.py:37`). | F1 (inviting an owner); a decision on the portal (decisions) | fix-first | Founder decides |
| 10 | Exposed production debug tools | done (conditional) | Docs and OpenAPI are off when deployed (`main.py:152-157`). No debug flag, `/metrics`, `/debug` or flower. All of this depends on `ENVIRONMENT` being set (row 29). | B10 asserts it at start | hardening-api | Claude |
| 11 | Secrets in logs | done | Paths are redacted (`logs.py:111-130`). The client IP is dropped from the access line (`logs.py:150-160`). Call sites log ids. `tests/test_secrets_stay_out_of_logs.py`. There is no field-level scrubber: safety relies on the call sites. | O3/D13 adds one forwarding point with a scrubber | hardening-ops | Claude |
| 12 | Verbose production errors | gap | No generic `Exception` handler (`errors.py:377-440`). An unhandled error is Starlette's bare 500, with no request id and no security headers. `BillingNotConfigured` names an env var (`services/billing.py:262`). | B10 | hardening-api | Claude |
| 13 | Secrets in git history | done | A history scan for 12 key shapes found test placeholders only (`af503d8`, `bc00748`). The only env file ever added is `.env.example`. | O1 adds a scan in CI | hardening-ops | Claude |
| 14 | Secrets in the JS bundle | done | No `NEXT_PUBLIC_*`. No `process.env` in client files. `API_PROXY_TARGET` is server-only and rejects credentials. | — | — | — |
| 15 | Client-only security | done | The seat limit, plan, allowance and roles are all enforced by the API (`services/seats.py`, `services/scan_allowance.py`). The web's role gating is cosmetic. The invite form offers Owner to admins (`SeatsPanel.tsx:450`), and F1 removes it. | F1 | fix-first | Claude |
| 16 | Input validation | gap | Request bodies are bounded (`schemas/*`). Unknown fields are silently ignored (`schemas/common.py:12-27`; `extra="forbid"` on 2 models only). `cursor`, `scanId` and the OAuth `state`/`code` query strings have no maximum length (`routers/scans.py:182,220`; `routers/clients.py:196,226`; `routers/auth.py:627-629`). The alerts feed is unbounded (`routers/alerts.py:70-96`). | A8, B1 | hardening-auth, -api | Claude |
| 17 | SQL injection | done | No raw SQL. `text()` appears only with constants (`routers/health.py:45`, model index fragments). | B2 adds a guard test | hardening-api | Claude |
| 18 | NoSQL injection (Redis keys from input) | done | Every key is built from an HMAC or a digest (`services/rate_limit.py:100-105`, `sessions.py:35-36`, `services/google_oauth.py:119-120`). | B2 guard test | hardening-api | Claude |
| 19 | XSS | gap | One `dangerouslySetInnerHTML`, a static constant (`app/layout.tsx:49`). The PDF escapes text and has no links (`services/pdf.py:202-211`). **Gap:** cited URLs from AI answers are stored with any scheme (`services/engines.py:582-611`) and rendered as `href` (`components/report/ReportView.tsx:1132-1140`) with no http(s) allow-list. | F5 (http(s) only, API and web), B3 | fix-first, hardening-api | Claude |
| 20 | CSRF | done | Origin check on POST, PUT, PATCH and DELETE, including a cookie-bearing write with no Origin (`origin_check.py:180-194`, `main.py:194-201`). SameSite=Lax. The Stripe webhook is exempt by exact path. | A3 asserts the cookie flags | hardening-auth | Claude |
| 21 | File uploads | n.a. | No `UploadFile`, multipart or `type="file"` anywhere. | — | — | — |
| 22 | Path traversal | n.a. | No filesystem path is built from input. The PDF filename is reduced to `[A-Za-z0-9._-]` (`routers/report.py:38`). | — | — | — |
| 23 | SSRF (crawler, robots and sitemap fetch, redirects, egress 3.2) | gap | Done at app level: the egress guard checks the resolved address, pins it and re-resolves at connect (`services/egress.py:169-202,254-280`); redirects are followed by hand, at most 5, each hop re-checked (`services/crawl.py:630-663`); every browser request goes through the guard (`egress.py:563-614`). **Open:** Chromium runs with `--no-sandbox` (`crawl.py:1048-1050`); speculation-rules prerender bypasses routing; no browser response-size cap; downloads are not disabled; no network-level egress control (checklist 3.2). | B7, plus the design note | hardening-api | Founder picks the egress control |
| 24 | Broken password reset | gap (one) | 256-bit token, digest stored, single use under a row lock, 1 h, a newer token retires older ones, bound to the user, and the reset ends every session (`services/password_reset.py:86-160`, `routers/auth.py:430-436`). **Gap:** the email is sent inside the request for known addresses only, which is a timing difference (`routers/auth.py:361-368`). | F5 (background send); A5 proves the rest | fix-first, hardening-auth | Claude |
| 25 | Weak session management | gap | 256-bit opaque tokens, stored by digest; server-side revocation; a per-user index; a new token at every sign-in; a password change or reset revokes the others (`sessions.py`, `routers/auth.py:290,430`). **Missing:** an idle timeout (`SessionStore.touch` is never called, `sessions.py:136-153`). The lifetime is one fixed 14 days (`config.py:127`). F2's link leaves sessions alive. | F2; A3 (D8, with proposed lifetimes) | fix-first, hardening-auth | Founder (values) |
| 26 | JWT / signing secrets | gap | AVP mints no JWTs. The Google id_token is checked for RS256, audience, issuer and expiry (`services/google_oauth.py:238-267`). `APP_SECRET` keys the rate-limit HMAC. Its only check is "does not start with dev-only" (`config.py:548-553`): no minimum length and no rotation. | A-phase: a minimum of 32 bytes at start; rotation steps in O4 | hardening-auth, -ops | Claude |
| 27 | Permissive CORS | done | Explicit origins from env; `*` and empty are refused when deployed (`config.py:547-566`); credentials with an explicit list; three request headers (`main.py:206-211`). | — | — | — |
| 28 | Rate limits on every route class | gap | Only login, sign-up, reset request, change-password (`services/rate_limit.py:57`) and detection (`:331`) are limited. **None on:** reset confirm, invitation accept, Google start, callback, pending and complete, the shared report and shared PDF, PDF and CSV export, fix generation, classification, and every authenticated read. Per-IP buckets rest on checklist 1.3 (summary). | F3, F4, F5, B4 | fix-first, hardening-api | Claude; founder (1.3) |
| 29 | Exposed environments | gap | `ENVIRONMENT` defaults to `local` (`config.py:90`). Without it, a deploy serves docs, logs DEBUG, accepts the dev secret and sets a non-Secure cookie. No preview or staging environment is defined in the repo. | F5 (refuse to start with a remote database and no `ENVIRONMENT`); B10 | fix-first, hardening-api | Claude |
| 30 | Default credentials | gap (scripts) | No seed users. The default secret is refused when deployed. But `scripts/seed_dev.py:569` writes to whatever `DATABASE_URL` says, with no production guard; the same goes for the `verify_*.py` scripts. | Refuse when `ENVIRONMENT=production` | hardening-api (B10) | Claude |
| 31 | Unsigned webhooks | done | `stripe.Webhook.construct_event` with the SDK's default 300 s tolerance (`services/billing.py:1103`). 400 before any DB write. Idempotent on the event id (PK and `ON CONFLICT DO NOTHING`, `services/billing.py:1283-1310`). Stale events refused (`:1413`). | B8 pins the tolerance explicitly and adds replay and unsigned tests | hardening-api | Claude |
| 32 | Front-end payment checks | done | The price id comes from server config (`services/billing.py:809,994`). The success URL grants nothing; only the webhook changes state (`:778-784`). | — | — | — |
| 33 | IDOR / BOLA | done | Appendix C: every path id is agency-scoped, and another agency's id is a 404. | A7 makes it a generated test | hardening-auth | Claude |
| 34 | User input passed to providers | done (by design) | SerpApi gets URL-encoded params (`services/serp.py:474-477`). The ad hoc prompt (500 characters) is sent verbatim to the engines, as intended. Competitor names (200) go into the generator and fix prompts. No email, user name or agency name reaches a provider. | G5 adds a test | hardening-legal | Claude |
| 35 | Exposed logs | done | No route returns logs. Render's log access is the founder's (row 45). | — | — | — |
| 36 | Exposed source maps | done | `productionBrowserSourceMaps` is unset (Next's default is off). `poweredByHeader: false`. | W2 asserts it | hardening-web | Claude |
| 37 | Vulnerable dependencies | gap (not measured) | CI runs licence audits only. No advisory audit has been run on the three lockfiles. | B11; O1 gates CI | hardening-api, -ops | Claude |
| 38 | Malicious or typosquatted packages | gap (transitive not checked) | The 19 API, 3 workers and 7 web runtime dependencies are the canonical project names (`apps/api/pyproject.toml:12-38`, `apps/workers/pyproject.toml:9-13`, `apps/web/package.json`). The lockfiles' transitive packages are not checked yet. | B11 | hardening-api | Claude |
| 39 | Prompt injection | gap | Crawled page text (up to 18,000 characters) goes into the classifier under a bare "Page text:" label, with no instruction to treat it as data (`services/classify.py:170-196,233-245`). Answer text goes into sentiment the same way (`services/extraction.py:495-508`). The outputs are schema-bound. The classifier's `brand_name` (200 characters) flows into later prompts (`services/prompts.py:189`). | B6 | hardening-api | Claude |
| 40 | Unpermissioned AI access | done | AVP executes no tools. Provider-side web search only: Claude search with `max_uses` 4 (`services/engines.py:536-543`) and Perplexity with `max_results` 10. Injected text can at most cause more provider-side searches within those caps. | B6 asserts "no tools" | hardening-api | Claude |
| 41 | Excessive DB permissions | gap | The app connects as `postgres` / `postgres.<ref>` (`docs/launch-checklist.md:145-147`): an owner-level role that bypasses RLS. | C1 (a runtime DML role; migrations under a separate role) | hardening-data | Founder (Render env) |
| 42 | Audit logs | gap | None. Log lines are the only trail. | C3 (D4) | hardening-data | Claude |
| 43 | Security monitoring | gap | Stable events exist: `stripe.webhook.rejected` (`services/billing.py:1105`), `auth.rate_limited` (`services/rate_limit.py:233`), `redis.data_lost` (`redis_watch.py:131`), `*.auth_failed`. There is no log stream and no alert rules. | O3; D13 (a forwarder, off by default) | hardening-ops | Founder (vendor) |
| 44 | Backups / restore | gap | No runbook section. Supabase's backup plan is not recorded. | C6 | hardening-data | Founder runs it |
| 45 | Exposed internal dashboards | gap (unknown) | The operator paths are the Render Shell, the Supabase SQL editor and the Stripe dashboard (`docs/runbook.md:148-152,918`). 2FA is not recorded anywhere. | The founder turns on 2FA for Render, Supabase, Stripe, GitHub, Google Cloud and Cloudflare; a checklist row | hardening-ops | Founder |
| 46 | Security headers | gap (part) | Done: nosniff, XFO DENY, `frame-ancestors 'none'`, HSTS (2 years, includeSubDomains), Permissions-Policy and Referrer-Policy on the API and the web (`security_headers.py`, `apps/web/next.config.mjs`). **Gap:** the full CSP is report-only, with no switch, and Starlette's bare 500 carries no headers. | W1 (D6), B10 | hardening-web, -api | Claude |
| 47 | Cookie settings | done (conditional) | `__Host-avp_session`, HttpOnly, Secure (forced when deployed), SameSite=Lax, Path=/, no Domain (`routers/auth.py:71-84`, `config.py:514-519,555`). Depends on row 29. | A3 asserts it in tests | hardening-auth | Claude |
| 48 | Encryption in transit and at rest | gap | No `sslmode` or `rediss://` enforcement (`db.py:24-30`, `redis_client.py:24`). At rest is the providers' (Supabase, Render), which the repo cannot show. | C2 | hardening-data | Founder confirms the URLs |
| 49 | Tenant isolation | done | Rows 6 and 33. The app role bypasses RLS, so isolation is enforced in code only. | A7, A9 | hardening-auth | Claude |
| 50 | Unreviewed code paths | gap | No test names `POST /scans/{id}/fixes`, `/scans/{id}/audit` or `/ready` by path. The `seed_dev.py` and `verify_*.py` scripts write to any `DATABASE_URL` (row 30). | A7 covers every route; B10 guards the scripts | hardening-auth, -api | Claude |
| 51 | Mass assignment | gap | The only privilege-bearing request field is `InviteSeatRequest.role` (F1). No request schema accepts `agency_id`, `user_id`, `plan`, `price` or `status`. Unknown fields are ignored rather than refused (`schemas/common.py:12-27`). | F1, A8 | fix-first, hardening-auth | Claude |
| 52 | Command injection | n.a. | No `subprocess`, `os.system` or shell in `apps/api/src` or `apps/workers/src`. | B2 guard test | hardening-api | Claude |
| 53 | Insecure deserialisation | done | Celery is JSON only (`apps/workers/src/avp_workers/celery_app.py:20-22`). No `pickle`, `yaml.load` or `marshal`. | B2 guard test | hardening-api | Claude |
| 54 | Misconfigured OAuth | gap | Done: 256-bit single-use `state` (600 s), PKCE S256, nonce, a browser-binding cookie, a fixed redirect URI, `email_verified` required, RS256/aud/iss (`services/google_oauth.py:190-337`; `routers/auth.py:556-568,706-725`). **Gap:** linking by email keeps a pre-registered password and its sessions (`services/google_oauth.py:467-469`). | F2, A6 | fix-first, hardening-auth | Claude |

### Also covered

| Item | Status | Evidence | Fix |
|---|---|---|---|
| Clickjacking | done | `X-Frame-Options: DENY` and `frame-ancestors 'none'` on every web page, including the report and share pages (`next.config.mjs`, site-wide rule; `app/securityHeaders.test.ts`), and on every API response, including the PDF and CSV exports (`security_headers.py`). Starlette's bare 500 has neither, but it is not framable content. | W1 adds an attacker-frame test |
| Account enumeration | gap | **Login:** no (one 401, dummy verify, `services/auth.py:129-134`). **Reset request:** the body is the same, but the timing differs (row 24). **Sign-up:** yes, a 409 for an existing address (`services/auth.py:80-86`), by design; the D9 proposal is in the decisions. **Google complete:** a 409, behind a single-use ticket. **Google callback:** tells the address's verified holder about their own account (by design, `google_oauth.py:384-404`). **Invite:** a 409 tells an admin whether an address is registered in *any* agency, with no limit (`routers/agencies.py:82`, `services/invitations.py:166-173`). | F5 (timing), A1, B4 (a limit on invites) |
| Debug flags | done (conditional) | No `debug=` anywhere. DEBUG logging and docs follow `ENVIRONMENT` (row 29). | F5, B10 |
| 3.113 Competitor `PUT` race | gap | The whole list is replaced under a row lock (`services/competitors.py:1178`), with no version or ETag (`schemas/competitor.py:119`). | A10 |
| 3.116 Nine reads by client id alone | gap (defence in depth) | Each sits behind an agency-scoped `get_client`. Candidates: `services/client_history.py:173`, `routers/alerts.py:122`, `services/alerts.py:124`, `routers/scans.py:227`, `services/crawler_access.py:99`, `services/answer_gaps.py:192,263`, `services/competitors.py:618`, `services/auto_rescan.py:127` (a batch query). | A9 |
| 3.2 Crawler egress and sandbox | gap | Row 23. | B7, design note |
| Invitation accept race | gap | No `with_for_update` on the invitation or the user (`services/invitations.py:259-283`), so two concurrent accepts both succeed. | F5 |
| CPU on the event loop | gap | Argon2 (`services/auth.py:102,130,133`), PDF rendering (`routers/report.py:41-60`), with no `to_thread`. The only offload is Google's JWKS (`google_oauth.py:236`). | F5 |
| Share tokens in plain text | gap | `scans.share_token` stores the token itself (`models/scan.py:175`, `services/share.py:312`). Invitation and reset tokens store a digest. | Store a digest (one migration; live links keep working) — hardening-data |

---

## Abuse scenarios — what happens today

1. **A bot tries 10,000 logins a minute.**
   - The limiter runs before the database and before Argon2 (`routers/auth.py:153`, `services/rate_limit.py:67-79`).
   - From one IP, the 31st failure in 15 minutes gets a 429, so the rest cost one Redis call each. Against one address, the 11th gets a 429.
   - Spread across IPs and addresses, each attempt costs one Argon2 hash (about 19 MiB, t=2) **on the event loop**. A few hundred a second stall every other request on that instance.
   - Spoofing `X-Forwarded-For` defeats the IP bucket until 1.3 is done.
   - Ten wrong guesses lock the real owner of an address out for 15 minutes.
   - The login page itself is static.
   - Fixed by: F5 (`to_thread`), A2, B4.
2. **Sign-up spam fills the database.**
   - The limit is 10 an hour per IP and 5 per address (`config.py:165-166`), and the IP bucket can be spoofed.
   - Each sign-up writes an agency, a user and a session. There is no email verification and no global cap.
   - A free account cannot run a scan without a Google identity (`services/scan_allowance.py:326-335`).
   - It can create unlimited clients, and each one is classified by a paid model with no plan check (F3).
   - Fixed by: F3, D11, B4.
3. **Hammering the expensive endpoints.**
   - **Scan start:** a free agency gets 1 scan and a paid one 40 a month. With Stripe unconfigured, the gate is off and scans are unlimited. There is no platform cap.
   - **Detection:** paid only, 1 per client per day, but clients are unlimited.
   - **Ad hoc prompts:** 30 per client per hour, check-then-act (a burst passes), and no plan check.
   - **Fix regeneration:** unlimited (an 8,000-token call each time).
   - **Classification:** unlimited, run one after another.
   - **PDF:** unlimited, and rendered on the event loop. The shared PDF needs no session.
   - Fixed by: D2, F3, F5, B4.
4. **Someone scrapes every report and share link, slowly.**
   - Share tokens are 256-bit and posted in the body, and every miss is the same 404, so guessing is infeasible.
   - There is no limit and no signal on `/reports/shared*`.
   - Within an agency, reads are unlimited. Across agencies, the answer is 404.
   - A database read would expose every live share token (plain text).
   - Fixed by: F5 (a limit on `/reports/shared*`), B4 (a scraping signal), hardening-data (digest).
5. **A scanned site returns hostile content.**
   - **Huge:** the httpx reads are capped (robots 512,000 characters, sitemap 65,536, inflation 4×). The browser has no byte cap, only a 12 s page timeout and the run cap. The extracted text is capped at 18,000 characters.
   - **Redirect loop:** the hand-followed chain stops after 6 requests. Chromium's own limit and the page timeout bound the browser.
   - **Injected instructions:** the text reaches the classifier unlabelled (row 39). The output is schema-bound, so at worst a wrong industry or brand name.
   - Fixed by: B6, B7.
6. **An AI call loops, retries or recurses without bound.**
   - None found. Every call site has `max_tokens`, a per-attempt timeout, bounded retries and an outer deadline (`services/call_bounds.py:57-98`, `services/engines.py:156`). A scan is capped at 2,400 s (`services/scan_executor.py:170`). The Celery retry config is inert (no paid task uses Celery).
   - The unbounded spend is user-repeated requests (scenario 3), not internal loops.
   - Fixed by: B5 adds a test that fails when a call site has no bound.

---

## Appendix C — Route scoping (from the authorization pass)

| Route group | Auth | Scoping |
|---|---|---|
| `/agencies/{agencyId}/seats`, `/invitations`, `/branding`, `/billing*` | `RequireAdmin` | `assert_own_agency` → 404 |
| `DELETE /users/{userId}` | `RequireAdmin` (+ owner-only to remove an owner) | `User.agency_id == principal.agency_id` → 404 |
| `/clients/{clientId}/…` (history, alerts, answer gaps, crawler access, reclassify, auto-rescan, other sites, questions, rival suggestions, prompt runs, competitors, scans) | `PrincipalDep` | `get_client` (`agency_id` filter) → 404 |
| `/scans/{scanId}/…` (results, prompts, scores, audit, fixes, report, PDF, CSV, share) | `PrincipalDep` | Scan loaded with `Scan.agency_id` → 404 |
| `/alerts/{alertId}/acknowledge` | `PrincipalDep` | `Alert.agency_id` → 404 |
| `/dashboard*`, `/clients` | `PrincipalDep` | `agency_id` from the principal |
| Public | none | `/health`, `/ready`, `/billing/plan`, `/billing/webhook` (signature), `/reports/shared`, `/reports/shared/pdf` (token), `/auth/sign-up`, `/login`, `/reset-password/*`, `/invitations/accept`, `/google/*` |

Loads by an id alone that follow an earlier agency check: `routers/audits.py:119-121`, `routers/action_items.py:93-94`, `services/report.py:133` (client from an agency-verified scan); `routers/competitors.py:29-36` (a set just built for the verified client); `routers/agencies.py:~275` (after `assert_own_agency`).
