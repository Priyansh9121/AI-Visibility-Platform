---
name: stripe-billing-ops
description: "Applies when a task involves AVP's Stripe billing in test mode: refunds, cancellations, disputes, plan or price changes (answered with founder-only dashboard steps and the approval gates, never acted on), checkout refusals (409), the billing portal, the webhook ledger and ordering, a second-subscription log line, or the runbook's Live Stripe test. Does not apply to pricing strategy or unrelated payment code."
---

# Stripe billing ops (AVP)

Layer: API billing (`apps/api/src/avp_api/routers/billing.py`, `services/billing.py`) plus operations. Phase: paid subscription. Read the code before you claim anything about it; every claim below was checked against the repo on 2026-10-01 and can drift.

## Approval gates (hard)

1. **Money is the founder's.** Refunds, cancellations, price or product changes, coupon or trial changes, and anything in Stripe **live** mode are done by the founder in the Stripe dashboard. You describe the exact steps and stop. You never call a Stripe write endpoint, never "just fix" a subscription. **The Stripe CLI is banned** (`stripe login` stores a live-mode key on the machine), as are curl or any other direct call to Stripe: this includes indirect calls through AVP's own checkout or portal endpoints on the local stack (they use the .env test key and create or expire real test objects); reproduce with the patched `test_billing.py` instead. Diagnostics are dashboard-only, done by the founder. No Stripe key, token or customer data passes through you.
2. **Live keys never reach you.** Live keys (`sk_live_`, `rk_live_`) are set by the founder on Render only (`CLAUDE.md`, Secrets). If one appears in any file, message or output, stop, do not copy or use it, and tell the founder to rotate it. The code has no live-mode switch on purpose (`apps/api/src/avp_api/config.py`, comment above `stripe_secret_key`): going live is the founder's key swap.
3. **Product decisions stop you.** Price, seat count, trial, dunning, what "active" means: ask the founder.
4. **No production.** No Render, Supabase or live-Stripe action from here. Render environment edits are the founder's, described step by step.

## Test or live? State it first

Begin every answer with one line: `mode: test` or `mode: unknown (live not excluded)`. You cannot read Render's environment, so you can only establish test mode from evidence the founder gives (for example a screenshot cropped to the Test mode toggle only, with no customer names or emails). With `unknown`, offer read-only analysis of code and logs only.

## What AVP does (code-backed)

- **Checkout** `POST /agencies/{agencyId}/billing/checkout` (owner/admin only) returns a Stripe-hosted URL; it never writes a subscription status. In `create_checkout_session`:
  - 409 `/problems/subscription-already-active` when the stored status is `active` or `trialing` (`is_active`), and again when Stripe itself lists an active or trialing subscription for the customer (paid, webhook not landed yet).
  - Row lock: the agency row is re-read `FOR UPDATE` (`FOR NO KEY UPDATE` when a customer id exists) with `SET LOCAL lock_timeout = '3s'`; a lock timeout is 409 `/problems/checkout-in-progress`. Note: the router docstring says 503 for this, `errors.py` says `Conflict` (409). Trust `errors.py`; see "Found" in CHANGES.md.
  - Open sessions: for an existing customer it lists open Checkout Sessions and expires each; `has_more` or a Stripe error is 503 `/problems/service-unavailable`, never "carry on" (carrying on is the double-subscription case).
  - Missing `STRIPE_SECRET_KEY` or `STRIPE_PRICE_ID` is 503 `billing-not-configured`, naming the variable.
  - Error handling: only the pre-check for an existing customer catches `stripe.StripeError` (mapped to 503; Stripe failure: 503 from the existing-customer pre-check, 500 elsewhere). Customer create, session create and portal create have no handler in `services/billing.py` and a Stripe failure there surfaces as a 500 (`errors.py` maps only `ProblemError` and framework errors). The branch may change this; check the current code before stating it.
  - Stripe client: 15 s timeout, 1 retry; return URLs come from `PUBLIC_WEB_BASE_URL`, never the request.
- **Portal** `POST /agencies/{agencyId}/billing/portal`: Stripe's hosted screens; 503 when the agency has no customer. Whether cancel-at-period-end is allowed is a Stripe dashboard setting the code cannot see.
- **Read** `GET /agencies/{agencyId}/billing` reads the `agencies` row plus one COUNT over the agency's scans for the allowance (`routers/billing.py:102`); it never asks Stripe. `past_due` is not active.
- **Webhook** `POST /billing/webhook` is the only writer of subscription state:
  - `verify_webhook` checks the HMAC over the raw body with `STRIPE_WEBHOOK_SECRET`; unset is 503, bad or missing signature is 400 `invalid-webhook-signature`. It never trusts an unverified body.
  - Handled events: `checkout.session.completed` (subscription mode only), `customer.subscription.created|updated|deleted`. Others answer 200 `handled:false`.
  - Ledger: `stripe_webhook_events` (model `models/stripe_webhook_event.py`) stores event id, type, Stripe's `created`, agency id, outcome (`applied`/`stale`). Facts only, no payload. A repeat id answers `duplicate:true`.
  - Ordering: an event older than `agencies.stripe_event_at` is `stale` (recorded, applied to nothing); the state machine also refuses to move a subscription backwards or overwrite `canceled`/`incomplete_expired`. Only `customer.subscription.*` moves the watermark.
  - Period end: on the pinned API version `current_period_end` lives under `items.data[]`, not top level (`period_end_of`, `services/billing.py:175`); reading the top-level field gives nothing, silently.
  - Unmatched events are not recorded, so Stripe's Resend applies once an operator links the agency.
  - Log lines: `stripe.webhook.applied|stale|duplicate|ignored|unmatched|rejected|second_subscription` (ids only).
- **Seats**: `Agency.seat_limit` defaults to 3 (`config.py` `default_seat_limit`) and `services/seats.py` enforces it under a row lock. The pricing card sells $150/month (since 2026-10-04; $160 before) and 3 seats (`apps/web/src/components/marketing/PricingCard.tsx`: `PLAN_PRICE_USD`, `PLAN_SEATS`; the API's `services/billing.py` `PLAN_PRICE_USD` is held equal by a web test and checked against the configured Stripe Price at start and hourly, when deployed, `billing.price_check`); Stripe quantity is fixed at 1, so seats are an AVP limit, not Stripe per-seat billing. Do not write "per seat" about Stripe.

## Procedures

### A. Explain a customer-visible symptom (read-only)
Classify first: already-active 409, checkout-in-progress 409, Stripe unreachable 503, not configured 503, webhook not delivered, webhook stale/duplicate, second subscription. Then trace it in code and in the agency's Settings state. Report: symptom, code path (file:line), evidence, recommended founder action. Do not propose a refund before the class is known.

### B. Second subscription (runbook "If the logs show a second Stripe subscription")
1. Founder finds `stripe.webhook.second_subscription` in Render logs (ids only: `agency_id`, `stored_subscription_id`, `incoming_subscription_id`).
2. Founder confirms two live subscriptions on the customer in the Stripe dashboard.
3. **Founder** cancels the earlier one (`stored_subscription_id`) and refunds if a payment was taken. You do not.
4. Known gap: the webhook applies events for any subscription id, so the cancellation event of the earlier one can mark the agency canceled while the kept one still bills. If Settings shows canceled, the runbook's fix is a small change to the kept subscription in Stripe (a metadata key) so a newer `customer.subscription.updated` is applied. Rehearse in test mode first. You only explain this; the founder performs it.

### C. Live Stripe test, test mode (runbook "Live Stripe test (test mode on Render)")
Follow `docs/runbook.md` as written; do not paraphrase the steps from memory. You prepare a checklist and read the results; the founder does every dashboard and Render step: test-mode Price, `STRIPE_PRICE_ID`, test-mode secret key, endpoint with exactly the four events, `STRIPE_WEBHOOK_SECRET`, portal settings, pay with Stripe's test card, Resend one delivery (expect `duplicate:true`), portal cancel (expect "Ends" date). Stop rule: nothing is switched to live from the runbook. Deploy while the endpoint has no pending retries (the watermark starts empty).

### D. Changing billing code
This is an API change: follow `fastapi`, run the targeted `apps/api/tests/test_billing.py` (no test reaches the network; `_client` is patched), keep Stripe calls inside `services/billing.py`, never add a status-writing route, and never log payloads, emails or keys (`logs.py`, `test_secrets_stay_out_of_logs.py`). Endpoint change: regenerate OpenAPI and shared-types per `CLAUDE.md`, and let the scribe update `docs/api-contracts.md`. A new Stripe dependency or host needs `providers.test.ts` and `/privacy` (founder decision on legal copy). Any billing diff goes to the `security-reviewer` agent (`CLAUDE.md`). Run targeted tests with output redirected to a file and read the exit code, never piped; `assert_count.py api-pytest` applies only to the full suite (the floor is for the whole run), not a targeted one. See `verification-before-completion`.

## Numbers
Quote prices and limits only from a named repo source with its date (for example `$150`, since 2026-10-04: `apps/web/src/components/marketing/PricingCard.tsx` (`PLAN_PRICE_USD`) and `docs/runbook.md` Live Stripe test step 2; seats: `config.py`). No revenue, MRR, fee or conversion figures unless the founder supplies them with a date and source; otherwise write "not measured".

## Output

```text
mode: test | unknown
SYMPTOM
EVIDENCE      (file:line, log line names, statuses; ids only)
PRODUCT TRUTH (what the code does vs what the page claims)
FOUNDER ACTION (dashboard/Render steps, or "none")
GAP           (exact follow-up, or "none")
```
