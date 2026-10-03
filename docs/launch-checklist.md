# Launch checklist

What stands between today's code and taking money from agencies. Each item says
who does it and the exact steps. **Founder** means only the founder can do it
(an account, a dashboard, a legal or product decision). **Claude** means an agent
session can do it, on a branch, with the full bar and a build-log entry, and
only after the founder says go. Nothing here is pushed to `main` without the
founder's OK; Render deploys `main` before CI finishes.

Status on 2026-10-03: PRs #2 to #6 are merged (`main` at `811f013`). This
checklist was last updated on branch `pilot-fixes-3` (the founder's brief "honest
reports and the remaining pilot decisions"), whose PR targets `main`. Rows that
branch closed say **Done** and name the commit; its merge steps are 1.1e.

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

### 1.1b Merge `pre-pilot`'s PR — Founder — **Merged 2026-10-01** (PR #4, `main` at `f1b7565`)
Kept for the record. If step 3 was not run, 1.1c's step 7 covers it.
1. Before merging, do 3.7 (the session cookie's SameSite value) and check
   `CORS_ALLOW_ORIGINS` on the API service names exactly the web app's origin,
   with no trailing `/`, no localhost and no preview host: it is now also the
   list of origins allowed to write.
2. Merge once CI is green. No migration, and nothing new to set on Render.
3. After the deploy, on the deployed web app: sign in, add a client, run a
   scan, and on its report get a share link and withdraw it. Every write is
   refused with `origin-not-allowed` if `CORS_ALLOW_ORIGINS` is wrong.

### 1.1c Merge `pilot-fixes`'s PR — Founder — **Merged 2026-10-02** (PR #5, `main` at `471a2b9`)
The web app now serves the API under its own origin (3.7) and Google sign-in is
bound to the browser (3.22). No migration. Kept for the record: steps 5 to 9 are the
founder's to confirm if not yet done.

Before merging:
1. Render → **API** service → Connect → **Internal**: copy the internal
   address (`http://<name>:<port>`). If Render shows none (a free instance, or
   the web app and the API in different regions), stop and tell Claude. Do not
   use the API's public `onrender.com` URL: every visitor would share one
   rate-limit bucket, and the web build refuses it.
2. Render → **web** service → Environment → add `API_PROXY_TARGET` = that
   address. Save (Render redeploys today's `main`, which ignores it).
3. Google Cloud Console → APIs & Services → Credentials → the OAuth client →
   Authorised redirect URIs → **add**
   `https://ai-visibility-platform-1.onrender.com/api/v1/auth/google/callback`.
   Keep the API-host one for now.
4. Merge at a quiet moment. Between the API's deploy and the web's nobody can
   sign in, and everyone is signed out once (the cookie's name changes).

After both deploys are live:
5. Render → **API** service → Environment → set `GOOGLE_OAUTH_REDIRECT_URL` to
   the address in step 3. Save. Until this, Google sign-in ends "could not be
   matched to this browser".
6. Delete, if present: `SESSION_COOKIE_SAMESITE` and `SESSION_COOKIE_DOMAIN` on
   the API service, `NEXT_PUBLIC_API_BASE_URL` on the web service (all three are
   ignored now).
7. On the deployed web app, in **Chrome and in Safari**: sign in with a
   password, sign out, sign in with Google; DevTools → Application → Cookies →
   the web host shows `__Host-avp_session`; add a client, run a scan, get a
   share link, reload the report and see "The link works until …" with
   Withdraw, then withdraw it.
8. Run 1.3 (it now goes through the web host).
9. Google Cloud Console: remove the old API-host redirect URI.

### 1.1d Merge `pilot-fixes-2`'s PR — Founder — **Merged 2026-10-03** (PR #6, `main` at `811f013`)
Nothing to set on Render and no migration. The API's image changes (`tini` becomes
its first process), so its deploy rebuilds it.

1. Merge once CI is green. The API and the web app deploy from the same push. For
   the minutes between them, the share bar may already say that a failed scan's link
   no longer opens while the old API still serves it (3.29); nothing else depends on
   the order.
2. Render → **API** service → Logs, after the deploy: one `app.memory_at_start` line
   (the baseline), and no `UnsupportedFieldAttributeWarning` after the first requests
   (M2).
3. Run one scan, then search the logs for its `scan.phase_memory` lines. The `audit`
   line's `cgroup_peak_mb` is the peak of a full scan on Standard, the number the
   brief left as "[FILL IN]". `docs/runbook.md`, "Reading the API's memory", says how
   to read each figure.
4. Do not set `WEB_CONCURRENCY` on the API: the entrypoint pins one worker (the
   browser slot is per process), so it would be ignored.
5. On the deployed app: get a share link (the bar says "Link ready. It works until
   …" and the link is selected); reload, press Get again: the same date (3.28).
   After 1.1e that sentence is announced to screen readers and no longer shown
   (3.38): step 6 there says what to look and listen for.

### 1.1e Merge `pilot-fixes-3`'s PR — Founder
Nothing to set on Render: no migration, no new dependency, outbound host or
setting. Both services redeploy from the same push.

1. Merge once CI is green. For the minutes between the two deploys, an old web app
   prints a new `ROBOTS_UNREACHABLE` intake reason as the code itself (3.41, 3.39);
   nothing else depends on the order.
2. Render → **API** service → Logs: open one report twice (signed in, then its
   share link). No `scoring.completed` line appears for either (R1, 3.44); one
   appears only when a scan is scored.
3. Run one scan. If a competitor search fails or is retried, its line is
   `serp.attempts_failed` or `serp.retried`, with `kinds` saying how (`connect`,
   `read`, `pool`, `total`); no line carries a URL or `api_key` (R2, 3.45). Both
   queries of the production scan timing out on one run is what to compare
   against.
4. In the same scan's lines: a `browser.slot_busy` with `waited_ms` near 120000
   is a scan's audit that queued behind a run that hung (3.35); `docs/runbook.md`,
   "Reading the API's memory", item 6.
5. On the deployed app: add a client whose robots.txt cannot be read (a host that
   does not answer on `/robots.txt`): the card says the crawler did not get a usable
   answer from it, not that the site asks crawlers to stay out, and prints no code
   (3.41, 3.39).
6. Get a share link. The date shows once (the standing line), and the link field
   is focused and selected. With VoiceOver (macOS) or NVDA (Windows) on, "Link
   ready. It works until …" is announced (3.38). Some screen readers drop a polite
   message when focus moves in the same moment; the date is still on the standing
   line.
7. Dashboard: with one live link, the "Share a report" step says "One live
   link."; withdraw it and reload: the step is no longer done (3.42).

### 1.2 `STRIPE_PRICE_ID` is the US$160 Price — Founder
1. Stripe dashboard (Test mode until 2.2 is done) → Product catalogue → the
   product with a recurring monthly Price of US$160. Copy its id (`price_…`).
2. Render → API service (`ai-visibility-platform-5gk4`) → Environment → set
   `STRIPE_PRICE_ID` to it. Save (Render redeploys).
3. Done when Settings → Subscribe opens a Stripe checkout page showing US$160
   per month.

### 1.3 Confirm how Render passes the client IP — Founder, after 1.1c
Run `docs/runbook.md` → "Confirm how Render passes the client IP (rate
limits)" from your own machine. It sends 11 password-reset requests with
made-up addresses to the **web** host, the path every browser call takes since
3.7 (email is off, nothing is sent).
- 11th answers 429: send one more from a **different network** (a phone
  hotspot). 200: done, nothing to set. 429: every visitor is sharing one
  bucket; check `API_PROXY_TARGET` is the API's internal address (1.1c step 1).
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
| 3.2 | **The crawler needs an egress control.** **App-level guard done on `pre-pilot`** (`942fa26`): every fetch the crawler and the audit make refuses addresses that are not public, and a refusal ends the run as `ADDRESS_NOT_PUBLIC`. Still open: a network-level control, and the browser runs without Chromium's sandbox. Found on `pilot-fixes`: a page's speculation-rules **prerender** is fetched by Chromium outside Playwright's routing and reporting, so the guard never sees it (no launch switch stops it without breaking interception); shared workers had the same gap and are now disabled (`57c3b2c`). The network-level control closes this class. Moving the browser work into a Render background worker (a design note, not built: `docs/crawl-worker-design-note.md`, with M1's numbers in 3.34) would leave only that service needing to reach arbitrary sites, so the control would apply to it alone. | Founder picks the egress control; Claude (sec) wires it | The network-level fix is an egress proxy that refuses non-public addresses, used by both the browser and the HTTP client. Then decide on the sandbox. The details are in the founder's reports, not here. |
| 3.3 | **Confirm the Supabase Data API exposes nothing.** The app never uses it, and whether it is on with grants for `anon` / `authenticated` has not been checked. | Founder | In Supabase → SQL editor, run the two read-only queries in `~/Skills-library/supabase-ops/SKILL.md` (table grants for `anon`, `authenticated`, `PUBLIC`; `pg_default_acl`). If anything is granted, turn the Data API off (Settings → API) or remove `public` from the exposed schemas; revoking grants alone does not cover tables Alembic creates later. |
| 3.4 | **Set the Redis eviction policy.** Under an evicting policy, the index that lets a password change or reset sign a user out everywhere can be dropped silently. | Founder | Render → the Key Value instance → set the eviction policy to `noeviction`; note whether one instance serves sessions, rate limits and the Celery broker (`REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`); add a memory alert if Render offers one. |
| 3.5 | **SQL parameters can reach the logs.** `db.py` does not set `hide_parameters=True`, and `logger.exception` / `exc_info=True` (`google.callback.failed` in `routers/auth.py`; `scan.lease.renewal_failed`, `scan.chain.phase_failed`, `scan.chain.reload_failed`, `scan.execute.failed` and `scan.execute.failed_to_record` in `services/scan_executor.py`) render SQLAlchemy errors whose text includes bound parameters, such as an email. Unconfirmed at runtime. | Claude (sec) | `hide_parameters=True` on the engine; log `error=type(exc).__name__` on identity paths; add an exception-path case to `tests/test_secrets_stay_out_of_logs.py`. |
| 3.6 | **No error pages.** `apps/web` has no `error.tsx`, `global-error.tsx` or `not-found.tsx`; an unhandled API 500 carries no CORS headers, so the browser sees a network error rather than the problem body. | Claude + ui-reviewer | Add the three pages with design-system components and facts-only copy; make the web's fetch wrapper show a sentence for a non-`ApiProblem` failure. |
| 3.7 | **Done on `pilot-fixes`** (`e39a2c2`): the web app serves the API under its own origin (`/api/v1`, a Next.js rewrite to `API_PROXY_TARGET`), so the session cookie is first-party: `__Host-avp_session`, SameSite=Lax, Path=/, no Domain; logout now really clears it; Next's proxy logs drop queries and report tokens. The probe the founder asked for passed (Render runs `next start`; the browser's Origin reaches the API through the proxy in Chromium, Firefox and WebKit). Was: the web app and the API are different sites, so the cookie needed SameSite=None and Safari could drop it. | Founder | The Render and Google steps are in 1.1c, in order. |
| 3.8 | **Done on `pre-pilot`** (`0562bfc`): "Links expire after 30 days." and a Withdraw link button with a confirm step. Was: **the share bar's copy is wrong.** `apps/web/src/components/report/ShareLinkBar.tsx:71-72` says the link "does not expire and cannot be withdrawn yet". It expires 30 days after it is created (`SHARE_TTL`, `services/share.py:101`; creating it again resets that), and the API can revoke it (`DELETE` in `routers/report.py:202-207`), though the web has no button for that. | Founder decides the wording (and whether to add a Withdraw button); Claude builds | One sentence, then optionally a Withdraw link button calling the existing route, with ui-reviewer. |
| 3.9 | **A JavaScript-running crawler that ignores `X-Robots-Tag` could read a leaked legacy `/share/<token>` report** now that robots.txt disallows nothing (founder decision 3, accepted). | Founder | Accept, or have Claude make `/share/[token]` wait for a click before loading the report. |
| 3.10 | **The token pages' headers have never been checked on a served response.** | Claude, after 1.1 | `curl -sI` the seven token paths on the web host (`/share`, `/share/x`, `/invite`, `/invite/x`, `/reset-password`, `/reset-password/x`, `/sign-up/google`): each must show `x-robots-tag: noindex, nofollow` and `referrer-policy: no-referrer`. |
| 3.11 | **Licence audit gap.** A Python package that declares GPL only as a classifier (`GNU General Public License v3 (GPLv3)`) lands in REVIEW, which does not fail CI, instead of BLOCK. None does today. | Claude | Extend `BLOCKING` in `apps/api/scripts/license_audit.py` to the classifier spellings, with tests. |
| 3.12 | **The scan allowance can read an outdated subscription status** under concurrent requests (`services/scan_allowance.py`, the locked re-select of the agency). | Claude (sec) | Refresh the row on the locked re-select (`populate_existing`), with a test. |
| 3.13 | **Stripe and Redis failures that answer 500.** Customer, Checkout Session and portal creation do not catch `StripeError` (only the existing-customer pre-check does), and a Redis out-of-memory error is not mapped, so these answer 500 instead of a 503 with a sentence. | Claude (sec for billing) | Map both to the existing 503 problem types, with tests. |
| 3.14 | **Migrations run in one transaction.** `infra/db/migrations/env.py` does not set `transaction_per_migration`, so a backfill shipped with DDL in one deploy would hold ACCESS EXCLUSIVE throughout. | Founder, when the first backfill is needed | Decide then; the `alembic-migrations` library skill describes the batched pattern. |
| 3.15 | **Done on `pre-pilot`** (`0562bfc`): the signed-in report and the dashboard say why, from a new `errorCode` field; the shared report does not. Was: **the web never says why a scan failed or is partial.** `errorCode` is in the API but no screen shows it, so a scan that ran out of time reads like an engine outage. | Founder decides the wording | Then Claude adds it to the scan screens with ui-reviewer. |
| 3.16 | **Done on `pre-pilot`** (`0562bfc`): "We could not reach the server" with Try again. Was: **the Google sign-up page said "expired" when the API did not answer**, which it cannot know (`apps/web/src/app/sign-up/google/page.tsx`). | Founder decides the wording | Claude adds a "could not check" state with ui-reviewer. |
| 3.17 | **Done:** the copy on `pre-pilot` (`0562bfc`); the contrast on `pilot-fixes` (`8e5ea01`): `text-tertiary` is at least 4.5:1 on every surface text sits on, at rest and under the hover wash, in both themes, held by a token contrast test. The report's own tertiary text is not covered (3.31). | — | — |
| 3.18 | **Done on `pre-pilot`** (`0562bfc`; checkout is owner or admin, not owner-only). Was: **the scan-limit notice offered Subscribe where it could not work:** to an agency whose subscription needs attention (it then shows the refusal and a link to Billing) and to members (403). | Claude + ui-reviewer, after the founder's go | `useScanAllowance` already reads `GET /billing`; use it to show Manage billing instead, and nothing to members. |
| 3.19 | **Done on `pre-pilot`** (`63d4c81`): the founder chose to count a deadline partial only when at least half its planned prompts were answered, applied to every deadline. Was: **decide whether a scan that runs out of time because of load should count** (decision 7 as written counts it when it has at least one answer). The engine rate limits are shared across the product, so heavy load can push scans past the 40-minute deadline. | Founder | Keep it, or have Claude count a deadline-partial only above a minimum share of answered prompts (from the security review of decision 7). |
| 3.20 | **Done on `pilot-fixes-2`** (`1b6f109`, M1): every browser run is under a cap (the audit 244.5 s, the intake's crawl 266 s), so a step that never returns ends the run as `TIMEOUT` and the scan goes on. Was: one step of the technical audit had no timeout of its own, so a hang ran into the scan's deadline and alerts and fixes did not run. | — | — |
| 3.21 | **Done on `pilot-fixes`** (`0e4ba12`): only a POST to the Stripe webhook skips the Origin check; a refused write logs the requesting origin's host name, strictly parsed (an IP, `null` or anything malformed is logged only as a kind). Writes from another site were already refused (`0071640`). | — | — |
| 3.22 | **Done on `pilot-fixes`** (`94378fe`): a Google sign-in, and the new-address sign-up ticket, can only be finished in the browser that started it (a `__Host-avp_google_flow` cookie compared at the callback, `pending` and `complete`). Two Google sign-ins in two tabs of one browser end with the earlier refused (fails safe). | Founder | 1.1c steps 3, 5 and 9 (the callback must be on the web host). |
| 3.23 | **Done on `pilot-fixes`** (`8c23ac5`): sign-up, sign-in, the intake form and the intake result say "We could not reach the server. Check your connection and try again." | — | — |
| 3.24 | **Done on `pilot-fixes`** (`5090bee`): a failed scan cannot be shared (`409 failed-scan-not-shareable`; the share bar says why), and its score is never shown or counted on the dashboard or the report, the PDFs included. Partial scans stay shareable. Open decisions: 3.29, 3.30. | — | — |
| 3.25 | **Done on `pilot-fixes`** (`fd79b6e`): Withdraw is offered whenever a link is live, without making one first; the bar shows when the link stops working; the lead says links last 30 days from when they are created. Open decision: 3.28. | — | — |
| 3.26 | **Done on `pilot-fixes`** (`dcfff6b`): the failed-audit card says what happened in a sentence instead of the code (signed-in and public); the PDF cover says when a scan was partial or failed; the light and report focus ring is a solid ring at least 3:1 on every surface. | — | — |
| 3.27 | **Done on `pilot-fixes`** (`57c3b2c`): a request the crawler refuses before sending it (a page's image, script, iframe; the sitemap) is skipped and the crawl goes on; anything that reached a private address (Chromium's connected address, a navigation's redirect, a sub-resource's redirect hop) still ends the run and keeps nothing. The residual from the egress review, and the prerender gap, close with 3.2. | Founder | 3.2. |
| 3.28 | **Done on `pilot-fixes-2`** (`4ad393a`): a press of "Get shareable link" on a live link returns it as it is, the same address and end; with no live link (none, withdrawn or ended) it makes a new one for 30 days, and an ended address stays dead. The lead's "30 days from when they are created" is now exact. Was: a press moved a live link's end to 30 days from the press. | — | — |
| 3.29 | **Done on `pilot-fixes-2`** (`5bd3cc1`, a test fixed in `55d7f94`): the public report and its PDF refuse a failed scan's link exactly as a withdrawn one; the token stays so the agency can withdraw it, and the share bar says the link no longer opens. Was: a link minted while a scan was running kept serving after it failed. | — | — |
| 3.30 | **Done on `pilot-fixes-2`** (`d772a19`): `GET /scans/{id}/score` is a 404, `/scores` is `[]` and `POST …/score` a 409 for a failed scan; no visibility alert is made from a failed scan and one written earlier is not listed; nothing the fix generator wrote is served for a failed scan (its input still had the score, the founder's decision); its audit shows no Technical Foundation. No row deleted or re-scored. Open: 3.37. | — | — |
| 3.31 | **The report's own tertiary text** (ink-400 on paper) measures 3.2–3.6:1, under 4.5:1. | Founder | Darken it (the printed report's look changes) or leave it. |
| 3.32 | **Done on `pilot-fixes-2`** (`5189273`): the client Technical screen says what happened to a failed audit in a sentence, not the code. Open: the other raw codes, 3.39. | — | — |
| 3.33 | **Done on `pilot-fixes-2`** (`72797ae`): after "Get shareable link" the status region says "Link ready. It works until <date>." and focus is on the link, text selected; a failed mint gives focus back to Get, described by its error. A focused primary or danger button in the light theme, and the report's, keeps a 2px gap between its fill and the ring. Open: 3.38, 3.40. | — | — |
| 3.34 | **API memory (M1). Done on `pilot-fixes-2`** (`1b6f109`): one browser per process at a time, each run capped and its wait bounded, one site read per agency and four agencies at once on the request routes (`429`), `tini` as PID 1, and memory in the logs per phase (`docs/runbook.md`, "Reading the API's memory"). Measured in the API's image: the idle server 97 MB; a heavy page's audit about 490 MB; a crawl and an audit at once 586 MB before, about 490 MB after (at 512 MB, before, the kernel killed the audit's browser); about 1.4 GB free on 2 GB at the worst moment. The resting rise after a deploy is page cache, which the kernel drops. Moving the browser to a worker: `docs/crawl-worker-design-note.md` (not built). | Founder | 1.1d steps 2 and 3 (the peak of a full scan on Standard); decide on the worker. |
| 3.35 | **Done on `pilot-fixes-3`** (`6c06790`): a scan's audit waits 120 s for the browser (it was 300 s), enough for a run ahead that finishes and not for one that hangs until its cap; the worst-case scan is 2,064.5 s of the 2,400 s deadline (×1.16; ×1.07 at 300 s with 3.41's pre-browser budget), stated in `scan_executor.py` and checked by its test. Was: 2,202.5 s (1.09×). | — | — |
| 3.36 | **Done on `pilot-fixes-3`** (`111045d`): the final status is written only while the row is still RUNNING; a chain that finds its scan failed stops before the engine loop; an executor that never claimed a scan can fail only a queued one. Was: the reaper's FAILED could be overwritten within one heartbeat, and a failed scan's link opened again. | — | — |
| 3.37 | **Done on `pilot-fixes-3`** (`9d25c0a`): a failed scan's report carries no standings (`withhold_score_like_proof` in `failed_scan.py`), and a test classifies every number the report can carry as withheld or a fact. Confirmed before the change: the standings rebuilt the withheld Sentiment (54.5464 against 54.55). Open: 3.47. | — | — |
| 3.38 | **Done on `pilot-fixes-3`** (`6c3d981`): after a mint the date shows once; "Link ready. It works until …" stays in the status region for screen readers, visually hidden. Open: the lead and busy label for a link live on load, 3.52. | — | — |
| 3.39 | **Done on `pilot-fixes-3`** (`504b9e2`): the intake card, the check rows and the engine cards say what went wrong in a sentence true of the case, never the code; our-side failures never say the site did not load; "Could not classify" on the card and the Clients list; the 429 is above the form; "A scan usually takes a few minutes." on all four screens that promised five. Open: 3.48, 3.49. | — | — |
| 3.40 | **Focus visibility gaps found in 3.33:** in forced colours no non-filled control shows focus (a shadow with `outline: none`), and Firefox draws its own ring on them; a focused text field shows a ring inside a ring; the dark theme's ring against a primary fill is 1.8–2.0:1 (its outer edge passes); every primary button has a 1px rim of the gradient's other end. | Founder (go), then Claude + ui-reviewer | One transparent outline on the global focus rule; one line on `.avp-field__input:focus-visible`; the dark ring and the rim. |
| 3.41 | **Done on `pilot-fixes-3`** (`24a139f`): an unreadable robots.txt is `ROBOTS_UNREACHABLE` (Technical Foundation excluded as `CRAWL_POLICY_UNREADABLE`, `FORMULA_VERSION` still v5) and every screen says the crawler did not get a usable answer; the crawler still reads nothing without one. The gate now reads within a size, a time per request and a 60 s budget before the browser, and decodes only a short list of text charsets. Open: 3.46, 3.50. | — | — |
| 3.42 | **Done on `pilot-fixes-3`** (`eac7a97`): the step counts only links that would open, by the one rule the public report uses (`share.link_would_open`); cancelled scans stay counted, as the public report still serves them. Open: 3.51. | — | — |
| 3.43 | **Smaller follow-ups.** The FastAPI cap (`<0.118`) also holds Starlette below 0.49: lift it, with a review, before the API serves files. The fix generator is told a failed audit's empty page fields. Two wall-clock tests (`test_call_bounds.py`, `test_scan_deadline.py`) fail under heavy CPU load. A 429 from the browser limits has no log line of its own. | Claude | Each its own small item. |
| 3.44 | **Done on `pilot-fixes-3`** (`3303deb`, item R1): opening a report, its PDF or a score reads the stored score and no longer re-scores the scan; `scoring.completed` is logged only when a Score is saved. No read ever wrote (checked row by row, `xmin`/`xmax` included); a report view went from 22 to 21 statements. | — | — |
| 3.45 | **Done on `pilot-fixes-3`** (`652ba9b`, item R2): a competitor search has a 10 s deadline per attempt and one retry on a new connection for a transport failure (21 s at most per query); a failure logs its kind; no logger can print the search key. Measured from here: 24 real searches, median 1.54 s, longest 6.93 s. | — | — |
| 3.46 | **Stored rows from before 3.41 keep `ROBOTS_DISALLOWED`** for a robots.txt that was really unreadable, so their report, Technical screen and intake card say the site asks crawlers to stay out. Nothing recorded which they were; a failed audit's AI-crawler table (all unknown) identifies the cases where it was the site's own file. | Founder | Have Claude relabel those, or accept. |
| 3.47 | **A failed scan's facts on the signed-in scan routes** (3.37's security review, Medium): `GET /scans/{scanId}`, `/results` and `/prompts` serve every input of Mention Rate, Share of Voice and Sentiment (intents, mention flags, sentiment labels, citations), and `/audit` every fact Technical Foundation is computed from, so the withheld sub-scores can be worked out. No web screen calls these routes. | Founder | Keep them as facts (as now documented), or have Claude withhold the per-answer sentiment labels for a failed scan (closes Sentiment only). |
| 3.48 | **The business-entity check knows eight schema.org types** (Organization, Corporation, OnlineBusiness, LocalBusiness, Dentist, Restaurant, Store, MedicalBusiness), so a site marked up as `ProfessionalService`, `LegalService` or `Plumber` fails it and loses Technical Foundation points. | Founder | Widen it (a `FORMULA_VERSION` bump; history is not re-scored) or keep. |
| 3.49 | **The report's fix list and the PDF still overstate three findings:** `NO_ORGANIZATION_OR_LOCALBUSINESS` ("Nothing on the page states … what this organisation is"), `NO_DATE_SIGNAL_AVAILABLE` ("anywhere on the page"; the audit reads only the Last-Modified header and the structured data's dates) and `ROBOTS_TXT_DISALLOW` ("nothing else on this list can take effect"). The Technical screen already says them truly (3.39). | Founder (go), then Claude + ui-reviewer | Reword the three in the fix table and `report_narrative.py` together, with the narrative fixture. |
| 3.50 | **A robots.txt or sitemap in a legacy non-Latin charset** (Shift_JIS, EUC-JP, GBK, Big5, EUC-KR, KOI8-R, DOS or Mac code pages) is now "no answer", so such a site is not read (3.41's allow-list). Those decoders measured linear and cheap. Also: the `/crawler` page does not state the 10 s and 60 s limits. | Founder | Allow them if a pilot needs one (one line, with a test); the `/crawler` wording with the lawyer (2.4). |
| 3.51 | **The dashboard's "Share a report" step goes dark again** when its only link ends or its scan fails (3.42 counts only links that would open). | Founder | Keep, or have it stay done once an agency has shared a report. |
| 3.52 | **The share bar for a link live on load** still says "Creates a link…" and "Creating…", though a press returns the existing link (3.28); Copy → "Copied" changes only the button's name, which screen readers do not reliably announce. | Founder (go), then Claude + ui-reviewer | Word the lead and busy label for a live link; a visually hidden status for Copy. |
| 3.53 | **`POST /clients/{clientId}/competitors/detect` has no allowance or rate limit** (R2): every call runs a fresh detection, 6 searches from the shared 250 a month, up to 12 with retries. | Founder | A limit (per agency per day, or only through a scan), then Claude (sec). |
| 3.54 | **`docs/skills-audit-2026-09-30/`** is untracked in the main checkout (the skills audit of 2026-09-30) and has been left untouched. | Founder | Commit it, move it out of the repo, or delete it. |
