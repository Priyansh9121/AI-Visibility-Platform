# Changes from ECC finance-billing-ops

Source lines are in `ECC-main/skills/finance-billing-ops/SKILL.md`.

- Renamed `finance-billing-ops` -> `stripe-billing-ops`; description rewritten third person, AVP surfaces named, with "does not apply" limited to pricing strategy and unrelated payment code (refunds etc. now trigger it, see revision below) -> removes the generic trigger (:3 "billing-model truth workflow for ECC").
- Dropped `metadata: origin: ECC` (:4-5) -> ECC branding.
- Removed the "Skill Stack" (:14-22: `customer-billing-ops`, `research-ops`, `github-ops`, `verification-loop`) -> siblings that do not exist here. Verification now points at installed names only (`verification-before-completion`, `fastapi`, `security-reviewer`); `market-research` is not referenced.
- Removed "Prefer live billing data" (:46) and the live-vs-snapshot default -> replaced by a mode line (`mode: test | unknown`) and a rule that the agent never handles live keys or live mode.
- Removed the `DECISION - refund / preserve / convert` output (:110-111) and "recommended operator action" refunds (:88-93) -> an agent must not decide or perform money movement; output now routes to "FOUNDER ACTION".
- Removed revenue/MRR/sales-snapshot and competitor-pricing scope (:26-28, :89, :121) -> no dated source exists in AVP; replaced by a "Numbers" rule (named source + date, else "not measured").
- Added approval gates: refunds, cancellations, price changes and live mode are the founder's in the Stripe dashboard; live keys set on Render only (CLAUDE.md "Secrets", `config.py` comment) -> new, source had none.
- Rewrote the seat guidance (:39 "do not say per seat unless...") for AVP: seat limit is `default_seat_limit = 3` enforced in `services/seats.py`; Stripe quantity is 1 -> keeps the source's good idea, grounded in code.
- Added code-backed sections for checkout (409 already-active, row lock with 3 s `lock_timeout`, expiry of open sessions, Stripe-side live-subscription check), portal, webhook (`verify_webhook`, `stripe_webhook_events`, ordering watermark, state machine, second-subscription warning), each verified in `services/billing.py` -> replaces generic "inspect checkout/entitlement" (:74-83).
- Added runbook-linked procedures B (second subscription) and C (Live Stripe test, test mode), pointing at `docs/runbook.md` rather than copying steps -> avoids drift.
- No `allowed-tools`, no hooks, no install steps, no `!`-backtick injection (none existed in the source).
- Found, not fixed in AVP: `routers/billing.py` (start_checkout docstring, ~lines 155-158) says checkout-in-progress is `503`, but `errors.py:136` makes `CheckoutInProgress` a `Conflict` (409) and the OpenAPI responses block lists only 409 already-active. Flagged for the founder/scribe.

## Revision after security review (2026-10-01)
- Dropped the former section D (restricted-key curl diagnostics) entirely -> its prefix guard used `false` in a `case` arm, which only set `$?`, so the following curl still ran with a live key. Diagnostics are now dashboard-only, done by the founder; no key, curl or network call near an agent.
- Gate 1 now bans the Stripe CLI (`stripe login` stores a live-mode key) and direct calls to Stripe -> removes the key-handling path. Removed `sk_test_`/`rk_test_` mentions and the `STRIPE_DIAG_KEY` presence check (nothing needs them now); the test-mode evidence line now cites the dashboard toggle only.
- Description now triggers on refunds, cancellations, disputes and plan/price changes and says the answer is founder-only dashboard steps plus the gates (was "Not for refunds...").
- Added error-handling note: only the existing-customer pre-check catches `StripeError` (`services/billing.py:386`); customer create (:259), session create (:397) and portal (:448) are unhandled and surface as 500 (`errors.py` ~284-330); "check the current code" as the branch may change it.
- Billing read now says it also counts scans (`routers/billing.py:102`).
- Added the `current_period_end` under `items.data[]` note (`services/billing.py:175-212`).
- Code-change procedure now requires `security-reviewer` for billing diffs and says `assert_count api-pytest` is for the full suite only, not a targeted run (`CLAUDE.md`, `infra/ci/floors.json`).
- Fixed the `market-research` mention in the earlier bullet (SKILL.md never referenced it).
- Gate 1 also bans indirect Stripe calls through AVP's own checkout/portal endpoints on the local stack (.env test key, real test objects); use the patched `test_billing.py`.
- Error note now states 503 (existing-customer pre-check) vs 500 (elsewhere); screenshot evidence must be cropped to the Test mode toggle; price/seat citations moved from `LandingView.tsx` to `PricingCard.tsx:69,79` (verified `PLAN_PRICE_USD = 160`, `PLAN_SEATS = 3`); description in third person ("Applies when...") and quoted for valid YAML.
