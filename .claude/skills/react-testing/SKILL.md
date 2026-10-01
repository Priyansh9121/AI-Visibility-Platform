---
name: react-testing
description: Writes and reviews vitest tests for React components, hooks (through a tiny component) and pages in apps/web or packages/design-system (report components, sign-in and settings panels, theme control). Not for API pytest, Playwright end-to-end flows, or for adding a test library; none of Testing Library, MSW or axe is installed.
---

# React testing in AVP (Vitest, no Testing Library)

Answer the north-star §8 drift check per brief. Test what the user sees and does, not implementation detail.

## What exists today (checked in the repo, 2026-10-01)

- Runner: vitest 2 in `apps/web` and `packages/design-system` (`corepack pnpm --filter @avp/web test`, `... @avp/design-system test`). Web config: `apps/web/vitest.config.ts` (`include: src/**/*.test.{ts,tsx}`, `@` alias, React plugin). Design-system has no vitest config: vitest loads its `vite.config.ts` (React plugin, no `test` block), and adding a `vitest.config.ts` would replace it. Run commands from the repo root.
- The default environment is **node** (no DOM). A file that needs a DOM opts in with a first-line docblock `// @vitest-environment jsdom`; `jsdom` is already a devDependency of both packages. Reason given in `Reveal.test.tsx`: do not move a whole package to jsdom for one component.
- **Not installed:** `@testing-library/*`, `msw`, `vitest-axe`/`jest-axe`, `user-event`. Do not import them. Adding any is a founder decision (see below).

## Three patterns, pick the lightest that proves the behaviour

1. **Static markup** (most component tests): `renderToStaticMarkup` from `react-dom/server`, then assert on the HTML string. Fixtures in `apps/web/src/lib/settings/__fixtures__/`. Example: `apps/web/src/components/settings/BillingPanel.test.tsx`. Use it for copy, tones (`avp-badge--neutral`), structure, and the *absence* of wrong vocabulary. It runs no effects and no events.
2. **Mounted, jsdom**: for effects, state and events. Docblock `// @vitest-environment jsdom`, `createRoot` from `react-dom/client`, `act` from `react`, and `(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true`. Mount into a `div` appended to `document.body`; in `afterEach`, `act(() => root.unmount())` and `document.body.innerHTML = ''`. Type into an input by calling the native `value` setter then dispatching `new Event('input', { bubbles: true })`; submit by dispatching a cancelable `submit` event on the form. Examples: `apps/web/src/app/home.test.tsx`, `components/SignInPanel.test.tsx`, `components/shell/WorkspaceShell.test.tsx`.
3. **Pure logic with a fake window or fetch**: `lib/googleTicket.test.ts` passes a hand-made `{ location, history }` object; `lib/api.googlePending.test.ts` uses `vi.stubGlobal('fetch', vi.fn().mockResolvedValue(...))` and inspects `mock.calls`. No DOM needed.

## Network and module mocking (no MSW)

Mock at the API module's edge, as the mounted tests do:

```tsx
vi.mock('@/lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/api')>();
  return { ...real, api: { ...real.api, me: () => me() } };
});
const { default: Home } = await import('./page'); // import AFTER the mock
```

Spread the real module so `ApiProblem` and constants stay real; replace only the method under test. Reset mocks in `beforeEach`. For fetch-level tests use `vi.stubGlobal('fetch', ...)` and restore it (`vi.unstubAllGlobals()`). Never hit a real host: a new outbound host must be classified in `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`.

## Async

Settle mocked promises inside `act(async () => { await Promise.resolve(); ... })` (see `settle` in `home.test.tsx`). Do not `setTimeout` and assert. Assert on `el.textContent` / `el.querySelector`; with no Testing Library there are no `getByRole` queries, so prefer stable roles, labels and class names from the design system (`avp-field`, `avp-btn`) over structure.

## Providers and the theme

There is no shared `test-utils`. Most components render fine bare. `next-themes` is wrapped once, in `apps/web/src/components/shell/ThemeProvider.tsx` (`attribute="data-theme"`, `defaultTheme="light"`, `storageKey` `avp.theme`); a test that needs theme state wraps the component in that provider under jsdom. next-themes 0.4.6 calls `window.matchMedia` on mount and jsdom has none: stub `window.matchMedia` per test, as `packages/design-system/src/components/Reveal.test.tsx:66` does. A hook is tested through a tiny component that calls it (pattern 2). The report (`.avp-report`) stays paper in both themes, so never assert a dark report (`reportIsolation.test.ts`, `ReportView.test.tsx`). If a third test needs the same wrapper, add a small helper beside the tests; do not build a provider stack speculatively. No React Router exists (App Router): mock `next/navigation` hooks at the module edge if a component uses them.

## Accessibility

axe is not installed. Until the founder decides, assert the basics directly: labels bound to inputs (`>Email<` beside `avp-field`), `alt` on images, `aria-*` present where the component sets them. For a real audit of a rendered page use the user-level `accessibility-scan` skills or `playwright-best-practices`. jsdom has no layout or CSS, so contrast and layout belong in a browser.

## Guard tests grep source on purpose; do not route around them

`providers.test.ts`, `reportIsolation.test.ts`, `stockClasses.test.ts` (fails any stock Tailwind class, `dark:`, ring width without colour, border width without a `border-line-*` colour), `referrerPolicy.test.ts`. Stock classes compile to nothing and jsdom has no stylesheet, so a component test cannot see that failure: only `stockClasses.test.ts` does. Run the FULL design-system suite after touching classes.

## Snapshots and Playwright

No DOM snapshots: assert the specific facts. Layout, flex whitespace, scroll, downloads, real navigation and multi-page flows are Playwright's (`playwright-cli`, `playwright-best-practices`), not vitest.

## Anti-patterns

- Asserting render counts or internal state; mocking React; mocking child components by default.
- Ignoring `act()` warnings; leaking a root or `document.body` content between tests.
- Sharing mutable state across tests; `it.skip` left in.
- Turning `IS_REACT_ACT_ENVIRONMENT` on globally in a node-environment file.
- Copying real AI answers or page HTML into fixtures (facts-only: ids, counts, URLs, names).

## TDD loop

Red (failing test, for the right reason) -> green (minimal code) -> refactor. Use `test-driven-development`. For a regression test, break the code, see the test fail, restore byte-for-byte (`cp` to `$LOG` and back, then `cmp`; never `git checkout` or `git stash`).

## Commands and count gates

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
# one file while working
rc=0; corepack pnpm --filter @avp/web exec vitest run src/path/file.test.tsx > "$LOG/one.log" 2>&1 || rc=$?; echo "rc=$rc"
# run from the repo root. Full suites: the exit code is the first gate, assert_count the second
rc=0; corepack pnpm --filter @avp/web test > "$LOG/web.log" 2>&1 || rc=$?; echo "rc=$rc"
[ "$rc" = 0 ] || { echo "FAIL rc=$rc"; exit "$rc"; }
python3 infra/ci/assert_count.py web-vitest "$LOG/web.log"
rc=0; corepack pnpm --filter @avp/design-system test > "$LOG/ds.log" 2>&1 || rc=$?; echo "rc=$rc"
[ "$rc" = 0 ] || { echo "FAIL rc=$rc"; exit "$rc"; }
python3 infra/ci/assert_count.py design-system-vitest "$LOG/ds.log"
corepack pnpm --filter @avp/web typecheck
corepack pnpm --filter @avp/design-system typecheck
```

Never pipe test output (`| tail`, `| grep`); read the file afterwards. Floors live in `infra/ci/floors.json` (read the current numbers there); bump a floor in the same change that adds tests. There is no coverage-percentage target: the gate is the test count plus the negative control.

## Founder decision: new test dependencies

`@testing-library/react`, `user-event`, `msw` and `vitest-axe` would each be a new dev dependency: licence check (`apps/api/scripts/js_license_audit.py`, `infra/ci/licence-pending-decisions.json`), a lockfile change, and `providers.test.ts` review if any ends up a runtime dependency. They are dev-only, yet the existing pattern (act + createRoot + native events) already works, so propose the addition with a concrete test it unblocks rather than installing it. Do not run an install from this skill.
