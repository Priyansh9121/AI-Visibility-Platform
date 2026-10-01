# Stock Tailwind habit -> AVP class

Modified from ui-styling (Apache-2.0); see CHANGES.md.

Every right-hand class exists in `packages/design-system/src/tailwind-preset.ts`.
Left-hand classes compile to nothing (colour, spacing, font, radius, shadow are
REPLACED) unless marked **(compiles)**: those still emit CSS because the preset
extends or leaves that scale, but they are off the system. Pick by role, not by nearest look.

## Surface, text, rules

| Habit | Use |
|---|---|
| `bg-white`, `bg-gray-50` (page) | `bg-surface-ground` |
| `bg-white` (card, panel) | `bg-surface-seated`, or `<Card>` |
| `bg-gray-100` (well, sidebar, input) | `bg-surface-sunken` |
| `hover:bg-gray-100` | `hover:bg-surface-hover` |
| `bg-gray-900`, `dark:bg-gray-900` | no `dark:`; the role token above already follows `[data-theme='dark']` |
| `text-gray-900`, `text-black`, `dark:text-white` | `text-text-primary` |
| `text-gray-700` | `text-text-body` |
| `text-gray-500` | `text-text-secondary` |
| `text-gray-400` | `text-text-tertiary` |
| `border` alone | `border border-line-hairline` (a bare `border` draws `currentColor`, because the stock `gray.200` default is gone; from `tailwindcss/stubs/config.full.js:84`, not compiled here) |
| `border-gray-200` / `border-gray-300` | `border-line-hairline` / `border-line-strong` |
| `divide-gray-200` | `divide-line-hairline` |

## Colour with meaning

| Habit | Use |
|---|---|
| `bg-blue-600 text-white` (primary action) | `<Button variant="primary">` |
| `text-blue-600` (accent, the client) | `text-beacon-600`, only for the client or prospect under analysis |
| `text-red-600` / `text-green-600` / `text-yellow-600` | `text-danger` / `text-success` / `text-warn`, system state only, never a score |
| `bg-red-100 text-red-800` (tinted chip) | `<Badge tone="danger">` (tones: neutral, beacon, success, warn, danger) |
| `text-white` on a fill | `text-on-accent`, `text-on-danger`, `text-on-warn` |
| score-coloured cell | `bg-vis-{00,25,50,75,100}` as a fill only, text via `onVisibility(score)`; interpolated colour via `rampVars(score)` + class `avp-ramp` |
| competitor series | `bg-competitor-{1..5}`, never a `vis-*` colour |
| `bg-gradient-to-r` (compiles) `from-* to-*` | `bg-accent` (the one accent gradient) |

## Type

| Habit | Use |
|---|---|
| `text-xs` / `text-sm` / `text-base` / `text-lg` | `text-ui-xs` (12) / `text-ui-sm` (13) or `text-ui-base` (14) / `text-ui-md` (16) / `text-ui-lg` (18) |
| 11px caption | `text-ui-2xs` |
| `text-xl` and up (headings) | `<PageHead>` or `<CardTitle>`; else `font-display` with `text-ui-xl`; figures `text-kpi`, `text-hero` |
| `font-sans` | `font-ui` |
| `font-serif` | `font-editorial`, report only |
| `font-mono` | `font-mono` (same name, now IBM Plex Mono; for evidence) |
| `leading-relaxed` / `leading-tight` / `leading-normal` (all compile) | `leading-prose` / `leading-display` / `leading-ui` |
| `text-xs uppercase tracking-wider (compiles) text-gray-500` | `text-ui-2xs uppercase tracking-caps text-text-tertiary` |
| `max-w-prose` (compiles) | `max-w-measure` |
| `max-w-2xl` etc. (still compiles, off the system) | `max-w-form`, `max-w-page`, `max-w-report`, `max-w-app`, `max-w-headline` |

Stock and unchanged: `font-medium`, `font-semibold`, `font-bold`, `tabular-nums`
(also set on `body`).

## Spacing

Keys and px: `0.5`=2, `1`=4, `1.5`=6, `2`=8, `3`=12, `4`=16, `5`=20, `6`=24,
`8`=32, `10`=40, `14`=56, `18`=72, `24`=96, `32`=128, plus `px`, `rhythm` (8),
`beat` (72, the gap between report beats).

| Habit | Use |
|---|---|
| `p-7`, `gap-7`, `mt-9`, `mt-11` | nearest key by role: `6` or `8` |
| `p-12`, `py-12` | `10` or `14` |
| `p-16`, `py-16` | `14` or `18` |
| `py-20`, `py-48` | `24`, `32`; a section break in a report is `beat` |
| `w-64`, `h-64` | none (width and height read the same scale): `w-full`, `w-1/2`, `max-w-*`, or a grid track |

## Radius, elevation, focus

| Habit | Use |
|---|---|
| `rounded` (bare) | `rounded-lg` (buttons, inputs, nav rows) |
| `rounded-lg` on a card | `rounded-2xl`, or `<Card>` |
| `rounded-full` | `rounded-full` (chips) |
| `shadow-sm` / `shadow-md` | `shadow-seated` / `shadow-raised`, or the component's own elevation |
| `shadow-lg` / `shadow-xl` (menus, modals) | `shadow-lifted` / `shadow-overlay` |
| `ring`, `ring-2` (compiles, stock blue `#3b82f6`), `ring-blue-500`, `focus:ring` | `focus-visible:shadow-focus`, and only when the global ring is not enough. The global `:focus-visible` rule (`base.css:50`) already draws `--avp-focus-ring` as a `box-shadow`, so from reading the cascade (not rendered) any `shadow-*` utility on a button, link or input overrides it: do not put `shadow-seated` etc. on those |
| `transition duration-150 ease-in-out` (compiles) | `transition-colors duration-hover ease-standard` |
