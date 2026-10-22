# Launch checklist

What stands between today's code and taking money from agencies. Each item says
who does it and the exact steps. **Founder** means only the founder can do it
(an account, a dashboard, a legal or product decision). **Claude** means an agent
session can do it, on a branch, with the full bar and a build-log entry, and
only after the founder says go. Nothing here is pushed to `main` without the
founder's OK; Render deploys `main` before CI finishes.

Status on 2026-10-11: PRs #2 to #15 are merged (`main` at `b80e65c`). This checklist was last
updated on branch `hardening-auth` (phase 1 of the founder's hardening brief, v4 of 2026-10-11).
Phase 0.5, `fix-first` (the audit in `docs/audits/2026-10-10-hardening.md`, F1 to F6, D2), is
merged: its steps and results are 1.1m, and the rows it left open are 3.119 to 3.130.

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

### 1.1f Merge `pilot-fixes-4`'s PR — Founder — **Merged 2026-10-04** (PR #8, `main` at `fc11f02`)
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

**Checked on production on 2026-10-04 (the founder's results): steps 2 to 5 passed.**
- Step 2: the migration `f45983cfa5e3` ran. `redis.eviction_policy policy=unknown`,
  because Render refuses `CONFIG GET`; the founder set `noeviction` in Render's
  dashboard. One `redis.data_lost` came at the first start, as expected.
- Step 3: query 2 returned no rows. Query 3 had no `postgres` row for `public` or for
  every schema (only `storage` for `postgres`, and `supabase_admin`'s own rows). Query 1
  listed only `rls_auto_enable()` (owner `postgres`, `SECURITY DEFINER`, returns
  `event_trigger`), Supabase's own helper that turns row-level security on for new
  tables. **Founder decision: leave it.**
- Step 4: every 3.1 header came once on `/` and `/share/x`. The share page sends
  `no-referrer` and `noindex`, which closes 3.10 on production. The app sends no
  `server` or `x-powered-by`; Render's edge adds `server: cloudflare`.
- Step 5, a new Australian client: `competitors.detected country=au confidence=0.200
  status=ok`.
  - 5 searches were answered in 1.1 to 1.8 s.
  - 1 was rescued by the retry (14.3 s, `attempts=2`).
  - 1 failed both reads (19.1 s), on a long descriptive industry query. Long
    descriptive industry queries are the ones that time out; it was also seen on a
    bank client (3.56).
- Steps 6 (the CSP click-through) and 7 (the screen-reader check) are not reported yet.
  3.68's decision waits on step 6.

### 1.1g Merge `pilot-fixes-5`'s PR — Founder
**Nothing has to be set for the code to be safe.**
- The free trial is off until `STRIPE_TRIAL_DAYS` is set.
- A deployed API only logs when its Stripe Price is not the plan.
- One migration, `7106cadcd0ed` (a nullable `agencies.subscription_trial_end`), runs in
  the API container before it serves, under `lock_timeout`.
- Both services deploy from the same push.
- Stripe stays in **Test mode** through this section; live mode is 2.2.

The founder's decisions this branch carries: the price is US$150 a month (it was
$160), and a 30-day free trial with a card at checkout, charged on day 31 unless
cancelled.

**Before merging:**
1. Stripe dashboard, **Test mode** → Product catalogue → the AVP product → add a
   Price: US$150.00, recurring, monthly, standard (licensed) pricing. Copy its id
   (`price_…`).
   - Do not set it on Render yet: until the deploy, the pages still say $160.

**After the deploy** (Render → **API** service → Logs):
2. Check the start:
   - `entrypoint: alembic upgrade head` (to `7106cadcd0ed`), then `entrypoint: serving
     on :<port>`;
   - then `billing.price_mismatch` (warning) with `expected_amount=15000` and
     `actual_amount=16000`. That is expected: `STRIPE_PRICE_ID` still names the $160
     test Price;
   - a `billing.half_configured` line names a Stripe variable that is not set; set it
     (`docs/runbook.md`, "Reading the billing price lines").
3. Render → API service → Environment → `STRIPE_PRICE_ID` = the $150 Price's id from
   step 1. Save (Render redeploys).
   - Expect `billing.price_check ok=True price_id=price_…` at the start.
   - A `billing.price_mismatch` here means the Price is not an active, licensed US$150
     a month Price. Fix it in Stripe before going on.
   - Between the deploy and this step the pages say $150 while Checkout shows $160.
     Test mode, so nobody is charged.
4. Run one scan on a new client, then read its lines (`docs/runbook.md`, "Reading the
   usage lines" and "Reading competitor-search lines"):
   - one `llm.call` per paid request;
   - one `scan.usage` at the end;
   - `serp.quota_left left=<n> plan_searches=250` from its detection;
   - `scoring.completed` with each left-out dimension's reason in `excluded`.

   To price the scan, run `cd apps/api && uv run python scripts/verify_e2e.py
   --price-usage '<the scan.usage line>'` on your machine (no key needed). Send
   Claude anything that looks wrong.

**Turning the free trial on: only after the founder has decided 3.72.** Until then
leave `STRIPE_TRIAL_DAYS` unset: the pages say nothing about a trial, and checkout is
exactly what it was.

5. Stripe (Test mode) → Developers → Webhooks → the endpoint → add the event
   `customer.subscription.trial_will_end`. That makes five in all; it only writes a log
   line.
6. Stripe → Settings → Billing:
   - turn on the reminder email Stripe sends before a free trial ends (production sends
     no email of its own);
   - check the Customer portal's cancel setting. At once or at the period's end are both
     safe: a trial cancelled before its end is not charged.
7. Render → API service → Environment → `STRIPE_TRIAL_DAYS=30`. Save. Then check:
   - signed out, the pricing card says "The first 30 days after you subscribe are
     free…";
   - on an agency that has never subscribed, Settings → Subscribe opens Checkout with a
     30-day trial, then US$150 a month;
   - pay with Stripe's test card. Settings shows "Trial" and "Free trial until <date,
     time> UTC, then $150 a month", and the API logs `billing.checkout_created …
     trial_days=30`;
   - a Stripe test clock in the dashboard can rehearse the end of a trial.

### 1.1h Merge `pilot-fixes-6`'s PR — Founder — **Merged 2026-10-06** (PR #10, `main` at `607f655`)
**Nothing has to be set for the code to be safe.** Every new feature is off until its
variable is set; the free trial stays off until `STRIPE_TRIAL_DAYS` is set.
- **Three migrations** run in the API container before it serves, in this order, each
  under `lock_timeout`: `7945e69b06e5` (the client's next competitor list, B4),
  `b3f1c8e2a9d4` (scan emails, F1), `c4a9e17d2b58` (monthly rescans, F2).
- **Deploy while no scan and no ad hoc prompt run is in flight.** The old instance
  holds a read lock on `competitor_sets` during a scan; B4's migration waits 5 s for its
  lock and otherwise fails the deploy, changing nothing. Redeploy when idle.
- Both services deploy from the same push. Stripe stays in **Test mode** here.

The founder's decisions this branch carries:
- **B1 (3.72):** a free trial needs the Google identity of the user who starts checkout.
  Trial abuse is accepted for the pilot; the Radar rule (one card on many customers) is
  deferred. **`STRIPE_TRIAL_DAYS` is set only after this branch is deployed.**
- **B2 (3.73):** test mode's Stripe state is cleared at the live-key swap (2.2 step 3).
- **B3 (3.74):** checkout refuses a confirmed Price mismatch; a failed read never blocks.
- **B4 (3.78):** a save never edits a scan's own rival list; the next scan takes it.
- **3.90:** a platform or publisher typed as a rival's domain is refused.
- **3.91:** removing any seat still clears the invitation link (as 3.70 decided).
- **3.43:** reviewed only, not upgraded (the row says when to lift the cap).

**After the deploy** (Render → **API** service → Logs):
1. Check the start: `entrypoint: alembic upgrade head` (to `c4a9e17d2b58`), then
   `entrypoint: serving on :<port>`, then `billing.price_check ok=True price_id=price_…`.
2. Count the stored domains that fail the hostname check (3.81): from the API service's
   Shell, `cd /app/apps/api && .venv/bin/python scripts/count_unchecked_domains.py` (runbook, "Count the stored
   domains that fail the hostname check"). It prints counts only. Send Claude any non-zero
   count; nothing is changed automatically.
3. Look at the logs of any earlier API deploy that **failed at start**: before this branch,
   a settings error printed the tail of the last setting that was set, which could be part
   of a secret. If one did, rotate that secret.
4. **Render has no log-based alerts** (corrected 2026-10-07). To see every trial that starts,
   check Stripe's Subscriptions list filtered to **Trialing**, or send the API's logs to a log
   stream (Better Stack or Papertrail, set under the service's log streams on Render) and alert
   there on `billing.checkout_created` with `trial_days` greater than 0. A log stream receives
   the API's log lines (ids and event names, never a secret, token or email), so it becomes a
   processor `/privacy` must name (2.4).

**Turning the free trial on** (after step 1): Render → API → `STRIPE_TRIAL_DAYS=30`, as 1.1g
steps 5 to 7 describe. Checkout logs `billing.checkout_created … trial_days=30`, and
`trial_withheld_no_google=true` when the user starting it has no Google sign-in.

**Turning each pilot feature on** (each is off until its variable is set; set it on the
API service only, Save, and Render redeploys):
5. **F4, "Send feedback":** `FEEDBACK_EMAIL=<one bare address>`. Use a dedicated mailbox
   with spam filtering: the address is shown to every signed-in user, and sign-up is open.
   - Check: signed in, "Send feedback" at the foot of the sidebar on every signed-in screen,
     and a Feedback section on Settings naming the address; the email it opens has a subject
     like `AVP feedback: /clients/…/alerts`, with no name, id or token; signed out, no link.
   - `GET /api/v1/auth/me` (the browser's Network tab) shows `"feedbackEmail"`.
   - A bad value stops the API starting, naming `FEEDBACK_EMAIL` and not the value.
6. **F3, the report's figures as a CSV:** `REPORT_CSV_ENABLED=true`.
   - Check: a finished scan's report shows "Download figures (CSV)" beside "Download PDF";
     it saves `<client>-ai-visibility-<date>.csv`; open it in a spreadsheet: the header
     `Section,Engine,Prompt,Item,Measure,Value` and six sections, starting with Scan.
   - A queued, running or failed scan has no button; a share link has none. Off, the route
     answers 404.
7. **F1, email when a scan finishes:** first **2.3** (Resend: a verified sending domain,
   `RESEND_API_KEY`, `EMAIL_FROM`), `PUBLIC_WEB_BASE_URL` the real web address, and the
   decision **3.93**. Then `EMAIL_SCAN_NOTIFICATIONS=true`.
   - Logs: `scan_emails.watch_started` at start (or `scan_emails.not_started reason="email
     is not configured"`); per scan `email.scan_ready.sent`, `email.scan_alerts.sent` or
     `email.scan_failed.sent`; `scan_emails.skipped reason=…` for someone who gets nothing.
   - Check: Settings shows "Email when a scan finishes" with On and Off; run a scan; within
     about a minute the email arrives, linking to the signed-in report; turn it Off and run
     another: `scan_emails.skipped reason=opted_out`, no email.
8. **F2, monthly rescans:** **not before the lawyer has fixed `/crawler`** (2.4: "Nothing
   repeats on its own" becomes untrue), and only with billing configured. Then
   `AUTO_RESCAN_ENABLED=true`.
   - Log: `rescan.watch_started interval_s=600`.
   - Check: as an owner or admin, a client that has a scan shows "Rescan every month" under
     Scan history; turn it On. When its latest scan is 30 or more days old, within about 10
     minutes: `rescan.started` with ids, and a scan marked scheduled. A skipped month logs
     `rescan.skipped reason=…` and shows a sentence under the switch. Each automatic scan is
     an ordinary scan: one of the agency's 40, about US$1.45 to 1.50 of provider spend.

**Open decisions from this branch** (blocking first): 3.93 before F1 is turned on; 2.4's
new sentences before F2 is turned on; 3.88 (Tier B, needs a `FORMULA_VERSION` bump); 3.89
(detect's queued scan); 3.86's "A page like this usually stops before this section"; 3.92
(the Google link's edge in forced colours, Claude's when convenient).

### 1.1i Merge `pilot-fixes-7`'s PR — Founder — **Merged 2026-10-08** (PR #11, `main` at `c319f1d`)
**Nothing has to be set.** No migration, no new setting, no new host.
- **Scores written after this deploy carry `formula_version` `v7`** (3.94): a brand's 2-4
  character short form written in capitals ("RMIT", "UTS") counts as a mention. Stored rows keep
  their version and numbers and nothing is re-scored; the visibility alert does not compare a
  `v7` score with a `v5` one (`alerts.formula_changed` in the log instead).
- **Generated fixes name only sites the scan cited, or the client's own,** and say no
  "largely" (3.95). shiksha.com and collegedunia.com are refused as rivals.
- **The report** (3.96, 3.99): no agency slug and no beat names on the PDF or the web report; PDF
  tables wrap instead of cutting names, a continued table repeats its header, and nothing is
  stranded at a page foot; the pitch heading reads as arithmetic, and when the points a fix
  recovers do not move the printed score the report says so instead of printing it twice.

**Re-running RMIT University after the deploy:**
1. In the API logs, check `entrypoint: serving on :<port>` and `billing.price_check ok=True`.
2. Remove Shiksha (and collegedunia.com, if it is listed) from RMIT University's rival list.
   While the list holds either, a save is refused, naming the row (3.90); the next scan takes
   the list as it stands.
3. Run a new scan from the client's page. It is one of the agency's 40 scans and about
   US$1.45 to 1.50 of provider spend (north-star §5.1).
4. When it finishes, check the CSV: "Named the client" should read yes on answers that write
   "RMIT". Mention Rate and Share of Voice will rise: that is the matching, not RMIT's market.
   The 2026-10-05 scan keeps its `v5` score; it cannot be re-matched, because answer text is
   not stored.

**Queued from this branch, nothing to decide:** 3.97 (refusing an explicit re-score of a scan
matched before `v7` — decided, and its own item because nothing yet records which matcher made a
scan's matches), 3.98 (a short form inside a rival's longer name, Claude's when it bites).

### 1.1j Merge `pilot-fixes-8`'s PR — Founder — **Merged 2026-10-09** (PR #12, `main` at `f9a76b0`)
**Nothing has to be set.** No new setting and no new host; **one migration** (`5863629b56ab`, 3.97) runs
in the API container before it serves.
- **Deploy while no scan is in flight.** The migration adds `scans.matcher_version` and marks each
  scan that exists by whether its own run scored it under `v7`, under `lock_timeout = '5s'`: if a
  running scan holds the table, the deploy fails and changes nothing; redeploy when idle.
- **What changes:** the pitch's three figures re-add as printed (3.101); the API's access log no
  longer records the visitor's IP address (3.102, and a note for the lawyer under 2.4); every line
  the fix generator writes names its scan (3.103); an explicit re-score of a scan matched before `v7`
  is refused with a 409 that says to run a new scan (3.97); CI runs on Ubuntu 24.04 with actions on
  Node 24 (3.104); the report's opening line names its biggest gap (3.107); and where the fixes'
  points carry the score past 100 the pitch says so instead of printing a sum (3.100).
- **After the deploy** (Render → **API** service → Logs): `entrypoint: alembic upgrade head` (to
  `5863629b56ab`), then `entrypoint: serving on :<port>`; an access line now reads
  ` - "GET /api/v1/health HTTP/1.1" 200 OK`, with no address in front.
- **Not on this branch:** market scope (3.105): the plan is on branch `market-scope-plan`
  (`794c343`, not pushed), its decisions accepted; nothing of it is built yet.

**Open from this branch:** 3.105 (market scope, ready to build, its first item first, because it
corrects a score today).

### 1.1k Merge `m1-other-sites`'s PR — Founder — **Merged 2026-10-09** (PR #13, `main` at `0297fbc`)
**Nothing has to be set.** No new setting and no new host; **one migration** (`89403ce44352`)
runs in the API container before it serves.
- **Deploy while no scan is in flight.** The migration adds three empty lists
  (`clients.other_sites`, `clients.dismissed_other_sites`, `competitors.other_sites`) with a
  constant default, under `lock_timeout = '5s'`: no row is rewritten, and if a running scan holds a
  table the deploy fails and changes nothing; redeploy when idle.
- **What changes** (3.109): a site carrying a brand's name under another country's ending
  (rmit.edu.vn for rmit.edu.au) is proposed on the client's **Sources** tab, under **Sites with the
  same name**, and counts as that brand's own once confirmed there; scans and scores from the deploy
  on are `v8`, every stored scan keeps its score, and an explicit re-score of an older scan is
  refused; the report names confirmed sites under its evidence and the CSV attributes them to the
  brand; the competitor editor refuses a rival on a confirmed site, and detection skips one; a site marked **Not theirs** can be taken back from the card's **Dismissed (n)** list.
- **After the deploy, for RMIT University:**
  1. Render → **API** service → Logs: `entrypoint: alembic upgrade head` (to `89403ce44352`), then
     `entrypoint: serving on :<port>`.
  2. Open RMIT University → **Sources** → **Sites with the same name**. rmit.edu.vn is proposed
     from the latest finished scan, with its citation count. Choose **It's theirs**; the status
     line says it counts as RMIT University's own site from the next scan.
  3. Run one new RMIT scan (one of the agency's scans, and one scan's provider spend). Its report
     prints "RMIT University's other sites: rmit.edu.vn — N citations" under the evidence, its CSV
     attributes rmit.edu.vn to RMIT University, and its Citation Strength no longer counts
     rmit.edu.vn. The earlier scans keep their `v7` scores, and a `v8` score is not compared with
     them (scores are never compared across a version).
- **Not on this branch:** market scope M2 to M4 (3.105).

**Open from this branch:** 3.110 and 3.112.

### 1.1l Merge `f5-prompt-fallback`'s PR — Founder — **Merged 2026-10-10** (PR #14, `main` at `c8f4aca`)
**Nothing has to be set.** No new setting and no new host; **two migrations** run in the API
container before it serves: `d5b2f84a6c13` (one column on `clients`), then `5f08c7c74129` (one
column on `scans`).
- **Deploy while no scan is in flight.** The migrations add `clients.new_questions_next_scan`
  (NOT NULL, default false) and `scans.named_domains` (JSONB, NULL on every existing scan), with
  no backfill and under `lock_timeout = '5s'`: if a running scan holds a table, the deploy fails
  and changes nothing; redeploy when idle.
- **What changes:**
  - **A scan's questions are the generator's** (F5): templates only top up a generated set of at
    least 15, as real questions placed in the client's country; fewer, or a failed call, ends the
    scan `failed` with `PROMPTS_UNAVAILABLE` before any engine is asked. The CSV's Scan section
    says how many questions came from each. The generator's call states its thinking setting,
    survives two transient failures (up to 182 s instead of 122 s; a normal call takes about
    12 s) and logs the HTTP status when it fails.
  - **A failed prompt phase is free three times a day per agency** when the model's answer caused
    it; from the fourth it counts against the agency's scans; an outage of ours never counts.
  - **A client keeps its questions** (F6): a rescan asks the client's most recent all-generated
    questions again; the trend charts, the changes they state and the alerts break where the
    questions changed; every report says "Same questions as the scan of <day>" or "New
    questions" and flags questions that name a past year; the client's **Prompts** tab says where
    its questions came from and has **Write new questions for the next scan** (anyone in the
    agency; it applies from the next scan).
  - **"Get onto" never names a probable rival** (F2): a site with the client's own restricted
    ending (edu.au for rmit.edu.au) that the scan's answers named is skipped there, and the
    client's **Competitors** page suggests it as a rival. Only scans from the deploy on record
    what their answers named; older reports read exactly as before.
  - Generated fixes no longer claim what rivals "cannot match" or speak of "several topics" (F1);
    Citation Strength is explained as how well an answer backs the brand (F3); a client page that
    is loading, or failed to load, no longer links to pages that 404 (F4).
- **After the deploy:**
  1. Render → **API** service → Logs: `entrypoint: alembic upgrade head` (to `5f08c7c74129`),
     then `entrypoint: serving on :<port>`.
  2. **RMIT University's next scan fixes its questions.** By F6's rule it reuses RMIT's most
     recent set written entirely by the generator, which is older than the 9 Oct scan that was
     half template filler; its report says "Same questions as the scan of <that scan's day>",
     and every later RMIT scan asks the same questions. To start RMIT from new questions
     instead, open RMIT University → **Prompts** and press **Write new questions for the next
     scan** before that scan: its report then says "New questions", and the scans after it reuse
     those.
  3. Run that scan (one of the agency's scans, and one scan's provider spend). In the API logs:
     `prompts.reused … from_scan_id=…` (or, after a request for new questions, `prompts.generated`
     with no `prompts.fallback_used`), and near the end `scan.named_domains scan_id=…`.
  4. Open RMIT University → **Competitors**. If the scan's answers named another university whose
     site they cited (deakin.edu.au, say), it is offered under **Probable rivals** as
     "deakin.edu.au: cited N times, named in M answers. Add it as a rival?". Adding it saves the
     whole list and marks every rival on it as set by hand, as the competitor editor's save does;
     the card says so. The report's "Get onto" fix names the next unclaimed source instead.

**Open from this branch:** 3.113 to 3.118.

### 1.1m Merge `fix-first`'s PR — Founder — **Merged 2026-10-11** (PR #15, `main` at `b80e65c`)
The hardening brief of 2026-10-10, phase 0.5: the audit's exploitable findings, one commit each:
the audit, F1, F6, F2, F4, D2 (and its fix forward), F5a, F3, F5b, F5c. **No migration.** Under the
founder's rule for this Mac, no full suite ran locally: each commit's targeted runs and the 30
tree-sweeping guard files ran, and GitHub CI runs the full bar on the draft PR. Merge only when
CI is green.
- **Before the deploy: `ENVIRONMENT=production` on the API service** (F5a). The API now refuses
  to start when `ENVIRONMENT` is unset and `DATABASE_URL` names a remote host, so without it the
  deploy fails to start (the old version keeps serving on Render until a healthy one is up).
  **Done: the API service already had it.** There is no workers service on Render: scans run
  inside the API process (`apps/api/docker-entrypoint.sh`), and `apps/workers` is not deployed,
  so nothing is set anywhere else.
- **Before the deploy: owner seats an admin may already have made** (F1). Until this branch, an
  admin could invite someone as an owner. Run these two read-only queries in Supabase → SQL editor
  and read each hit: an inviter who was an owner at the time and was later removed, or changed
  role, also shows up in A, so a hit is not proof of abuse. Revoking an invitation or demoting a
  seat is your call; tell Claude which, and it writes the statement for you to run.
  ```sql
  -- A. Owner invitations whose inviter is not a live owner now.
  SELECT i.id AS invitation_id, i.agency_id, i.email AS invited_email,
         i.created_at, i.accepted_at, i.revoked_at,
         i.invited_by_user_id, u.role AS inviter_role, u.deleted_at AS inviter_deleted_at
  FROM invitations i
  LEFT JOIN users u ON u.id = i.invited_by_user_id
  WHERE lower(i.role::text) = 'owner'
    AND (u.id IS NULL OR lower(u.role::text) <> 'owner' OR u.deleted_at IS NOT NULL)
  ORDER BY i.created_at DESC;

  -- B. Owners who joined through an invitation from a non-owner.
  SELECT o.id AS owner_user_id, o.agency_id, o.email AS owner_email, o.status, o.created_at,
         i.id AS invitation_id, i.accepted_at, i.invited_by_user_id, inv.role AS inviter_role
  FROM users o
  JOIN invitations i
    ON i.agency_id = o.agency_id AND lower(i.email) = lower(o.email)
  LEFT JOIN users inv ON inv.id = i.invited_by_user_id
  WHERE lower(o.role::text) = 'owner'
    AND (inv.id IS NULL OR lower(inv.role::text) <> 'owner')
  ORDER BY o.created_at DESC;
  ```
  Owners who signed up directly have no invitation row and match neither query.
  **Run by the founder on 2026-10-11: query A 0 rows, query B 0 rows.** Nothing to revoke or demote.
- **Before the deploy: accounts that already hold both a password and a Google link** (F2). Until
  this branch, a Google sign-in that linked a password account left its password in place. Count
  them (read-only); whether to clear those passwords is your call:
  ```sql
  SELECT count(*) FROM users
  WHERE google_sub IS NOT NULL AND password_hash IS NOT NULL AND deleted_at IS NULL;
  ```
  **Run by the founder on 2026-10-11: 1 account, the founder's own. It stays as it is.**
- **After the deploy, what people notice** (F2): a password account that signs in with Google for
  the first time loses its password and every other session, and signs in through Google from then
  on. The free scan needs Google, so most password accounts that take it go this way. Settings says
  "This account has no password, so it signs in through Google only." Letting such an account add a
  password back is a product decision (launch checklist 3.119).
- **If `google.link.post_commit_revoke_failed` appears in the log:** the link is in place, but a
  session made in the moment between the two revocations may survive. Since A3 part 3 the link's
  cutoff refuses such a session, and a password sign-in racing it is refused by its own read-back;
  to be certain, run the runbook's set-cutoff command for that `user_id` ("Ending one person's
  sessions for certain"), which ends every session signed in before it.
- **New settings, all with defaults; nothing needs setting** (D2, F3, F4, F5b, F5c):
  `MAX_SCANS_PER_DAY_PLATFORM` 60, `MAX_SIGNUP_FREE_SCANS_PER_DAY` 20 (D2);
  `PROMPT_RUNS_PER_CLIENT_PER_HOUR` 30, `PROMPT_RUNS_PER_AGENCY_PER_DAY` 100,
  `MAX_PROMPT_RUNS_PER_DAY_PLATFORM` 300, `FIX_REGENERATE_COOLDOWN_SECONDS` 3600,
  `FIX_RUNS_PER_AGENCY_PER_DAY` 30, `MAX_FIX_RUNS_PER_DAY_PLATFORM` 100,
  `CLASSIFICATIONS_PER_AGENCY_PER_DAY` 20, `CLASSIFICATIONS_PER_UNPAID_AGENCY_PER_DAY` 5,
  `MAX_CLASSIFICATIONS_PER_DAY_PLATFORM` 200, `MAX_FREE_CLASSIFICATIONS_PER_DAY_PLATFORM` 60 (F3);
  `GOOGLE_START_LIMIT_PER_IP` 30, `GOOGLE_START_WINDOW_SECONDS` 900, `REDIS_MEMORY_WARN_RATIO` 0.5
  (F4); `ARGON2_MAX_CONCURRENCY` 2, `ARGON2_QUEUE_TIMEOUT_SECONDS` 10 (F5b);
  `PDF_RENDER_MAX_CONCURRENCY` 1, `PDF_RENDER_QUEUE_TIMEOUT_SECONDS` 20, `SHARED_REPORT_LIMIT_PER_IP` 60,
  `SHARED_PDF_LIMIT_PER_IP` 10, `SHARED_WINDOW_SECONDS` 600 (F5c).
  **The spend kill switch:** `MAX_SCANS_PER_DAY_PLATFORM=0` refuses every new scan, and `0` on any
  `MAX_…_PER_DAY_PLATFORM` refuses that kind of paid call; running scans finish.
- **What people notice after the deploy:**
  - a free agency can no longer ask its own questions on the Prompts tab: "Asking your own
    questions needs a plan." (F3; the founder was told this reading of "an active plan or
    allowance"); owners and admins are offered a link to Billing;
  - past a daily cap, a scan, question, fix or classification is refused with a sentence that says
    so and when to try again;
  - an admin's invite form no longer offers Owner (F1);
  - Google sign-in from one network past 30 starts in 15 minutes is refused with a sentence (F4);
  - a shared report or its PDF requested too often from one network is refused with a sentence and
    a Reload button, and a cited link that is not an http(s) address shows as plain text (F5c).
- **After the deploy, read the logs for:**
  - `redis.memory` at start: `max_mb` should be about 25 (F4). If it is `null`, or
    `redis.memory_limit_unknown` appears, the memory warning cannot fire;
  - `redis.eviction_policy policy=noeviction` (a `volatile-*` policy would let the daily-cap keys be
    evicted and their counts reset; checklist 3.4 says it is `noeviction`);
  - no `scan.platform_cap_reached` or `ai.platform_cap_reached` on a normal day; one means a cap
    is too low or someone is pushing it.
- **Checked after the deploy (founder, 2026-10-11):** a real RMIT University scan ran on the new
  deploy (`scan_01M4K9ERRQACY2T8SBE1G7D5VH`, 70.46). The scan, its PDF and its CSV were fine, and
  `redis.memory` was logged.

**Open from this branch:** 3.119 to 3.130.

### 1.2 `STRIPE_PRICE_ID` is the US$150 Price — Founder
The price is US$150 a month since 2026-10-04 (founder decision; it was $160). This is
1.1g steps 1 and 3, in Test mode until 2.2 is done.
- Done when the API logs `billing.price_check ok=True`, and Settings → Subscribe opens
  a Stripe checkout page showing US$150 per month (with a trial only once
  `STRIPE_TRIAL_DAYS` is set).

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
Until this is done, the per-IP limits may not be reliable. The per-address
guards hold either way (the per-email limits on sign-up, reset request and
change password; the slow-down on login), but since A2 the per-IP window is
login's only hard limit: if the probe shows anything other than one bucket
per visitor, login has no working hard limit, and if every request shares one
bucket, 30 failed sign-ins from anyone refuse every password sign-in until the
15-minute window ends. Must be done before launch.

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
2. In **live** mode: create the product and the US$150 monthly Price (standard,
   licensed); add the webhook endpoint
   `https://ai-visibility-platform-5gk4.onrender.com/api/v1/billing/webhook`
   with exactly `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted` and
   `customer.subscription.trial_will_end` (the fifth only writes a log line); set the
   Customer portal (cancel at period end, update payment method) as in the
   runbook's step 5.
   Also in live mode: the trial reminder email and the portal's cancel setting, as in
   1.1g step 6.
3. **The swap, then clear test mode's billing state** (3.73, decided 2026-10-05). Every
   agency's stored subscription state is test mode's, and no live webhook will ever
   change it. On Render (API service), replace `STRIPE_SECRET_KEY` with the live secret
   key, `STRIPE_WEBHOOK_SECRET` with the live endpoint's signing secret and
   `STRIPE_PRICE_ID` with the live Price id, in one save. Live keys go on Render only,
   never in the repo or a chat.
   - Expect `billing.price_check ok=True` with the live Price's id.
   - `STRIPE_TRIAL_DAYS` is not tied to a mode: it stays as 1.1g left it.
   - When the redeploy has finished, and before anyone subscribes in live mode, open
     Render → the API service → **Shell** on the new deploy and run the dry run:
     `cd /app/apps/api && .venv/bin/python scripts/clear_test_billing_state.py`. It
     prints counts only, and its key line must read `Stripe key: live; Stripe lists no
     subscription and no customer.`
   - Then run the same command with `--confirm`. Every Stripe count under `After:`
     should be `0`, then `Cleared.`. Any exit code but 0 means stop: the runbook's
     "Clearing test-mode billing state at the live-key swap" says what each one means.
4. Buy one subscription with your own card, on an agency that has never subscribed.
   - Check Settings shows it and the webhook deliveries answered 200, then cancel it.
   - With `STRIPE_TRIAL_DAYS` set it starts a trial and charges nothing before its
     end. To see a real charge, do this before setting it, then cancel and refund from
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
Since `pilot-fixes-5`, also for the lawyer: the sentences a free trial leaves
incomplete. They are listed in the build log's entry for P1:
- the terms' "The plan", their cancellation sentence and their fair-use example;
- the privacy page's "Billing." line, now that the trial's end is stored;
- From `pilot-fixes-6`'s pilot features, before each is turned on (1.1h):
  - F1: `PrivacyPolicy.tsx:153-156` lists only password-reset and invitation emails as sent through Resend; scan emails go through it too and carry the client's name. "Your account" (`:66-69`) does not list the stored on/off choice for them.
  - F2: `CrawlerInfo.tsx` ("How often", about line 86) says "Nothing repeats on its own."; with monthly rescans on, an automatic scan runs the technical audit, so AVPScanBot visits on a schedule.
  - F4: "What is stored" (`PrivacyPolicy.tsx:64-102`) says nothing of feedback emails held in the `FEEDBACK_EMAIL` mailbox; its provider is not named (`:118-119`); a second contact address now exists (`:55-56`).
- 3.86's "never limited". Since `pilot-fixes-6` (B5) the pricing card says "Viewing a report or exporting it as a PDF does not use a scan. A shared link works for 30 days and can be withdrawn sooner.", so the terms now say the opposite of the card until the lawyer rewords them. B5's list:
  - `TermsOfService.tsx:80`: "Viewing, sharing and exporting reports is never limited." (a shared link ends after 30 days);
  - `TermsOfService.tsx:82-84`: "…and the reports you already have stay open." (absolute about viewing after a cancellation; true of the routes today);
  - `PrivacyPolicy.tsx:234-235`: "…can be revoked sooner." (true; the product's word is "withdrawn");
  - `PrivacyPolicy.tsx:98`: "A shared report's link is kept as it is, so it can be copied again." (true; a downloaded PDF outlives a withdrawn link).
- From `pilot-fixes-8` (A3): the API's own log no longer records the visitor's IP address (only the
  method, the path and the status), so `PrivacyPolicy.tsx:204` ("The API's server log records each
  request: the IP address it came from and the address it asked for.") now says more is kept than
  is. `:113-114` ("IP addresses do appear in the server logs, described below.") also covers
  Render's own record of each request (`:125`, `:212-216`), whose fields were not checked: that
  sentence stays until they are.
- From `f5-prompt-fallback` (the cap on free prompt failures): `TermsOfService.tsx:75` says "A scan
  that fails does not count."; from the fourth failed prompt phase in a day that the model's answer
  caused, one does.
- From `hardening-auth` (A4, D10, decision 3.130 (1)): the breached-password check. When a password
  is set (sign-up, a password change, a reset, an invitation accept) the API can look it up in the
  Pwned Passwords service (`api.pwnedpasswords.com`, Have I Been Pwned's range lookup), sending the
  first five characters of the password's SHA-1 hash and nothing else of the password, with a
  User-Agent that names AVP and its version (`AVP-API/<version> (breached-password check)`); the
  service sees the API server's address, that User-Agent and those five characters, and nothing
  the service answers is kept. It ships
  OFF (`BREACHED_PASSWORD_CHECK_ENABLED`, default false), so "Who else handles it" in
  `PrivacyPolicy.tsx` does not name it yet. The page must name it, in the lawyer's words, before
  the setting is turned on (`docs/runbook.md`, "The breached-password check"); `providers.test.ts`
  fails if the default becomes true before the page does. The service's own terms of use
  (attribution, limits) were not checked when this was built; they are for the lawyer to read.

Both pages say they were last updated on 2026-10-04.

### 2.5 Deletion endpoint and the data-retention decision — Founder decides, Claude builds
There is no way to delete an account or agency today.
1. Founder decides: what a deletion request removes (agency, users, clients,
   scans, reports, share links), what is kept and for how long (for example
   Stripe invoices for tax), and how fast it happens.
2. Claude builds it to that decision: an owner-only endpoint with tenancy
   checks, the Stripe subscription cancelled first, a migration if needed,
   tests, a security review, and the `/privacy` text updated to match.

### 2.6 Per-device lockout — deferred (founder decision 8)
Sign-up, change-password and reset are limited per email and per IP; login is
limited per IP and slowed per address (A2). A per-device lockout was deferred
on 2026-10-01. Revisit if the logs show many `auth.login.slowed` lines for one
`user_id`.

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
| 3.5 | **Partly done on `fix-first` (F5a):** `hide_parameters=True` on the API's engine, the workers' readiness engine, the migrations engine and the two operator scripts; what remains (Postgres's own `DETAIL` text, asyncpg's encoding errors, and logging only the exception class on identity paths) is 3.127. **SQL parameters can reach the logs.** `db.py` does not set `hide_parameters=True`, and `logger.exception` / `exc_info=True` (`google.callback.failed` in `routers/auth.py`; `scan.lease.renewal_failed`, `scan.chain.phase_failed`, `scan.chain.reload_failed`, `scan.execute.failed` and `scan.execute.failed_to_record` in `services/scan_executor.py`) render SQLAlchemy errors whose text includes bound parameters, such as an email. Unconfirmed at runtime. | Claude (sec) | `hide_parameters=True` on the engine; log `error=type(exc).__name__` on identity paths; add an exception-path case to `tests/test_secrets_stay_out_of_logs.py`. |
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
| 3.18 | **Done on `pre-pilot`** (`0562bfc`; checkout was owner or admin then; owner only since 3.130, 2026-10-11). Was: **the scan-limit notice offered Subscribe where it could not work:** to an agency whose subscription needs attention (it then shows the refusal and a link to Billing) and to members (403). | Claude + ui-reviewer, after the founder's go | `useScanAllowance` already reads `GET /billing`; use it to show Manage billing instead, and nothing to members. |
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
| 3.40 | **Done on `pilot-fixes-6`**: every control shows keyboard focus in forced colours (a transparent outline on a rule of its own, which also ends Firefox's native ring; the Google sign-in link's own `outline: none` is gone); a text field is lit once; the dark theme's filled buttons get a gap then the ring (5.9:1 against the gap, from about 1.6 to 2.0:1 against the fill); the primary button's 1px rim is gone. Open: 3.92. | — | — |
| 3.41 | **Done on `pilot-fixes-3`** (`24a139f`): an unreadable robots.txt is `ROBOTS_UNREACHABLE` (Technical Foundation excluded as `CRAWL_POLICY_UNREADABLE`, `FORMULA_VERSION` still v5) and every screen says the crawler did not get a usable answer; the crawler still reads nothing without one. The gate now reads within a size, a time per request and a 60 s budget before the browser, and decodes only a short list of text charsets. Open: 3.46, 3.50. | — | — |
| 3.42 | **Done on `pilot-fixes-3`** (`eac7a97`): the step counts only links that would open, by the one rule the public report uses (`share.link_would_open`); cancelled scans stay counted, as the public report still serves them. Open: 3.51. | — | — |
| 3.43 | **Reviewed 2026-10-06 on `pilot-fixes-6`, not upgraded (the founder: review only).** The FastAPI cap (`<0.118`) holds Starlette at 0.48.0; the latest are FastAPI 0.142.2 and Starlette 1.7.0. None of the six Starlette advisories since 0.48 reaches AVP's code today (no `FileResponse`, `StaticFiles` or form routes; the CSV and PDF are in-memory responses). The upgrade has two silent traps: from 0.118 a request's `db_session` would stay open, idle in transaction, for a whole scan unless scoped to the function; and from 0.137 `app.routes` changes shape, which would empty CORS's method list. 0.142 also turns OpenTelemetry on by default. Recommendation: lift it before the API serves files, as its own item, straight to the latest. The other follow-ups (the fix generator's empty page fields, two wall-clock tests under load, a 429 with no log line) are open. | Claude | The lift, as its own item, before any file, form or upload route. |
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
| 3.56 | **Detection is still weak for some clients** (S1's findings): queries built from a long niche phrase return unrelated sites; `NON_COMPETITOR_DOMAINS` lists US publishers only, so local news, comparison and city-guide sites pass as rivals; and nothing records a client's country, so a `.com` business is searched in Australia whatever its market. | — | **Founder decision 2026-10-04: after the pilots.** 1.1f's check on production adds the evidence: long descriptive industry queries are the ones that time out (an Australian client's failed both reads at 19.1 s; also seen on a bank client). |
| 3.57 | **Done on `pilot-fixes-4`** (`40c9a43`, S2): every answered competitor search logs `serp.completed` (`elapsed_ms`, `attempts`, `hits`, `status`, `error_code`). SerpApi counts only successful searches, and a read failure was not billed when measured. The timeouts are unchanged until production data says otherwise. | Founder | 1.1f step 5; after the pilot's first week, send Claude the lines. |
| 3.58 | **Done on `pilot-fixes-4`** (`3114aff`, C1): Citation Strength was left out of two production scans under `NO_AUTHORITY_DATA` (no cited source had an authority tier), as designed; the report's sentence about the first tier now names what the code counts. `scoring.completed` logs which dimensions were left out but not why. **Done on `pilot-fixes-5`** (`bcdab98`): it maps each one to its reason. Left: `audit.completed` logs component names only, and an audit that failed is logged as `NOT_YET_MEASURED` (3.87). | — | — |
| 3.59 | **Done on `pilot-fixes-4`** (`b3cdfb2`, K1): `redis.eviction_policy` at start; `redis.data_lost` when Redis has lost its data; every key family expires on its own (a test sweeps them); a scan whose job vanished fails within its 15 minutes with a plain reason. Requeuing such a scan automatically was not built; the build log has the trade-off, and pressing Run again already requeues it. | — | **Founder decision 2026-10-04: keep failing a lost scan; pressing Run requeues it.** |
| 3.60 | **Superseded on 2026-10-11 by 3.130 (4)** (a session ends after 72 hours idle or 14 days in all), built in A3 part 1 (2026-10-21). Was: **sessions never slide:** a session lasts 14 days from sign-in, in the cookie and in Redis (`SessionStore.touch` has no caller). | — | **Founder decision 2026-10-04: sessions do not slide** (superseded by 3.130 (4)). |
| 3.61 | **Done on `pilot-fixes-5`** (`4a0466d`): detection from the button (`POST /clients/{clientId}/competitors/detect`) is for an agency that is `active` or `trialing` and for the exempt operator; everyone else gets a plain 402. The per-client day stays, and a scan's own detection is unchanged. It holds for real only once Stripe is live: on test keys anyone becomes `active` with a test card (2.2, 3.73). Left: 3.78, 3.79. | — | — |
| 3.62 | **No web screen calls the detect route,** so the brief's "message in the UI" has no screen today; the 429's sentence is ready for one. | — | **Founder decision 2026-10-04: no Detect button.** |
| 3.63 | **Done on `pilot-fixes-5`** (`8bafd3d`): the landing page and the pricing card say fixes are ordered by the points each would recover, with the effort of each shown beside it. Checking both against the code corrected the other claims it does not support; the rest are 3.86. | — | — |
| 3.64 | **The fix generator's model inputs** still say that something which blocks the others outranks something worth more points (`fix_generator.py`), which invites generated wording like "this blocks everything else". | — | **Founder decision 2026-10-04: leave the fix generator's ordering input.** |
| 3.65 | **Audit checks that may read wrongly** (scoring inputs, so a `FORMULA_VERSION` question, with 3.48): the blanket robots check ignores Allow lines and reads only the start host's file; the entity check misses an IRI-form `@type`; the fix list puts a missing robots.txt (a warning) above every measured gap. | — | **Founder decision 2026-10-04: with 3.48, as a later formula version.** |
| 3.66 | **Done on `pilot-fixes-5`** (`8153e5b`): a 200 whose body is not an object, or is nested too deeply, is `SERP_BAD_JSON`. A stray item, a link that is not text, or a position that is not a whole number is skipped or replaced, never raised out of a detection. | — | — |
| 3.67 | **Done on `pilot-fixes-5`** (`3c1223f`): intake refuses a host that is not a valid hostname (IDNs encoded with `idna`, punycode checked, lengths bounded before encoding), and neither refusal quotes what was typed. Left: 3.80, 3.81. | — | — |
| 3.68 | **Header residuals** (3.1's review): an unhandled 500, uvicorn's own 400s, Next's redirects and its proxy error carry no security headers; framing is denied everywhere, so a share link cannot be embedded (the product spec's embeddable widget would need its route left out); and `includeSubDomains` needs a second look when a custom domain is attached. | Founder, then Claude | **Founder decisions 2026-10-04:** enforce the CSP once the founder's console click-through (1.1f step 6) shows no violation, and Claude switches it; share links are never embeddable; revisit HSTS `includeSubDomains` with a custom domain. Claude can still make the API's 500 carry the headers. |
| 3.69 | **Done on `pilot-fixes-5`** (`64a6010`): a migration's NOTICEs and WARNINGs reach the deploy log, and both migration test files make a scratch database of their own per run. Unverified: whether Supabase's pooler passes a notice through (D1's own warning is the line to rely on). | — | — |
| 3.70 | **Done on `pilot-fixes-5`** (`f450775`): Settings' invitation link says "Link copied." in a status region, as the share bar does, and a refused copy takes it back. Left: 3.85. | Founder | Decide whether "Copied" goes back after a few seconds, on both buttons, with one wording ("Copy" against "Copy link"). |
| 3.71 | **Done on `pilot-fixes-5`** (`c9e6043`): a client whose site no model saw records no classifier model or time. Rows stored before are not backfilled (nothing reads them). | — | — |
| 3.72 | **Decided 2026-10-05, built on `pilot-fixes-6`** (B1, `1a598c7`): a free trial needs the Google identity of the user who starts checkout; without one checkout still works with no trial, and every screen that mentions the trial says so. Trial abuse is accepted for the pilot; the Radar rule (one card on many customers) is deferred. | Founder | Set `STRIPE_TRIAL_DAYS` only after this branch is deployed (1.1h). |
| 3.73 | **Decided 2026-10-05, built on `pilot-fixes-6`** (B2): test mode's billing state is cleared at the live-key swap by a founder-run script, `apps/api/scripts/clear_test_billing_state.py` (runbook: "Clearing test-mode billing state at the live-key swap"). It is a dry run by default and prints counts only. `--confirm` clears every agency's Stripe columns in one transaction, and runs only with a live key while Stripe's live account lists no subscription and no customer. | Founder | 2.2 step 3. |
| 3.74 | **Decided 2026-10-05, built on `pilot-fixes-6`** (B3, `6572719`; `b566f97`): checkout refuses when a fresh read confirms the plan's Price is wrong, with a plain sentence and an error log line; a failed or slow read (10 s) never blocks. | — | — |
| 3.75 | **Usage is in the logs only** (U1). Keeping it would be one table, `scan_usage_lines`: about 8 rows and 3 KB a scan, written after the scan ends so it can never fail one. | Founder | Decide whether to keep usage in the database (U1's build-log entry has the shape, the cost and the alternatives). |
| 3.76 | **Done on `pilot-fixes-6`** (`0a6456c`): a schema miss from `messages.parse` in sentiment, prompt generation or co-citation takes the path a provider error takes, so one malformed answer no longer fails a paid scan. | — | — |
| 3.77 | **Done on `pilot-fixes-6`** (`f1da6a3`): when one of a group of paid calls fails (the scan's prompts, an engine's answers, co-citation, SerpApi), the others are cancelled. | — | — |
| 3.78 | **Decided 2026-10-05, built on `pilot-fixes-6`** (B4): the report's competitor editor shows for an empty and a missing set as well as one with rivals. A save (`PUT`) and a detection write the client's next list, never a scan's own set; the next scan takes the list, so a finished scan's rivals, score and history Share of Voice never change. History reads the stored Share of Voice; ad hoc prompt runs use the client's current list. **The deploy runs migration `7945e69b06e5`:** deploy while no scan or ad hoc run is in flight (1.1h). Open: 3.88, 3.89. | — | — |
| 3.79 | **Done on `pilot-fixes-6`** (B4): the editor says a list saved empty stays empty and later scans carry it forward; the report's empty card says the list had no rivals because none were found or it was emptied by hand; every editor sentence says a save is for later scans and the scan on screen keeps its list and score. | — | — |
| 3.80 | **Internationalised names** (3.67's security review): mixed-script look-alikes are accepted (D1); one site can be two clients in its two spellings, matched by exact string (D2); Tier B's authority list excludes every agency's competitor rows, manual and suppressed ones included (D4); subdomain labels are held to the strict rule (D6). | Founder | Decide each. |
| 3.81 | **Done on `pilot-fixes-6`**: a competitor domain typed through `PUT /clients/{clientId}/competitors` is checked as a client's website is and refused unless it already is the rival's own domain (a pasted page, path or subdomain is refused, never reduced; the client's own domain is refused); a model's co-citation domain that fails is dropped and the brand kept. Stored rows are counted, not changed: `scripts/count_unchecked_domains.py` (runbook). Open: 3.90. | Founder | After the deploy, run the count on production (runbook) and report a non-zero count. |
| 3.82 | **Done on `pilot-fixes-6`** (`c1ac10f`): the API suite refuses any non-loopback request, fails the test that made one even when the code swallowed it, and never reads `apps/api/.env`. | — | — |
| 3.83 | **`trialing` is not bounded by its end date** (P1's review, L1): if the event that ends a trial is lost, the agency keeps the plan. The same is true of `active` past its period. | Founder, then Claude | Decide whether `trialing` counts only until its end plus a grace period, or a runbook check instead. |
| 3.84 | **Trial copy decisions** (P1's UI review): the hero's "$150/month after that." does not mention a trial; the scan-limit notice's Subscribe buttons carry no trial terms; a cancelled agency is not told why it gets no trial. | Founder | Decide each. |
| 3.85 | **Done on `pilot-fixes-6`**: Settings announces a new invitation link (a sentence in the link's status region), the seat notice and the password message are announced from always-present regions, the notices say no email was sent when none was (and no longer claim one was), and a link a failed removal or invite did not replace stays. Open: 3.91. | Founder | The listening step: invite with email off and hear "Invitation link ready…" and then the notice. |
| 3.86 | **Decided 2026-10-05, built on `pilot-fixes-6`** (B5): the hero's unsourced "Buyers increasingly ask an assistant before they ask a search engine." is gone; the pricing card says "Viewing a report or exporting it as a PDF does not use a scan. A shared link works for 30 days and can be withdrawn sooner."; "What it does not do yet" says no screen sets a logo or colour yet, and nothing re-measures on its own (a new scan does). The terms' copy of "never limited" waits on the lawyer (2.4). Open for the founder: "A page like this usually stops before this section." is an unsourced claim about other pages, kept; the fix list's bare "effort S/M/L" letters are explained nowhere. | Founder (the lawyer for the terms) | Keep or cut the "usually stops" sentence; send 2.4's list. |
| 3.87 | **Done on `pilot-fixes-6`, one commit each** (2026-10-05/06): an audit that failed is `AUDIT_FAILED` (B6, `8b8a6dc`); the Stripe SDK prints nothing in any process that loads the app (`d0b7426`); `redis_watch.stop` gives up after 5 s (`a5477c5`); the usage lines and `verify_e2e.py` no longer call an unreadable 200 "not billed" (`1ac96bd`); a missing SerpApi key is `SERPAPI_KEY_MISSING` and degrades detection (`2755f31`); provider dollar figures left the five files' comments (this commit; `cocitation.py` and `fix_generator.py` keep one each). **Open:** `audit.completed` logs component names only. | Claude | The `audit.completed` line, when convenient. |
| 3.88 | **Tier B counts every competitor domain on the platform** (B4's review): `domain_authority.load_platform_domains` leaves out of Tier B every client domain and every `Competitor.domain`, struck rows and next lists included, so a rival named on one client can move Citation Strength on an explicit re-score of any scan that cites that domain. In `avp_dev` today: 0 struck rows among 125 competitor domains and no next lists, so no effect. The founder's leaning: count only rows not struck, in sets that belong to a scan. That changes the formula's input, so it needs a `FORMULA_VERSION` bump (stored scores are never re-scored). | Founder | Decide; Claude builds it with the version bump after the founder's go. |
| 3.89 | **Detect still opens a QUEUED scan** (B4): `POST /clients/{clientId}/competitors/detect` (paid-up agencies only) opens or reuses a queued scan, although after B4 the list goes to the client's next list and that scan holds nothing. The allowance counts the row for 15 minutes from its creation (`IN_FLIGHT_WINDOW`; nothing submits it), then not at all; it is never reaped, and the client's next scan reuses it and is counted from when it is submitted. **Keep:** nothing changes. **Drop:** a detect press opens no scan, so it no longer holds one of the month's 40 slots for those 15 minutes, and an agency at its cap is no longer refused a scan because of a recent detect press; the scan count is otherwise the same. The tests that rely on the placeholder change with it. | Founder | Keep or drop. |
| 3.90 | **Decided 2026-10-06, built on `pilot-fixes-6`**: `PUT /clients/{clientId}/competitors` refuses a platform or publisher typed as a rival's domain, by detection's own list, with "That is a platform or publisher, so it cannot be added as a rival." A next list that already holds one is refused on its next save, naming the row. | — | — |
| 3.91 | **Decided 2026-10-06: kept.** Removing any seat clears the invitation link shown once, as 3.70 decided and tested. | — | — |
| 3.92 | **The Google sign-in link has no edge in forced colours** (3.40's review): its 1px border is an inset shadow, which forced colours drops, so the link has no visible outline at rest (it shows focus since 3.40). | Claude + ui-reviewer | A real border (or an outline) that forced colours keeps. |
| 3.93 | **F1 emails addresses nobody has verified** (F1's security review): scan emails are on by default for every user once the founder turns F1 on, and no address is verified (no email verification exists), so a password sign-up made with someone else's address could make us email that person client names it chose. Bounded by the scan allowance and a card on file; the invitation email has the same exposure today. | Founder | Accept for the pilot, or send scan emails only to users with a Google sign-in. |
| 3.94 | **Done on `pilot-fixes-7`** (`26cc941`, the founder's brief of 2026-10-07): a brand's 2-4 character short form (the domain label or the name, ASCII), upper-cased, counts as a mention when it appears in capitals as a whole word, for the subject and rivals alike; "rmit", "Rmit" and "RMIT-accredited" do not; a stop list of ordinary capitalised words and generic acronyms (IT, AI, HR, SEO, NSW, …) is skipped, ART is not; a short form two brands share goes to the subject, else the first rival listed. `FORMULA_VERSION` `v7` (`v6` was the rejected experiment, never stored); nothing re-scored. | — | — |
| 3.95 | **Done on `pilot-fixes-7`** (`88b9663`): a generated fix that names a domain the scan did not cite (the client's own is allowed), or uses a proportion word such as "largely" instead of a count, is dropped; shiksha.com and collegedunia.com join detection's platform-and-publisher list. | — | — |
| 3.96 | **Done on `pilot-fixes-7`** (`7f63842`): the PDF and the web report drop the agency's slug and the beat names; PDF table cells and headers wrap to two lines, a continued table repeats its header, a heading never ends a page and the footer never takes a page of its own; the pitch heading reads "<t> today. Up to <a> if these fixes close every gap they address (<t> + <d>).", <d> the difference of the two printed numbers. | — | — |
| 3.97 | **Done on `pilot-fixes-8`** (`67a23d2`, decided 2026-10-08): an explicit re-score (`POST /scans/{scanId}/score`) of a scan whose brand matches were made before `v7` is refused with a 409 that says to run a new scan: answer text is not kept, so the matches cannot be re-made. `scans.matcher_version` records the rule a scan's own run matched it under (written at its RUNNING transition); migration `5863629b56ab` marks a scan `v7` only when its own run scored it under `v7`. The agency check still comes first. `MATCHER_VERSION` moves only when what a scan stores changes. | — | — |
| 3.98 | **A short form inside a rival's longer name still names the subject** (3.94's review): "RMIT Vietnam is cheaper" names RMIT University too, as a long name already can ("Monash" inside "Monash Malaysia"). | Claude | When a scan shows it: drop a brand's match that lies inside a longer rival's match. |
| 3.99 | **Done on `pilot-fixes-7`** (`8f0df7b`, the founder's decision of 2026-10-08; found by 3.96's UI review): the pitch heading asked whether the two PRINTED figures differ while the paragraph under it, and the PDF's figure rows, asked whether the RAW recoverable points were above zero. They disagreed when points worth recovering did not move the rounded score (**corrected on `pilot-fixes-8`:** no self-consistent report reaches it — the gaps sum to 100 minus the composite and a fix needs at least 2 points — so it stays as a guard), so the paragraph printed the same number either side of a gain. One helper now feeds both, and that case reads "Closing the gaps listed above recovers 2.0 points, which does not change the rounded score of 100." on the web and the PDF alike. | — | — |
| 3.100 | **Decided 2026-10-09, done on `pilot-fixes-8`** (`02ad7f1`): the pitch keeps the rule built in A1 (every total is the sum of the figures printed above it); the web paragraph ends "That is today's score plus the points above, not a forecast."; where the fixes' printed points carry the score past 100, the equation is dropped: "…recovers 29.6 points, which takes the score to 100, the most a score can reach." (web and PDF, pinned in both languages); and every printed gap is rounded half-up once, as the fix list rounds it. | — | — |
| 3.101 | **Done on `pilot-fixes-8`** (`073239b`, A1): the pitch's three figures re-add as printed on the PDF, the web and the CSV; the ceiling was rounded a second time, to 1dp (RMIT's 72.48 + 25.9 printed as 98.40). No formula change. | — | — |
| 3.102 | **Done on `pilot-fixes-8`** (`5d0e121`, A3): the API's access log keeps the method, the path and the status, and no longer the visitor's IP address (`logs.DropClientAddress`); `--ws none` and `--log-level info` on the entrypoint close the two other ways uvicorn could print it (a WebSocket handshake line, and `UVICORN_LOG_LEVEL=debug`). Rate limiting and `CLIENT_IP_HEADER` are untouched. `/privacy`'s `:204` now says more is kept than is: listed for the lawyer under 2.4. | — | — |
| 3.103 | **Done on `pilot-fixes-8`** (`02f121d`, A4 and A5): every one of the fifteen lines the fix generator writes carries `scan_id`; `fixes.generated accepted=5` beside six listed fixes is as designed (the sixth is the deterministic citation fix, which the generator never sees). | — | — |
| 3.104 | **Done on `pilot-fixes-8`** (`89c8e6c`, A7): `runs-on: ubuntu-24.04` in every job, before `ubuntu-latest` moves to 26.04 (19 October 2026); each action on its first Node 24 major (checkout v5, setup-node v5, setup-uv v7, pnpm/action-setup v5, setup-buildx v4, build-push v7), each read from its own `action.yml`. The first CI run after the push proves them. | — | — |
| 3.105 | **Market scope — planned, its eleven decisions accepted 2026-10-09** (`docs/plans/market-scope.md`, branch `market-scope-plan`, `794c343`, pushed, no PR; continents and several-country scopes are out of this version on purpose). Found while mapping it: **a brand's own site in another country counts as an independent, top-tier source.** `rmit.edu.vn` is Tier A (`edu`), the rule for "the brand's own" is exact equality, so every answer that names RMIT and cites RMIT Vietnam credits RMIT's Citation Strength; it is also eligible for "Get onto …" (4 citations, the threshold). The plan's first item fixes that on its own, before any market scope: **M1 is done on `m1-other-sites`** (3.109); M2 to M4 remain. Also: today's prompts can ask "near me" (the generator's own example) with no location sent to any engine. | Claude | Build M2 to M4, each as its own branch after the founder's go. |
| 3.106 | **Small, found on `pilot-fixes-8`, not fixed:** the gap beat prints a gap with `toFixed(1)` while the fix list rounds it half-up first, so an exact half (28.95) reads 28.9 in one and 29.0 in the other (about 0.6% of gaps); Perplexity's docs no longer list the `search_context_size` the adapter sends (`engines.py:939`), so it may be ignored (unconfirmed); and `src/app/settings/page.test.tsx`'s "costs the page no wait at all" asserts within a time window and failed once under a load average of about 12 (passed 5/5 alone), as did `test_browser_slot.py`'s "the second waits for the browser" (a 100 ms wait measured 91; passed 3 runs alone). | Claude | Round once in `derive.ts` and `ReportView.tsx`; one live Perplexity check; give both timing tests a fake clock. |
| 3.107 | **Done on `pilot-fixes-8`** (`34ae3c9`, A2, the founder's sentences of 2026-10-09): the report's opening line names the biggest-gap dimension with its own figure (RMIT University: "…is named in 97% of unprompted answers, but rivals take 70% of the mentions."). It falls back to the band line where a sentence could not be true: a score before v2; Citation Strength before v5 or with no rival ahead; a figure that would round to a false 0 or 100. One shared table holds the PDF and the web to the same sentence. | — | — |
| 3.108 | **Decided 2026-10-09, done on `pilot-fixes-8`:** the opening line does not lead with a biggest gap under 2 points (it uses "…is one of the names these answers reach for."), and the web's line at the cap ends "It is not a forecast." | — | — |
| 3.109 | **Done on `m1-other-sites`** (market scope M1, the founder's brief and decisions of 2026-10-09): a brand's own sites in other countries are never a third party. `8324a8f` the rule (proposed under the Public Suffix List's ICANN section only, never `rmit.github.io` and the like), the lists, the routes and migration `89403ce44352`; the competitor editor refuses a rival on a confirmed site ("rmit.edu.vn is confirmed as RMIT University's other site. Remove it there first.") and detection skips one. `a366d3e` attribution and scoring, `MATCHER_VERSION` and `FORMULA_VERSION` `v8`, no stored scan re-scored. `ff88b28` the report's line "RMIT University's other sites: rmit.edu.vn — 4 citations" and the CSV's attribution. `b4683fb` the **Sites with the same name** card on the client's Sources tab. `eda4fc3` its collapsed **Dismissed (n)** list with an Undo for each (3.111). | — | — |
| 3.110 | **Small, found on `m1-other-sites`, not fixed** (nits of the second security review; the same agency has to cause them, and `v8`'s precedence rule decides the outcome): a competitor save and an other-sites save at the same moment can each pass its check and leave a rival on a confirmed site; and a struck rival keeps its confirmed sites and gets them back when named again at the same host, even if one was confirmed for another brand meanwhile. | Claude | Lock the client's row at the start of both saves; clear a rival's sites when it is struck. |
| 3.111 | **Decided 2026-10-09, done on `m1-other-sites`:** (1) the own-sources table's empty message "No answer cited rmit.edu.au." and the card's and refusals' wording stay as written; (2) a site marked **Not theirs** can be taken back: a collapsed **Dismissed (n)** list under the card, each with **Undo** (`eda4fc3`), and the card's empty line reads "No site with the same name is waiting for a decision." when everything cited was dismissed. | — | — |
| 3.112 | **Small, found on `m1-other-sites`, not fixed:** the other-sites line is tied to its table only by position (a screen reader moving table by table does not meet it; `aria-describedby` needs a prop on the design system's table); the PDF sets the line at size 10, to check on a rendered report; and the client's own domain is still matched exactly, so a host cited in its other spelling (`xn--bcher-kva.de` for `bücher.de`) is a third party to itself, as before M1. Seen in the production-build screenshots of the card (2026-10-09): collapsed, **Dismissed (n)** is muted text with no chevron, so it does not look like it opens; and the quiet buttons keep their inner padding, so where one starts a line (the toggle; every row at phone width) its label sits about 13px right of the text edge. | Claude | The table prop; one look at a rendered PDF; matching the main domain on its DNS form is its own item; a chevron on the toggle and a flush quiet button, once the design system has them. |
| 3.113 | **(sec) A save of the competitor list can strike a rival another writer just added** (found on `f5-prompt-fallback` by F2's security review; older than F2, whose suggestion card is a second writer). `PUT /clients/{id}/competitors` replaces the whole list and checks nothing about the list the writer read: if the report's competitor editor was opened before someone pressed **Add as a rival**, its save turns the new rival into a struck manual entry, so later scans skip it and detection cannot bring it back, and nothing says so. Both writers are members of the same agency. | Claude | The body carries the version of the list it was built from; the `PUT`, which already locks the row, answers `409` "The list changed since you read it." when it moved. A contract change, so its own item. |
| 3.114 | **Founder: what counts as "named" for a probable rival** (F2). The scan's own matcher is used unchanged, so an acronym counts ("UAC" in an answer makes uac.edu.au a probable rival, where the founder's example kept it a "Get onto" target), a long common-word label counts wherever the word appears (health.gov.au), and a label of six letters or more is also found inside a longer word. | Founder decides, Claude builds | Keep it; or count a name only where the answer writes the label capitalised, or the full domain (this misses lower-case mentions). |
| 3.115 | **The fix generator can still name a probable rival** (F2, found, not fixed): `fix_runner.collect_facts` gives the model `top_cited_domains`, which can include one, so a generated fix could say "get onto deakin.edu.au" in its own words. | Claude | Drop the scan's named domains from the facts the fix generator is given. |
| 3.116 | **(sec, defence in depth) Nine reads select a client's scans by the client's id alone** (found by F6 section 3's security re-review): the client history (`client_history.py`), the alerts' scan count and baseline, the client's scan list, crawler access, answer gaps (twice), competitors and the auto-rescan check. Each sits behind `get_client`, which is scoped to the agency, and the app never writes a scan under another agency's id, so nothing leaks today. The one-line change to the history was proposed on this branch; its edit was declined in the session, so it was not made. | Claude, on the founder's go | Add `Scan.agency_id == client.agency_id` to each, with a test on a row that names the client under another agency's id (`_stray_scan` in `test_question_lines.py`). |
| 3.117 | **The past-year flag counts only the answer shelf** (F6 section 3, found, not fixed): the shelf holds a scan's first 20 questions (`MAX_SHELF_PROMPTS`) and a set holds 20 to 30, so a later question that names a past year is neither counted nor listed, and when only such questions do, no line appears. | Claude | Count and list from the scan's own prompts, in both twins. |
| 3.118 | **Small, found on `f5-prompt-fallback`, not fixed:** the third free prompt failure's sentence does not say that the next one counts; while a client page loads, the shell's `<nav>` is an empty landmark; a paraphrase of a refused comparative claim ("rivals lack this") passes the fix generator's guard, which lists whole phrases; the web's API helper puts a client's id into a path unencoded (`lib/api.ts`; the ids are the server's own); the Prompts tab's empty state says "A scan tests the questions it thinks this client's buyers ask.", which reads as if every scan writes its own, with straight apostrophes; at 320px **Write new questions for the next scan** leaves "scan" alone on its second line; and, older and unconfirmed in the live app, the Overview's score chart renders at 300 of its 420px in a static render. | Claude | A warning on the third free failure; hide the empty landmark; widen the phrase list when a scan shows one; `encodeURIComponent` in `lib/api.ts`; one true sentence for the empty state; a balanced wrap and the chart frame when the design system has them. |
| 3.119 | **Decided 2026-10-11: a Google-only account stays Google-only for now** (revisit later). A password account that signs in with Google loses its password (F2 of `fix-first`), a reset never reaches an account with no password, and Settings says "This account has no password, so it signs in through Google only." | — | — |
| 3.120 | **Decided 2026-10-11: a global ceiling of 300 Google sign-in starts per 15 minutes, a setting; built in A6 part 2 (2026-10-22)** (F4's security review, Medium: the per-IP limit of 30 per 15 minutes bounds each address, not the total). A global counter is checked first, and a refusal there writes no IP key. Past it only Google sign-in is refused, with a sentence saying so; password sign-in keeps working. Built: `GOOGLE_START_LIMIT_GLOBAL` (300, at least twice `GOOGLE_START_LIMIT_PER_IP`) counts admitted starts only, in the same Redis step as the address's count, so one address's refused attempts cannot use it up; past it the browser comes back with `reason=busy` and the founder's two sentences (signed out, signed in); a request that is not a person's own navigation, by its Fetch Metadata headers, goes to the front door uncounted. Open for the founder: while it is full, an account with no password (created with Google or linked to it) cannot sign in at all (merge notes item 13). | Claude (`hardening-auth`, A6) | — |
| 3.121 | **Decided 2026-10-11: a user holds at most 10 sessions, a setting, and the 11th sign-in signs out the oldest; built in A3 part 2 (2026-10-21)** (F4's security review, High, pre-existing: a login loop, or the Google callback, could otherwise fill Redis). One script in `SessionStore.create` (`SESSION_MAX_PER_USER`, 10, 1 to 100; the oldest by sign-in time goes, in the same step as the sign-in), and a runbook step to find and revoke the largest set. | Claude (`hardening-auth`, A3) | Nothing to set on Render. Optional before the deploy: the runbook's read-only listing of the longest session lists. |
| 3.122 | **Decided 2026-10-11: a platform cap of 10 competitor detections a day, a setting** (D2's security review, A3). It counts presses of **Detect competitors** (a scan's own detection is under the scan caps): a `detections` kind in `spend_caps`, claimed after the plan check. The runbook notes that SerpApi's Free plan runs out at about 40 detections a month whatever the cap allows. | Claude (`hardening-api`, B5) | — |
| 3.123 | **Decided 2026-10-11: after a Google link, Settings → Team shows the holder, once, the people who already had access** (F2's security review, High, partly taken): seats and accepted invitations from before the link, each with **Remove**, and **Keep these people** to dismiss the list. It shows only when there is someone to review. A migration records when the link happened. The structural fix is still email verification at sign-up once email is on (2.3). | Claude (`hardening-auth`, A6) | — |
| 3.124 | **(sec) Two owners removing each other at the same moment** can both pass the last-owner check and leave an agency with no owner (pre-existing; F1's security re-check). | Claude (`hardening-auth`) | A row lock on the agency in `DELETE /users/{userId}` before counting owners. |
| 3.125 | **Built in A3 part 3 (2026-10-22): `users.sessions_valid_after`, set by every writer that ends a person's sessions (a seat's removal and revival included) and checked on every request; a password sign-in's read-back after minting takes a share lock, so it waits for a change in flight.** **(sec) A session minted between a Google link's two revocations survives a post-commit failure** (F2's re-check, B2). | Claude (`hardening-auth`, A3) | A credential version (`sessions_valid_after`) checked on every request. Until then, 1.1m says what to do on `google.link.post_commit_revoke_failed`. |
| 3.126 | **(sec) Google sign-in activates an invited seat without its invitation token** (pre-existing; F2's review, Medium). Someone who invites another person's address into their own agency captures that person's first Google sign-in there. | Claude (`hardening-auth`, A6) | Accept an invitation through Google only with the token in hand, or after an explicit "join X?" step. |
| 3.127 | **(sec) Postgres's own error detail can carry an email to Render's log** (F5a's security review, Medium, pre-existing). Two simultaneous sign-ups with one address: the second insert's unique violation leaves as an unhandled 500 whose `DETAIL` names the address. Also asyncpg's client-side encoding errors quote a value. Completes 3.5. | Claude (`hardening-api`, B10) | Catch `IntegrityError` at the user insert (`email-already-registered`); one error handler that logs only the exception class and the constraint name, and answers a generic 500 with a request id. |
| 3.128 | **Decided 2026-10-11: today's behaviour is kept.** A successful login gives back only its own hit, and a test pins it. The brief's F5 asked to "release only the email key on a successful login"; it was built and then reverted on `fix-first` (F5b), because a success never refunded an IP's failed guesses, and counting successes against an address would refuse offices and VPNs that share one. | — | — |
| 3.129 | **(sec) Invitation accept and reset confirm hold their row locks, and a database connection, while they wait for a hashing slot** (up to 10 s; F5b's security re-check, Low). A burst of valid tokens could tie up the pool. | Claude (`hardening-auth`) | Check the token unlocked, end the read, hash, then lock and check again, as login now does. |
| 3.130 | **Decided 2026-10-11** (the eight decisions sent with the audit of 2026-10-10): (1) a new outside service ships switched off by default, and its `/privacy` line goes to the lawyer brief, so D10's breached-password check ships off; (2) while email is off, the reset screen stays as it is, the same for every address, and D9's sentence comes with email; (3) sign-up with an address that already has an account gets one neutral refusal, "We couldn't create an account with that email. If you already have one, sign in or reset your password.", taking the same time as a real sign-up, and "Check your inbox to finish" once email is on; (4) a session ends after 72 hours idle or 14 days in all; (5) F3's caps are kept; (6) billing checkout and the billing portal are owner-only, built in Phase 1; (7) W11 (the landing redesign) gets its own branch after PR 4; (8) a phase branch starts from `origin/main` after the merge, and stacks on the previous one only while a merge is pending. | Claude (`hardening-auth`: (1) and (3) in A1 and A4, (4) in A3, (6) after A7) | — |
