# Launch checklist

What stands between today's code and taking money from agencies. Each item says
who does it and the exact steps. **Founder** means only the founder can do it
(an account, a dashboard, a legal or product decision). **Claude** means an agent
session can do it, on a branch, with the full bar and a build-log entry, and
only after the founder says go. Nothing here is pushed to `main` without the
founder's OK; Render deploys `main` before CI finishes.

Status on 2026-10-02: PR #2 and PR #3 are merged (`main` at `9a6a1a7`). This
checklist was last updated on branch `pre-pilot` (the founder's pre-pilot brief,
items 1–6), whose PR targets `main`. Rows that branch closed say **Done** and
name the commit.

## 1. Merge and deploy what is built

### 1.1 Merge PR #2, then this branch's PR — Founder — **Done 2026-10-01** (`main` at `9a6a1a7`)
The steps are kept for the record; only step 6 is still open.
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

### 1.1b Merge `pre-pilot`'s PR — Founder
1. Before merging, do 3.7 (the session cookie's SameSite value) and check
   `CORS_ALLOW_ORIGINS` on the API service names exactly the web app's origin,
   with no trailing `/`, no localhost and no preview host: it is now also the
   list of origins allowed to write.
2. Merge once CI is green. No migration, and nothing new to set on Render.
3. After the deploy, on the deployed web app: sign in, add a client, run a
   scan, and on its report get a share link and withdraw it. Every write is
   refused with `origin-not-allowed` if `CORS_ALLOW_ORIGINS` is wrong.

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
| 3.2 | **The crawler needs an egress control.** **App-level guard done on `pre-pilot`** (`942fa26`): every fetch the crawler and the audit make refuses addresses that are not public, and a refusal ends the run as `ADDRESS_NOT_PUBLIC`. Still open: a network-level control, and the browser runs without Chromium's sandbox. | Founder picks the egress control; Claude (sec) wires it | The network-level fix is an egress proxy that refuses non-public addresses, used by both the browser and the HTTP client. Then decide on the sandbox. The details are in the founder's reports, not here. |
| 3.3 | **Confirm the Supabase Data API exposes nothing.** The app never uses it, and whether it is on with grants for `anon` / `authenticated` has not been checked. | Founder | In Supabase → SQL editor, run the two read-only queries in `~/Skills-library/supabase-ops/SKILL.md` (table grants for `anon`, `authenticated`, `PUBLIC`; `pg_default_acl`). If anything is granted, turn the Data API off (Settings → API) or remove `public` from the exposed schemas; revoking grants alone does not cover tables Alembic creates later. |
| 3.4 | **Set the Redis eviction policy.** Under an evicting policy, the index that lets a password change or reset sign a user out everywhere can be dropped silently. | Founder | Render → the Key Value instance → set the eviction policy to `noeviction`; note whether one instance serves sessions, rate limits and the Celery broker (`REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`); add a memory alert if Render offers one. |
| 3.5 | **SQL parameters can reach the logs.** `db.py` does not set `hide_parameters=True`, and `logger.exception` / `exc_info=True` (`google.callback.failed` in `routers/auth.py`; `scan.lease.renewal_failed`, `scan.chain.phase_failed`, `scan.chain.reload_failed`, `scan.execute.failed` and `scan.execute.failed_to_record` in `services/scan_executor.py`) render SQLAlchemy errors whose text includes bound parameters, such as an email. Unconfirmed at runtime. | Claude (sec) | `hide_parameters=True` on the engine; log `error=type(exc).__name__` on identity paths; add an exception-path case to `tests/test_secrets_stay_out_of_logs.py`. |
| 3.6 | **No error pages.** `apps/web` has no `error.tsx`, `global-error.tsx` or `not-found.tsx`; an unhandled API 500 carries no CORS headers, so the browser sees a network error rather than the problem body. | Claude + ui-reviewer | Add the three pages with design-system components and facts-only copy; make the web's fetch wrapper show a sentence for a non-`ApiProblem` failure. |
| 3.7 | **Confirm the production SameSite value (corrected on `pre-pilot`).** The web app (`ai-visibility-platform-1.onrender.com`) and the API (`ai-visibility-platform-5gk4.onrender.com`) are different sites, because `onrender.com` is a public suffix. So the session cookie is only stored and sent with `SameSite=None`; with the default `lax`, sign-in cannot stay signed in. Browsers that block third-party cookies (Safari by default) may still drop it. The Origin check (`0071640`) is now the main guard for signed-in writes. | Founder; then Claude | Render → API service → Environment: confirm `SESSION_COOKIE_SAMESITE=none` (if it is unset, sign in on the deployed app and say whether it stays signed in). The lasting fix is one registrable domain for both, e.g. `app.` and `api.` on the custom domain 2.3 needs. Then Claude sets the cookie back to `lax` and gives it the `__Host-` prefix. |
| 3.8 | **Done on `pre-pilot`** (`0562bfc`): "Links expire after 30 days." and a Withdraw link button with a confirm step. Was: **the share bar's copy is wrong.** `apps/web/src/components/report/ShareLinkBar.tsx:71-72` says the link "does not expire and cannot be withdrawn yet". It expires 30 days after it is created (`SHARE_TTL`, `services/share.py:101`; creating it again resets that), and the API can revoke it (`DELETE` in `routers/report.py:202-207`), though the web has no button for that. | Founder decides the wording (and whether to add a Withdraw button); Claude builds | One sentence, then optionally a Withdraw link button calling the existing route, with ui-reviewer. |
| 3.9 | **A JavaScript-running crawler that ignores `X-Robots-Tag` could read a leaked legacy `/share/<token>` report** now that robots.txt disallows nothing (founder decision 3, accepted). | Founder | Accept, or have Claude make `/share/[token]` wait for a click before loading the report. |
| 3.10 | **The token pages' headers have never been checked on a served response.** | Claude, after 1.1 | `curl -sI` the seven token paths on the web host (`/share`, `/share/x`, `/invite`, `/invite/x`, `/reset-password`, `/reset-password/x`, `/sign-up/google`): each must show `x-robots-tag: noindex, nofollow` and `referrer-policy: no-referrer`. |
| 3.11 | **Licence audit gap.** A Python package that declares GPL only as a classifier (`GNU General Public License v3 (GPLv3)`) lands in REVIEW, which does not fail CI, instead of BLOCK. None does today. | Claude | Extend `BLOCKING` in `apps/api/scripts/license_audit.py` to the classifier spellings, with tests. |
| 3.12 | **The scan allowance can read an outdated subscription status** under concurrent requests (`services/scan_allowance.py`, the locked re-select of the agency). | Claude (sec) | Refresh the row on the locked re-select (`populate_existing`), with a test. |
| 3.13 | **Stripe and Redis failures that answer 500.** Customer, Checkout Session and portal creation do not catch `StripeError` (only the existing-customer pre-check does), and a Redis out-of-memory error is not mapped, so these answer 500 instead of a 503 with a sentence. | Claude (sec for billing) | Map both to the existing 503 problem types, with tests. |
| 3.14 | **Migrations run in one transaction.** `infra/db/migrations/env.py` does not set `transaction_per_migration`, so a backfill shipped with DDL in one deploy would hold ACCESS EXCLUSIVE throughout. | Founder, when the first backfill is needed | Decide then; the `alembic-migrations` library skill describes the batched pattern. |
| 3.15 | **Done on `pre-pilot`** (`0562bfc`): the signed-in report and the dashboard say why, from a new `errorCode` field; the shared report does not. Was: **the web never says why a scan failed or is partial.** `errorCode` is in the API but no screen shows it, so a scan that ran out of time reads like an engine outage. | Founder decides the wording | Then Claude adds it to the scan screens with ui-reviewer. |
| 3.16 | **Done on `pre-pilot`** (`0562bfc`): "We could not reach the server" with Try again. Was: **the Google sign-up page said "expired" when the API did not answer**, which it cannot know (`apps/web/src/app/sign-up/google/page.tsx`). | Founder decides the wording | Claude adds a "could not check" state with ui-reviewer. |
| 3.17 | **Copy done on `pre-pilot`** (`0562bfc`); the contrast half is still open. Was: **Settings tells every signed-in agency "Your first scan is free…"** (`apps/web/src/components/settings/SettingsView.tsx:217`), which is wrong once the free scan is used; dark-theme `text-tertiary` on seated cards measures about 4.1:1. | Founder (copy); Claude (contrast token, ui-reviewer) | Decide the sentence; then fix both. |
| 3.18 | **Done on `pre-pilot`** (`0562bfc`; checkout is owner or admin, not owner-only). Was: **the scan-limit notice offered Subscribe where it could not work:** to an agency whose subscription needs attention (it then shows the refusal and a link to Billing) and to members (403). | Claude + ui-reviewer, after the founder's go | `useScanAllowance` already reads `GET /billing`; use it to show Manage billing instead, and nothing to members. |
| 3.19 | **Done on `pre-pilot`** (`63d4c81`): the founder chose to count a deadline partial only when at least half its planned prompts were answered, applied to every deadline. Was: **decide whether a scan that runs out of time because of load should count** (decision 7 as written counts it when it has at least one answer). The engine rate limits are shared across the product, so heavy load can push scans past the 40-minute deadline. | Founder | Keep it, or have Claude count a deadline-partial only above a minimum share of answered prompts (from the security review of decision 7). |
| 3.20 | **One step of the technical audit has no timeout of its own** (`services/technical_audit.py`). If it hangs, the scan runs into its deadline and alerts and fix generation do not run. | Claude | Bound it, so a hang becomes an ordinary failed audit phase. |
| 3.21 | **Writes from another site are refused** (not a row before): **Done on `pre-pilot`** (`0071640`). A write whose `Origin` is not in `CORS_ALLOW_ORIGINS` is refused, signed in or not; a signed-in write with no `Origin` too. Left: the webhook exemption covers every method on its path (only `POST` exists), and the refusal log does not name the origin. | Claude (sec) | Exempt only `POST` on the webhook path; optionally log the refused origin. |
| 3.22 | **The Google sign-in should bind its `state` to the browser that started it.** It predates `pre-pilot`; the callback is a `GET`, which the Origin check does not cover. | Claude (sec), after the founder's go | A short-lived cookie set at the start of the sign-in and compared at the callback, with tests. |
| 3.23 | **Four screens still tell people to check "port 8000"** when the API does not answer: sign-up, sign-in, the intake form and the intake result. | Claude + ui-reviewer | Route them through `lib/apiAway.ts` and the sentence the Google page uses. |
| 3.24 | **Sharing and failed scans.** A failed or partial scan can be shared with no warning, and a failed one shares as an almost empty report. A failed scan whose deadline came after scoring keeps its score, which the dashboard shows and counts. | Founder (product call) | Decide whether failed scans can be shared and whether a failed scan's score is shown; Claude builds it. |
| 3.25 | **A share link's 30 days restart each time "Get shareable link" is pressed**, and Withdraw is reached through that press. | Founder (wording) | Reword the lead, or have Claude offer Withdraw without making a link first (the delete route accepts a scan that was never shared). |
| 3.26 | **Report details that predate `pre-pilot`:** the failed-audit card prints the audit's error code, also on the public report; the PDF never says a scan was partial or failed; the global focus ring is about 1.7:1 on the light seated surface. | Claude + ui-reviewer | Plain sentences instead of the code; a line in the PDF; a focus token with at least 3:1. |
| 3.27 | **Crawler strictness and one residual from the egress review.** A page that loads anything from a private address ends the whole crawl (strict, as the brief asked). One kind of failed read in the connected-address check is treated as checked (details in the founder's report). | Founder (keep or relax); Claude (sec) | Decide on the strictness; the residual closes with 3.2's network-level control. |
