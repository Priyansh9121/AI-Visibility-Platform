# apps/web — Next.js + React (TypeScript)

Customer-facing app: intake, scan setup, competitor override, the AI Visibility
Report, and pitch export.

**Stack** (product-spec.md §5.1): Next.js + React + Tailwind, with Tailwind
**customized to the proprietary design tokens** via the preset exported from
`@avp/design-system/tailwind-preset`. Charts are hand-drawn SVG components from
`@avp/design-system` (`ChartFrame`, `TrendChart`, `ShareBar` and others); there
is no charting library.

Every customer-facing surface **must** import from `@avp/design-system`.
Ad hoc Tailwind defaults on customer-facing screens are not permitted —
the design-system-only rule.

Scaffolded in Epic 1. Screen work begins at Epic 2+.
