# Changes (Apache-2.0 section 4(b): this file states that the files were modified)

This is a rewrite for AVP's design system, not a light edit. Almost no source
sentence survives verbatim. Paths below are relative to the source folder.

## Removed
- shadcn/ui, Radix, `components.json`, react-hook-form/zod form pattern and all `references/shadcn-*.md` -> a second component library beside `@avp/design-system` (`SKILL.md:35-40,58-94,114-152`).
- `npx shadcn@latest init` / `add` install steps -> installer and dependency, breaks the no-new-dependency rule (`SKILL.md:60-70`; `scripts/shadcn_add.py:116`).
- Tailwind-only setup with `npm install -D tailwindcss @tailwindcss/vite` and `@import "tailwindcss";` -> Tailwind 4 syntax; AVP is Tailwind 3.4 with a preset (`SKILL.md:96-112`).
- `references/tailwind-customization.md` (`@theme {` at line 12, custom variants, `@apply`) -> Tailwind 4 directives; AVP customises only through the preset.
- Canvas posters and `references/canvas-design-system.md` -> not an AVP UI concern (`SKILL.md:49-54,196-209`).
- Both Python scripts and their tests (`scripts/shadcn_add.py`, `scripts/tailwind_config_gen.py`, `scripts/tests/*`) -> shadcn installer and an Inter-based config generator; execute-nothing rule (`SKILL.md:211-225`).
- Stock-palette example `bg-white dark:bg-gray-900 ... border-gray-200 dark:border-gray-700` -> emits nothing under the AVP preset and uses `dark:` (`SKILL.md:300-315`).
- External links (ui.shadcn.com, tailwindui.com, headlessui.com, v0.dev, radix-ui.com) and the "Resources" list -> outbound references and unwanted tools (`SKILL.md:15-18,317-324`).
- "Museum-quality", "Expert Craftsmanship" filler and the frontmatter `argument-hint`, `metadata`, `license: MIT` -> unsourced flourish; licence claim conflicts with `LICENSE.txt` (Apache-2.0).
- Generic Tailwind class catalogues (`references/tailwind-utilities.md`, `tailwind-responsive.md`) as full copies -> stock scales that do not exist here; replaced by one preset-checked table.

## Replaced
- `form.handleSubmit(console.log)` (`SKILL.md:283`; also `references/shadcn-components.md:65`) -> the rule "never log form values" (a login form's values contain a password and an email; project rule: no secret or email in a log).
- Description "Use when building user interfaces, implementing design systems, creating responsive layouts..." (`SKILL.md:3`) -> triggers only on writing or restyling markup and classes in `apps/web` or `packages/design-system`; excludes critique and new visual directions.
- `container mx-auto p-6 grid gap-6 md:grid-cols-2` example (`SKILL.md:79`) -> AVP-checked layout pattern with token widths; `text-2xl font-bold`, `text-muted-foreground` and `hover:shadow-lg` do not exist here.
- "Design Tokens: consistent spacing scale, colour palettes" (`SKILL.md:233`) -> the real token families, spacing keys, four fonts, shape lock, from `tailwind-preset.ts` and `docs/design-system.md` §0-§3.
- "Dark Mode Consistency: apply dark variants" (`SKILL.md:234`) -> do NOT use `dark:`; the config sets no `darkMode` (Tailwind default `media`), the theme is `[data-theme='dark']` and role tokens follow it. The report stays paper in both themes.
- Mobile-first breakpoints (`references/tailwind-responsive.md:5-33`) -> kept as one short section; the default breakpoints are not replaced, widths use token `max-w-*`.
- "Accessibility-First: leverage Radix UI primitives" (`SKILL.md:232`; `references/shadcn-accessibility.md`) -> semantic HTML, the global `:focus-visible` ring, `TextField`/`SelectField` wiring, lucide-react `aria-hidden`, colour never alone; warning about bare `ring` falling back to stock blue.
- "Performance: avoid dynamic class names" (`SKILL.md:235`) -> kept, reworded as workflow step 4.

## Kept (as ideas, rewritten)
- Composition over custom CSS (`SKILL.md:229-230`) -> "reuse exported components before markup" and "utilities for layout only".
- Utility-first workflow and mobile-first responsive thinking (`SKILL.md:230-231`).
- Accessibility habits (`SKILL.md:232`).

## Added
- Verification section using the test-runner agent, full design-system and web vitest, `assert_count.py` keys, ui-reviewer and AccessLint.
- `references/token-map.md`: stock-habit -> AVP class table.
- Statements checked against files rather than the source: bare `border` draws `currentColor` and bare `ring` falls back to `#3b82f6` (both read from `apps/web/node_modules/tailwindcss/stubs/config.full.js`, not compiled); no `dark:` utility and no `darkMode` setting exist in `apps/web` or `packages/design-system` (grep 2026-09-30); `apps/web/src` has no `console.` call outside tests.

## Review fixes (security review, PASS WITH NITS)
- `SKILL.md` intro and Verification: removed the claim that "a test or a build catches" stock classes. Tailwind 3.4 skips unknown classes, `next build` passes, and `reportIsolation.test.ts` (about :218-338) guards only numeric spacing suffixes, `-semantic-` colours, unknown `line`/`surface`/`text`/`on` suffixes and `rounded-*` suffixes. Listed what it covers, added a read-only `rg` for stock palette, font size, shadow, bare `rounded` and `font-sans/serif`, and a browser look. Extending the guard is the founder's call.
- `SKILL.md` Verification: `license_audit.py` walks only the API's Python env; JS package licences are read by hand from `package.json`, plus `providers.test.ts` and `/privacy`.
- `references/token-map.md`: marked left-hand rows that still compile (`leading-*`, `tracking-wider`, `max-w-prose`, `duration-150 ease-in-out`, `bg-gradient-to-r`, `ring-2`).
- `references/token-map.md`: `ring`/`focus:ring` now maps to `focus-visible:shadow-focus`, with the note (from reading the cascade at `base.css:50`, unrendered) that any `shadow-*` on a button, link or input overrides the global focus ring.
- `SKILL.md` step 2: stated the inline-style exception (`rampVars()` at `ScoreMeter.tsx:101`, `visibilityColor()`/`onVisibility()` at `Badge.tsx:88`); hand-written inline colours stay banned.
- `SKILL.md` step 3: `cn` only joins strings, so the example is `dense ? 'gap-2' : 'gap-4'`.
- Earlier "Removed" bullet: `next-themes` dropped from the shadcn baggage list; it is AVP's own dependency (`apps/web/package.json`, `ThemeProvider.tsx`).
- Apache-2.0 4(b): added "Modified from ui-styling (Apache-2.0); see CHANGES.md." at the top of `SKILL.md` and `references/token-map.md`.
