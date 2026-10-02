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
   find or create the product with a recurring monthly Price of US$160. Copy
   its Price ID (`price_…`).

3. **Render.** On the API service (`ai-visibility-platform-5gk4`), open
   Environment. Set `STRIPE_PRICE_ID` to that id. Confirm `STRIPE_SECRET_KEY`
   is the test secret key (it starts `sk_test_`) and `PUBLIC_WEB_BASE_URL` is
   the web URL. Save; Render redeploys.

4. **Webhook endpoint.** In Stripe, go to Developers → Webhooks → Add endpoint.
   The URL is
   `https://ai-visibility-platform-5gk4.onrender.com/api/v1/billing/webhook`. Select exactly these
   events: `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`. Save,
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
     `{"received":true,"handled":true}`.
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
   A wait has a bound (60 s for a request, 300 s for a scan's audit): see 6.
5. An audit that ends with the code `TIMEOUT` was cut by its cap: a step in the
   browser never returned, for example a page whose script never let the read
   finish. The cap is 244.5 s for the audit and 266 s for the crawl intake asks
   for (arithmetic in `technical_audit.audit_run_cap_s` and
   `crawl.crawl_run_cap_s`), and a run that is cut closes its browser and gives
   the slot back. Launch checklist 3.20.
6. A `browser.slot_busy` line means a run waited for the browser as long as it
   was allowed and was sent away without one. What happens next depends on who
   asked. A scan's audit is recorded `failed` with `BROWSER_BUSY` and the scan goes
   on to scoring, alerts and fixes; the line carries the `scan_id`. A request
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

**Not in the logs:** a number for any one request, and nothing from before the
last restart: the counters belong to the container, and a restart starts them
again. The API image runs `tini` as PID 1, so the zombie `headless_shell`
processes a browser run used to leave behind are reaped; they cost no memory.
