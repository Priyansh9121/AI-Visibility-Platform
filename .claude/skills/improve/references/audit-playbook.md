# Audit Playbook (AVP)

What to look for, per category. Each subagent or direct pass gets its section plus "Finding format". A finding needs evidence: `apps/api/src/avp_api/x.py:142 issues one query per row inside a loop` is a finding; "probably has N+1 somewhere" is not. Scope subagents to packages (`apps/api`, `apps/workers`, `apps/web`, `packages/*`, `infra/`), not the root.

Settled, not findings: the five guard tests that grep source on purpose (listed in SKILL.md), decisions in `docs/north-star.md`, open items already under "Found, not fixed" at the end of `docs/build-log.md`. A stale decision doc (code drifted from it) is a finding.

## 1. Correctness / bugs

- Swallowed exceptions, bare `except`, missing error states in UI.
- Async hazards in FastAPI/asyncpg code: blocking calls inside `async def`, unawaited coroutines, sessions shared across tasks.
- Concurrency and idempotency: check-then-act without a lock or transaction, retried Celery tasks (`apps/workers/src/avp_workers/celery_app.py`: retry delay, max retries, backoff, jitter) that are not idempotent, webhook replay (`POST /billing/webhook`, `stripe_webhook_events`).
- Null flows, unchecked indexing, status enums with a silent `default:`.
- Type escapes: `# type: ignore`, `Any`, `as`, `@ts-ignore` clusters. mypy has a ceiling in `infra/ci/floors.json`.
- Resource leaks: connections, Redis clients, Playwright pages, missing `finally`.

## 2. Security

Code evidence only; frame as defensive maintenance. Plans stay at the level of code changes, configuration and tests; no runnable misuse strings. Never copy a secret value: `file:line` plus credential type, and the fix includes rotation. By-design conventions (honouring `https_proxy`) are not findings unless the implementation adds risk.

- Credentials hardcoded, committed or logged. Redaction lives in `apps/api/src/avp_api/logs.py`; `tests/test_secrets_stay_out_of_logs.py` checks it. Email or token in a log line is a finding.
- Auth and sessions: login, sign-up, reset, change-password, Google sign-in (`POST /auth/google/pending`); rate limits (`services/rate_limit.py`); session revocation; Redis failure mode (503, not fail-open).
- Tenancy: object access by id without an agency check (IDOR); every agency-scoped route and query.
- Billing: Stripe webhook signature, idempotency, stale-event handling, checkout guards.
- Crawler (`apps/api/src/avp_api/services/crawl.py`): honest crawling only. `AVPScanBot` identifies itself and obeys robots.txt through `RobotsGate`. SSRF and redirect handling are in scope; evasion is never a fix.
- Token routes (`[token]` pages): `Referrer-Policy: no-referrer` and `X-Robots-Tag` (`apps/web/next.config.mjs`).
- Input contracts: request bodies without Pydantic validation, mass assignment, path or SQL built from request data.
- CORS with credentials, cookie flags, stack traces to clients.
- Facts-only: any column or log that stores third-party raw text (AI answer text, page HTML, competitor copy). Only booleans, counts, positions, URLs, names. `apps/api/tests/test_facts_only.py` enforces the schema side; look for what it cannot see.
- Dependencies: licences via `apps/api/scripts/license_audit.py` (no copyleft, source-available or undeclared). Do not run `pip-audit`, `npm audit` or `pnpm audit` without the founder's OK (they contact a registry); the `dependency-audit` library skill covers it. Any new outbound host or runtime dependency must be classified in `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`.

## 3. Performance

- N+1 (per-row queries, per-row Redis or Stripe calls), nested scans where a dict belongs, missing pagination, over-fetching.
- Missing indexes: claim only with schema evidence (models and `infra/db/migrations/versions/`); see `supabase-postgres-best-practices`.
- Work that belongs in Celery but runs in the request. LLM calls without a cap or cache (scan cost is real money).
- Web: heavyweight client bundles, client fetching of data available at render, waterfalls. Defer to Next 15 conventions.
- CI time: only if `.github/workflows/ci.yml` shows a concrete waste.

## 4. Test coverage

The goal is which untested code is dangerous, not a percentage. Coverage targets do not exist here; AVP gates counts (`infra/ci/floors.json` via `infra/ci/assert_count.py`).

- Critical paths: auth, billing, scoring (`docs/scoring-spec.md`; formula change bumps `FORMULA_VERSION`, history is never re-scored), crawler, the scan core loop. Zero or trivial coverage is a finding.
- High churn plus no tests: a "characterization tests first" candidate.
- Test quality: assertions that prove nothing, mocks testing the mocks, flaky patterns.
- Verification baseline: the bar is in `infra/ci/README.md` and `.github/workflows/ci.yml`; a gap between the two is a finding.

## 5. Tech debt and architecture

- Duplicated or drifted logic, layering violations, dead code, god modules, inconsistent patterns (pick the most recent convergence).
- Design-system drift in `apps/web`: raw hex, inline style colours, stock Tailwind classes, off-scale spacing. The preset replaces the theme, so these compile to nothing; the design-system suite (`packages/design-system/src/tokens/stockClasses.ts`) catches many. The report stays paper in both themes.

## 6. Dependencies and migrations

- Major-version lag with real cost (EOL, security cutoffs), deprecated APIs with removal dates, abandoned critical deps, duplicate libraries, lockfile drift (`uv lock --check` in both `apps/api` and `apps/workers`).
- Deliberately absent: psycopg, vendor LLM SDKs other than anthropic, PDF libraries. Adding one is a product and licence decision, not a plan step.
- Schema changes need a model change plus an Alembic revision; estimate blast radius.

## 7. DX and tooling

- Broken or missing scripts, wrong README steps, undocumented env vars, slow feedback loops, unstructured logs, missing request ids.
- `.claude/agents/` (explorer, implementer, scribe, security-reviewer, test-runner, ui-reviewer) and `CLAUDE.md` exist; gaps in them are a finding only with concrete cost.

## 8. Docs

Lowest priority: only where absence or staleness has a concrete cost. `docs/api-contracts.md` must match `packages/shared-types/openapi.json`; `docs/runbook.md` must match production reality (for example email is off). Do not audit legal page wording beyond factual mismatch with the code; wording is the founder's and a lawyer's.

## 9. Direction

Grounding rule: every suggestion cites repo evidence (unfinished intent, stated-but-undelivered items in `docs/`, surface asymmetries, adjacent possibilities, friction worth productising). Anchor on `docs/north-star.md` and `docs/product-spec.md`; never propose what they rejected. Strategy is the founder's; give options with trade-offs and coarse effort. Plans for these are design/spike plans. Any number in a suggestion needs a named source or the label "unverified heuristic". Marketing or UI copy never invents numbers, testimonials, logos or customers.

---

## Finding format

```markdown
### [CATEGORY-NN] Short imperative title

- **Evidence**: `path/file.py:123` - what is there. (2-5 strongest locations; "and ~N similar sites" if widespread.)
- **Impact**: what goes wrong, concretely.
- **Effort**: S (hours) / M (a day-ish) / L (multi-day), for the fix including tests.
- **Risk**: what the fix could break; LOW/MED/HIGH plus one line.
- **Confidence**: HIGH (read the code) / MED (needs verification) / LOW (smell; gets an "investigate" plan).
- **Fix sketch**: 1-3 sentences.
```

## Prioritisation

Leverage = impact / effort, discounted by confidence and fix risk. Tiebreakers: unblockers (verification baseline, characterization tests) first; HIGH-confidence security above equal non-security; prefer fixes with a clean verification story. "Not worth doing" is a valid verdict; record it in one line.
