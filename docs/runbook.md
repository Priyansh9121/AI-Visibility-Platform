# Operator runbook

Tasks the operator does by hand on the running service. Each one names the
exact command and what it prints. Started on 2026-09-29.

## Reset someone's password by hand

**When:** a password user asks for a reset. Email is off in production (no
verified sending domain at Resend yet), so the forgot-password screen tells
them to email the operator, "and we'll reset it by hand". This is how.

**Where:** the Render dashboard → the **API** service
(`ai-visibility-platform-5gk4`) → **Shell**. Run:

```bash
cd /app/apps/api && .venv/bin/python scripts/issue_password_reset.py --email 'person@example.com'
```

Put the account's address in the quotes. The shell already has the service's
`DATABASE_URL` and `PUBLIC_WEB_BASE_URL`, so the link points at the live web
app.

**What it prints:**

| Outcome | Exit code | Output |
|---|---|---|
| Link issued | 0 | `Reset link for user_… — it works once and expires in 60 minutes:` and the link on the next line |
| No account with that address | 1 | `No such user.` |
| Signs in with Google only | 2 | Refused: they should use Sign in with Google |
| Suspended, or invited but never joined | 2 | Refused: the account is not active |

It may also print a `password_reset.request.minted` line with the user id.
That is the service's own log line, and it carries no token and no address.

**Then:**

- **Send the link to the account's own address,** by replying to the
  person's email. Never send it to a different address someone gives you:
  whoever holds the link can set the password.
- The link works **once** and expires after **an hour**. Issuing a new one
  cancels any earlier link.
- Opening it asks for a new password. Afterwards the person is signed out
  everywhere and signs in with the new one.

**What it does not do:** it logs nothing that carries the link, the token or
the address. The link appears only in your shell. It mints the token through
the same code as the forgot-password form: the same hashed storage, the same
expiry, and no second way in.

**If the Shell tab is missing** (Render offers no shell on some instance
types), run the same script from a checkout, pointed at production:

```bash
cd apps/api
DATABASE_URL='<the API service's DATABASE_URL, from Render → Environment>' \
PUBLIC_WEB_BASE_URL='https://ai-visibility-platform-1.onrender.com' \
  uv run python scripts/issue_password_reset.py --email 'person@example.com'
```

Both variables are required. Without `PUBLIC_WEB_BASE_URL`, the link points at
`localhost`.

## Live Stripe test (test mode on Render)

Set up a subscription payment and verify that events apply once and in order.
All of this happens in Stripe's **Test mode**. Never use a live key.

1. **Before you start.** Everything happens in Stripe's **Test mode**. Never
   paste a live key (`sk_live_…`) anywhere. `charges_enabled: false` is expected
   in test mode. Deploy this change while the webhook endpoint shows no pending
   retries in Stripe: the ordering watermark starts empty, so the first retry
   after deploy applies whatever its age.

2. **Price.** In the Stripe dashboard (Test mode), open Product catalogue and
   find or create the product with a recurring monthly Price of US$150 (the
   price since 2026-10-04). Copy its Price ID (`price_…`).

3. **Render.** On the API service (`ai-visibility-platform-5gk4`), open
   Environment. Set `STRIPE_PRICE_ID` to that id. Confirm `STRIPE_SECRET_KEY`
   is the test secret key (it starts `sk_test_`) and `PUBLIC_WEB_BASE_URL` is
   the web URL. Save; Render redeploys.

4. **Webhook endpoint.** In Stripe, go to Developers → Webhooks → Add endpoint.
   The URL is
   `https://ai-visibility-platform-5gk4.onrender.com/api/v1/billing/webhook`. Select exactly these
   events: `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted` and
   `customer.subscription.trial_will_end`. The fifth only writes a log line
   (`billing.trial_will_end`, a few days before a free trial ends); it changes
   nothing, and the endpoint works without it. Save,
   reveal the endpoint's Signing secret (`whsec_…`), set it as
   `STRIPE_WEBHOOK_SECRET` on the Render API service, and save.

5. **Customer portal.** Go to Settings → Billing → Customer portal. Allow
   customers to cancel subscriptions at the end of the billing period and to
   update payment methods. Save. The code cannot see this setting; cancellation
   depends on it.

6. **Subscribe.** Sign in to the web app as the owner of a test agency and
   open **Settings**, and press **Subscribe** on the plan card (it reads
   "Opening checkout…" while Stripe loads). Pay with Stripe's
   test card `4242 4242 4242 4242`, any future expiry and any CVC.

7. **Check afterwards:**
   - (a) Settings shows the plan as active, with "Renews" and a date.
   - (b) In Stripe → Webhooks → your endpoint → Event deliveries, each of the
     four event types answered 200. The subscription events answer
     `{"received":true,"handled":true}`. (The fifth, `customer.subscription.trial_will_end`,
     is sent only for a free trial, a few days before it ends; it answers the same and
     writes only a log line.)
   - (c) Press **Resend** on one delivered `customer.subscription.updated`. It
     answers `{"received":true,"handled":false,"duplicate":true}` and changes
     nothing.
   - (d) On the Render API service's Logs, there are `stripe.webhook.applied`
     lines and no `stripe.webhook.rejected`.
   - (e) Press **Manage billing** in Settings and cancel in Stripe's portal.
     Settings then shows "Ends" and the date, and the `customer.subscription.updated`
     delivery answered handled.
   - (f) Link expiry. This needs an agency that has not paid, so use a second
     test agency, or do it before step 6. Press **Subscribe** in Settings and
     leave the Stripe page unpaid. More than an hour later, open the same Stripe
     page again: it no longer accepts payment, and Stripe shows it as expired.
     Press **Subscribe** again in Settings: it makes a new link, and that one
     pays.
   - (g) Subscribing again after a cancellation. Step (e) cancels at the end
     of the period, so the subscription stays active until then. Instead, in
     the Stripe dashboard (Test mode) open the test subscription and cancel it
     immediately. When its `customer.subscription.deleted` delivery has
     answered handled, open Settings in two browser tabs: both show the plan
     as Canceled, with Subscribe. In the first tab press **Subscribe** and pay
     with the test card; Settings shows the plan active again. In the second
     tab, which still shows Subscribe, press **Subscribe**: it answers "This
     agency already has a current subscription. Manage it from Manage billing
     in Settings." and shows **Manage billing**, and no second checkout opens.

8. **Stop.** Nothing is ever switched to live mode from this runbook. Going
   live is the founder's key swap after Stripe's business verification.

## Clearing test-mode billing state at the live-key swap

**When:** once, when going live. Everything the API stored about Stripe so far
came from Stripe's Test mode. A live key cannot see any of it, and an agency
that still holds a test subscription id would look already subscribed. Run this
**after** the live keys are saved on Render and the API has redeployed, and
**before anyone subscribes in live mode**: between the key swap and the first
live checkout (launch checklist 2.2, between steps 3 and 4).

**Where:** only the Render dashboard → the **API** service → **Shell**. The
script reads the live key from the service's own environment, so it runs only
there. Open the Shell on the **new deploy**, after the redeploy has finished:
a Shell opened earlier still has the test key and the script will refuse. If
there is no Shell tab, stop and ask Claude.

If the key is an `rk_live_` restricted key, it needs read access to
**Subscriptions** and **Customers**, or every run ends with "could not check".

Dry run first; it writes nothing:

```bash
cd /app/apps/api && .venv/bin/python scripts/clear_test_billing_state.py
```

Read the counts, then clear:

```bash
cd /app/apps/api && .venv/bin/python scripts/clear_test_billing_state.py --confirm
```

**What it prints:** counts only, never an id, an email, a name, a Stripe id or
any part of the key. `Before:` lists how many agencies there are in all and how
many have each Stripe column set (for `subscription_cancel_at_period_end`, how
many are true), then one `Stripe key:` line. The dry run with a test key says
`Stripe key: not live; Stripe was not checked; --confirm would refuse.`, which
means the swap has not reached this Shell. With a live key it says
`Stripe key: live; Stripe lists no subscription and no customer.`

`--confirm` runs only with a live key. It clears every Stripe column on every
agency, soft-deleted ones included, in one transaction. It asks Stripe whether
the live account has any subscription or customer twice: once before touching
anything, and again while the change is held uncommitted (within 10 seconds).
It commits only if both reads list none. If a read lists one, or anything fails
before the commit, nothing changes. It prints `After:`, where every
Stripe count should be `0`, and `Cleared.`. It leaves the `stripe_webhook_events`
ledger alone (test and live event ids never collide).

**Exit codes** (every refusal writes nothing; the dry run makes the same Stripe
reads and refuses the same way):

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | Counted (dry run) or cleared (`--confirm`) | Carry on with step 4 |
| 1 | Bad arguments | Run it as shown above |
| 2 | The live Stripe account already has a subscription or a customer | Stop. Someone has used live mode; clearing would orphan them. Do not clear by hand; ask Claude |
| 3 | Stripe could not be asked (error, timeout, or a key that cannot read Subscriptions and Customers) | Check the key's permissions and Stripe's status, then run it again |
| 4 | `--confirm` with a key that is not live (test, missing or unrecognised) | The live key is not in force in this Shell: check the saved keys and that the redeploy finished, open a new Shell, and start from the dry run |
| 5 | A database error stopped the run (the database could not be reached, or a statement failed). It prints no detail | Run the dry run to see the current state, then decide; if it repeats, ask Claude |

## The free trial, and what it depends on in Stripe

`STRIPE_TRIAL_DAYS` on the Render API service is the trial's length; 0, the default, is no
trial. It is the founder's to turn on, in test mode first. Two settings in the Stripe
dashboard decide how it behaves, and the code cannot see either:

- **Trial-ending reminder email.** Stripe can email a customer before a trial ends. AVP
  sends no email of its own (Resend is off), so that reminder, if wanted, is Stripe's: a
  Billing setting in the Stripe dashboard, and the founder's decision.
- **How the portal cancels** (Settings → Billing → Customer portal, step 5 above): at the
  end of the billing period, or at once. Either way a trial cancelled before its end is not
  charged. At the period's end the agency keeps the plan until the trial ends and Settings
  shows "Ends" and the day; cancelled at once, it loses the plan, and the allowance with it,
  at that moment.

To rehearse the first charge, use a Stripe test clock in the dashboard (Test mode) to move
the clock past the trial's end: the subscription goes from `trialing` to `active`, and
Settings drops "Free trial until" for "Renews". Do it with the test card, as in the steps
above.

## Confirm how Render passes the client IP (rate limits)

The per-IP rate-limit bucket uses uvicorn's client address, which is the
leftmost `X-Forwarded-For` entry. If Render appends to a client-sent
`X-Forwarded-For` instead of replacing it, anyone can pick their own bucket.
The per-email limits hold either way.

Since branch `pilot-fixes` the browser reaches the API through the web app
(`/api/v1` on the web host, a Next.js rewrite to `API_PROXY_TARGET`), so the
probe goes to the **web** host: that is the path every real request takes.

1. **Why.** Spoof-safe per-IP rate limiting requires that the server trusts
   a proxy header Render adds, not one a client controls.

2. **Probe the default.** From one machine, send 11 `POST
   https://ai-visibility-platform-1.onrender.com/api/v1/auth/reset-password/request`
   requests, each with a different made-up email address (for example
   `probe1@example.com` … `probe11@example.com`) and a different header
   `X-Forwarded-For: 198.51.100.N`. Use:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     -H 'Content-Type: application/json' \
     -H 'X-Forwarded-For: 198.51.100.N' \
     --data '{"email":"probeN@example.com"}' \
     https://ai-visibility-platform-1.onrender.com/api/v1/auth/reset-password/request
   ```

   Replace N each time (1 through 11). Email is off in production, so nothing
   is sent.

3. **Read the result.**
   - If the 11th answers **429**, send one more request from a **different
     network** (a phone hotspot, say) without the header override. It must
     answer **200**: then Render replaces the header and the default is safe;
     stop here. A **429** means every request shares one bucket: on the web
     service, check that `API_PROXY_TARGET` is the API's internal address
     (Render → API service → Connect → Internal), not its public URL, and
     probe again after the redeploy.
   - If it answers **200**, the header can be spoofed; go on.

4. **Switch to Cloudflare's header.** On the Render API service, open
   Environment and set `CLIENT_IP_HEADER=CF-Connecting-IP`. Save; Render
   redeploys. Never set it to `X-Forwarded-For`, because its first entry is
   the one a client controls.

5. **Probe again.** After the deploy, repeat step 2, varying a
   `CF-Connecting-IP` header instead and sending no `X-Forwarded-For`. The
   11th must answer 429. If it does not, undo step 4 and report it.
   Then, while that bucket is still full, send one more request from a
   **different network** (a phone hotspot, say) without the header override.
   It must answer 200. A 429 here means every request is sharing one bucket
   (the header is not arriving): undo step 4. Finally, open the API service's
   Logs and check there is no `auth.rate_limit.ip_header_missing` line.

6. **Note.** Each probe run uses up the prober's own IP bucket for an hour. If
   the service logs `auth.rate_limit.ip_header_missing`, requests are
   arriving without the header and are sharing one bucket; undo step 4.

**Rotating `APP_SECRET`** renames every rate-limit key (each ends in a hash keyed
with it), so it resets every login, sign-up, reset and change-password window and
every client's 24-hour detection day. For the same reason a client's detection day
cannot be cleared by hand without that secret.

## Reading the billing price lines

**When:** a **deployed** API (staging or production) reads the Stripe Price named by
`STRIPE_PRICE_ID` once at start and then once an hour, when both `STRIPE_SECRET_KEY` and
`STRIPE_PRICE_ID` are set. A local API never does: starting it on a laptop calls nothing
at Stripe, whatever its `.env` holds. The check is a read; it changes nothing at Stripe.

**Where:** Render → the **API** service → **Logs**, by event name.

- `billing.price_check ok=True price_id=…`: the Price is active, licensed (a flat amount,
  not metered) and US$150 a month, billed every month: what the pricing card and Settings
  say.
- `billing.price_mismatch` (warning): it is not. Each field comes as `expected_…` and
  `actual_…`: the amount in cents (15000 is US$150), the currency, the interval, the
  interval count, whether the Price is active, and its usage type. An archived Price
  (`actual_active=False`) and a metered one (`actual_usage_type=metered`) are mismatches
  even when the amount is right: checkout cannot use the first and the second is billed by
  usage, not as the plan. The fix is the founder's: create an active, licensed US$150
  monthly Price in Stripe (test mode until 2.2) and set `STRIPE_PRICE_ID` on the API
  service to its id. The line repeats every hour until then.
- Each of the three lines above carries `trigger`: `watch` is the hourly task, and
  `checkout` is a checkout click made while a mismatch is recorded (that click reads the
  Price again before deciding, and waits at most 10 seconds for Stripe to answer). A
  `trigger=checkout` line therefore means a customer tried to subscribe while the Price was
  wrong. If that read confirms the mismatch, the click is refused and
  `billing.checkout_refused_price_mismatch` (error, `agency_id` and `price_id`) follows it.
- `billing.price_check_failed error_type=…` (warning): Stripe could not be read (a wrong
  key, the network, a timeout, or an answer that is not a Price). Only the error's class
  is logged, never its message. With `trigger=checkout` and `error_type=TimeoutError`, the
  click's 10 seconds ran out: the click went on without a price answer (a read that fails
  never refuses), and the mismatch stays recorded, so the next click reads again. The
  hourly task waits longer, up to 60 seconds.
- `billing.half_configured missing=…` (error, once at start, deployed only): billing is
  only part set up. `missing` names the one variable that is not set, and never a value.
  - `STRIPE_SECRET_KEY` or `STRIPE_PRICE_ID`: only one of the two is set. Billing then
    counts as not configured: checkout answers 503 and the scan allowance is off for
    every agency.
  - `STRIPE_WEBHOOK_SECRET`: the key and the Price id are set but not the webhook secret.
    Customers can pay, and every Stripe webhook is refused with a 503, so a payment is
    never recorded and the agency never shows as subscribed.

  Set the missing variable.

`STRIPE_LOG` does nothing on this service: the container's start script unsets it, because
the Stripe SDK would otherwise print every request, error message and body to stderr.

## If the logs show a second Stripe subscription

Checkout refuses a new subscription while one is live, expires older open
checkout links, and checks Stripe for a live subscription before creating a
new session. If two subscriptions still end up on one customer, the API logs
`stripe.webhook.second_subscription` at warning level with `agency_id`,
`stored_subscription_id` (the earlier one) and `incoming_subscription_id`
(the one AVP now tracks). The agency is billed twice until you act.

1. **Find it.** Render → the **API** service → Logs → search
   `stripe.webhook.second_subscription`. Note the three ids.
2. **Confirm in Stripe.** Stripe Dashboard → Customers → the agency's
   customer → Subscriptions. Two should be active or trialing.
3. **Cancel and refund the earlier one** (its id is `stored_subscription_id`),
   and refund its payment if one was taken. Keep the one AVP tracks.
4. **Check AVP.** In the agency's Settings → Billing, the plan should still
   show as current. Known gap: the webhook applies events for any
   subscription id, so the cancellation of the earlier one can mark the agency
   canceled while the kept one is still billing. If Billing shows canceled,
   make any small change to the kept subscription in Stripe (for example add
   a metadata key); Stripe sends a new `customer.subscription.updated`, which
   AVP applies as the newest event. Re-check Billing. Rehearse this in test
   mode before relying on it.

Never clear an agency's Stripe columns by hand (`stripe_customer_id`,
`stripe_subscription_id`, `subscription_status`, `stripe_event_at`) to start it
again or to make Settings look right: a row with none of them reads as an agency
that has never had a subscription, and checkout will offer it a second free trial.

## Reading the API's memory

**When:** a scan ended `EXECUTOR_LOST` with no deploy at the time, Render says
the API restarted for running out of memory, or you want to know how close it
runs to its limit. The API runs scans in its own process and drives a real
Chromium for two things: the technical audit of each scan, and the crawl when a
client is added with classification on. Measured on the API image (2026-10-02):
the idle server holds about 97 MB of its own (about 100–160 MB charged to its
container), one heavy page's audit peaks near 490 MB, and a crawl and an audit
running together reached about 586 MB. So one browser runs at a time per process,
and the API writes its own memory to the log, as numbers.

**Where:** the Render dashboard → the **API** service → **Logs**. Search for the
event name. Every line below carries numbers and our own ids and names, and
nothing else: no URL, no domain, no client name, no address.

**The lines**

| Event | When | Carries |
|---|---|---|
| `app.memory_at_start` | once, when the API starts | the five figures |
| `scan.phase_memory` | after each phase of a scan: `competitors`, `engines`, `audit`, `scoring`, `alerts`, `fixes`, `finalized` | `scan_id`, `phase`, the five figures |
| `browser.memory` | when a browser run ends: the browser is already closed, and the next run has not started | `kind` (`crawl` or `audit`), `scan_id` (an audit) or `client_id` (a crawl), the five figures |
| `browser.slot_waited` | a browser run had to wait for the one before it | `kind` (the one that waited), `scan_id` or `client_id`, `waited_ms` |
| `browser.slot_busy` | a browser run waited as long as it was allowed and was sent away without a browser | `kind` (the one sent away), `scan_id` or `client_id`, `waited_ms` |
| `browser.close_failed` | a page or a context would not close, or the browser did not close in time | `part` (`page`, `context` or `browser`), `error` (the error's class) |

The `scan_id` and `client_id` on the three `browser.*` lines about the slot are
our own opaque ids for what the run was for: ids only, never a URL or a domain.
Look the id up in the app, not in the log. A line from a script has neither.

**The five figures** are MB (1 MB is 1,048,576 bytes), whole numbers, or `null`
when they cannot be read:

- `process_tree_rss_mb`: the API process and every process under it (the
  browser), added up from `/proc`.
- `cgroup_current_mb`: the memory charged to the container, page cache included.
- `cgroup_anon_mb`: the part of it that is not cache.
- `cgroup_peak_mb`: the highest `cgroup_current_mb` the container has reached
  since it started. It is a running maximum, so it only rises: **a jump between
  two lines is the peak the work between them reached, and a jump on a scan's
  `audit` line is the audit's peak.**
- `cgroup_limit_mb`: the container's memory limit; `null` when there is none.

`null` is normal on a laptop (macOS has no `/proc` and no cgroups) and wherever
a file is missing, such as `memory.peak` on an older kernel. A reading that fails
is `null`; it never stops a scan, a request or the start-up.

**After the first browser run of a deploy, `cgroup_current_mb` stays well above
`cgroup_anon_mb`** — 150 to 260 MB above it on a 2 GB instance in the
measurements. That is page cache: Chromium's own files, read from disk, which the
kernel keeps while memory is free and drops when it is needed (under a 512 MB
limit it fell to about 5 MB). It is not a leak, and it is not what gets a
container killed; `cgroup_anon_mb` is. A memory graph that counts page cache
shows the API's resting memory rising after the first scan or client of a deploy
for this reason.

**How to read it**

1. `app.memory_at_start` is the baseline: what the process holds before it
   serves anything.
2. Follow one scan by its `scan_id`. Compare `cgroup_peak_mb` on its `engines`
   line with its `audit` line: the difference is what the browser added.
   `cgroup_peak_mb` close to `cgroup_limit_mb` means the container came close to
   being killed.
3. `browser.memory` is taken after the browser is closed, so
   `process_tree_rss_mb` there should be back near the baseline. If it stays
   high run after run, something is keeping memory.
4. `browser.slot_waited` with a long `waited_ms` means a browser run queued
   behind another. Occasional waits are the design; a client being added while a
   scan audits waits for it. Waits that keep growing mean a run is slow or stuck.
   A wait has a bound (60 s for a request, 120 s for a scan's audit): see 6.
5. An audit that ends with the code `TIMEOUT` was cut by its cap: a step in the
   browser never returned, for example a page whose script never let the read
   finish. The cap is 244.5 s for the audit and 266 s for the crawl intake asks
   for (arithmetic in `technical_audit.audit_run_cap_s` and
   `crawl.crawl_run_cap_s`), and a run that is cut closes its browser and gives
   the slot back. Launch checklist 3.20.
6. A `browser.slot_busy` line means a run waited for the browser as long as it
   was allowed and was sent away without one. What happens next depends on who
   asked. A scan's audit is recorded `failed` with `BROWSER_BUSY` and the scan goes
   on to scoring, alerts and fixes, without a Technical Foundation; the line carries
   the `scan_id`. A scan's audit waits at most 120 s (300 s until launch checklist
   3.35): long enough for a run ahead that finishes, and runs take seconds, but not
   for one that hangs until its cap cuts it (244.5 s for an audit, 266 s for the
   crawl intake asks for: item 5). So an audit that queues behind a hung run with
   more than 120 s of its cap still to run ends `BROWSER_BUSY` after 120 s, and its
   line has `waited_ms` near 120000. A request
   (adding a client with classification, reclassifying, or running an audit by
   hand) is answered **`429 rate-limited`**, "The crawler is busy with other pages
   right now. Try again in a minute.", with `Retry-After: 60`, and **writes
   nothing**: no client is created, the client keeps its classification, the audit
   already stored is kept; the line carries the `scan_id` or `client_id` it was
   for. So `BROWSER_BUSY` is only ever stored on a scan's audit, never on a
   client. It says nothing about the client's site, so do not read it as one. A
   few are the design under load; a run of them means a run ahead is slow, so look
   at the `browser.memory` lines and the `TIMEOUT` audits above.
7. Two more 429s on those three routes have no line of their own and show in
   Render's request log as a 429. "Your agency already has a site being read. Try
   again in a minute." is the limit of one site read per agency at a time.
   "The crawler is busy with other pages right now. Try again in a minute." is
   also what a fifth agency gets while four agencies' reads are inside (four at
   most, because each holds a database connection while it waits). A scan's own
   audit is never counted or refused by either limit. The per-agency count, the
   four-agency cap and the browser slot are per process AND per instance, and the
   API runs one worker per instance: during a deploy's overlap an agency can have
   one read on each instance.
8. To see why a scan's Technical Foundation was left out, read two lines for the
   `scan_id`. `audit.completed` carries the audit's `status`, its `error_code` (for
   example `TIMEOUT`, `BROWSER_BUSY`, `HTTP_503`) and `technical_foundation_reason`, the
   reason the dimension will be left out under (`AUDIT_FAILED`, or one of the three
   crawl reasons; empty when the audit did not fail). `scoring.completed` carries the
   reason it stored for each dimension in `excluded`. `NOT_YET_MEASURED` there means
   the scan has no audit row; a scan scored before 2026-10-05 whose audit had failed
   also says it. Launch checklist 3.87.

**Not in the logs:** a number for any one request, and nothing from before the
last restart: the counters belong to the container, and a restart starts them
again. The API image runs `tini` as PID 1, so the zombie `headless_shell`
processes a browser run used to leave behind are reaped; they cost no memory.

## Reading the Redis lines

**When:** everyone was signed out at once, a pilot says "it logged me out", or
you want to know how the instance is set. One Redis instance (Render Key Value)
holds the sessions, the rate-limit windows and the Google sign-in state. On the
Free plan (25 MB, nothing kept on disk) a restart empties it. The API writes four
lines about it, from K1 (launch checklist 3.4 is the setting they check).

**Where:** the Render dashboard → the **API** service → **Logs**. Search for the
event name. No line carries a key, an address or an id; the first carries one
word, the policy, and the memory lines carry sizes.

**The lines**

| Event | Level | When | Carries |
|---|---|---|---|
| `redis.eviction_policy` | info | once, when the API starts, just after `app.memory_at_start` | `policy`: the instance's `maxmemory-policy`, or `unknown` |
| `redis.data_lost` | warning | the API looked for its marker key and Redis no longer had it | nothing |
| `redis.memory` | info | when the API starts, then at most every 15 minutes | `used_mb`, `max_mb`, `used_ratio` (the last two are `null` when Redis has no limit, as on a laptop) |
| `redis.memory_high` | warning | with a `redis.memory` line whose `used_ratio` is at or above 0.5 (`REDIS_MEMORY_WARN_RATIO`) | the same three |
| `redis.memory_limit_unknown` | warning | once per API process, when a deployed Redis reports no memory limit | nothing |
| `redis.memory.check_skipped` | info | instead of `redis.memory`, when Redis refused or failed the read | `error`: the exception's class |

**`redis.eviction_policy`.** `noeviction` is what checklist 3.4 asks for: when
Redis is full it refuses writes (a sign-in then fails with an error) and deletes
nothing. Any `allkeys-…` or `volatile-…` policy means Redis may delete keys when
it is full, without saying so: sessions (people are signed out), the list that
lets a password change sign someone out everywhere, and rate-limit windows.
`unknown` means the API could not read the policy: Render refused the command,
or Redis could not be reached when the API started. The instance's settings page
in Render says what it is.

**`redis.data_lost`.** The API writes one small key, `avp:sentinel`, that never
expires, and looks for it when it starts and every 60 seconds after. If it is
missing, Redis was emptied since the last look. The one API process that finds it
missing writes it back and logs this line, once, so it shows within about a
minute of the wipe. A deploy alone never logs it: Redis keeps running through a
deploy and the new API finds the key. **It is also logged once the first time an
API starts against a Redis that has never held the key: the first deploy of this
change, and any new instance. That one is not a loss.** The marker is the one key
the API keeps with no expiry, on purpose. Every other key expires on its own (a
session after 14 days, a rate-limit window when its window ends, a Google sign-in
after 10 minutes), and `tests/test_redis_expiry.py` checks it for every key the API writes.

**`redis.memory` and `redis.memory_high`.** The API reads Redis's own memory
figures when it starts and then every 15 minutes, not every minute. `used_ratio`
is used memory over the instance's limit: 0.5 on the 25 MB plan is about 12.5 MB.
Under `noeviction` a full Redis refuses every write, so every sign-in and sign-up
fails; the high line is the warning before that. It never appears when the limit
is `null`. On Render the limit should read about 25: if `redis.memory_limit_unknown`
appears, or `redis.memory.check_skipped` replaces every `redis.memory` line, the
warning cannot fire. Check the instance's memory on its Render page by hand until
that is fixed.

**What a wipe costs.**

- *Lost:* every session (everyone signs in again), every rate-limit window (a
  login or sign-up count starts from zero, and competitor detection can be run
  again for every client because its 24 hours start over), and any Google sign-in
  in flight (the person lands back on the sign-in page with an error, or is told
  the sign-in can no longer be used, and starts again).
- *Not lost:* scans (they run in the API process and live in Postgres, not
  Redis), clients, reports, share links, billing, accounts.
- A session also ends on its own 14 days after sign-in, used or not, so someone
  signed out after two weeks is not a wipe.

**When to act (the founder's decision, 2026-10-03):** upgrade the Key Value plan
during the pilot when `redis.data_lost` appears and no wipe was expected, or when
the instance's memory is above 50%. Concretely, upgrade the plan when
`redis.memory_high` shows on two days in one week, when any `redis.data_lost`
appears with no wipe expected, or after a rate-limit storm (a burst of
`auth.rate_limited` lines, the Google sign-in start included, filling the windows).
Do not change the Key Value settings while
pilots are signed in: a change may restart the instance, and a restart is the
wipe.

## Reading competitor-search lines

**When:** competitor detection lost a search (`competitors.serp_failed`), or you want
to know how long SerpApi takes from Render and whether the retry ever helps, or how many
of the month's searches are left. A detection makes 3 to 6 Google searches through
SerpApi, three at a time, and the API logs a line for each search that reached SerpApi,
and one more for what the account has left.

**Where:** the Render dashboard → the **API** service → **Logs**. Search for the event
name. Every line below carries numbers, our own codes and the HTTP status, and nothing
else: no query, no client, no domain, no URL, no key.

**The lines**

| Event | Level | When | Carries |
|---|---|---|---|
| `serp.completed` | info | SerpApi answered the search, whatever it said | `elapsed_ms`, `attempts`, `hits`, `status`, `error_code` |
| `serp.retried` | info | the first attempt got no answer and the retry did; logged before that search's `serp.completed` | `attempts` (always 2), `kinds` (how the first attempt failed), `elapsed_ms` |
| `serp.attempts_failed` | warning | no attempt got an answer, so the search failed | `attempts`, `kinds` (how each attempt failed), `elapsed_ms` |
| `serp.quota_left` | info | once for each detection, after its searches, when SerpApi's account said how many searches are left | `left`, and `plan_searches` when the account gave the plan's size |
| `serp.quota_low` | warning | the same moment, in addition, when `left` is under 50 | the same two fields |
| `serp.quota_unavailable` | info | the account could not be read: written instead of the two lines above, one for the detection | `kind` |

**Every search that reached SerpApi logs exactly one of `serp.completed` and
`serp.attempts_failed`**, and `serp.retried` besides when a retry answered, so those two
add up to the number of searches made. A search that was never sent (no `SERPAPI_KEY`)
logs none of the three. The three quota lines are not searches and are not in that count.

**`serp.completed`, field by field**

- `elapsed_ms`: milliseconds from the start of the search's first attempt to the end of
  reading its answer. The wait for one of the three slots is not in it. An answer to the
  retry has the failed first attempt and the 1 s pause in it: at least 1,000, and up to
  about 21,000.
- `attempts`: 1, or 2 when the first attempt got no answer and the retry did.
- `hits`: the organic results kept (those with a usable domain). 0 for an error answer,
  and also when Google had nothing to return: `error_code` tells the two apart.
- `status`: the HTTP status SerpApi sent. A 200 is not always a result page: see
  `error_code`.
- `error_code`: `None` for a result page. Otherwise our code for the answer:
  `SERP_AUTH_FAILED` (401: the key is wrong or revoked; a `serp.auth_failed` error line
  comes with it), `SERP_RATE_LIMITED` (429), `SERP_HTTP_<n>` (any other status; a
  `serp.http_error` warning with the status comes with it), `SERP_BAD_JSON` (a 200 whose
  body could not be read as a page of results: it was not JSON or was nested too deeply to
  read, or it was JSON of another shape, such as a list, a string, a number or `null`, an
  `organic_results` that is not a list, or a list of results of which none is an object) or
  `SERP_API_ERROR` (a 200 whose body carried an `error`). A page in which only some results
  can be used is read: the others are skipped (a result that is not an object, or whose link
  is not a string, is one of them), and `hits` counts what was kept.

**`serp.retried` and `serp.attempts_failed`, field by field**

- `attempts`: how many attempts were made. Always 2 on `serp.retried`. On
  `serp.attempts_failed` it is 2, or 1 when the failure is one that is not retried (an
  httpx error that is not a transport failure, such as a body it could not decode).
- `kinds`: one entry for each attempt that got no answer. `connect`: never connected.
  `read`: connected, sent the search and heard nothing back within the 9 s read budget.
  `write` and `pool`: the other httpx timeouts. `total`: the attempt ran out its 10 s
  deadline with no single step out of time (an answer that kept arriving a byte at a
  time). Anything else is the error's class name (`ConnectError`, `ReadError`,
  `RemoteProtocolError`: refused, reset, dropped; `DecodingError`).
- `elapsed_ms`: as above, to the answer (`serp.retried`) or to the end of the last
  attempt (`serp.attempts_failed`).

**How to read the latency**

1. Search `serp.completed` and copy `elapsed_ms` from the lines with `attempts=1` and
   `error_code=None`: single requests that worked. With fewer than 20 lines the 95th
   percentile is only the slowest one, so let some scans run first.
2. Sort them. The median is the middle value; the 95th percentile is the one 95% of the
   way up (the 19th of 20). For comparison, 24 searches made from a development Mac on
   2026-10-03 had a median of 1.54 s, a 95th percentile of 2.54 s and a slowest of
   6.93 s, none over 10 s.
3. Count the lines close to the limits. An attempt is cut when it has heard nothing for
   9 s (the read budget, `PHASE_TIMEOUT_S`) or has run 10 s (the deadline,
   `ATTEMPT_TIMEOUT_S`), so a search at 8,000 ms or more came close.
4. Read the `attempts=2` lines apart: each is a search the retry rescued, and its
   `elapsed_ms` is not one request's time.
5. Then read the `serp.attempts_failed` lines by their `kinds`. A `read` on both
   attempts is a search SerpApi took and did not answer in time.

**Billing.** A failed connect is never billed and a read failure was not billed when
measured (2026-10-03, against the account's monthly count), and SerpApi bills only a
search it records as successful, so a search it finished after we stopped waiting would
be billed and only the account's count would show it, not these lines (the evidence is
in the comment at the top of `services/serp.py`).

**How many searches are left: `serp.quota_left` and `serp.quota_low`.** A detection reads
SerpApi's account once, after its searches and while the model is still answering, and logs
what it says. The read is free (it is not a search and does not count against the month),
it is not retried, and it is cut off after 5 s. The model's calls take far longer, so the
read does not lengthen a detection (if they took less, it would add at most the rest of
its 5 s). Only a detection that searches reads it: a client's first scan, and a press of
Detect competitors (once per client per 24 hours). A repeat scan carries the client's set
forward, searches nothing and reads nothing.

- `left`: the searches the plan still allows this month (SerpApi's `plan_searches_left`).
  `plan_searches`: the plan's size (`searches_per_month`), left out when the account does
  not give one, so `left=184 plan_searches=250` is 184 of 250. The Free plan is 250 a
  month, and the account is shared with production, so a development run spends from the
  same count. At up to six searches a detection, the Free plan covers about 40 detections a
  month (a client's first scan and each press of Detect competitors), whatever any daily
  cap allows. Only these two numbers are logged: the account's body also carries the API
  key and the owner's address, and neither is ever written.
- **It lags.** SerpApi's count trails the searches by a minute or two, and a search that
  failed can be counted for a moment and then dropped (measured 2026-10-03). The line
  written right after a detection can be off by that detection's own searches, and the next
  detection's line is the settled one. Read the trend, not one line.
- **`serp.quota_low` is `left` under 50.** A detection makes up to six searches, so under 50
  is at most eight more detections. Open SerpApi's account page for the figure and the
  renewal date, then decide: wait for the renewal, or move from the Free plan to SerpApi's
  Starter plan.
  **That is the founder's decision.** If the searches stop being answered (`serp.completed`
  lines with an `error_code`, `competitors.serp_failed`), a detection runs on the model's
  answers alone and its set says so (`weak_signal`, or `no_signal` if the model names no one
  either).
- **`serp.quota_unavailable` instead of `serp.quota_left`** means the read did not work. It
  is one line at INFO, `kind` and nothing else, so it is in the logs Render keeps and nobody
  has to turn DEBUG on to see it. `kind` says why: `http_<status>` (SerpApi answered with that
  status); `connect`, `read`, `write`, `pool` or `total` (no answer within 5 s, and which part
  ran out); `bad_json` or `no_count` (it answered, and the body was not a JSON object, or had no
  whole-number `plan_searches_left`, which would mean SerpApi changed it); `no_key`; or an error's class
  name. A detection with neither line did not get as far as the read: its searches raised.
  With no `SERPAPI_KEY` nothing is sent: each search is `SERPAPI_KEY_MISSING`, with a
  `competitors.serp_failed` line, and the detection runs on the model's answers alone.

**What would justify a change.** The timeouts and the retry are unchanged until there is
data. This is what the numbers would mean.

- Answered searches cluster near the 9 s read budget (many `attempts=1` lines at 8,000 ms
  or more): the read budget, and the 10 s deadline with it, are too short for SerpApi
  from Render, and would be raised together (`PHASE_TIMEOUT_S` and `ATTEMPT_TIMEOUT_S`
  in `services/serp.py`). A scan's worst case moves with them, because
  `serp.QUERY_CEILING_S` is added into it (`test_scan_executor.py`).
- Answered searches are all fast (none near 9,000 ms) and the failures are `read` on both
  attempts: the provider is stuck on those searches, and waiting longer will not help. The
  retry costs about 10 s a search (the 1 s pause and a second 9 s wait) and has not
  rescued one yet: of the 3 searches seen to time out on a read by 2026-10-03, the retry
  timed out too. The founder has decided to keep retrying a read failure; these lines are
  how to see whether it ever pays.

**Not in the logs:** which client, scan or query a line is for (several searches run at
once, so their lines interleave), the market, the URL, the key and anything SerpApi
returned. `competitors.serp_failed` (one for each search that failed, an error answer
included) names the query and its `error_code`, and `competitors.detected`, logged after
the searches' lines, carries the `client_id` and the `country` the searches were made in.

## Reading the usage lines

**When:** you want to know what a scan used: how many requests each provider got, how many
tokens they reported back, how many requests were retries and how many prompts never got an
answer; or what a scan cost to run. The API writes two lines about it, from U1 (2026-10-04):
`llm.call`, one line for each paid HTTP request, and `scan.usage`, one total for each scan. Both
carry counts and nothing else: no prompt, no answer, no brand, no domain, no error message and no
key.

**Where:** the Render dashboard → the **API** service → **Logs**. Search for the event name, or
for `scan_id=<the scan's id>`, which finds a scan's `scan.usage` line and the `llm.call` lines
it made. Both are INFO lines. **`LOG_LEVEL` must stay at `INFO` or be unset (unset is INFO on
staging and production), or both lines disappear:** at `WARNING` or above neither is written.

**The lines**

| Event | Level | When | Carries |
|---|---|---|---|
| `llm.call` | info | a paid request has ended: one line for each HTTP request to Anthropic, OpenAI, Perplexity or Google (a request the SDK or the adapter sent again is a second line) | `scan_id`, `provider`, `model`, `purpose`, `engine`, the token and search counts, `retry`, `ok`, `error` |
| `scan.usage` | info | once, when the executor that ran a scan is done with it, however the scan ended | `scan_id`, then counts for each line of the scan and for SerpApi, flat as `key=value` |

A scan of twenty prompts on four engines writes up to about 170 `llm.call` lines, and a retry
adds one: the engines (prompts times engines), a sentiment request for each answer that named
the client, one for prompt generation, one for the fixes and, on a client's first scan, two to
four for competitor detection (a later scan carries the competitor set forward and makes
none). `scan.usage` is one line of about one and a half kilobytes for four engines.

**`llm.call`, field by field**

- `scan_id`: the scan the request was made for. `None` outside a scan (see below).
- `provider`: `anthropic`, `openai`, `perplexity` or `google` (Gemini).
- `model`: the model the provider's response says served the request. When the response names
  none, or there was no response, it is the model the API asked for, so a request that failed
  still says what was being called.
- `purpose`: `engine`, `sentiment`, `prompt_generation`, `fixes`, `detection` (the co-citation
  requests that find rivals) or `classification`.
- `engine`: the engine's id (`claude`, `claude_search`, `chatgpt`, `perplexity`, `gemini`) for
  an engine request, `None` otherwise.
- `input_tokens`, `output_tokens`: as the provider reports them. Reasoning is inside the output
  count, not on top of it.
- `reasoning_tokens`: the part of the output that was reasoning, where the provider reports it.
- `cache_read_tokens`, `cache_write_tokens`: cached tokens, as reported. Anthropic's input count
  leaves them out and OpenAI's and Google's include the cached part. Nothing the API sends is
  cacheable today, so they are 0.
- `search_invocations`: web searches or grounding queries the request made (Claude's web search,
  Perplexity's search, Gemini's grounding queries). Perplexity's is read from
  `tool_calls_details.search_web` (U4, 2026-10-05; `web_search` is the fallback key). **Lines
  written before that change show 0 for every Perplexity call.** Each answered Perplexity call
  made one search in the measured call, billed at US$0.0025, so to price an old line by hand:
  answered Perplexity requests (`calls` minus `unanswered`) x US$0.0025, on top of the tokens.
  That sum is right for those lines, which all come from before U5, when `unanswered` counted
  every request that got no usage back (see `<line>_unanswered` below).
- `retry`: this request sent an earlier one again for the same call. Two things do that: the
  Anthropic SDK, which retries once inside a call, and Perplexity, which retries once after a
  429 that names its wait. SerpApi's retry is not an `llm.call`: it is in `serpapi_attempted`.
- `ok`: a response came back and its usage was read. `False` means no usage came back, so the
  counts are 0: there was no response (a timeout, a refused connection, an error status) or the
  SDK could not parse the response. A request that was answered and then did not count as an
  answer (a truncated or refused answer) is `ok`: it was billed, and its usage is logged.
- `error`: why `ok` is `False`, as one word: `http_<status>` for an error status (`http_429`,
  `http_529`), otherwise the exception's class (`APITimeoutError`, `TimeoutError`,
  `ValidationError`, `CancelledError`). It is never the message. `None` when `ok` is `True`.

**`scan.usage`, field by field**

`scan_id`, then these for each of `engines` (every engine added together), `engine_<id>` (each
engine that was asked, such as `engine_claude_calls`), `sentiment`, `prompt_generation`, `fixes`
and `detection`:

- `<line>_calls`: REQUESTS made, retries included. Every request counts, answered or not.
- `<line>_unanswered`: CALLS that never got an answer (from U5, 2026-10-06). A call is the
  requests it takes to get one answer: for an engine, one prompt asked of one engine; for the
  other lines, one request of that kind and any retry of it. A call is unanswered when its LAST
  request got no usage back (`ok=False`), and counts once however many requests it made. A call
  that a retry answered is not unanswered.
- `<line>_retries`: REQUESTS that were sent again: every request of a call but its first.
- `<line>_input_tokens`, `<line>_output_tokens`, `<line>_reasoning_tokens` (inside the output),
  `<line>_cache_read_tokens`, `<line>_cache_write_tokens`, `<line>_search_invocations`: the sums
  of the `llm.call` lines of that line.
- `<line>_model`: the model or models that served it, joined with `+`. **Only a request that
  was answered names a model.** A request that failed carries the model the API asked for, which
  can be an alias of the one that answers (`gpt-5.5` for `gpt-5.5-2026-04-23`) and would make one
  model look like two.

**Reading `_calls`, `_retries` and `_unanswered` together.** A prompt that Perplexity refused with
a 429 and answered on the retry is `_calls` 2 and `_retries` 1, and no `_unanswered`. One refused
twice (the retry too) is `_calls` 2, `_retries` 1 and `_unanswered` 1: one prompt with no answer,
not two. One refused and not sent again is `_calls` 1 and `_unanswered` 1. So:

- Prompts asked of an engine: `_calls` less `_retries`. Prompts answered: that, less `_unanswered`.
- Requests that got no usage back: `_retries` plus `_unanswered`. What they cost is not in the
  tokens: a request that timed out may have been billed.
- Against the `llm.call` lines of the same scan with that line's purpose (and, for an engine,
  that engine): `_calls` is how many there are, `_retries` how many have `retry=True`, and
  `_unanswered` how many have `ok=False` less how many have `retry=True`.
- A call that was cancelled (a sibling failed, or the scan ran out of time) logs
  `error=CancelledError` and never got an answer: one unanswered call. If it had been refused
  once before, that refusal is its retry.

**A line from before U5 counts `_unanswered` differently.** Then every request that got no usage
back was counted, one that a retry rescued included: a scan on 2026-10-05 in which one Perplexity
request was refused with a 429, sent again and answered, and every result was there (80 results,
no failures), logged `engine_perplexity_unanswered=1`. That is what such a request reads as from
U5 on: `engine_perplexity_retries=1` and no `_unanswered`. To read an older line as a current one,
take `_retries` away from `_unanswered`. Nothing else about the lines changed, and
`--price-usage` prices the two kinds of line the same way, from tokens and searches alone.

**A counter that is zero is left out: read an absent counter as zero.** The three every line
carries are `_calls`, `_input_tokens` and `_output_tokens`. A `_model` that nobody named is left
out too. `engines_*` is every engine added together: read the `engine_<id>_*` fields for one
engine, and never add `engines_*` to them.

SerpApi, for the scan's detection searches. The first four are always present:

- `serpapi_attempted`: HTTP requests made, retries included.
- `serpapi_ok`: answered with a result page. SerpApi bills it unless it served the search from its
  cache.
- `serpapi_error`: answered with an error (a 401, a 429, any other status, a body that could not
  be read as a page of results, or an `error` in the body). Counted as not billed, which holds for
  all but one kind: a 200 whose body could not be read was a 200 to SerpApi, which may have counted
  it as a search. This count cannot tell that kind from the rest (see "What the lines undercount").
- `serpapi_no_answer`: requests that got no answer (a timeout or a transport failure). Nobody can
  say whether SerpApi billed them. SerpApi's counts are per request, unlike `<line>_unanswered`:
  a request that got no answer and was asked again and answered still counts here, because it
  may have been billed.
- `serpapi_billed_or_unknown`: `serpapi_ok` plus `serpapi_no_answer`. **An upper bound on what
  SerpApi billed, but for one kind of answer**: an answered search is billed unless SerpApi served
  it from its cache, an error answer is not billed, and a search nobody answered may have been. The
  exception is a 200 whose body could not be read: it is counted in `serpapi_error`, so it is not
  in this number, and SerpApi may have billed it. Left out when it is zero.

`serpapi_ok`, `serpapi_error` and `serpapi_no_answer` add up to `serpapi_attempted`.

**One total for each scan, and what is not in it.** `scan.usage` is logged once for each scan an
executor ran, whether it finished, ended partial or failed, stopped on an error of ours, was
cancelled because its lease was lost, or ran out of time. An executor that did not claim the scan
(a duplicate submission, a scan already finished) logs none: it spent nothing.

These are never in a scan's total. Their `llm.call` lines carry `scan_id=None` and no
`scan.usage` follows them:

- the detect route, `POST /clients/{clientId}/competitors/detect` (`purpose=detection`);
- the fixes route, `POST /scans/{scanId}/fixes` (`purpose=fixes`);
- classification at intake, `POST /clients` and `POST /clients/{clientId}/reclassify`
  (`purpose=classification`; it is left out of a scan's total even when it is logged inside one);
- an ad-hoc prompt run, `POST /clients/{clientId}/prompt-runs`.

A scan's own detection and fixes requests, made while the scan runs, are in its total.

**What the lines undercount**

- A response the SDK could not parse (it did not fit the schema) was billed and logs zero counts:
  `ok=False error=ValidationError`. A run of these in a `_unanswered` count is the sign.
- A request that timed out may have been billed with nothing to show for it: `ok=False`, `error`
  `APITimeoutError` or `TimeoutError`, zero counts. A request cut by `CancelledError` is the same.
  In `scan.usage` such a request is in `_retries` when its call went on after it and in
  `_unanswered` when it was the call's last request, so the requests whose cost the tokens do not
  show are `_retries` plus `_unanswered`: read the two together.
- Since 3.77, when one of a scan's prompt slots raises, the scan cancels the others and waits for
  them to stop before it ends, so their lines are in its total: a request cut short logs
  `ok=False error=CancelledError` with zero counts, and may still have been billed (above).
- Whether a SerpApi search with no answer was billed. Only the account's count says.
- A SerpApi search answered with a 200 whose body could not be read as a page of results. It is
  counted in `serpapi_error`, which is not billed, and it is not in `serpapi_billed_or_unknown`;
  but SerpApi sent it as a 200 and may have counted it as a search. How many there were is in the
  `serp.completed` lines with `error_code=SERP_BAD_JSON`: they carry no scan id and also cover the
  searches the detect route makes, so read them over the same period. Whether any was billed, only
  the account's count says.

**How to price a line**

```
cd apps/api && uv run python scripts/verify_e2e.py --price-usage '<pasted line>'
```

Paste the `scan.usage` line as Render shows it, with its timestamp and level, or just its
`key=value` fields. Give `-` instead of the line to read it from standard input
(`pbpaste | uv run python scripts/verify_e2e.py --price-usage -`). It needs no database and no
key and calls no provider. A line from before `serpapi_billed_or_unknown` existed prices the same.

`--serp-per-search <price>` is optional: what your SerpApi plan charges for one search, a finite
number of 0 or more (anything else is refused with exit code 2). With it the command prices
`serpapi_billed_or_unknown`, an upper bound, and says so; without it SerpApi is listed under
missing rates.

It prints each line of the scan (each engine, sentiment, prompt generation, fixes, detection)
with its model, requests, tokens and what they come to at the rates in `RATES` in
`scripts/verify_e2e.py`; the total; and every rate it used with the day it was read and the
vendor page it came from. **The rates are perishable.** Each is a vendor's published rate on the
date shown, and nothing the command prints is a quote or a bill. A line it cannot price (a model
with no rate in the table, a line that more than one model with different rates served, no model
at all) is listed under MISSING RATES and left out of the total, never priced at nothing. Cached
tokens are not priced separately, since no cached-token rate is recorded, and the command says
when a line has some.

## Count the stored domains that fail the hostname check

**When:** once after the deploy that ships launch checklist 3.81, and whenever you want to know
whether any stored client or competitor domain is not one DNS would carry. 3.67 checks a
client's domain at intake and 3.81 checks a competitor's (typed, or proposed by the model), but
nothing re-checks what was stored before; this counts them. It only reads.

**Where:** the Render dashboard, the **API** service, **Shell**. Run:

```bash
cd /app/apps/api && .venv/bin/python scripts/count_unchecked_domains.py
```

The shell already has the service's `DATABASE_URL`.

**What it prints:** counts only, never a domain, name or id:

```
clients.domain: checked=N empty=N not_a_hostname=N no_public_domain=N not_in_checked_form=N
competitors.domain: checked=N empty=N not_a_hostname=N no_public_domain=N not_in_checked_form=N
failing the check: N; would be rewritten: N
```

`not_a_hostname` and `no_public_domain` fail the check. `not_in_checked_form` passes but is not
the form intake would store (upper case, a `www.` or other subdomain, a scheme or path).
`empty` is a competitor with no domain, which is allowed. The counts cover every row: clients
that were soft-deleted, and the competitor sets of past scans (and any next list), not only the
live ones. The script declares its transaction read only before it reads anything.

**If a count is not zero:** report it to the founder, with the numbers and nothing else. Nothing
is changed automatically and the script has no option to change anything; which rows to correct
is a decision, and finding them means reading production domains, which this step does not do.
All zeros needs no action.

## Supabase grants check

**When:** before the deploy that ships revision `f45983cfa5e3` (which takes `anon` and
`authenticated`, the two roles Supabase's Data API acts as, off everything in `public`), after
it, and again after any revision that creates a table, view, sequence or function there, after
any change to the Data API settings, and after enabling an extension into `public`. It is
read-only and it is yours to run: nothing in the repo opens a production session.

**Where:** the Supabase dashboard → **SQL Editor**. Paste one query at a time.

**Before the deploy.** Two checks.

- Confirm that the user part of the API service's `DATABASE_URL` (Render → the API service →
  Environment) is `postgres`, or `postgres.<project-ref>` through the pooler. The revision acts
  as that user, and its step on the default privileges of `postgres` needs it to be `postgres`
  or a member of it. Read the user part only; do not paste the URL anywhere.
- Run this query. It lists every table, view, sequence and routine in `public` whose owner
  `postgres` cannot act as. Expect no rows. The revision cannot revoke on such an object, so
  it skips it without a word and the object keeps whatever the Data API roles were granted on
  it (a revoke on it would fail the deploy).

```sql
select c.oid::regclass::text as object, c.relkind::text as kind,
       pg_get_userbyid(c.relowner) as owner
  from pg_class c
 where c.relnamespace = 'public'::regnamespace
   and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
   and not pg_has_role('postgres', c.relowner, 'USAGE')
union all
select p.oid::regprocedure::text, 'routine', pg_get_userbyid(p.proowner)
  from pg_proc p
 where p.pronamespace = 'public'::regnamespace
   and not pg_has_role('postgres', p.proowner, 'USAGE')
 order by 1;
```

**After the deploy.** Three queries.

**1. Functions and procedures in `public`.** Every one, with whether each role can run it.
Expect no rows: AVP creates none (a search of the revisions and `apps/api/src` on 2026-10-03
found no function, procedure, trigger, view or extension). A row with `from_extension` filled
belongs to an extension, and a row whose `owner` is not `postgres` is not ours either. `true`
in the two `can_execute` columns is Postgres's default grant of EXECUTE to every role, not a
grant to `anon` or `authenticated` by name, and the revision leaves it alone. It matters only
for a function that is there, and the Data API is off.

```sql
select p.oid::regprocedure                                       as routine,
       pg_get_userbyid(p.proowner)                               as owner,
       p.prosecdef                                               as security_definer,
       has_function_privilege('anon', p.oid, 'EXECUTE')          as anon_can_execute,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') as authenticated_can_execute,
       (select e.extname
          from pg_depend d
          join pg_extension e on e.oid = d.refobjid
         where d.classid = 'pg_proc'::regclass and d.objid = p.oid
           and d.deptype = 'e')                                  as from_extension
  from pg_proc p
 where p.pronamespace = 'public'::regnamespace
 order by p.proname, p.oid;
```

**2. Privileges held by name.** Every privilege either role still holds on a table, view,
sequence, column or routine in `public`, and every grant to PUBLIC on a relation or column
(`kind` is `r` table, `v` view, `S` sequence, `column` or `routine`; `grantee` is `PUBLIC` for
those). Expect no rows.

```sql
select c.relname::text as object, c.relkind::text as kind, null::name as column_name,
       a.privilege_type, coalesce(r.rolname, 'PUBLIC') as grantee
  from pg_class c
 cross join lateral aclexplode(c.relacl) a
  left join pg_roles r on r.oid = a.grantee
 where c.relnamespace = 'public'::regnamespace
   and (a.grantee = 0 or r.rolname in ('anon', 'authenticated'))
union all
select c.relname::text, 'column', att.attname, a.privilege_type, coalesce(r.rolname, 'PUBLIC')
  from pg_attribute att
  join pg_class c on c.oid = att.attrelid
 cross join lateral aclexplode(att.attacl) a
  left join pg_roles r on r.oid = a.grantee
 where c.relnamespace = 'public'::regnamespace
   and (a.grantee = 0 or r.rolname in ('anon', 'authenticated'))
union all
select p.oid::regprocedure::text, 'routine', null, a.privilege_type, r.rolname
  from pg_proc p
 cross join lateral aclexplode(p.proacl) a
  join pg_roles r on r.oid = a.grantee
 where p.pronamespace = 'public'::regnamespace
   and r.rolname in ('anon', 'authenticated')
 order by 1, 3, 4, 5;
```

**3. Default privileges.** Every default privilege that still names either role, for any
owner role and any schema (`object_type` is `r` table, `S` sequence, `f` function). Expect no
row with `owner_role` `postgres` whose `schema` is `public` or `(every schema)`. A row for
another owner role, such as `supabase_admin`, may appear: the revision does not touch it, and
Alembic creates nothing as that role.

```sql
select pg_get_userbyid(d.defaclrole) as owner_role,
       case d.defaclnamespace when 0 then '(every schema)'
            else d.defaclnamespace::regnamespace::text end as schema,
       d.defaclobjtype::text as object_type,
       a.privilege_type, r.rolname as grantee
  from pg_default_acl d
 cross join lateral aclexplode(d.defaclacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in ('anon', 'authenticated')
 order by 1, 2, 3, 4, 5;
```

**Reading it.** Query 2 empty and no `postgres` row for `public` or `(every schema)` in query
3: done. Anything else means a grant is back or the revision did not reach it. Say which row
it is: the fix is a new revision, because a grant changed by hand is invisible to
`alembic check`.

- A table, view, sequence or routine still listed by query 2 is most likely one the
  before-the-deploy query listed: the revision skipped it.
- If query 3 still shows `postgres` rows for `public`, the likeliest cause is that the deploy
  ran as a user that is neither `postgres` nor a member of it. The revision then skips that
  step, and the deploy log says so in two lines from `alembic.runtime.migration`, both
  containing `revoke_data_api_grants`: first the server's own notice, logged as
  `Postgres NOTICE: revoke_data_api_grants: this user is not a member of role postgres, ...`
  and then the revision's own warning. Search the deploy log for `revoke_data_api_grants`.
  The warning is the one to rely on: the revision writes it itself, while the notice has to
  come through the connection from the database, and it has not been checked that Supabase's
  pooler passes it on.
- A `postgres` row whose schema is `(every schema)` is a default that applies to every schema.
  A per-schema revoke cannot remove it, so the revision leaves it; removing it takes a
  revision of its own, and whether to write one is the founder's call.

**Not covered:** `service_role`, the default privileges of any owner role other than
`postgres` and the user the migrations run as, and PUBLIC's own default privileges. Whether
the Data API is switched on is a dashboard setting; this check shows what the database would
allow if it were.
