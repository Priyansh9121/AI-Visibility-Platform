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
