# Changes from upstream skills/click-path-audit/SKILL.md

- Description rewritten: third person, names AVP surfaces and when not to use -> upstream's generic trigger "after any major refactor touching shared state stores" (:3) would fire on unrelated work; AVP has no shared store.
- Removed `metadata: origin: community` frontmatter -> not part of the library contract.
- Removed the Zustand/Redux store-mapping step and "emailStore" example (:49-74, :211-245) -> AVP state lives in route components; replaced by a per-route state map and the view-machine overwrite case (`app/page.tsx`). The "Bug That Inspired This Skill" story (54 bugs, ThreadList) is the author's app; removed.
- Removed the 8-agent split "Agent 2: Dashboard (Tasks, Notes, Journal, Ideas)", Chat, Emails, CRM, Management Suite (:170-183) -> the risk named in the audit; replaced by a table of AVP's real routes, each confirmed to exist under `apps/web/src/app`.
- Removed `/superpowers:systematic-debugging`, `/superpowers:verification-before-completion`, `/superpowers:test-driven-development` links (:205-207) -> plugin not installed; replaced with `systematic-debugging`, `verification-before-completion` (installed) and `test-driven-development` (installed), plus `avp-ui-styling` (library).
- Added React 19 / Next 15 section (StrictMode double-run, `use(params)`, refs, useOptimistic vs hand-rolled, actions/transitions, cleanup) -> verdict asked for it. States that no server actions or `useOptimistic` are used today (grep of `apps/web/src`, 2026-10-01).
- Added patterns "stuck pending", "swallowed problem", "stale after error" -> AVP's ApiProblem `detail`/`fieldErrors()` and 402/409/429/503 handling.
- Added six seed candidates, each read in code on 2026-10-01 and marked unconfirmed -> so the first run has leads; none asserted as fact.
- Added live-check rules: playwright-cli, localhost:3000 only, no production, no founder browser, no real tokens, Stripe test mode -> avoids credential/browser-profile access.
- Added "read-only" statement and no-token-in-output rule -> CLAUDE.md secrets rule; legal pages copy left to the founder.
- Kept: per-touchpoint trace method, six upstream bug patterns, report format (extended with Evidence and Path lines).
- Unsourced numbers removed: "54 bugs" (:24, :205).

## Security review fixes (2026-10-01)
- M1: live checks never start a scan, email, checkout or Google sign-in; API on the 55433 avp_dev URL; spend needs founder OK -> local API holds live provider keys and SerpApi (source :203-209 had no live-check rules at all).
- L2: forbidden playwright-cli commands named, `${LOG:?}` guard, no real-token URLs -> browser-profile/cookie access and token leakage into the transcript.
- L3: avp-ui-styling marked library-only with the `${LOG:?}` guard -> it is not installed.
- L4: added /clients and /clients/[clientId]/* row; noted fragment-token files exist only on launch-readiness and later -> missing coverage and branch mismatch.
- L5: seed 2 marked billing-sensitive, security-reviewer required, Subscribe limited to no subscription, canceled, incomplete_expired -> founder decision.
- Nit: seed 6 corrected (failed refetch shows the GET's ApiProblem detail); CHANGES "54" reference corrected to :205.
- Re-review nits: seed 2 cites founder decision 4 (2026-10-01 brief); removed the Stripe 'do not complete a checkout' sentence (contradicted the no-checkout rule); fragment-file note names `origin/launch-readiness` (PR #2) and `post-launch-readiness` -> accuracy and consistency.
