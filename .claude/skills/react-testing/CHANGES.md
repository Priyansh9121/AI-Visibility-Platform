# Changes from upstream (skills/react-testing/SKILL.md)

- Rewritten around what AVP has: vitest, default node environment, per-file `// @vitest-environment jsdom`, `renderToStaticMarkup`, `createRoot` + `act` -> removes the false assumption of Testing Library/RTL (src :8-20, :49-108, :152-225). Verified against `apps/web/vitest.config.ts`, `home.test.tsx`, `SignInPanel.test.tsx`, `BillingPanel.test.tsx`.
- Removed `@testing-library/user-event` and RTL query-priority/`waitFor`/`renderHook` sections -> not installed; replaced by the native-event and `act` pattern (src :49-106, :184-225).
- Removed MSW setup and examples, replaced by `vi.mock('@/lib/api')` and `vi.stubGlobal('fetch')` -> new dependency and outbound-host gating (src :110-150, :118, :365-384).
- Removed `jest-axe`/`vitest-axe` section -> new dependency; replaced by direct label/alt assertions and a pointer to AccessLint and playwright skills (src :227-247, :230).
- Removed `MemoryRouter`, `ThemeProvider theme={lightTheme}`, React Query wrapper and the test-utils example -> React Router, styled theme and TanStack Query do not exist in AVP; replaced by the real `next-themes` ThemeProvider facts (src :152-182, :172).
- Removed Jest, Playwright/Cypress Component Testing, Percy/Chromatic and Cypress content and Jest commands -> Vitest only (src :38-47, :262, :347-349).
- Removed coverage percentage targets and `coverage.thresholds` config -> replaced by count gates (`infra/ci/floors.json`, `infra/ci/assert_count.py`) (src :282-308).
- Commands now never piped: output to `$LOG` file, own exit code, `assert_count.py` (src :338-354).
- Added the guard-tests-grep-source-on-purpose section and the stock-class blind spot of jsdom -> prevents "fixing" the guards (AVP-FACTS).
- Removed the Related block (rules/react/testing.md, react-reviewer, tdd-guide, `/react-test`, `/react-review`, sibling skills) and the `origin: ECC` metadata -> do not exist here; points to `test-driven-development`, `playwright-cli`, `playwright-best-practices` instead (src :356-361).
- Dropped the error-boundary and Suspense examples -> generic; Testing Library based. Dropped "When to Activate" list in favour of a narrower description.
- Rewrote the description to third person with when-not and AVP surfaces (src :3).
- Floors quoted (1104, 846) are read from `infra/ci/floors.json` on 2026-10-01 and will drift.

## Review fixes (2026-10-01)
- Commands now gate on the exit code before `assert_count.py` (second gate, ci.yml:273,289); "run from the repo root" added, design-system typecheck added -> a failed run can no longer pass on a stale count.
- Theme section: next-themes calls `window.matchMedia`, jsdom has none; stub per test as `packages/design-system/src/components/Reveal.test.tsx:66` -> avoids a mount crash.
- Removed the invented drift-check line; now "answer the §8 drift check per brief".
- Floor numbers dropped; "read `infra/ci/floors.json`" -> numbers drift.
- Description in third person; hooks tested through a tiny component (pattern 2).
- Design-system config corrected: vitest loads `vite.config.ts`; a `vitest.config.ts` would replace it.
