# Launch checklist

What stands between today's code and taking money from agencies. Each item says
who does it and the exact steps. **Founder** means only the founder can do it
(an account, a dashboard, a legal or product decision). **Claude** means an agent
session can do it, on a branch, with the full bar and a build-log entry, and
only after the founder says go. Nothing here is pushed to `main` without the
founder's OK; Render deploys `main` before CI finishes.

Status on 2026-10-01: PR #2 (`launch-readiness`) is open, not merged, and
`origin/main` is `c5b4ba4`. This checklist was written on
`post-launch-readiness`, which is stacked on `launch-readiness`; its PR targets
`main` (CI runs only on pull requests into `main`) and shows PR #2's commits
too until PR #2 is merged.

## 1. Merge and deploy what is built

### 1.1 Merge PR #2, then this branch's PR — Founder
1. Read PR #2's description: it lists two migrations, `7c1d4e9a2b53` and
   `3e8a5b7c9d14`. Both run in the API container before it serves, each under
   `lock_timeout = '5s'`.
2. In Stripe → Developers → Webhooks → the endpoint, check there are no
   pending retries (the ordering watermark starts empty).
3. Merge PR #2. Render deploys the API and the web app. Deploy them close
   together: during the gap a person mid-way through Google sign-up is told
   the link is not valid and starts again.
4. On the Render API service → Logs, check the deploy got past
   `entrypoint: alembic upgrade head` to `entrypoint: serving on :<port>` (if
   it stops at the first line, a migration is probably waiting on a lock).
   Then open `/api/v1/ready`: 200.
5. Then this branch's PR, which targets `main`: once PR #2 is merged its diff
   shows only this branch's own commits. Merge it the same way. It adds no
   migration.
6. After both deploys, run item 3.10 (the token pages' headers on a served
   response).

### 1.2 `STRIPE_PRICE_ID` is the US$160 Price — Founder
1. Stripe dashboard (Test mode until 2.2 is done) → Product catalogue → the
   product with a recurring monthly Price of US$160. Copy its id (`price_…`).
2. Render → API service (`ai-visibility-platform-5gk4`) → Environment → set
   `STRIPE_PRICE_ID` to it. Save (Render redeploys).
3. Done when Settings → Subscribe opens a Stripe checkout page showing US$160
   per month.

### 1.3 Confirm how Render passes the client IP — Founder
Run `docs/runbook.md` → "Confirm how Render passes the client IP (rate
limits)" from your own machine. It sends 11 password-reset requests with
made-up addresses (email is off, nothing is sent).
- 11th answers 429: done, nothing to set.
- 11th answers 200: set `CLIENT_IP_HEADER=CF-Connecting-IP` on the API service
  and run the runbook's steps 5–6.
Until this is done, the per-IP limits may not be reliable; the per-email
limits hold either way.

### 1.4 Live Stripe test, in Stripe's Test mode on Render — Founder
Run `docs/runbook.md` → "Live Stripe test (test mode on Render)", all steps.
Since this branch it also checks (f) that an unpaid Checkout link stops
working after an hour, and (g) that a canceled agency can subscribe again from
Settings while a live one is refused with the Manage billing sentence.

## 2. Before charging real money

### 2.1 Rotate the 21st.dev key and remove `API_KEY_21ST` — Founder
The old literal key sat in `.claude/settings.local.json` as `env.API_KEY_21ST`
(git-ignored, but a plaintext copy). `.mcp.json` already reads
`${TWENTYFIRST_API_KEY}` instead.
1. In the 21st.dev dashboard, revoke the old key and create a new one.
2. Put the new key in your shell profile as `export TWENTYFIRST_API_KEY=…`
   (never in a file inside the repo).
3. Open `.claude/settings.local.json` yourself and delete the
   `API_KEY_21ST` entry under `env`. Agents must not open that file.
4. Quit VS Code and Claude Code, start them from a shell that has the new
   variable, and check a `21st` tool answers.

### 2.2 Stripe verification and live keys — Founder
1. Stripe → Activate account: business details (sole trader, ABN), identity,
   bank account for payouts. Wait for `charges_enabled`.
2. In **live** mode: create the product and the US$160 monthly Price; add the
   webhook endpoint
   `https://ai-visibility-platform-5gk4.onrender.com/api/v1/billing/webhook`
   with exactly `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`; set the
   Customer portal (cancel at period end, update payment method) as in the
   runbook's step 5.
3. On Render (API service), replace `STRIPE_SECRET_KEY` with the live secret
   key, `STRIPE_WEBHOOK_SECRET` with the live endpoint's signing secret and
   `STRIPE_PRICE_ID` with the live Price id, in one save. Live keys go on
   Render only, never in the repo or a chat.
4. Buy one subscription with your own card, check Settings shows it active and
   the four webhook deliveries answered 200, then cancel and refund it from
   the Stripe dashboard.

### 2.3 Email through Resend — Founder, then Claude
Production sends no email today (`RESEND_API_KEY` unset); the product says so
and shows invite links to the inviter instead.
1. Founder: a custom domain is needed (Resend will not verify `onrender.com`).
   Add the domain in Resend, publish its DNS records, wait for "Verified".
2. Founder: create a Resend API key; on Render set `RESEND_API_KEY` and
   `EMAIL_FROM` (an address on that domain).
3. Claude: run a password reset and an invitation against the deployed API
   with the founder's address and confirm both emails arrive; check
   `/privacy`'s Resend line now reads true as written.

### 2.4 Lawyer review of `/privacy`, `/terms` and `/crawler` — Founder
The pages are drafts written by an agent from the code's own facts
(`apps/web/src/components/legal/`). The founder sends them to a lawyer and
returns the wording; Claude applies it verbatim, keeping the constants the
tests read back from the API (`lib/legal/retention.ts`, `PricingCard.tsx`).

### 2.5 Deletion endpoint and the data-retention decision — Founder decides, Claude builds
There is no way to delete an account or agency today.
1. Founder decides: what a deletion request removes (agency, users, clients,
   scans, reports, share links), what is kept and for how long (for example
   Stripe invoices for tax), and how fast it happens.
2. Claude builds it to that decision: an owner-only endpoint with tenancy
   checks, the Stripe subscription cancelled first, a migration if needed,
   tests, a security review, and the `/privacy` text updated to match.

### 2.6 Per-device lockout — deferred (founder decision 8)
Today login, sign-up, change-password and reset are limited per email and per
IP. A per-device lockout was deferred on 2026-10-01. Revisit if the logs show
`auth.rate_limit.*` events from many addresses against one account.

### 2.7 Remove the legacy `?ticket=` fallback — Claude, one release after 1.1
`apps/web/src/lib/googleTicket.ts` still reads a Google sign-up ticket from the
query string for links minted before the ticket moved to the fragment (marked
`LEGACY`). One release after PR #2 is live, delete that branch and its tests,
run the bar, and add a build-log entry. The founder says when the release has
passed.

### 2.8 Pilot outreach — Founder
Pick the first agencies to approach and what they are offered (the free scan
with Google sign-in is the product's own first step). Marketing copy follows
the facts-only rule: no invented numbers, testimonials, logos or customers.

## 3. Hardening found while building (not yet done)

Each needs the founder's go before Claude starts; a security review applies to
every one marked (sec).

| # | What | Who | Steps |
|---|------|-----|-------|
| 3.1 | **Security headers.** Neither host sends HSTS, a Content-Security-Policy, `X-Content-Type-Options: nosniff` or a frame policy (only the token routes carry headers, `apps/web/next.config.mjs`; the API sets CORS only). | Claude (sec), after the founder's go | Add the headers for every web route in `next.config.mjs` (CSP in report-only mode first, because Next inlines scripts) and a small API middleware for nosniff and frame denial; tests that read the config; after deploy `curl -sI` both hosts. |
| 3.2 | **The crawler needs an egress control.** It fetches the sites agencies add without limiting which network addresses those sites resolve to, and its browser runs without Chromium's sandbox. | Founder picks the egress control; Claude (sec) wires it | A check inside the app is not enough on its own; the fix is network-level (an egress proxy that refuses non-public addresses, used by both the browser and the HTTP client). Then decide on the sandbox. The details are in the founder's 2026-10-01 report, not here, because this repository is public. |
| 3.3 | **Confirm the Supabase Data API exposes nothing.** The app never uses it, and whether it is on with grants for `anon` / `authenticated` has not been checked. | Founder | In Supabase → SQL editor, run the two read-only queries in `~/Skills-library/supabase-ops/SKILL.md` (table grants for `anon`, `authenticated`, `PUBLIC`; `pg_default_acl`). If anything is granted, turn the Data API off (Settings → API) or remove `public` from the exposed schemas; revoking grants alone does not cover tables Alembic creates later. |
| 3.4 | **Set the Redis eviction policy.** Under an evicting policy, the index that lets a password change or reset sign a user out everywhere can be dropped silently. | Founder | Render → the Key Value instance → set the eviction policy to `noeviction`; note whether one instance serves sessions, rate limits and the Celery broker (`REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`); add a memory alert if Render offers one. |
| 3.5 | **SQL parameters can reach the logs.** `db.py` does not set `hide_parameters=True`, and `logger.exception` / `exc_info=True` (`google.callback.failed` in `routers/auth.py`; `scan.lease.renewal_failed`, `scan.chain.phase_failed`, `scan.chain.reload_failed`, `scan.execute.failed` and `scan.execute.failed_to_record` in `services/scan_executor.py`) render SQLAlchemy errors whose text includes bound parameters, such as an email. Unconfirmed at runtime. | Claude (sec) | `hide_parameters=True` on the engine; log `error=type(exc).__name__` on identity paths; add an exception-path case to `tests/test_secrets_stay_out_of_logs.py`. |
| 3.6 | **No error pages.** `apps/web` has no `error.tsx`, `global-error.tsx` or `not-found.tsx`; an unhandled API 500 carries no CORS headers, so the browser sees a network error rather than the problem body. | Claude + ui-reviewer | Add the three pages with design-system components and facts-only copy; make the web's fetch wrapper show a sentence for a non-`ApiProblem` failure. |
| 3.7 | **Confirm the production SameSite value.** The session cookie is HttpOnly and Secure when deployed, and the API relies on SameSite `lax`, the default. | Founder | Render → API service → Environment: confirm `SESSION_COOKIE_SAMESITE` is unset or `lax`. |
| 3.8 | **The share bar's copy is wrong.** `apps/web/src/components/report/ShareLinkBar.tsx:71-72` says the link "does not expire and cannot be withdrawn yet". It expires 30 days after it is created (`SHARE_TTL`, `services/share.py:101`; creating it again resets that), and the API can revoke it (`DELETE` in `routers/report.py:202-207`), though the web has no button for that. | Founder decides the wording (and whether to add a Withdraw button); Claude builds | One sentence, then optionally a Withdraw link button calling the existing route, with ui-reviewer. |
| 3.9 | **A JavaScript-running crawler that ignores `X-Robots-Tag` could read a leaked legacy `/share/<token>` report** now that robots.txt disallows nothing (founder decision 3, accepted). | Founder | Accept, or have Claude make `/share/[token]` wait for a click before loading the report. |
| 3.10 | **The token pages' headers have never been checked on a served response.** | Claude, after 1.1 | `curl -sI` the seven token paths on the web host (`/share`, `/share/x`, `/invite`, `/invite/x`, `/reset-password`, `/reset-password/x`, `/sign-up/google`): each must show `x-robots-tag: noindex, nofollow` and `referrer-policy: no-referrer`. |
| 3.11 | **Licence audit gap.** A Python package that declares GPL only as a classifier (`GNU General Public License v3 (GPLv3)`) lands in REVIEW, which does not fail CI, instead of BLOCK. None does today. | Claude | Extend `BLOCKING` in `apps/api/scripts/license_audit.py` to the classifier spellings, with tests. |
| 3.12 | **The scan allowance can read an outdated subscription status** under concurrent requests (`services/scan_allowance.py`, the locked re-select of the agency). | Claude (sec) | Refresh the row on the locked re-select (`populate_existing`), with a test. |
| 3.13 | **Stripe and Redis failures that answer 500.** Customer, Checkout Session and portal creation do not catch `StripeError` (only the existing-customer pre-check does), and a Redis out-of-memory error is not mapped, so these answer 500 instead of a 503 with a sentence. | Claude (sec for billing) | Map both to the existing 503 problem types, with tests. |
| 3.14 | **Migrations run in one transaction.** `infra/db/migrations/env.py` does not set `transaction_per_migration`, so a backfill shipped with DDL in one deploy would hold ACCESS EXCLUSIVE throughout. | Founder, when the first backfill is needed | Decide then; the `alembic-migrations` library skill describes the batched pattern. |
| 3.15 | **The web never says why a scan failed or is partial.** `errorCode` is in the API but no screen shows it, so a scan that ran out of time reads like an engine outage. | Founder decides the wording | Then Claude adds it to the scan screens with ui-reviewer. |
| 3.16 | **The Google sign-up page says "expired" when the API did not answer**, which it cannot know (`apps/web/src/app/sign-up/google/page.tsx`). | Founder decides the wording | Claude adds a "could not check" state with ui-reviewer. |
| 3.17 | **Settings tells every signed-in agency "Your first scan is free…"** (`apps/web/src/components/settings/SettingsView.tsx:217`), which is wrong once the free scan is used; dark-theme `text-tertiary` on seated cards measures about 4.1:1. | Founder (copy); Claude (contrast token, ui-reviewer) | Decide the sentence; then fix both. |
| 3.18 | **The scan-limit notice offers Subscribe where it cannot work:** to an agency whose subscription needs attention (it then shows the refusal and a link to Billing) and to members (403). | Claude + ui-reviewer, after the founder's go | `useScanAllowance` already reads `GET /billing`; use it to show Manage billing instead, and nothing to members. |
| 3.19 | **Decide whether a scan that runs out of time because of load should count** (decision 7 as written counts it when it has at least one answer). The engine rate limits are shared across the product, so heavy load can push scans past the 40-minute deadline. | Founder | Keep it, or have Claude count a deadline-partial only above a minimum share of answered prompts (from the security review of decision 7). |
| 3.20 | **One step of the technical audit has no timeout of its own** (`services/technical_audit.py`). If it hangs, the scan runs into its deadline and alerts and fix generation do not run. | Claude | Bound it, so a hang becomes an ordinary failed audit phase. |
