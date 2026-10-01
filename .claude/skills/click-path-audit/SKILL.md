---
name: click-path-audit
description: Read-only code audit of AVP's critical click paths in apps/web (sign-up and sign-in, Google ticket, onboarding, scan start and polling, report and share link, Settings billing, invites and password reset, /crawler, /privacy, /terms), tracing each handler's state writes to find buttons that do nothing, dead-end, or hide an ApiProblem. Use when a button "does nothing" or before a release touching these flows; not for API bugs (use systematic-debugging), styling, or performance.
---

# click-path-audit: AVP behavioural flow audit

Find bugs that reading one function at a time misses: a later call undoes an earlier one, an effect resets what a click set, a disabled button never re-enables, an `ApiProblem` is swallowed so the user sees nothing.

**Read-only.** The audit reads `apps/web/src` and reports. It edits nothing, commits nothing. Fixes are a separate change (test first: `test-driven-development`).

Drift check (`docs/north-star.md` §8): touches the web app layer; serves the onboarding and scan-and-watch phases.

## Method, per touchpoint

1. Identify the handler (`onClick`, `onSubmit`, effect, `hashchange`).
2. List each call in order: what state it reads, writes, and resets as a side effect.
3. Ask: does a later call, effect, or poll undo an earlier write? Does the final UI match the button label?
4. Ask: what happens on each failure? Is `ApiProblem.problem.detail` (or `fieldErrors()`) shown, or swallowed?
5. Ask: can the control be left disabled or the view left in `working` forever?
6. Ask: is the path correct under React 19 dev (StrictMode double-run, `use(params)`, refs)?

State map first (once per area): for each `useState`/`useRef` owner (the route file), note setters, what they reset, and what derives from them. AVP has no global store: state lives in route components (`app/**/page.tsx`) and is passed to pure views in `components/`. The "dangerous reset" to look for is a view-state machine (for example `View` in `app/page.tsx`) overwritten by a late async `setView`.

## Bug patterns

- **Sequential undo**: call B resets what call A set.
- **Async race**: two requests settle in either order, or a stale response overwrites a newer one (look for a missing `cancelled` flag or key check).
- **Stale closure**: `setX(x + 1)` twice; use the updater form.
- **Missing transition**: label says Save or Delete, handler only validates.
- **Dead path**: a branch guarded by state that is always false there.
- **Effect interference**: an effect resets what the click set.
- **Stuck pending**: `busy` set true, success path navigates away and never resets it (see bfcache below).
- **Swallowed problem**: `catch {}` or a generic string where `err.problem.detail` exists; a 4xx the user can act on (409, 402, 429) shown as a generic failure or not at all.
- **Stale after error**: a 409 says the world changed, but the screen still shows the old server state and offers the same action.

## React 19 and Next 15 cases

As of 2026-10-01 `apps/web/src` uses none of `'use server'`, `useActionState`, `useOptimistic`, `useFormStatus` or `useTransition` (grep returned nothing). Forms use `onSubmit` + `useState` busy flags. Check these when reviewing, and re-grep before assuming.

- **StrictMode double-run of effects in dev** (`next.config.mjs` sets `reactStrictMode: true`). An effect that *consumes* something (clears the address bar, reads a one-shot value) loses it on the second run. AVP's answer is a ref that holds the first read (`lib/fragmentToken.ts`, `app/sign-up/google/page.tsx`). Any new "take then clear" code needs the same, and a test that runs the effect twice.
- **`use(params)`** (`app/scans/[scanId]/report/page.tsx`): params is a Promise in Next 15; a client page that reads `params.x` directly gets a Promise.
- **Refs**: a ref read during render, or a ref that survives a remount it should not. `poller` in `app/dashboard/page.tsx` is built in an effect and read in others; order of declaration matters.
- **`useOptimistic` would revert on failure; hand-rolled optimistic state does not.** `dismissGettingStarted` in `app/dashboard/page.tsx` sets state then swallows the failure; check the screen and server cannot disagree for long.
- **Actions and transitions**: if a form is moved to `useActionState` or a server action, check pending state, that the error is returned (not thrown past the boundary), and that a redirect does not leave `busy` stuck.
- **Cleanup**: every effect that starts a fetch has a `cancelled` flag or an abort; every listener is removed (`visibilitychange`, `hashchange`).
- **Late `setState` after navigation**: `window.location.assign` does not stop an in-flight `finally { setBusy(false) }`; harmless, but flag if it re-enables a submit button.

## AVP path checklist

Confirm each route exists (`find apps/web/src/app -name page.tsx`) before auditing. Routes as of 2026-10-01:

| Path | Files | Look at |
|---|---|---|
| `/` sign-in, sign-up, forgot, intake | `app/page.tsx`, `components/SignInPanel.tsx`, `SignUpPanel.tsx`, `ForgotPasswordPanel.tsx`, `IntakeForm.tsx`, `lib/auth/frontDoor.ts`, `lib/auth/googleFailure.ts` | `load()` runs `takeGoogleFailure` and `takeFrontDoorForm`, which clear the query; under dev double-run the second `load()` sees nothing. Sign-in 401/422/429 messages; sign-up 409 email and 422 field errors. |
| `/sign-up/google` | `app/sign-up/google/page.tsx`, `components/google/GoogleSignUpPanel.tsx`, `lib/googleTicket.ts` | `#ticket=` read once into a ref; `POST /auth/google/pending` peeks, does not consume (`routers/auth.py`), so the dev double call is safe; `google/complete` 422 field errors; expired state. |
| `/welcome` | `app/welcome/page.tsx`, `components/welcome/WelcomeView.tsx` | `me()` failure path, onboarding state machine. |
| `/dashboard` (start a scan, watch it) | `app/dashboard/page.tsx`, `components/dashboard/DashboardView.tsx`, `lib/dashboard/polling.ts` | `rerun` adds to `watching`, then `load()`; 402 scan limit is a prompt, not an error; poll trouble shown as `pollProblem`; visibility poke; poller rebuilt under StrictMode. |
| `/scans/[scanId]/report` | `app/scans/[scanId]/report/page.tsx`, `components/report/ShareLinkBar.tsx`, `CompetitorEditor.tsx`, `DownloadPdfButton.tsx` | 401, 404 and other states; `reload` after a competitor save; share mint failure state; clipboard denial. |
| `/share#token` | `app/share/page.tsx`, `lib/fragmentToken.ts`, `app/share/[token]/page.tsx` (redirect) | Fragment kept, `hashchange` followed; every error collapses to one state on purpose (do not "fix" that). |
| `/invite#token`, `/reset-password#token` | `app/invite/page.tsx`, `app/reset-password/page.tsx`, `components/invite/AcceptInvitationView.tsx`, `components/auth/ResetPasswordView.tsx`, `components/routes/FragmentRedirect.tsx` | Fragment cleared once, held in a ref; 400 invalid vs 422 rejected; the legacy `[token]` routes redirect via `FragmentRedirect`. |
| `/settings` Billing | `app/settings/page.tsx`, `components/settings/SettingsView.tsx`, `BillingPanel.tsx`, `SeatsPanel.tsx`, `PasswordChangePanel.tsx` | `subscribe` and `manage` set `busy`, then `window.location.assign`; 409 `/problems/subscription-already-active` and `/problems/checkout-in-progress`, 503 service-unavailable shown via `problem.detail`; which subscription states offer which button. |
| `/clients`, `/clients/[clientId]/*` (prompts, alerts, competitors, gaps, rankings, sentiment, sources, technical, crawler) | `app/clients/page.tsx`, `app/clients/[clientId]/**/page.tsx`, `components/clients/ClientsView.tsx`, `components/client/Client*View.tsx` | `ClientPromptsView` form `onSubmit`; alerts page `api.acknowledgeAlert` and the row "acknowledge failed" state; each page's 401/404/other load state. |
| `/crawler`, `/privacy`, `/terms` | `app/crawler/page.tsx`, `app/privacy/page.tsx`, `app/terms/page.tsx` | On `/privacy` and `/terms` the header "Log in" and "Get started" go to `/?view=sign-in` and `/?view=sign-up` (`frontDoorHref`); links resolve. Review for dead links only. Do not propose copy changes: legal pages are the founder's. |

Token routes: never log, print or paste a real token from a run; use fixtures. Token routes send `Referrer-Policy: no-referrer` (`referrerPolicy.test.ts`). Fragment-token files (`app/share/page.tsx`, `app/invite/page.tsx`, `app/reset-password/page.tsx`, `lib/fragmentToken.ts`, `lib/googleTicket.ts`, `components/routes/FragmentRedirect.tsx`) exist on `origin/launch-readiness` (PR #2) and `post-launch-readiness`, not on main; on main those routes are path-token pages. Check which branch you are on.

## Seed candidates (read 2026-10-01, NOT confirmed in a browser)

Start from these; confirm or drop each, then add your own.

1. `components/settings/BillingPanel.tsx` `subscribe` and `manage`: on success `busy` stays true after `window.location.assign(url)`. If the user returns with the browser Back button and the page is restored from bfcache, the button may be stuck on "Opening portal…" and disabled. Pattern: stuck pending.
2. `BillingPanel` with a non-null status that is not active (`canceled`, `incomplete`, `incomplete_expired`): only "Manage billing" is offered, and only when `hasBillingAccount`; the `incomplete` copy says "Starting again is the fix" but no subscribe button is in that branch. Check whether the API allows checkout from these states, then whether there is a dead end. Billing-sensitive: any fix goes through `security-reviewer`. Per founder decision 4 in the 2026-10-01 brief (recorded in `docs/build-log.md` once the checkout change lands), Subscribe is offered only for no subscription, `canceled` and `incomplete_expired`; `past_due`, `unpaid`, `paused` and `incomplete` must use Manage billing. So the `incomplete` copy may need changing, not a new button.
3. `BillingPanel` 409 from checkout (`subscription-already-active`): the detail is shown, but `billing` is a prop from the page load, so the panel keeps showing the pre-409 state and the same subscribe button. Pattern: stale after error.
4. `app/page.tsx` `load()` under dev double-run: `takeGoogleFailure` and `takeFrontDoorForm` clear the query on the first run, so the second run may find neither and overwrite the `sign-in` or `sign-up` view with `signed-out` when `api.me()` returns 401. Dev only (production does not double-run). Compare with the ref pattern in `lib/fragmentToken.ts`.
5. `app/share/page.tsx`: on a `hashchange` the old report stays on screen until the new fetch settles (the view is not reset to `loading`). Low severity; confirm.
6. `app/scans/[scanId]/report/page.tsx` `reload` + `CompetitorEditor.tsx`: if the competitor save succeeds and the refetch then rejects, the editor's catch shows the refetch (GET) ApiProblem `detail` (or the generic save-failed line for a non-ApiProblem) as if the save had failed, although it was saved.

## Live checks (optional)

Only with the installed `playwright-cli` skill, only against `http://localhost:3000` (CORS needs `localhost`, not `127.0.0.1`), with the API started without `--reload`. Never production, never the founder's signed-in browser profile, never real credentials or a real token; use a throwaway test account created on the local stack. A live run confirms a candidate; it does not replace reading the code.

Live checks never start a scan, send an email, run checkout or use Google sign-in: the local API uses live provider keys and SerpApi, and a scan spends real credit and crawls a real site. Start the API with `DATABASE_URL` set to the 55433 `avp_dev` URL (never the `.env` default). Any spend needs the founder's OK.

Run `playwright-cli` from a shell that has `LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"`, in a subshell `( cd "$LOG" && ... )`, because snapshots go to `.playwright-cli/`, which is not gitignored. Forbidden commands: `attach --extension`, `attach --cdp`, `--persistent`, `--profile`, `cookie-*`, `state-save`. Never open a `/share#...`, `/invite#...` or `/reset-password#...` URL with a real token (URLs are printed into the transcript).

## Report format

```
CLICK-PATH-NNN [CRITICAL|HIGH|MEDIUM|LOW]
  Path: [route] Touchpoint: [label] in [file:line]
  Pattern: [one of the above or a React 19 case]
  Trace: 1. [call] -> sets {..}   2. [call] -> resets {..}  <- conflict
  Expected: [what the label promises]   Actual: [what happens]
  Evidence: code read / live check (localhost:3000)
  Fix (for a later change): [specific; name the test that should prove it]
```

End with the paths audited, the paths not reached, and candidates dropped with the reason.

## Related

`systematic-debugging` first for crashes and API faults; then this. `test-driven-development` for a failing test per bug found (vitest in `apps/web`, run as `corepack pnpm --filter @avp/web exec vitest run <file> > "$LOG/x.log" 2>&1`, read the exit code, never pipe). `verification-before-completion` before calling a fix done. `avp-ui-styling` (library only, not installed; read `~/Skills-library/avp-ui-styling/SKILL.md`, and set `LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"` before any shell snippet in it) for layout or token issues found in passing.
