# apps/web — Next.js + React (TypeScript)

Customer-facing app: intake, scan setup, competitor override, the AI Visibility
Report, and pitch export.

**Stack** (product-spec.md §5.1): Next.js + React + Tailwind, with Tailwind
**customized to the proprietary design tokens** via the preset exported from
`@avp/design-system/tailwind-preset`. Recharts for conventional charts, styled
through `@avp/design-system` chart theme — never Recharts' default theme.

Every customer-facing surface **must** import from `@avp/design-system`.
Ad hoc Tailwind defaults on customer-facing screens are not permitted
(see `/docs/ip-safety.md`, constraint 2).

Scaffolded in Epic 1. Screen work begins at Epic 2+.
