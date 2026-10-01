---
name: avp-ui-styling
description: Use when writing or restyling markup and Tailwind classes in apps/web or packages/design-system, so the classes actually compile against AVP's replace-theme preset and reuse the exported components. Not for design critique (use impeccable or design:design-critique) and not for choosing a new visual direction.
---

# AVP UI styling

Modified from ui-styling (Apache-2.0); see CHANGES.md.

Style AVP screens with the design system's own tokens and components. The
preset (`packages/design-system/src/tailwind-preset.ts`) REPLACES Tailwind's
colours, spacing, font family, font size, radius and shadow. A stock class such
as `bg-white`, `text-gray-500`, `text-sm`, `p-7` or `shadow-md` **emits nothing
and raises no error**. Tailwind 3.4 skips an unknown class silently, so
`next build` passes, and the existing guard tests cover only part of the
problem (see Verification). Most stock classes are caught by nothing but your
own check and a look in the browser.

Scope: the working screens in `apps/web`, the components in
`packages/design-system`, and the marketing pages. Reference for the tokens
themselves: `docs/design-system.md` §0 (read first), §1 colour, §2 type,
§3 spacing. Stock-class replacements: `references/token-map.md`.

## Workflow

1. **Reuse before markup.** Import from `@avp/design-system` before writing a
   `div`: `Button`, `Card` (+ `CardHeader`, `CardTitle`, `CardBody`,
   `CardFooter`), `Badge`, `StatTile`/`StatRow`, `DataTable`, `TextField`,
   `SelectField`, `PageHead`, `ScoreHero`, `LoadingState`, `ErrorState`,
   `EmptyState`, `PageSection`, `AppShell`. A field, empty state or error
   written longhand is how the product ends up looking assembled rather than
   designed (`EmptyState.tsx` header comment). Check `src/index.ts` for the list.
2. **Then utilities, for layout only.** Utility-first is right here for flex,
   grid, gap, margin and width around components. Colour, radius, elevation and
   type come from tokens (below). No new CSS file, no `@apply`, no arbitrary
   colour (`bg-[#fff]`, `text-[oklch(...)]`) and no hand-written inline style
   colour. The exception is a component-provided inline CSS variable or value
   computed from a score: `rampVars(score)` (`ScoreMeter.tsx:101`) and
   `visibilityColor()`/`onVisibility()` (`Badge.tsx:88`).
3. **Compose classes with `cn`** from `@avp/design-system` (not a new `clsx`
   dependency): `cn('flex', dense ? 'gap-2' : 'gap-4', className)`. It only joins
   strings (dropping `false`, `null`, `undefined` and empty strings); it does
   not resolve conflicts, so never pass both `gap-4` and `gap-2`.
4. **Never build class names dynamically** (`` `gap-${n}` ``): Tailwind's scanner
   cannot see them. Write whole class strings, or a lookup of whole strings.
5. **Verify** (last section).

## What the preset gives you

| Concern | Classes that exist | Notes |
|---|---|---|
| Surface | `bg-surface-{ground,sunken,seated,raised,void,hover,desk}` | Role tokens respond to dark mode. Prefer these over raw ramps. |
| Text | `text-text-{primary,body,secondary,tertiary}` | Headings primary, body body, captions secondary/tertiary. |
| Rules | `border-line-{hairline,strong,ink}` | Always paired with a width (`border`). |
| On a fill | `text-on-{accent,danger,warn}` | Text on a `beacon`, `danger` or `warn` fill. Never `text-white`. |
| State | `text-{success,warn,danger,info}`, `bg-...` | System state only, never a score. |
| Brand | `beacon-{050,100,400,600,700}`, `signal-*` | Beacon is the client under analysis, always. `signal` is its gradient partner only. |
| Type size | `text-ui-{2xs,xs,sm,base,md,lg,xl}` | 11/12/13/14/16/18/20 px. `text-ed-*`, `text-score` are editorial and belong to the report. `text-kpi`, `text-hero` for the dark identity's figures. |
| Fonts | `font-display` (Space Grotesk), `font-ui` (IBM Plex Sans), `font-mono` (IBM Plex Mono), `font-editorial` (Fraunces) | `font-editorial` only inside `.avp-report`. No Inter. |
| Spacing | keys `0 px 0.5 1 1.5 2 3 4 5 6 8 10 14 18 24 32 rhythm beat` | `p-`, `m-`, `gap-`, `space-`, `inset-` all read this scale. 7, 9, 11, 12, 16, 20 do not exist. |
| Radius | `rounded-{none,sm,md,lg,xl,2xl,full}` | Shape lock: cards and tiles `2xl`, buttons/inputs/nav rows `lg`, chips `full`. |
| Elevation | `shadow-{none,flat,seated,raised,lifted,overlay,focus,selected}` | Prefer the component's own elevation. |
| Measure | `max-w-{measure,report,page,app,form,headline}` | `measure` is 68ch prose. |
| Leading, tracking | `leading-{display,ui,prose,mono}`, `tracking-{display,hero,caps}` | Micro labels: `text-ui-2xs uppercase tracking-caps text-text-tertiary`. |
| Motion | `duration-{hover,state,layout,reveal}`, `ease-{standard,out,reveal}` | Reduced-motion is handled globally in `base.css`. |

Still stock, and fine: `flex`, `grid`, `grid-cols-*`, `items-*`, `justify-*`,
`w-full`, `w-1/2`, `min-w-0`, `min-h-screen`, `hidden`, `sr-only`, `font-medium`
/`font-semibold`/`font-bold`, `uppercase`, `tabular-nums`, `truncate`,
`overflow-*`, `sm:` `md:` `lg:` `xl:` `2xl:` breakpoints, state variants.

## Responsive

Mobile first, as in stock Tailwind: write the phone layout, add `sm:`, `md:`,
`lg:` (the breakpoints are not replaced). Real examples in the app:
`grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-4`,
`grid gap-3 md:grid-cols-3`. Cap width with a token (`max-w-page mx-auto` for
landing/auth/intake pages, `max-w-report` for the report, `max-w-app` for the
shell), not a stock `max-w-2xl`, which still compiles but sits off the system.
Width utilities read the spacing scale, so `w-64` does not exist: use `w-full`,
fractions, `max-w-*`, or a grid track.

## Dark mode

The theme switch is `[data-theme='dark']` on `<html>` (`tokens.css`, set by
`ThemeProvider`). Light is the default. **Do not write `dark:` variants.** The
Tailwind config sets no `darkMode`, so Tailwind's default (`media`, the OS
setting) would apply and disagree with the user's saved choice; and the role
tokens already change value under `[data-theme='dark']`. One class is right in
both themes: `bg-surface-seated text-text-body border border-line-hairline`.
Values that cannot be tokens (a score's interpolated colour) use
`rampVars(score)` and the `avp-ramp` class, not a hand-picked colour.

**The report stays paper in both themes.** Anything rendered inside
`.avp-report` (`ReportPage`, `ReportHeader`, `ScoreBlock`, `Beat`, `Prose`,
`Evidence`, `FixList`) uses the paper token values whatever the app theme, and
must not name the Working-screen accent layer (`bench-*`); the guard is
`packages/design-system/src/tokens/reportIsolation.test.ts`. Do not add a theme
branch or a `dark:` class inside the report.

## Colour rules that are easy to break

- `vis-*` is **fill only**. Never `text-vis-*`; pick text on a ramp fill with
  `onVisibility(score)`.
- Semantic colours (`success`, `warn`, `danger`, `info`) describe system state,
  never a score value.
- Competitors never take a ramp colour (`competitor-1..5`, separated by
  lightness and pattern). The client is always `beacon`.
- Do not hand-paint a primary action: `<Button variant="primary">` carries the
  accent gradient and its `on-accent` text.

## Accessibility habits

- Semantic elements first (`button`, `a`, `label`, `table`, headings in order).
- Focus is drawn globally (`base.css` `:focus-visible` uses `--avp-focus-ring`).
  Do not remove it (`outline-none` alone) and do not reach for `ring`: with the
  stock palette gone, a bare `ring` falls back to Tailwind's stock blue
  (`#3b82f6`, `tailwindcss/stubs/config.full.js:762`). Use `shadow-focus` if a
  custom element needs it.
- Use `TextField`/`SelectField`: label bound by id, `aria-describedby` for hint
  and error, `aria-invalid`, error text `role="alert"`. Do not rebuild them.
- Icons are `lucide-react` only, decorative ones with `aria-hidden`, icon-only
  buttons with an `aria-label`. No other icon set or illustration kit.
- State is never colour alone: pair with text, a pattern or an icon (reports
  are printed in greyscale).
- Motion: leave reduced-motion handling to the global rule; do not add
  `animate-*` outside the design system's `Reveal`.

## Forms: never log form values

Do not pass a form's values to `console.log`, an error reporter or the API
client's log. A login or reset form's values contain a password and an email
(the project rule: no secret, token or email in a log). Log an event name or an
outcome, never the payload. The web app logs nothing today; keep it that way.

## Verification

No build catches a stock class. Tailwind 3.4 emits nothing for `bg-white
text-gray-500 text-sm shadow-md`, `next build` passes, and every suite passes.
What the guards DO cover (`packages/design-system/src/tokens/reportIsolation.test.ts`,
about lines 218-338, over `apps/web/src`, `components` and `styleguide`):
numeric spacing suffixes off the scale (`p-7`, `w-40`), `-semantic-` colour
names, unknown suffixes in the `line-`, `surface-`, `text-` (grouped), `on-`
colour groups, and unknown `rounded-*` suffixes. Nothing else. Extending that
guard is the founder's call, not part of this skill.

So check the rest yourself, read-only, over the files you changed:

```bash
rg -n -e '\b(bg|text|border|ring|fill|stroke|divide)-(white|black|slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)\b' \
   -e '\btext-(xs|sm|base|lg|[2-9]?xl)\b' -e '\bshadow-(sm|md|lg|xl|2xl)\b' \
   -e '\brounded\b(?!-)' -e '\bfont-(sans|serif)\b' <changed files>
```

(`rounded\b(?!-)` needs `rg --pcre2`; drop it if unavailable and search for
bare `rounded ` by eye.) Then look at it in a browser at both themes; an absent
style is only visible on screen.

Then delegate to the test-runner agent: the FULL design-system vitest suite
(`corepack pnpm --filter @avp/design-system test`) and web vitest
(`corepack pnpm --filter @avp/web test`), each to a log file with its own exit
code, never piped, then `python3 infra/ci/assert_count.py design-system-vitest
<log>` and `web-vitest <log>`, plus both typechecks. Run the full
design-system suite, not one file: `tokens.test.ts` and the class guards live
apart from the component you touched. Anything a user can see then goes to the
ui-reviewer agent, and AccessLint (`accessibility-scan` / `accessibility-audit`).

A new runtime dependency or outbound host needs a class in
`apps/web/src/components/legal/providers.test.ts` and a line on `/privacy`.
`apps/api/scripts/license_audit.py` walks only the API's Python environment; no
CI check covers JS licences, so read a JS package's `license` field in its
`package.json` by hand. This skill adds none.
