# Launch checklist

What stands between today's code and taking money from agencies. Each item says
who does it and the exact steps. **Founder** means only the founder can do it
(an account, a dashboard, a legal or product decision). **Claude** means an agent
session can do it, on a branch, with the full bar and a build-log entry, and
only after the founder says go. Nothing here is pushed to `main` without the
founder's OK; Render deploys `main` before CI finishes.

Status on 2026-10-03: PRs #2 to #7 are merged (`main` at `942115a`). This
checklist was last updated on branch `pilot-fixes-4` (the founder's brief "search
accuracy, guardrails and pilot hardening"), whose PR targets `main`. Rows that
branch closed say **Done** and name the commit; its merge steps are 1.1f.

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

### 1.1e Merge `pilot-fixes-3`'s PR — Founder — **Merged 2026-10-03** (PR #7, `main` at `942115a`)
Kept for the record; steps 2 to 7 are the founder's to confirm if not yet done. Step 5
is now an automated test (T1, `aff4641`), and steps 6 and 7 were checked in a browser on
a local production build (3.52, `9760e1a`).

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

### 1.1f Merge `pilot-fixes-4`'s PR — Founder
Nothing to set on Render. One migration, `f45983cfa5e3` (D1), runs in the API container
before it serves, under `lock_timeout = '5s'`: in Supabase it removes the Data API roles'
grants; where those roles do not exist it does nothing. The API's image changes (its start
line drops the `Server` header).

Before merging: in Supabase's SQL editor, run the "Before the deploy" query in
`docs/runbook.md`, "Supabase grants check", and confirm that the user part of
`DATABASE_URL` is `postgres` (or `postgres.<project-ref>` through the pooler), without
pasting the URL anywhere. Expect no rows. An object it lists is not AVP's: the migration
skips it, and the checks in step 3 show what it holds.

1. Merge once CI is green. Both services deploy from the same push; nothing depends on
   their order.
2. Render → **API** service → Logs: `entrypoint: alembic upgrade head`, then `entrypoint:
   serving on :<port>` (as in 1.1 step 4). Then `redis.eviction_policy` with
   `policy=noeviction` (or `unknown` if Render refuses the command), and ONE
   `redis.data_lost`: expected at the first start, because the sentinel key is new. After
   that, a `redis.data_lost` means Redis lost its data, 3.4's trigger to upgrade
   (`docs/runbook.md`, "Reading the Redis lines").
3. Supabase → SQL editor: run the three read-only queries in `docs/runbook.md`, "Supabase
   grants check". Query 2 should return no rows, and query 3 no row for owner `postgres`
   whose schema is `public` or "(every schema)". If query 1 returns any rows (functions in
   `public` the two roles can run), send them to Claude.
4. Headers, from your own machine: `curl -sS -D - -o /dev/null
   https://ai-visibility-platform-1.onrender.com/`, then the same for `/share/x` and
   `/api/v1/health`. Expect 3.1's headers once each; on `/share/x`, `referrer-policy:
   no-referrer` and `x-robots-tag: noindex, nofollow` (this closes 3.10); and no `server`
   or `x-powered-by` line. (`curl -I` sends HEAD, which the API's routes answer with 405.)
5. Run one scan on a new client. Each of its searches logs `serp.completed` (`elapsed_ms`,
   `attempts`, `hits`, `status`, `error_code`), and `competitors.detected` carries
   `country` (`au` for a `.com.au` or a `.com`, `in` for a `.in`). Keep the
   `serp.completed` lines from the pilot's first week: `docs/runbook.md`, "Reading
   competitor-search lines", says what they would justify.
6. The full content security policy is report-only. With the browser console open: sign
   in with Google, open a report, get and copy its share link, download its PDF, press
   Subscribe (Checkout) and Manage billing. If no console line says "[Report Only]", tell
   Claude, who switches the policy to enforced (3.1).
7. With VoiceOver (macOS) or NVDA (Windows) on: on a report with a live link, the lead
   says "This report has a live link…", Get says "Getting the link…", Copy says "Link
   copied." on every press without reading "Link ready…" again, and withdrawing
   announces "Link withdrawn…" and not the lead (3.52).

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
   `customer.subscription.updated`, `customer.subscription.deleted` and
   `customer.subscription.trial_will_end` (the fifth only writes a log line); set the
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
| 3.1 | **Done on `pilot-fixes-4`** (`f6e232d`): every web page and every API response carries HSTS (two years, `includeSubDomains`, no `preload`), `nosniff`, a Referrer-Policy (`strict-origin-when-cross-origin` on the web; `no-referrer` on the API and the token pages), `X-Frame-Options: DENY` with an enforced `frame-ancestors 'none'`, and a Permissions-Policy. The full content security policy is report-only: a local production build showed no violation in 17 page loads, but Google sign-in, Stripe and signed-in pages could not be exercised. Neither host names its server any more. Open: enforcing the policy, and 3.68. | Founder | 1.1f steps 4 and 6. |
| 3.2 | **Founder, 2026-10-03: after the pilots.** **The crawler needs an egress control.** **App-level guard done on `pre-pilot`** (`942fa26`): every fetch the crawler and the audit make refuses addresses that are not public, and a refusal ends the run as `ADDRESS_NOT_PUBLIC`. Still open: a network-level control, and the browser runs without Chromium's sandbox. Found on `pilot-fixes`: a page's speculation-rules **prerender** is fetched by Chromium outside Playwright's routing and reporting, so the guard never sees it (no launch switch stops it without breaking interception); shared workers had the same gap and are now disabled (`57c3b2c`). The network-level control closes this class. Moving the browser work into a Render background worker (a design note, not built: `docs/crawl-worker-design-note.md`, with M1's numbers in 3.34) would leave only that service needing to reach arbitrary sites, so the control would apply to it alone. | Founder picks the egress control; Claude (sec) wires it | The network-level fix is an egress proxy that refuses non-public addresses, used by both the browser and the HTTP client. Then decide on the sandbox. The details are in the founder's reports, not here. |
| 3.3 | **Done:** the founder turned the Supabase Data API off (2026-10-03; `anon` and `authenticated` then held REFERENCES, TRIGGER, TRUNCATE and MAINTAIN on all 25 public tables, with RLS on). On `pilot-fixes-4`, D1 (`c247ef9`) revokes both roles' grants on every table, sequence and routine in `public` and `postgres`'s default privileges for them, so tables Alembic adds later are not exposed either. | Founder | 1.1f step 3. |
| 3.4 | **Done** (founder, 2026-10-03): eviction is `noeviction`. The instance is Render Key Value Free (25 MB, about 10% used, nothing kept on disk), one instance for sessions, rate limits and the configured Celery broker and results; changing the eviction setting restarted it and signed everyone out. A restart wipes sessions, rate-limit windows, Google sign-ins in flight and detection days. K1 found it cannot lose a scan: scans run in the API's process and never touch Redis (an API restart can lose one; it fails after 15 minutes as `EXECUTOR_NEVER_STARTED`). Since K1 (`b3cdfb2`) the API logs the policy at start and `redis.data_lost` when the data is gone. Upgrade during the pilot when `redis.data_lost` appears or memory passes 50%; do not change Key Value settings while pilots are signed in. | Founder | Watch for `redis.data_lost` and the memory figure. |
| 3.5 | **SQL parameters can reach the logs.** `db.py` does not set `hide_parameters=True`, and `logger.exception` / `exc_info=True` (`google.callback.failed` in `routers/auth.py`; `scan.lease.renewal_failed`, `scan.chain.phase_failed`, `scan.chain.reload_failed`, `scan.execute.failed` and `scan.execute.failed_to_record` in `services/scan_executor.py`) render SQLAlchemy errors whose text includes bound parameters, such as an email. Unconfirmed at runtime. | Claude (sec) | `hide_parameters=True` on the engine; log `error=type(exc).__name__` on identity paths; add an exception-path case to `tests/test_secrets_stay_out_of_logs.py`. |
| 3.6 | **No error pages.** `apps/web` has no `error.tsx`, `global-error.tsx` or `not-found.tsx`; an unhandled API 500 carries no CORS headers, so the browser sees a network error rather than the problem body. | Claude + ui-reviewer | Add the three pages with design-system components and facts-only copy; make the web's fetch wrapper show a sentence for a non-`ApiProblem` failure. |
| 3.7 | **Done on `pilot-fixes`** (`e39a2c2`): the web app serves the API under its own origin (`/api/v1`, a Next.js rewrite to `API_PROXY_TARGET`), so the session cookie is first-party: `__Host-avp_session`, SameSite=Lax, Path=/, no Domain; logout now really clears it; Next's proxy logs drop queries and report tokens. The probe the founder asked for passed (Render runs `next start`; the browser's Origin reaches the API through the proxy in Chromium, Firefox and WebKit). Was: the web app and the API are different sites, so the cookie needed SameSite=None and Safari could drop it. | Founder | The Render and Google steps are in 1.1c, in order. |
| 3.8 | **Done on `pre-pilot`** (`0562bfc`): "Links expire after 30 days." and a Withdraw link button with a confirm step. Was: **the share bar's copy is wrong.** `apps/web/src/components/report/ShareLinkBar.tsx:71-72` says the link "does not expire and cannot be withdrawn yet". It expires 30 days after it is created (`SHARE_TTL`, `services/share.py:101`; creating it again resets that), and the API can revoke it (`DELETE` in `routers/report.py:202-207`), though the web has no button for that. | Founder decides the wording (and whether to add a Withdraw button); Claude builds | One sentence, then optionally a Withdraw link button calling the existing route, with ui-reviewer. |
| 3.9 | **A JavaScript-running crawler that ignores `X-Robots-Tag` could read a leaked legacy `/share/<token>` report** now that robots.txt disallows nothing (founder decision 3, accepted). | Founder | Accept, or have Claude make `/share/[token]` wait for a click before loading the report. |
| 3.10 | **Done locally on `pilot-fixes-4`** (3.1's check of a production build): all seven token paths serve `x-robots-tag: noindex, nofollow` and `referrer-policy: no-referrer`. Production: 1.1f step 4. | Founder | 1.1f step 4. |
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
| 3.38 | **Done on `pilot-fixes-3`** (`6c3d981`): after a mint the date shows once; "Link ready. It works until …" stays in the status region for screen readers, visually hidden. The lead and busy label for a link live on load: 3.52. | — | — |
| 3.39 | **Done on `pilot-fixes-3`** (`504b9e2`): the intake card, the check rows and the engine cards say what went wrong in a sentence true of the case, never the code; our-side failures never say the site did not load; "Could not classify" on the card and the Clients list; the 429 is above the form; "A scan usually takes a few minutes." on all four screens that promised five. Open: 3.48, 3.49. | — | — |
| 3.40 | **Focus visibility gaps found in 3.33:** in forced colours no non-filled control shows focus (a shadow with `outline: none`), and Firefox draws its own ring on them; a focused text field shows a ring inside a ring; the dark theme's ring against a primary fill is 1.8–2.0:1 (its outer edge passes); every primary button has a 1px rim of the gradient's other end. | Founder (go), then Claude + ui-reviewer | One transparent outline on the global focus rule; one line on `.avp-field__input:focus-visible`; the dark ring and the rim. |
| 3.41 | **Done on `pilot-fixes-3`** (`24a139f`): an unreadable robots.txt is `ROBOTS_UNREACHABLE` (Technical Foundation excluded as `CRAWL_POLICY_UNREADABLE`, `FORMULA_VERSION` still v5) and every screen says the crawler did not get a usable answer; the crawler still reads nothing without one. The gate now reads within a size, a time per request and a 60 s budget before the browser, and decodes only a short list of text charsets. Open: 3.46, 3.50. | — | — |
| 3.42 | **Done on `pilot-fixes-3`** (`eac7a97`): the step counts only links that would open, by the one rule the public report uses (`share.link_would_open`); cancelled scans stay counted, as the public report still serves them. Open: 3.51. | — | — |
| 3.43 | **Smaller follow-ups.** The FastAPI cap (`<0.118`) also holds Starlette below 0.49: lift it, with a review, before the API serves files. The fix generator is told a failed audit's empty page fields. Two wall-clock tests (`test_call_bounds.py`, `test_scan_deadline.py`) fail under heavy CPU load. A 429 from the browser limits has no log line of its own. | Claude | Each its own small item. |
| 3.44 | **Done on `pilot-fixes-3`** (`3303deb`, item R1): opening a report, its PDF or a score reads the stored score and no longer re-scores the scan; `scoring.completed` is logged only when a Score is saved. No read ever wrote (checked row by row, `xmin`/`xmax` included); a report view went from 22 to 21 statements. | — | — |
| 3.45 | **Done on `pilot-fixes-3`** (`652ba9b`, item R2): a competitor search has a 10 s deadline per attempt and one retry on a new connection for a transport failure (21 s at most per query); a failure logs its kind; no logger can print the search key. Measured from here: 24 real searches, median 1.54 s, longest 6.93 s. Founder, 2026-10-03: keep retrying a read failure. Since S2 every answered search logs `serp.completed`; the retry has not rescued a read failure yet (3 of 3 seen timed out again). | — | — |
| 3.46 | **Accepted** (founder, 2026-10-03): stored rows from before 3.41 keep `ROBOTS_DISALLOWED` for a robots.txt that was really unreadable. | — | — |
| 3.47 | **Kept** (founder, 2026-10-03): the signed-in scan routes keep serving a failed scan's facts (3.37's security review, Medium). | — | — |
| 3.48 | **Kept for now** (founder, 2026-10-03): the business-entity check keeps its eight schema.org types; widening it is a later formula version (with 3.65). | — | — |
| 3.49 | **Done on `pilot-fixes-4`** (`9d1b213`): the fix list, the PDF and the Technical screen say only what the audit read: a `Disallow: /` under `User-agent: *` (not that nothing else can take effect), the eight organisation types the check knows, the Last-Modified header and JSON-LD dates (not "anywhere on the page"), and `CONTENT_STALE`'s 180-day rule. The list's lead names the fixes that come first and says when nothing has a point figure, and a single item reads "1 change". Open: 3.63, 3.64, 3.65. | — | — |
| 3.50 | **Left until a pilot needs it** (founder, 2026-10-03): a robots.txt or sitemap in a legacy non-Latin charset stays "no answer"; the `/crawler` page's 10 s and 60 s limits go with the lawyer's review (2.4). | — | — |
| 3.51 | **Kept** (founder, 2026-10-03): the dashboard's "Share a report" step goes dark again when its only link ends or its scan fails. | — | — |
| 3.52 | **Done on `pilot-fixes-4`** (`9760e1a`): a link live on load has its own lead ("This report has a live link…") and busy label ("Getting the link…"); a failed press on it says "We could not get the link. Try again."; Copy announces "Link copied.". Checked in Chromium on a production build answered by the real API's own responses: the date shows once, and the dashboard step goes dark after the only live link is withdrawn (`docs/screenshots/share-bar-2026-10-03/`). Open: 3.70. | Founder | 1.1f step 7. |
| 3.53 | **Done on `pilot-fixes-4`** (`5a919c8`): `POST /clients/{clientId}/competitors/detect` runs once per client per 24 hours (a 429 that says in a sentence how many hours are left); a scan's own detection is never limited and never spends the day. Open: 3.61, 3.62. | — | — |
| 3.54 | **Done** (2026-10-03): `docs/skills-audit-2026-09-30/` (104 files) was moved to `~/Skills-archive/skills-audit-2026-09-30/`; nothing committed. | — | — |
| 3.55 | **Done on `pilot-fixes-4`** (`596e403`, S1): competitor detection searched every client as `gl=us` and told the model only the brand name. Both signals now work in the client's market (the domain's country-code TLD, else Australia), and the model is told the domain. Reproduced on 2026-10-03: a `.com.au` client's confidence went from 0.000 under `gl=us` to 0.200 under `gl=au`. Open: 3.56. | — | — |
| 3.56 | **Detection is still weak for some clients** (S1's findings): queries built from a long niche phrase return unrelated sites; `NON_COMPETITOR_DOMAINS` lists US publishers only, so local news, comparison and city-guide sites pass as rivals; and nothing records a client's country, so a `.com` business is searched in Australia whatever its market. | Founder | Decide whether Claude reshapes the queries, adds local publisher lists, or adds a country field. |
| 3.57 | **Done on `pilot-fixes-4`** (`40c9a43`, S2): every answered competitor search logs `serp.completed` (`elapsed_ms`, `attempts`, `hits`, `status`, `error_code`). SerpApi counts only successful searches, and a read failure was not billed when measured. The timeouts are unchanged until production data says otherwise. | Founder | 1.1f step 5; after the pilot's first week, send Claude the lines. |
| 3.58 | **Done on `pilot-fixes-4`** (`3114aff`, C1): Citation Strength was left out of two production scans under `NO_AUTHORITY_DATA` (no cited source had an authority tier), as designed; the report's sentence about the first tier now names what the code counts. `scoring.completed` logs which dimensions were left out but not why. | Claude | Optional: add the reason to that log line. |
| 3.59 | **Done on `pilot-fixes-4`** (`b3cdfb2`, K1): `redis.eviction_policy` at start; `redis.data_lost` when Redis has lost its data; every key family expires on its own (a test sweeps them); a scan whose job vanished fails within its 15 minutes with a plain reason. Requeuing such a scan automatically was not built; the build log has the trade-off, and pressing Run again already requeues it. | Founder | Decide whether to requeue automatically. |
| 3.60 | **Sessions never slide:** a session lasts 14 days from sign-in, in the cookie and in Redis (`SessionStore.touch` has no caller). | Founder | Keep, or have Claude extend a session on use. |
| 3.61 | **The detect route caps each client, not each agency** (3.53's security review, Medium): an account can add clients without a classification call and detect once on each. | Founder | Allow the route only to a user with a verified identity or an agency with an active plan (the free scan's rule, no new number), or set a per-agency daily count. |
| 3.62 | **No web screen calls the detect route,** so the brief's "message in the UI" has no screen today; the 429's sentence is ready for one. | Founder | Decide whether to add a "Detect again" button. |
| 3.63 | **The landing page and the pricing card say fixes are ordered by what they would cost;** they are ordered by points, with effort shown beside each. Proposed: "…ordered by the points each would recover, with the effort of each shown beside it." | Founder | Approve the wording; Claude changes both. |
| 3.64 | **The fix generator's model inputs** still say that something which blocks the others outranks something worth more points (`fix_generator.py`), which invites generated wording like "this blocks everything else". | Founder | Decide whether to change the model's input. |
| 3.65 | **Audit checks that may read wrongly** (scoring inputs, so a `FORMULA_VERSION` question, with 3.48): the blanket robots check ignores Allow lines and reads only the start host's file; the entity check misses an IRI-form `@type`; the fix list puts a missing robots.txt (a warning) above every measured gap. | Founder | Decide with 3.48. |
| 3.66 | **A competitor search answered 200 with a JSON body that is not an object** raises instead of returning `SERP_BAD_JSON`, so neither of S2's lines is logged for it. | Claude | A guard, with a test. |
| 3.67 | **Intake accepts a domain without checking that every label is a hostname label** (S1's security review, Low). | Claude (sec) | A check in `intake.parse_domain`, with tests. |
| 3.68 | **Header residuals** (3.1's review): an unhandled 500, uvicorn's own 400s, Next's redirects and its proxy error carry no security headers; framing is denied everywhere, so a share link cannot be embedded (the product spec's embeddable widget would need its route left out); and `includeSubDomains` needs a second look when a custom domain is attached. | Founder, then Claude | Claude can make the API's 500 carry the headers; the rest are decisions. |
| 3.69 | **D1's residuals:** `infra/db/migrations/env.py` registers no asyncpg notice listener, so any migration's NOTICE or WARNING never reaches the deploy log (D1 logs its one warning through Alembic instead); PUBLIC keeps its default EXECUTE on routines in `public` (AVP has none; query 1 lists any); other owners' default privileges (such as `supabase_admin`'s) are left alone; and `tests/test_migrations.py` uses one fixed scratch database name per cluster, so two runs at once can collide. | Claude | A notice listener in `env.py`, and a per-run scratch database name; each small. |
| 3.70 | **The same announcement gap elsewhere:** Settings' invite link (Copy link → Copied) changes only the button's name; and the share bar's "Copied" never goes back to "Copy". | Claude + ui-reviewer | 3.52's pattern. |
| 3.71 | **A client whose crawl was refused still records a classifier model and a classification time,** though no model was called (`apply_outcome` copies the default model; T1). Nothing on screen reads either field. | Claude | Record neither when no model ran, with a test. |
