# Build Log

Chronological record of what was built, why, key decisions, and trade-offs.
One entry per completed checklist item.

---

## 2026-08-20 — Epic 0.1 · IP-safety doc created

**Built:** `/docs/ip-safety.md`, containing the standing constraints verbatim as a
standalone normative reference, plus an operational section translating three of
them into enforceable repo rules.

**Why:** Constraint 9 requires a self-check before any UI epic is marked complete.
That check needs a fixed, quotable artifact rather than a chat scrollback.

**Key decisions:**
- The doc declares itself normative and says it wins over conflicting instructions
  until the owner overrides in writing. Without a precedence rule, a future "just
  make it look like X" instruction has no written thing to lose against.
- Added an explicit allow-list of licences for constraint 6 (MIT, Apache-2.0, BSD-*,
  ISC, 0BSD, Unlicense, CC0; OFL-1.1 for fonts) and a stop-list (GPL/AGPL/LGPL/SSPL/
  BUSL/Elastic/commons-clause/undeclared). The original constraint named the
  allowed three and the blocked two; the gap in between (ISC, OFL, unlicensed
  packages) is where an ambiguous call would actually happen.
- Made constraint 7 concrete: enumerated exactly which shapes may be persisted
  (booleans, counts, ordinal positions, URLs/domains, entity names, structural
  signals) and stated that raw text may exist only transiently inside a worker
  process. "Facts only" is not self-executing at the schema level; this is.

**Trade-offs:** The operational section is my interpretation layered on the owner's
verbatim constraints. It is kept in a clearly separate section below a horizontal
rule so the verbatim block stays unpolluted and auditable. If any operational rule
is wrong it can be changed without touching the constraints themselves.

---

## 2026-08-20 — Epic 0.2 · Repo scaffold

**Built:** pnpm workspace monorepo.

```
/apps/web /apps/api /apps/workers
/packages/design-system /packages/shared-types
/infra/db /infra/deploy
/docs
README.md package.json pnpm-workspace.yaml tsconfig.base.json .gitignore
```

Each workspace has a README stating what it is for and which epic fills it in.
`docs/api-contracts.md` and `docs/scoring-spec.md` exist as stubs from day one so
there is never a moment where "I'll add the doc later" is an option.

**Why:** the structure was specified in the brief. pnpm workspaces over npm/yarn
workspaces because the design system is consumed by `apps/web` as source (not a
built artifact) during development, and pnpm's strict node_modules layout prevents
`apps/web` from accidentally importing a transitive dependency it never declared —
which is how ad hoc styling libraries sneak onto customer-facing screens.

**Key decisions:**
- `git init` run — the brief calls this "the repo" and `.gitignore` is meaningless
  without it. No commit made.
- `tsconfig.base.json` is strict-plus: `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `verbatimModuleSyntax`. Deterministic scoring
  (Epic 5) is a hard acceptance criterion, and the cheapest place to buy
  determinism is refusing to let `undefined` move around silently.
- API conventions were fixed now, in `api-contracts.md`, before any endpoint
  exists: `/api/v1`, camelCase bodies, prefixed ULIDs, RFC 9457 problem+json
  errors, cursor pagination. Retrofitting an error format across a built API is
  expensive; picking one costs nothing today.
- No dependencies installed yet, so the constraint-6 licence ledger is empty.
  It starts on the next entry.

**Trade-offs:**
- Went with a working assumption of Next.js/React for `apps/web` and a
  React component library for the design system, because the brief asks for a
  style guide *page* rendering the components. Not yet committed in code — no
  framework dependency is installed. If the intended stack is different, say so
  before the component library is built and only the token layer survives (which
  is framework-agnostic CSS custom properties by design).

**⚠️ Blocker raised:** `/docs/product-spec.md` was created but is **empty** — the
kickoff brief pasted the *instruction* to paste sections 3, 5, 6 and 7, not the
sections. Epic 0 does not depend on them and proceeds. Epic 1 (needs §5 architecture),
Epic 4 (needs §3 core loop), Epic 5 (needs §6 formula), and every acceptance gate
(needs §7) do depend on them. The working assumptions Epic 0 was designed against
are written down at the bottom of that file for reconciliation.

---

## 2026-08-20 — Epic 0.3 · Spec landed; stack assumptions reconciled

**Built:** nothing new — this entry records a correction.

`docs/product-spec.md` now carries the real §3, §5, §6, §7. Two Epic 0
assumptions were wrong and are fixed:

- **Backend is Python (FastAPI) with Celery or Temporal (§5.1), not Node.**
  `apps/api` and `apps/workers` were removed from `pnpm-workspace.yaml`, which
  now globs `apps/web` + `packages/*` only. Their READMEs were rewritten to state
  the Python stack and that they are deliberately outside the Node workspace.
  Had this been left, Epic 1 would have run `pnpm install` across two Python
  services and produced a confusing half-broken workspace.
- **`packages/shared-types` is a TS↔Python contract (§5.2), not TS-only.** Noted
  the approach for Epic 1: FastAPI emits OpenAPI, TS types are generated from it.
  Hand-writing parallel type definitions in two languages is a correctness
  hazard and will not be used.

Frontend assumption held: Next.js + React + Tailwind + Recharts (§5.1).

**Also updated `docs/scoring-spec.md`** with the real §6 formula — Mention Rate
30 / Share of Voice 25 / Citation Strength 20 / Sentiment 15 / Technical
Foundation 10 — plus determinism rules and the defined edge cases that Epic 5's
acceptance criterion names. Two decisions recorded there that Epic 5 must honour:
zero-mention scans **exclude** Sentiment and redistribute its weight rather than
scoring it 0 (a brand with no mentions has no sentiment; scoring it zero
double-punishes the same absence), and a zero-prompt scan returns `null` with
`INSUFFICIENT_DATA` rather than 0 (an unrunnable scan must never be shown to a
client as a bad score).

**Trade-off:** `pnpm -r test` at the root now covers only the Node workspaces.
Epic 1 adds a root task runner spanning both languages.

---

## 2026-08-20 — Epic 0.4 · Design tokens

**Built:** `packages/design-system/src/tokens/{color,typography,spacing,elevation,motion}.ts`
and the mirrored `src/styles/tokens.css`, plus `src/styles/base.css`.

**Why the palette is shaped the way it is.** Every decision falls out of one
observation: this product's output is not a dashboard, it is an argument someone
makes to another person. It is read twice — by an operator running scans, and by
the prospect's decision-maker on a printed PDF. The presenting context closes
deals, so it wins ties. That produces light-first paper neutrals, an editorial
serif, a ramp that survives photocopying, and elevation from borders not blur.
It also lands where ip-safety.md #3 points, so the two constraints agree.

**Key decisions:**
- **Visibility = luminance.** The ramp runs dark/desaturated (absent) to
  light/saturated (cited), so the colour scale carries meaning instead of needing
  a legend. Three properties are asserted in tests, not assumed: monotonic
  lightness (survives greyscale print, where red/green resolves to one grey),
  warm→cool traverse (CVD-safe; ~8% of men misread red/green), and chroma rising
  with lightness (double-encoded).
- **Ramp is fill-only.** The light end cannot reach 4.5:1 on paper.
  `onVisibility()` picks the label colour by luminance so callers cannot get it
  wrong.
- **Competitors are never ramp-coloured.** Green implies endorsement, red makes
  the report a hatchet job — either way it loses the room. Neutral slate,
  separated by lightness + fill pattern, which also survives B&W and supports
  unlimited series. Enforced in one function, `seriesStyle()`.
- **Warm/cool hue hand-off in the neutrals** at `ink-400`: warm dark greys print
  muddy, cool ones read like ink.
- **Two type scales** (UI 1.125, editorial 1.25). One ratio cannot make 13 and 14
  meaningfully different *and* give headlines that carry a conference table.
- **Non-linear spacing tail**, plus `rhythm` (8) and `beat` (72) — `beat` exceeds
  any intra-beat spacing so a report's five-part structure is visible in thumbnail.
- **Elevation from borders and hard offsets.** Blurred shadows disappear in print
  and this product's artifact is a PDF. Levels 0–2 print; 3–4 are transient UI.
- **Emphasis is light, not lift** — focus rings and selected states illuminate
  rather than raise. This is the rule that makes the system feel authored, so it
  is a token, not a per-component choice.

**Trade-offs:**
- OKLCH has no fallback. All current browser targets support it; a `color-mix`
  or hex fallback would double the token surface for users we do not have. If a
  legacy target appears, the CSS layer is the single place to fix.
- Tokens exist twice, in TS and in CSS. TS is needed for computed colours
  (`visibilityAt`) and CSS for the export path where no JS runtime exists.
  `tokens.test.ts` parses the CSS and fails on drift, so the duplication cannot
  rot silently.

---

## 2026-08-20 — Epic 0.5 · Luminance Ledger (signature visualisation)

**Built:** `src/components/chart/ledgerLayout.ts` (pure geometry),
`LuminanceLedger.tsx`, `ChartFrame.tsx`, `ChartPatterns.tsx`, and 19 unit tests.

**Why it works.** Segment height tracks *weight* (points available) and the lit
fraction tracks *value* (points earned), which yields:

```
Σ lit heights = H × score/100
```

The total lit height of the column is exactly the composite score. The chart is
the number, not a picture of it. That identity is the component's correctness
condition, so geometry lives in a pure function and the identity is asserted
across five score profiles rather than trusted.

The same maths gives the "biggest gap" for free: the largest *unlit* area is the
largest recoverable point total, `weight × (100 − subscore)/100`. That ranks by
leverage — a weak-but-light dimension never outranks a mediocre heavy one — and
the chart's annotation **is** the report headline rather than something authored
separately alongside it.

**Key decisions:**
- Geometry split from rendering so the identity is testable without a DOM.
- Heaviest dimension at the bottom: light accumulates from the ground, and the
  most consequential dimension takes the most stable position.
- Deterministic tie-break on lowest index — equal gaps must always resolve the
  same way or a re-run reshuffles the client's headline.
- Empty input renders INSUFFICIENT_DATA, never zero.
- Accessibility: `role="img"` with a spoken per-dimension summary plus a
  visually-hidden data table. This survives into exported PDFs, which some
  enterprise clients audit.
- Geometry rounded to 3dp so output is byte-stable across platforms — asserted
  over 20 repeat runs, because these numbers reach PDFs clients compare monthly.

**Trade-offs:**
- The design constrains the scoring model to roughly ≤7 dimensions before
  segments get too thin to label. §6 defines exactly 5. Treating this as a
  feature: a score with 12 sub-dimensions is not explainable to a client anyway.
- Ghost columns show only each competitor's composite, not their per-dimension
  breakdown. Showing full competitor segments made the chart unreadable at 3+
  competitors and, more importantly, shifted the argument from "here is your gap"
  to "here is a league table." The report's job is the former.
- **Recharts is not yet installed.** §5.1 names it, but the Ledger is bespoke SVG
  (Recharts has no primitive for this) and nothing else in Epic 0 needs a
  conventional chart. It gets added in Epic 7 with a real chart, mounted inside
  `ChartFrame` so it inherits the accessibility contract. Adding an unused
  dependency now would put a package in the tree with no code exercising it.

---

## 2026-08-20 — Epic 0.6 · Component library + style guide

**Built:** `Button`, `Card` (+ Header/Title/Body/Footer), `Badge`,
`VisibilityBadge`, `DataTable`, `ScoreDisplay`; chart primitives; report
primitives `ReportPage`/`ReportHeader`/`Beat`/`Prose`/`Evidence`/`FixList`;
`src/styles/components.css`; `src/tailwind-preset.ts`; and the style guide at
`src/styleguide/` (Vite app, 9 sections, renders every component).

**Key decisions:**
- **The Tailwind preset replaces rather than extends** `colors`, `spacing`,
  `fontFamily`, `fontSize`, `boxShadow`, `borderRadius`. Tailwind's stock palette
  is gone, so `bg-slate-500` does not compile. ip-safety.md #2 becomes a build
  error instead of a review comment — the only version of that rule that holds up
  over a year.
- **The system's own components use plain CSS over token custom properties, not
  Tailwind.** The package must render in Next.js, in the Vite style guide, and in
  the React-PDF/WeasyPrint export path; requiring a Tailwind build in each is a
  worse trade than writing the CSS once.
- **`<Evidence>` takes `engine`, `prompt`, and `{label, value}` facts — and has no
  free-text body prop or children.** A paragraph of scraped answer text has
  nowhere to go, so ip-safety.md #7 is enforced by the prop types rather than by
  a reviewer noticing. This is the single most useful thing in the component API.
- **`BeatId` is a closed union of the five narrative beats,** and `<Beat>` numbers
  itself from `BEAT_SEQUENCE`. Inventing a sixth beat is a type error.
- **Beat headings are claims, not categories** — the eyebrow carries the
  structural label so the heading is free to argue.
- **The style guide lives inside the design-system package as its own Vite app,**
  not as a route in `apps/web`. Epic 0's acceptance needs a rendered guide before
  Epic 1 scaffolds Next.js, and this keeps the system reviewable without booting
  the product.
- **`cn()` written inline (9 lines) instead of adding `clsx`.** Every dependency
  has to clear the licence gate and earn a build-log line; not worth it here.
- **Fixtures use an invented company and invented competitors** ("Northaven
  Dental", "Competitor A/B/C") with original prompt strings. No real brand and no
  captured text appears anywhere.

**Dependency licence ledger (first entry — ip-safety.md #6).** 102 third-party
packages installed, audited programmatically:

| Licence | Count | Status |
|---|---|---|
| MIT | 88 | ✅ allowed |
| ISC | 7 | ✅ allowed |
| Apache-2.0 | 3 | ✅ allowed |
| BSD-3-Clause | 1 (`source-map-js`) | ✅ allowed |
| CC-BY-4.0 | 1 (`caniuse-lite`) | ✅ allowed — browser-support *data*, attribution-only, no copyleft |

**Zero GPL / AGPL / LGPL / SSPL / BUSL / Elastic / undeclared.** Direct
additions: react, react-dom, vite, vitest, @vitejs/plugin-react (MIT);
typescript (Apache-2.0); lucide-react (ISC).

⚠️ **One correction to the brief:** it lists "Lucide icons (MIT)". Lucide is
actually **ISC**, not MIT. Both are permissive and functionally equivalent, and
ISC is on the allow-list in `ip-safety.md`, so no action is needed — but the
record should be accurate.

**Trade-offs:**
- `components.css` is a single 522-line file rather than co-located per-component
  CSS. At this size one file is easier to audit for token compliance; if it grows
  past ~1000 lines it should split by component.
- No visual-regression testing yet. The 14 render tests assert structure and
  accessibility attributes, not appearance. Worth adding before Epic 7, when the
  report becomes a client-facing artifact.

---

## 2026-08-20 — Epic 1.1 · Python tooling: uv over Poetry

**Decision:** `uv` manages both Python services.

**Why.** Poetry is not installed on this machine and uv is, which settles a
close call — but three properties made it the right answer regardless:

1. **It manages Python versions too.** The system interpreter here is 3.14,
   which is too new for reliable Celery and asyncpg wheels. uv downloads and
   pins 3.12 per project via `.python-version`, so nobody has to install
   pyenv separately or discover the incompatibility through a build failure.
2. **Standard PEP 621 metadata.** Dependencies live under `[project]`, not a
   proprietary `[tool.poetry]` block, so migrating away later is deleting a
   lockfile rather than rewriting the manifest. This is the property that makes
   the choice low-stakes.
3. Resolution is fast enough that a full `uv sync` is not a coffee break.

**Trade-off:** uv is younger than Poetry and its lockfile format is still
moving. Mitigated by point 2 — the manifest is standard, so only `uv.lock` is
tool-specific.

**Also decided:** `apps/workers` takes a path dependency on `apps/api`
(`[tool.uv.sources]`) so both share one set of SQLAlchemy models. Two
declarations of the same schema in one repo is how they drift.

---

## 2026-08-20 — Epic 1.2 · Orchestrator: Celery, behind an abstraction

**Decision:** Celery for Phase 1. The pipeline depends on an `Orchestrator`
protocol, not on Celery directly, so Temporal remains a real option.

**Why not Temporal, given the workload fits it well.** The Phase 1 pipeline
(§5.4) is eight sequential stages with a large fan-out in the middle, where
every stage calls a slow, flaky, rate-limited, and *expensive* third-party API.
That is close to Temporal's ideal use case: durable execution means a workflow
that dies at step 7 resumes at step 7 rather than re-running the 60+ LLM and
engine calls from step 5. That is real money.

It still loses on cost of ownership right now:

- **§5.1 already mandates Redis** for queue and caching. Celery therefore adds
  *zero* new infrastructure; Temporal adds a server cluster to operate before
  the product has a single user.
- **Temporal cannot run in this environment at all** — there is no Docker on
  this machine, and Temporal's dev server is distributed as a container.
  Choosing it would have meant shipping Epic 1 unverified.
- **Most of the durability benefit is recoverable at the data layer.** §5.3
  already requires each stage's output to be persisted — `CompetitorSet`,
  `PromptSet`, `EngineResult` are durable entities, not in-flight state. Each
  stage is written to check for its own output and return it if present. A
  retried scan then skips the expensive work it already paid for. That is most
  of what durable execution buys, for the price of some idempotency discipline.

**Revisit at Epic 10** (bulk overnight batch). That is the workload where
per-workflow durability starts to earn a cluster.

**Celery configuration that is not the default,** because the defaults suit a
fast-task web workload and this is the opposite:

| Setting | Value | Why |
|---|---|---|
| `accept_content` | `["json"]` | Celery's pickle support is remote code execution for anyone who can write to the broker |
| `task_acks_late` | `True` | A worker killed mid-scan requeues rather than silently dropping the job |
| `worker_prefetch_multiplier` | `1` | The default of 4 lets one worker hoard long scans while its peers idle |
| `task_retry_jitter` | `True` | Without jitter, a 429 hits every in-flight task at once and they retry in lockstep, reproducing the burst |
| `task_soft_time_limit` | `600` | Epic 9 targets a full scan under 5 minutes; a stage past 10 is stuck, not slow |
| queue split | `scans` / `engines` / `audits` | IO-bound engine calls must not starve CPU-bound audits |

**Trade-off:** Celery Canvas chords over Redis have known sharp edges at high
concurrency. Not a factor at Phase 1 volumes, and the persistence-per-stage
design means a lost chord costs a retry, not a corrupt scan.

---

## 2026-08-20 — Epic 1.3 · Auth: httpOnly session cookies, Argon2id

**Decision:** opaque session tokens in Redis, delivered as an httpOnly cookie.
Passwords hashed with Argon2id.

**Why sessions rather than JWTs.** This is *seat-based* software. When an agency
removes a seat, that person's access has to end now — not whenever their access
token expires. A JWT cannot be revoked without a server-side blocklist consulted
on every request, and a blocklist consulted on every request is a session store
with extra steps. So: a session store, chosen deliberately rather than
defaulted into.

Everything else follows from that and is upside: `httpOnly` means XSS cannot
read the cookie (a token in `localStorage` can be exfiltrated by any script that
gets in), revocation is a `DEL`, "sign out everywhere" is a set scan that seat
management needs anyway, and there is no refresh-token dance or clock-skew class
of bug.

Only the **SHA-256 digest** of a token is stored, so a Redis dump does not hand
an attacker working cookies. SHA-256 without a work factor is correct here: the
token already carries 256 bits of entropy, so there is nothing to brute-force
and a slow KDF would only add latency to every authenticated request.

Every authenticated request **re-reads the user and agency from Postgres**.
That is the point of a server-side session — a suspended user or soft-deleted
agency loses access on their next request. Tested in `test_tenant_isolation.py`.

**Why Argon2id rather than bcrypt.** Argon2id won the Password Hashing
Competition and is memory-hard, which is what actually resists the GPU and ASIC
rigs that make bcrypt's pure-CPU cost function look cheap. bcrypt additionally
truncates input at 72 bytes, a silent correctness trap for passphrase users.
Parameters follow OWASP's recommended second option (19 MiB, t=2, p=1), and
`needs_rehash` on the login path migrates the user base transparently when the
cost is raised.

**Login is not a user-enumeration oracle.** Unknown email, wrong password, and
suspended account return an identical response, and the unknown-email path
performs a dummy Argon2 verification so response latency does not leak account
existence. The dummy hash is *generated* at the configured cost rather than
hard-coded — a literal that failed to parse would return in microseconds and
reintroduce exactly the timing signal it exists to suppress.

**Seat enforcement uses a row lock, not a count.** `SELECT ... FOR UPDATE` on
the agency row before the seat check, in the same transaction as the insert. The
naive `count then insert` is a TOCTOU race: two concurrent invitations both read
2 against a limit of 3 and both pass. `test_seats.py` runs four concurrent
transactions against a 3-seat agency and asserts exactly two succeed —
**verified to fail (4 of 4 granted) when the lock is removed**, so the test
proves the mechanism rather than merely passing.

Enforced in the service layer rather than a database trigger because seat limits
are commercial policy, not a data invariant: the rules will grow (grace seats,
trials, per-role limits) and policy that lives in a trigger is invisible to the
people who change it.

**Trade-off:** email is globally unique, so one person cannot hold seats at two
agencies with the same address. Multi-agency membership needs a join table and a
tenant picker at login — a deliberate future change rather than something to
half-build now.

---

## 2026-08-20 — Epic 1.4 · Data model and migrations

**Built:** 16 tables covering every §5.3 entity, one Alembic revision, and
`infra/db` wired to models in `apps/api` as §5.2 requires.

**A licence constraint changed the architecture.** Alembic's default template
runs migrations through a synchronous driver, which in practice means psycopg2
or psycopg3 — and **both are LGPL-3.0**, which `ip-safety.md` #6 puts on the
stop-and-ask list. Rather than request an exception for a dependency used only
by migrations, `env.py` drives Alembic through **asyncpg (Apache-2.0)**, which
the service already depends on. One driver, one licence, no exception needed.

The knock-on effect is worth recording: **there is no synchronous Postgres
driver anywhere in this project.** Celery tasks are synchronous, so any worker
touching Postgres bridges through `asyncio.run` — see
`tasks/health.py:check_datastores` for the pattern Epic 2+ follows.

**Schema decisions:**

- **Prefixed ULID text primary keys** (`scan_01J...`). Self-describing in logs,
  time-sortable so `ORDER BY id` is a valid cursor without a second index, and
  `parse(value, expected_prefix)` turns "client id passed where a scan id
  belongs" into a 400 at the edge instead of an empty result set three layers
  down. Costs ~14 bytes per row over a native uuid; worth it.
- **VARCHAR + CHECK for enums, not native Postgres ENUM.** Native enums validate
  equally well, but adding a value means `ALTER TYPE`, which interacts badly
  with migration tooling and long transactions. A CHECK constraint is ordinary
  transactional DDL.
  *This bit back:* Alembic autogenerate cannot see SQLAlchemy-generated enum
  CHECK constraints, so it proposed **dropping all 17 of them on every run**.
  Caught by the drift test, not by review. `env.py:include_object` now filters
  exactly those names, so drift detection stays trustworthy for everything else.
- **Soft delete via `deleted_at`.** An agency removing a client must not
  cascade-destroy the scan history its historical reports reference.
- **`agency_id` denormalised onto `scans`.** Every tenant-scoped query filters
  on it; carrying it avoids a join on the hottest read path.

**Scoring-spec guarantees are schema-level, enforced now** even though the
engine is Epic 5, because retrofitting them onto written rows is far harder:

| Rule | Enforcement |
|---|---|
| Decimal, not float | Every score column is `NUMERIC(5,2)`; asserted round-tripping as `Decimal` |
| Version the formula | `formula_version` NOT NULL; uniqueness is `(scan_id, formula_version)` so re-scoring INSERTS rather than overwriting — Epic 11's before/after reporting depends on this |
| No hidden inputs | `inputs_digest` fingerprints the EngineResult set, distinguishing "inputs changed" from "scoring is non-deterministic" when a client disputes a number |
| INSUFFICIENT_DATA ≠ 0 | `composite` nullable, `status` enum, and a CHECK that the two agree |
| Clamp AND raise | Range CHECKs reject an out-of-range score instead of silently storing it |

**ip-safety #7 is enforced by the schema, not by review.** `EngineResult` and
its children have no column capable of holding an engine's answer text — no
`raw_response`, `snippet`, `excerpt`, `context`, or generic JSONB payload.
`test_ip_safety.py` asserts this structurally: it fails on any forbidden column
name, any `TEXT` column, and any `VARCHAR` over 2048 in the facts-only tables.
Someone adding `raw_response` in eighteen months gets a red CI run.

Two columns were **considered and rejected** on those grounds:

- **`Citation.title`** — a page title is the publisher's words. #7 permits "URLs
  and domains that were cited", not their copy. The UI renders the domain and
  links out.
- **`Competitor.description`** — competitor marketing copy is theirs. Name,
  domain, and detection provenance are facts and are all that is stored.

`EngineResult.response_digest` (SHA-256) is the compensating design: Epic 12 can
detect that an answer *changed* without retaining what it said.

The one deliberate exception is **`Prompt.text`**, which stores full text
because we generate it. `test_ip_safety.py` asserts that exception explicitly so
it stays deliberate.

**A test caught a real schema bug:** `formula_version` was `String(16)`, but §6
anticipates per-industry tuning and `"v1-industry-dental"` is 18 characters.
Widened to 40 before the migration was finalised.

---

## 2026-08-20 — Epic 1.5 · shared-types: generated, never hand-written

**Built:** `apps/api/scripts/export_openapi.py` → `openapi.json` →
`openapi-typescript` → `src/api.gen.ts`, implementing the approach recorded in
Epic 0.3.

FastAPI is the source of truth. Hand-writing parallel type definitions in
Python and TypeScript was rejected because they drift silently and the drift
surfaces as a runtime bug in the browser; generating from one source makes a
backend field rename a **compile error in `apps/web`**.

The export runs schema generation without starting a server or touching a
database, so it works in CI and in a pre-commit hook. Output is `sort_keys`'d so
regenerating without an API change produces a zero diff — otherwise the
generated file becomes review noise and people stop reading it.

`src/index.ts` is hand-maintained but contains **only aliases and things
OpenAPI cannot express**: the RFC 9457 `ProblemDetail` shape (FastAPI does not
describe error responses), the `Page<T>` envelope, and the id-prefix table. It
never restates a generated shape.

Eight contract tests assert the conventions actually hold in the emitted schema
— `/api/v1` base path, kebab-case paths, camelCase bodies, no credential field
in any `*Out` schema, and `compositeScore` nullable so INSUFFICIENT_DATA is
representable.

**Documented and verified:** `compositeScore` crosses the wire as a
**string-encoded decimal** (`"38.35"`), not a JSON number. A JSON number is an
IEEE double, which would turn 38.35 into 38.349999999999994 — reintroducing at
the transport layer exactly the error scoring-spec rule 3 exists to prevent.
Confirmed against the running app before documenting it, and pinned by a test.

---

## 2026-08-20 — Epic 1.6 · Dependency licence ledger

Audited with `apps/api/scripts/license_audit.py`, which resolves licences from
PEP 639 `License-Expression` first, then classifiers, then the legacy field, and
exits non-zero on anything copyleft or undeclared.

**Python — 49 distributions:**

| Licence | Count |
|---|---|
| MIT / MIT License | 25 |
| BSD-3-Clause / BSD-2-Clause / BSD License | 11 |
| Apache-2.0 / Apache Software License | 3 |
| PSF-2.0, `MIT AND PSF-2.0` | 2 |
| ISC | 1 |
| MIT-0 | 1 |
| The Unlicense | 1 |
| `Apache-2.0 OR BSD-2-Clause`, `MIT OR Apache-2.0` | 2 |
| MPL-2.0 | 2 |

**Node — 24 new packages** for `openapi-typescript`: all MIT.

**Zero GPL, AGPL, LGPL, SSPL, BUSL, Elastic, or undeclared.**

⚠️ **Two flags for sign-off:**

1. **`psycopg2` / `psycopg3` are LGPL-3.0 and were NOT added.** Both are on the
   ip-safety.md stop list. Alembic was reconfigured to run on asyncpg instead —
   see Epic 1.4. No exception requested, no LGPL code in the tree.
2. **`certifi` and `pathspec` are MPL-2.0**, which is neither on the allow list
   ("MIT/Apache-2.0/BSD") nor the stop list ("GPL/AGPL/LGPL/SSPL/BUSL"). Both are
   transitive (certifi via httpx, pathspec via mypy), both used entirely
   unmodified. MPL-2.0 is *file-level* copyleft: obligations attach only to
   modified MPL files, so it does not affect proprietary distribution. Assessed
   as safe, but flagged because the constraint as written does not cover it.
   The audit script classifies MPL as REVIEW rather than auto-allowing it, so a
   human decides.

---

## 2026-08-20 — Epic 2.0 · Secret handling incident (resolved, no exposure)

The `ANTHROPIC_API_KEY` was pasted into `apps/api/.env.example` — the
**committed template** — rather than `apps/api/.env`, which is the gitignored
one. Caught on the pre-flight key check.

**No exposure.** The repository has zero commits, `.env.example` was untracked
and unstaged, and the key never entered git history. Verified before acting, so
no rotation was required.

**Resolved:** the value was moved to `apps/api/.env` (mode 600, gitignore
verified), and `.env.example` was restored to an empty placeholder.

Worth recording because the failure mode is generic: the two filenames differ
by one suffix, and the committed one is the one an editor opens by default when
the other does not exist yet. A pre-commit secret scan would catch the class —
noted as a hardening item, not built here.

---

## 2026-08-20 — Epic 2.1 · A dotenv bug Epic 1's tests could not see

Creating `apps/api/.env` for the first time broke the entire API test suite:

```
SettingsError: error parsing value for field "cors_allow_origins"
                from source "DotEnvSettingsSource"
```

pydantic-settings **JSON-decodes complex types (`list`, `dict`) read from a
dotenv file before field validators run**, so `CORS_ALLOW_ORIGINS=http://localhost:3000`
fails to parse and never reaches the `_split_origins` validator written for it.
Fixed with `Annotated[list[str], NoDecode]`.

The reason Epic 1 shipped this: every test constructs `Settings(...)` with
keyword arguments, so no test ever loaded a dotenv file. The configuration path
that production actually uses had zero coverage. A latent bug that would have
surfaced on first deploy.

---

## 2026-08-20 — Epic 2.2 · Crawl: facts out, content discarded

**Built:** `services/crawl.py` — Playwright/Chromium fetch of the homepage plus
up to three key pages (`/about`, `/services`, `/products`…), extracting text for
classification and structural signals for storage.

**The ip-safety boundary is this module** (constraint 7), so the rule is stated
in the file: raw page text lives only in `CrawlResult`, an in-memory dataclass
that is deliberately **not** a SQLAlchemy model and has no persistence path. It
is handed to the classifier and discarded when the request ends. Only
`CrawlSignals` — counts, booleans, URLs, schema.org type names — reaches the
database.

The distinction that makes this legitimate: page text is sent to a classifier
the way a person would read a page to work out what a business does. What is
kept is the conclusion, not the copy. Four tests enforce it structurally, plus
one asserting the log-safe `redacted()` view cannot leak text into structured
logs — logs are a durable store too.

**Two decisions worth recording:**

- **Images, fonts and video are blocked at the router.** They contribute
  nothing to classification and are most of the bytes; blocking roughly halves
  crawl time, which matters against a 30-second end-to-end budget.
- **Schema.org extraction takes TYPE NAMES only.** The values inside JSON-LD —
  descriptions, addresses, review text — are content and are discarded rather
  than parsed. `LocalBusiness` is a signal; the `description` field next to it
  is the publisher's copy.

**Two bugs found by running it against real sites:**

1. **`tldextract` fetches the Public Suffix List over the network on first use**
   and caches it to disk — a network call on the request path that fails closed
   in a sandbox and makes domain parsing differ between environments. Pinned to
   the bundled snapshot with `suffix_list_urls=()`.
2. **PSL *private* domains were excluded** (tldextract's default), so
   `practice.github.io` resolved to `github.io` and `myshop.myshopify.com` to
   `myshopify.com`. Every GitHub Pages or Shopify prospect would have collapsed
   to one domain and collided on the `(agency_id, domain)` unique key — the
   second would be rejected as a duplicate of the first. Agencies prospect small
   businesses, so hosted-platform domains are common, not edge cases. Fixed with
   `include_psl_private_domains=True`.

**A third finding from the live run:** `patagonia.com` answers our crawler with
a genuine HTTP 404 (bot or geo blocking). The crawl correctly refused, but
reported `FETCH_FAILED`, which conflates "answered with an error" and "did not
resolve at all" — different operational problems. Now reports `HTTP_404` and
records the status.

---

## 2026-08-20 — Epic 2.3 · Classification: prompt, threshold, and the refusal to guess

**Built:** `services/classify.py` — `claude-opus-5` via the Anthropic SDK's
structured-output helper (`messages.parse` with a Pydantic schema).

### Prompt design

The system prompt is short and states the **cost asymmetry** first: *"Your
output is the input to a competitive-analysis pipeline, so a confident wrong
answer is far more costly than an admitted uncertainty."* Everything else
follows from that framing rather than from a list of rules.

Specific constraints, each present because of a failure it prevents:

- **"a short, lower-case noun phrase a practitioner would recognise"** — without
  it, models return marketing categories ("innovative wellness solutions") or
  full sentences, neither of which clusters for the per-industry weight tuning
  §6 defers.
- **"Never infer the industry from the domain name alone"** — `smiledental.com`
  behind a login wall should be low confidence, not a confident "dental".
- **"A holding page for a conglomerate is a low-confidence input, not a
  high-confidence 'conglomerate'"** — a concrete anchor for the calibration
  instruction, which on its own is too abstract to change behaviour.
- **"Do not quote the page in `rationale`"** — keeps model output on the right
  side of the facts-only rule by construction.

Schema field descriptions carry the rest, since with structured outputs they are
part of the prompt.

**Effort `low`, model `claude-opus-5`.** Classification from page text is a
simple extraction task; low effort keeps the call inside the budget the crawl
has already spent 2–5 seconds of. The model tier is *not* economised on:
classification quality determines the input quality of three downstream epics.

### Confidence: label plus number, threshold in one place

Per the approved decision, `industry_confidence` stays a coarse label
(`String(16)`) and a nullable `industry_confidence_score` (`NUMERIC(4,3)`) sits
alongside it. The label leads because an LLM's self-reported confidence is not
calibrated, and a label is honest about that where a bare number implies
precision it does not have. The numeric column exists so a future calibrated
threshold needs no migration; nothing reads it yet.

**The model reports; this module decides.** Asking an LLM "are you sure enough?"
makes the threshold invisible and unauditable. `CONFIDENCE_THRESHOLD = 0.70`
lives in one constant, is unit-tested at its exact boundary (0.699 → ambiguous,
0.700 → classified), and is trivially tunable once there is data to calibrate
against. 0.70 is a starting point, not a measured value — it should be revisited
after the first few hundred real classifications.

### Ambiguity stores NULL, never a guess

Confirmed decision, implemented at three layers so it cannot be bypassed:

1. `decide()` withholds `industry` below the threshold
2. `apply_outcome()` clears `industry` for any non-`classified` status
3. `ck_clients_industry_matches_classification_status` rejects the row

The reasoning is the same as Score's `INSUFFICIENT_DATA`: a wrong industry
silently poisons Epic 3's competitor detection and Epic 4's prompt generation,
and **neither has any way to detect that its input was wrong.** A visible "we
could not tell" is recoverable; a plausible wrong label is not.

Brand name and confidence are still kept on an ambiguous result — they are
useful, and they were not what we were unsure about. Only `industry` is withheld.

### Fallback ladder

Every failure path returns a reason code rather than raising, because a site
that cannot be classified is a normal outcome for a tool pointed at an arbitrary
URL. Guards run before the model call (`FETCH_FAILED`, `HTTP_*`,
`INSUFFICIENT_CONTENT` for pages under 40 words — a parked domain must not yield
a confident hallucination), then provider errors map to distinct codes.

`PROVIDER_QUOTA_EXHAUSTED` is split out from `PROVIDER_BAD_REQUEST`: the API
returns **400** for an exhausted credit balance, and an operator reading "bad
request" would go hunting for a malformed payload when the fix is billing.
Matching on the message is the only available signal — type and status are
identical.

**The model's `rationale` is never persisted and never returned.** It exists for
debug logging while tuning the prompt. Asserted by tests against the ORM model
and every `*Out` schema, in Python and again in the OpenAPI contract tests.

---

## 2026-08-20 — Epic 2.4 · Intake screen, the first customer-facing surface

**Built:** `apps/web` (Next.js 15 + React 19), the intake form, a minimal
in-progress state, and a result panel. Plus `TextField` in the design system.

**`TextField` went into `@avp/design-system`, not `apps/web`.** ip-safety.md #2
prohibits ad hoc styling on customer-facing screens, and a form input styled
locally would be exactly that. It wires up its own accessibility — real
`<label>`, `aria-describedby` for hint and error, `aria-invalid`, and
`role="alert"` on the error so a screen reader hears a validation failure it did
not cause.

**The Tailwind preset makes the constraint a build error.** It *replaces*
Tailwind's colour, spacing, font and shadow scales rather than extending them,
so `bg-slate-500` does not compile in `apps/web`. The only local styling is
layout utilities that are themselves generated from the design tokens.

**Client-side validation is deliberately thin** — it catches "you typed
nothing" and nothing else. The server owns the real rule (`parse_domain`,
resolving against the Public Suffix List); duplicating it in the browser creates
two definitions of "valid" that drift, and the browser's copy is the one that
gets stale.

**Three outcomes, three treatments.** The result panel renders `classified`,
`ambiguous`, and `unclassifiable` differently, and never shows a null industry
as an empty field or a dash that reads like data. The ambiguous copy explains
*why* nothing was stored — "rather than guess, we have left it unset" — because
an unexplained blank looks like a bug rather than a decision.

**The in-progress state names the actual steps** rather than showing an
indeterminate spinner, and has no progress bar: we cannot measure real progress,
and a fake one is a lie the user eventually notices.

**Two build-integration decisions:**

- `next.config.mjs` sets `resolve.extensionAlias` so webpack maps the design
  system's ESM-correct `./Card.js` specifiers to `.tsx`. The alternative —
  dropping extensions from the design system's imports — would break it under
  plain Node ESM, so the fix belongs in the consumer.
- `'use client'` was added to the three hook-using components
  (`LuminanceLedger`, `ScoreDisplay`, `TextField`) rather than to the package.
  Card, Badge, Table and the report primitives stay server-renderable, which
  Epic 7's server-side PDF render depends on.

---

## 2026-08-20 — Epic 2.5 · Dependency licence ledger

Added: `anthropic` (MIT), `playwright` (Apache-2.0), `tldextract` (BSD-3-Clause),
plus transitive dependencies. Node: `next`, `tailwindcss`, `autoprefixer`,
`postcss` and their trees — all MIT.

Python distributions: **49 → 62**. Audit result unchanged: **PASS**, no
copyleft, source-available, or undeclared licences, and no new REVIEW items
beyond the two MPL-2.0 transitives (`certifi`, `pathspec`) already flagged in
Epic 1.6 and still awaiting a call.

---

## 2026-08-20 — Epic 2.6 · Live verification, and two prompt-tuning findings

**Ran `scripts/verify_intake.py` for real** — real Playwright crawls, real
`claude-opus-5` calls, five genuinely different businesses. Nothing mocked.

**Mechanical result: 5/5 classified, 5/5 within the 30-second budget**
(slowest 17.7s, allbirds.com). §7's acceptance criterion is met.

| Site | Label returned | Verdict |
|---|---|---|
| anthropic.com | `artificial intelligence research and products` | ✅ correct |
| basecamp.com | `b2b saas` | ⚠️ **too generic** |
| allbirds.com | `footwear brand (dtc e-commerce)` | ✅ correct, format drift |
| stripe.com | `payments technology` | ✅ correct |
| ycombinator.com | `venture capital` | ⚠️ **loses the distinguishing feature** |

### Finding 1 — the model generalises when writing the label

Basecamp and YC are wrong in the *same* way, and the rationale field proves it
is not a knowledge gap. The model wrote:

- Basecamp: *"a subscription web-based **project management and team
  communication tool**"* → label `b2b saas`
- YC: *"a three-month batch program investing seed capital in startups … **which
  is a startup accelerator/seed fund**"* → label `venture capital`

It identified the specific business correctly, then wrote a broader category
into the label. This matters downstream rather than cosmetically: `b2b saas`
would seed Epic 3 with keywords matching Salesforce, Datadog and Gusto equally,
and `venture capital` would surface a16z and Sequoia as YC's competitors instead
of Techstars and 500 Global. A bad competitor set then feeds Epic 4's prompts and
Epic 5's Share of Voice, and nothing downstream can detect the original error.

**Probable cause is in our prompt, not the model.** The instruction says "a
short, lower-case noun phrase a practitioner would recognise" and offers
`"b2b logistics software"` as an example — which *is itself* a
business-model-plus-vertical construction, modelling the exact generalisation we
do not want. Candidate fixes, untested:

1. Replace the exemplar with a specific one (`"project management software"`,
   not `"b2b logistics software"`).
2. Add an explicit negative: *"Name what the business sells, not its business
   model. 'b2b saas', 'e-commerce', 'marketplace' and 'technology company' are
   never acceptable answers."*
3. Ask for the rationale **before** the label in field order — the rationale is
   consistently more specific than the label, so having the model commit to it
   first may anchor the label to it.

### Finding 2 — confidence is not calibrated (the more serious one)

Scores across five sites: **0.97, 0.97, 0.97, 0.97, 0.96.** Effectively one
value, despite the prompt explicitly asking for calibration and saying "use the
full range".

This undermines the `CONFIDENCE_THRESHOLD = 0.70` design. The whole
ambiguity-detection mechanism assumes the score is informative; if a genuinely
vague site also scores ~0.95, the threshold never fires and every site is
recorded as `classified` — including the ones we specifically built the
AMBIGUOUS path to catch.

**The AMBIGUOUS path is currently unverified against real data.** It is well
unit-tested at the boundary, but no real site has come near the threshold, so
there is no evidence it fires when it should. Until that is fixed, the guarantee
"we do not store guesses" holds in code but is unproven in practice.

Candidate approaches, untested: score a deliberately ambiguous corpus (holding
pages, multi-line-of-business conglomerates, parked domains) to see whether the
distribution separates at all; or drop self-reported confidence in favour of a
measurable signal — agreement across two independent calls, or presence of
corroborating `LocalBusiness`/`Organization` schema.

**Neither finding is fixed here.** Both are prompt/scoring-design work, and
tuning against five data points would be overfitting. Recorded as the first item
to address before Epic 3 depends on these labels.

### Also observed

- **allbirds.com took 13.8s to crawl** (3 pages) versus 2–3s for the others —
  heavy JS. Still well inside budget, but it is the current worst case and the
  budget is 30s.
- **Format drift:** `footwear brand (dtc e-commerce)` crams two facets into one
  field using a parenthetical. That is what `niche` is for. Worth tightening
  when the label prompt is revised.

---

## 2026-08-20 — Epic 2.7 · Guard against secrets in the committed template

The API key was pasted into `apps/api/.env.example` — the **committed**
template — rather than `apps/api/.env`, **twice**, in consecutive sessions.
Neither reached git (the repo has no commits and the file was untracked), but
catching it by inspection both times is luck, not process.

**Added `tests/test_env_template.py`** (8 tests): every secret-bearing key must
be empty in the template, no credential-shaped string may appear anywhere in it
(`sk-ant-…`, `sk-…`, `AIza…`, `pplx-…`) regardless of which key it sits under,
and `APP_SECRET` must remain the recognisable `dev-only…` placeholder that
`config.py` refuses to boot with in a deployed environment.

The failure mode is generic and worth naming: the two filenames differ by one
suffix, and an editor opens the committed one by default when the gitignored one
does not exist yet. A pre-commit hook running this test would close it fully —
noted as a hardening item.

---

## 2026-08-20 — Epic 2.8 · Prompt tuning for Finding 1 (label over-generalisation)

**Scope:** Finding 1 only. Finding 2 (confidence calibration) deliberately
untouched — it needs an adversarial corpus, not more happy-path sites, and is
recorded as open.

### Method

`scripts/tune_prompt.py`. Two design choices that make the result mean something:

- **The crawl is held constant.** Each site is fetched once and the same
  in-memory `CrawlResult` feeds every variant. Re-crawling between variants
  would let page changes and different secondary-page selection move the label,
  and a difference could not be attributed to the prompt.
- **Crawls are never written to disk** — in-memory for the process lifetime
  only. A "just for testing" cache of page text is still a durable store, and
  ip-safety.md #7 does not carve out an exception for test fixtures.

`classify()` gained an optional `system_prompt` override for the harness.
Production always passes `None`; an override on a request path would mean a
scan was classified under a prompt that `classifier_model` does not record.

**Corpus: 9 sites.** The original five, plus four chosen because the
generalise-to-business-model failure would be unmistakable: two niche SaaS
tools (SavvyCal, Help Scout), one service business (Roto-Rooter), one
single-product retailer (Ooni).

### Variant B — replace the misleading exemplar (one variable)

The prompt offered `"b2b logistics software"` as an example — itself a
business-model-plus-vertical construction, modelling the exact generalisation
the label should avoid. Replaced with `"warehouse management software"`.

**Result: 1 of 9 labels changed, 0 failures fixed.** The one change
(`footwear brand / dtc ecommerce` → `footwear brand`) was a format tidy.
Basecamp, SavvyCal and Help Scout all still returned `b2b saas`; YC still
returned `venture capital`.

Worth recording as a negative result: the misleading exemplar was a real flaw,
but fixing it changed nothing measurable. Had this been the only change, it
would have been easy to declare victory on the format tidy and miss that the
failure mode was untouched.

### Variant C — B plus an explicit negative (second variable)

Added:

> Name what the business SELLS or DOES, never its business model or delivery
> channel. "b2b saas", "saas", "e-commerce", "marketplace", "technology
> company", "home services" and "consumer goods" are never acceptable answers —
> if one is your first instinct, go one level more specific and name the actual
> product or service.

**Result: all four failure-mode cases fixed, stable across two runs.**

| Site | Before (A) | After (C) | Verdict |
|---|---|---|---|
| basecamp.com | `b2b saas` | `project management software` | ✅ fixed |
| savvycal.com | `b2b saas` | `meeting scheduling software` | ✅ fixed |
| helpscout.com | `b2b saas` | `customer support software` | ✅ fixed |
| ycombinator.com | `venture capital` | `startup accelerator and venture capital` | ✅ fixed |
| ooni.com | `consumer cooking appliance brand` | `pizza oven and kitchen appliance manufacturer` / `cooking appliance manufacturer` | ⚠️ variable |
| roto-rooter.com | `plumbing services` | `plumbing services` | ✅ already correct |
| anthropic.com | `…research and products` | `…research and development` | ✅ both correct |
| stripe.com | `payments technology` | `payment processing software` | ✅ both correct |
| allbirds.com | `footwear brand (dtc e-commerce)` | `footwear brand` | ✅ format tidied |

Adopted C. The comment above `SYSTEM_PROMPT` records why the negative is
load-bearing, so it does not get "simplified" back out.

### Run-to-run variance is real, and worth knowing

Variant A produced different labels for allbirds and Ooni across two runs
(`footwear brand / dtc ecommerce` vs `footwear brand (dtc e-commerce)`;
`consumer cooking appliance brand` vs `consumer kitchen appliance brand`).
Single-run A/B comparisons therefore carry noise, which is why every conclusion
above rests on a repeat run.

The four `b2b saas` / `venture capital` failures were stable across both runs,
and their fixes were stable across both runs. That is what makes the result
trustworthy rather than a lucky draw.

### Still imperfect

- **Ooni is variable under C** — sometimes `pizza oven and kitchen appliance
  manufacturer`, sometimes `cooking appliance manufacturer`. Not the business-
  model failure (it names a product category either way), but one level broader
  than ideal on some runs.
- **Some labels drifted sideways.** Stripe moved from `payments technology` to
  `payment processing software`; arguably slightly narrow for a company that
  also does issuing, billing and treasury. Anthropic moved from `research and
  products` to `research and development`. Both remain correct; neither is
  clearly better than before.
- **Finding 2 remains open and untouched.** Confidence scores in the post-fix
  production run: 0.95, 0.97, 0.97, 0.96, 0.96 — still clustered, still
  uninformative, so the AMBIGUOUS threshold still never fires on real sites.

---

## 2026-08-21 — Epic 3.0 · Local dev services, and a test-design flaw

Two things broke before Epic 3 could start, both worth recording because both
were self-inflicted.

**The throwaway Postgres cluster was gone.** Epic 1 put it in the session
scratchpad, which OS cleanup deletes. The README said the suite "needs a
Postgres and a Redis" but never said how to create either, so a fresh clone
could not run the tests at all. Added `infra/db/scripts/dev_cluster.sh`
(`up|down|destroy|status`) which keeps its data in gitignored `.devdata/`,
starts Postgres on 55433 so it cannot collide with a system install, and starts
Redis only if nothing already answers on 6379 — a developer's own Redis is never
displaced, and `down` leaves it alone.

Finding a usable Postgres turned out to be the fiddly part: `command -v initdb`
resolves to Homebrew's **libpq**, which ships client tools and no server, so
initdb failed with *"program 'postgres' is needed by initdb but was not found"*.
The script now looks for a directory containing every required binary including
`postgres`, rather than resolving each one independently.

**A secret-scanning test failed because a database was down.** `_clean_state` in
`tests/conftest.py` was `autouse=True` and declared `engine` as a parameter, so
every test in the suite connected to Postgres and Redis — including
`test_env_template.py`, which only reads a file. With the cluster down, a test
that checks for committed API keys reported `ConnectionRefusedError`.

Fixed by resolving the datastore fixtures **lazily**, and only when the test
actually requested one. `test_env_template.py` now passes in 0.01s with the
database pointed at a dead port. A test that fails for a reason unrelated to
what it asserts is worse than no test: it trains you to ignore the failure.

---

## 2026-08-21 — Epic 3.1 · SerpApi integration

**Built:** `services/serp.py`.

**No SerpApi SDK.** Their API is one authenticated GET returning JSON, and
`httpx` was already vetted (BSD-3-Clause). The official `google-search-results`
package would have added a dependency, a licence to audit, and a sync-only call
style inside an async service — for a single HTTP request. `httpx` was promoted
from a dev dependency to a runtime one; no new packages, so the licence audit is
unchanged at 62 distributions, PASS.

**Two things the module must never do**, both enforced by a test:

1. **Never log `response.text` or `response.url`.** The SerpApi request URL
   carries the API key as a query parameter, and their error bodies sometimes
   echo the request. `test_serp_error_paths_never_log_the_request_url` asserts
   this by source inspection, because the alternative is discovering it in a
   production log.
2. **Never persist titles or snippets.** Result titles are publisher copy. They
   live in `SerpResult.titles` in memory, and `redacted()` — the log-safe view —
   omits them.

**The publisher exclusion list is a heuristic, and knowingly incomplete.** It
holds ~70 registrable domains across encyclopaedias, forums, review aggregators,
directories, marketplaces, publishers and job boards. It is biased toward false
negatives on purpose: wrongly excluding a real rival costs one slot in a set an
operator can edit, while wrongly including Wikipedia as a competitor makes the
whole report look unserious to the client it is shown to.

---

## 2026-08-21 — Epic 3.2 · Co-citation discovery, facts only

**Built:** `services/cocitation.py`.

The signal: when a buyer asks an AI assistant a purchase question, which brands
does it name? Anything named alongside — or instead of — the subject is a
competitor by the definition that matters to this product.

**How it stays facts-only.** The structured-output schema is the enforcement
point, not a review convention:

```
CoCitedBrand    = {name, domain}
CoCitationAnswer = {brands[], subject_named}
```

There is **no field capable of holding answer text**. The engine's prose is
never stored, never returned, never rendered — because the contract gives it
nowhere to go. `test_cocitation_schema_cannot_carry_answer_text` asserts the
field sets exactly, so adding a `snippet` field later fails the suite.

Model-returned domains are normalised through the Public Suffix List before use,
since a model may return `www.Stripe.com/` where SERP returns `stripe.com` — and
the two must compare equal or corroboration silently never fires.

**Scope boundary.** This is deliberately *not* Epic 4's engine runner. Epic 4
executes a generated prompt set across multiple real engines and parses
mentions, positions and citations from raw responses. Here one model answers one
structured question per seed prompt, purely to discover who the rivals are.
Keeping them separate meant Epic 3 did not have to wait on the engine
abstraction, and Epic 4 can replace this signal without touching ranking.

---

## 2026-08-21 — Epic 3.3 · Ranking: corroboration, not industry match

**Built:** `services/competitors.py`.

### How unreliable industry classification is handled

This is the part the brief specifically asked about, and it shaped three
decisions.

**1. No industry-keyed query template dictionary.** Query and prompt shapes are
generic and parameterised by whatever string `Client.industry` holds. A dict
keyed on industry would launder a bad classification into a confident-looking
competitor set: every returned rival would "fit" the wrong industry, and nothing
downstream could tell. A test (`test_no_industry_keyed_template_dictionary_exists`)
guards against reintroduction.

**2. Brand-anchored queries always run, and never depend on the
classification.** `"<brand> alternatives"`, `"<brand> competitors"`,
`"<brand> vs"`, and the equivalent seed prompts, work with no industry at all.
Industry-seeded queries are added on top when a label exists. The asymmetry is
the point: **a misclassification degrades recall rather than corrupting the
result.** Brand queries are also listed first, so they survive any truncation.

**3. Ranking is by cross-signal corroboration, not industry fit.** Nothing in
the ranking asks "does this look like a $INDUSTRY company?". A mis-classified
client therefore produces a **visibly incoherent** set an operator notices and
corrects, rather than a plausible wrong one they accept.

`CompetitorSet.used_industry_seed` records whether a label was involved, so when
an operator reports a bad set, "was this seeded from a bad industry?" is
answerable without re-running.

### Dedup

Domain-first, name-second. A co-citation hit carrying a domain merges with a
SERP hit on the same domain; one without merges on a slugified name compared
against the SERP domain's label (`Front` ↔ `front.com`). Legal suffixes are
stripped so `Zendesk Inc.` and `Zendesk` are one rival — with a guard so `Coco`
does not become `Co`.

### Scoring

Each signal is normalised against **its own maximum** before the two combine.
Six SERP queries return up to sixty hits and four seed prompts up to forty; that
difference is a configuration artefact, and without normalisation it would decide
the ranking. Position weight is `1/sqrt(rank)`. A candidate both signals surfaced
gets a 1.6x multiplier — the single largest term, because agreement between two
methods that fail differently is far stronger evidence than volume within either.

Ordering is fully deterministic (score, then corroborated, then mentions, then
name), so two reports of the same client never show competitors in swapped
order.

### detection_confidence

The share of the returned set that both signals surfaced independently.

**Null when only one signal ran** — not zero. Zero asserts "two signals looked
and agreed on nothing"; null says agreement was never measurable. Epic 5 needs
that distinction before presenting a Share of Voice comparison as authoritative.
Same discipline as Score's `INSUFFICIENT_DATA`.

Deliberately **not** a measure of whether the competitors are correct — nothing
here can know that. It measures agreement, which is the only thing observed.

### Two SQLAlchemy bugs worth remembering

Both produced *silently empty* competitor sets rather than errors:

1. **Lazy load on a new `CompetitorSet`.** After `flush()`, accessing
   `.competitors` triggered a lazy load and raised `MissingGreenlet` under the
   async session. Fixed by passing `competitors=[]` at construction, which marks
   the collection loaded.
2. **`delete-orphan` ate the new rows.** The relationship cascades
   `delete-orphan`; assigning `competitor_set.competitors = manual` and then
   attaching new `Competitor` rows via `session.add()` with only a foreign key
   made them orphans of the reassigned collection, and flush deleted them.
   Fixed by appending through the relationship. The same bug existed in the PUT
   handler.

---

## 2026-08-21 — Epic 3.4 · Live verification: 88% precision, and one clear finding

Ran `scripts/verify_competitors.py` against **10 real businesses across
different industries** — real SerpApi searches, real model calls, nothing mocked.

**Automated score: 76% (38/50), 8/10 URLs at ≥80%.** Below the bar.

**Audited score: 88% (44/50), 9/10 URLs at ≥80%.** Above it.

The gap is entirely my reference lists being incomplete, which the script warns
about in its own output. Six flagged candidates were real competitors I had not
listed: ConnectPay (payments), Pala Pizza and Forno Bravo (pizza ovens), Zencal
(scheduling), Airwallex (cross-border payments), Shorthand (publishing). Six were
genuine false positives: The Digital Project Manager and Serious Eats
(publications), Famous Footwear and Buckman's (multi-brand retailers, channels
rather than rivals), Reedsy and MindStir Media (book publishing — a different
market from Ghost's).

Per-row reasoning is in the session transcript so the calls can be disputed;
the automated number is left deliberately un-tuned as a conservative lower bound,
because editing the reference lists after seeing results would make the
instrument useless.

### The finding: SERP-only candidates are the entire error budget

| Detection source | Correct | Precision |
|---|---|---|
| `both` | 22/22 | **100%** |
| `co_citation` | 16/16 | **100%** |
| `serp` only | 5/11 | **45%** |

**Every single false positive was SERP-only.** Not one corroborated or
co-citation-sourced candidate was wrong across fifty rows.

The mechanism is explicable rather than coincidental: SERP returns whatever
*ranks* for a query, which includes listicles, review blogs and adjacent-market
pages. A model naming a brand is a stronger assertion — it is claiming the brand
is an option a buyer would consider, not merely a page that mentions the topic.

This suggests a targeted fix — require a SERP-only candidate to appear in **two
or more distinct queries** before it can take a top-5 slot, or down-weight
single-query SERP hits sharply. **Not implemented.** With one 10-URL run behind
it, tuning the ranking now risks fitting the constant to the sample, and the same
discipline was applied to Epic 2's Finding 1. Recorded for a decision.

### ghost.org is a Finding 2 consequence, visible in the wild

Ghost scored worst (3/5) and the reason is instructive: its industry label,
`publishing platform`, is genuinely ambiguous — it pulled in Reedsy and MindStir
Media, which serve *book* publishing rather than newsletters and blogs.

This is precisely the failure mode the "no industry-keyed templates" decision
anticipated, and the design behaved as intended: the bad seed produced a
**visibly odd set** rather than a plausible wrong one. The two wrong rivals are
obviously off to anyone who knows the market, and both are one `PUT` away from
being corrected. Had ranking been keyed on industry match, they would have looked
like a coherent answer.

Finding 2 (confidence calibration) remains open and untouched.

---

## 2026-08-21 — Epic 3.5 · SERP-only gating: implemented, and it largely did not work

**The fix:** an uncorroborated candidate must appear in at least
`MIN_SERP_QUERIES_FOR_UNCORROBORATED = 2` **distinct SERP queries** before it
may occupy a returned rank. Corroborated and co-citation-sourced candidates are
exempt — both measured 100% precision in Epic 3.4.

Threshold chosen as **two** because it is the smallest value that expresses
"more than one query agreed", i.e. the weakest possible form of the rule. Per
the brief, the constant was not iterated against the result.

Applied in `decide_detection`, **before** truncation to the top five, so a
filtered candidate frees its slot for the next eligible one rather than
shortening the set. Filtering is a pure predicate over already-scored
candidates, so ordering among survivors — and determinism — is unchanged
(asserted over eight repeat runs).

### Result: the gap did not close

| Detection source | Before | After |
|---|---|---|
| `both` | 22/22 = **100%** | 22/22 = **100%** |
| `co_citation` | 16/16 = **100%** | 16/16 = **100%** |
| `serp` only | 5/11 = **45%** | 7/12 = **58%** |

| | Before | After |
|---|---|---|
| Automated precision | 76% (38/50) | **76% (38/50)** |
| Audited precision | 88% (44/50) | **90% (45/50)** |
| URLs at ≥80% (audited) | 9/10 | **9/10** |

Per-URL audited, before → after: helpscout 5→5, basecamp 4→4, stripe 5→5,
allbirds 4→4, **ooni 4→5**, savvycal 5→5, roto-rooter 5→5, patagonia 4→4,
wise 5→5, ghost 3→3.

**One site improved by one row. Nine were unchanged.** SERP-only precision moved
45% → 58%, still nowhere near the 100% of the other two sources. A +2pp overall
move across two separate live runs is inside run-to-run variance, so the honest
reading is that **this fix did not deliver the improvement its hypothesis
predicted.**

### Why it failed — the useful part

Every false positive from Epic 3.4 survived the gate: The Digital Project
Manager, Famous Footwear, Buckman's, Reedsy, MindStir Media. They were not
single-query artefacts. Instrumenting helpscout.com made the mechanism obvious:

```
PASS  thecxlead.com    src=serp  distinct_queries=3
PASS  zapier.com       src=serp  distinct_queries=3
```

`thecxlead.com` is a **publication**, and it appeared in three of six queries.

**The gate's premise is false for the query set we generate.** Three of the six
shapes — `best {seed}`, `top {seed} companies`, `{seed} providers` — are near
paraphrases and return substantially the same listicles. Appearing in two of
them is therefore *not* independent corroboration; it is the same query asked
three ways. Counting distinct query strings measured query-set redundancy, not
evidence.

That reframes the problem usefully: the weakness is not "SERP-only candidates
need more hits", it is **"a listicle ranks like a competitor, and query
repetition cannot tell them apart."** Separating them needs a different signal —
whether the domain is a vendor or a publisher — not more of the same one. Two
directions worth considering, neither attempted here: classify the candidate
domain itself (one cheap model call over ~10 domains), or require SERP-only
candidates to appear in queries of *different shapes* (brand-anchored **and**
industry-seeded), which are genuinely uncorrelated.

### Recall impact: none observed, but the risk is real

**All 10 sites returned a full set of 5. No set was shortened.** In production
`build_queries` issues 3–6 queries, so a genuine competitor generally appears in
at least two.

The risk is nonetheless real and is now pinned by a test
(`TestSerpGateRecallImpact`): with only one query's worth of SERP data and no
co-citation, every candidate is gated and the set is emptied — `NO_SIGNAL` where
it previously returned a 3-competitor `WEAK_SIGNAL` set. `candidates_considered`
still records the evidence, so the loss is visible rather than silent. A thin
market or a partial SerpApi outage could hit this.

**Three existing tests encoded the pre-gate behaviour** and were updated rather
than weakened: two in `TestDetectionConfidence` and one endpoint stub had used a
single query for brevity, which production never does. Their fixtures now issue
two queries so they keep testing confidence and limit semantics; the single-query
case they used to cover incidentally is now covered deliberately, and
adversarially, in `TestSerpGateRecallImpact`.

### Keep or revert?

Kept. It is a small, honest improvement (SERP-only 45% → 58%, ooni +1) with no
observed recall cost, and it makes the *next* attempt cheaper by having ruled
out the "more query hits" hypothesis with evidence. But it should not be
described as having fixed the problem, and the ~58% figure should be treated as
the current state of SERP-only precision rather than a solved issue.

### Caveat on the comparison

Before and after are two separate live runs, so some delta is noise, not the
gate. Observed variance between runs, independent of the change: allbirds' Atoms
moved `both` → `serp` (and allbirds dropped to `weak_signal` with 0.000
confidence for lack of any corroborated candidate); ooni gained Bertello;
helpscout's top five gained Kustomer and Crisp while Kayako and Intercom fell
out. Single-run A/B on this corpus cannot resolve differences smaller than a few
rows — which is itself a reason not to keep tuning against it.

### Addendum — a floor, closing the gap this entry identified

The empty-set regression above never fired in the live run, but
`TestSerpGateRecallImpact` proved it reachable, and "reachable but not yet
observed" is not a safe thing to ship in a path that degrades under a partial
SerpApi outage. A floor was added.

**Rule:** if gating would leave fewer than `MIN_COMPETITORS_FOR_OK` (3)
candidates, backfill from the gated-out ones, highest score first, until the
floor is met or they are exhausted. No new constant — the floor reuses the
existing minimum. No new status — a backfilled set is `WEAK_SIGNAL`, which is
precisely what that flag already means.

**Two ordering decisions:**

- Backfilled candidates are appended **after** the eligible ones rather than
  merged by score. A rank is a claim about evidence, and a candidate that failed
  the gate has weaker evidence than one that passed it, whatever its raw score.
  `test_single_signal_with_realistic_query_count_keeps_the_real_one_first` pins
  this.
- A backfilled set is **never** reported as `OK`, even when it contains
  corroborated rows and would otherwise qualify. It contains rows that failed the
  evidence bar, and an operator should look.

**Epic 3.5's numbers above are unchanged, and that is proved rather than
asserted.** All ten sites returned a full set of five, so at least five
candidates cleared the gate in every case — far above the floor.
`TestFloorIsANoOpAboveTheFloor` demonstrates the property directly: with enough
eligible candidates the floor is inert, ordering is untouched, and a fully
corroborated set still reports `OK` with confidence 1.000. The paid 10-URL
script was deliberately not re-run.

**One property worth knowing: the floor masks the gate below three eligible
candidates.** That is intended, but it surfaced as five failing tests whose
fixtures had fewer than three eligible rows — including the end-to-end test —
where the backfill silently readmitted the very candidate the test was asserting
was excluded. Those fixtures now carry three eligible candidates so the gate is
observable, and the floor has its own boundary tests
(`TestSerpGateFloor`: exactly 3 eligible stays inert; 2 eligible promotes exactly
one, not all). Worth recording because the failure mode is subtle — a test can
keep passing while measuring something other than what it names.

Finding 2 (confidence calibration) remains open and untouched.

---

## 2026-08-21 — Epic 4.0 · A silent migration gap, and the Epic 3 bug it caught

Two findings that predate this epic's feature work and matter more than it.

### Alembic cannot see enum members being added

Enums are mapped as `VARCHAR + CHECK` (`native_enum=False`). Alembic's
autogenerate does not diff the *contents* of a CHECK constraint — it sees an
unchanged VARCHAR column. **Adding a member to a Python enum therefore produces
an empty migration**, and `alembic check` reports no drift. Everything looks
correct until the first insert of the new value fails at runtime.

Adding `Engine.CLAUDE_SEARCH` hit exactly this: autogenerate emitted
`pass`. The migration had to be hand-written, and needed `op.f()` on the
constraint name — the metadata naming convention prefixes `ck_%(table_name)s_`,
so passing an already-qualified name produced
`ck_engine_results_ck_engine_results_engine` and the DROP failed. The whole
upgrade then rolled back silently, leaving four "Running upgrade" log lines and
a database still at the old revision.

### The guard was tautological at first, which is worth recording

`tests/test_enum_constraints.py` was written to close the gap by asserting every
Python enum member is accepted by the database. It passed. A deliberately
unmigrated member was then injected as a negative control — **and it still
passed.**

The reason: `conftest.py` builds the test schema with
`Base.metadata.create_all()`, which regenerates every CHECK constraint from the
*current* Python enum. Checking the Python enum against that database compares a
thing to itself. The guard could never fail.

Repointed at a database built by running the real migrations from base to head
(its own session-scoped fixture, own throwaway database, dropped afterwards).
The negative control now fails as it should. This also gives the suite its first
genuine execution coverage of the migration files — a broken revision now fails
in CI rather than on a deploy.

**The general lesson: a guard that has never been observed to fail is not
evidence.** Both the original guard and the tests it was meant to protect were
passing for the same reason — they were all measuring the model against itself.

### What it immediately caught: DetectionSource.BOTH

Epic 3 added `DetectionSource.BOTH` — the value marking a competitor that SERP
and co-citation surfaced independently, the strongest signal in the entire
ranking — and never widened its CHECK constraint. Every Epic 3 test passed,
because they all ran against the create_all schema.

**Against a migrated database, the first corroborated competitor would have
failed to insert.** Fixed in its own revision. Epic 3's live verification never
hit it because that script exercises the ranking functions directly and does not
persist.

---

## 2026-08-21 — Epic 4.1 · Prompt generation

**Built:** `services/prompts.py`.

The prompt set is the measuring instrument. Every number this product reports —
mention rate, share of voice, sentiment — is a statement about *these* prompts,
so how they are built determines what the score means.

### Handling an uncalibrated industry label

Same discipline as Epics 2 and 3, for the same reason: `Client.industry`
confidence is uncalibrated (Finding 2, still open).

- **No industry-keyed template dictionary.** Generation is one generic
  instruction parameterised by whatever the label says. The Epic 3 guard test
  was extended to this module
  (`test_no_industry_keyed_template_dictionary_exists`).
- **The label is passed to the model marked as unreliable** — literally
  "(classified automatically, may be imprecise)" — rather than as ground truth.
- **A deterministic, brand-anchored fallback** covers provider failure and does
  not depend on the classification at all. `generated_by` records which path
  produced the set, so a scan can always be explained.

### Intent quotas are enforced in code, not requested in the prompt

A model asked for "a mix" returns whatever mix it likes, and the mix decides
what the score measures — a set skewed to bottom-funnel prompts flatters a brand
with strong branded search and says nothing about discovery. Quotas
(45% awareness / 35% comparison / 20% bottom-funnel) are applied after
generation. Awareness is weighted highest because that is where invisibility
actually costs a business.

`enforce_intent_mix` **never pads**. If the model under-produced an intent the
set is smaller and honest about it, rather than topped up with near-duplicates
that would inflate the denominator of every rate scoring computes.

### The instruction that matters most

*"Most questions must NOT contain the subject brand's name. A question that
names the brand can only confirm the brand exists; it cannot reveal whether the
brand gets discovered."* Without it, generators produce sets dominated by
"is X any good?", which measure nothing this product exists to measure. The live
run bore this out: 0 of 12 awareness prompts named the subject.

---

## 2026-08-21 — Epic 4.2 · Two engines, one vendor

**Built:** `services/engines.py`.

Only `ANTHROPIC_API_KEY` is provisioned; OpenAI, Perplexity and Google are
empty. Checking the key's scope showed the server-side `web_search` tool is
available, which makes a genuinely different second engine reachable without a
new provider. **Approved before building.**

| Engine | Behaviour | Analogue |
|---|---|---|
| `claude` | Parametric recall. Names what it learned in training, cites nothing. | A non-browsing assistant |
| `claude_search` | Retrieves live, answers with real cited URLs. | Perplexity, AI Overviews |

They are modelled as **separate engines rather than a flag** because they answer
differently in the way this product measures. A report must be able to say "you
are absent from grounded answers but present in parametric ones", which needs
two rows. The live run produced exactly that disagreement on one prompt.

`claude_search` is also the only source of genuine Citation rows available
today — 63 real cited URLs across six prompts.

**Limitation, stated plainly: this is one vendor and one model, so it does not
test cross-vendor variance**, which is part of the product's eventual value. That
is credential-bound, not design-bound. Adding ChatGPT or Perplexity is a new
class implementing `EngineAdapter` plus a key — nothing in the runner,
extraction or persistence layer knows which engines exist.

---

## 2026-08-21 — Epic 4.3 · Facts-only extraction

**Built:** `services/extraction.py`, `services/scan_runner.py`.

`EngineAnswer.text` holds a live engine response. It is a plain dataclass with
no SQLAlchemy mapping and no persistence path — the same contract as
`CrawlResult` (Epic 2) and `SerpResult` (Epic 3). What survives is booleans,
ordinals, a sentiment label, counts, cited domains and URLs.

**`response_digest` is how change-detection works without retention.** A
SHA-256 of the whitespace-normalised answer lets two scans be compared for "did
the answer change?" without keeping either answer. Normalising first means
trivial reformatting does not read as a substantive change.

**Web-search blocks carry titles and page snippets — publisher copy — and are
deliberately not read.** Only the URL and its registrable domain are taken, and
a test asserts this by source inspection, because the alternative is noticing it
in a database months later.

**Sentiment is the one non-deterministic step**, and it is confined. Per
scoring-spec.md rule 4 it is classified once, upstream, and persisted; scoring
reads the stored label and never re-invokes a model. It is only requested when
the subject was actually mentioned — sentiment toward a brand that does not
appear is meaningless, and scoring-spec excludes it from the composite rather
than scoring it zero.

Everything else — mention detection, position, prominence, citation typing — is
pure string and set operations, so the same answer always yields the same facts.

### The brand-matching bug

Mention detection matched the subject by name and domain with word boundaries.
For a client whose classification had not run, `brand_name` is NULL and the
subject falls back to the bare **domain** — `helpscout.com` — which never appears
in prose that says "Help Scout".

**Every scan of an unclassified client would have reported a 0% mention rate**,
and it would have looked like a finding rather than a bug. Caught by an endpoint
test whose fixture created a client with `classify: false` — the realistic case.

Fixed with a third matching pass: a separator-insensitive slug comparison, so
`helpscout.com` and `HelpScout` both find "Help Scout". Restricted to slugs of
six characters or more, because stripping separators makes short names match
inside unrelated words. The existing false-positive guards still hold — "On" does
not match "on the shelf", "Front" does not match "Frontier".

**Brand detection is scoped to the subject plus KNOWN competitors**, never
open-ended entity extraction. A scan run before Epic 3 detection therefore
reports position 1 of 1 for every mention. Epic 5 needs to know this: share of
voice against an empty competitor set is not a meaningful number, and
`CompetitorSet.detection_confidence` is the signal for how solid the comparison
base is.

---

## 2026-08-21 — Epic 4.4 · Live verification, and the bug it exposed

Two live runs, both real: real prompt generation, real Claude parametric and
Claude web-search calls, real extraction. Nothing mocked.

### Run 1 — recovered from a backgrounded task

The first run was backgrounded after a transient `ENOTFOUND` and a 600s timeout.
It **completed successfully** and its output was recovered rather than
re-spending on a fresh run. The DNS failure did not recur; a follow-up auth probe
confirmed the credential was never the problem.

```
engine result rows : 12/12 prompt x engine pairs
  claude         results=6  mentioned=6 (100%)  citations= 0  failed=0
  claude_search  results=6  mentioned=5 (83%)   citations=63  failed=0
RESULT: PASS
```

The two engines **disagreed on one prompt** — "cheapest customer support platform
for a 5 person startup": parametric named the subject first, grounded did not
name it at all. That disagreement is the entire argument for running two
engines, and it appeared in the first six prompts.

### What run 1 exposed: every executed prompt was `awareness`

`enforce_intent_mix` returned prompts **grouped by intent**, so `generated[:6]`
was six awareness prompts. `position` follows that order, which means anything
taking a prefix of the set — the API's `promptLimit`, a run cut short by a rate
limit, a partial re-run — measured **one third of the buyer journey while
reporting a mention rate that looks whole**.

The unit test checked the ratio of the whole set, which was correct, and said
nothing about its order. A live run was the only thing that would have shown it.

Fixed with deterministic round-robin interleaving. Totals are unchanged
(12/8/4); any prefix is now representative — one of each intent in the first
three, all three present by six.

### Run 2 — after the fix

```
[awareness]     what's a good shared inbox tool for a small support team
[comparison]    help scout vs zendesk for a growing support team
[bottom_funnel] how much does help scout cost per user and what's included
[awareness]     we're outgrowing gmail for support emails, what should we move to
[comparison]    front vs help scout, which is better for shared inboxes
[bottom_funnel] is help scout worth it, what do current users complain about

engine result rows : 12/12 prompt x engine pairs
  claude         results=6  mentioned=6 (100%)  citations= 0  failed=0
  claude_search  results=6  mentioned=6 (100%)  citations=64  failed=0
RESULT: PASS
```

This run carries information the first could not, because comparison and
bottom-funnel prompts had never been executed live:

- **Sentiment discriminates.** Positive, neutral **and negative** all appeared.
  The negative came from "is help scout worth it, what do current users complain
  about" on the grounded engine — a prompt built to surface complaints, correctly
  classified. A classifier that only ever returns positive would have looked
  fine in run 1.
- **The engines disagree on position, not just presence.** Same prompt, subject
  first parametrically and second when grounded.
- **Bottom-funnel prompts name one brand** (`brands=1`) — a pricing question is
  about one company — while awareness prompts name four to six. The intent
  tagging is measuring something real.
- **Latency is wide and worth planning around:** 20.7s to 128.7s per call, with
  216.6s observed in run 1. A full 24-prompt two-engine scan is ~48 calls; at
  concurrency 3 that is roughly 15–25 minutes of wall clock. Synchronous
  execution is fine for verification and will not survive contact with a real
  user — Epic 9's end-to-end target is 5 minutes, which will need the Celery
  path Epic 1 scaffolded.

### Acceptance

§7: *"a scan produces structured EngineResult records for every prompt x engine
pair, with mentions and citations correctly parsed."* **Met.** 12/12 pairs in
both runs, 0 failures, 63 and 64 real cited URLs parsed from the grounded engine,
mentions and positions parsed on every row.

**Caveat stated plainly:** both runs used 6 of the 24 generated prompts, capped
for cost. Generation itself produced the full 24 with the correct intent mix in
both runs; the cap applies only to execution. The prompt × engine matrix is
complete for what was run.

---

## 2026-08-21 — Epic 5.1 · The negative control that failed to fail

Epic 4.0 established the rule: *a test that has never been observed to fail is
not evidence.* Forty-two scoring tests passed on first run, so before trusting
any of them, two determinism breakages were injected deliberately.

### Breakage A — float in the weighted-sum path: **NOT CAUGHT**

A float cast was injected into the composite calculation. **All 42 tests
passed.**

The reason is worth stating, because it generalises: **float does not break
determinism on one machine.** It breaks *precision*, and *cross-platform*
reproducibility. Running the same float arithmetic twice on the same host gives
the same wrong answer, so every same-in-same-out assertion held. And
`test_every_stored_value_is_a_decimal` passed because the injected code cast
back to `Decimal` at the end — the type was right, the arithmetic was not.

scoring-spec.md rule 3 ("Decimal, not float — binary floats make `0.30 × 33.33`
platform-fragile at the rounding boundary") was therefore **completely
unguarded**, in the exact module it exists to protect.

**Fixed in two ways.** The weighted sum was extracted into
`weighted_composite()`, previously inline in `compute_score` and reachable only
by crafting ResultFacts that happen to produce boundary-value sub-scores — which
is why the injection went unnoticed. A grid search then found real inputs where
the two disagree:

| effective weights 33.33 / 27.78 / 22.22 / 16.67 | Decimal | float |
|---|---|---|
| values 67.13 / 77.75 / 77.55 / 50.33 | **69.60** | 69.59 |
| values 76.72 / 52.87 / 67.21 / 86.28 | **69.58** | 69.57 |
| values 79.46 / 6.22 / 5.17 / 28.76 | **34.16** | 34.15 |

A one-point swing in a client-facing score, from arithmetic alone. Those three
are now parametrised tests, backed by a source-level guard that rejects any
float entering the module — the boundary cases only catch a float that lands on
a rounding edge, whereas the rule is categorical.

Re-injecting the float now fails 4 tests, including the source guard on its own.

### Breakage B — unsorted iteration: **caught correctly**

Removing the explicit sort from `compute_inputs_digest` failed
`test_digest_ignores_input_ordering` immediately. Removing it from
`compare_competitors` failed `test_ordering_is_deterministic`. Both behaved as
intended without modification.

### The pattern, twice now

Epic 4.0's enum guard passed against a `create_all()` schema because it compared
the model to itself. This one passed against float arithmetic because it
compared a machine to itself. **Both were self-consistent and both proved
nothing.** The cost of checking is one deliberate breakage; the cost of not
checking is a guard that reads as protection and provides none.

---

## 2026-08-21 — Epic 5.2 · Sub-scores, and two deliberate spec deviations

**Built:** `services/scoring.py` (pure), `services/scoring_runner.py`
(persistence), `routers/scores.py`, `schemas/score.py`.

### How each sub-score is computed from real tables

| Sub-score | Source | Notes |
|---|---|---|
| **Mention Rate** 30% | `engine_results.mentioned` | Denominator is **answered** results only. A timeout or rate limit is *missing data*, not evidence of absence — counting it would turn an outage into a low score, which is what the PARTIAL scan status exists to prevent. |
| **Share of Voice** 25% | `engine_result_brand_mentions` | Counts every appearance, not merely presence, so a rival named in eight answers outweighs one named twice. Yields exactly `100/(1+n)` when all are tied. |
| **Citation Strength** 20% | `engine_result_citations.cites_subject` | Distinct domains, normalised against the best-cited brand in the same scan. |
| **Sentiment** 15% | `engine_results.sentiment` | Read from storage, never re-classified. positive=100, **neutral=50**, negative=0. |
| **Technical Foundation** 10% | — | No input exists until Epic 6. |

**Neutral sits at the midpoint, not zero.** Being listed without evaluation is
materially better than being warned against, and collapsing the two would make
Sentiment a near-duplicate of Mention Rate.

**§6 asks Citation Strength for "number AND authority of domains". There is no
authority data in this system** — no Domain Authority feed, no backlink source,
nothing upstream that produces one. scoring-spec.md anticipated this: fall back
to raw domain count normalised against the competitor maximum, and record the
degradation. Every score therefore carries `NO_AUTHORITY_DATA`. Normalising
against the best-cited brand in the same scan keeps the number answerable —
"how close is the subject to the most-cited player here" is knowable from what
we have; "is 7 citing domains good" is not.

### Deviation 1 — no-competitor Share of Voice (scoring-spec v1.1)

v1 said *"No competitors detected → Share of Voice = 100. Flag the scan."*

That awards **a quarter of the composite for a detection failure.** A brand
whose competitor detection returned nothing would score full marks on 25% of the
formula, which is a technically-computable but meaningless number — the exact
thing this project refuses everywhere else (null classification rather than a
guess, null score rather than a zero, `INSUFFICIENT_DATA` rather than a
plausible number).

Share of Voice is now **excluded and its weight redistributed** when the
competitor set is absent, empty, or `NO_SIGNAL`, with `NO_COMPETITOR_SET` on
`degradation_flags`. This is not an invented rule: it is the treatment v1 already
prescribed for sentiment with no population. Recorded in scoring-spec.md's
changelog; `FORMULA_VERSION` bumped to `v1.1`, since the §6 weights are unchanged
but the composite a given EngineResult set produces is not — and rule 5 exists so
that difference is attributable rather than silent.

A `WEAK_SIGNAL` competitor set still scores, but flags `WEAK_COMPETITOR_SET` —
Epic 3.5 measured SERP-only competitor precision at ~58%, and a weakly
corroborated set is a weaker denominator.

### Deviation 2 — Technical Foundation excluded, with a distinct reason

Excluded pending Epic 6 and its weight redistributed, rather than scored zero.
Scoring it zero would depress every score by up to 10 points for a reason that
has nothing to do with the client.

Its reason code is **`NOT_YET_MEASURED`, deliberately distinct from
`NO_POPULATION`.** "We have not checked this yet" and "there was nothing to
measure" must be worded differently to a client, and Epic 7's report cannot tell
them apart from a shared flag. This forced a schema change: `excluded_dimensions`
was `ARRAY(String)` — a list of dimension names with nowhere to put a reason —
and is now `JSONB` holding `{dimension: reason}`. The migration discards old
values rather than mapping them, which is correct: the reasons never existed, and
an invented one would be worse than an empty map.

### Re-scoring semantics — a correction

The brief said "INSERT, never overwrite". Implementing that hit a unique
constraint Epic 1 had put on `(scan_id, formula_version)`, and the constraint is
right. scoring-spec.md rule 5 versions **the formula**, not the invocation:
because scoring is deterministic, re-running under the same formula against the
same inputs yields an identical row, and storing N copies would be noise rather
than history. Re-scoring now refreshes the row for the current formula version
and leaves other versions untouched — which is what Epic 11's before/after
reporting actually reads.

### Competitor comparison — no composite

Per the confirmed decision: competitors carry Mention Rate, Share of Voice and
Citation Strength only. Sentiment is classified toward the subject alone
(Epic 4.3) and Technical Foundation is Epic 6, so 25% of the weight has no
per-competitor input. A composite computed over a different weight basis would
not be comparable to the subject's — which is the entire purpose of a
comparison. The comparison is **derived on read** rather than stored: it is a
pure function of persisted rows, and a second copy could fall out of step.

---

## 2026-08-21 — Epic 5.3 · Live verification on real data

`scripts/verify_scoring.py` runs the whole pipeline against the dev database:
real SerpApi competitor detection, a real 3-prompt × 2-engine Claude scan, then
scoring. **Scoring itself makes no provider calls** — the cost is entirely in
producing genuine rows to score.

```
AI VISIBILITY SCORE : 54.99   (status=scored)
formula_version     : v1.1
inputs_digest       : a6b8ebef36f71dc4679bef86c2ec04ed6802126626c2c7bbb0edb4f1ee68d0b5
------------------------------------------------------------------------------
  mention_rate            100.00  x 33.33%  =  33.33
  share_of_voice           30.00  x 27.78%  =   8.33
  citation_strength         3.70  x 22.22%  =   0.82
  sentiment                75.00  x 16.67%  =  12.50
  technical_foundation        --    EXCLUDED  NOT_YET_MEASURED
------------------------------------------------------------------------------
  degradation_flags   : ['NO_AUTHORITY_DATA', 'TECHNICAL_FOUNDATION_NOT_MEASURED']

  competitor                mention      SoV  citations
  Zendesk                     83.33    25.00       3.70
  Freshdesk                   66.67    20.00       0.00
  Front                       83.33    25.00       3.70
  Kustomer                     0.00     0.00       0.00
  Thecxlead                    0.00     0.00       0.00

re-scoring the same persisted rows 5 times:
  distinct composites : {'54.99'}     distinct digests : 1
  distinct score rows : 1 (idempotent per formula version)
RESULT: PASS
```

The numbers behave the way the design intends: Help Scout is named in every
answer (100% mention rate) but holds only 30% of the voice against four rivals,
and is barely cited (3.70) — a profile that would read as "you are present but
not the answer", which is exactly the finding this product exists to surface.

### What the live run caught that unit tests did not

**The stored effective weights were full-precision Decimals** —
`33.33333333333333333333333333%` in the raw output. The composite was computed
from those, while the report would display 2dp. A client re-adding the numbers
in their own report would not get the total back, which quietly violates
scoring-spec.md rule 2 ("a displayed breakdown always re-sums to the displayed
total").

Fixed by rounding the effective weights **once**, and using the rounded values
for both the composite and storage. Verified against the same persisted scan:
weights now sum to exactly `100.00`, the breakdown re-sums to `54.99`, and the
composite is unchanged by the fix. A new test asserts the re-sum property
directly rather than within a tolerance.

Two runs in a row now (Epic 4.4's intent-ordering bug, this one) where the
defect was invisible to assertions and obvious in raw output.

### Two known-open issues, visible in real data

- **`Thecxlead` is in the competitor set.** It is a *publication*, not a rival —
  the SERP-only precision gap measured at ~58% in Epic 3.5. It scores 0.00
  across every dimension because no engine ever names it, so its effect on this
  composite is nil, but it would look wrong in a client-facing report. Still
  open, still out of scope.
- **`NO_AUTHORITY_DATA` on every score.** There is no Domain Authority source in
  the system, so Citation Strength is a normalised domain count. §6 asks for
  "number *and* authority"; only the first half exists.

### Also confirmed

The Epic 2 constraint `ck_clients_industry_matches_classification_status` fired
during this work — the verification script set `industry` while leaving
`classification_status` at `pending`, and the database refused the row. That
constraint exists to stop a guess being stored as a result, and it caught a real
violation written months later by its own author.

---

## 2026-08-22 — Epic 6.0 · Reconciling §6 and §7, and what got built

### The specs named different checks

| §6 (Technical Foundation inputs) | §7 (Epic 6 checklist) |
|---|---|
| Schema presence | Schema/structured data ✓ |
| Structured data | (same) |
| Content freshness | **absent** |
| **absent** | Core Web Vitals |
| **absent** | Indexation/crawlability |

Also worth noting: §7's acceptance criterion is *"audit returns pass/fail +
detail for each check on a known test site"* — **per-check verdicts, not a
score**. The 0–100 Decimal is a §6 requirement, not §7's. Both are delivered.

**Resolved (approved):** build all of §7's checks, and let indexation feed the
score alongside §6's three named inputs. A site blocked by `robots.txt` or
marked `noindex` has a catastrophic technical foundation, and scoring it as
though only markup mattered would be misleading. Recorded as a
scoring-spec change of *inputs*, not of the 10% weight.

**Core Web Vitals are measured and reported but carry no weight.** `lcpMs` and
`cls` here are single-cold-load lab numbers; letting a measurement that varies
run to run move a client-facing score would make the score vary too. Every CWV
check carries `LAB_MEASUREMENT_NOT_FIELD_DATA` so a report cannot present them
as field data.

**INP is not measurable and is not faked.** It measures real user interaction
latency — a crawler that never clicks anything cannot produce one. Recorded as
`not_applicable` with `FIELD_METRIC_REQUIRES_REAL_USER_DATA`, and `inpMs` stays
null. Real INP needs CrUX or real-user monitoring; a lab proxy would be a
fabricated number wearing a real metric's name.

### Epic 2's crawler: examined, partially reused

`services/crawl.py`'s URL normalisation and Public Suffix List parsing **are**
reused. Its page fetch is not, for two reasons that are not stylistic:

1. **It blocks images, fonts and video at the router** to halve classification
   crawl time. Largest Contentful Paint is usually an image. Measuring LCP
   through a crawler that refuses to load images would produce a confidently
   wrong number.
2. It fetches several pages and extracts text for a classifier. An audit needs
   one page loaded completely, two side fetches, and no text at all.

### Signals crawled, and why those

| Signal | Why |
|---|---|
| JSON-LD + microdata **type names** | §6's "schema presence" and "structured data". Type names are explicitly permitted structural signals. |
| `Organization`/`LocalBusiness`/`FAQPage`/`Product` flags | "Has markup" and "has the RIGHT markup for this business" are different findings with different fixes. |
| title / meta description / canonical / OG **presence** | Markup completeness. Presence only — the values are page copy. |
| `robots.txt`, `sitemap.xml`, `meta robots` | §7's indexation and crawlability. |
| `Last-Modified` header, JSON-LD `datePublished`/`dateModified` | §6's "content freshness". Only the DATE is read from JSON-LD — the headline, author and article body sitting beside it are content and are not touched. |
| LCP, CLS via `PerformanceObserver` | §7's Core Web Vitals, lab-grade. |

### Normalisation, and why it is deterministic

Four scored components, each 0–100, weighted: **indexation 30, structured data
25, content freshness 25, schema presence 20.** Indexation is heaviest because
it is a precondition rather than a nicety.

Schema presence is **graduated** (0 / 60 / 100 by distinct type count) rather
than a boolean: one stray `WebSite` type is not a marked-up site, and a flag
would call them equal. Freshness uses bands (≤90d, ≤180d, ≤365d, ≤730d) because
the difference between yesterday and last week is immaterial while the
difference between last year and three years ago is not.

**A component with no signal is excluded and its weight redistributed**, never
scored zero — a site that does not advertise `Last-Modified` has a publishing
convention, not a visibility problem. Same discipline as scoring-spec.md's
treatment of sentiment with no population.

Determinism: the scored signals are presence booleans, type names and counts,
read identically every time. `Decimal` throughout, weights rounded once so the
breakdown re-sums. The genuinely non-deterministic parts — CWV timings, and
`content_age_days` which depends on wall-clock — are handled the same way
sentiment was in Epic 4: measured once, upstream, persisted, and read
deterministically thereafter. CWV additionally carries no weight at all.

### Model changes

Epic 1's `TechnicalAudit` already had `lcp_ms`/`inp_ms`/`cls`, the schema
booleans, the indexation flags and `content_age_days` — a good shape that
anticipated this epic. Four columns were added: `status`, `error_code`,
`technical_foundation`, `excluded_components`, `audited_at`.

`status` was the necessary one. Without it a failed crawl and a genuinely bare
site are indistinguishable — both a row of nulls — and Technical Foundation must
never treat "we could not read the site" as "the site has no markup".

`technical_foundation` is **stored** rather than recomputed on read, unlike Epic
5's competitor comparison. The full `AuditSignals` object is transient by design
(ip-safety.md #7), so there is nothing to recompute from.

### scoring.py's contract was not touched

`compute_score()` has always taken `technical_foundation: Decimal | None`.
Epic 6 produces the Decimal; scoring consumes it exactly as before. The only
change in `scoring_runner.py` is loading the audit's value instead of passing
`None`. **No stop-and-flag was needed.**

A *failed* audit still passes `None`, keeping the dimension excluded — an
unreadable site must not be scored as a measured zero.

---

## 2026-08-22 — Epic 6.1 · Live verification and the integration proof

### Three real sites

```
helpscout.com   TECHNICAL FOUNDATION = 87.50
  indexation=100  schema_presence=100  structured_data=50  content_freshness=100
  14 pass, 2 warn, 1 not_applicable
anthropic.com   TECHNICAL FOUNDATION = 55.00
  indexation=100  schema_presence=0    structured_data=0    content_freshness=100
  12 pass, 2 warn, 2 fail, 1 not_applicable
basecamp.com    TECHNICAL FOUNDATION = 75.00
  indexation=100  schema_presence=100  structured_data=0    content_freshness=100
```

A genuine spread with legible causes: anthropic.com publishes no structured data
at all (two hard fails), basecamp.com has schema but no business-entity type,
helpscout.com has both and loses points only on FAQ/Product markup. Every site
passed indexation, which is what one would expect of established sites and is a
useful sanity check on the check itself.

### The integration proof (§7's real point)

Same persisted scan, scored before and after the audit existed:

```
BEFORE audit:
  composite            : 54.99
  technical_foundation : None
  excluded             : {'technical_foundation': 'NOT_YET_MEASURED'}
  flags                : ['NO_AUTHORITY_DATA', 'TECHNICAL_FOUNDATION_NOT_MEASURED']

audit run: status=ok technical_foundation=87.50 checks=17

AFTER audit:
  composite            : 58.24
  technical_foundation : 87.50
  excluded             : {}
  weights              : mention_rate=30.00, share_of_voice=25.00,
                         citation_strength=20.00, sentiment=15.00,
                         technical_foundation=10.00
```

**The effective weights return to §6's table exactly** — 30/25/20/15/10 — once
all five dimensions are included. That is the identity case for the
redistribution logic written in Epic 5, and it holding on real data is a
stronger check on that logic than any of its unit tests.

### Negative controls — four, one per new invariant class

Per the Epic 4.0 / 5.1 discipline. **All four failed as intended**, unlike
Epic 5.1's float control which initially failed to fail:

| Injected breakage | Caught by |
|---|---|
| `float()` in the normalisation arithmetic | `test_module_uses_no_float_arithmetic_in_normalisation` |
| `AuditSignals.meta_description` (a page-copy field) | `test_signals_carry_no_page_content` |
| Core Web Vitals moving a scored component | `test_vitals_do_not_change_the_score` |
| `TechnicalAuditOut.meta_description` on the API surface | `test_audit_response_schemas_expose_no_page_content` |

The float guard worked first time here specifically because Epic 5.1's failure
taught the lesson: a source-level guard was written up front rather than relying
on same-in-same-out assertions, which cannot detect float — those compare a
machine to itself.

### Also confirmed

The Epic 4.0 enum guard picked up the new `audit_status` enum **automatically**
(21 enum columns checked, was 20) and verified its CHECK constraint. A guard
built two epics ago covering a table that did not exist then is the outcome that
justifies having built it.

### No new dependencies

`playwright` (Apache-2.0) and `httpx` (BSD-3-Clause) were already vetted.
Licence audit unchanged: **62 distributions, PASS**.

---

## 2026-08-22 — Epic 7.0 · The narrative report

The first customer-facing surface. Everything Epics 1–6 built exists to produce
this screen, and the design constraints in `ip-safety.md` that were dormant
until now (1–5, 8) are live for the first time.

### Scope: §7's checklist vs what shipped

§7 Epic 7 lists three items. **One shipped in full, one partially, one deferred:**

| §7 item | Status |
|---|---|
| Report layout: narrative structure (score → gap → proof → fix → pitch) | ✅ shipped |
| White-label branding injection (agency logo/domain/colours) | ◐ name + slug only |
| PDF export + shareable web link | ⬚ deferred to Epic 7.1 |

This was a product-owner call, made before any code was written, because the
task brief and §7 disagreed — the brief listed all three as out of scope and §7
puts all three in. Flagged rather than resolved unilaterally.

**Why white-labelling stopped at name and slug.** `Agency` has `name` and `slug`
(the latter commented "used for white-label report URLs from Epic 7" since Epic
1) and nothing else. Logo, custom domain and brand colours need new columns —
and, more importantly, a **written policy on which tokens an agency may
override**. The visibility ramp is load-bearing: it is the only thing that makes
a score legible as a score, it is monotonic in lightness so it survives greyscale
print, and it is warm-to-cool for CVD safety. An agency free to recolour it
changes what the score *means*. That policy is a design decision, not an
implementation detail, and inventing one silently in order to tick a checkbox
would be the wrong trade. Recorded in `api-contracts.md` under Epic 7.1.

### Where the biggest-gap computation lives, and why not in `scoring.py`

The brief asked for the gap to be computed rather than authored. It already was:
`gap_i = weight_i × (100 − subscore_i) / 100` is implemented and unit-tested
inside the design system's `layoutLedger`, because it is what draws the unlit
portion of each ledger segment.

**So the report calls that same function rather than reimplementing it in
Python.** Two implementations of one number would eventually disagree, and the
failure mode is the worst available: the chart annotating one dimension while
the headline above it names another. Calling the function that draws the chart
makes that disagreement impossible by construction.

The derivation lives in `apps/web/src/lib/report/derive.ts`. It takes the
report payload and returns the whole narrative — headline claims, the ranked
gap, the fix list, the pitch arithmetic. `scoring.py` was not touched.

On the real Help Scout scan the gap ranking is usefully non-obvious:

```
mention_rate       30 ×   0.00 / 100 =  0.00
share_of_voice     25 ×  70.00 / 100 = 17.50
citation_strength  20 ×  96.30 / 100 = 19.26   <- biggest
sentiment          15 ×  25.00 / 100 =  3.75
technical          10 ×  12.50 / 100 =  1.25
```

Citation Strength wins on recoverable points, not on being the lowest number —
and `derive.test.ts` carries a second case where a heavier dimension with a
milder deficit (mention_rate 50/100, weight 30 → 15.0 points) outranks a lighter
disaster (sentiment 10/100, weight 15 → 13.5 points), which is the case the
formula exists to get right.

### Design system: reused vs added

**Reused, unchanged —** `ReportPage`, `ReportHeader`, `Beat`, `Prose`,
`Evidence`, `FixList`, `BEAT_SEQUENCE`, `LuminanceLedger`, `layoutLedger`,
`ScoreDisplay`, `Card`, `CardBody`, `Badge`, `VisibilityBadge`, `DataTable`,
`Button`, and the token set via the Tailwind preset. Epic 0 anticipated this
epic well: `Beat`'s `BeatId` union made skipping a beat a compile error, and
`Evidence`'s prop shape (`engine`, `prompt`, `findings: {label, value}[]`, no
children) meant there was nowhere to put a paragraph of scraped answer text even
if someone wanted to.

**Added to the design system —** exactly one thing: `lineHeight` in the Tailwind
preset. The `--avp-leading-*` tokens have existed in `tokens.css` since Epic 0
but were never listed in the preset, so `leading-prose` compiled to **nothing**.
Epic 2's intake screen had been using it silently since it shipped. A class that
looks applied, reads as applied in review, and does nothing is the worst failure
mode a design system has, so the fix came with a test that checks every `var()`
the preset references against the stylesheet — the preset and the tokens can no
longer drift apart in either direction.

**Deliberately NOT used: competitor ghost columns.** `LuminanceLedger` supports
them and the Epic 0 style guide demos them. They are wrong here. A ghost column
is a competitor *composite*, and competitor sub-scores cover three of five
dimensions — sentiment is classified toward the subject only, and the technical
audit is of the subject's own site. A composite over 75% of the weight would
render every rival shorter than they actually are, which is the exact
weight-basis error `api-contracts.md` warns about under Epic 5. The comparison
is made per-dimension in the proof beat instead, where both sides have a real
figure, and the gap beat says so in a sentence rather than leaving the absence
unexplained.

### One endpoint, not four

`GET /api/v1/scans/{scanId}/report` — the shape was already agreed in
`api-contracts.md` ("planned, not yet built", Epic 7). It aggregates; it
computes nothing. The proof beat needs figures the raw endpoints do not expose
(citations grouped by domain, mention shares, per-engine coverage), and deriving
those client-side means paging `/scans/{id}/results` — 48 rows and ~400
citations on a full scan — and re-aggregating on every render. It also keeps the
facts-only projection in **one** place for `test_ip_safety.py` to assert over;
four client-side derivations would be four places for a snippet to slip in.

### How each degraded state is handled in the UI

The brief's standard: "a report screen that only handles the happy path is not
done." Each state below renders differently, and each is covered by a test.

| State | What the screen does |
|---|---|
| `score: null` (never scored) | Score beat says "has not been scored"; the proof beat still shows its real evidence, because the scan *did* run. Distinct from the row below. |
| `status: insufficient_data` | `ScoreDisplay` renders `—`, never `0`. The ledger is not mounted at all — its empty state would only repeat the sentence above it, so the gap beat explains the absence in words. Pitch declines to project. |
| `NOT_YET_MEASURED` | "Not yet checked." Worded as **our** missing capability. It also **suppresses the matching fix** — telling a client to fix a dimension we never measured blames them for our gap. |
| `NO_POPULATION` | "Nothing to measure." An absence, not a bad result. |
| `NO_COMPETITOR_SET` | "No comparison was made", plus "rather than awarding points for a detection that did not happen" — the v1.1 reasoning, in client-facing words. |
| `WEAK_SIGNAL` competitor set | A `warn` badge beside the comparison table: "treat this rival set as a starting point and correct it before sending the report on." Actionable, since the operator can override the set. |
| `audit: null` | "The site has not been audited… a check that has not happened, not a check that failed." |
| `audit.status: failed` | "The site could not be read", with the error code. Explicitly: "a site we could not reach is not a site with a bad technical foundation." |
| `audit.status: partial` | A `warn` badge, and a note that the sub-score used what could be measured. |
| Degradation flags | Resolved to sentences from our own string table. `NO_AUTHORITY_DATA` never appears as a code on screen. |

**An excluded dimension is never passed to the ledger as `subscore: 0`.** That
would draw a full-height unlit segment reading as total failure on a dimension
the scoring engine deliberately refused to score. Excluded dimensions are
dropped from the chart and surfaced separately with their reason and their
**nominal** weight, so the report can say what the dimension would have been
worth. The **included** weights are the post-redistribution ones and always sum
to 100 — which is what preserves the ledger identity through an exclusion, and
is asserted both server-side and in `derive.test.ts`.

One refinement found by looking at the rendered degraded screen rather than the
markup: when every dimension shares one exclusion reason, the identical
paragraph printed five times, which reads as a page fault rather than a fact.
It now states the reason once and lists what it applied to.

### Two defects the real data caught that the tests had not

**1. Concrete audit fixes were being crowded out.** Five dimensions all carry
some gap, and every one of them outranks an audit finding on points — because
an audit finding carries *no* point value, deliberately. With a flat top-N the
fix list came out entirely abstract ("improve citation strength"), and the two
concrete, checkable changes the crawl actually returned — `NO_FAQ_SCHEMA`,
`NO_PRODUCT_OR_SERVICE_SCHEMA` — were pushed off the end. The brief specifically
asked for `"no FAQ schema" → a specific fix`, and the first implementation
silently dropped exactly that. Dimension fixes are now capped at 3, audit fixes
get the remaining slots, and a dimension gap below 2 points is not printed at
all (technical_foundation's 1.25 was noise beside citation_strength's 19.26).

**2. Competitor citations were being ranked out of existence.** `zendesk.com`
and `front.com` were each cited once; ranking cited domains purely by count
pushed both out of a twelve-row list otherwise full of review blogs — dropping
precisely the evidence the proof beat exists to show, which is *who is cited
instead of the subject*. Competitor-attributed domains now sort above
unattributed ones, then by count.

Both were invisible in unit tests and obvious in the rendered page. Same pattern
as Epic 5.1's injected float: the test suite was asserting the mechanism worked,
not that the output was any good.

### The pitch beat: arithmetic, not adjectives

Product-owner call. Everything the pitch asserts is arithmetic over figures
already on the page — the points the listed fixes recover, the composite that
would result, and the per-dimension distance to named rivals. On the real scan
it reads "58 today. 99 with the fixes above."

No revenue estimate, no traffic projection, no urgency language, because nothing
in this system measures any of those, and a number a client can dispute costs
more than it wins. `ReportView.test.tsx` asserts the rendered page contains none
of `revenue`, `roi`, `traffic`, `leads`, `conversion`, `guarantee`, `dominate`,
`act now` and similar. The projection is also clamped at 100 and declines
entirely when there is no score to project from.

Note the pitch compares **per-dimension**, never composite-to-composite — see
the ghost-column reasoning above.

### apps/web had no test runner

Its `test` script was an `echo` placeholder from Epic 2. The report is the first
screen with logic worth testing rather than markup worth looking at: the
narrative is *derived*, so the derivation can be wrong, and a wrong derivation is
a report that argues the opposite of what the data says. Added vitest (MIT,
already in the tree for the design system — no new package was downloaded) and
`@vitejs/plugin-react` (MIT, likewise). `vitest.config.ts` is excluded from
`tsc` because it pulls Vite's own types, which resolve to a different Vite major
than the design system's; that is a config-file-only conflict and says nothing
about the app's types.

### Test fixtures are real data

`apps/web/src/lib/report/__fixtures__/reports.ts` holds the actual report
payload for `scan_01M0HDRGJNWNZDSJPP0NC3SV8W` — the Help Scout scan from the
Epic 5/6 verification runs. Checked in verbatim so the derivation is tested
against shapes the pipeline really produces, including the awkward ones: a
citation strength of `3.70`, a `NO_AUTHORITY_DATA` flag, and an audit whose only
findings are two warnings. The degraded variants are derived from it by removing
data, which is how they arise in production.

The fixture caught one of my own errors: I hand-wrote the redistributed
composite for the no-competitor variant as `64.99`; the correct figure is
`67.65`. Worth stating why it goes *up*: excluding a weak dimension moves its
weight onto dimensions that score better. That is correct — the score is only
ever a claim about what was measured.

### Live verification

`scripts/verify_report.py`, run against `avp_dev`. Costs nothing.

```
PART 1 — a full report from a REAL scan
  scan     : scan_01M0HDRGJNWNZDSJPP0NC3SV8W
  subject  : Help Scout (helpscout.com)
  included weights sum : 100.00
  breakdown re-sums to : 58.24
  stored composite     : 58.24
  -> the ledger's lit height IS the stored composite. OK
  biggest gap: citation_strength = 19.26 points

PART 2 — ip-safety.md #7: the wire payload carries facts only
  payload size : 7323 bytes
  no prose-bearing key present. OK

PART 3 — a REAL degraded scan: INSUFFICIENT_DATA
  status  : insufficient_data   composite: None   reason: INSUFFICIENT_DATA
  every dimension excluded, each carrying its reason
  -> null composite, no dimension scored zero. OK
```

PART 3 builds the degraded scan through the real models and scores it with the
real scoring runner, so the INSUFFICIENT_DATA screen is a genuine database row.

Both screens were then rendered by the running app (API on 8000 against
`avp_dev`, Next on 3100) and captured with Playwright:
`docs/screenshots/epic7-report-helpscout.png` and
`epic7-report-insufficient-data.png`.

### IP-safety self-check (constraint 9)

Covering 1–5, 7 and 8, per the brief. Detail:

**1 — designed from the data model, not a competitor screenshot.** Each beat
exists because a table exists: score → `scores`, gap → the weight/sub-score
arithmetic, proof → `engine_results` + `citations` + `brand_mentions` +
`technical_audit_checks`, fix → audit `detail_code`s + the gap ranking, pitch →
the same numbers added up. No competitor product was opened, referenced or
described during this epic.

**2 — every screen imports from `@avp/design-system`.** No ad hoc Tailwind: the
preset uses `theme` (replace), so `bg-slate-500` does not compile. Verified at
render level too — `ReportView.test.tsx` scans the emitted HTML's inline styles
for raw hex and `rgb()` values, since inline styles bypass Tailwind entirely.
The only colours the page emits are `oklch()` from the token ramp.

**3 — narrative, not a dashboard.** All five beats render in `BEAT_SEQUENCE`
order, asserted by DOM position. Headings are claims ("Citation Strength is
costing the most — 19.3 points"), not category labels, and which claim is made
is chosen by the number, so the heading cannot contradict the chart under it.

**4 — assets.** Icons: `lucide-react` (MIT), one glyph (`ArrowRight`). Fonts:
Fraunces + IBM Plex Sans/Mono via `GOOGLE_FONTS_HREF`, all OFL-1.1, declared once
in the design system. No icon pack or illustration kit from any competitor
product. No new dependency was added by this epic; the licence audit passes.

**5 — no competitor source inspected.** No competitor page was fetched, viewed
or read during this epic. The only third-party URLs that appear anywhere are
citation `sample_url`s, which are rendered as outbound links and never fetched
by us.

**7 — the render gate.** This is the first epic that *renders* collected facts,
so the rule was checked at the surface rather than only at the schema:
- A citation appears as **a domain and a link out** — `<a href>` with the domain
  as its own link text, `rel="noreferrer nofollow"`. Never the cited page's
  title, and never a quotation. Asserted against the emitted HTML.
- A competitor appears as **name + domain** and per-dimension numbers. There is
  no description field to render — `Competitor` has never had one, and
  `ReportCompetitorOut` is asserted to have none.
- Audit findings render through **our own string table** keyed by
  `detail_code`. `NO_FAQ_SCHEMA` never reaches the screen as a code, and no
  crawler- or model-authored sentence is stored anywhere to render.
- **Verified explicitly, as the brief asked**, that there is no engine answer
  text to leak: `test_engine_result_has_no_text_column_for_a_report_to_render`
  walks `EngineResult`'s actual column *types* — no `Text` column, and the only
  `String` columns are `response_digest` (64) and `engine_version` (120).
  Neither can hold prose. The absence is now UI-visible rather than a database
  fact, and it holds.
- `test_report_projection_exposes_no_third_party_prose` sweeps **every** schema
  in `schemas/report.py` rather than a hand-picked list, so a field added later
  is caught rather than missed.

**8 — no verbatim competitor marketing copy.** Every string in
`lib/report/strings.ts` — dimension labels, exclusion reasons, degradation-flag
explanations, all sixteen audit fix descriptions, every beat's prose — was
written from the data model for this epic. Nothing is adapted from another
product's UI, including microcopy.

**IP-safety check passed** — see the completion summary for the one-line form.

### Tests

635 total, up from 548. api 429 → 451 (+22: report endpoint 16, ip-safety 6),
workers 13 (unchanged), shared-types 36 → 45 (+9), design-system 70 → 72 (+2:
preset/token parity), web 0 → 54 (new runner: derivation 28, render 26).

---

## 2026-08-22 — Epic 8.0 · The generated fix list

The first place a model writes something a client reads. Every earlier model
call in this system produces a *fact* — an industry label, a set of prompts, a
sentiment verdict, a list of brand names — which is then rendered by our own
code. This one produces prose that goes on the page as-is, which changes what
the guards have to protect and where they have to sit.

### What Epic 7 already did, and what was actually missing

§7 Epic 8 asks for two things: *"LLM cross-references audit + scan gaps into
named, specific recommendations"* and *"priority + effort estimation per fix"*.
Epic 7 already shipped a fix list that met the first one's acceptance criterion
on a technicality — `strings.ts` says so itself: *"this table is the
deterministic floor that already meets it, before Epic 8's LLM pass enriches
it."* So the honest first question was what remained.

Reading `deriveFixes` settled it. Three things were real work and one was not:

| Concern | Epic 7 | Epic 8 |
|---|---|---|
| Which dimensions get a fix | `gap >= 2`, top 3 by gap | **unchanged** |
| Which findings get a fix | every `detail_code` with a table entry | **unchanged** |
| Order, cap, blocking promotion | points desc, cap 5, `indexable`/`robots_txt_present` first | **unchanged** |
| `pointsUpside` | `round1(segment.gap)` | **unchanged** |
| Title + detail | fixed lookup: `FIX_FOR_DIMENSION` / `FIX_FOR_DETAIL_CODE` | model-authored per scan |
| Priority | `isBiggestGap ? high : gap >= 8 ? medium : low` | model-reasoned |
| Effort | fixed `S`/`M`/`L` per code, hardcoded in the table | model-reasoned |

The arithmetic column is the whole left half of that table and none of it moved.
What a lookup table structurally cannot do is the right half: `FixCopy.effort`
is a constant per detail code, so "add FAQ schema" is `M` for every site that
has ever been scanned, whatever else is true of it. And Epic 7's priority
heuristic sees exactly one number — this dimension's own gap — so it cannot
know that a 17.5-point share-of-voice gap sits behind a 19.3-point citation gap
that would move both.

That is the epic: **the model may not decide what is wrong, only how to say it
and how much it matters.**

### The candidate boundary, and why it is enforced rather than requested

`build_candidates` in `services/fix_runner.py` produces a closed list before any
call is made, and the prompt names it explicitly (*"Write one fix for each of
these 5 candidates, and no others"*). Asking is not enough, so `accept()` in
`services/fix_generator.py` discards any returned fix whose `candidate_id` does
not match one, and takes `rank` and `points_upside` from the **candidate**, never
from the response. A model that invents `gap:backlink_authority` gets nothing
persisted; a model that reorders the list is ignored.

**On duplicating the candidate rules in Python.** Epic 7 refused to reimplement
the gap formula server-side, and gave a good reason: two sources for one number
eventually disagree, and the failure mode was the chart annotating one dimension
while the headline named another. Epic 8 does now compute candidates in Python,
so that reasoning needs answering rather than ignoring.

It does not bite here, because **nothing this module computes is ever rendered.**
The client keeps deriving the fix list, its order and its point figures exactly
as before, and merges generated copy onto it *by key* in `enrich()`. If the two
candidate sets ever diverge, the unmatched generated row fails to match and
Epic 7's deterministic copy renders in its place. The degradation is weaker
wording, never a wrong claim — which is the opposite of the failure Epic 7 was
protecting against, where divergence produced a confident contradiction. Both
sides are pinned: `test_reproduces_the_client_s_list_for_the_real_scan` asserts
the five literal keys, and `derive.test.ts`'s existing block asserts the same
list from the other language.

### Facts-only prompt construction

`FixFacts` is a transient dataclass with no field capable of holding page copy,
an engine answer, a citation title or a competitor description — the same
`redacted()`-bearing, never-persisted shape as `CrawlResult` and `AuditSignals`.
`build_fix_prompt` interpolates only names, domains, labels, codes and numbers.
The real prompt for the Help Scout scan, in full, is printed by PART 2 of
`scripts/verify_fixes.py`; it is 27 lines and every one of them is a measured
figure, an entity name, a cited domain, or a check code.

This is the **first prompt-INPUT guard in the repo**, and that is worth stating
plainly. Epics 2 and 4 deliberately hand third-party text to a model — page copy
to the classifier, engine answers to the sentiment judge — and guard the
*output* schema so nothing comes back that could be stored. That is the right
shape for those. It is the wrong shape here: this module's output is prose by
design, so the boundary has to sit on what goes in. A survey of
`test_ip_safety.py` before this epic found no test anywhere that asserts a
prompt string *excludes* anything; every existing prompt assertion is a positive
inclusion check.

Three guards, deliberately different in kind:

1. `test_the_fix_prompt_is_built_from_facts_only` — `inspect.getsource` on the
   function, negative substrings. Same technique as Epic 4's
   `_extract_citations` guard.
2. `test_the_fix_prompt_names_the_facts_it_is_allowed_to_use` — a **positive**
   source assertion, per Epic 6's `audit_site` guard. Absence-only assertions
   pass vacuously against a gutted function.
3. `test_the_fix_prompt_carries_no_line_that_is_not_a_whitelisted_fact` — every
   line of the built prompt must match a whitelisted label. This is the
   categorical form Epic 5.1 argued for: a canary test catches the canary, this
   catches the class.

### Negative controls — 11 injected breakages, and the near-miss that mattered

Per the Epic 4.0 / 5.1 / 6 discipline. All 52 new generator tests and all 11 new
ip-safety tests passed on first run, which by that rule is not evidence of
anything.

| # | Injected breakage | Caught by |
|---|---|---|
| 1 | A citation title interpolated into the prompt | whitelisted-line sweep |
| 2 | `competitor_description` field added to `FixFacts` and interpolated | source inspection + 2 more |
| 3 | Engine answer text read into the prompt | source inspection + 2 more |
| 4 | Prompt construction gutted to a stub | whitelisted-line sweep + 8 more |
| 5 | Model reasoning persisted onto `PersistableFix` | `test_model_reasoning_is_never_persisted_or_returned` |
| 6 | Free-text `evidence` field added to the output schema | exact-field lock |
| 7 | Competitor names leaked into the `redacted()` log view | `test_the_facts_redacted_log_view_collapses_entities_to_counts` |
| 8 | `float()` introduced into the gap arithmetic | `test_no_float_enters_the_gap_arithmetic` |
| 9 | Unsupportable-claim guard disabled | 8 parametrised claim tests |
| 10 | Generator allowed to invent an unmeasured candidate | `test_a_fix_for_something_nobody_measured_is_discarded` + 1 |
| 11 | Model allowed to override Epic 7's rank | `test_rank_and_upside_come_from_the_candidate_not_the_model` + 1 |

**Control 2 reported NOT CAUGHT on the first pass, and the diagnosis is the
useful part.** The first version of the harness ran each control against only
the test it was *expected* to trip. Control 2 was scored against the `FixFacts`
field sweep and the prompt-line sweep; both passed, so it was recorded as a
failure to fail.

Running the full suite showed a third guard — the `getsource` check — caught it
immediately. So the guard set was fine and **the control harness was the thing
that was broken**, which is the same self-consistent-measurement error Epic 4.0
recorded: a harness that only asks the question it already expects the answer to
cannot report a surprise. Fixed to run every guard for every control and report
every test that failed.

But the near-miss was still worth having, because the two guards that *didn't*
catch it should have, and both were weaker than they looked:

- **The `FixFacts` field sweep used exact-name-set intersection.** Its forbidden
  set contained `description`; the injected field was `competitor_description`.
  A leak field will essentially always be named for what it holds *plus a
  qualifier*, so exact matching was close to useless. Hardened to substring
  matching, with the exemption keyed on the field's **type** rather than a name
  allowlist — `answers_analysed` may contain "answer" because it is an `int`
  and cannot hold one, whereas `answer_excerpt: str` could not be waved through
  the same way.
- **The prompt-line sweep only saw lines the prompt actually emitted.** The
  injected line was conditional (`if facts.competitor_description:`) on a field
  defaulting to `None`, so the branch never ran and the sweep inspected a prompt
  the injection had never touched. Hardened to assert over
  `dataclasses.fields()` that every field is set before the prompt is built, so
  a newly added field fails the test *unset* rather than passing unexamined.

Second pass: **11/11 caught**, control 2 now tripping three guards independently.
Every source file was diffed byte-for-byte against its pre-injection backup after
each control, and the full API suite re-run clean afterwards.

### The defect the rendered page caught that the tests had not

Same pattern as Epic 7.0's two, and found the same way — by looking at real
output rather than at whether the mechanism worked.

The first live generation produced genuinely good copy, and
`ReportView.test.tsx`'s new rendered-page assertion failed on it anyway. The
detail for the FAQ fix read, verbatim:

> The schema_faq check returned NO_FAQ_SCHEMA; the site currently declares only
> Corporation, VideoObject and WebSite.

That is a straightforward breach of an Epic 7 guarantee. `ReportView.test.tsx`
has asserted since Epic 7 that the page contains `FAQPage schema` and **never**
`NO_FAQ_SCHEMA`, because a finding is resolved through our own words rather than
the stored code. Epic 8 is the first thing in the system that hands those codes
to something which then writes client-facing prose, so it is the first thing
that could put one back on the page — and it did, immediately, on the first try.

No unit test could have caught it: every generator test used stub copy, and the
stub did not quote a code because the author did not think to make it. It failed
only against real generated text rendered through the real component.

Fixed in two places, per Epic 5.1's "a prompt rule is a request, a guard is a
rule": a system-prompt line (*"Never quote an internal identifier back… Write
'the site declares no FAQ markup', not 'the schema_faq check returned
NO_FAQ_SCHEMA'"*), **and** `machine_code_in()`, which rejects any fix whose copy
contains its candidate's `detail_code` or `source_key`. A rejected fix is simply
not persisted, so the client falls back to Epic 7's wording for that item —
degraded, not broken. Re-verified live afterwards: zero identifiers quoted,
nothing rejected.

The stub fixture had to change too, because it had been embedding the candidate
key in the title — copy the real guard now refuses. A stub that produces output
production would reject is a stub testing the wrong thing.

### Priority and effort, actually reasoned

`GeneratedFix` carries `priority_reason` and `effort_reason` alongside the
letters. They exist for the same reason `IndustryClassification.rationale` does
in Epic 2 — a model made to justify a judgement before stating it picks less
arbitrarily than one that only emits the letter — and, like `rationale`, they
are **debug-only**: absent from `PersistableFix`, from `ActionItemOut`, from the
`action_items` table, and from every response.
`test_model_reasoning_is_never_persisted_or_returned` walks that whole chain.

What the model actually returned for the real scan, at `logger.debug`:

> `gap:share_of_voice` → **high** · *"Second-largest gap at 17.50 points and
> mention rate is already at 100, so depth of mention is the remaining lever."*
>
> `gap:citation_strength` → **effort L** · *"Requires new long-form comparison
> content plus ongoing outreach to sites Help Scout does not control."*

Epic 7 made that first one `medium` — its gap is 17.5, under the 8-point line
for `high` is false, so `medium`; correct by its own rule and blind to the fact
that mention rate is already maxed. And Epic 7's effort for the citation fix was
`M`, a constant, against a change that depends on third parties. Both shifts are
visible in the screenshots below.

### Persistence: refresh in place, keyed on `(scan_id, source, source_key)`

Three precedents existed and none fits unmodified, so the reasoning is recorded
in `services/fix_runner.py`'s docstring as well as here.

**Not Score's versioning.** `scoring_runner` keeps one row per
`(scan, formula_version)` because scoring is *deterministic*: a re-run under the
same formula is identical, so extra rows are "noise rather than history", and
only a formula change produces something new. Neither half transfers. There is
no formula_version analogue for a fix list, and generation is **not**
deterministic — the same scan yields differently-worded fixes every run. Keeping
every invocation would accumulate near-identical rows differing only in phrasing,
and the report would have to pick one arbitrarily. That is Score's own argument
against versioning, in a stronger form.

**Audit's framing, but not its mechanism.** `audit_runner`'s *"a second audit of
the same scan is a correction rather than a new observation"* is exactly right
for a regenerated fix list. But it implements that by deleting its children and
rebuilding them, and **`ActionItem` is the first re-runnable entity in this
codebase carrying operator state**: `status` moves `open → in_progress → done →
dismissed` as an agency works the list. A blind rebuild would silently destroy
every `done` an agency had recorded.

**So: the audit's semantics with the competitor precedent's care.**
`competitors.persist_detection` exists to stop exactly this — *"an operator who
has corrected a bad set must not have their correction silently undone by the
next run."* Each candidate upserts onto its own row, carrying `status` and the
row id across; `_apply` is shared between insert and refresh so the two paths
cannot drift. A candidate that has disappeared — the client fixed it and
re-audited — is deleted **only while nobody has touched it**; once it carries
operator state it is kept as the record of that work, and since it no longer
matches any candidate the client derives, it simply does not merge and does not
render.

This needed a migration (`f89ef9eb38c5`). `action_items` shipped in the initial
revision with **no unique constraint of any kind**, so nothing keyed a row for
upsert and nothing prevented two rank-1 rows on one scan. Added `source` (enum),
`source_key`, `generated_by`, and `uq_action_items_scan_source_key`. Both key
columns are `NOT NULL` rather than one nullable `check_key`, because Postgres
treats NULLs as distinct inside a unique constraint — a key with a nullable
member enforces nothing on exactly the rows it exists to protect. `generated_by`
follows `prompt_sets.generated_by`; the sentiment path, which records no model
at all and leaves a changed judge unattributable, is the precedent *not* followed.

**Epic 4.0's enum guard picked up `action_item_source` automatically** — 22 enum
columns checked, was 21 — and verified its CHECK constraint against a migrated
database without anyone touching that file. Same outcome Epic 6 recorded.

### Live verification

`scripts/verify_fixes.py`, one live `claude-opus-5` call against
`scan_01M0HDRGJNWNZDSJPP0NC3SV8W` in `avp_dev`. §7's criterion is *"for a test
scan with known gaps, the generated fix list correctly names those gaps with
actionable language"*; the known gaps are citation_strength (3.70 against a
weight of 20 — a 19.26-point gap, the largest on the scan) and two audit
warnings, `NO_FAQ_SCHEMA` and `NO_PRODUCT_OR_SERVICE_SCHEMA`.

- **PART 3** — all five candidates named, zero rejected, zero internal
  identifiers quoted, zero banned-claim hits. "Actionable" is not directly
  assertable, so its absence is asserted instead: a list that never names the
  subject domain or a concrete artefact is the abstract list Epic 7 already
  refused to ship, and that fails the script.
- **PART 4** — marked `gap:citation_strength` as `done`, regenerated: **5 rows
  after two generations, not 10**, and the done item's status untouched. The
  persistence policy verified against a real re-run rather than only a unit test.
- **PART 5** — the report projection drops the completed item: 4 of 5 on the
  live payload.

### Screenshots

Both captured from the running app (API on 8000 against `avp_dev`, Next on 3100)
with Playwright, clipped to the fix beat's own section because the fix beat *is*
the comparison: `docs/screenshots/epic8-fixes-before.png` and
`epic8-fixes-after.png`.

The "before" state was produced by setting every action item to `dismissed` —
which `services/report.py` filters out — rather than by deleting the rows, so the
shot exercises the real fallback path rather than an artificial one.

What the pair shows: identical heading (*"5 changes, worth 40.6 points."*),
identical order, identical point chips (`+19.3`, `+17.5`, `+3.8`) — Epic 7's
arithmetic, untouched. And underneath that, five completely rewritten
recommendations, plus the priority and effort shifts:

| # | Epic 7 | Epic 8 |
|---|---|---|
| 1 citation_strength | high · M | high · **L** |
| 2 share_of_voice | **medium** · L | **high** · L |
| 3 sentiment | **low** · M | **medium** · M |
| 4 schema_faq | medium · **M** | medium · **S** |
| 5 schema_product_or_service | medium · **M** | medium · **S** |

Epic 7: *"Make the site the source an answer cites, not just a name it
mentions."* Epic 8: *"Publish and maintain comparison and 'best help desk
software' pages on helpscout.com that AI answers can cite in place of
third-party roundups"* — with a detail naming the 3-of-45 citation split and
`eesel.ai`, `featurebase.app` and `hiverhq.com` as the pages currently supplying
the evidence.

### Dependencies

**None added.** `anthropic` has been a dependency since Epic 2 and is already
recorded. No licence audit change.

### IP-safety self-check (constraint 9)

**1–5** — no new screen. The fix beat is Epic 7's, designed from the data model;
Epic 8 changed the text inside `FixList` and added one explanatory paragraph, on
design-system tokens only, asserted by a rendered-markup check for raw hex/rgb.

**6 — dependencies.** Nothing added.

**7 — facts only.** The load-bearing one this epic, verified four ways:
- **The prompt carries facts only.** Every interpolated value is a name, domain,
  label, code or number. `FixFacts` has no field capable of holding page copy, an
  engine answer, a citation title or a competitor description, and it is not an
  ORM model. Enforced by three guards of different kinds and by 11 negative
  controls, two of which hardened the guards that let a near-miss through.
- **`ActionItem` holds free text, and that is the documented exception.** It is
  deliberately *not* in `FACTS_ONLY_MODELS`, and `test_action_item_is_the_
  documented_free_text_exception` records that as a decision rather than an
  omission. These are our own recommendations about our own client's site,
  generated from our own measurements — not scraped material.
- **No third-party prose can reach the page through the new path.** The output
  schema is exact-field-locked; `ActionItemOut` is swept; `machine_code_in()`
  stops even our *own* internal codes reaching a client, and
  `banned_claim_in()` stops claims this product cannot support — the same
  vocabulary the rendered-page sweep uses, asserted equal so the two cannot
  disagree.
- **The generator cannot read what does not exist.** Source inspection over both
  new modules for `response_text`, `answer_text`, `.snippet`, `text_extract` and
  friends. Epic 4 stored a digest instead of the answer and `Citation` has no
  title column, so there is nothing to read; the guard makes that intent explicit
  rather than incidental.

**8 — no verbatim competitor marketing copy.** The generator is never shown any.
Competitors reach the prompt as **name and domain only**, which ip-safety.md #7
permits as "names of entities mentioned". Cited sources reach it as **domain and
count**. Every string in the new system prompt, schema descriptions and UI
paragraph was written for this epic from the data model.

**IP-safety check passed:** the prompt input carries only facts (names, domains,
labels, check codes, counts and scores), verified by source inspection, a
categorical whitelisted-line sweep, a typed field sweep and 11 negative controls;
`ActionItem`'s free text is our own generated recommendation and is the
documented, tested exception rather than a gap; no competitor prose, engine
answer, citation title or page copy can reach the prompt, the database, the API
or the page; and generated copy is blocked from carrying either an unsupportable
claim or one of our own internal identifiers.

### Tests

**723 total, up from 635.**

| suite | before | after | delta |
|---|---|---|---|
| api | 451 | 515 | +64 |
| workers | 13 | 13 | — |
| shared-types | 45 | 53 | +8 |
| design-system | 72 | 72 | — |
| web | 54 | 70 | +16 |

The api +64 breaks down as `test_fix_generator.py` 52 (new), `test_ip_safety.py`
+11 (56 → 67), and `test_enum_constraints.py` +1 (21 → 22) — that last one
picked up by a guard written two epics ago, covering a column that did not exist
then. web +16 is `derive.test.ts` +9 and `ReportView.test.tsx` +7, both built
against `generatedFixesReport`: real generated copy from a live call, checked in
under the same discipline as `helpscoutReport`.

---

## 2026-08-23 — Epic 3.6 · Competitor manual override, and the half of it that never worked

The oldest open item in the product, and the only write in an otherwise
read-only surface. §7's Epic 3 checklist lists **"Manual override/edit UI for
competitor list"** — a UI item, which is the part that was missing.

### What Epic 3 already had

More than expected, and worth stating precisely because the brief for this epic
assumed greenfield:

| Piece | State before this epic |
|---|---|
| `Competitor.is_manual_override` column | ✅ shipped Epic 1 |
| `persist_detection` preserve-on-re-detection | ✅ shipped Epic 3 |
| `PUT /clients/{clientId}/competitors` | ✅ **shipped and documented** Epic 3 |
| `ReplaceCompetitorsRequest` / `CompetitorInput` | ✅ shipped Epic 3 |
| Tests for the override | ✅ four, incl. survives-re-detection |
| `isManualOverride` on the report payload | ✅ inherited via `CompetitorOut` |
| **Any way for a human to reach it** | ⬚ **nothing** |

`api-contracts.md` also carried `PATCH /api/v1/scans/{scanId}/competitors` in
its "Planned, not yet built" table. That was **a stale duplicate**, not an open
gap — different verb, different resource, and the PUT that actually does the job
had shipped and been documented forty lines above it. Building the PATCH would
have added a second write path to one resource, which is precisely the
half-applied state `ReplaceCompetitorsRequest`'s own docstring rejects. Row
deleted rather than implemented.

So the epic reduced to: build the UI, and prove the mechanism works. Proving it
is where the epic actually went.

### Three defects, none of which the test suite could have found

**1. Removals silently reverted.** `persist_detection` preserved operator
*additions* correctly — it keeps rows flagged `is_manual_override` and skips
re-offering any candidate matching a manual name or domain. But a rival the
operator *struck* left no row and therefore **no record**. On the next run it
matched no override and came straight back.

Epic 3's docstring promises *"an operator who has corrected a bad set must not
have their correction silently undone by the next run."* It held for exactly
half of what correcting a set means:

| Operator action | Survived re-detection, before this epic |
|---|---|
| Add a rival detection missed | ✅ |
| Keep a rival detection found | ✅ |
| **Remove a rival that is wrong** | ❌ **it came back** |

`test_manual_entries_survive_redetection` had passed since Epic 3 because it
only ever asserts an *addition* survives. No test at any level had struck a
competitor and re-detected. This epic's own negative controls did not find it
either — controls 7 and 8 broke *preservation*, which is the half that worked.

It was found by `scripts/verify_competitor_override.py`, on its first run
against real data, and the failure line was unambiguous:

```
FAIL  re-detection reinstated Thecxlead, which the operator had struck
FAIL  scoring still compares against Thecxlead
```

**Fixed** with `Competitor.is_suppressed` (migration `bc32281a20c5`). A struck
rival is kept as a **tombstone**: `is_manual_override=True, is_suppressed=True`,
which puts it in `persist_detection`'s `manual` partition so its key blocks the
candidate from being re-offered. Tombstones are excluded from every read path
via a new `CompetitorSet.active_competitors`, and take tail ranks so they never
push a real rival down the list.

`active_competitors` exists because **seven** call sites iterate the collection
— scoring, the report (×2), prompt seeding, engine extraction, fix generation
and the API. A filter copied seven times is a filter that eventually disagrees
with itself, and the failure mode is silent: a missed filter means a struck
rival reappears in exactly one surface, which is worse than all of them because
nobody would believe the bug report. `test_the_set_has_one_definition_of_which_
competitors_are_real` asserts by source inspection that no module reads the raw
collection.

**2. The verification script tested its own copy of the code.** The first
version of `verify_competitor_override.py` reimplemented the endpoint's logic
inline rather than calling it. It passed its own PART 2 while the real endpoint
was still deleting struck rivals without recording them — the script and the
code under test were two copies of the same mistake, which is Epic 4.0's
compare-a-thing-to-itself trap in a new costume.

Fixed by extracting the write logic out of the router into
`services.competitors.apply_override`, so there is **one** implementation for
the endpoint, the script and any future worker to share. That is where it
belonged anyway: it is the write half of the contract `persist_detection` reads,
and a rule enforced in a request handler is a rule no script can reach.

**3. `PUT` was blocked by CORS, so the endpoint had never been reachable from a
browser.** Found by driving the real UI with Playwright: the save did nothing,
and the API log showed

```
"OPTIONS /api/v1/clients/{id}/competitors HTTP/1.1" 400 Bad Request
```

`main.py` listed `allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]`.
The list was written when the API had no PUT, the competitor override is the
only PUT in it, and nothing in a browser called it until this epic — so the
preflight had never been issued in five epics.

The suite could not have caught it: tests drive the app through httpx's
`ASGITransport`, which calls the application directly and **never runs a
preflight**. Every CORS failure is invisible to it by construction. So the guard
in the new `tests/test_cors.py` is on the configuration, not on a response, and
it is derived from the registered routes rather than hand-listed — hand-listing
is how PUT went missing in the first place.

Same shape as the three defects above, and worth naming: **each was invisible to
the layer that was being tested, and visible immediately at the layer that was
not.** Epic 5.1's float, Epic 7.0's crowded-out fixes, Epic 8's leaked detail
codes, and now these. The pattern is not that the tests were bad; it is that a
test exercises the seam it was written for and nothing else.

### Persistence: `apply_override` and `persist_detection` are one contract

Epic 3's docstring is quoted rather than restated because it already says the
thing that matters, and this epic did not change it — it made it true for
removals as well as additions. What changed is that a correction now has two
halves that must agree about what an override *is*, so they live in one module:

- `apply_override` (write) turns an operator's list into live rows plus
  tombstones for anything struck, and clears `detection_confidence`.
- `persist_detection` (read/merge) treats every `is_manual_override` row —
  tombstones included — as untouchable, and appends detected candidates below
  them.

The wholesale-replacement semantics are unchanged, and the UI states them
plainly rather than hiding them: *"Saving replaces the whole list and marks
every entry on it as set by hand, including ones you did not change."* An
operator should not discover that from a badge appearing on rows they never
touched.

### Known and open: corroboration over a mixed set

`apply_override` clears `detectionConfidence`, but a later re-detection sets it
again from its own outcome — and that outcome measured agreement across only the
rows detection found, not the operator's. The report then presents one
corroboration figure for a set that is part-detected and part-hand-set.

Deliberately **not** fixed here. Changing it means redefining what the field
measures, which is an Epic 3 scoring-semantics decision rather than a UI one.
Epic 3.6's badge is what made it visible; `verify_competitor_override.py` prints
it as a `NOTE` rather than failing on it, so it stays on the record.

### Negative controls — 12 injected breakages, 12 caught

Eight before the suppression fix, four after it, plus one on the CORS guard.
Every control ran the **full** suite rather than a hand-picked expected test —
Epic 8's harness reported a false NOT CAUGHT by doing the latter.

| # | Injected breakage | Caught by |
|---|---|---|
| 1 | free-text `note` field added to `CompetitorInput` | exact-field lock + 2 |
| 2 | `note` added AND copied onto the row by the writer | exact-field lock + 3 |
| 3 | `max_length` dropped from `CompetitorInput.name` | the bounds guard |
| 4 | `description` column added to `Competitor` | Epic 3's table sweep + 3 |
| 5 | prose-bearing schema added to the competitor module | the module sweep |
| 6 | writer stops reading the operator's domain | positive source assertion |
| 7 | `persist_detection` stops preserving overrides | Epic 3's own test |
| 8 | re-detection preserves rows but clears the flag | Epic 3's own test |
| 9 | `suppression_reason` free-text column added | the boolean guard + 3 |
| 10 | `is_suppressed` widened from boolean to `String` | the boolean guard + 3 |
| 11 | report reverts to the raw competitor collection | the one-definition guard + 1 |
| 12 | `persist_detection` stops honouring tombstones | two suppression tests |
| — | `PUT` removed from `allow_methods` again | both CORS guards |

Two are worth calling out. **7 and 8 were caught by Epic 3's own
`test_manual_entries_survive_redetection`** — the preservation guarantee this
epic depends on was already genuinely guarded, which is what made the *absence*
of a removal guarantee the only real gap. And the six new suppression tests were
run against Epic 3's delete-and-forget persistence before being trusted: two
fail against it, and eight fail once tombstones stop being filtered.

Every source file was diffed against its pre-injection backup after each
control. That check earned its keep: the harness initially restored two files
from *pre-refactor* snapshots, leaving the tree in a state where two tests
failed for reasons unrelated to any control. Caught by the diff, not by the
test output.

### Live verification

`scripts/verify_competitor_override.py`, against the real Help Scout set in
`avp_dev`. Costs nothing — detection is not re-run; a `DetectionOutcome` is
constructed and handed to the real `persist_detection`, so no SerpApi search and
no model call. Everything else is the real service path.

Strikes `Thecxlead` (SERP-only, uncorroborated, a listicle publisher rather than
a help-desk vendor — the case an operator would actually correct), adds
`Intercom`, then re-detects with an outcome that deliberately re-offers the
struck rival plus a new one. PART 4 asserts the comparison count **before**
checking membership, because `all()` over an empty list is true and an empty
comparison list is exactly the failure that part exists to detect.

The database is restored and the restore is **verified**, not assumed — Epic 7's
and Epic 8's screenshots and the checked-in report fixtures are taken from this
scan, and a set left corrected would silently invalidate them. PART 6 compares a
full snapshot including row ids.

### Screenshots

Captured from the running app with Playwright, driving the interface end to end
— open the editor, strike a rival, add another, click save — rather than writing
rows and re-rendering. A screenshot of a state the UI never produced would prove
only that the renderer works. It is also how defect 3 surfaced.

`docs/screenshots/epic36-competitors-detected.png`,
`epic36-competitor-editor.png`, `epic36-competitors-corrected.png`. The database
is snapshotted and restored around the capture, verified identical.

### Dependencies

**None added.**

### IP-safety self-check (constraint 9)

**1–5** — no new screen. The editor is operator chrome inside Epic 7's proof
beat, built entirely from design-system primitives (`Button`, `TextField`,
`Badge`) and role tokens; no raw hex or `rgb()` reaches the markup, asserted by
the existing sweep. `Badge` is used for system state (provenance), never for a
score value, per its own docstring.

It is passed to `ReportView` as a **slot** rather than built into it, for two
reasons: `ReportView` is rendered with `renderToStaticMarkup` and stays
server-renderable for the PDF path, which a hook would break; and a report
rendered for a client carries no editor because no slot is passed. The report
stays a document.

**6 — dependencies.** None added.

**7 — facts only.** This epic opens the first **user-facing write path** into a
competitor row, which is a new kind of exposure: an operator typing into a form
is the one input to this system that no upstream extractor has already reduced
to facts. Epic 3's guards covered the tables and the read schemas — everything
that existed when detection was the only writer. Five new guards cover the
request side:

- `CompetitorInput` is **exact-field locked** to `{name, domain}`, and
  `ReplaceCompetitorsRequest` to `{competitors}`. Locked by equality rather than
  a forbidden list, because the risk runs in the direction of *adding* a field
  and nobody names it `tagline` — they name it `note` and mean the same thing.
- Both fields are length-bounded to their column widths. An unbounded "brand
  name" box is a free-text field with extra steps.
- A module-wide sweep of `schemas/competitor.py`, with a coverage floor.
- Source inspection of the writer, with **positive** assertions that `item.name`
  and `item.domain` are what reach the row.
- `is_suppressed` must stay a `Boolean`. The obvious next field is *why* the
  operator removed a rival, and the honest answer is usually a sentence about a
  competitor — the exact third-party characterisation this rule keeps out.

**8 — no verbatim competitor marketing copy.** A manually added competitor is a
name and a domain, identical to a detected one; the model has no column that
only an override could populate, and that is asserted. All new UI copy was
written for this epic.

**IP-safety check passed:** the new write path can carry only an entity name and
a domain, enforced by exact-field locks, length bounds, a module sweep and
source inspection of the writer, with five negative controls run against them;
a suppression records the fact and never the reason; the report's new provenance
badge reads a boolean column, not prose; and the editor is built from
design-system primitives with no off-system colour value.

### Tests

**748 total, up from 723.**

| suite | before | after | delta |
|---|---|---|---|
| api | 515 | 533 | +18 |
| workers | 13 | 13 | — |
| shared-types | 53 | 53 | — |
| design-system | 72 | 72 | — |
| web | 70 | 77 | +7 |

api +18: `test_competitor_endpoints.py` +8 (2 downstream-contract, 6
suppression), `test_ip_safety.py` +7, `test_cors.py` 3 (new). web +7, all in
`ReportView.test.tsx` against a new `manualOverrideReport` fixture.

**One test was removed rather than fixed.** A downstream assertion that
re-scoring compares against the corrected set passed, and passed for a bad
reason: detection and the scan pipeline create separate scans, so the set under
test had no answered engine results, `compare_competitors` short-circuits to
`[]` in that case, and the assertion was `all(... for c in comparisons)` —
vacuously true over an empty list. Tightening it to assert the count first is
what exposed that. Making it real in the unit suite would have meant rebuilding
`test_report_endpoint.py`'s whole engine-stub pipeline to produce one scan
carrying both, so it lives in the live verification instead, against a scan with
six real answered results. The removal and its reason are recorded in the test
file itself, where the next reader will look.

---

## 2026-08-23 — Epic 3.7 · Closing what 3.6 left open

Three loose ends, deliberately not folded into Epic 3.6. Two are closed here;
one is a recommendation rather than an implementation, and the reason is the
finding.

### 1. `detectionConfidence` now has a home — and so does the register it needed

**Verified against source before doing anything**, rather than trusting Epic
3.6's summary. `decide_detection` computes `confidence = corroborated /
len(top)` over `top`, the detected candidates; manual rows are never in `top`.
`apply_override` clears it to null — correct. `persist_detection` sets it again
**unconditionally** from the outcome, and `services/report.py` passes it
straight through. The description was accurate.

**The interesting part was step 1's other question: where do known-open items
live in this repo? The answer is nowhere structural.** The convention is to
name a finding inside a `build-log.md` entry and re-cite the name in later
ones. That has worked for Epic 2's Finding 2 — it appears in five build-log
entries and in `api-contracts.md` — but it works because it got quoted often
enough to stay visible, not because anything holds it. It does not generalise,
and `detectionConfidence` is the proof: it had a paragraph and a script `NOTE`,
and no name at all.

Added an **Open findings** register to `api-contracts.md`, deliberately
separate from "Planned, not yet built" — that table is work not started, this
is shipped behaviour that is wrong. Seeded with all three findings so it is a
register rather than a one-item note, and **Finding 3** is now cited from the
three places that carry it, per the register's own rule:
`services/competitors.py` at the write, `schemas/report.py` at the field, and
`verify_competitor_override.py`'s `NOTE`.

Two things were recorded more honestly than expected while writing it:

- **Finding 1 is marked improved, not fixed.** Epic 2.8's own "Still imperfect"
  section records that Ooni stayed variable between runs and some labels stayed
  a level broader than ideal. The four stable `b2b saas` / `venture capital`
  failures were genuinely fixed. A finding marked ✅ stops being looked at, so
  it is ◐.
- **The SERP-only gating idea at build-log Epic 3.0 is not in the register.**
  It was recorded there as "not implemented, recorded for a decision", and
  Epic 3.5 subsequently implemented it — with the honest conclusion that it
  "largely did not work". That is a closed experiment, not an open defect, and
  putting it in would make the register a list of everything ever considered.

**Not fixed, deliberately.** What `detectionConfidence` should mean once a set
is part-detected and part-hand-set is a product decision about the field's
semantics, with at least three defensible answers (clear it on any override;
report it alongside the row count it covers; make it per-row). Each changes
what Epic 5 may infer. Out of scope for a cleanup epic, and the register says
so with the options written down.

### 2. Live-verification scripts in CI — recommended, not built, and here is why

**There is no CI.** No `.github/`, no Makefile, no workflow file anywhere; the
only YAML in the repo is `infra/deploy/docker-compose.yml` and the pnpm
lockfiles. The three references to "CI" in this log describe intent, not a
running system. Per the brief, that means recommend and stop — building one is
a larger decision than a cleanup epic should carry.

Costs, taken from each script's own docstring:

| script | epic | cost |
|---|---|---|
| `verify_competitor_override.py` | 3.6 | free |
| `verify_report.py` | 7 | free |
| `verify_audit.py` | 6 | unpaid, but hits three live third-party sites |
| `verify_fixes.py` | 8 | one `claude-opus-5` call |
| `verify_intake.py` | 2 | crawl + Anthropic (`--crawl-only` skips the model) |
| `verify_scan.py` | 4 | ~48 model calls at 24 prompts |
| `verify_scoring.py` | 5 | real SerpApi + Claude to produce data to score |
| `verify_competitors.py` | 3 | ~6 SerpApi + ~4 model calls **per URL**, ×10 URLs |

**The substantive finding: none of the free scripts is CI-runnable as written**,
which is why "just wire in the free ones" is not the small task it looks like.

- `verify_competitor_override.py` is hard-coupled to one hand-built row. Its
  module constant is `REMOVE = "Thecxlead"`, and it bails at line 126 —
  *"Thecxlead is not in this set; nothing to correct"* — against any database
  that does not contain that exact competitor, plus line 129 if the set already
  carries overrides.
- `verify_report.py` needs a succeeded, scored scan and returns 1 without one.
- `verify_audit.py` audits `helpscout.com`, `anthropic.com` and `basecamp.com`
  over the network, so it fails on a third party's downtime or markup change.

All three assume the hand-built `avp_dev`. **The blocker is not CI; it is the
absence of a seed fixture**, and building one is real work with a real design
question behind it — a fixture faithful enough to be worth running against is
close to a second implementation of the pipeline's output.

**Recommendation, concrete enough to act on:**

1. **First, and independently of CI:** a `scripts/seed_dev.py` that builds the
   `avp_dev` state these scripts assume — one scored scan with competitors,
   engine results and an audit — from committed fixtures, with no provider
   calls. This is the prerequisite for everything below, and it is worth having
   on its own: today that state exists only because it was built by hand across
   Epics 3–8 and has been carefully restored by every script that touches it.
2. **Then**, on every push: the existing `pytest` / `pnpm test` suites plus
   `ruff`, `tsc`, and `alembic upgrade head && alembic check` against a service
   container. Roughly 2–3 minutes, no API cost. This is the bulk of the value
   and needs no seed data.
3. **Then**, nightly against a seeded database: `verify_report.py` and
   `verify_competitor_override.py`. Free, and this is where the regression
   coverage Epic 3.6 deferred to a script actually lands.
4. **Weekly or on demand:** `verify_audit.py`, isolated so a third party's
   downtime does not redden the main pipeline.
5. **Never automatic:** `verify_fixes`, `verify_intake`, `verify_scan`,
   `verify_scoring`, `verify_competitors` — each spends real money, and
   `verify_competitors` at ~100 API calls per run is the outlier. This matches
   the existing convention that costed operations are explicitly
   operator-triggered: detection, scanning and fix generation are all
   POST-triggered rather than implicit, for the same reason.

Nothing above is implemented. Recorded so the decision is a decision rather
than a silence.

### 3. Configuration audit — one finding, five clean

Prompted by the CORS bug taking five epics to notice. Every check below was
derived from source rather than read, because reading is what missed it.

**FOUND — `allow_methods` was over-permissive.** Registered methods across
every route are `GET, HEAD, POST, PUT`. The list allowed `PATCH` and `DELETE`
as well, left over from a hand-written value. Epic 3.6's guard could not see it:
it asserted registered ⊆ allowed, which only catches a route a browser cannot
reach, and this is the same drift pointing the other way.

**Fixed by deleting the list, not correcting it.** `main.py` now derives
`allow_methods` from `app.routes` (`_registered_methods`), so it cannot drift
in either direction. Routers are registered before the middleware is added,
which the docstring says plainly because the ordering is now load-bearing.

Tightening the value by hand was the alternative and was rejected: `PATCH` and
`DELETE` are both on the roadmap (Epic 1.5's `DELETE /users`, Epic 7.1's
`PATCH /branding`), so a hand-tightened list would re-create the exact trap
that hid the missing PUT — a correct list that nobody revisits when a route is
added. The test moved from subset to **exact equality**, with `OPTIONS`
whitelisted as the one member that is legitimately allowed without being
registered, since the middleware answers preflight before routing.

**Clean — reported because a negative result is a result:**

| Surface | Checked by | Result |
|---|---|---|
| `ProblemError` subclasses → `_STATUS_SLUGS` | introspection | 9 subclasses, 0 missing |
| `ids` prefix constants → `ALL_PREFIXES` | introspection | 16 constants, 0 missing |
| Mapped models → `models.__all__` | mapper registry | 16 mapped, 0 missing |
| Error handlers vs raised exceptions | source | base-class handler covers all 9 — no drift possible |
| Cookie hardening vs its comment | source | `_harden_deployed_environments` genuinely forces `session_cookie_secure=True` and rejects the placeholder secret |

Two things were found that are **not** drift and were left alone. `RateLimited`
is defined in `errors.py` and raised nowhere — dead code for an unimplemented
feature, not a list that has drifted. And there is no security-headers
middleware (CSP, HSTS, `X-Frame-Options`); that is an absence rather than a
mismatch, and adding one is new work this brief excludes. Both are recorded
here rather than fixed, so the next person does not have to rediscover them.

`allow_headers` (`Content-Type, Accept, X-Requested-With`) against a client
that sends only `Content-Type` is technically broad by two, but `Accept` is
CORS-safelisted regardless and header breadth carries none of the consequence
method breadth does. Not changed.

### Negative controls — 5/5 caught, after one control was rewritten

| # | Injected breakage | Caught by |
|---|---|---|
| 1 | hand-written list reinstated, missing PUT (the Epic 3.6 bug) | exact-equality + named PUT test |
| 2 | hand-written list reinstated, over-permissive by PATCH/DELETE | exact-equality |
| 3 | `OPTIONS` dropped from the derived list | the preflight test |
| 4 | `HEAD` stops being filtered out | exact-equality |
| 5 | CORS middleware moved above the router registration | exact-equality + named PUT test |

**Control 5 reported NOT CAUGHT on the first attempt, and the control was
wrong, not the guard.** It injected a *second* `CORSMiddleware` before the
routers while leaving the original in place. Starlette's `add_middleware`
inserts at position 0, so the helper that reads the middleware kwargs found the
later-added correct one and the test passed. A duplicate middleware is not a
mistake anyone makes. Rewritten to *move* the single existing block — which is
the realistic careless refactor — the derived list collapses to
`['GET', 'OPTIONS']` and two guards fire immediately.

Worth recording because it is the third time in four epics that a control has
mis-reported: Epic 8's harness scored against a hand-picked expected test, Epic
3.6's restored two files from stale backups, and this one modelled an
unrealistic failure. **The guards have been right every time; the harness has
been wrong three times.** A negative control is itself code, and nothing checks
it.

`main.py` was diffed against its pre-injection backup after the run and is
byte-identical.

### Dependencies

**None added.** No new packages, no CI system, no security middleware.

### IP-safety self-check (constraint 9)

Config-facing changes only; no UI changed and no screen was touched. Relevant
constraints:

**6 — dependencies.** None added.

**7 — facts only.** Nothing in this epic touches what is stored, returned or
rendered. The three code changes are a comment citing Finding 3 at the
`detection_confidence` write, the same on the report schema field, and the
derived CORS method list. `detectionConfidence` remains a `Decimal | None`
carrying a ratio; no field gained the ability to hold prose, and the register
entry deliberately records the *shape* of the problem without adding any
explanatory field to the API to compensate for it.

The CORS change **narrows** what a browser may do — `PATCH` and `DELETE` are no
longer advertised — so it cannot widen any surface through which third-party
content could reach the product.

**IP-safety check passed:** no dependency added; no stored, returned or
rendered field changed shape or gained prose capacity; the only behavioural
change narrows the CORS method surface rather than widening it; and the new
open-findings register records a known imprecision in a number rather than
introducing a free-text field to explain it.

### Tests

**749 total, up from 748.**

| suite | before | after | delta |
|---|---|---|---|
| api | 533 | 534 | +1 |
| workers | 13 | 13 | — |
| shared-types | 53 | 53 | — |
| design-system | 72 | 72 | — |
| web | 77 | 77 | — |

`test_cors.py` 3 → 4: the subset assertion became exact equality inside the
existing test, and `test_preflight_is_advertised_even_though_no_route_declares_it`
is new — `OPTIONS` is the single hand-added member of an otherwise derived
list, and therefore the one a tidy-up could remove without noticing.

A one-test epic is the honest outcome. Two of the three loose ends were closed
with documentation and a deletion; the third was closed by removing a
hand-maintained list, and a list that no longer exists needs less testing than
one that does, not more.

---

## 2026-08-23 — Epic 3.8 · A seed fixture, the regression it exposed, and the guard that did not guard

Epic 3.7 recommended a seed script as the prerequisite for ever running the free
verification scripts automatically. This builds it. It also found that one of
those two scripts had been failing against the real dev database since Epic 8,
fixed that, and then found that the first fix did not work — by adversarially
auditing it rather than by trusting the negative control that had already passed.

### What the investigation found before any code

Epic 3.7 read the scripts' docstrings, because it was costing them. This epic
read the scripts, because it had to run them, and the bail-out conditions turned
out to matter more than the costs:

| Script | Will not proceed without |
|---|---|
| `verify_report.py` | a `SUCCEEDED` scan on a client whose domain is not the degraded one; an `Agency` for PART 3; and, if scored, weights summing to 100 with the breakdown re-summing to the composite within 0.01 |
| `verify_competitor_override.py` | any `CompetitorSet` — **selected by `order_by(CompetitorSet.id.desc())`**; a competitor in it named exactly `REMOVE`; no row already carrying `is_manual_override`; and a **non-empty** comparison list from `score_scan` for PART 4 |

Two details shaped everything after. The override script picks the **newest**
competitor set by ULID, so a freshly-seeded set becomes its target with no code
change — the seed does not have to be told about the script, or the script about
the seed. And PART 4 needs a non-empty comparison list, so the seed cannot be a
competitor set alone: `compare_competitors` short-circuits to `[]` without
answered engine results, which is the same vacuity trap Epic 3.6 fell into.

Also recorded: `verify_report.py` is **not read-only**. PART 3 self-seeds a
degraded client and scan and commits them.

**`tests/conftest.py` cannot be reused, and nothing is duplicated by not reusing
it.** Its session-scoped `engine` runs `drop_all` + `create_all` and its autouse
`_clean_state` runs `TRUNCATE … CASCADE` over every table — pointed at `avp_dev`
it would erase it. It is pytest-only, and its data seam is `monkeypatch` on
service functions. The one piece of logic worth not reimplementing is the
scoring computation, and the seed avoids that by calling the real
`scoring_runner.score_scan`, exactly as `verify_report.py`'s own PART 3 does.

**One dataset serves both.** Their needs nest rather than diverge.

### What `scripts/seed_dev.py` produces

An Agency, Client, one Scan, a PromptSet with 2 prompts, 4 EngineResults with
BrandMentions and Citations, a CompetitorSet with 3 competitors, a
TechnicalAudit with 12 checks, and 2 ActionItems. Then it calls the real scorer.

**The Score is computed, never written.** `verify_report.py` asserts the
breakdown re-sums to the stored composite. Hand-authoring a Score would turn
that assertion into a test of my arithmetic instead of the scorer's — green, and
measuring nothing.

**Everything is obviously synthetic.** `Seedwell Supply` at
`seed-fixture.example`; every competitor and cited domain also `.example`, a
reserved TLD (RFC 2606) that can never resolve. It keeps a real company out of
committed fixture data, and a reader who sees `seed-fixture.example` knows
immediately they are not looking at a measurement.

**Minimum viable, not a replica** — 2 prompts against the real scan's 3, 3
competitors against 5, 12 audit checks against 17. That is a real limitation,
and it bit immediately; see below.

### `REMOVE` became configurable rather than seeded

Two ways to make `verify_competitor_override.py` work against seeded data: name
a seeded competitor "Thecxlead", or make the constant configurable. Seeding the
name was rejected — it would put a real third-party brand into synthetic data to
satisfy a constant, and would quietly make the script's own claim that it runs
against real output "not a fixture" false without changing a word of its
docstring. `REMOVE` / `ADD_NAME` / `ADD_DOMAIN` now read from
`AVP_OVERRIDE_STRIKE` / `AVP_OVERRIDE_ADD` / `AVP_OVERRIDE_ADD_DOMAIN`,
defaulting to the originals.

### The RESULT line was lying, and reading the output is what caught it

After the first green seeded run the script printed *"PASS — an operator's
correction to a **real** competitor set survived a real re-detection…"*. It was
three invented companies at `.example` domains written thirty seconds earlier.
The wording was inherited from Epic 3.6, when only one kind of data existed, and
became false the moment a second kind did.

It now detects a synthetic subject by its reserved TLD, says so in PART 1
(*"SEEDED, synthetic — the persistence guarantee is exercised; the data behind
it is not real"*), and the PASS line reads "a SEEDED competitor set" with a note
on what a seeded run does and does not prove.

### The regression: `verify_report.py` had been failing since Epic 8

Proving Help Scout unaffected meant running both scripts against `avp_dev`.
`verify_competitor_override.py` passed. `verify_report.py` did not:

```
FAIL  report payload contains prose-bearing keys: ['"title"']
```

**Not caused by this epic** — `git` confirms `verify_report.py`,
`schemas/report.py` and `services/report.py` untouched since the Task 1 commit.
All five `"title"` keys were in `actionItems[*].title`.

Epic 8 added `actionItems` to `ReportOut`. `verify_report.py`'s
`FORBIDDEN_SUBSTRINGS` was written in Epic 7, when every string in a report was
somebody else's, and forbids `"title"`. An ActionItem is the one thing in that
payload that is **ours**, and it is the documented exception —
`models/action_item.py` says so, `schemas/action_item.py` lives in its own module
precisely so `schemas/report.py`'s sweep does not see it, and
`test_ip_safety.py::test_action_item_is_the_documented_free_text_exception`
asserts it stays out of `FACTS_ONLY_MODELS`.

The payload was right; the check was stale — **a hand-written list that drifted
from what the code requires, the third instance of that defect class in four
epics** (CORS missing PUT, CORS carrying unused PATCH/DELETE, now this).

It survived Epic 8 because nothing runs these scripts. It survived Epic 3.7's
config audit because that audit swept application configuration, not script
constants. And **it passed against the seeded database while failing against the
real one**, because the seed produced no action items.

### The first fix did not work, and the negative control that "proved" it was wrong

The fix lifted `actionItems` out of the sweep and asserted the exemption's edges
by comparing each item's keys against `{to_camel(f) for f in
ActionItemOut.model_fields}`, looking for a surplus. Two negative controls were
run and both caught their injection, so it was called done.

An adversarial audit of the change disagreed, and it was right.

**The surplus check was a tautology.** The items are serialised *by*
`ActionItemOut` and compared *against* `ActionItemOut`, and `ApiModel` inherits
pydantic's `extra='ignore'`, so an undeclared key can never appear in the
payload. A prose field *added to the schema* lands on both sides and cancels.
The comment above it claimed "a field added there shows up here rather than
slipping through the gap this pop() opens" — provably false.

**And the negative control had tested the wrong thing.** It injected
`competitorBlurb` into the parsed dict *after* serialisation, which the check
does catch. The realistic failure mode is a field added to the schema. Verified
directly: adding `summary: str | None` and `competitor_snippet: str | None` to
`ActionItemOut` and re-running produced **PASS**.

That is the fourth harness failure in five epics — Epic 8's selector scored
against a hand-picked test, Epic 3.6's restored from stale backups, Epic 3.7's
modelled a mistake nobody would make, and this one injected at the wrong layer.
**The guards have been right nearly every time; the harness has been wrong four
times.** A negative control is code, and nothing checks it.

Three further defects the audit surfaced in the same change:

- Five forbidden names lost coverage inside the exemption, and `summary` and
  `raw_response` were backstopped by nothing else in the repo.
- Both replacement checks read **top-level keys only**, while the sweep they
  replaced searched raw JSON at any depth — turning a one-field exemption into
  an arbitrarily deep one. `evidence: [{"snippet": …}]` would have passed.
- When a scan has no action items the whole block was skipped silently, with no
  output and no failure, and the script still printed "the payload carries facts
  only". In this epic's own seed-then-verify workflow that was the **guaranteed**
  case.

**Rebuilt.** The check is now a **recursive substring sweep** over every key at
every depth under each action item, against the forbidden vocabulary the repo
settled on in Epic 8 — substring, because nobody names a leak field `snippet`,
they name it `competitor_snippet`. `title` and `detail` are exempted by name.
It counts what it inspected and fails if that is zero, so a vacuous pass is
impossible. The misleading byte count and the overclaiming provenance line are
gone.

Four negative controls, all caught, and this time at the layer that matters:

| Injected | Caught |
|---|---|
| `description` on a competitor row (outside the exemption) | `LEAKED: ['"description"']` |
| **`summary` + `competitor_snippet` added to `ActionItemOut` — the mode that defeated the first fix** | `actionItems.summary looks like prose (summary)…` |
| `competitorBlurb` after serialisation | `actionItems.competitorBlurb looks like prose (blurb)` |
| **`evidence: [{"snippet": …}]` nested two levels deep** | `actionItems[0].evidence[0].snippet looks like prose (snippet)` |

**And the seed was changed too**, because the deeper problem was that a fixture
omitting a branch cannot verify it. `seed_dev.py` now writes 2 ActionItems, so
the exemption boundary is exercised against seeded data — 30 keys inspected —
rather than silently skipped. `_clear_children` deletes them on re-run;
`ActionItem` carries `UniqueConstraint(scan_id, source, source_key)`, so without
that a second seed would have failed.

### The bigger finding: the override script was destroying the data it promised to restore

The adversarial audit that caught the tautology also checked blast radius, and
found something that was **already true in `avp_dev`**, not hypothetical.

`apply_override` replaces a competitor set by **hard-deleting** every
`Competitor` row. `Citation.competitor_id` and `BrandMention.competitor_id` are
both `ON DELETE SET NULL` (`confdeltype=n`, confirmed against the live schema).
So every run of `verify_competitor_override.py` — and every real operator
override — nulls the attribution on every citation and brand mention for that
scan. PART 6 restores the competitor rows with their original ids, but nothing
points at them any more, and PART 6 never looked.

Measured in `avp_dev`: **45 citations and 14 non-subject brand mentions, zero
attributed.** The live report attributed no cited domain to any competitor,
while the checked-in fixture still asserted `front.com → "Front"` and
`zendesk.com → "Zendesk"`. The database and the committed fixture had diverged.

**My own "Help Scout unaffected" proof could not have seen it.** It compared
thirteen row counts and a fingerprint of composite, competitor names, override
count and action-item count. `SET NULL` changes none of those — it nulls a
column on rows that all still exist. The check was structurally blind to the
one kind of damage the script actually does. PART 6's own claim, *"byte-identical
to the starting snapshot, ids included"*, was true only of the competitors table
and had been reassuring everyone since Epic 3.6.

I cannot prove whether this session caused it or merely inherited it. Epic 3.6
ran that script against `avp_dev` several times and drove a real override through
the UI for its screenshots; this session ran it several more. There was no
"before" measurement of link integrity to compare against, because nobody had
thought to take one.

**Three things were done about it.**

`avp_dev` was repaired — links re-derived from domain and name equality, which
is unambiguous on that scan (no domain or name matches two competitors) and
reproduces the committed fixture exactly: `front.com → Front`, `zendesk.com →
Zendesk`, mentions Front 5 / Zendesk 5 / Freshdesk 4.

`verify_competitor_override.py` was fixed to snapshot both child link maps in
PART 1 and restore them in PART 6, and to **fail** if the count does not come
back. Proven both ways: a normal run now reports `links : 16 restored (was 16)`
and leaves the database intact; reverting the re-link to Epic 3.6's behaviour
reports `links : 0 restored (was 16)` and fails.

The **product** path was recorded as Finding 4 rather than fixed. An agency
correcting a competitor set through the UI still destroys that scan's
attribution, and closing it means choosing between re-linking on write, not
hard-deleting rows whose name is unchanged, or changing the FKs — which changes
what "replace the set wholesale" means and belongs with Epic 3's semantics.

The pattern worth naming: **every verification in this epic compared counts, and
the defect was in a column.** Counts are what is easy to compare, which is
exactly why they are where blind spots live.

### What the adversarial audit added, after the epic was already "done"

The two defects above were both found by a 28-agent adversarial audit of this
epic's own changes, run after the live proofs had passed and the build-log entry
had been drafted. It is worth recording what that bought, because the honest
answer is "the two most serious findings in the epic".

It also found three smaller things that were fixed the same way:

- **The seed's own instructions injected a real company.** `seed_dev.py` printed
  `AVP_OVERRIDE_STRIKE=…` and nothing else, so following its instruction left
  `AVP_OVERRIDE_ADD` on its default — `Intercom` / `intercom.com` — writing a
  real brand into a set that is otherwise entirely `.example`. This epic argued
  at length against exactly that and then did it by a route it had not checked.
  It now prints all three variables with synthetic values, and
  `verify_competitor_override.py`'s fabricated re-detection candidates follow
  the kind of data they are running against rather than being hardcoded
  (`Gorgias`/`gorgias.com` against real data, `Redmoor Supply`/`redmoor.example`
  against seeded).
- **`verify_report.py` called seeded data "real".** The same lie the override
  script's PASS line told, in the other script; nobody had looked. Both now
  print a `data :` line naming the source, and the PASS line follows it.
- **The `.example` invariant was documented and asserted nowhere.** It is now
  the eighth precondition the seed checks, evaluated over the domains actually
  in the database rather than over the constants — which is the form that would
  have caught the `Intercom` case.

Fixing the candidate names broke an assertion that still looked for the literal
`"Gorgias"`; caught immediately by the script failing, and both now read one
variable. Two literals that must agree is the same defect class as everything
else in this epic.

### Idempotency, proven by running it

Keys: Agency by `slug` (unique), Client by `(agency_id, domain)` (unique), Scan
by the oldest for that client via `.first()`. Children are then deleted scoped to
that scan id and rebuilt.

Run against a database created from nothing — `createdb` plus `alembic upgrade
head` to `bc32281a20c5` — then seeded **five times**. Every table count identical
across all runs, and the agency, client and scan ULIDs unchanged: the same rows
were reused, not recreated.

```
agencies 1 · clients 2 · scans 2 · prompt_sets 1 · prompts 2
engine_results 4 · brand_mentions 10 · citations 16
competitor_sets 1 · competitors 3 · audits 1 · audit_checks 12
scores 2 · action_items 2
```

(`clients` and `scans` are 2 because `verify_report.py`'s PART 3 self-seeds its
own degraded client, which the seed leaves alone.) Both target scripts were
re-run against the re-seeded database and still passed — a re-run leaves a state
that is not merely the same size but still valid.

**Coverage checked by derivation, not by reading.** The schema has 6 tables with
a direct FK to `scans.id` and 5 more reachable through them; `_clear_children`
deletes all 11. Every delete is scoped — 6 by `scan_id ==` directly, 5 by an
id-list that is itself `scan_id`-scoped — so the blast radius is provably one
scan. One latent sharp edge was hardened: an informational lookup used
`scalar_one_or_none()` on `Client.domain` without `agency_id`, which would raise
on a database carrying two agencies with a same-named client.

### The Help Scout scan is unaffected — checked, not assumed

`seed_dev.py` was never run against `avp_dev`; confirmed by querying for its
agency slug and client domain, both absent. Both scripts were then run against
`avp_dev` with default environment and all thirteen counts compared to the
pre-epic baseline — identical. The scan's fingerprint, byte-identical:

**This check was not sufficient, and the section above says why** — it compares
counts, and the damage `verify_competitor_override.py` was doing lived in a
nulled column. Link integrity is now part of what that script asserts, so a
future run of it does check.


```
scan_01M0HDRGJNWNZDSJPP0NC3SV8W | composite=58.24
  | competitors=Zendesk,Freshdesk,Front,Kustomer,Thecxlead
  | manual=0 | action_items=5
```

Epic 7's and Epic 8's screenshots and
`apps/web/src/lib/report/__fixtures__/reports.ts` all derive from that scan.

### The throwaway database was dropped

`avp_seedtest` is gone. Keeping it as a standing fixture database was considered
and rejected: a preserved database that becomes load-bearing is exactly the
problem this epic exists to remove, and the next person cannot tell whether such
a thing is authoritative or stale. It is three commands to recreate, and they are
in the script's docstring.

### Tests: none added, deliberately

**749 total, unchanged** (api 534, workers 13, shared-types 53, design-system 72,
web 77). This epic touched only `apps/api/scripts/`, which no suite covers.

No pytest coverage was added for `seed_dev.py`, and that is a decision. A unit
test would have to build a database, run the script and assert on rows — which
the five-run live proof already does, against real Postgres, through the real
scorer. The pytest version would be strictly weaker: `conftest.py` truncates
every table between tests, so it would assert idempotency against a database
wiped before each run, which is the opposite of the property under test.

There is a stronger argument for adding one thing, and it is recorded rather
than acted on: the `summary` gap the audit found in
`test_ip_safety.py::test_action_item_response_schemas_expose_no_third_party_prose`
is real — that guard uses exact-name intersection where the repo's own
`FixFacts` sweep documents at length why substring matching is correct. It
predates this epic and belongs with a change to that test, not to a seed script.

### Dependencies

**None added.**

### IP-safety self-check (constraint 9)

Checked explicitly, because this is the first synthetic data in the repo
standing in for real pipeline output. Every string the seed writes, enumerated
out of the database:

| Field | Values | Verdict |
|---|---|---|
| `clients.name` / `domain` | `Seedwell Supply` / `seed-fixture.example` | invented name, reserved TLD |
| `clients.industry` | `wholesale supply` | generic category label, not scraped copy |
| `competitors.name` / `domain` | `Northaven Group`, `Marlowe Direct`, `Kestrel Trade`, all `*.example` | names + domains — explicitly permitted (#7) |
| `brand_mentions.entity_name` / `entity_domain` | those three plus the subject | "names of entities mentioned" |
| `citations.source_domain` / `source_url` | four `.example` domains and paths | "URLs and domains that were cited" |
| `prompts.text` | two generic buyer questions | the documented exception: the text is ours |
| `technical_audits.schema_types` | `Organization`, `WebSite` | schema.org **type names** — structural signals |
| `action_items.title` / `detail` | two invented recommendations naming only `.example` domains | our own recommendations, the documented exception (`models/action_item.py`) |

Everything else is a count, boolean, ordinal, enum member or Decimal. **No
seeded field carries prose, page text, answer text or marketing copy**, and no
seeded row populates a column `FACTS_ONLY_MODELS` forbids. Because every domain
is under a reserved TLD, no seeded value can be mistaken for real scraped
content.

The `verify_report.py` change exempts `actionItems` from one substring sweep and
replaces it with a recursive substring sweep over the same subtree — bounded,
depth-agnostic, non-vacuous, and proven in four directions by negative control
including the two the first attempt missed. The guard is stronger than before
this epic for everything except the two documented fields.

**IP-safety check passed:** every value the seed writes is a name, a
reserved-TLD domain, a URL, a generic label, a schema.org type name, our own
prompt text, our own recommendation text, or a number — no prose, no page text,
no answer text, no real company's data; the one guard this epic altered was
rebuilt after an adversarial audit showed the first version could not fail, and
is now depth-agnostic and non-vacuous with four passing negative controls; and
no third-party content can reach the database, the API or the page by any path
this epic added.

---

## 2026-08-23 — Epic 3.9 · Finding 4, and the 500 it was hiding

Finding 4 said an override destroys citation attribution. It does, it did it in
two places rather than one, and while fixing it a second defect turned up that
was arguably worse: **re-running detection has returned a 500 since Epic 3**
whenever it re-found a rival it already had.

### The question the register asked, answered: mechanical, not semantic

The register recorded three candidate fixes and flagged option 2 — reuse the row
when identity is unchanged — as *probably right but possibly a semantics change*,
because it "changes what replace-the-set-wholesale means". This brief's first job
was to settle that. It does not change it, for four reasons, each checkable:

**The identity key already existed in the function.** `apply_override` already
computed `kept_keys = {slugify(name) for name, _ in items}` to work out which
rows had been struck. Option 2 uses that same key to decide which rows to keep.
No new notion of "the same competitor" enters the codebase — `_identity()` is a
one-line wrapper over the call that was already there.

**Every mutable field is overwritten identically either way.** On reuse the row
gets name, domain, rank, `detection_source=MANUAL`, zeroed signals,
`is_manual_override=True` — the exact set the insert path wrote. `_apply_manual`
is shared between both paths, the same discipline `scoring_runner._apply` and
`audit_runner._apply` use. **The only difference is whether the row keeps its id.**

**Nothing observable changes.** `CompetitorOut` exposes id, name, domain, rank,
detection_source, the three signal counts, corroborated, score and
is_manual_override — no `created_at`. The resulting set is identical in every
field an operator or an API consumer can see. The only difference is that an
opaque surrogate key stays stable, and nothing anywhere relies on competitor ids
*changing*.

**The precedent is in the sibling function.** `persist_detection` already
preserved manual rows with their ids intact across a re-detection
(`competitor_set.competitors = manual`). "Reuse the row rather than recreate it"
was already the established pattern for this exact entity.

Where the register's worry actually pointed: the docstring's "wholesale" language
is about *reconciliation* — "reconciling that against auto-detected rows one at a
time invites a half-applied state". That is a claim about the resulting **state**:
no partial merge, every submitted row ends up MANUAL with signals zeroed. Option 2
preserves that exactly. It changes the mechanism, not the state. The concern was
about merging; this is about key stability. **Implemented without asking, and this
paragraph is the justification for not asking.**

### The finding was bigger than recorded: both write paths had it

Finding 4 named `apply_override`. `persist_detection` does the same thing at
`competitors.py:663`:

```python
for competitor in list(competitor_set.competitors):
    if not competitor.is_manual_override:
        await session.delete(competitor)
```

Every auto-detected competitor, hard-deleted on every run. In `avp_dev` all five
competitors are non-manual and Front and Zendesk are the two carrying attributed
citations — so re-running detection nulled attribution exactly as an override
did. Both paths are fixed; the register entry is updated to say so.

### Why no test caught it, stated precisely

Not one of the twenty-three tests in `test_competitor_endpoints.py` builds a
`Citation` or a `BrandMention`. Grepping for either name returns only
`SerpHit`/`CoCitationHit` — detection *stub inputs*, never persisted rows. None
of the tests runs a scan, so there are no `engine_results`, so there is nothing
pointing at the competitor set to lose. **The set was always exercised in
isolation.** That is the whole reason a defect this severe lived in a file with
twenty-three passing tests from Epic 3.6 to Epic 3.8.

### The second defect: re-detection has returned 500 since Epic 3

Writing the re-detection test produced an `IntegrityError`, not an assertion
failure. A bare two-call probe — detect, then detect again with the same stub —
reproduced it with nothing else involved:

```
UniqueViolationError: duplicate key value violates unique constraint
  "uq_competitors_set_name"
DETAIL:  Key (competitor_set_id, name)=(cset_…, Keep) already exists.
```

Deleting `Keep` and inserting `Keep` in one flush is a same-table
delete-then-insert on a unique key, and SQLAlchemy's unit of work orders INSERTs
before DELETEs. So **re-running detection and finding the same rivals — the
ordinary case — 500s.**

No test caught it because every existing re-detection test uses a **disjoint**
result set: `test_manual_entries_survive_redetection` goes a.com → b.com/c.com,
`test_tombstones_do_not_consume_ranks` goes keep.com → fresh.com. No name was
ever deleted and re-inserted in the same flush.

This is the strongest argument for option 2 over option 1. Re-linking on write
would have fixed the attribution and left the 500 in place; reuse removes the
delete/insert pair, so the collision cannot arise. The register's instinct that
option 2 was the right one was correct, for a reason it had not identified.

### What shipped

| | Before | After |
|---|---|---|
| `apply_override` | delete every row, insert all | reuse by `_identity`, insert only genuinely new rows — **delete-free** |
| struck rival | deleted, new tombstone inserted | tombstone **in place**, id and attribution preserved |
| struck rival's domain | nulled on the tombstone | kept — `persist_detection` blocks by name *or* domain, so the strike is harder to defeat |
| `persist_detection` | delete every non-manual row, insert candidates | reuse a re-found rival, delete only rivals this run did not find |
| re-detecting the same rival | **500** | 201 |

`persist_detection` still deletes an auto-detected row the current run did not
re-find, nulling its attribution — correct, since the rival has left the set, and
now the only case where attribution is lost at all.

### Negative controls — both fixes independently guarded

| Reverted | Failed |
|---|---|
| `apply_override` → delete-and-recreate | `…keeps_attribution_for_rivals_it_keeps`, `…keeps_the_row_id_for_an_unchanged_rival` |
| `persist_detection` → delete-all-non-manual | `…redetection_keeps_attribution…`, `…redetecting_the_same_rival_does_not_500` |

Each revert fails exactly the tests for its own path and leaves the other four
passing, so neither fix is carrying the other. The source file was diffed against
its pre-injection backup after each control and restored identical.

### Live verification

A real override, through the real `apply_override`, on the real Help Scout scan
in `avp_dev` — the only data in the repo with genuine attributed citations:

```
BEFORE  competitors=5  attributed links=5
        ['cite:front.com', 'cite:zendesk.com', 'men:Freshdesk', 'men:Front', 'men:Zendesk']
AFTER   competitors=5  attributed links=5
  -> attribution SURVIVED unchanged (same ids)
RESTORE identical  links=5
```

**No post-hoc repair, which is the point** — Epic 3.8 had to re-derive these
links by hand after every run. `avp_dev` was snapshotted across all eleven
mutable competitor fields plus `detection_confidence` and `status`, restored, and
the restore verified identical. Final state: 2/45 citations and 14/14 non-subject
mentions linked, five competitors `src=both`/`serp`, `manual=false`,
`confidence=0.800` — byte-identical to where Epic 3.8 left it.

Both verification scripts still pass against `avp_dev`, and
`verify_competitor_override.py` now reports `links : 16 restored (was 16)` —
its Epic 3.8 restore machinery is still correct but no longer load-bearing.

**`seed_dev.py` checked rather than assumed**: seeded a fresh database from
nothing, seeded it again for idempotency, and ran the override script against it.
All three exit 0.

### Dependencies

**None added.**

### IP-safety self-check (constraint 9)

No UI-facing behaviour changed — `CompetitorEditor.tsx` is untouched and needed
no change, as the brief anticipated. Confirmed rather than skipped: the API
surface is byte-identical (`CompetitorOut`'s field list is unchanged, and the
OpenAPI schema regenerates identically), so nothing rendered changes shape.

The fix strictly **preserves** facts that were previously destroyed — citation
and mention attribution are `competitor_id` foreign keys, i.e. "which named
entity this cited domain belongs to", which ip-safety.md #7 explicitly permits
as an entity name and a cited domain. No new field, column or value is
introduced, nothing is stored that was not stored before, and the struck-rival
tombstone carries a domain where it previously carried null — a fact, not prose.

**IP-safety check passed:** no UI changed and none needed to; no new column,
field or stored value was introduced; the only data-shape change is that a
tombstone now retains the rival's domain, which is a registrable domain and an
explicitly permitted fact; and the epic's net effect is to stop destroying
attribution the system had legitimately recorded.

### Tests

**754 total, up from 749.**

| suite | before | after | delta |
|---|---|---|---|
| api | 534 | 539 | +5 |
| workers | 13 | 13 | — |
| shared-types | 53 | 53 | — |
| design-system | 72 | 72 | — |
| web | 77 | 77 | — |

All five are in `TestAttributionSurvivesAnOverride`: attribution survives an
override, the row id is reused, attribution survives a re-detection, re-detecting
the same rival does not 500, and an overridden set can be overridden again. The
last two cover the collision defect; the class docstring records why the existing
twenty-three tests could not have caught any of it.

---

## 2026-08-24 — Epic 3.10 · Auditing the six remaining verification scripts

Three consecutive epics found a real production defect by scrutinising a
`verify_*.py` script. Six had never been looked at. This epic read all six, ran
all six, and fixed what that turned up.

**Headline: one genuine production defect in the scoring engine, five defective
verification checks, and three pipelines confirmed healthy.** The scripts were
in worse shape than the product.

### Real API spend

Stated plainly, per the transparency Epic 3.7's cost table established.

| script | spend | outcome |
|---|---|---|
| `verify_audit` (×3 runs) | £0 — 4 Chromium crawls each, no paid API | 2 defects fixed |
| `verify_intake --crawl-only` (×3) | £0 | 1 defect fixed |
| `verify_fixes` (×2) | **4 claude-opus-5 calls** (first run wasted, see below) | 1 defect fixed |
| `verify_scan` (×1) | **~25 claude-opus-5 calls** | 1 defect fixed, pipeline healthy |
| `verify_scoring` (×1) | **~17 claude-opus-5 + 6 SerpApi** | 2 defects fixed, engine healthy |
| `verify_competitors` (×1 full) | **40 claude-opus-5 + 60 SerpApi** | 1 defect fixed, 1 logged, detection healthy |
| **total** | **~86 claude-opus-5 calls + 66 SerpApi searches** | |

Two of those calls were wasted. The first `verify_fixes` run went against a
seeded database whose audit an earlier `verify_audit` run had already replaced,
so it failed on missing schema warnings rather than on anything about
`verify_fixes`. That is itself a finding — **the scripts are not composable**:
running one against a database changes what the next one sees. Nothing warns you.

`verify_competitors` was approved in both `--serp-only` and full variants. Only
the full run was made, because it subsumes `--serp-only` — saving 60 redundant
SerpApi searches.

### The production defect: `inputs_digest` has been lying since Epic 6

`compute_inputs_digest` fingerprints `results` and `competitors`. Epic 6 made
`technical_foundation` the fifth scored dimension and the digest was never
widened. So re-auditing a site moves the composite while `inputs_digest` **and**
`formula_version` both stay identical — exactly the ambiguity the function's own
docstring promises cannot happen, and which scoring-spec.md rule 5 forbids.

Proven before fixing: identical results and competitors scored **79.50** with
`technical_foundation=20` and **87.00** with 95, under one unchanged digest.

`test_digest_changes_when_any_scored_input_changes` is named for "any scored
input" and covered two of the three families. Widened, plus a second test
asserting the end-to-end form on the score rather than the digest. Negative
control: dropping the field from the payload fails both.

**Confirmed live on `avp_dev`.** Re-scoring Help Scout moved its stored digest
`a6b8ebef36f71dc4 → 3b9ef35cfa9c261c` — the fix visibly taking effect on real
data — with the composite unchanged at 58.24.

### Per script

**`verify_audit.py` — two defects, both serious.**
It selected `ORDER BY Scan.id DESC`. Scan ids are time-ordered ULIDs, so when
Epic 7's `verify_report.py` PART 3 created the degraded fixture scan, that became
the newest and this script silently began auditing `epic7-degraded.example` —
a domain that does not resolve. It had been crawling nothing since Epic 7.
It also hard-deleted the scan's audit and all its checks and **committed that
eleven lines before attempting the replacement crawl**, with no restore anywhere
in the file, so any failure in between destroyed the audit permanently. The
delete was redundant: `run_audit` is documented as refreshing in place.
Both fixed; two silent `return 0` paths that exited green having verified
nothing now return 1.

Then, running it against `avp_dev`, a third: its only real assertion required
`before.composite != after.composite` unconditionally. Right for a first audit,
impossible for a re-audit — so a live re-crawl of helpscout.com that reproduced
`technical_foundation=87.50` and `composite=58.24` exactly, the best possible
outcome, was reported as NEEDS REVIEW. Now conditioned on which case occurred.

**`verify_intake.py` — one defect. `--crawl-only` could not fail.** It appended
every attempt to `rows` before looking at `crawl_ok`, printed
`f"{len(rows)}/{len(TEST_SITES)} sites crawled"`, and returned 0 unconditionally.
A run in which every crawl failed printed "5/5 sites crawled" and exited green.
Negative-controlled: a non-resolving site now yields "5/6", a named FAIL line and
exit 1. **The crawler itself is healthy** — 5/5 real sites, 423–2008 words each.

**`verify_fixes.py` — one defect, and it was failing good output.** The
"actionable language" check demanded the literal token `schema`. Against a
seeded scan the model wrote "FAQPage **markup**" — plainer English, naming the
same artefact just as concretely — and the script called a correct generation a
failure. It had passed against Help Scout only because that run happened to use
the word "schema". Now matches groups of equivalent spellings. Validated offline
against the captured output of the paid run that surfaced it, so no re-spend;
negative-controlled in the direction that matters — the widened check still
rejects generic advice, still fails a list naming the artefact but not the
domain, and still fails one naming the domain but no artefact.

**`verify_scan.py` — one defect. The headline check was `n == n`.**
"every prompt × engine pair produced a row" is `pairs == len(selected) ×
len(ENGINES)`, and `pairs` increments once per row from `ask_all`, which gathers
exactly one coroutine per engine, filters nothing, and whose per-engine `ask`
returns an `EngineAnswer` with `ok=False` rather than raising. The row count is
the pair count by construction. Worse, engine failures were counted, printed,
then left out of the exit code — a run where every call failed could still print
PASS. The verdict now requires zero failures. Also now states plainly that the
script writes nothing (0 `scan_runner` imports, 0 commits) so it verifies
extraction, not the persisted `EngineResult` records §7 actually asks for.
**The pipeline is healthy** — 12/12 pairs, zero failures, 43 citations.

**`verify_scoring.py` — two defects.** The determinism loop compared cached
objects: `expire_on_commit=False` means the identity map returned the same
instances, so it could catch nondeterministic arithmetic while being blind to
the read path — which is what the digest's sorting defends against. And it never
runs an audit, so it has only ever exercised the degraded four-of-five-dimension
path; it has never verified the fifth dimension since Epic 6 added it, which is
precisely why the digest gap survived. **The engine is healthy** — composite
58.62, deterministic across five re-scores, one score row.

**`verify_competitors.py` — one defect fixed, one logged.** `--serp-only`
returned 0 unconditionally after spending all sixty SerpApi searches, so a dead
key read as success; fixed. The larger problem is logged as **Finding 5**: it
calls `detect_for_client` zero times and rebuilds the pipeline itself, so it
verifies a copy. Not fixed here because it holds no database connection by
design and closing it is a design decision. **Detection is healthy** — 80%
precision, exactly the §7 bar, 8/10 URLs at ≥80%.

### Two things I got wrong, and one the audit did

**My own fix broke the script.** The first version of the `verify_scoring`
determinism fix used a bare `session.expire_all()`, which expires the `Scan`
too — so the next `scan.id` access became a lazy load outside a greenlet and
raised `MissingGreenlet`, the exact async trap this codebase warns about
elsewhere. Caught by *running* the change before committing it. The Scan is now
re-fetched with an awaited `get()`.

**My first negative control proved nothing.** Testing the `verify_intake` fix, I
injected a failing site using an anchor that did not match, so the injection
silently did not apply and the control "passed" against unmodified code. Caught
because the output still said 5/5. Redone with a verified anchor. That is the
fifth harness failure in six epics — Epic 8's selector, Epic 3.6's stale
backups, Epic 3.7's unrealistic control, Epic 3.8's post-serialisation
injection, and now this. **The guards keep being right; the harnesses keep being
wrong.**

**The adversarial audit overstated twice.** It called `verify_scoring`'s
determinism check "unfalsifiable, True on every run that does not raise" — but
`score_scan` does re-query, and the check would catch genuinely nondeterministic
arithmetic; the real weakness was narrower. And it claimed
`verify_competitors`'s "exit code has never once agreed with its printed
verdict" — but both derive from `overall >= 0.8` and agree by construction; the
real defect was the `--serp-only` path only. Recorded because this project's
audit agents have a track record of overstating, and a finding taken on trust is
how Epic 3.8 shipped a tautological control.

### Coverage cross-check: not done

Six agents were tasked with checking whether each script's pytest suite builds
genuinely related rows (the Epic 3.9 lens — the competitor tests never built a
`Citation`). All six **failed on a subagent session limit**. That analysis does
not exist and is not claimed. It remains the most likely place a seventh defect
is hiding.

### `avp_dev` is unaffected

Diffed field by field against a pre-run snapshot after the only script that
writes to it. The audit row keeps its id and its `technical_foundation`; all 17
check statuses identical. Four values changed and every one is correct:
`audited_at` and `computed_at` timestamps, `content_freshness` 0 → 1 day (the
site is a day older), `cwv_lcp` 572 → 976ms (lab noise, which Epic 6 excludes
from the score by design), and the corrected `inputs_digest`. Composite 58.24,
`technical_foundation` 87.50 and every check verdict unchanged. Two throwaway
databases were created and dropped.

### Dependencies

**None added.**

### Tests

**755 total, up from 754** (api 539 → 540). One new test —
`test_a_changed_audit_cannot_move_the_score_under_one_digest` — plus the
widening of the existing digest test. Five of the six defects live in
`scripts/`, which no suite covers; that is a real limitation of this epic's
coverage and the reason each fix was negative-controlled by hand against
captured output instead.

---

## Epic 3.11 — resolving Findings 3, 5 and 6

Three entries in the `api-contracts.md` register had been open long enough to
be furniture. This epic was scoped to close them, and — the part that shaped
everything below — to **present the tradeoffs and implement whichever option
was chosen**, rather than to pick unilaterally. That distinction mattered more
than expected: on two of the three, reading the code to write the options
changed what the options were.

### The decision conversation

Each finding was presented with its real alternatives before any code moved.

**Finding 3 — what should `detectionConfidence` mean on a mixed set?**
Options: clear it whenever any manual row survives (simple, destroys a real
measurement); scope it explicitly by publishing the count of rows it covers;
or split it into a per-row property. **Chosen: scope it.** The reasoning that
made this the right first move is that the three options are not alternatives
at the same level — clearing it loses information permanently, the per-row
split is the principled long-term answer but changes what Epic 5 may infer from
the field, and scoping it fixes the part that is actually *misleading* without
foreclosing either. It is the only one of the three that is purely additive.

**Finding 5 — how should `verify_competitors.py` stop verifying a copy?**
Options: give the script a throwaway database and a seeded client per case, or
extract a database-free core both it and the service call. **Chosen: extract
the core.** The register had already argued for this and then declined to do it
on cost grounds. Those cost grounds turned out to be wrong (below).

**Finding 6 — where should the five-dimension path be covered?**
Options: add an audit step to `verify_scoring.py`, or write a new combined
script. **Chosen: add the step.** A new script would have been a third thing to
keep in sync with two others; the objection to adding a crawl to `verify_scoring`
was about the script's conceptual purity, and a scoring verification that cannot
exercise one of its five scored inputs has a purity problem already.

"Defer this, still not urgent" was an explicitly available answer for any of the
three. None were deferred.

### Finding 5 — the register was wrong about the cost

The entry said closing this "is not a small change" because `detect_for_client`
takes a `Client` row while the script deliberately holds no database connection.

Reading the function to write that tradeoff: **it makes zero database calls**
and reads the `Client` in exactly six places, every one a plain scalar —
`brand_name`, `domain`, `industry`, `industry_niche`, `name`, and `id` for a log
line. The ORM object was only ever an awkward way to pass six strings. The
"database-free core" the register imagined extracting was essentially the whole
function.

`detect_from_facts` now holds the body and takes those six scalars.
`detect_for_client` is a thin unpack over it, so no existing caller changed. The
script calls `detect_from_facts` directly and still touches no database, keeping
the property the register wanted to protect.

**A correction to Epic 3.10's write-up, and to the register entry itself.** Both
said the script "reassembles the pipeline" and stopped there. It also called
`decide_detection`, with arguments structurally identical to the service's — the
two were genuinely equivalent, and the 80% precision Epic 3.10 reported was a
sound number. Finding 5 was a **drift risk, not a live incorrectness**. The
entry as written implied the measurement had been wrong all along, which would
have been a much more serious thing to have shipped.

`--serp-only` still assembles its own outcome, because `detect_from_facts`
always runs both signals. It now says so in the code rather than leaving a
reader to assume the diagnostic half measured what the API does.

Three guards, negative-controlled in both directions: the wrapper and the core
must return identical outcomes for identical facts; the core must stay free of
`session`/`select`/`commit`/`execute`; and the wrapper must hold no logic of its
own, since logic there would run in production but not in the script — which
would reopen the finding silently.

**One methodology note.** Checking "does the script still reassemble the
pipeline?" by grepping for `merge_candidates`/`decide_detection`/`build_queries`
returned True — because the explanatory comment I had just written names all
three. Re-checked with comments stripped: `reassembles pipeline in code: False`.
A guard that matches its own documentation is the same trap in miniature.

### Finding 3 — the figure had no home to be misleading in

`detectionConfidence` is the share of the rivals detection ranked that both
signals surfaced independently. `apply_override` clears it, correctly. But a
subsequent re-detection writes a fresh figure while the operator's rows survive,
so the API published one corroboration number over a part-hand-set list.

`CompetitorSetOut` and `ReportCompetitorSetOut` now both carry
**`confidenceCovers`** — the number of rows in the set that detection produced.
Derived at projection time from a new `CompetitorSet.detected_competitors`
rather than stored, so it cannot go stale against the rows it counts. The stored
arithmetic is untouched; only what the API says about its scope changed.

**The field is deliberately not the figure's own denominator**, and is
documented as not being it. Detection computes the ratio over every candidate it
ranked, including ones the operator had already named or struck, which are then
held back from the set. The published count is the number of rows a reader can
actually see it apply to.

**A claim I wrote and then had to retract before committing.** The first version
of the field comment said `confidenceCovers` is 0 exactly when
`detectionConfidence` is null. It is false in both directions: a set where only
one signal ran carries a null confidence and a full complement of detected rows,
and a re-detection that re-found nothing but rivals the operator had already
named carries a real confidence and zero of them. Written from intuition about
how the two fields relate, caught by tracing `decide_detection`'s branches.

**And the finding was worse than recorded.** The report never rendered the
figure at all. Meanwhile the note under the competitor table told the reader
that hand-set rivals were not described by "the corroboration figure above" —
and there was no such figure on the page. That copy had pointed at nothing since
Epic 3.6 wrote it. There is now a `CorroborationNote` rendering the percentage
with its scope, and the dangling reference is gone.

Guarded on both projections **separately**, because they are separate schemas
built by separate functions. Negative control confirmed the separation is real:
making either projection count all active rows fails that projection's tests and
**only** that projection's — so fixing one and not the other would have left the
figure misleading in the place a client actually reads it, with a green suite.

`verify_competitor_override.py` printed Finding 3 as a `NOTE` and passed
regardless. It now asserts the count, **and fails if the set it built is not
actually mixed** — a check that passes vacuously is the failure mode this
sequence keeps hitting.

`stub_discovery` moved to `conftest.py`, since `test_report_endpoint.py` needs
the same stub to build a mixed set and a second copy of a fixture that fiddly
would drift.

### Finding 6 — the five-dimension path, now exercised live

`verify_scoring.py` now scores **before** the audit, runs the audit, and scores
again, asserting four things about the transition instead of printing a caveat:
`technical_foundation` becomes included; every dimension in the `Dimension` enum
is included (derived from the enum rather than a hardcoded 5 — the
hardcoded-list defect class this codebase keeps hitting); the
`TECHNICAL_FOUNDATION_NOT_MEASURED` flag clears; and **the `inputs_digest`
moves**. The last is the live regression test for Epic 3.10's defect —
everything else about the scan is byte-identical across the two scores, so a
digest that does not move means the fifth input is not in it.

The determinism loop now runs over the five-dimension path, which was the other
half of the gap: reproducibility had only ever been demonstrated for a formula
that excluded one of its inputs. `--skip-audit` restores the old behaviour and
keeps the old caveat.

The cost was put up before it was spent — 17 model calls, 6 SerpApi searches,
one free crawl — and approved. **Run live against a throwaway database, and it
passes:**

    [3/4] scoring, BEFORE any audit
          composite 59.22   excluded=['technical_foundation']
          flags=['NO_AUTHORITY_DATA', 'TECHNICAL_FOUNDATION_NOT_MEASURED']

    [4/4] technical audit of helpscout.com   status=ok  score=87.50  checks=17
          composite     : 59.22  ->  62.05
          inputs_digest : 7604bc4c41fb26af  ->  75429cf57317e2ec
          included      : 5/5
          flags         : ['NO_AUTHORITY_DATA']
          OK  technical_foundation is now included
          OK  every dimension is included
          OK  the NOT_YET_MEASURED flag is gone
          OK  the digest moved when the fifth input appeared

    deterministic across re-scores : True   (1 composite, 1 digest, 1 score row)
    RESULT: PASS

Detection returned five rivals at confidence 1.000, all corroborated by both
signals — Zendesk, Front, Kayako, Freshdesk, Kustomer — so the run exercised
the competitor-dependent dimensions properly rather than degrading past them.

**Three of the four assertions are self-evidently non-vacuous**: the run printed
the failing state and then the passing state inside a single execution. That is
better evidence than a synthetic control, because nothing had to be injected to
produce the negative case — the scan genuinely was four-of-five before the audit.

**The digest assertion was negative-controlled separately**, against the rows
that run had just persisted, so it is a statement about real data. Reverting
`compute_inputs_digest` to its Epic 3.10 defect collapses both digests to one
value — `7785e2c34b073f0a` — and the assertion returns False, meaning the script
would have failed. Restored, it returns True and reproduces `7604bc4c41fb26af`
and `75429cf57317e2ec`, the exact pair the live run printed. So the check is
driven by `technical_foundation` and by nothing else.

**`avp_dev` untouched.** The run's scan is absent from it; the one
`Scoring Verification` agency there dates from 2026-08-21, an earlier epic. The
throwaway database was created, migrated, used and dropped.

### A test count I got wrong twice

The Epic 3.10 entry recorded 755 total. Recounting for this epic I got 742,
concluded 755 was a stale figure carried forward, and amended a commit message
to say so. That was the error: I had counted `apps/api`, `apps/web`,
`shared-types` and `design-system` and **forgotten `apps/workers`**, which has
13. The original 755 was correct, and one commit message now understates by 13
with the correction recorded in the next one rather than by rewriting history.

Verified suite by suite this time: api 548, workers 13, web 82, shared-types 53,
design-system 72.

### IP-safety self-check

UI-facing behaviour changed, so #7 applies. `CorroborationNote` interpolates a
percentage, two integers and the word "rival"/"rivals" — counts and ordinals,
which #7 permits explicitly. No entity name, engine text or competitor prose
reaches it. `confidenceCovers` is an `int` on both schemas.
`test_ip_safety.py` passes at 74. No new dependencies, so #6 does not apply.

### API spend

17 `claude-opus-5` calls and 6 SerpApi searches, in one approved run of
`verify_scoring.py --prompts 3`. Nothing else in this epic spent anything:
Finding 5 was closed on structural and unit evidence rather than by re-running
`verify_competitors.py`, which would have cost 60 SerpApi searches and 40 model
calls to watch it print the same 80% it printed in Epic 3.10.

### Dependencies

**None added.**

### Tests

**768 total, up from 755** (api 540 → 548, web 77 → 82). Eight new API tests —
three for detection having one implementation, four for the confidence scope on
`CompetitorSetOut`, one for it on `ReportCompetitorSetOut` — and five new web
tests for the rendered figure and its caveat. Six negative controls run across
the two findings, each failing only the guard it targeted.

---

## 2026-08-24 — Epic 7.1 · The Answer Shelf, and five epics of a promise nobody read

`design-system.md` §5 said Directions A and C were "approved and land in Epic 7."
Epic 7's own entry describes a proof beat of citation tables and a fix beat of
`FixList` text. Neither resembles a per-prompt ordinal shelf or a bipartite
domain map. This epic found out which of those two documents was wrong.

### The investigation, before any code

The brief offered three possibilities: never built; built under different names;
or descoped somewhere off the record. The evidence is unusually clean:

| Search | Result |
|---|---|
| repo-wide grep, all source and docs | "Answer Shelf" / "Source Map" appear in **three places**: `design-direction.md` 231 and 270, `design-system.md` 275. Nothing else. (`sourceMap` in `tsconfig.base.json` is the compiler option; "shelf" in `test_prompts_extraction.py` is the substring fixture `"put it on the shelf"`.) |
| `git log --all -S AnswerShelf` / `-S SourceMap` | nothing |
| `git log --all -S 'Answer Shelf'` / `-S 'Source Map'` | only `1e8fdea`, the initial import — the design docs themselves entering the repo |
| stashes, branches, dangling commits | one branch, no stashes; three dangling commits, all Epic 3.11 autostashes touching competitor and verify files |
| `build-log.md`, all 4,300 lines | **zero** occurrences of "Direction", "Shelf" or "Source Map" in any entry — Epic 0.5, 0.6, 7.0, 8.0 included |
| `api-contracts.md` deferred register | lists Epic 7.1 deferrals: PDF export, share link, branding. Not A or C. |
| `packages/design-system` style guide | 9 sections; §07 is the Ledger. No shelf, no map, no draft, no commented-out block |
| both source trees, by shape rather than name | one chart component exists, `LuminanceLedger` |

**Answer: (a) — genuinely never built, with nothing recorded.** Not "built under
another name": the shipped proof beat aggregates `mention_shares` by
`entity_name` alone, discarding prompt identity at aggregation time, so it is
structurally incapable of per-prompt ordinality; and `deriveFixes` had exactly
two sources, with `ActionItemSource` carrying a docstring arguing for exactly
two, so the fix beat had nowhere to put a domain recommendation.

**One caveat stated honestly.** History is squashed — `1e8fdea "Initial import:
Epics 0-3.7"` actually spans Epic 0 through Epic 8, since 7.0 and 8.0 are dated
2026-08-22 and the 3.6+ series is a later remediation run. Git alone cannot prove
a component was never written and deleted inside that window. The build log can,
and it is the meticulous record.

### Why the two documents disagreed for five epics

The brief assumed all three directions "got explicit approval." They did not, and
that turns out to be the whole mechanism.

`design-direction.md` §6 — *"What I need signed off"* — is five numbered items,
and item 5 reads: **"Motif — build B (Luminance Ledger) as the signature
component."** A and C appear only in §5's *Recommendation* prose. `design-system.md`
§5, written in Epic 0.6, upgraded that recommendation into "**approved** and land
in Epic 7."

Then nothing downstream ever read it again. Epic 7 was planned from
`product-spec.md` §7's Epic 7 checklist — layout, white-labelling, PDF/share —
which never mentions A or C; `api-contracts.md`'s deferred register was written
from the same list. Epic 7 built its checklist faithfully and recorded its own
scope-down of white-labelling in detail. The commitment existed in exactly one
sentence, in the design system's own reference doc, which the report epic had no
reason to open.

So this is not a lapse in the build log's discipline. **The build log never knew.**
Both design docs are now corrected: `design-direction.md` carries a status note
reconciling its five-year-stale "AWAITING APPROVAL" heading and stating plainly
that A and C were never in the sign-off list, and `design-system.md` §5b records
what shipped, what did not, and why.

### What was decided, and what it cost

Presented as four options with costs. Chosen: **build A, build C's deliverable,
defer C's map** — and make the new work **additive**, leaving Epic 7's tables
exactly as they were.

**Direction A shipped in full.** The data was already there at full fidelity —
`BrandMention` carries the ordinal, `Citation` the attribution, `Prompt` the text
— so there was no pipeline work, only a projection (`ReportProofOut.prompt_shelf`)
that had been aggregating it away, and a component.

**Direction C shipped its deliverable, not its picture.** The map re-presents what
the "Cited instead" table already shows; `design-direction.md` itself called it
"the heaviest engineering lift of the three" and "hairballs fast", and it is the
least likely of the three to survive §0's greyscale-print ruling. What was
genuinely missing was the *recommendation*, and that shipped: the fix beat now
names the heaviest domain nobody owns.

**One thing Direction C did not anticipate.** `rank_domains` sorts
competitor-attributed domains **above** unattributed ones on purpose — Epic 7
added that after `zendesk.com` and `front.com` were ranked out of the evidence
table. Correct for evidence, and exactly backwards for this: on the real Help
Scout scan the heaviest unclaimed domain (`eesel.ai`, 6 citations, twice the
subject's 3) sorted **third**, below two rivals cited once each, and
`MAX_CITED_DOMAINS = 12` could truncate it entirely. A recommendation must not
inherit an evidence table's display cap, so `unclaimed_cited_domains` is computed
from the full set before both.

### The load-bearing identity, and the bug that would have deleted the finding

The Ledger's correctness condition is that total lit height *is* the composite.
The Shelf's is:

```
every answered row carries exactly one subject mark
```

A marker at the ordinal the answer gave it, or an explicit empty notch — never
nothing. A row that renders nothing when the subject is missing does not look
like a bug. **It looks like a clean report.** Asserted in
`answerShelfLayout.test.ts`, not trusted.

Three ways that identity can break, each closed and each negative-controlled:

1. **Presence derived from the slot list.** `BrandMention.position` is nullable;
   a positionless mention takes no slot, so `any(slot.isSubject)` would report an
   absence in an answer that named the subject. Presence reads `mentioned`.
2. **The display cap dropping the subject.** Past the visible track the subject
   falls back to the notch column rather than off the edge, so a layout constant
   can never fabricate an absence.
3. **Re-indexing the visible slots.** A brand named 5th must not render 3rd
   because 2–4 were not in the competitor set. Stored ordinals only.

### The Epic 7.0 defect this epic exposed

`ANSWERED_NO_MENTION` is a distinct status, and `EngineResult`'s own comment says
why: *"the engine answered but the brand was absent. Distinct from ERROR: a
confirmed absence is a valid, scoreable data point; an error is not."*

Epic 7's `_proof` read `status is OK` everywhere — for `answered_results`,
`engine_coverage.answered`, the citation aggregation and the mention shares. So
every confirmed absence was filed with the timeouts. Demonstrated directly
against `_proof`, on a scan naming the subject in 3 of 6 answers:

```
REALITY : 6 answers returned, subject named in 3
REPORTED: answeredResults=3
          coverage: Answered 3 of 6, Named subject 3 of 3   <- a 100% mention rate
          totalCitations=3   (6 exist)
          rival appearances=3 of 6
```

The proof beat claimed a perfect mention rate on a subject named half the time,
and threw away the citations and rival mentions carried by the answers where the
subject was missing — which is the most damning evidence the beat has. Worse,
`proofHeading` would have said *"No engine returned an answer to measure"* for a
scan where the subject was simply never named, hiding total invisibility behind
an apparent technical failure.

**Fixed, and it was in scope rather than adjacent to it:** the Shelf would
otherwise have drawn three empty notches directly above a table reading "Named
you 3 of 3". A report that contradicts itself on one page is worse than either
half alone — the same reasoning Epic 7 used to refuse a second implementation of
the gap formula. No existing test encoded the old behaviour; all 548 passed
unchanged after the fix.

**Why it survived from Epic 7 to now: no fixture had one.** The real Help Scout
scan names the subject in all six of its answers, and `seed_dev.py`'s four
answers were all `mentioned=True`. The single most important case this product
measures — the client missing from an answer — was the one case no fixture
covered. `seed_dev.py` now seeds two absences, with the real
`ANSWERED_NO_MENTION` status rather than `OK`, because a fixture that cannot
reproduce a real status cannot catch a bug in how that status is read.

### Three more defects the work turned up

**The seed contradicted itself on ordinals.** `seed_dev.py` recorded
`EngineResult.position` from its `ANSWERS` table but always wrote the subject's
`BrandMention` at ordinal 1 — so a seeded answer could say "named 3rd" and store
the mention 1st. Nothing read both numbers, so nothing noticed. The Shelf draws
both, and the fixture contradicted itself on screen. The subject now takes its
declared place and rivals fill around it.

**`components.css` was unguarded.** Epic 7 added a test that every `var()` the
Tailwind preset references exists in `tokens.css`, after `leading-prose` compiled
to nothing for two epics. The stylesheet where every component is actually
painted was never checked. The Shelf was written against
`--avp-border-hairline`, which has never existed — the real token is
`--avp-line-hairline`. It rendered, it looked plausible, and the rule it drew was
invisible. The guard now covers `components.css` too.

**Two of this epic's own tests failed to fail.** The first negative control —
deriving presence from the slot list — passed every endpoint test, because the
stubbed scan runner always writes a positioned mention alongside a named subject,
so the two facts can never disagree there. Same for the unclaimed ranking and its
floor: the stub cites exactly one unclaimed domain, so every ordering and every
threshold passed. Rewritten as pure-function tests over constructed rows, which
is the only way to build the disagreement. Same failure class as Epic 5.1's
injected float and Epic 3.8's tautological key check.

### Existing tests this changed, and why each was safe

Three assertions had to be restated. Each was exact when written and became too
narrow once a third measurement existed; none had its intent weakened.

| Test | Was | Now |
|---|---|---|
| `marks generated items without inventing a third source` | `source === 'gap' \|\| source === 'audit'` | sources come from the closed set of **measurements**, and `'generated'` is asserted absent by name. The target was always "model wording is not a measurement"; the enumeration was shorthand for it |
| `carries no findings to turn into fixes` (failed audit) | `every(source === 'gap')` | `every(source !== 'audit')`. The claim is about the audit contributing nothing; a failed crawl does not make that scan's citations less true |
| `every fix is generated` (Epic 8 fixture) | all fixes marked | all fixes **Epic 8 built a candidate for** are marked; the citation fix is deterministic, which is `enrich`'s documented behaviour for an unmatched item |

**And one deliberate scope line.** Epic 8's `build_candidates` was **not**
extended to the citation source. Doing so needs a migration, a change at the
model boundary and paid calls to verify, and the deterministic string-table copy
is exactly where Epic 7 left the entire list before Epic 8 enriched it. The
consequence is visible rather than hidden: the fix beat's provenance note now
says "*where* a change was drawn from a measured gap or an audit finding" rather
than claiming the whole list was model-worded.

### Two design decisions the rendered page forced

Both invisible in tests and obvious in the capture — the Epic 7.0 pattern again.

**The fix list grew to 6, not 5.** The citation fix carries no point value by
design, so on a points ranking it truncates away — the same crowding-out Epic 7
already fixed once for audit findings. Taking a slot from the dimension budget
instead dropped the third-ranked gap, and the pitch beat sums exactly the listed
fixes, so a concrete recommendation would have been bought with a visibly smaller
projected composite on the sales beat. The ceiling moves to 6 only when there is
a domain to name, and that sixth item is the most concrete line on the list.

**The first render was unreadable in two ways.** The notch column's header ran
straight out of the viewBox (`6Help Scou`), and each prompt produced two rows —
one per engine — labelled identically, so the shelf read as every row printed
twice. Row labels now carry prompt *and* engine, right-anchored so they can never
cross into the track, and the notch column sits three pitches clear of the last
ordinal.

### Live verification

`verify_report.py` gained PART 4 (the shelf) and PART 5 (the unclaimed domain).
Costs nothing — no model, search or crawl calls.

Run against the **real** Help Scout scan in `avp_dev`, the same scan Epics 5–7
verified against. Real data was the right choice here over the synthetic seed for
the reason Epic 3.8 established: a seeded run exercises the persistence
guarantee, not the data. This chart's entire claim is about what real answers
did.

```
p1 claude         #1   *Help Scout(1) > Front(2) > Freshdesk(3) > Zendesk(4)
p1 claude_search  #1   *Help Scout(1) > Front(2)
p2 claude         #1   *Help Scout(1) > Zendesk(2) > Front(3) > Freshdesk(4)
p2 claude_search  #1   *Help Scout(1)^ > Zendesk(2) > Freshdesk(3)
p3 claude         #1   *Help Scout(1) > Zendesk(2) > Freshdesk(3) > Front(4)
p3 claude_search  #1   *Help Scout(1)^ > Zendesk(2)^ > Front(3)^
                       (* = subject, ^ = cited in that same answer)

    -> every answer has a row. OK
    -> all 6 row labels are our own stored prompts, verbatim. OK

  cited domains, as the EVIDENCE table ranks them (attributed first):
      1  front.com       Front
      1  zendesk.com     Zendesk
      6  eesel.ai        third party
  heaviest unclaimed : eesel.ai (6 citations), rank 3 in the evidence table
    -> out-cites the subject's own pages (6 vs 3). OK
```

The row labels are checked against the `prompts` table rather than eyeballed,
because `promptText` is the one prose-bearing field in the projection and #7
turns on it being ours.

**The real scan has no absences** — Help Scout is named first in all six answers
— so it cannot exercise the band of holes, and saying otherwise would overstate
what this run proved. That path was verified separately against a seeded
throwaway database (`createdb`, `alembic upgrade head`, `seed_dev.py`, dropped
after), which now carries two: `6 answered rows, 2 of them absences`, each still
showing the rivals that were named.

Captured from the running app (API on 8000 against `avp_dev`, Next on 3100):
`docs/screenshots/epic71-answer-shelf.png` and `epic71-report-full.png`. The
absence case is `epic71-answer-shelf-absence.png`, from the style guide's
synthetic fixture, for the reason above.

**`avp_dev` data untouched**, with one exception recorded rather than glossed:
the Epic 7 UI-review account `review@epic7.example` had no recorded password, so
its `password_hash` was reset to sign in for the capture. No scan, client, score,
competitor or citation row was written. The throwaway seeded database was dropped.

### IP-safety self-check

Customer-facing screens changed, so constraint 9 applies. Covering 1–5, 7 and 8.

**1 — designed from the data model.** The Shelf exists because `BrandMention`
has an ordinal and `Citation` has an attribution. No competitor product was
opened, referenced or described.

**2 — imports from `@avp/design-system`.** `AnswerShelf` lives in the design
system and mounts inside `ChartFrame`. `render.test.tsx` and
`ReportView.test.tsx` both assert the emitted markup carries no raw hex or
`rgb()` — extended this epic to SVG `fill`/`stroke` attributes, since chart marks
are painted there rather than in `style=""`.

**4 — assets.** No new icon, illustration or font. The Shelf is bespoke SVG over
existing tokens.

**5 — no competitor source inspected.** None fetched, viewed or read.

**7 — the facts-only rule, at the surface this epic added.**
- A shelf slot is `position`, `entity_name`, `entity_domain`, `is_subject`,
  `competitor_name`, `cited`. ip-safety.md #7 names this shape almost verbatim:
  *"counts and ordinal positions (e.g. 'mentioned 3rd')"* and *"names of entities
  mentioned."* There is no field able to carry what the answer *said* about a
  brand.
- `prompt_text` is **our own generated question**, the same field `PromptOut.text`
  has returned since Epic 4. It is the one prose-bearing field in the report
  projection, and it is now **registered by name** in the sweep
  (`sanctioned = {("PromptShelfOut", "prompt_text")}`) with `prompt_text` added to
  the forbidden list, rather than passing by not matching a word on it. Three
  negative controls confirm the allowlist is exactly one field on exactly one
  class: a `snippet` elsewhere fails, a second prose field on `PromptShelfOut`
  fails, and `prompt_text` on any other class fails. The allowlist is also
  asserted not to outlive its field.
- The unclaimed-domain fix names a **domain and two counts**. It never describes
  what is on that domain, which would be republishing it — and we have never read
  it. Asserted over the fix's own strings against nine describing phrases.
- Verified live that every rendered row label matches a stored `Prompt.text`.

**8 — no verbatim competitor marketing copy.** Every new string —
`fixForUnclaimedDomains`, the shelf's caption, its titles, the style-guide prose
— was written for this epic from the data model. The style-guide fixture is an
invented practice with invented rivals at reserved TLDs.

**IP-safety check passed:** the shelf renders ordinals, entity names and citation
booleans only; the one prose field is our own prompt and is now an allowlisted,
negative-controlled exception rather than an accident; the domain fix names a
domain and counts and never its content; no ramp colour reaches a competitor
series; and no raw colour value reaches the markup, including SVG paint
attributes.

### Dependencies

**None added.**

### Tests

**834 total, up from 768.** api 548 → 571 (+23), design-system 72 → 99 (+27),
web 82 → 98 (+16); workers 13 and shared-types 53 unchanged.

Fifteen negative controls run in total, each failing only the guard it targeted:
seven on the projection (presence source, stored ordinals, omitted absence rows,
the two `ANSWERED_NO_MENTION` reads, unclaimed ranking, unclaimed floor), three
on the ip-safety allowlist, one on the stylesheet token guard, and four on the
web layer (ramp colour, the fix dropped, the shelf unmounted, the audit
disclaimer widened). Three further controls **failed to fail** on first run and
the tests behind them were rewritten before being counted.

---

## 2026-08-25 — Investigation · Can SerpApi give us AI Overviews? Measured; building nothing

**Not an epic.** Deliberately unnumbered: `product-spec.md` already defines
Epic 9 as MVP Launch Readiness, and this is speculative pre-work for two
product directions that are not on the roadmap yet. Numbering it would claim
roadmap position it does not have.

An investigation brief, not a development one. Two product directions — AI
Overview capture as a third engine, and organic-rank-weighted fix
prioritisation — both rest on assumptions about a SerpApi response nobody in
this project had ever looked at. "Measure before you diagnose." This measures.

**Outcome: NO-GO on `google_ai_overview` as a fourth engine. Conditional GO on
persisting organic rank.** Nothing was implemented. No schema, no migration,
no change to `serp.py`, `competitors.py` or `engines.py`.

### SerpApi spend

Stated before the calls were made, per Epic 3.10's convention.

| | |
|---|---|
| Plan (read from `account.json`, not inferred) | **Free Plan**, $0.00/month |
| Quota | 250/month, **116 left** at start, resets 2026-09-20 |
| Consumed by this investigation | **15 searches** (116 → 101) |
| Currency cost | **$0.00** — the cost is quota, ~13% of what remained |

`account.json` calls are free and do not count. So are cache hits, which
matters below.

### What the code does today

`services/serp.py` issues one authenticated GET to `search.json` with
`engine=google, num=10, hl=en, gl=us`, and parses **`organic_results` only**.
No other key of the response is read. `SerpResult` is documented "**Transient
— never persist**" and has no SQLAlchemy mapping. The only consumers are
`competitors.py` (detection) and `extraction.py` (which imports the
non-competitor domain list, nothing else).

### 1. Can SerpApi return AI Overview content on this plan?

**Partially — and not for the queries this product actually asks.**

Eight queries, production parameters. Seven returned an `ai_overview` block,
but in two incompatible shapes:

| Query group | n | `ai_overview` shape |
|---|---|---|
| Broad informational ("what is customer service software") | 3 | **inline** — `references` + `text_blocks`, content present |
| **This product's own generated prompt shapes** | 4 | **`page_token` + `serpapi_link` only** — no content |
| Branded/navigational control ("help scout") | 1 | **absent** |

The control behaved as assumed, which was worth checking rather than trusting.

The free plan is **not** tier-gated out of the second path: `engine=
google_ai_overview` accepted the token, authenticated fine, and responded in
0.7–1.3s. It just returned nothing:

```
error: "Google hasn't returned any results for this query."
text_blocks=0  references=0
```

**Three of three** redemptions came back empty — tried both hand-built and via
SerpApi's own `serpapi_link`, redeemed within ~1s of the search that minted the
token, well inside the documented 4-minute expiry. This is adjacent to SerpApi
public-roadmap issue #2577 (opened 2025-03-31), closed **wontfix** — though
that issue reports the token path still working, so what was observed here is
the worse case.

### 2. The exact JSON shape, when content is present

```
ai_overview
├── text_blocks[]        type: heading | paragraph | list | expandable | comparison
│   ├── snippet          <- third-party prose
│   ├── snippet_links[]  {text, link}
│   └── reference_indexes[]  -> indices into references[]
└── references[]
    ├── index, link, source        <- FACTS
    └── title, snippet, thumbnail, source_icon   <- third-party prose
```

Cited source URLs **are** present, in `references[].link`, with an ordinal in
`references[].index` and the publisher in `references[].source`. On the
11-reference sample the domains were salesforce.com (×3), ximasoftware.com,
servicenow.com, gladly.ai, superoffice.com, kustomer.com, zapier.com,
thecxlead.com, appvizer.com.

**ip-safety.md #7 note for whoever builds this.** `references[].title` and
`.snippet`, and `text_blocks[].snippet`, are publisher copy. Only `link` →
domain, `index` → ordinal, and entity names are persistable. That is the same
boundary `serp.py` already holds for `organic_results`, so the rule is
established — but this payload carries far more prose than an organic result
does, and the temptation to store a "summary" is correspondingly larger.

### 3. The rate on this product's real query shapes

**0 of 4.**

Not 4 of 4, despite all four returning an `ai_overview` key. A block that
carries only a token, and a token that redeems to nothing, is not content.
Three of the four were the verbatim prompts from the real Help Scout scan used
throughout Epics 5–7; the fourth was a bottom-funnel shape from
`prompts.fallback_prompts`.

n=4 is small and is stated as an observed fraction, not an estimate. But the
split is clean: 3/3 short broad informational queries gave content, 4/4
conversational buyer questions gave a dead token.

**The obvious "fix" is the one thing that must not be done.** Rewriting the
prompt set toward short informational queries would raise the AI Overview hit
rate and corrupt the instrument. `prompts.py` says it in its own docstring:
"The prompt set is the measuring instrument. Every number this product reports
— mention rate, share of voice, sentiment — is a statement about *these*
prompts." Changing prompts to suit a data source inverts that.

### 4. Stability — and a measurement error worth recording

The first stability pass looked perfect: three queries re-run ~12 minutes
later, identical reference lists. It was wrong. The responses carried the
**same `search_metadata.id` and the same `created_at`** as the first round —
SerpApi served its cache, and cache hits are free, so the quota had not moved
either. A cache replay compared against its own original is not a stability
measurement.

Re-run with `no_cache=true`, ~4 minutes after the originals, distinct search
ids:

| Query | refs before → after | Stable? |
|---|---|---|
| what is customer service software | 11 → 11 | identical |
| how does a shared inbox work | 8 → 8 | identical |
| **best help desk software for small business** | **4 → 2** | **changed** — reddit.com appeared, 3× google.com dropped |

**Two of three stable, one of three changed inside four minutes** — and the
one that moved is the commercial-intent query, the shape closest to what this
product asks. Also visible: three of that query's four original "references"
were `www.google.com` self-links, not publisher citations, so the usable
citation count was 1, not 4.

Implication for a tracked engine: a single poll is a sample, not a fact, on
exactly the query class we care about. Anything built here needs either repeat
sampling (multiplying an already-infeasible quota cost) or an explicit
"observed once, at this timestamp" caveat on screen.

### 5. Is organic rank persisted? No — and here is where it dies

Traced through the code rather than from the build log's prose.

`SerpHit.position` (`serp.py:88`) is real and populated. It flows to
`Candidate.serp_positions` (`competitors.py:123`, appended at `:252`). Its
**only** consumer is `_position_weight` at `:286`, which reduces the whole list
to one ranking scalar via `1/sqrt(position)`.

Then `_apply_detected` writes the row, and writes exactly: `name`, `domain`,
`rank`, `detection_source`, `signal_count`, `serp_mentions`,
`co_citation_mentions`, `corroborated`, `score`. **`serp_mentions` is
`len(serp_positions)` — a count.** The positions themselves reach no column;
the `Competitor` and `CompetitorSet` models have no position field at all.

So per-query organic rank is **transient and lost**, discarded when the request
ends. It is fetched, used to rank, and thrown away.

### 6. Would this fit `EngineAdapter`?

The interface fits better than expected. `EngineAdapter` is
`ask(prompt, *, settings) -> EngineAnswer`, and `EngineAnswer` is
`{text, citations: list[CitedSource], status, error_code, latency_ms}` with a
`digest()` for change detection. An AI Overview genuinely is "ask a question,
get an answer with citations": `text_blocks` → `text` (transient, same as a
completion), `references[].link` → `CitedSource`, and `extract_facts` would
work on it unchanged.

What does **not** fit is the cost model and the failure taxonomy:

- **Two HTTP calls per prompt**, not one, whenever a token is returned — with
  a ≤4-minute expiry to honour inside a single `ask()`. A 24-prompt scan
  becomes ~48 searches against a 250/month quota, before any repeat sampling.
- **There is no correct status for "AI Overview promised, then redeemed
  empty."** `ERROR` overstates it, and `ANSWERED_NO_MENTION` would be actively
  wrong — that status means *the engine answered and the brand was absent*,
  which is a scoreable fact about the client. Reusing it here would feed a
  "you are invisible" signal from what is really our own retrieval failure.
  That is precisely the confusion Epic 7.1 (`9542963`) just spent a commit
  correcting in the other direction, and it would be careless to reintroduce
  it from the opposite side.
- `engine_version` has no meaningful value for a SERP feature.

### Recommendations

**`google_ai_overview` as a fourth engine — NO-GO.** Not because of a tier
gate (there isn't one) and not because the API is broken (it works fine on
broad queries). Because on this product's own query shapes the observed yield
of usable content is **0 of 4**, the retrieval costs two searches per prompt
where it works at all, the quota is 250/month against ~48 searches per scan,
and the one commercial-intent query that did return content changed its
citations within four minutes. No smaller version of this feature is proposed,
because none of the measurements support one.

Revisit if any of these change: a larger sample contradicts 0/4; SerpApi's
token path starts returning content; or a paid plan is taken for unrelated
reasons, at which point re-measure rather than assume.

**Persisting organic rank — GO, with the scope stated honestly.** The data is
already fetched and already thrown away, so capturing it costs no API calls —
only a column and a migration. But it is narrower than "organic rank for
tracked prompts": SERP data is collected during *competitor detection*, over
the ≤6 queries `build_queries` produces (3 brand-anchored, 3 industry-seeded),
**not** over the 20–30 tracked prompts the report is about. Whether rank on six
discovery queries can prioritise fixes for a report built on thirty different
prompts is a product question this investigation did not answer and should not
pretend to. Recommend a follow-up brief that settles that first, before any
migration.

### IP-safety self-check

No customer-facing screen changed and no code changed, so constraint 9 does not
strictly bite — recorded anyway because this handled third-party content.

Raw SerpApi responses, including AI Overview prose and publisher snippets, were
written to a scratchpad **outside the repository** purely to inspect the
payload's shape, and nothing derived from them was committed. The probe printed
structure, domains and ordinals only — no titles, no snippets, no AI Overview
text — and this entry reproduces domains and counts, never publisher copy. That
is the same transient-in-process boundary `serp.py` and `crawl.py` already hold.
No dependency was added. **IP-safety check passed:** third-party prose was read
transiently to determine a JSON shape, never persisted, never rendered, and
never committed.

### Tests

**834, unchanged** (api 571, workers 13, shared-types 53, design-system 99,
web 98). Nothing was implemented, so nothing was added. Confirmed by running
the suite rather than carried forward from the prior session.

---

## 2026-08-25 — Investigation · Detection-time organic rank cannot prioritise the fix list

**Not an epic**, for the same reason as the previous entry: `product-spec.md`
defines Epic 9 as MVP Launch Readiness, and this is speculative pre-work with
no roadmap position.

The previous investigation (`d39f70b`) ended with a *conditional* GO on
persisting organic rank, on the grounds that the data is already fetched and
already thrown away, so capturing it costs no API calls. It explicitly did not
answer whether the data is the right data. **This answers that: it is not.**
Capturing something for free and it being useful once captured are two
different questions, and the second one comes back NO.

**Outcome: NO-GO on usefulness. Nothing implemented.** No schema, no
migration, no change to `serp.py`, `competitors.py`, `prompts.py` or
`engines.py`. Suite unchanged at 834.

### SerpApi spend

Plan and quota re-confirmed live via `account.json` immediately before
spending, not carried forward from the prior session.

| | |
|---|---|
| Plan | **Free Plan**, $0.00/month |
| Quota at gate | **101 left** of 250, renews 2026-09-20 |
| Approved, in-session, before spending | **6 searches** — the exact set `build_queries()` produces for the Help Scout client |
| Actually spent | **6** (101 → 95) |
| Tracked-prompt side | **0 new calls** — reused the payloads the prior investigation already paid for |

Run through `serp.search()` itself, not a hand-rolled script, so the domains
come out of the same `registrable_domain` parsing production uses.

### A premise that did not survive contact with the data

The brief framed this as "detection's ≤6 queries versus the report's 20-30
tracked prompts". The only real client in `avp_dev` has **3 tracked prompts**,
not 24 — it was a budget-limited verification scan from the Epic 5/6 runs.

That matters beyond arithmetic. At `TARGET_PROMPTS = 24` the 45% awareness
quota yields ~11 non-branded questions, so `prompts.py`'s rule — "most
questions must NOT contain the subject brand's name" — holds. At n=3 the quota
rounds to one prompt per intent, and since the rule permits the brand in
comparison and bottom-funnel, **2 of the 3 tracked prompts name Help Scout**.
The real scan is therefore *more* brand-heavy than a real 24-prompt set would
be, which biases every overlap measured below **in favour** of the feature.
The conclusion is negative anyway, which makes it stronger, not weaker.

### Structural comparison, and why it did not settle the question

`build_queries()` produces, for this client:

```
BRAND   Help Scout alternatives / competitors / vs
  -     best customer support software
  -     top customer support software companies
  -     customer support software providers
```

Half brand-anchored by construction; half terse category keywords. The tracked
prompts are conversational buyer questions, and `prompts.py` deliberately
suppresses the brand in awareness ones because "a question that names the
brand can only confirm the brand exists; it cannot reveal whether the brand
gets discovered."

The tempting conclusion was that the two sets are structurally opposed and the
brief could close there without spending. **That reasoning was wrong, and the
live data said so.** Overlap by half:

| Detection half | domains | also rank for a tracked prompt |
|---|---|---|
| Brand-anchored (3 queries) | 16 | **11 = 69%** |
| Category-seeded (3 queries) | 17 | **4 = 24%** |

The half predicted to be least relevant overlapped *most*. The explanation is
the confound above — two of three tracked prompts name the brand, so both sets
were drawing from the same pool of Help Scout comparison publishers. Worth
recording as a caution: the structural argument was clean, plausible, and
would have produced the right verdict for the wrong reason.

### What the data actually shows

**Rank does not carry across.** Over the 12 domains present in both sets,
Spearman on best-rank: **rho = −0.434** (t = −1.52, df = 10, two-sided
p ≈ 0.16). At n = 12 that is **not distinguishable from zero**, so the honest
statement is *no positive correlation was observed* — not that it is inverted.
Either way it is not the positive relationship the feature would need.

```
eesel.ai         detection #1  -> tracked #7
zendesk.com      detection #2  -> tracked #6
kayako.com       detection #2  -> tracked #9
featurebase.app  detection #5  -> tracked #1
happyfox.com     detection #8  -> tracked #1
```

**The decisive finding is coverage, not correlation.** The fix beat operates
on *cited* domains — what engines cite when answering tracked prompts. Of the
13 cited domains in this report, **8 (62%) appear in none of detection's six
queries.** There is nothing to join on for most of the rows the feature would
be prioritising.

That includes the fix list's own output:

```
eesel.ai         6 citations   detection #1        <- would have a signal
featurebase.app  4 citations   detection #5, #9    <- would have a signal
hiverhq.com      4 citations   ABSENT              <- no row, no signal
```

One of the three domains Epic 7.1 actually names is invisible to detection.

**And the same holds for the persisted competitor set.** `thecxlead.com` ranks
**#1 twice** across detection's queries — the strongest organic performer in
the whole set — and appears in **no** tracked prompt. `freshworks.com`
(detection #2) likewise. The best detection signal available points at domains
the report never mentions.

This is structural rather than unlucky: detection samples the top ~10 of six
queries, ~30 distinct domains. The report's citations come from what engines
chose to cite across the prompt set, drawing on a far wider pool. Widening the
overlap means more queries and deeper result pages — i.e. real recurring cost,
which is the next section.

### Go / no-go — on usefulness, kept separate from feasibility-of-capture

**The prior entry's finding stands and is unchanged:** capture is free. The
positions are fetched, reduced to a scalar by `_position_weight`, and
discarded; persisting them costs no API call.

**This entry's finding: NO-GO. Do not build it.** A prioritisation signal that
covers 38% of the rows it is meant to rank, and shows no positive rank
relationship on the 38% it does cover, is not a prioritisation signal. It
would be a column that looks like evidence.

**What a genuinely useful version would require, and why it is not affordable
now.** The signal the fix beat would actually want is organic rank *for the
tracked prompts themselves* — where the client and the cited domains rank on
the questions the report is built from. That means SerpApi at report time
against 20-30 prompts:

| | Free Plan |
|---|---|
| Searches per scan (24 prompts) | ~24 |
| Plus detection's own | ~6 |
| **Per scan** | **~30 = 12% of a 250/month quota** |
| Scans per month before the quota is gone | **~8, across all clients** |

Eight scans a month is not a product. And it would be a *different* feature —
"where do you rank organically versus where you get cited by AI", which is a
new comparison worth considering on its own merits — not the "capture what we
already discard" idea this brief set out to validate. No smaller version is
proposed, because the measurements do not support one.

Revisit only if the prompt count drops, a paid plan is taken for unrelated
reasons, or the fix beat's inputs change shape. Re-measure then rather than
assuming this result carries.

### One data point, said plainly

This is **one client, one scan, three tracked prompts, six detection queries**.
It is not statistical proof of a general law, and the correlation figure in
particular is not significant at this n. The right reading is: the coverage gap
and the absent-domain cases are **consistent with** detection-time rank being
the wrong input for this job, and nothing observed here **contradicts** that.
A second client would strengthen or overturn it. Given the verdict is "do not
build", the cost of being wrong is a deferred feature, not a shipped defect.

### IP-safety self-check

No code and no customer-facing screen changed, so constraint 9 does not
strictly bite — recorded anyway because this handled SerpApi output.

Everything touched was domains, positions and counts: exactly what constraint
7 permits. Result titles and snippets were never printed, never persisted and
never committed — `serp.search()` discards them by construction, and the
tracked-prompt payloads were read from a scratchpad outside the repository and
reduced to domain + ordinal before anything was displayed. `avp_dev` was read
only; no row was written. No dependency added. **IP-safety check passed:**
domains, ordinals and counts only, nothing persisted, nothing rendered.

### Tests

**834, unchanged** (api 571, workers 13, shared-types 53, design-system 99,
web 98). Run, not assumed. Nothing was implemented, so nothing was added.

---

## 2026-08-25 — Epic 9.0 · Reading Epic 9 for the first time, and what it actually needs

Numbered, unlike the two SerpApi investigations, because Epic 9 genuinely is on
the roadmap. This entry is the **scoping and verification pass only** — nothing
was implemented, no code, schema or test changed. Suite unchanged at 834.

Epic 9 had only ever been referenced in recent sessions as a one-line paraphrase
lifted from an earlier context: *"MVP Launch Readiness (Phase 1 complete)"*. No
session had read its acceptance criteria. This reads them.

### What Epic 9 actually says, verbatim

`product-spec.md` §7, lines 155-159:

```
### Epic 9 — MVP Launch Readiness (Phase 1 complete)
- [ ] End-to-end test: URL in → report out, under 5 minutes
- [ ] Basic agency dashboard: list of past scans, re-run scan
- [ ] Pilot with 3-5 real agencies, collect feedback
- **Acceptance:** pilot agencies successfully generate and send at least one real prospect report
```

The five-minute budget is real spec text, not a build-log aside — worth
confirming, because the brief that commissioned this was right to doubt it.

### Item by item, against evidence

**1. End-to-end test, under 5 minutes — NOT STARTED, and the budget is at risk.**

No end-to-end script exists. `apps/api/scripts/` holds eight per-epic verifiers
(`verify_intake`, `verify_competitors`, `verify_competitor_override`,
`verify_scan`, `verify_scoring`, `verify_audit`, `verify_report`,
`verify_fixes`) and not one of them chains URL → report, or times anything.

The only timed scan that has ever run is in `avp_dev`:

```
scan_01M0HDRGJNWNZDSJPP0NC3SV8W  (Help Scout)
  started  2026-08-21 05:47:35Z
  finished 2026-08-21 05:49:24Z
  WALL CLOCK 109.3s   for 3 prompts / 6 engine results
  engine latency: sum 162.6s, max 42.8s
```

`PROMPT_CONCURRENCY = 4` (`scan_runner.py:43`), so those 3 prompts ran as a
**single batch**. 109.3s is therefore roughly one batch plus the one-off LLM
prompt-generation call — not three prompts in sequence. At `TARGET_PROMPTS =
24` that is six batches, extrapolating to roughly **9-11 minutes for the scan
phase alone**.

And "URL in → report out" is wider than the scan: it also covers crawl and
classification (Epic 2), competitor detection (6 SerpApi searches plus
co-citation model calls, Epic 3), the technical audit (Epic 6), scoring
(Epic 5), and Epic 8's fix-generation model call.

**Stated precisely: this is an extrapolation from one 3-prompt sample, not a
measurement.** Wall clock (109.3s) already exceeds max engine latency (42.8s)
by a wide margin, so something other than the engine calls dominates, and a
naive multiply may be wrong in either direction. That is exactly why item 1
exists, and it is why it should go first.

**2. Basic agency dashboard — PARTIALLY SATISFIED. The API is done; the UI does
not exist.**

`GET /api/v1/dashboard` is built and registered (`main.py:102`). It returns
agency identity, seat usage, client and scan counts, `is_empty`, and
`recent_scans[]` carrying id, client name, client domain, status, composite
score, created and finished timestamps. The score is **left-joined** so an
unscored or insufficient-data scan still appears with a null rather than being
filtered out. `limit` is bounded 1-50, default 10. Six tests pass, covering the
empty state, decimal-as-string encoding, null-not-zero, unscored scans,
newest-first ordering, and the limit bound.

"Re-run scan" also already has its endpoint: `POST /clients/{clientId}/scans`
(`scans.py:82`) — re-running is creating a new scan for an existing client.

What is missing is the screen. `apps/web` has exactly two routes:

```
apps/web/src/app/page.tsx                      -> sign-in / intake / classification
apps/web/src/app/scans/[scanId]/report/page.tsx -> the report
```

`grep -rn dashboard apps/web/src/` returns exactly one hit, and it is a comment
in `ReportView.tsx` saying the report is *not* a dashboard. So the frontend has
never called this endpoint.

**3. Pilot with 3-5 real agencies — NOT ENGINEERING WORK**, but it has a hard
engineering dependency, below.

**Acceptance: "generate and SEND at least one real prospect report" — BLOCKED.**

"Send" is the load-bearing word. Today a report exists only at an authenticated
`/scans/{id}/report` route inside the agency's own session. A prospect cannot
open it. There is no PDF export and no shareable link.

### The stale cross-reference this turned up

`product-spec.md` Epic 7 still reads:

```
- [ ] PDF export + shareable web link — **deferred to Epic 7.1**
```

and `api-contracts.md`'s deferred register lists `GET /reports/{token}` and
`GET /scans/{scanId}/report.pdf` under Epic **7.1**.

**Epic 7.1 has since shipped — commits `9542963`..`6f953f3` — and it delivered
the Answer Shelf and the unclaimed-domain fix. It did not deliver PDF export or
shareable links.** Both documents now point the reader at an epic that came and
went without doing the thing they promise. Same failure class as
`design-direction.md`'s "AWAITING APPROVAL" heading: a forward reference that
outlived the thing it referred to.

Not corrected here — this is a scoping pass, and the correction belongs with
whichever slice actually builds the send path, so the label lands on real work.

### The checkbox audit, and why §7 cannot be trusted as a status board

The brief asked whether an unchecked box means unstarted, citing Epic 3's
manual-override checkbox as one known case. It is not one case. It is the whole
document.

| Epic | §7 checkbox state | Reality (live rows in `avp_dev`) |
|---|---|---|
| 0 Design system | all `[ ]` | shipped; 99 tests in the package |
| 1 Infra & auth | all `[ ]` | `agencies` 1, `users` 1, `invitations` table present |
| 2 Intake & classification | all `[ ]` | `clients` classified: 1 |
| 3 Competitor detection | all `[ ]` | `competitor_sets` 1, `competitors` 5 |
| 4 Prompts & engines | all `[ ]` | `prompt_sets` 1, `engine_results` 6 |
| 5 Scoring | all `[ ]` | `scores` with composite: 1 |
| 6 Technical audit | all `[ ]` | `technical_audits` 1 |
| 7 Report | `[x] [~] [ ]` | the only epic with any marks |
| 8 Fix generator | all `[ ]` | `action_items` 5 |

Eight epics shipped, live-verified, each with a build-log entry recording its
acceptance criterion met — and all eight still read as untouched. **§7's
checkbox state carries no signal in either direction and must not be used to
decide what is done.** Every "is this built?" question has to be answered
against code or data, which is what this pass did.

### Two spec items that are stale rather than pending

- **Epic 4, "start with 2 engines (e.g., ChatGPT API + Perplexity API)"** —
  shipped as Claude parametric + Claude web search, one vendor, deliberately
  (build-log Epic 4.2, "Two engines, one vendor"). §5.1's "direct API calls to
  ChatGPT/Perplexity/Gemini" is stale the same way. The abstraction the item
  actually asks for (`EngineAdapter`) exists.
- **Epic 4, "Playwright-based runner for engines without clean APIs (AI
  Overviews)"** — the two investigations committed earlier today (`d39f70b`,
  `d2d5f40`) concluded AI Overview capture is a NO-GO on the current plan. This
  box is not pending work; it is work that has been measured and declined, and
  should read that way.

### Slice order, and the reasoning

Epic 9 is three unrelated kinds of work — a measurement, a screen, and a GTM
activity — plus one blocking dependency the epic does not mention. Ordering is
**highest-risk-first**, because exactly one of these items can invalidate the
others.

**Slice 1 — the end-to-end timing harness.** `scripts/verify_e2e.py`: URL in →
report out, timed per phase, run once at a real prompt count. This goes first
because it is the only item whose outcome can reshape the rest. If a full scan
takes eleven minutes rather than five, that is an architecture question —
concurrency limits, queueing, whether the UI needs progress and polling rather
than a request that waits — and it changes what the dashboard has to display.
Building the screen first and *then* learning scans take eleven minutes means
building it twice.

Concretely, the next brief should: chain client creation → crawl/classify →
competitor detection → scan → audit → score → fix generation → report
projection; emit a per-phase timing table plus a total; run at `--prompts 24`
once; and record the result against the five-minute line.

It **spends real money** — roughly 48 engine calls plus sentiment calls, ~6
SerpApi searches, a classification call, a prompt-generation call and a
fix-generation call. That brief must state the spend and get approval before
running, per this project's convention, and should note that SerpApi sits at 95
of 250 for the month.

**Slice 2 — the dashboard screen.** Thin, because the API is finished and
tested: a `/dashboard` route listing recent scans with client, domain, status,
score and date, linking each to its report, plus a re-run button on the existing
POST. Deliberately second: its empty/loading/progress states depend on what
slice 1 measures.

**Slice 3 — the send path.** A shareable token link or PDF export, whichever is
chosen. Epic 9's acceptance cannot be met without one, and this is where the
stale "Epic 7.1" references in `product-spec.md` and `api-contracts.md` get
corrected onto real work. A signed token link is materially cheaper than
server-side PDF rendering and should be costed first.

**Slice 4 — the pilot.** Not engineering, and genuinely blocked until slice 3.

**Riding along, free:** reconcile §7's checkboxes to reality and re-label the
two stale Epic 4 items. Zero risk, and it stops the next reader inheriting the
same false picture this pass had to dig through. Best attached to slice 1 so it
is not left as its own never-scheduled chore.

### IP-safety self-check

No code changed and no customer-facing screen changed, so constraint 9 does not
strictly bite — recorded because the pass read live client data. `avp_dev` was
read only: row counts, timestamps and table names. No engine text, no competitor
prose, no third-party content was read, printed or committed. No SerpApi calls
were made and no dependency added. **IP-safety check passed:** counts,
timestamps and schema names only.

### Tests

**834, unchanged** (api 571, workers 13, shared-types 53, design-system 99,
web 98). Run live at the start of the pass, not assumed. Nothing implemented.

---

## 2026-08-25 — Epic 9.1 · The end-to-end timing harness, and where the eight minutes actually go

Slice 1 of the order set out in Epic 9.0. `scripts/verify_e2e.py` chains URL →
report against a **fresh** client and times each phase separately. Epic 9.0 had
one data point — a 109.3s wall clock over 3 prompts against a 42.8s worst
engine call — and no way to tell whether the unexplained remainder was engine
latency, queueing, sequential steps that could overlap, or database round
trips. Guessing wrong in either direction would have mis-shaped the dashboard,
so this was built before slice 2 rather than after.

### Spend, stated before it was spent

| | |
|---|---|
| SerpApi quota, re-verified live immediately before the run | **155 used, 95 left** of 250 |
| Approved and spent | **6 searches** — exactly what `build_queries()` emits |
| SerpApi quota after the run | **161 used, 89 left** — the 6 predicted, no more |
| Anthropic | **48 engine calls + 41 sentiment + 1 classify + 4 co-citation + 1 prompt-generation + 1 fix-generation = 96 `claude-opus-5` calls** |

Epic 9.0 recorded SerpApi as "95 of 250 **used**". The live figure was 95
**remaining**. The direction was recorded backwards; corrected here.

Before spending anything the harness was validated end to end against `avp_test`
with every paid boundary faked using the shapes `tests/` already uses — a full
24-prompt run through the real orchestration, real DB writes and the real table,
for zero calls. That caught four defects (a crash on an empty agency table, a
freshness check comparing the raw argument against the normalised domain, an
`InvalidUrl` escaping uncaught, and two ORM fields that only exist on the report
projection) which would otherwise have surfaced *after* 48 engine calls were
already spent.

### The measurement

`basecamp.com`, 24 prompts, one run. `scan_01M0VPVXWM94S9534SDD12YYE0`.

```
  phase                      epic          secs      %    db  external calls
  client creation            —              0.0   0.0%     3  -
  crawl + classify           2             10.2   2.0%     1  anthropic x1, playwright x1
  competitor detection       3             21.2   4.3%     6  anthropic x4, serpapi x6
  prompt generation          4             15.4   3.1%     2  anthropic x1
  scan loop                  4            419.7  84.2%    10  anthropic x65
  technical audit            6              8.6   1.7%     3  http x1
  scoring                    5              0.0   0.0%     8  -
  fix generation             8             23.0   4.6%    12  anthropic x1
  report projection          7/7.1          0.0   0.0%    18  -
  TOTAL                                   498.2 100.0%    63
```

**498.2s — eight minutes and eighteen seconds. The 5-minute budget is MISSED by
198.2s, a factor of 1.66.**

### Engine latency, or something else? Engine latency — with one large caveat

This is the question the brief existed to answer, and the breakdown answers it
rather than leaving it inferred.

**It is engine latency.** The scan loop is 84.2% of the total. Summed engine
latency was 1648.0s across 48 calls at a 24.9s median. Perfectly packed at
`PROMPT_CONCURRENCY = 4` that is 412.0s; the loop actually took 419.7s. **The
loop is running at 98% of the best it could do at its configured concurrency.**

That result rules out the three alternatives explicitly, which is the part Epic
9.0 could not do:

* **Not database round trips.** The entire pipeline — nine phases, 48 engine
  results, 5 fixes, a full report projection — issued **63 SQL statements**.
  The report projection issues 18 of them and takes 0.0s.
* **Not queueing.** There is no queue; every phase runs inline in one process.
* **Not blocking CPU work on the event loop.** `extract_facts` was the obvious
  suspect, running synchronously between every engine call. Measured directly
  on a realistic 5,940-character grounded answer with 5 competitors and 8
  citations: **0.47 ms**, or 0.02s across all 48 calls. Hypothesis tested and
  rejected rather than assumed.

**The caveat, and it is a big one.** One call consumed **271.6s and returned
nothing** — `claude_search`, status `error`, `PROVIDER_UNREACHABLE`. That single
dead call is **16.5% of all engine time in the run**, and it is why the scan
finished `partial` rather than `succeeded`.

It is not inherent model latency. `engines.py:63` sets `DEFAULT_TIMEOUT = 90.0`
and passes it to `AsyncAnthropic(timeout=...)`, but that bounds **each
attempt**, not the call. With the SDK's default `max_retries=2` the real ceiling
is 90s × 3 attempts ≈ 270s — which is 271.6s to within rounding. The timeout
that looks like a 90-second guarantee is a 270-second one.

The grounded engine's distribution shows how isolated this is:

```
claude_search : 11 14 15 16 16 17 19 20 21 21 24 25 26 26 31 33 35 38 39 42 44 | 117 118 | 272
claude        : 13 15 16 17 17 18 19 20 20 21 22 22 23 26 27 27 29 32 32 33 34 36 46 46
```

21 of 24 grounded calls land between 11s and 44s. The tail is three calls, and
the worst of them is a failure.

### Candidate fixes — named, not built

Out of scope for this slice by the brief. Recorded in cost order:

1. **Bound the whole engine call, not each attempt** (`engines.py:63,149`).
   Pass `max_retries` explicitly or wrap the request in a single deadline. A
   call that has already spent 90s twice is not going to succeed on the third.
   Recovers up to ~210s of slot time in the pathological case.
2. **Raise `PROMPT_CONCURRENCY` from 4** (`scan_runner.py:42`). This is the
   binding constraint: the loop tracks `sum ÷ concurrency` to within 2%, so in
   this region the lever is close to linear. 24 prompts × 2 engines = 48 calls
   are available and 4 prompts are permitted. **The single biggest lever, and a
   one-line change** — but its ceiling is the slowest single prompt, so it must
   be taken together with fix 1, not instead of it.
3. **Overlap the technical audit with the scan loop** (8.6s). It needs only the
   client's domain; it does not read a single scan result.
4. **Overlap the two detection signals** (`competitors.py:479-483`). SerpApi
   `search_many` and co-citation `run_seed_prompts` are independent and are
   awaited one after the other. Perhaps 8-10s of the 21.2s.

Fixes 1 and 2 together plausibly land the total near 250-300s — at or just
under the line, not comfortably inside it. Worth saying plainly rather than
presenting the budget as recoverable by a one-line change.

### What this means for slice 2 — it must show progress, not wait

**The dashboard cannot be a synchronous request that waits.** At 498.2s
measured, and 250-300s even on the optimistic side of the fixes above, a
request-and-wait design fails on the numbers, and would fail against ordinary
proxy and browser timeouts well before the user's patience.

So slice 2 needs incremental progress: a queued scan, a status the client polls,
and something truthful on screen while it runs. Two findings from this run feed
that design directly. The phase table is the natural progress vocabulary — nine
named stages with real relative weights, of which one is 84% and needs its own
sub-progress rather than a single spinner. And the run finished `partial`, not
`succeeded`, on an ordinary healthy site — so `partial` is a normal state the
dashboard must render honestly, not an edge case to design around later.

### IP-safety self-check

No UI changed, so constraint 9 does not strictly bite; recorded because the run
touched live third-party data. The harness prints counts, durations, statuses,
domains and call tallies. No engine answer text, no competitor prose and no
crawled copy is printed, stored or committed — the raw answers stayed transient
inside the worker exactly as ip-safety.md #7 requires, and what persisted is the
usual facts plus a SHA-256 digest. The latency figures above are timings, not
content. No dependency was added. **IP-safety check passed:** durations, call
counts, statuses and domains only.

### Tests

**834, unchanged** (api 571, workers 13, shared-types 53, design-system 99,
web 98). Run live at the start of the pass and again after the script landed,
not assumed. `verify_e2e.py` is a script, not a test, and adds no test cases;
`ruff check` is clean on it.

---

## 2026-08-25 — Epic 9.2 · The 90-second timeout that was really 271.6 seconds

Slice 1.5, and the follow-up Epic 9.1 named but deliberately did not build. Epic
9.1's headline (498.2s total, 419.7s scan loop) contained one `claude_search`
call that consumed **271.6s and returned nothing** — 16.5% of all engine time in
that run, and the reason the scan finished `partial`. That is a latent defect,
not model latency, and leaving it in would have baked a known bad number into
every estimate slice 2 draws from.

### The SDK's real behaviour, confirmed rather than assumed

Epic 9.1 inferred the cause as "90s × 3 attempts ≈ 270s". The inference was
right, but this pass checked it against the pinned SDK instead of trusting the
arithmetic — `pyproject.toml` allows anywhere in `anthropic>=0.40,<1`, and
`uv.lock` pins **0.125.0**.

| Claim | Where it was verified |
|---|---|
| `DEFAULT_MAX_RETRIES = 2` | `anthropic/_constants.py:10`, in the installed 0.125.0 — not from memory |
| Timeouts **are** retried | `APITimeoutError` **subclasses** `APIConnectionError` (`_exceptions.py:99`), and `_should_retry_exception` returns `True` for any `APIConnectionError` unconditionally (`_base_client.py:895`) |
| 3 attempts, not 3 tries-then-one | the retry loop is `for retries_taken in range(max_retries + 1)` (`_base_client.py:1131`) |
| The 90s was not silently rescaled | `messages.create` only recomputes a non-streaming timeout when `client.timeout == DEFAULT_TIMEOUT` (`resources/messages/messages.py:1029`). Passing `90.0` opts out, so 90.0 stood |
| Backoff between attempts | `INITIAL_RETRY_DELAY 0.5` doubling toward `MAX_RETRY_DELAY 8.0`, times jitter in `(0.75, 1.0]` → at two retries, **1.125–1.5s** |

The concern worth checking was the real one: **some SDKs retry only 5xx and rate
limits, not connection timeouts.** This one does not — a timeout is an
`APIConnectionError` subclass and is always retried. Had that gone the other
way, the 271.6s would have needed a different explanation entirely.

Reproduced directly, with a fake httpx transport and no network:

```
kwargs engines.py passed: {'timeout': 90.0}          <- no max_retries at all
PRE-FIX: 3 HTTP attempts, status=error, code=PROVIDER_UNREACHABLE
implied worst case: 3 x 90.0s = 270.0s + backoff
```

270.0s + 1.125–1.5s backoff = **271.1–271.5s**, against **271.6s** observed.
Epic 9.1 called the residual "rounding"; it was the retry backoff. Both the
duration *and* the exact error code reproduce.

**A second defect fell out of the same read.** `_map_error` tested
`APIConnectionError` before `TimeoutError`, and since `APITimeoutError`
subclasses it, every SDK timeout was reported as `PROVIDER_UNREACHABLE` —
which is exactly what Epic 9.1 recorded. `EngineResultStatus.TIMEOUT` was
unreachable on this path. Fixed by ordering the check correctly.

### The fix, and the trade-off taken openly

`engines.py` now states a ceiling and enforces it:

```python
DEFAULT_TIMEOUT = 60.0          # per attempt (was 90.0)
MAX_RETRIES = 1                 # explicit — never inherited from the SDK
RETRY_BACKOFF_ALLOWANCE = 2.0
ENGINE_CALL_CEILING = 122.0     # 60 x 2 + 2
```

`max_retries` is now passed to `AsyncAnthropic` explicitly, and `ask()` wraps
the request in `asyncio.timeout(ENGINE_CALL_CEILING)` so the bound holds by
construction rather than as an arithmetic claim about someone else's internals
across a `>=0.40,<1` version range.

**Why `max_retries=1` and not `0`.** The brief offered both. Epic 9.1's own
latency distribution decided it: two grounded calls landed at **117s and 118s**.
No single attempt could exceed the 90s per-attempt bound, so each of those was a
timed-out attempt plus a retry that **succeeded** at ~27s. `max_retries=0` would
have converted 2 of 48 calls (4.2%) from answers into `PROVIDER_UNREACHABLE`.
The retry is earning its place.

**Why 60s and not 90s.** The slowest call that actually succeeded in that run
was 46s, and 21 of 24 grounded calls landed between 11s and 44s. 60s keeps ~30%
headroom over the slowest observed success while letting two attempts sum to
something bounded.

**What this costs, stated rather than buried.** A genuinely slow-but-alive call
between 60s and 90s that would previously have completed now fails as a timeout,
and one fewer retry means more transient failures surface as a status instead of
silently recovering. That is the price of a ceiling that is actually a ceiling.

### The test proves the ceiling, not "it eventually returns"

`tests/test_engine_timeout.py`, 6 cases. The weak property — "the call returns"
— was satisfied by the 271.6s call too, so it proves nothing. These bound it:

* a call whose every attempt hangs **returns inside the deadline**, measured on
  the clock, against a transport that ignores per-attempt timeouts entirely
* exactly `MAX_RETRIES + 1` HTTP attempts, asserted *not* to equal the SDK
  default's 3
* `max_retries` and `timeout` are asserted **as constructor kwargs**, because a
  dropped kwarg silently restores the SDK default — which is the whole bug
* `ENGINE_CALL_CEILING` is pinned at 122.0 and asserted below 271.6

There was no existing way to inject a timeout into the Anthropic client, so the
minimal fake is a pair of `httpx.AsyncBaseTransport` subclasses. The Anthropic
client itself stays real, so the retry budget under test is the one `engines.py`
genuinely passes. **5 of the 6 fail against pre-fix `engines.py`**; the sixth is
a regression guard on unchanged behaviour (a real connection failure is still
`PROVIDER_UNREACHABLE`).

### Spend, stated before it was spent

| | |
|---|---|
| SerpApi, re-verified live immediately before the run | **161 used, 89 left** of 250 |
| Approved and spent | **6 searches** |
| SerpApi after the run | **167 used, 83 left** — the 6 predicted, no more |
| Anthropic, predicted | ~96 `claude-opus-5` calls, from Epic 9.1's split |
| Anthropic, actual | **103** — 48 engine + 48 sentiment + 1 classify + 4 co-citation + 1 prompt-generation + 1 fix-generation |

The 7-call overshoot is honest and explainable: sentiment is only spent where an
answer mentions the brand. Epic 9.1 spent 41 (of 48); this run spent 48, because
**every** answer mentioned the subject. 103 is the top of the 55–103 band the
script's own docstring documents.

### The re-measurement

`linear.app`, 24 prompts, one run. `scan_01M0VRFGKSSAAZNZ9AXVJ77KSS`. A fresh
domain, so Epic 9.1's `basecamp.com` rows stay independently inspectable.

```
  phase                      epic          secs      %    db  external calls
  client creation            —              0.0   0.0%     3  -
  crawl + classify           2              8.3   2.3%     1  anthropic x1, playwright x1
  competitor detection       3             20.9   5.8%     6  anthropic x4, serpapi x6
  prompt generation          4             14.6   4.1%     2  anthropic x1
  scan loop                  4            288.5  79.9%    10  anthropic x72
  technical audit            6              8.4   2.3%     3  http x1
  scoring                    5              0.0   0.0%     8  -
  fix generation             8             20.3   5.6%    12  anthropic x1
  report projection          7/7.1          0.0   0.0%    18  -
  TOTAL                                   361.3 100.0%    63
```

| | Epic 9.1 | Epic 9.2 | change |
|---|---|---|---|
| total | 498.2s | **361.3s** | −136.9s (−27.5%) |
| scan loop | 419.7s | **288.5s** | −131.2s (−31.3%) |
| slowest single engine call | **271.6s** | **91.5s** | −180.1s |
| scan status | `partial` | **`succeeded`** | 48/48 results `ok`, zero errors |
| over the 300s budget by | 198.2s (1.66x) | **61.3s (1.20x)** | −136.9s |

### Is the improvement really the fix? Yes — and here is the check

The two runs use different subjects, which is a genuine confound the fresh-domain
requirement forced. Decomposing the engine time settles it:

* Epic 9.1: 1648.0s summed across 48 calls, of which **271.6s was the dead
  call** → **1376.4s across 47 live calls, 29.3s mean**
* Epic 9.2: **1353.4s across 48 live calls, 28.2s mean**

Live engine latency is within 1.7% between the two runs. The subjects differ;
their engine latency does not. **The improvement is the removed dead call, not a
friendlier website.**

The 91.5s slowest call is itself the trade-off working as designed: above the 60s
per-attempt bound, so it was a timed-out attempt plus a retry that succeeded at
~31s. Under `max_retries=0` that would have been a failure and this scan would
have finished `partial` like the last one.

**The outlier is gone.** No call can now exceed 122.0s by construction, and the
worst observed was 91.5s.

### The budget is still missed. This fixed one defect, not the timing problem

**361.3s against a 300s budget — over by 61.3s.** Removing a 271.6s dead call
recovered 27.5% and was not enough. Candidate fix 2 from Epic 9.1 — raising
`PROMPT_CONCURRENCY` from 4 — remains **unbuilt and untouched here**, exactly as
Epic 9.1 sequenced it: raising concurrency before the timeout was bounded would
have multiplied a hung call across more parallel slots. That ordering constraint
is now discharged, so fix 2 is unblocked. Fixes 3 and 4 (overlapping the
technical audit; overlapping the two detection signals) are also still unbuilt.

One correction to Epic 9.1's analysis while here. It reported the loop "running
at 98% of the best it could do", from `summed ÷ concurrency` = 1648.0 ÷ 4 =
412.0s. That model is not a floor: this run's loop took **288.5s against a
`summed ÷ concurrency` of 338.4s**, beating it outright, because each prompt runs
its two engines concurrently *inside* one of the 4 slots. The script's own
printed floor (per-prompt max engine latency + sentiment, ÷ concurrency) gives
245.7s here, leaving 42.8s (15%) of genuine unexplained overhead. The loop has
more headroom than Epic 9.1's figure implied.

### What this means for slice 2 — still "show progress", but the margin moved

Epic 9.1's requirement was "must show progress, not wait." **That does not
change at 361.3s.** Six minutes fails a request-and-wait design on ordinary
proxy and browser timeouts long before it fails the user's patience, so slice 2
still needs a queued scan and a polled status.

What *has* changed is the shape of the problem, in two ways worth carrying into
slice 2's design:

* **The tail is no longer pathological.** The progress UI must represent a
  ~360s job with a bounded worst-case call, not a 498s job with an unbounded
  one. A single stalled phase can no longer sit for 4½ minutes.
* **`partial` is no longer the expected outcome.** Epic 9.1 finished `partial`
  on an ordinary healthy site *because of this bug*, and concluded the dashboard
  must treat `partial` as normal. This run finished `succeeded` with 48/48
  results `ok`. `partial` must still render honestly — real provider errors
  remain possible — but it is a genuine degradation to surface, not the routine
  case.

Slice 2 should not read this as permission to simplify to "refresh to check".
It is not near budget; it is 20% over it.

### IP-safety self-check

No UI changed, so constraint 9 does not strictly bite; recorded because the run
touched live third-party data. `engines.py`'s facts-only boundary is untouched —
`EngineAnswer.text` is still transient, and the new test asserts on statuses,
error codes, attempt counts and elapsed time, never on answer content. The new
fake transports carry no third-party text: one raises a timeout, the other
sleeps. No engine prose, competitor copy or crawled text was printed, stored or
committed. No dependency was added. **IP-safety check passed:** durations,
attempt counts, statuses, error codes and domains only.

### Tests

**840, up from 834** (api 571 → **577**, workers 13, shared-types 53,
design-system 99, web 98). Run live at the start of the pass at 834, and again
after the fix. `ruff check` clean across `src/` and `tests/`; `mypy` unchanged at
its pre-existing 38 errors, none of them new and none in the changed lines.

---

## 2026-08-25 — Epic 9.3 · The agency dashboard, and the progress it cannot honestly show

Slice 2 of Epic 9's ordering, built after slice 1 (Epic 9.1's measurement) and
slice 1.5 (Epic 9.2's timeout fix) precisely so the screen's loading design
would be decided by real numbers. It was — and the numbers pointed somewhere
neither slice predicted.

Epic 9.0 found the API finished: registered, tested, left-joining the score so
an unscored scan still appears. The gap was the screen. This is the screen.

### The design decision, and the finding that made it

Epic 9.1 concluded slice 2 "must show progress, not wait", and proposed "a
queued scan, a status the client polls". At Epic 9.2's measured **361.3s** that
instinct is still right: six minutes fails a request-and-wait design on ordinary
proxy and browser timeouts well before it fails the user's patience.

**But polling is not buildable against today's API, and the reason is
structural.** Re-reading `POST /clients/{clientId}/scans` for this brief turned
up something Epic 9.1's harness never had to notice, because it called the
service layer directly rather than the endpoint:

* The endpoint runs the **entire pipeline inline** and calls `db.commit()`
  **once, after the scan finishes** (`routers/scans.py`). `session_scope` is
  explicit about this — "Commit is explicit in the service layer."
* `get_or_create_scan` creates the scan row inside that uncommitted
  transaction (`competitors.py:562`).

So for the whole 361 seconds, **the running scan is invisible to every other
request.** `GET /api/v1/dashboard` opens its own session and cannot see it. A
polling UI would poll for a status that structurally cannot appear, then watch
the scan materialise already-finished.

**This screen therefore does not poll.** Not because 361.3s is fast — it is 20%
over budget — but because there is nothing to poll. It states the wait honestly
instead, following the precedent the intake screen set: no progress bar, because
we cannot measure real progress and a fake one is a lie the user will notice.

Step 7 answers itself. The dashboard API needs no new field and no phase-level
progress endpoint. Adding either would be dressing a window onto a wall. The
real blocker is that **scans are synchronous**, and making progress real means
making a scan a queued job that commits `QUEUED` before it starts working.
That is a backend change well outside a frontend brief, so it is recorded here
rather than smuggled in. **Epic 9's remaining timing work and this are now the
same piece of work.**

### What re-run does, given that

The same finding decides the button. `get_or_create_scan` reuses the most recent
`QUEUED`/`RUNNING` scan rather than creating a second one — but only among
**committed** rows. A second click arrives on a second session that cannot see
the first's uncommitted scan, so the reuse never fires and the second request
creates a **second scan and spends a second scan's worth of model calls.**

The server cannot defend itself here. So the screen does:

* re-run is disabled on **every row of a client** that has a `QUEUED` or
  `RUNNING` scan, not just the row that is running — an older finished row must
  not be a side door to the same double-spend
* re-run is disabled while this browser's own request is in flight, which is the
  only double-submit guard available from the client
* a failed re-run is surfaced above the table rather than swallowed

This is a guard, not a fix. The fix is the queued job above.

### The screen

`apps/web/src/app/dashboard/page.tsx` owns fetching and re-run state;
`components/dashboard/DashboardView.tsx` is pure, so it renders to static markup
and can be asserted over the way `ReportView` is. Agency identity and seat usage
in the header, then recent scans: client name and domain, status, score, started
and finished, a link to `/scans/{scanId}/report`, and the re-run action. The
home screen gained a Dashboard link, since a route nothing reaches is half a
feature.

Three states the endpoint distinguishes, which the screen now distinguishes too:

* **`isEmpty`** — no clients and no scans. Written empty state with a way
  forward, not a table with headers and no rows.
* **clients but no scans** — `isEmpty` is *false* here, because the endpoint
  sets it only when both counts are zero. Reachable, and previously would have
  rendered as a blank table with no explanation. It now says so in words.
* **`partial`** — its own badge tone, keeping its score. A partial scan
  measured something on fewer answers. Epic 9.1 called `partial` the routine
  case; Epic 9.2 showed it was routine *because of the timeout bug* and its own
  run finished `succeeded`. It is now rendered as a genuine degradation rather
  than either a success or a failure.

`compositeScore` arrives as a **string** and is null for both "not scored yet"
and `INSUFFICIENT_DATA`. Neither is a zero, and the endpoint left-joins the score
precisely so those rows survive — so the cell is an em dash. An endpoint careful
about a degraded scan is worth nothing if the screen renders it as 0.

### No new design-system primitive was needed

Every element is an existing export: `Button`, `Card`/`CardBody`, `Badge`,
`VisibilityBadge`, `DataTable`. `Badge`'s documented purpose is *system state*
("scan failed, quota low, engine unavailable") and its tones are kept disjoint
from the visibility ramp, which is exactly the scan-status/score split this
screen needs — the status chip cannot be misread as a score. Epic 0's
build-before-screens discipline paid here: nothing had to be invented, and
nothing had to be reached for outside the system.

### Tests

**865, up from 840** (api 577, workers 13, shared-types 53, design-system 99,
web **98 → 123**). Run live at the start of the pass at 840 and again after.

The 25 new cases are deliberately the states `test_dashboard.py` already proves
the endpoint produces — empty, unscored-still-appears, null-not-zero,
newest-first, a bounded page — re-asserted at the point they reach a human,
plus each scan status, the re-run states above, and UTC-stable timestamps
(`toLocaleDateString` would render differently in CI than in a browser and make
every assertion untrustworthy). `pnpm --filter @avp/web typecheck` is clean and
`next build` emits `/dashboard` as a static route.

### IP-safety self-check

* **#1** — designed from the data model and the user goal. The columns are the
  fields `ScanSummaryOut` actually carries; no competitor product was opened,
  referenced or described.
* **#2** — every element imports from `@avp/design-system`. No new primitive was
  added and none was needed. The only utility classes used are the design
  system's own token utilities from its Tailwind preset — which *replaces*
  Tailwind's palette, spacing, font and radius scales rather than extending
  them, so an off-system class like `bg-slate-500` does not compile. **No ad hoc
  Tailwind on this screen.**
* **#3** — the narrative-report structure is the report screen's obligation. This
  is a list of scans and is honest about being one; it does not present a grid
  of metric tiles as analysis.
* **#4** — no icons, illustrations or fonts added.
* **#7/#8** — the screen renders identity, statuses, a score, timestamps and
  domains. No engine answer text, no competitor prose, no marketing copy; a test
  asserts the rendered markup carries none. All microcopy is newly written.

**IP-safety check passed:** designed from `ScanSummaryOut` and the user goal;
design-system components and token utilities only, no ad hoc Tailwind, no new
primitive; no third-party prose rendered, asserted in test.

### Still open, and not touched here

The budget is **not** met — Epic 9.2 left it at 361.3s against 300s. Candidate
fix 2 (`PROMPT_CONCURRENCY`) and fixes 3 and 4 remain unbuilt, and the
synchronous-scan finding above is now the largest single item in Epic 9's
remaining scope: it blocks real progress UI and it is what makes a double click
cost money. Slice 3 (the send path) is untouched.

---

## 2026-08-25 — Epic 9.4 · Scoping the async scan: what apps/workers actually is, and what it would take

Investigation only. Nothing implemented. Epic 9.3 found that
`POST /clients/{clientId}/scans` runs inline and commits once at the end, so a
running scan is invisible for its full duration — which blocks both real
progress UI and a genuine fix for the re-run double-spend. This pass traces what
changing that actually costs, before a build brief commits to it.

### apps/workers is scaffolding. It has never been connected to anything

The Epic 1 checkbox "Redis + job queue setup" is `[x]`, Celery is a real
dependency, and `apps/workers` contains a carefully configured Celery app. None
of that is wired to the scan pipeline. Traced rather than assumed:

| Question | Answer, from code |
|---|---|
| What tasks exist? | `avp.health.ping`, `avp.health.echo`, `avp.health.check_datastores`. That is **all** of `tasks/`. |
| Does `apps/api` import `avp_workers`? | **No.** Two grep hits are prose in docstrings — `redis_client.py` says "see apps/workers", and `health.py` says "an orchestrator" meaning a *container* orchestrator. Zero code references. |
| Could it? | Not without a new dependency. `apps/api/pyproject.toml` does not depend on `avp-workers`; the path dependency runs the other way (`avp-workers` → `avp-api`). `orchestrator.py` states the API "must not import worker code" — the direction is deliberate. |
| Who uses `CeleryOrchestrator`? | Its own test, and a README snippet. **No production caller.** |
| Is a worker process deployed? | **No.** `infra/deploy/docker-compose.yml` defines `postgres` and `redis` only. No worker service exists anywhere. |
| Do the configured queues exist? | `task_routes` routes `avp.engine.*` and `avp.audit.*` to `engines` and `audits`. No task matches either pattern. |

So the checkbox is *literally* true — a queue is configured and a health task
round-trips through it — and materially false for the scan pipeline. It was
reconciled to `[x]` this session on the strength of the package existing. It is
worth being precise now: **the queue works and does nothing.**

### Two promises from Epic 1.2 that were never kept

Epic 1.2 chose Celery behind an abstraction, and stated the design that would
make that choice safe. Neither half survived contact with Epic 2 onward:

1. **"Pipeline code from Epic 2 onward depends on the `Orchestrator` protocol,
   not on Celery directly."** It does not. Every stage from Epic 2 on was built
   inline in `apps/api/services/`, and the protocol has no callers. The seam
   that was supposed to keep Temporal a live option is intact and unused —
   which, to be fair, is also why swapping executors now is cheap.
2. **"Each stage is written to (a) check whether its output already exists and
   return it if so, and (b) write its output before returning."** Only (b)
   happened. `build_prompt_set` (`scan_runner.py:69`) calls the paid
   `generate_prompts` unconditionally — there is no existence check anywhere in
   the scan pipeline. Epic 1.2 traded Temporal's durable execution *for* this
   discipline, and the discipline was not implemented, so the trade did not pay.

`product-spec.md` §5.2 scopes `/apps/workers` as "Celery/Temporal scan workers",
and §5.3's data model defaults `Scan.status` to **QUEUED**. A queued-scan design
was the original intent. `get_or_create_scan` overrides that default to RUNNING
at creation, and `api-contracts.md` documents the endpoint as running
"synchronously" — describing what was built, not what was designed.

### What the change concretely requires

**(a) The endpoint becomes two-step.** Create the scan `QUEUED`, **commit**,
return immediately with the scan id; something else executes it. The executor
choice is discussed below, but note what it is *not*: the thing that unblocks
polling is the **commit boundary**, not the executor. Any executor works once
the row commits early.

**(b) `run_scan` needs less change than expected.** It already sets the terminal
status itself — `SUCCEEDED` / `PARTIAL` / `FAILED` with `finished_at` and
`error_code` (`scan_runner.py:287-295`) — and flushes. What is missing is only
`commit()` at the boundaries, plus an exception path: today a crash propagates
and `session_scope` rolls back, which is harmless while nothing is committed. On
early commit, a crash would leave the row stuck at `RUNNING` forever, so the
executor must commit a `FAILED` terminal state in an `except`.

**And the read side is already finished.** `GET /api/v1/dashboard` has no status
filter and LEFT-joins the score, so a `QUEUED`/`RUNNING` scan already appears
with a null score — `test_unscored_scan_still_appears` proves exactly that
against a QUEUED row. Epic 9.3's `DashboardView` already renders both statuses,
already disables re-run on them, and already treats a null score as an em dash.
**No frontend change and no dashboard-API change is needed.** Commit timing is
the entire blocker.

**(c) `get_or_create_scan` needs no logic change — and early commit does not fix
the double-spend.** Traced, not assumed:

* *Today:* request B's `SELECT` cannot see request A's uncommitted scan for
  ~303s, so B creates a second scan. Both run fully. Nothing catches it.
* *After early commit:* the window shrinks from ~303 seconds to the few
  milliseconds between B's `SELECT` and A's `COMMIT`. Two requests that
  interleave inside that window still both find nothing, both `INSERT`, both
  commit. **Still two scans, still double spend.** This is an ordinary
  check-then-act race; committing earlier narrows it and cannot close it.
* *The one existing safety net* is `UniqueConstraint("scan_id")` on
  `prompt_sets`, which stops a second run against the **same** scan row. It does
  not help here, because the race produces two *different* scan rows — and even
  where it fires, it fires *after* `generate_prompts` has already been paid for,
  as a 500 rather than a clean conflict.

So step 10's answer is **no**: a second slice is required. The right mechanism
is a **partial unique index** — one unfinished scan per client:

```sql
CREATE UNIQUE INDEX uq_scans_one_open_per_client
    ON scans (client_id) WHERE status IN ('queued', 'running');
```

`status` is VARCHAR-backed with a CHECK constraint (`native_enum=False`), so a
partial index over it is straightforward. It encodes the invariant
`get_or_create_scan` already intends, and enforces it against every code path
rather than the one that remembered to check.

**Two things block that index, and both are real:**

* **Detection creates placeholder RUNNING scans.**
  `POST /clients/{clientId}/competitors` also calls `get_or_create_scan` and
  **commits** (`routers/competitors.py:97-101`). A detect-only run therefore
  leaves a committed scan at `RUNNING` that nothing is running. `RUNNING`
  currently means "open", not "executing". (No such rows exist in `avp_dev`
  today — both detections there were followed by a scan in the same session —
  but the path is reachable.) That conflation has to be resolved first, and it
  has a **live consequence in Epic 9.3's screen**: a placeholder row renders as
  "Running…" and disables re-run for that client indefinitely.
* **A dead executor strands a row at RUNNING forever**, which under the index
  becomes a permanent block on that client's scans. A stale-scan reaper or
  heartbeat is therefore not optional.

### What does NOT need to change

Epic 9.2's phase table settles this — the scan loop is 79.9% of the total and
everything else is seconds. Mapped onto the endpoints that actually exist
(the pipeline is driven by ~6 separate requests, not one):

| Endpoint | Measured | Verdict |
|---|---|---|
| `POST /clients` (crawl + classify) | 8.3s | fine synchronous |
| `POST /clients/{id}/competitors` | 20.9s | fine synchronous |
| **`POST /clients/{id}/scans`** | **~303s** (prompt gen 14.6 + loop 288.5) | **the only one that needs this** |
| `POST /scans/{id}/audit` | 8.4s | fine synchronous |
| `POST /scans/{id}/score` | 0.0s | fine synchronous |
| `POST /scans/{id}/fixes` | 20.3s | fine synchronous |
| `GET /scans/{id}/report` | 0.0s | fine synchronous |

**This is not a pipeline re-architecture.** One endpoint is long; the rest are
under 21 seconds and should stay exactly as they are. Any brief that proposes
making the whole pipeline async is proposing more than the measurements support.

### BackgroundTasks or Celery, for a first slice

**Recommendation: FastAPI `BackgroundTasks` for the first slice, behind the
existing `Orchestrator` seam, with Celery as a later migration if scaling ever
demands it.** Not a default reach for the lighter option — the reasoning:

*What Celery costs, given what step 4 found.* Celery is "already a dependency"
only of `apps/workers`, which the API cannot import. Using it means: a first
real task module; an enqueue path from the API that does not import worker code
(`send_task` by name, which adds a broker client to the API process); a worker
process to run, supervise and add to `docker-compose`; an `asyncio.run` bridge
inside each sync task (the project has no synchronous Postgres driver, because
psycopg2/3 are LGPL and `ip-safety.md` #6 puts LGPL on the stop-and-ask list —
`health.check_datastores` already demonstrates the pattern); and eager-mode test
wiring. That is a deployment-surface change, not a code change.

*What BackgroundTasks costs.* `background.add_task(...)`, and a fresh session
from `get_sessionmaker()` inside the task, because the request-scoped session is
closed by the time it runs. Nothing else. No new process, no new infra.

*What BackgroundTasks genuinely gives up.* It runs in the API process: it does
not survive a restart or deploy, and it does not scale past one instance. Both
are real — and neither is a constraint today. Epic 9 is pre-pilot, single
instance. **The honest consequence is the stale-scan reaper above: choosing the
cheap executor makes the reaper mandatory rather than merely prudent**, because
every deploy will strand any in-flight scan. That is the price, and it is worth
paying to avoid operating a worker fleet for a product with no users.

**"First slice" and "final answer" are not the same thing here, and should not
pretend to be.** The migration path is genuinely cheap precisely because
`orchestrator.py` exists and is unused: if the executor is injected as a
dependency, moving from BackgroundTasks to Celery later is one new
implementation of a protocol that is already written, not a rewrite. Epic 1.2
built that seam for this exact moment; it just never got used for the reason it
was built.

### Proposed slice order

**Slice 9.5 — make the scan pollable.** The minimum that turns "there is nothing
to poll" into "there is a status to poll", with no phase-level progress UI:

1. `get_or_create_scan` creates at `QUEUED`, not `RUNNING` (the model's own
   default), and stops setting `started_at` at creation.
2. `POST /clients/{clientId}/scans` commits the queued scan and returns **`202`
   with `ScanOut`** — id and status, no results.
3. Execution moves behind an injected executor. Production schedules a
   background task that opens its own session, sets `RUNNING` + commit, runs
   `run_scan`, commits the terminal status, and on exception commits `FAILED`
   with an error code rather than leaving the row at `RUNNING`.
4. `api-contracts.md` updated — it currently documents the synchronous
   behaviour and the `201` shape.

**Slice 9.6 — close the double-spend, and stop stranding rows.** The partial
unique index; resolving detection's placeholder-`RUNNING` conflation; converting
the resulting `IntegrityError` into "return the open scan" or a clean `409`; and
the stale-scan reaper that 9.5's executor choice makes mandatory.

Richer phase-level progress (Epic 9.1's nine-stage table as a progress
vocabulary) is a later slice again, and should not be attempted until 9.5 proves
a status is actually observable.

### Sizing, honestly

**Slice 9.5 is a single-session brief, but not a small one, and it needs an
internal commit split.** The production change is modest — the endpoint, the
executor, and the commit boundaries are perhaps a hundred lines. **The bulk of
the work is test churn**, and it should be sized as such up front:
`test_scan_endpoints.py` has 14 tests with ~15 POST calls and 18 assertions that
read `results` / `promptSet` straight out of the POST body, and
`test_score_endpoints.py` (2 call sites) and `test_fix_generator.py` (1) all
assume the scan is *complete* when the POST returns.

The mitigation is the injected executor itself: tests override it with an inline
runner, so "the scan is finished when the POST returns" stays true in the suite
while production returns immediately. That keeps the *setup* call sites working
unchanged; the ~18 assertions still have to move from the POST body to a
follow-up `GET /scans/{id}`. Mechanical, contained to one file, but real.

Verification scripts are unaffected — `verify_scan.py` and `verify_e2e.py` call
the services directly, not the endpoint.

**Slice 9.6 is its own session.** A migration, a semantic change to how detection
opens a scan, an error path, and a reaper is not a rider on 9.5.

So: **the async scan is at least two sessions, not one.** Presenting it as a
single brief would understate it.

### IP-safety self-check

No code changed and no screen changed, so constraint 9 does not strictly bite;
recorded because the pass read live data. `avp_dev` was read only — scan ids,
statuses, timestamps and domains, to check for stranded `RUNNING` rows. No
engine text, no competitor prose, no third-party content was read, printed or
committed. No provider calls were made, no SerpApi quota was spent, and no
dependency was added. **IP-safety check passed:** statuses, timestamps and
domains only.

### Tests

**865, unchanged** (api 577, workers 13, shared-types 53, design-system 99,
web 123). Run live at the start of the pass, not assumed. Nothing implemented.

---

## 2026-08-25 — Epic 9.5 · The scan becomes a job, and the row becomes visible

Epic 9.4 scoped this; this builds it. `POST /clients/{clientId}/scans` now
returns `202` with a QUEUED scan and executes out of band, and a stale-scan
reaper ships with it rather than after it.

### The fix was the commit boundary, not the background task

Worth stating plainly because it shapes everything else: what made a running
scan invisible was never "it runs in the request". It was that the row lived
inside an uncommitted transaction for ~303s. Any executor works once the row
commits early; no executor helps if it does not.

So the three real changes are commits:

* `get_or_create_scan` creates at **QUEUED** — the model's own default, which
  it had been overriding to `RUNNING` with a `started_at` that claimed work had
  begun before anything had picked it up. **RUNNING now means claimed, QUEUED
  means open**, and the reaper below depends on that distinction holding.
* `run_scan` commits the **RUNNING transition before any slow work starts**, so
  the state is observable for the whole run rather than at the end of it.
* `run_scan` commits its **terminal status** where it already computed it. A
  scan that has finished has finished, whatever the caller does next.

### `202` with `ScanOut`, not `201` with an empty `ScanDetailOut`

Both halves were decisions, not defaults. At `202` there is no prompt set and
there are no results — the prompt set is generated *by* the scan. A
`ScanDetailOut` carrying `promptSet: null, results: []` would describe a shape
this endpoint never has, and leave a caller unable to distinguish "not
generated yet" from "generated, and empty".

The response **always reports `queued`**, even under an executor that happens to
run synchronously. It describes what was accepted, not how far it has since
got; completion is answered in one place, `GET /scans/{scanId}`.

### A narrow `ScanExecutor`, not `avp_workers.Orchestrator`

Epic 1.2 built `Orchestrator` as the seam for exactly this moment, and Epic 9.4
noted it was still unused. It is deliberately **not** reused:

1. **`apps/api` cannot import it.** No dependency on `avp-workers` exists; the
   path dependency runs the other way, and `orchestrator.py` states the API
   "must not import worker code". Reusing it means inverting a direction chosen
   on purpose.
2. **Its shape is job-id-centric and this mechanism has no job ids.**
   `enqueue` returns an opaque handle that `result`/`status` key on.
3. **We do not want one.** The `Scan` row *is* the job record: `scan.status` is
   the status, `GET /scans/{scanId}` is the result. A parallel job id would be a
   second source of truth for "is this scan running", and that is how two
   sources of truth drift.

`ScanExecutor` is one method — `submit(job)`. Production supplies
`BackgroundScanExecutor`; the suite supplies `InlineScanExecutor`; a future
Celery slice supplies one that calls `send_task`.

**This does not weaken Epic 9.4's "Celery migration is cheap" claim — it
strengthens it,** and the correction is worth recording for whoever picks that
slice up. 9.4 assumed the migration would implement `Orchestrator`. It will
instead implement `ScanExecutor`: **one method rather than three, and no job-id
plumbing to invent**, because nothing downstream asks for a job id. What
changes is *which* seam to wire — `Orchestrator` remains unused and, for
running scans, is now superseded. A future reader should not go looking for it.

### The reaper, and why it was in this brief rather than the next one

`BackgroundTasks` does not survive a restart. Without a correction mechanism,
every deploy mid-scan strands a row at `RUNNING` **forever** — the dashboard
renders "Running…", Epic 9.3's screen disables re-run for that client on the
strength of it, and the next queue attempt reuses the row and dies on
`prompt_sets`' unique constraint as a 500. Shipping the executor alone would
have traded a visible failure mode (a request that hangs) for a silent one (an
orphaned row nobody notices), which is worse than what it replaced.

| Decision | Choice | Why |
|---|---|---|
| Threshold | **900s** | Derived, not rounded to taste: ~3x this endpoint's measured 303s share (Epic 9.2: prompt generation 14.6s + scan loop 288.5s), and clear of the 600s `task_soft_time_limit` the repo already treats as "stuck, not slow". |
| What it reaps | **RUNNING only** | QUEUED means *open, unclaimed* — which is what a detect-only run leaves for a later scan to reuse. Reaping it would break detect-now-scan-tomorrow. A QUEUED row stranded by a crash is harmless anyway: `get_or_create_scan` reuses it. |
| Error code | **`EXECUTOR_LOST`** | Distinct from `ALL_ENGINE_CALLS_FAILED` (the pipeline ran, every engine failed) and `EXECUTION_FAILED` (the pipeline raised). "The process running this went away" is a different fact. |
| Where it runs | **Check-on-read**, on the dashboard *and* the queue path | No scheduler, for the same reason Epic 9.4 chose BackgroundTasks over Celery: a periodic task is a process to run and supervise, and this is pre-pilot and single-instance. The dashboard is where a stranded row does its **visible** damage; the queue path is where it does its **worst** damage. A startup-only check would miss a scan stranded by one worker dying while its peers keep serving; this does not. One UPDATE against the existing `ix_scans_status`, and a no-op when nothing is stale. |

A GET that writes is deliberate, and noted where it happens.

### The exception path

`run_scan` raising used to surface as a 500 that somebody saw. In the
background there is nobody to tell, so the row carries the news: `FAILED` with
`EXECUTION_FAILED`, written on a **fresh session**, because Postgres aborts the
transaction that raised and every further statement on it fails until rollback —
writing the failure through it is exactly as likely to fail as the thing that
just did. `session_scope` makes the same assumption for requests; a background
task has no such wrapper, so it is explicit.

Only the exception **type** is persisted. An exception message can carry a
provider response body (ip-safety.md #7), and a test asserts a marker planted in
one never reaches the row or any response.

### The test migration — smaller than 9.4 sized it, and one trap in it

9.4 sized this as the bulk of the work and expected ~18 assertions to move.
The actual figure is smaller, because a single helper absorbed most of it. The
sixteen call sites were accounted for individually rather than bulk-assumed:

| Where | Sites | Disposition |
|---|---|---|
| `test_scan_endpoints.py` | 7 | move to a `_run_scan` helper that POSTs, asserts `202`, and reads the completed scan from `GET /scans/{scanId}` |
| `test_scan_endpoints.py` | 5 | read only the scan id — untouched |
| `test_scan_endpoints.py` | 1 | needed care (below) |
| `test_score_endpoints.py` | 2 | untouched — not testing scan creation |
| `test_fix_generator.py` | 1 | untouched — same |

The seam is what keeps it small: `conftest` overrides the executor dependency
with `InlineScanExecutor`, so "the scan is finished once the POST returns" stays
true in the suite without the endpoint pretending to be synchronous in
production. It deliberately does *not* make the POST body report completion.

**The one that needed care.**
`test_answer_text_never_appears_in_the_response` searched the POST response for
engine prose. That response now carries no results, so **it would have kept
passing no matter what the pipeline did with the text** — a green test guarding
nothing, which is worse than a missing one. It now searches
`GET /scans/{scanId}` and the results page, and asserts both are non-empty
first, so it cannot go quietly vacuous again. It was found by reading the
migration site by site; nothing in the suite would have reported it.

### What is now true that was not

* A scan is **readable as QUEUED by a separate request while the work has
  demonstrably not run** — asserted directly with a deferred executor, not
  inferred from other tests passing.
* That queued scan **reaches `GET /api/v1/dashboard`**, which is the consumer
  Epic 9.3 built.
* `get_or_create_scan`'s reuse **actually functions** for sequential requests.
  It always intended to and never could, because the row it was looking for was
  invisible.
* Re-run **returns immediately** instead of holding a connection open for
  minutes.

### What this does NOT close

**The double-spend race is still open.** Early commit narrows the window between
`SELECT` and `COMMIT` from ~303 seconds to milliseconds; it cannot close it. Two
genuinely concurrent requests still create two scans. Epic 9.6's partial unique
index is the fix, and this slice does not claim otherwise — a test asserts the
sequential case and says so in its own docstring.

**Epic 9.1's slice-2 requirement is partly met, and should not be read as done.**
"Must show progress, not wait" had two halves. The *wait* is gone: the request
returns in milliseconds instead of ~303s, and there is now a real status —
`queued → running → succeeded/partial/failed` — that a client can poll. The
*progress* half is not built: **no poller exists in `apps/web`**, and no
phase-level progress exists at all (Epic 9.1's nine-stage table is not exposed
by any endpoint). What changed is that polling is now *possible*; a dashboard
that refreshes still needs a manual refresh. Progress UI is a follow-up slice,
not a finished one.

**The detection placeholder is improved, not fixed.** A detect-only run now
leaves a QUEUED row rather than a RUNNING one, which is more honest — nothing is
running — and the dashboard labels it "Queued" instead of "Running…" forever.
But re-run is still disabled for that client, because the screen treats both as
in-flight, and the reaper deliberately does not touch QUEUED. **Unchanged in
effect, more honestly labelled.** 9.4 scoped the real fix into 9.6.

Epic 9.3's code comments explaining that polling was impossible were corrected
rather than left to mislead the next reader; no UI behaviour changed.

### IP-safety self-check

Constraint 9 applies: this changes API behaviour a customer-facing screen
consumes, even though the screen itself was not rebuilt.

* **#7** — the new failure path persists only an exception **type**, never the
  message, which can carry a vendor response body. A test plants a marker in an
  exception message and asserts it reaches neither the row nor any response.
  The migrated ip-safety test was strengthened rather than allowed to go vacuous
  (above). `EngineAnswer.text` is untouched and still transient.
* **#2** — no UI component or style changed; the two edits under `apps/web` are
  a type correction (`ScanDetail` → `Scan`) and comments. No new primitive, no
  Tailwind.
* **#6** — no dependency added. `BackgroundTasks` is Starlette, already present.

**IP-safety check passed:** statuses, error codes, exception type names and
timestamps only; no third-party prose persisted or rendered; no dependency
added.

### Tests

**881, up from 865** (api **577 → 593**, workers 13, shared-types 53,
design-system 99, web 123). Run live at the start of the pass at 865 and again
after. `ruff check` clean across `src/` and `tests/` (the project's configured
scope; `scripts/` carries 8 pre-existing errors, unchanged and verified against
HEAD). `mypy` unchanged at its pre-existing 38. `openapi.json` and
`api.gen.ts` regenerated — the response model change is a contract change, and
FastAPI is the source of truth for it.

---

## 2026-08-25 — Epic 9.6 · One open scan per client, and a placeholder that stops blocking

Epic 9.5 left two things open and said so. Both are closed here: the double-spend
race, and detection's placeholder disabling re-run indefinitely.

### The reaper's read-path cost — measured, and left alone

Epic 9.5 put `reap_stale_scans` on every `GET /api/v1/dashboard`. Checked before
touching anything else, since a write on a read path deserves a number rather
than a shrug:

```
Update on scans  (cost=0.14..8.17 rows=0) (actual time=0.017..0.017 rows=0.00)
  ->  Index Scan using ix_scans_status on scans
        Index Cond: ((status)::text = 'running'::text)
        Filter: ((started_at IS NOT NULL) AND (started_at < now() - '00:15:00'))
  Buffers: shared hit=3 dirtied=1
Execution Time: 0.664 ms
```

**0.664 ms**, three buffers, an index scan that matches nothing in the common
case. Epic 9's acceptance names 3–5 pilot agencies, and no poller exists yet, so
real dashboard traffic is a handful of manual loads per day. Even inventing an
aggressive future poller — 5 agencies at one load every 5 seconds — that is 1
request/second, or **0.066% of one core**. **No change made.**

Worth recording the shape of the cost rather than only the number: the index
scan matches on `status='running'` and *then* filters by `started_at`, so it
grows with the number of **concurrently running** scans, not with total scans.
At pilot scale that is a handful. If concurrent scans ever reached the
thousands, a composite `(status, started_at)` index is the fix — noted as a
trigger condition, not built, because building it now would be optimising a
measurement that says there is nothing to optimise.

### Part A — the double-spend race, closed by the database

9.5 narrowed the window from ~303s to milliseconds by committing early, and was
explicit that **narrowing a race is not closing one**. Two concurrent requests
could still both look, both find nothing, and both insert.

```sql
CREATE UNIQUE INDEX uq_scans_one_open_per_client
    ON scans (client_id) WHERE status IN ('queued', 'running');
```

**Partial**, because the invariant is about *open* scans only. A client
accumulates any number of finished ones, and a constraint covering terminal
scans would let each client be scanned exactly once, ever. `status` is
VARCHAR-backed with a CHECK constraint (`native_enum=False`), so the predicate
is a plain string comparison needing no enum cast. Declared on the **model** as
well as in the migration — `test_migrations.py` runs `alembic check`, and a
migration-only index is drift.

**The migration cannot fail against real data.** It resolves violations before
creating the index. `avp_dev` was checked at authoring time and had none — zero
clients with more than one open scan — but it runs anyway, because a migration
has to be safe against every database it will meet, not the one in front of its
author, and every request that raced before this index existed could have made a
pair. The newest open scan per client survives (`id` is a ULID, so that is
creation order); older ones are FAILED with **`EXECUTOR_SUPERSEDED`** — distinct
from `EXECUTOR_LOST` (9.5's reaper: the process went away) and
`ALL_ENGINE_CALLS_FAILED` (the pipeline ran and failed). Failed rather than
deleted: a scan that consumed paid model calls is a record, and its
`engine_results` still reference it. The downgrade drops only the index and says
plainly why it does not restore those rows — re-opening them would recreate the
duplicates the index forbids, so downgrade-then-upgrade would fail.

**The loser is handled, not crashed into.** `get_or_create_scan` runs its INSERT
inside a **SAVEPOINT**, so a lost race rolls back only that statement — a plain
rollback would discard whatever else the caller had pending in the surrounding
transaction. It then re-reads and adopts the winner's row. This is sound because
of how Postgres sequences it: a losing INSERT **blocks** until the winner
commits and only then raises, so by the time the exception arrives the winner is
guaranteed findable. An `IntegrityError` with no open scan to adopt is not the
race, and still raises.

Tested at all three layers, because each can be right while another is wrong:
the **invariant** (all four open/open combinations rejected; terminal scans
unconstrained); the **recovery branch**, driven deterministically by forcing the
first lookup to miss — a real race needs a microsecond window a test cannot
reliably hit, so pretending otherwise would be a flaky test dressed as a
thorough one; and the **end-to-end path**, two and then five genuinely
concurrent HTTP requests resolving to one scan.

**Before → after, for concurrent scan requests.** Before: two requests, two
scans, two full runs of paid model calls, both returning `202` and each naming
a different scan. After: two requests, **one** scan; both return `202` and both
name the same scan; the second costs nothing. Neither request errors — the
loser is answered with the winner's scan in its current state, which is the
response contract for this case.

### Part B — the placeholder that looked correct and was not

`POST /clients/{clientId}/competitors/detect` opens a scan for the
CompetitorSet to hang off. If no scan is run afterwards, that row stays open
indefinitely — by design, since it exists for a later scan to reuse. The
dashboard treated QUEUED as work in progress and **disabled re-run for that
client permanently**, for a scan nobody had started.

**Epic 9.5 made this worse by making it more honest**, which is worth stating
because it is the kind of regression that does not look like one. Before 9.5 the
row read "Running…" forever — obviously wrong, and it invites investigation.
After, it reads "Queued" — which looks correct and does not. No test guarded the
behaviour in either direction.

**Fixed in UI logic, not schema**, and the measurement is what decided it. The
question was whether QUEUED-from-detection needs to be distinguishable from
QUEUED-from-a-real-scan. Timed against `avp_test`, 20 samples of exactly what
the executor does before `run_scan` commits RUNNING:

| | |
|---|---|
| QUEUED → RUNNING, median | **1.1 ms** |
| worst of 20 | 4.0 ms |
| RUNNING phase, for comparison (Epic 9.2) | **~303 s** |
| ratio | about **1 : 275,000** |

**QUEUED is not a state a real scan meaningfully occupies.** The only QUEUED
scan that lasts is the placeholder — precisely the one that must not block. So
`busyClients` keys off `RUNNING` alone. A nullable `queued_by` column would add
schema to encode a distinction the UI stops needing, and this project's
discipline (Epic 3.6, Epic 7's white-label deferral) has consistently preferred
deferring a schema change until its shape is genuinely understood.

Offering re-run on a placeholder is not merely harmless — **it is the point**:
the row exists so a later scan reuses it, and re-run is the action that runs it.
Part A's tests prove that reuse end to end.

**And it is safe, because the double-spend defence is no longer that list.** The
partial unique index makes a second open scan impossible, and a losing request
adopts the winner, so a click inside the 1.1 ms window returns the same scan
rather than buying another. The flag now decides only whether offering the
action would confuse, not whether it costs. Part A had to land first for Part B
to be defensible. A running scan still blocks re-run, still per **client**
rather than per row — both asserted, so the guard cannot quietly go away.

**Before → after, in the terms a pilot agency would hear it.** Before: "You ran
competitor detection on a client but did not run a scan. The dashboard shows a
scan sitting at Queued, and the Re-run button for that client is greyed out —
permanently. Nothing is running; there is no way to start one from this screen."
After: "Detection leaves a scan ready to run. It shows as Queued, Re-run is
available, and pressing it runs that scan rather than starting a second one."

### IP-safety self-check

Constraint 9 applies — a customer-facing screen changed.

* **#1** — the change is one predicate in the dashboard's busy logic, decided
  from a measured state transition and the data model. No competitor product
  was consulted.
* **#2** — no component, style or token changed; no new primitive. The only
  `apps/web` edits are a constant, a comment, a fixture and tests.
* **#7/#8** — the migration and the new failure code persist a status, an error
  code and our own sentence (`Superseded by a newer open scan for the same
  client.`). No engine text, no competitor prose, no vendor response body. Test
  fixtures use invented names on `.example` domains.
* **#6** — no dependency added.

**IP-safety check passed:** statuses, error codes, an index predicate and our
own microcopy only; no third-party content persisted or rendered; no dependency
added; no new design-system primitive.

### Still open

Epic 9's budget is unchanged at **361.3s against 300s** — nothing in this slice
touched timing. `PROMPT_CONCURRENCY` (Epic 9.1's candidate fix 2) and fixes 3
and 4 remain unbuilt. There is still **no poller** in `apps/web` and no
phase-level progress; 9.5 made a status pollable and 9.6 made it trustworthy,
but neither built the poller. Slice 3 (the send path) is untouched.

### Tests

**896, up from 881** (api **593 → 605**, workers 13, shared-types 53,
design-system 99, web **123 → 126**). Run live at the start of the pass at 881
and again after. `ruff check` clean across `src/` and `tests/`; `mypy` unchanged
at its pre-existing 38. `alembic check` clean, and the migration runs clean from
zero and downgrades to base (`test_migrations.py`). `openapi.json` regenerated
and unchanged, as expected — an index is not part of the API contract.

---

## 2026-08-25 — Epic 9.7 · The dashboard updates itself

Epic 9.1 stated slice 2's requirement as "must show progress, not wait". 9.5
made a scan's status genuinely readable while it runs; 9.6 made it safe to act
on; both left the poller unbuilt. Pressing Re-run showed the row turn Queued and
then required a manual refresh to learn anything else. This is that last piece.

Pure frontend. `apps/api` is unchanged and its suite is unchanged at 605.

### Which endpoint to poll — the measurement inverted the assumption

The obvious guess is that `GET /scans/{scanId}` is the cheap, targeted poll and
the full dashboard is the expensive one. **It is the other way round**, and by a
wide margin:

| | |
|---|---|
| `GET /api/v1/dashboard`, total DB time | **~1.74 ms** — reap 0.664 + clients 0.034 + scans 0.019 + recent-scans 0.248 + seats 0.777 |
| `GET /scans/{scanId}`, a finished 24-prompt scan | **464 rows** across four tables — 48 engine results, 149 brand mentions, 243 citations, 24 prompts |

`_detail` eagerly loads every engine result with its mentions and citations, so
the single-scan endpoint is roughly the heaviest read in the product — and it is
heaviest exactly at completion, which is when polling would last touch it. It
also would not update what is on screen: the dashboard is the rendered view, so
polling the scan would mean a second request to refresh the row anyway.

**So: poll `GET /api/v1/dashboard`, and nothing else.** One request, one render,
and the lighter of the two by ~50×. The hybrid the brief floated was worth
checking and turned out to be the wrong way round.

### The interval — 5 seconds, costed

A scan's observable life is ~303s (Epic 9.2: prompt generation 14.6s + scan loop
288.5s), and the transition worth catching happens once, at the end.

| | |
|---|---|
| **5s** | ~61 polls per scan; completion at most 5s stale against a 300s+ wait — 1.6%, imperceptible |
| 1s | ~303 polls to observe one transition; five times the traffic for latency nobody can feel |
| 30s | a user stares at "Running" for up to half a minute after it finished, which reads as broken |

At ~1.74 ms per load, a whole scan's polling costs **~106 ms** of database time.
Server cost is simply not the constraint here.

**No backoff.** It would optimise something already measured as free, and add a
second timing behaviour to reason about and test for no gain. There is also no
long tail to protect against: Epic 9.5's reaper caps a stuck scan at 900s.

### When to poll — neither "any unfinished scan" nor "running" alone

Both obvious rules are wrong, in opposite directions.

**"Any non-terminal scan" polls forever.** A detect-only run leaves a QUEUED
scan that nothing will move until somebody runs one. That rule would make a
dashboard left open fire a request every five seconds, indefinitely, about a
scan that is never going to start — the same trap Epic 9.6 removed from the
re-run button, and worse here, because it is network traffic rather than a
greyed-out button.

**"RUNNING only" misses the scan the user just started.** `POST /clients/{id}/scans`
returns `202` while the row is still QUEUED, and the executor promotes it ~1.1ms
later (Epic 9.6's measurement) — *after* the refresh that follows the click has
already read it. The poller would look once, see QUEUED, decline, and never
look again. The one case the feature exists for.

So the condition is **RUNNING, or a scan this session started that has not
finished**. The watch set exists solely to cover that ~1.1ms window plus any
queue backlog, and dissolves the moment the scan reaches a terminal status.

**There is one polling mechanism, not two.** Pressing Re-run does not start a
separate poller; it supplies the second of the two conditions the single poller
starts on. Worth stating plainly, because "I just clicked re-run" and "the
general poll" otherwise read as competing mechanisms.

A backgrounded tab keeps its timer but does no work, and refocusing polls
immediately rather than making the user wait out an interval on a stale view.

### When polling fails

A poll that fails must not leave the page looking live while it is frozen.

* a blip **retries silently** — one or two failures heal themselves and the user
  does not need to read about it
* **three consecutive failures** surface a visible notice, *while still
  retrying*. The notice reports reality; it does not give up on it
* a **401 stops** polling and switches to the sign-in view. Retrying an expired
  session every five seconds forever is noise, not resilience

Silence is never indefinite, which was the one outcome to avoid.

### Framework-free rules, and the first fake timers in this repo

The timing rules are the part that can be wrong — starting when it should not,
never stopping, leaking an interval, going quiet after a blip. None of that is
visible in a rendered string, and this project's frontend tests use
`renderToStaticMarkup`, which does not run effects.

Rather than add a React test renderer, the rules were extracted into
`lib/dashboard/polling.ts` and tested directly. That is not a novel move here —
`derive.ts`, `ledgerLayout.ts` and `answerShelfLayout.ts` all pull decidable
logic out of components for exactly this reason. `useDashboardPolling` is a thin
wrapper. **No new dependency; `package.json` and the lockfile are untouched**, so
ip-safety.md #6's licensing gate did not need to be opened.

**This is the repo's first use of `vi.useFakeTimers`**, recorded here and in the
test file's header rather than introduced silently.

The two rules that matter were **mutation-checked**, because a test that cannot
fail proves nothing: making the condition "any non-terminal scan" fails the
placeholder test, and deleting the stop branch fails the two tests that watch a
scan finish. Both confirmed to fail, then reverted.

### The screen says what it is doing

While polling: *"A scan is running — this page updates itself."* When polling has
been failing: a notice saying so. A page that silently rearranges itself is
unsettling, and a stale page that looks live is a lie — the same instinct as the
intake screen's refusal to draw a progress bar it cannot honestly fill.

### Stale comments corrected

Per the convention 9.5 and 9.6 established, prior epics' "this does not exist
yet" language was not left standing:

* `DashboardView`'s **"WHY THIS STILL DOES NOT POLL"** header — three epics of
  explanation for a state of affairs this slice ends — is now a short history
  and a description of what exists.
* The route's re-run docstring said the concurrent-request race was still open
  and that "Closing that is Epic 9.6's partial unique index." **9.6 shipped it.**
  That sentence was already stale before this brief and was corrected here.

### Does this satisfy Epic 9.1's requirement?

**"Must show progress, not wait" — the *wait* is fully gone, and *progress* is
met at the level the backend can support.** Precisely:

* A user presses Re-run and watches the row move Queued → Running → Complete
  without touching the page. That is the requirement's plain reading, and it now
  holds.
* What is **not** built is phase-level progress — Epic 9.1's nine named stages
  with real relative weights, one of which is 84% of the runtime and would need
  its own sub-progress. **No endpoint exposes that**, and exposing it is a
  separate and larger piece of work; it is explicitly out of scope here.
* So a user sees *that* a scan is progressing, not *how far along* it is. For a
  ~5-minute job that is a real limitation and worth naming rather than
  papering over — but it is a gap in resolution, not the "request-and-wait
  fails on the numbers" problem Epic 9.1 actually raised. That problem is
  closed.

Epic 9's **budget is untouched at 361.3s against 300s**. This brief changed how
the wait is presented, not how long it is. `PROMPT_CONCURRENCY` and Epic 9.1's
other candidate fixes remain unbuilt.

### IP-safety self-check

Constraint 9 applies — this is a behaviour change on a customer-facing screen.

* **#1** — designed from the data model and the user goal: what the status field
  can express and what someone waiting on a scan needs to know. No competitor
  product consulted.
* **#2** — every element still imports from `@avp/design-system`; the two added
  elements reuse `Card`/`CardBody` and token utilities only. **No new primitive
  and no ad hoc Tailwind.**
* **#4/#6** — no icons, fonts or dependencies added.
* **#7/#8** — the poller moves statuses, counts and timestamps. No engine text
  or competitor prose enters the screen, and all new microcopy is newly written.

**IP-safety check passed:** statuses, counts and timestamps only; design-system
components and token utilities, no new primitive, no ad hoc Tailwind; no
third-party prose; no dependency added.

### Tests

**922, up from 896** (api 605 **unchanged** — this brief touched no backend —
workers 13, shared-types 53, design-system 99, web **126 → 152**). Run live at
the start of the pass at 896 and again after. `next build` emits `/dashboard` as
before, `tsc --noEmit` clean, and the API gates are unmoved (`ruff` clean,
`mypy` at its pre-existing 38).

---

## 2026-08-27 — Epic 9.8 · The send path: a link, a stranger, and a class that never existed

Slice 3 of Epic 9's order. §7's acceptance criterion is that a pilot agency can
"generate and **send** at least one real prospect report," and until this entry
the word *send* had nothing behind it: every report surface resolved a session
cookie and scoped every query to one agency, so the person a report is *about*
had no way to read it. north-star.md §5.4 and §6 both name the shareable link as
the correct first build over PDF export, on cost, and §6 goes further — a scan
without a way to help close the deal it supports is "evidence without a
mechanism."

**This is the first unauthenticated read surface in the product.** That framing
did most of the design work: the interesting questions are all about what must
not be reachable, not about the happy path, which is one request.

### Storage: nullable, minted on demand, and never the ULID

`share_token` on `scans` — nullable, unique on a partial index, minted the first
time an operator asks rather than at scan creation.

**Why not mint for every scan.** A token that exists is a URL that works. Minting
one per scan would publish every scan ever run and then rely on nobody learning
the address. Absent-by-default is the safer state and it makes "is this shared?"
a column read rather than an inference.

**Why not the scan's own ULID.** It is already in the authenticated URL, already
in logs, and already handed to the browser. Reusing it would mean anyone who had
ever seen a scan id could read that report forever. `security.new_share_token`
sits directly beside `new_session_token` and is the same call —
`secrets.token_urlsafe(32)`, **256 bits from the OS CSPRNG**. north-star.md §7
claims this product's data discipline is a sellable asset; a share token drawn
from anything weaker than the session token it stands in for would make that
claim false in the one place a stranger can actually reach.

One genuine asymmetry, recorded where it lives rather than glossed: session
tokens are stored as a SHA-256 digest, this one is stored in the clear. A digest
would make the link unrecoverable after minting, and an operator has to be able
to come back and copy the URL again. The cost is that a database dump exposes
live share links — acceptable only because the same dump exposes every report
those links lead to.

### The race the unique index cannot arbitrate

`get_or_create_share_token` takes `SELECT … FOR UPDATE`, and the lock is
load-bearing rather than defensive. This is **not** Epic 9.6's race, and the
same fix does not apply.

Epic 9.6's double-spend was two INSERTs competing for one constraint, so the
database could pick a winner and the loser could adopt its row. Here two
concurrent calls generate two *different* tokens and issue two UPDATEs against
the *same row*. There is no unique violation to catch — the second write simply
wins, and the first caller walks away holding a URL that 404s. Row locking makes
the loser read the winner's token instead.

`POST /scans/{scanId}/share` is therefore **`200` and idempotent, not `201`**.
There is no revocation, so a second click minting a second token would leave a
live URL nobody is tracking. The second call creates nothing, so it does not say
it did.

### Rejection is one code path, on purpose

`GET /reports/{token}` answers `404` for a malformed token, an unknown token and
a well-formed miss alike — identical status, identical body.

There is deliberately **no shape or length pre-check**. Validating the charset
first would answer "was that even a plausible token?" faster than it answers
"does it exist?", and a timing difference between those two is what makes
enumeration cheap. Every guess pays for the same index lookup.

**Never `401`.** A `401` would mean "this token is real, now authenticate",
which is precisely the bit an enumerator is trying to buy.

`build_report` is reused rather than reimplemented, so the facts-only sweep in
`test_ip_safety.py` covers the public response too. A test asserts the public and
authenticated payloads are byte-identical apart from `generatedAt`, which is a
clock read — a second assembly path would be a second place for a snippet to slip
in, which is the whole reason Epic 7 put the projection in one module.

`url` is built from `PUBLIC_WEB_BASE_URL`, never from the request's `Host` or
`Origin`. Those are attacker-controlled, and a share link built from a spoofed
Host is a phishing URL carrying a real token.

### The index coexistence was verified, not assumed

The brief said to confirm the new unique index does not interact with Epic 9.6's
`uq_scans_one_open_per_client`. Read off the live schema after `upgrade()`:

```
"uq_scans_one_open_per_client" UNIQUE, btree (client_id) WHERE status IN ('queued','running')
"uq_scans_share_token"         UNIQUE, btree (share_token) WHERE share_token IS NOT NULL
```

Different column, different predicate. A client still gets exactly one open scan
while any number of its finished scans are shared.

### 18 tests, weighted at the negative surface

The happy path is one assertion. The rest are what must not be true: every
bad-token shape returns 404 with an identical body (seven shapes, including the
scan ULID, a traversal string and a 4000-character token); an unshared scan has
no token at all; another agency gets 404 rather than 403; and the public payload
is swept **recursively at every depth** for account, operator and credential keys
— with a floor on keys walked, because a typo'd empty body would otherwise pass
every assertion above it. The token also must not appear in the report it
unlocks, or a forwarded screenshot carries its own credential.

That sweep is the `test_ip_safety.py` pattern applied to a payload rather than a
schema module: assert over everything present, not over a list someone
remembered to update.

### A design-token bug that no test could catch

Found by reading the preset, not by any gate.

The first draft of `ShareLinkBar` rolled its own bordered `<input>` and reached
for **`border-border-subtle`**. That class does not exist. The preset's colour
scale exposes `line.hairline` / `line.strong`, so there is no `border-*`
namespace at all — the border silently rendered as nothing.

**Every gate was green through all of it.** `tsc --noEmit` passed before and
after, because Tailwind classes are opaque strings to the type checker. The JS
suite was 152/99/53 before and is 152/99/53 now, because **nothing in this repo
asserts that a class name resolves to a real token.**

This has happened here before, and the evidence is still in the file.
`tailwind-preset.ts` carries a comment from Epic 7: the leading tokens "existed
in tokens.css from Epic 0 but were never exposed as utilities, so
`leading-prose` silently compiled to nothing." Same failure mode, five epics
apart, caught the same way both times — a human reading the preset.

**The fix was not a better class.** `TextField`'s own docstring already said what
the right answer was: it lives in the design system "because ip-safety.md #2
prohibits ad hoc styling on customer-facing screens — a form input styled locally
would be exactly that." The hand-rolled input *was* the violation; swapping one
utility for another would have left it in place. Using `TextField` also replaced
a faked `aria-label` with a real bound `<label>` and brought the focus ring with
it. Confirmed in a real browser afterwards: the control computes to
`1px solid oklch(0.87 0.012 75)`, where the original produced no border at all.

The remaining 25 classes across both new files were then audited individually
against the preset's actual scales. All resolve.

**Naming the gap rather than closing it:** a preset-conformance lint — every
`className` token checked against `tailwind-preset.ts` — is the thing that would
catch this class of defect, and it is its own brief. Recording it here so the
third occurrence is not also found by eye. north-star.md §8.1 would call this a
Layer 0/1 concern, not Layer 5, which is why it does not belong in this one.

### The public page reuses the report, and drops one button

`/share/{token}` renders the same `ReportView` the authenticated screen renders.
Epic 7 had already made this nearly free: `competitorEditor` is a slot, and its
docstring says "rendered for a client, no slot is passed and no editing
affordance exists."

What that reasoning missed is the pitch beat's **"Build the proposal"** CTA. It
is operator chrome — a prospect is not building the proposal — and it has no
handler, so to a stranger it reads as a broken button rather than a disabled one.
One boolean prop (`publicView`) drops it; a boolean rather than another slot
because the public view wants it *gone*, not replaced.

`ShareLinkBar` sits **above** `ReportView`, never inside it, and is deliberately
not a slot: `ReportView` is the document that gets sent, so a control for sending
it must not appear in what is sent.

### Spend, stated before it was spent

Approved in-session before any call.

| | |
|---|---|
| SerpApi, re-verified live immediately before the run | **167 used, 83 left** of 250 |
| Approved and spent | **6 searches** |
| SerpApi after the run | **173 used, 77 left** — the 6 predicted, no more |
| Anthropic, predicted | 55–103 `claude-opus-5` |
| Anthropic, actual | **64** — 48 engine + 9 sentiment + 1 classify + 4 co-citation + 1 prompt-generation + 1 fix-generation |

64 is near the **bottom** of the predicted band, and the reason is the finding
itself: sentiment is only spent where an answer mentions the brand, and only 9 of
48 answers did. Epic 9.1 spent 96 and Epic 9.2 spent 103 on the same shape of
run, both because nearly every answer named the subject. **The cheap scan and the
bad score are the same fact.**

### The real run

`psmdigitalagency.com`, 24 prompts, one run. `scan_01M10RH5BHT8EQJN7D2QWZ69T3`,
status **`succeeded`** — 48/48 results, zero errors.

```
  phase                      epic          secs      %    db  external calls
  scan loop                  4            273.0  74.7%    10  anthropic x33
  fix generation             8             25.1   6.9%    12  anthropic x1
  technical audit            6             15.0   4.1%     3  http x1
  prompt generation          4             13.2   3.6%     2  anthropic x1
  ...
  TOTAL                                   365.6 100.0%    65
```

**365.6s against the 300s budget — over by 65.6s**, consistent with Epic 9.2's
361.3s. This brief did not touch timing and does not claim to; `PROMPT_CONCURRENCY`
remains Epic 9.1's unbuilt candidate fix 2.

### What the scan says about PSM, unsoftened

**Composite 28.89 / 100.** Recorded plainly because an unflattering finding is
the pitch working, not a defect to fix before showing them.

| Dimension | Score |
|---|---|
| Mention Rate | **18.75** |
| Share of Voice | 25.71 |
| Citation Strength | **0.85** |
| Sentiment | 44.44 |
| Technical Foundation | **100.00** |

**39 of 48 answers named nobody at PSM at all.** Citation Strength at 0.85 means
essentially no source the engines trust cites them. Technical Foundation at 100
is the sharp half of the argument: **their site is not the problem.** Nothing is
broken, and they are still invisible — which is exactly the gap this product
exists to name, and a much better conversation than "your schema is malformed."

Rivals detected and named more often: WebFX, Ignite Visibility, Thrive Internet
Marketing Agency, Jumpingjackrabbit, Rightleftagency.

The report renders it as "PSM Digital Agency is close to invisible when buyers
ask", biggest gap "Mention Rate is costing the most — 24.4 points", and closes at
"29 today. 92 with the fixes above."

### Verified in a real browser, with no cookie

A fresh Chromium context — no cookies, no storage, the incognito case — loaded
the public URL: all five beats render, 40 Answer Shelf rows, 4 action items, 5
competitors; `Build the proposal` absent; no editing affordance present.
`ctx.cookies()` was `[]`.

### Deliberately not built

Named here so a fast, correct MVP is not mistaken for a finished feature:

* **No expiry.** A minted link works until the scan row is deleted.
* **No revocation.** There is no way to un-share a report.
* **No PDF export** — still `[ ]`, still the other half of the send path.
* **No branding customisation** — white-labelling remains name-and-slug only, so
  a sent report carries the agency's name but not its logo or colours.

The first two are the real debt. An agency that sends a link to the wrong address
cannot take it back, and that is not acceptable as a permanent design. Revocation
is nearly free — clear the column — but it needs a route and a UI, and neither is
in this slice. Recorded in `models/scan.py`, `services/share.py`,
api-contracts.md and the screen's own microcopy, which tells the operator
plainly that the link "does not expire and cannot be withdrawn yet."

### IP-safety self-check (constraint 9)

Constraint 9 applies — a new customer-facing screen, and the first one a
non-customer can reach.

* **#1** — designed from the data model and the user goal: what a stranger
  holding a link needs to see, and what an operator needs in order to send one.
  No competitor product was consulted or referenced.
* **#2** — every element imports from `@avp/design-system`. **This is the
  constraint the epic actually caught itself on**: the hand-rolled `<input>` was
  ad hoc styling on a customer-facing screen, and it was replaced with
  `TextField` rather than patched. All 25 remaining classes audited against the
  preset.
* **#3** — the public page is the narrative report unchanged; the send control
  lives outside the document.
* **#7/#8** — the public projection is the same `build_report` output the
  authenticated route returns, and `test_report_projection_exposes_no_third_party_prose`
  sweeps it. A new recursive test additionally asserts the public payload carries
  no account, operator or credential key at any depth. All new microcopy is newly
  written.

**IP-safety check passed:** design-system components only, no hand-styled input,
no ad hoc Tailwind, no third-party prose in the public payload, no dependency
added.

### Tests

**940, up from 922** (api 605 → **623**, workers 13, shared-types 53,
design-system 99, web 152). Run live at the start of the pass at 922 and again
after, with `set -o pipefail` so the reported exit code is pytest's and not the
pipe's — north-star.md §4.3's own documented trap. `ruff` clean across `src/` and
`tests/`; `mypy` unchanged at its pre-existing 38 errors across 65 source files.
`tsc --noEmit` clean. `openapi.json` and `api.gen.ts` regenerated;
api-contracts.md moved `GET /reports/{token}` out of "Planned, not yet built"
into the shipped contract and corrected the surrounding prose, which still said
no endpoint allowed Epic 9's "send".

---

## 2026-08-27 — Epic 9.9 · The dashboard joins the product

A polish pass, not a redesign. The dashboard was a *correct* table — Epic 9.3
built it carefully and Epic 9.7 taught it to update itself — sitting next to a
report that uses the full design system. Correct data in a plain grid, one click
from a narrative document with a signature visualisation, made a finished
product look unfinished.

**This is explicitly not a step toward a metrics dashboard.** ip-safety.md #3
mandates narrative reports over generic metrics-tile dashboards and that ruling
stands; no tile, trend arrow or percentage delta was added. No competitor
dashboard was consulted.

### `ScoreMeter`, and why it is not a miniature Ledger

The Luminance Ledger's correctness condition is that total lit height IS the
composite — *"the chart is the number rather than a picture of it."* `ScoreMeter`
is that identity at one dimension: a track whose lit **length** is the score,
filled from the visibility ramp, numeral in the editorial face.

The obvious idea was a small Ledger in the row. **It cannot be built honestly.**
A Ledger divides its column by per-dimension weight, and `ScanSummaryOut` carries
`compositeScore` and nothing else — the dashboard endpoint serves no sub-scores.
Drawing segments there would mean inventing the divisions, which destroys the one
property that makes the Ledger trustworthy. One dimension is what the data
supports, so one dimension is what it draws.

**The numeral rounds; the bar does not.** 38.35 reads as `38` and lights 38.35%
of its track. The number stays legible, the chart stays exact.

Built into `packages/design-system` rather than locally in `apps/web`, per Epic
0's build-before-screens rule. Pure and hook-free, so it stays server-renderable
like Card, Badge and Table — Epic 7's static-markup path depends on that.

### The em dash was hiding a real distinction

`compositeScore` is null for **two different facts**, and the cell rendered both
as `—`. A bare dash reads as a rendering fault, not an outcome — which is
precisely the impression this pass set out to remove.

`status` separates them, from data the endpoint already serves:

* **queued / running** → no score *yet*. "Measuring". It will resolve on its own.
* **anything terminal** → no score *at all*. "Not scored". Never scored, or
  scored `INSUFFICIENT_DATA`, and permanent until re-run.

Both draw an **empty track** rather than a zero-width fill, so the row keeps its
shape and an absence is visibly an absence. That follows the precedent
`ScoreDisplay` set on the report, which pairs its dash with "Not enough data to
score this scan" rather than leaving a dash to speak for itself.

### Status stays `Badge` — that is the decision, not an omission

The brief asked for status to carry more weight. It does not get it from colour.
`tokens/color.ts` keeps the semantic and visibility palettes **disjoint**, and
`Badge`'s own docstring says why: it is "what stops a red error chip from being
misread as 'bad score'". Giving status the ramp would breach that from the other
side.

The weight went where it earns something instead. The meter now carries the row,
and the in-flight states say "Measuring" — so the rows that are *doing* something
look different from the rows that are done. A finished scan's status is the least
interesting thing about it; its score is not.

### Typography, deliberately unchanged

The agency name already used the editorial face, matching the report's voice, and
the table stays in the UI face. This is an index, not a document: the report's
editorial register belongs to the argument it makes, not to a list of rows.

`VisibilityBadge` is no longer rendered here — the meter states the same band
inline, and two chips per row was the noise the pass existed to remove. The
component is untouched and still exported.

### One existing assertion changed on purpose

`expect(html).toContain('38.4')` became `'>38<'`. A dashboard showing **38.4**
beside a report showing **38** is exactly the two-screens-one-product mismatch
this brief closes, and sub-point precision is not a distinction an agency
operator acts on. Recorded here rather than buried, because silently relaxing a
test someone wrote deliberately is how a suite stops meaning anything. Its
describe block's actual intent — *a null score is never a zero* — is asserted
harder than before: the absence is now named, and the lit element must be absent.

### Audits

Every className checked against `tailwind-preset.ts`'s real scales before
committing, per Epic 9.8's finding — all 40 resolve, and the pass **removed** ad
hoc styling rather than adding any. Every CSS custom property in the new block
checked against `tokens.css` the same way: all 11 exist. Epic 9.3's empty-state
tests confirm `isEmpty` and no-scans-yet are unregressed.

### IP-safety self-check

* **#1** — designed from `ScanSummaryOut`'s real fields and the operator's goal.
  **No competitor dashboard was referenced, opened, or described.**
* **#2** — every element from `@avp/design-system`; the new primitive lives in
  the design system, not in `apps/web`.
* **#3** — no tiles, no trend arrows, no deltas. The narrative-report ruling is
  untouched.
* **#4/#7** — no icon, asset or third-party content added.

**IP-safety check passed:** design-system components only, no ad hoc Tailwind, no
new colour or type scale, no competitor reference, no third-party content.

### Tests

design-system 99 → **106**, web 152 → **153**. Suite **948**, up from 940.
`tsc --noEmit` clean in both packages.

---

## 2026-08-27 — Epic 9.10 · A public page, including the part that says what it cannot do

Until now `/` showed a sign-in panel to anyone without a session. A stranger who
typed the domain was asked for credentials before being told what the product
was, and there was nowhere to send a prospective agency. `apps/web` contained
only authenticated screens plus Epic 9.8's share page.

### Where it lives, and why not a new path

The landing page takes over the **`signed-out` branch of `/`** rather than moving
in at `/product` or similar.

`/` is what someone types, and it is what an existing user has bookmarked. The
route already resolves the session before rendering, so the branch was there for
free: a signed-in operator still lands in intake and pays no extra click, while a
stranger gets the explanation. Sign-in became one step *behind* the call to
action rather than the front door.

The cost, stated: the page waits on a session check before painting, so it is not
a static document and is not ideal for indexing. If SEO ever matters more than
the bookmark, `LandingView` is a standalone component and can be promoted to a
static route without being rewritten. Not built now.

Verified unaffected afterwards, in a browser: signed-in `/` renders intake and
**not** the landing page; `/dashboard`, `/scans/{id}/report` and
`/share/{token}` all still work.

### Every claim is one the codebase can meet

Each was checked against this log before it was written — the pipeline and its
~6 minutes (9.2, 9.8), 24 intent-tagged prompts (4.1), five weighted dimensions
(5.2), per-answer ordinality (7.1), facts-only storage enforced by sweep tests
(ip-safety #7), named fixes with priority and effort (8.0), and one vendor in two
modes (4.2's own stated limitation).

**Nothing else is claimed.** No testimonials, no logo wall, no user count, no
press mention, no funding line. This product has none of them. Inventing one
would be contradicted three sections later by the page's own argument about
verifiable data — the fabrication would not merely be dishonest, it would be
self-defeating.

Where a page like this normally carries social proof, this one carries
north-star.md §7's argument instead: an agency puts its own name on the report,
so what is underneath it matters, and "we never store or reproduce anyone's
copyrighted content" is enforced by tests rather than asserted in a policy.

**No price appears.** north-star.md §5.3's tiers are `[HYPOTHESIS]` and have met
no customer; publishing them would convert a working assumption into a public
promise. The page says access is by conversation during pilot, which is true.

### The example chart is synthetic, and says so above itself

Epic 9.8 produced a real scan of a real named business, and it would be more
persuasive than any invented figure. **It is not this page's to publish.** That
business never consented to having its visibility score made public, and a page
whose central argument is that this tool can be trusted with client data does not
get to open by breaching a client confidence. Consent was not merely unconfirmed
— the conversation had not happened yet — so the brief's own default applied.

The five dimensions and their §6 weights are real; the sub-scores are invented
and labelled `Example — illustrative figures, not a real client`. A test asserts
the label appears **before** the chart in the markup, so it cannot drift into a
footnote below the fold where it would stop working.

### The section that says what it cannot do

One vendor today, no PDF export, name-only white-labelling, no self-serve plan.

A page like this usually stops before that section. It is here because the
section immediately above it is about verifiability, and a page that overstates
its own product spends exactly the credibility it just asked for. It is also the
cheapest possible demonstration of the claim: a reader can check every limitation
against the product in a minute.

### `PageSection`, and why it is not `Beat`

New primitive, in the design system rather than `apps/web` — a hero styled
locally is precisely the ad hoc styling ip-safety.md #2 prohibits, and Epic 0's
build-before-screens rule does not exempt marketing layout.

It deliberately does **not** reuse `Beat`. `Beat` is bound to `BeatId` and numbers
itself from `BEAT_SEQUENCE` to enforce the score → gap → proof → fix → pitch
order #3 mandates. If a marketing section could take a step number, that sequence
would stop meaning anything on the screen where it is load-bearing. A test
asserts `PageSection` never emits beat markup.

### The tests are mostly negative, and one of them was wrong

The risk on a marketing page is not that a heading fails to render. It is that a
claim appears which nothing supports — and a suite that only checked the copy was
present would pass just as happily on an invented testimonial. So the assertions
are: no testimonial or press shapes, no `N+ brands` construction, no real client
named, no price, the example label precedes the chart, no `<img>` or background
image, no arbitrary-value or raw-palette utility.

One of them was a false positive on honest copy: `toContain('rated')` matched
inside **"Gene*rated*"**. Word-bounded. A small reminder that a lint-by-substring
over prose fails in the direction that wastes time rather than the direction that
ships a defect — but it fails.

### IP-safety self-check

* **#1 / #5** — written from the data model, this build log and product-spec.md
  §3 only. **No competitor site was opened, referenced, or described to me while
  building this**, and no section order, layout or visual element came from one.
* **#8** — every sentence newly written. Nothing here is a reworded version of
  anyone else's marketing copy.
* **#4** — no icon pack, illustration kit or stock imagery. The only graphic is
  the product's own Luminance Ledger; a test asserts no `<img>` and no background
  image is emitted.
* **#2** — every element from `@avp/design-system`; all classes audited against
  `tailwind-preset.ts`, with tests rejecting arbitrary-value and raw-palette
  utilities.
* **#7** — no third-party content anywhere on the page.

**IP-safety check passed:** no competitor copy, layout or asset referenced or
reproduced; no fabricated social proof; design-system components and tokens only.

### Tests

design-system 106 → **109**, web 153 → **166**. Suite **964**, up from 948
(api 623, workers 13, shared-types 53, design-system 109, web 166).
`tsc --noEmit` clean in both packages; `next build` compiles all five routes.

---

## 2026-08-27 — Epic 9.11 · The screens nobody looked at, and two bugs only a browser could find

The last planned UI pass before Epic 10. Epics 7, 9.8, 9.9 and 9.10 brought the
report, share page, dashboard and landing page to a standard. What was left were
the screens a user hits **first** — sign-in, intake, classification — plus every
loading and error state in the app. A polished report behind a rough sign-in
form still reads as unfinished on first impression, which undermines exactly the
credibility the rest of the session was building.

### Four loading states and five error states, all the same

The audit found `LoadingState`-shaped markup written out longhand four times
(root, dashboard, report, share) and `ErrorState`-shaped markup five times
(three full-page, plus the dashboard's poll-failure and re-run cards). Identical
structure, different strings, no shared treatment. That is how a product ends up
looking assembled rather than designed.

Both are now single primitives in the design system, per Epic 0's
build-before-screens rule.

**`LoadingState` has no spinner and no progress bar**, and that is a rule rather
than an omission. The intake screen set it in Epic 2 and the dashboard restated
it in Epic 9.7: this product cannot measure real progress on any of its long
operations — Epic 9.1's phase table is exposed by no endpoint — and a bar that
fills on a timer is a lie the user eventually catches. Naming the work is what
makes a wait tolerable, so that is all it does. It also omits an empty step list
rather than rendering a bullet of nothing, because inventing steps would imply
progress the screen cannot observe.

**`ErrorState` owns the treatment, never the words.** Only the caller knows
whether a 404 means "that scan is not yours" or "that link has been withdrawn",
and collapsing those into one generic message is how error screens stop being
useful. The title is a statement, never a status code; the machine code renders
small and last, for someone who needs to quote it back.

### Two width tokens, replacing two numbers nothing defined

`--avp-form-width` (26rem) and `--avp-headline` (20ch) retire `max-w-[26rem]` in
SignInPanel and `max-w-[20ch]` on the intake header. Neither was *broken* —
arbitrary values do compile, unlike Epic 9.8's `border-border-subtle` — but both
were off-system numbers, which is what ip-safety.md #2 rules out.

**After this pass `apps/web` contains zero arbitrary-value and zero raw-palette
classes**, verified by grep across every `.tsx` in the app. Every CSS custom
property in the new blocks was checked against `tokens.css` first (13/13), and
every className against the preset's real scales — the third time this project
has run that audit deliberately, and the first time it found nothing.

### The sign-in error was blamed on the password

Every failure landed on the password field. An unreachable API rendered "Could
not reach the API" underneath **Password**, so a network problem read as a wrong
credential and sent people to reset something that was never wrong. Only a
rejected credential (401/422) belongs on the field; everything else is now an
`ErrorState` above the card.

### The intake form showed a failure and its own progress at the same time

**Found in a browser, and visible nowhere else.**

Submitting a duplicate domain rendered the field error — *"helpscout.com is
already tracked by this agency"* — with the "Reading the site" progress card
still sitting underneath it. `onStarted` moved the page into `working`, and
nothing ever moved it back, so a rejected submission left the screen claiming to
be mid-flight indefinitely.

**Neither component was wrong on its own.** `IntakeForm` handled its error
correctly; `page.tsx` rendered `working` correctly. The defect lived in the
handshake between them, which is precisely the class of bug a unit test on
either side passes straight through — and the reason the brief asked for a
browser walkthrough rather than a green suite.

`onFailed` is **required, not optional**. An optional callback would let a caller
silently drop the wiring and reintroduce it; required makes the compiler the
guard. That is a stronger guarantee than a test, and one this repo could not
write anyway — there is no DOM-driving test library here, and adding one for a
single assertion would need a licence review under ip-safety.md #6. The test
pins the decision with `@ts-expect-error`, so making the prop optional again
fails the suite.

### The classification screen reported a number it cannot stand behind

**This one was not a cosmetic fix, and it is worth saying so.**

The screen disagreed with itself about what kind of thing it was. Both failure
branches already argued in sentences; the SUCCESS branch was a `<dl>` of
Industry / Niche / Confidence — the generic field-list shape ip-safety.md #3
rules against — on the screen that decides what every competitor and prompt in
the scan is generated from.

It now leads with the conclusion as a claim, the way the report's beats do:
*"Ghost is a publishing and newsletter software."*

**The confidence percentage is gone, deliberately.** `industryConfidenceScore`
is the model's own self-report, and Epic 2.6 Finding 2 measured what it actually
does:

> Scores across five sites: **0.97, 0.97, 0.97, 0.97, 0.96.** Effectively one
> value, despite the prompt explicitly asking for calibration.

A number that does not move with its input carries no information, and rendering
it as "97%" borrows the authority of a measurement it has not earned. That is
the same error as rendering a null score as a zero, which this project already
refuses to make. The qualitative label stays — the threshold behind it is real
and does gate the AMBIGUOUS path — and the screen now says plainly that the
label is not a calibrated score.

**Presentation only. The calibration problem is still open and still Epic 2.6's.**

The screen was also a dead end: it offered only "Scan another site", so a
classified business led nowhere and the core loop's next step (product-spec.md
§3: URL → detect → **run scan**) was reachable only by finding the client again
on the dashboard. It now offers a route onward — navigation to a screen that
already exists, not a new capability.

### Verified in a browser, end to end, in one sitting

No session, then signed in: landing → sign-in (including a rejected credential)
→ intake → **live classification of `ghost.org`** → dashboard → report → share,
plus the report's 404 and the share link's 404. **Zero JS errors at any step.**

Spend: one classification was approved in-session. It took two attempts and cost
less than approved — the first, against `northaven-dental.com`, is the intake
form's own placeholder and does not resolve, so it returned `FETCH_FAILED`
before any model call and incidentally verified the `unclassifiable` branch live.
The second, `helpscout.com`, was rejected as a duplicate before any model call
and is what exposed the progress/error collision above. Only `ghost.org` spent a
model call. No SerpApi quota was touched.

### WAS THIS COMPLETABLE AS ONE PASS? Mostly — with two things named, not built

The brief asked for this to be answered plainly rather than discovered later.

**Everything in scope was completed as a visual/structural pass**, with one
screen needing genuine restructuring rather than polish (classification, above)
and two bugs fixed that were state-handling defects rather than styling.

**Two things are missing FEATURES, and were deliberately not built:**

1. **There is no sign-up anywhere in `apps/web`.** `POST /auth/sign-up` has
   existed since Epic 1.3 and nothing in the browser has ever called it. The
   landing page's call to action leads to a sign-in form a new visitor cannot
   use. Epic 9.10 shipped a public page inviting strangers in; the door it leads
   to only opens from the inside. The panel now states the real situation, which
   is honest but is not a fix.
2. **A scan cannot be started from intake.** `runScan` is called only from the
   dashboard, so the core loop in §3 is broken between "detect" and "scan": a
   user classifies a business and must then find it again on another screen.
   Adding a button there spends real money and belongs where the re-run guard
   already lives, so it is a scoping decision, not a one-liner.

Both are small, both are Epic 10 territory or a slice before it, and neither is
a UI polish question.

### IP-safety self-check (constraint 9)

* **#1** — every decision came from the data model (`ClientDetail`'s real
  fields), the build log, and what the screens already did. **No competitor
  screen was referenced, opened, or described at any point in this pass.**
* **#2** — every element from `@avp/design-system`; two new primitives built
  into the design system rather than solved five times locally; **zero
  arbitrary-value and zero raw-palette classes remain in `apps/web`.**
* **#3** — the classification screen moved AWAY from a field list toward a
  stated conclusion, which is the direction this constraint points.
* **#4** — no icon pack, illustration or asset added. The only icons remain
  Lucide (MIT), already in use since Epic 2.
* **#7/#8** — no third-party content rendered anywhere; all new copy newly
  written.

**IP-safety check passed:** design-system components and tokens only, no ad hoc
Tailwind anywhere in the app, no competitor reference, no third-party content,
no dependency added.

### Tests

**995, up from 964** (api 623 unchanged — this pass touched no backend —
workers 13, shared-types 53, design-system **109 → 117**, web **166 → 189**).
Three components that had never had a test now have 21 between them. Run live
before the pass at 964 and after at 995, with `set -o pipefail`. `tsc --noEmit`
clean in both packages; `next build` compiles.

---

## 2026-08-27 — Epic 9.13 · A shell, a second vendor, a way back in — and the gap the walkthrough found

The largest brief in this project's history, and the first to add backend
surface inside an otherwise frontend arc. Both exceptions are named here rather
than left to look like scope creep, and the list of what was deliberately NOT
built is at the end so nothing is later assumed to exist.

### Part B was already done, and is not counted twice

The brief asked for sign-up. **Epic 9.12 built it in the previous session** —
`SignUpPanel`, `api.signUp`, the email-on-the-field error mapping, and
SignInPanel's real link. Those two commits were unpushed when this brief
arrived; they were pushed first and nothing was rebuilt. What 9.13 added on top
is the routing decision: the landing CTA now leads to sign-**up** rather than
sign-in, and a new agency goes to onboarding rather than an empty dashboard.

### THE TWO BACKEND EXCEPTIONS, AND WHY NEITHER COULD BE UI

**Password reset.** Cannot exist as a screen: it needs a token table, an expiry,
single-use enforcement, and something that can reach an address only the account
holder controls.

**A second engine.** Cannot exist as a screen either — it is an adapter, a
registry entry, and a provider key.

Everything else in this epic is composition over what already shipped.

### The migration the brief asked for, which would have been theatre

Item 17 asked for an `Engine` enum migration modelled on
`add_claude_search_engine`. **None is needed.** `Engine.CHATGPT = "chatgpt"` has
existed since the initial schema, and the live CHECK constraint already permits
it — read off `avp_dev`, not assumed:

```
CHECK (engine IN ('chatgpt','perplexity','google_ai_overview','gemini',
                  'claude','claude_search','copilot'))
```

The migration the brief pointed at exists because `claude_search` was the
SEVENTH member, added after the enum was fixed. `chatgpt` was in the original
six. Writing one here would have altered nothing and claimed to have done work.

`OPENAI_API_KEY` also turned out to be **provisioned already** — north-star.md
§3 Layer 2 still records it as empty, which is now stale, and Epic 4.2's
"credential-bound, not design-bound" limitation was therefore already liftable.

### The engine: same reasoning, second vendor

`chatgpt` is deliberately the PARAMETRIC analogue of `claude`, not of
`claude_search`. Epic 4.2's argument is applied unchanged rather than replaced:
two modes of one vendor answer differently, and so do two vendors in the same
mode. Holding the MODE constant is what makes "Claude names you, ChatGPT does
not" a statement about the vendors instead of about browsing.

**No `openai` SDK.** Its current major requires `httpx2` — a second HTTP stack
beside the pinned `httpx` — and pinning back to 2.x drags in `tqdm`, which is
"MPL-2.0 AND MIT" and would slip past `license_audit.py`'s OR/AND handling. The
answer path needs text out of a JSON POST, and `httpx` is already vetted. Same
call Epic 3.1 made for SerpApi. **No `resend` SDK** either — its default client
is synchronous `requests` inside an async service, the exact objection Epic 3.1
raised. **Zero dependencies added across the whole epic.**

`_map_openai_error` mirrors `_map_error` rather than inventing a vocabulary, and
a test derives Claude's code set from the Anthropic exception hierarchy and
asserts OpenAI's is a subset. Branch order carries Epic 9.2's lesson: httpx's
`TimeoutException` subclasses `TransportError`, so testing transport first would
make TIMEOUT unreachable — the same shape as the defect that cost 271.6s.

Six existing tests asserted "x 2 engines" and now derive `N_ENGINES` from
`DEFAULT_ENGINES`; the pagination test walks every page instead of asserting a
fixed two-page shape. A fourth engine should not cost another afternoon of
arithmetic.

### Password reset: an endpoint built not to answer

`POST /auth/reset-password/request` is **always 200 with the same body** — for a
known address, an unknown one, a suspended one, and whether or not a provider is
configured. Tests assert byte-identity, because a different sentence is as good
an oracle as a different status. `authenticate` already burns a dummy Argon2
hash on the login path for exactly this reason.

**Only the digest is stored — deliberately NOT Epic 9.8's share-token pattern.**
`Scan.share_token` is clear-text because an operator must be able to copy that
URL again. A reset token is minted, emailed once and never shown back, so it
follows `SessionStore`'s rule and a dump of the table hands an attacker nothing.

`services/email.py` never raises and never reports its outcome — that is the
security property the endpoint depends on, not tidiness. Unconfigured, it logs
the would-be email and says plainly that nothing was sent. **It is not a fake
send.** Verified live below.

The migration drift test rejected an index declared by hand: `fk_column` already
sets `index=True`, so it was a second index on one column. Removed, and `avp_dev`
was dropped and re-migrated so it matches a fresh apply exactly.

### The shell: no item is a stub

`AppShell` + `NavItem` frame what Epics 7 / 9.8 / 9.9 / 9.10 settled and restyle
none of it. A test asserts the shell renders exactly ONE `<main>` and passes its
child through verbatim.

`NavItem` has **no disabled state to reach for**. Settings is a destination that
says what is missing and why; Clients reads `GET /clients`, which has existed
since Epic 2 with nothing in the browser calling it — the same way `sign-up` sat
unused until 9.12. `/share/{token}` deliberately does not get the shell, and the
walkthrough confirmed a stranger sees no workspace navigation.

Dashboard is the landing route after signing **in**, and only then: `/` typed
directly still gives Compare, which is what it has always been.

### Onboarding: orchestration, nothing forked

`IntakeForm` and `ClassificationResult` are rendered as-is. The wizard has no
scan button of its own because `ClassificationResult` grew one in 9.12, and a
second would be the second scan-triggering path this project has avoided.

**The agency's own client is an ORDINARY dashboard row**, stated before
building: no column records "this one is ours", inferring it from the name is
wrong for any agency whose trading name differs from its URL, and the loop
treats every client identically. A visual exception would be a claim the data
cannot support.

### THE LIVE WALKTHROUGH, AND WHAT IT FOUND

Approved in session. Twenty steps, one sitting, **zero JS errors at any step**:
landing → sign-up → onboarding → classification → 3-engine scan → dashboard →
report → Compare via nav → Clients → Settings → share (no cookie, no shell) →
sign out → forgot password → reset link from the log → weak password refused →
password changed → link reuse refused → sign in with the new password.

Account created: `founder@northlight.example` / agency **Northlight Partners**.
Password is now `a-brand-new-northlight-passphrase` (it was reset during the
walkthrough, which was the point).

**The scan: `scan_01M11J9HPQ533CN7JZMY7BQWAX`, 24 prompts, 72 engine results.**

| engine | ok | other |
|---|---|---|
| `claude` | 24 | — |
| `claude_search` | 23 | 1 `TIMEOUT` |
| `chatgpt` | 23 | 1 `answered_no_mention` |

Status `partial`, **caused by the incumbent grounded engine, not the new one** —
`answered_no_mention` is a valid finding, not a failure. The new vendor was the
cleanest of the three on its first real run.

Measured spend: **120 Anthropic + 24 OpenAI = 144 calls, and ZERO SerpApi**
against a predicted 79–151. The SerpApi zero is not good news — see below.

**Three engines render with no frontend change**, verified rather than assumed:
`ENGINE_LABEL` already mapped `chatgpt`, `engineLabel` falls back to the raw
key, and the generated `Engine` union already contained it. The report showed
three engine-coverage blocks and 40 ChatGPT references.

### THE GAP THE WALKTHROUGH FOUND, AND IT IS SIGNIFICANT

**A scan started from the UI runs prompt generation and engine execution, and
nothing else.** `run_scan` does not detect competitors, does not score, does not
audit, and does not generate the LLM fix list. Those are four separate endpoints
that only `verify_e2e.py` has ever chained.

Concretely, in the walkthrough: SerpApi spend was zero because detection never
ran; the dashboard read **"Not scored"**; and scoring by hand afterwards returned
66.57 carrying `NO_COMPETITOR_SET`, `NO_AUTHORITY_DATA` and
`TECHNICAL_FOUNDATION_NOT_MEASURED` — three of the five dimensions unmeasured.

The screens are all honest about it: Epic 9.9's `ScoreMeter` says "Not scored"
rather than showing a zero, and the report renders the degraded state rather
than smoothing it. **But the button reads "Run a scan" and produces a partial
artifact**, which is a product defect, not a rendering one.

This was not introduced here — it has been true since Epic 9.5 made the endpoint
asynchronous, and Epic 9.12 inherited it when it wired the button. It is named
now because a walkthrough is what surfaces it. **Fixing it is backend
orchestration and its own brief**, and it is deliberately not smuggled into an
epic whose backend exceptions were scoped to reset and the engine.

### DELIBERATELY NOT BUILT — so nothing is later assumed to exist

* **Content generation / briefs** — no endpoint, no screen, no button.
* **Conversational agent chat** — none. north-star.md §2.4 still records
  "where a real agent is worth its complexity" as [OPEN].
* **Ads / shopping tracking** — none.
* **Query fan-out** — none. Still `product-spec.md` §7 Epic 12, unchecked.

Also absent and named: PDF export, share-link expiry and revocation, an
authenticated password change, seat invitation endpoints, white-label logo and
colours, and any billing surface. `/settings` lists most of these on screen with
the reason each is missing, rather than hiding them.

**`PROMPT_CONCURRENCY` is untouched at 4.** Each slot now awaits three
concurrent calls rather than two. Wall clock should move little — a slot costs
max(latency), and the measured `chatgpt` latency was 3.8s against the Claude
pair's 22.7s median — but that is a **prediction**, labelled as one, and Epic
9.1's discipline forbids asserting three-engine timing from a two-engine run.
Re-timing `verify_e2e.py` at three engines is a follow-up.

### IP-safety self-check (constraint 9)

* **#1 / #5** — **no competitor screen, site, layout or algorithm was
  referenced at any point.** The OpenAI adapter's design comes from Epic 4.2's
  own precedent and the `EngineAdapter` Protocol; the shell comes from the four
  destinations this product actually has.
* **#2** — every element from `@avp/design-system`; four new primitives built
  into it rather than locally. **`apps/web` still has zero arbitrary-value and
  zero raw-palette classes**, audited again this epic.
* **#4** — icons are Lucide (MIT), already in use since Epic 2. No pack added.
* **#6** — **zero dependencies added.** Both candidate SDKs were assessed and
  declined on licence and architecture grounds; licence audit unchanged.
* **#7 / #8** — `EngineAnswer.text` remains transient on the new adapter, a test
  asserts the answer never reaches a log view, and all copy is newly written.

**IP-safety check passed:** no competitor reference, no third-party prose
persisted or rendered, design-system components only, no ad hoc Tailwind, no
dependency added.

### Tests

**1066, up from 1009** (api 623 → **665**, workers 13, shared-types 53,
design-system 117 → **125**, web 203 → **210**). `ruff` clean, `mypy` unchanged
at its pre-existing 38, `tsc --noEmit` clean in both packages, `next build`
compiles 9 routes. `openapi.json` 24 → 26 paths.

---

## 2026-08-28 — Epic 9.14 · The last agency-facing surface: seats, a PDF, a password

Three screens that close out what an agency can actually do with this product,
plus the test-coverage gap 9.13's own review left open. Three backend
exceptions, two of them pre-agreed and one designed here from nothing.

### The two exceptions that were already agreed, and the ones that were not

`POST /agencies/{agencyId}/invitations` and `DELETE /users/{userId}` sat in
api-contracts.md's "planned, not yet built" table from Epic 1, with a note
saying the `invitations` table, `services/seats.py` and
`SessionStore.revoke_all_for_user` were built and tested and only the HTTP
surface was outstanding. That note was accurate — no migration, no new domain
logic, no new table.

**But two more endpoints turned out to be necessary, and neither was in the
table.** `GET /agencies/{id}/seats`, because `/auth/me` carries the seat COUNT
and never the roster, and a management screen needs the roster. And `POST
/auth/invitations/accept`, because an INVITED user has a null `password_hash`
and `authenticate` refuses it by design — an invitation nobody can redeem is a
seat consumed by nobody, and the brief's own walkthrough step ("sign in as that
account") is impossible without it. Both are written up in full rather than
merely shipped.

### THE THREE DECISIONS THE BRIEF ASKED TO BE STATED

**Re-inviting a pending address is allowed, and consumes no second seat.** The
`User` row with `status = INVITED` is what occupies the seat, and it is the row
being re-invited, so the call revokes the outstanding link and mints a new one.
Refusing — which the sign-up path's `email-already-registered` check would have
done — leaves an agency with a seat consumed, nobody in it, and no way out but
removing the seat and starting over. A new link retires the old one, the same
rule `password_reset.request_reset` applies for the same reason.

**Removing a seat revokes every session it holds, immediately.** `sessions.py`
opens by saying this exact case is why the product has a server-side session
store rather than JWTs. There IS a fallback and it is not the mechanism:
`current_principal` re-reads the user and 401s on a soft-deleted row, closing
the door within one request; revocation closes it within zero, and the
difference is a request already in flight.

**An authenticated password change revokes every OTHER session and keeps the
caller's.** Deliberately not the reset flow's answer:

| | reset-confirm | change-password |
|---|---|---|
| What the caller proved | control of an inbox | knowledge of the password |
| Other sessions | revoked | revoked |
| The caller's session | revoked; signed out | re-minted; stays signed in |

The reset flow signs the caller out because they proved control of a mailbox,
not knowledge of a password. Here they just demonstrated the password, so
signing them out of the session they are using is friction with no security
value — the argument sign-up already makes. The other sessions are a separate
question with the same answer: the commonest reason to change a password while
signed in is thinking somebody else has it. The caller's token is **re-minted
rather than spared**, because "revoke all except this digest" is a special case
inside a revocation path, and a bulk revoke with an exception in it is the code
that later fails to revoke.

**A third decision nobody asked for.** `uq_users_email` does not exclude
soft-deleted rows, so a removed address could never be invited again — a
permanent ban earned by one mis-click. Re-inviting a removed seat REVIVES the
row, which also keeps `scans.requested_by_user_id` pointing somewhere live.

### THE PDF, AND WHY NO DEPENDENCY WAS ADDED

product-spec.md §5.1 names React-PDF or WeasyPrint. Both were checked against
ip-safety.md #6 rather than assumed — the discipline 9.13 applied to `openai`
and `resend` — and both were declined:

* **WeasyPrint** is BSD, but hard-requires **Pyphen**, whose PyPI classifiers
  are `GPLv2+`, `LGPLv2+` and `MPL 1.1`. All three trip `license_audit.py`; #6
  requires explicit sign-off for GPL/LGPL and MPL is not in ALLOWED either.
  Read off PyPI metadata, not memory. It also needs system pango/cairo.
* **React-PDF** is MIT and architecturally wrong: a Node library called from a
  FastAPI endpoint means a subprocess in the request path. And the reuse
  argument that would justify it does not survive the library — it renders its
  own `StyleSheet` primitives, not HTML with Tailwind classes, so `ReportView`
  could not be rendered by it in any case.
* Checked while there: **fpdf2** is `LGPL-3.0-only`, blocked. **reportlab** is
  BSD with clean transitive licences and would pass — declined because this
  document has no image and no embedded font, so it buys a native Pillow wheel
  for nothing.

So `services/pdf.py` writes the file directly, using the PDF standard-14 fonts
(no embedding) and stdlib `zlib`. **Zero dependencies added, again.** It is not
a layout engine and says so: one column, rules, page breaks. Adobe's AFM width
tables are embedded because wrapping has to measure, and the standard 14 are
frozen by the specification.

### The uncomfortable part, named rather than hidden

`derive.ts` computes the report's argument, and its own docstring warns against
a second implementation — "the chart annotating one dimension while the headline
names another". A Python process cannot call it, so
`services/report_narrative.py` IS that second implementation.

**It is policed, not trusted.** `tests/test_report_narrative.py` and
`crossLanguage.test.ts` read the same two checked-in files — six report payloads
(real Help Scout data from the Epic 5/6 run, plus its degraded variants) and the
expected output generated from the TypeScript side — and both assert against
them. Change either derivation and one suite reddens on the field that moved.
Ported with it: JavaScript's `Math.round` is half-away-from-zero and Python's is
banker's rounding, which would have printed a fix as 19.2 points in the PDF and
19.3 on screen.

**A stranger holding a share token can download the PDF**, and the share page
offers it. It exposes nothing the JSON route does not already serve the same
holder; Epic 9.8 minted the token because a prospect must not need an account;
and granting the live URL while withholding the file is backwards for a send
path. The cost — a downloaded file outlives revocation — is currently zero,
because share links have no revocation at all. **When revocation ships, that is
the route to revisit.**

### Three defects found while building

* **`redis_client.get_redis()` fell back to `get_settings()`**, which the
  `client` fixture had just cache-cleared — so every session the suite minted
  went to Redis DB 0 while `_clean_state` flushed DB 15. The conftest
  docstring's promise ("DB 15 deliberately, so a local dev session on DB 0 is
  never signed out") was not being kept. Found because the seat-removal test
  reads Redis directly and got an empty set from the wrong database.
* **Starlette's path converter matches `.`**, so `/reports/{token}` matched
  `/reports/abc.pdf` with a token of `"abc.pdf"`, and routes are tried in
  registration order — every PDF request silently became a 404 JSON request.
  The `.pdf` route is registered first and a test asserts it.
* **api-contracts.md never recorded `reset-password/request` or `/confirm`.**
  Epic 9.13 built both; the file's opening rule says every endpoint is recorded
  in the task that adds it. Both are written up now, saying plainly they are
  late.

Also noted, not fixed: **`ApiModel` sets `str_strip_whitespace=True`, so
passwords are whitespace-trimmed on every path** — sign-up, login, reset and
change alike. Consistent, so nobody can be locked out by it. Recorded because it
is surprising.

### A MISTAKE MADE AND CORRECTED IN THIS EPIC

The first draft of the PDF endpoint tests called `POST /clients/{id}/scans`
against the suite's INLINE executor, which runs prompt generation and every
engine call **for real**. It hung for five minutes and burned at least one
Anthropic call before it was killed; no results were persisted, and the leftover
row was truncated. `test_report_endpoint.py` avoids this with a stub fixture.
The tests now defer execution entirely — which is also the better test, because
an unscored scan is exactly what a UI-started scan produces today. The brief
said "no paid API calls in this brief"; that was breached and is recorded here
rather than quietly fixed.

### Part E: the four screens 9.13 shipped with no tests

ClientsRoute, SettingsRoute, WelcomeRoute and ResetPasswordRoute had zero
coverage — not through neglect, but because every state that mattered lived
inside an effect a static render never runs. Each is now split the way
`DashboardView` was in Epic 9.3: a pure prop-driven view in `components/`, a
route that only fetches. They live in `components/` because a Next.js page
module may only export a default plus framework fields; `next build` rejects
anything else by name.

`ResetPasswordView` carries the property the brief singled out.
`test_password_reset.py` proves the BACKEND cannot distinguish unknown from
expired from used from orphaned; that was never checked where it reaches a
person, and it is exactly what a screen can undo with one helpful sentence. One
`invalid` state, a fixed title naming no reason, the API's sentence verbatim,
and a sweep over the screen's own copy for every phrase that could name a cause.

**Item 16 resolved: the Clients copy was right.** `GET /clients` does
`.order_by(Client.id.desc())` over ULIDs and
`test_intake.py::test_list_paginates_newest_first` has asserted it since Epic 2.
The review flagged it unconfirmed, not wrong. Confirmed; the copy stays.

### THE LIVE WALKTHROUGH

One sitting, real Chromium against real servers, **zero JavaScript exceptions**
(the console entries were Chromium logging the expected 403 for a member reading
the roster and 401s for the revoked seat — no `pageerror` at any step).
Screenshots in `docs/screenshots/epic914-*`.

Sign in → Settings → invite `colleague@northlight.example` → recover the link
from the API log (no provider configured, the supported development mode) →
accept in a clean browser context → signed straight into the dashboard →
confirm **2 of 3 seats** from the new account, and that a member is refused the
roster with "Only an owner or an admin can see and change who holds a seat" →
owner removes the seat, after a confirmation that states the consequence first →
**1 of 3 seats** → the removed session is dead on its next request AND cannot
sign back in → change the password while signed in → still signed in, on a fresh
cookie → sign out → sign in with the new password → report → **Download PDF**.

**Credentials as they now stand:** `founder@northlight.example` /
`northlight-changed-while-signed-in` (changed during the walkthrough, which was
the point). `colleague@northlight.example` was invited, accepted with
`the-second-seat-passphrase`, and removed — its row is soft-deleted.

**The PDF matched the on-screen report line for line**, including both degraded
states the ghost.org scan carries: Share of Voice excluded with "No comparison
was made" and Technical Foundation with "Not yet checked", each with its reason
and no sub-score — never a zero. Same agency name, same beat headings, same
biggest gap (Citation Strength, 30.5 points), same three degradation flags.
The share-token download was **byte-identical** to the authenticated one, and
the share page leaked no workspace navigation.

The null-score path was verified separately by rendering the `unscored` fixture
and opening it: a large **"Not scored"** and the sentence "Nothing here should
be read as a low score", with no number anywhere. The ghost.org scan is scored
(66.57), so the live walkthrough could not exercise that branch.

### DELIBERATELY NOT BUILT — repeated verbatim from the brief

* **The scan-orchestration gap (build-log Epic 9.13):** a UI-started scan still
  doesn't chain competitor detection, technical audit, scoring, or fix
  generation the way `verify_e2e.py` does. NOT fixed here. Named so it stays
  visible instead of getting rediscovered from zero next time. Any screen built
  in this brief must degrade honestly (the existing "Not scored" / null-not-zero
  pattern) rather than assume the pipeline ran to completion.
* **White-label branding** (`PATCH /agencies/{agencyId}/branding`) — blocked on
  a written token-override policy per api-contracts.md, not a missing screen.
  Do not build a UI for this.
* **Billing/plans** — blocked on north-star.md §5.3's pricing [HYPOTHESIS],
  which is still undecided. Do not build a pricing or plan-selection screen.
* **Share-link expiry/revocation** — known debt, not addressed here.
* **Two items Epic 9.13's own review surfaced and didn't fix:** the
  reset-password-request timing side-channel (the "user exists" branch does real
  DB work the "doesn't exist" branch skips — a possible timing tell against the
  "never reveal account existence" requirement), and the session-revocation test
  that would still pass even if `revoke_all_for_user` were deleted from the
  code. Both backend/test-rigor, both deliberately deferred, neither silently
  forgotten.
* **Motion/interactivity direction** — a separate, parallel thread (prototype
  first, decide, then spec). Not folded into this brief.
* **Content generation, conversational agent chat, ads/shopping tracking, query
  fan-out** — as in every prior brief. Not built, not stubbed, not hinted at.

Two notes on that list. The **second** deferred review item is now partially
addressed as a side effect rather than as work: seat removal and password change
both assert revocation by **reading Redis directly**, so those two call sites
fail if `revoke_all_for_user` is deleted. The reset flow's own test is
unchanged and still has the weakness described. And the **first** — the
orchestration gap — is what makes the ghost.org report carry `NO_COMPETITOR_SET`
and an unmeasured Technical Foundation, which is precisely the degraded state
the PDF was verified against.

### IP-safety self-check (constraint 9)

* **#1 / #5** — **no competitor product, screen, layout or algorithm was
  referenced at any point.** Seat management was derived from the data model and
  from `CompetitorEditor`, this codebase's own precedent for "manage a list
  against a mutating endpoint" (brief item 15). The password form was derived
  from the endpoint's own requirements. The PDF's structure is the five beats
  ip-safety.md #3 already mandates. **This is also the first epic under the new
  wording of #1 and #5**, and it was followed as written: nothing was designed
  from a competitor's screenshot, and nothing was measured against one in either
  direction.
* **#2** — every element from `@avp/design-system`. `SelectField` was built INTO
  the system rather than beside it, after a first draft hand-assembled a
  `<select>` from `avp-field__*` classes — the same rule in a thinner disguise.
  **`apps/web` still has ZERO arbitrary-value and ZERO raw-palette classes**,
  audited again.
* **#4** — icons are Lucide (MIT), already in use. No pack added.
* **#6** — **zero dependencies added.** Four PDF candidates assessed, two
  blocked on licence, two declined on architecture. Licence audit PASS,
  unchanged.
* **#7 / #8** — the PDF is the first surface to render the collected facts to a
  FILE, so the facts-only sweep was extended to it: a test decompresses the
  content streams and asserts no third-party prose reaches the document. All
  copy is our own, and `report_narrative.py`'s string table is asserted
  character-for-character against the TypeScript original.

**IP-safety check passed:** no competitor reference in design or algorithm, no
third-party prose persisted or rendered, design-system components only, no ad
hoc Tailwind, no dependency added.

### Tests

**1313, up from 1066** (api 665 → **766**, workers 13, shared-types 53,
design-system 125, web 210 → **356**). `ruff` clean, `mypy` unchanged at its
pre-existing 38, `tsc --noEmit` clean across all three packages, `next build`
compiles 10 routes. `openapi.json` 26 → **33 paths, 39 operations**.

---

## 2026-08-28 — Epic 9.15 · A price on page one, and a checkout that really charges a test card

The first commercial code in this product. north-star.md §5.4 row 3 recorded
the payment vendor as *"NOT decided — Stripe is the obvious candidate, not a
commitment."* It is decided, built, and verified against real Stripe pages.

### SAY THIS FIRST: THESE ARE TEST KEYS AND NOTHING CHARGES REAL MONEY

**`STRIPE_SECRET_KEY` is `sk_test_…`. `STRIPE_PRICE_ID` points at a Price with
`livemode: false`. Not one cent of real money can move through any of this.**

The Stripe account behind it is `acct_1U9FZr7Qi3nSzCKc`, "PSM Digital sandbox",
and it reports `charges_enabled: false` and `details_submitted: false` — Stripe's
business verification has not been done. Even if a real card were entered, it
could not be charged. Finishing that verification is the founder's decision,
whenever he wants to make it, and it is explicitly out of scope here.

**What would have to change to go live: the keys. Nothing else.** There is
deliberately no `stripe_live_mode` setting, no `if key.startswith("sk_live")`
anywhere, and no branch in `services/billing.py`, `routers/billing.py` or any
component that behaves differently on a live key. That is a design constraint
this brief asked for and it was honoured literally, for a reason worth writing
down: if the live switch required a code change, the code would be the thing
standing between a commercial decision and its effect — and that change would
get written in a hurry, on the day revenue was waiting on it.

**Existing free sign-up and onboarding are untouched and ungated.** Sign-up,
the onboarding wizard, intake, scanning, scoring, the report, the share link and
the PDF all work exactly as they did yesterday for an agency with no
subscription — which is every agency in the database. `subscription_status`
records whether an agency **pays**, not what an agency **may do**, and nothing
in the system consults it before allowing anything. The live walkthrough below
signed up a brand new agency and reached the onboarding wizard with all four
billing columns NULL, which is the cheapest possible proof of that.

### THE BRIEF SAID THE KEYS WERE IN `.env`. TWO OF THE THREE WERE NOT.

The brief opened with *"STRIPE_SECRET_KEY and STRIPE_PRICE_ID are already in the
founder's `.env` … Do not ask for them again."* They were not there. `.env` held
19 keys and none of them were Stripe's.

Asked rather than assumed, which produced two corrections in one exchange. The
founder had put `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` in — the
publishable key being the one this integration has no use for at all, since the
browser never talks to Stripe directly (see below). And a query against his
account returned **zero Price objects**, so `STRIPE_PRICE_ID` could not have
been pasted from anywhere: the Price did not exist yet.

Created it on his explicit instruction — `prod_V9Yw45K4FDE6N6` /
`price_1U9For7Qi3nSzCKcNOnb4DnH`, $29.00 USD monthly, `livemode: false` — and
wrote the id into `.env`. Recorded here because a brief that asserts a
precondition which turns out to be false is exactly the thing that gets silently
worked around, and then nobody knows which of the two states is real.

### THE THREE DECISIONS THE BRIEF ASKED TO BE STATED

**1. A separate `routers/billing.py`, not more of `routers/agencies.py`.** The
brief leaned this way and asked for confirmation rather than assumption; the
lean was right, and for the reason it gave. Three of the four routes are
agency-scoped and would sit perfectly happily beside seat management. The fourth
is why they do not: `POST /billing/webhook` has no session, no principal, no
role and no agency in its path, and is authenticated by an HMAC over its own raw
body. `agencies.py`'s module docstring opens *"Owner or admin. A member holds a
seat; they do not decide who else does."* A module carrying that sentence and
also carrying a route it does not describe is one where somebody adds a fifth
route, copies the shape of the one above it, and copies the wrong one.

**2. Four columns on `Agency`, not the three the brief named.**
`stripe_customer_id`, `stripe_subscription_id`, `subscription_status` — and
`subscription_current_period_end`. The fourth is not scope creep; it is forced
by two of the brief's own requirements read together. Point 13 asks Settings to
say *"Active — renews &lt;date&gt;"*. Point 14 says the status endpoint must never
call Stripe on page load. Both hold only if the webhook writes the date beside
the status, so it does.

**3. `subscription_status` is a `VARCHAR(32)`, not an `enum_column`.** Every
other status in this schema is a VARCHAR-backed enum with a CHECK constraint,
so this is a deliberate exception rather than an oversight. The values are
Stripe's to define and to extend — `active`, `trialing`, `past_due`, `unpaid`,
`incomplete`, `incomplete_expired`, `canceled`, `paused`, and whatever they add
next. A CHECK constraint over today's eight means that the day Stripe adds a
ninth, the webhook raises on write, Stripe retries and then abandons the event,
and the row holds a status that stopped being true days ago. **That is a wrong
answer wearing the face of a right one**, which is the failure mode this
codebase spends the most effort avoiding. `is_active()` in
`services/billing.py` owns the one interpretation that matters, in one place.

### THE `stripe` SDK, AND `email.py`'s OBJECTION ANSWERED RATHER THAN IGNORED

`services/email.py` refused the `resend` SDK in Epic 9.13 and used `httpx`
directly, on two grounds: the call was one authenticated JSON POST, and the
SDK's default client is synchronous `requests` inside an async service. Adding
a vendor SDK here needed both objections answered, not waved past.

The **first** fails because of signature verification. Stripe's webhook scheme
is a timestamped HMAC with a replay window, and hand-rolling the verification of
a payments webhook — the one place where getting it wrong means believing an
attacker who says they paid — is not a saving, it is a liability.
`stripe.Webhook.construct_event` is the reason this dependency exists;
everything else it does is a bonus.

The **second** fails on inspection. This module calls only the SDK's `*_async`
methods, and `stripe` 15.6 routes those through its `HTTPXClient` when `httpx`
is importable — which it has been since Epic 3. Verified by reading the SDK's
own client-selection code, not assumed. Nothing here blocks the event loop.

**Licence (ip-safety.md #6): `stripe` 15.6.0 is MIT.** Verified by downloading
the sdist and reading the `LICENSE` file, **not** by trusting the PyPI
classifier — the brief said explicitly not to assume, and the constraint says
the same. Worth noting the classifier was the only signal PyPI's JSON carried:
the `license` and `license_expression` fields were both `null`. Its two runtime
dependencies were already present: `requests` (Apache-2.0, via `tldextract`) and
`typing_extensions` (PSF-2.0). **One dependency added, and no frontend
dependency at all** — the checkout and portal endpoints return URLs and the
browser navigates to them, so `apps/web` needs no Stripe JavaScript library,
which is also why the publishable key is unused.

### AN API-VERSION TRAP, CAUGHT BEFORE IT SHIPPED AND CONFIRMED BY REAL DATA

**`current_period_end` is no longer a top-level field on `Subscription`.** On
the API version this SDK pins (`2026-08-26.dahlia`) it moved onto each
subscription *item*, because a subscription's items can bill on different
schedules. Every older example on the internet reads the old location.

Reading it there returns nothing, and **the failure is silent**: the status
saves correctly, Settings renders "Active", and the renewal date is simply never
there. Nothing errors. Found by checking the SDK's own type annotations before
writing the handler rather than after noticing an empty date in a browser.

The real subscription created during the live walkthrough settles it:

```
sub_1U9GiD7Qi3nSzCKcPWqAH3cm  status=active
  items.data[0].current_period_end = 1790567055
  top-level current_period_end     = None
```

`period_end_of()` reads items first and keeps the legacy top-level field as a
fallback — which is not dead code, because a webhook delivery is stamped with
the API version configured on the endpoint that receives it, and Stripe lets you
replay events from the dashboard months later. Both shapes are tested.

### THREE DEFECTS FOUND WHILE BUILDING

**1. A misconfigured server orphaned a real Stripe customer.**
`create_checkout_session` called `ensure_customer` — which creates a customer as
a side effect — and only then read `STRIPE_PRICE_ID`. With the price unset, a
customer was created at Stripe for a checkout that could never work, and the
next attempt after the price was configured reused it, hiding that it had
happened. Both settings are now read before anything is created. Found by
`test_checkout_without_a_key_names_the_exact_variable`, which was written to
assert an error message and caught an ordering bug instead.

**2. The developer's real `.env` reaches `Settings` inside the test suite.**
pydantic-settings reads the dotenv for any field a fixture does not pass
explicitly, and pytest runs with `apps/api` as its working directory — so
`Settings(environment="test")` returns the founder's actual `sk_test_` key. A
test-mode key is still a live credential pointing at a real account, and a test
that forgot to stub would have quietly created real customers in it. This was
already true for `ANTHROPIC_API_KEY` and every other provider key; billing is
just the first place where the consequence is somebody else's database.

`test_billing.py` therefore carries two autouse guards: fake values pinned over
whatever the dotenv supplied, and a client factory that **raises** so that
forgetting to stub is a loud failure naming the problem rather than a slow test
and a stranger's row in the Stripe dashboard.

**3. `test_env_template.py`'s secret-shape patterns could not match a Stripe
key.** Its four existing patterns all use hyphens — `sk-ant-`, `pplx-`,
`sk-[A-Za-z0-9]{32,}` — and Stripe uses underscores. A pasted `sk_test_…` or
`whsec_…` would have sailed through the guard and into a committed file. Three
patterns added and checked against real-shaped samples, with `price_…`
asserted **not** to match, because a Price id is not a credential.

**A fourth, found only in the browser:** clicking "Pricing" scrolled the section
heading underneath the sticky header that had just been used to click it. No
test could have caught it — a static render has no scroll position to be wrong
about. Fixed with `scroll-mt-18` on both anchor targets, verified by comparing
bounding boxes rather than by looking at a screenshot.

### TWO STALE CLAIMS REMOVED FROM THE LANDING PAGE

Not new work, but worth recording as the same class of defect this project keeps
finding. `LandingView`'s honest-limitations section said **"There is no PDF
export yet"** — false since Epic 9.14 shipped it one day earlier — and
**"Access is by conversation while this is in pilot"**, false as of this epic.

On a page whose entire argument is that its claims are checkable, a stale
limitation is the same defect as an inflated feature. It is just the flattering
direction that normally gets caught. Both are gone; the list stays, because the
four remaining lines are true. `LandingView.test.tsx` now asserts their absence,
so the next thing to ship has to come back here.

### north-star.md §5.3 — SPLIT, NOT RELABELLED

The brief asked for the `[HYPOTHESIS]` pricing flag to be updated so the doc
stops contradicting the live page. It was split instead, because relabelling the
whole section would have made a different claim untrue.

**§5.3.1 is now `[DECIDED]`:** one plan, $29/month, 3 seats, published, backed
by a real Stripe Price. **§5.3.2 keeps `[HYPOTHESIS]` and keeps saying "do not
quote them"**, because the three-tier Starter/Agency/Agency Pro table has met no
customer and does not exist in code. Only one plan is built and sold.

The section also says, in as many words, that **$29 is decided but not
validated** — the §0 re-validation trigger has not fired, no pilot conversation
has happened, no pricing objection has been heard, and §5.1's per-scan dollar
cost is still unmeasured. Those are different things, and the distinction is the
entire reason this document has labels.

Two §5.4 rows were also corrected, since this epic falsified them: row 1's
*"No `plan`, `subscription`, `billing` or `stripe` identifier appears anywhere in
`models/`"*, and row 3's *"Vendor NOT decided"*. Row 3 now says what remains
true — the **metered** half is still unbuilt and needs row 2's `UsageRecord`
first.

### THE LIVE WALKTHROUGH — WHAT WAS VERIFIED, AND THE ONE THING THAT WAS NOT

Driven with Playwright against the real dev stack, real Stripe test-mode pages
throughout. Ten screenshots in `docs/screenshots/epic-9-15/`.

| # | Step | Result |
|---|---|---|
| 1 | Landing header on page one — wordmark, Product, Pricing, Log in, Get started free | ✅ all visible without scrolling |
| 2 | Pricing section — $29, per month, 3 seats, "signing up is free and stays free" | ✅ |
| 3 | Header stays put while scrolling | ✅ sticky |
| 4 | "Log in" goes to SIGN-IN, not sign-up | ✅ sign-in form, no "Agency name" field |
| 5 | Sign up a brand new agency | ✅ reached `/welcome`, all billing columns NULL |
| 6 | Settings shows "No subscription" and the plan card | ✅ |
| 7 | Click Subscribe → real Stripe checkout | ✅ `checkout.stripe.com`, "PSM Digital sandbox", **Sandbox** badge, "Subscribe to AI Visibility Platform", $29.00/month, "3 seats" |
| 8 | Pay with Stripe's published test card `4242 4242 4242 4242` | ✅ accepted |
| 9 | Redirected back to `/settings?checkout=success` | ✅ |
| 10 | Webhook marks the agency active | ✅ — **see the caveat below** |
| 11 | Settings shows Active + renewal date | ✅ "ACTIVE · Renews 28 Sep 2026" |
| 12 | Manage billing → real Stripe portal | ✅ `billing.stripe.com`, $29.00/month, next billing 28 Sep 2026, Visa ••••4242, invoice `$29.00 Paid`, Cancel subscription |

**THE CAVEAT, STATED PLAINLY: live webhook delivery from Stripe's own
infrastructure was NOT exercised.** The founder confirmed he has not run
`stripe listen --forward-to localhost:8000/api/v1/billing/webhook`, which is
what produces `STRIPE_WEBHOOK_SECRET` and which needs his own Stripe CLI login.
That step remains his to do.

What *was* done instead, and it is worth being precise about the difference: the
**real events Stripe generated for the real payment** — `evt_1U9GiE7Qi3nSzCKcqor1WGfb`
(`checkout.session.completed`) and `evt_1U9GiE7Qi3nSzCKc48Y41whj`
(`customer.subscription.created`) — were fetched from Stripe's API and POSTed to
the running local endpoint with a signature computed against a
`STRIPE_WEBHOOK_SECRET` set for the server process. So the payload was Stripe's
own bytes, the verification was the real SDK verifier, the handler was the real
handler, and the database write was real:

```
unsigned        → 400 invalid-webhook-signature ("No Stripe-Signature header")
bad signature   → 400 invalid-webhook-signature ("did not match this payload")
valid signature → 200 {"received":true,"handled":true}
invoice.paid    → 200 {"received":true,"handled":false}   ← real, unhandled type
```

```
name                      | stripe_customer_id | stripe_subscription_id       | status | period_end
Ninth Fifteen Test Agency | cus_V9Zrm7FGFvgE2O | sub_1U9GiD7Qi3nSzCKcPWqAH3cm | active | 2026-09-28
```

**The only unexercised link in the chain is Stripe's delivery infrastructure
reaching this machine.** Everything downstream of the HTTP request is verified
against genuine Stripe data. That is not the same as an end-to-end `stripe
listen` run, and this entry does not claim it is.

Residue left in the sandbox on purpose rather than cleaned up, because it is the
evidence: one active test subscription, three test customers (two from an
aborted first attempt), and two test agencies in `avp_dev`.

### DELIBERATELY NOT BUILT — repeated from the brief so nothing is assumed

* **Live keys / real money.** Out of scope until the founder finishes Stripe's
  business verification. His call, his timing.
* **A hard paywall.** Sign-up and onboarding stay exactly as free as they were.
  Nothing consults `subscription_status` before permitting anything.
* **Multiple tiers, annual billing, coupons, proration, tax.** One plan, one
  price, monthly. Each of those is its own brief.
* **Metered/usage billing.** north-star.md §5.2's recommended model. Needs
  §5.4 row 2's `UsageRecord` first. Not started.
* **The `[OPEN]` question of what happens to an in-flight scan on payment
  failure** (§5.4). Untouched, and it must stay untouched until the founder
  decides — it is exactly the kind of `[OPEN]` that gets silently resolved by
  whoever implements billing first, and it was not resolved here because nothing
  here can fail a scan.
* **The scan-orchestration gap, the reset-password timing side-channel, the
  motion/animation direction** — all still open from prior epics, none addressed.
* **Content generation, agent chat, ads/shopping tracking, query fan-out** — as
  in every prior brief.

One thing noticed and deliberately not fixed: `.env`'s `DATABASE_URL` points at
`127.0.0.1:5432/avp`, which does not exist on this machine — the dev cluster is
`55433/avp_dev`. The walkthrough overrode it rather than editing `.env`, since
that is the founder's file and the value may be right in another environment.
Flagged, not changed.

### IP-safety self-check (constraint 9)

* **#1 / #5** — the landing page's product copy and layout were **derived from
  `product-spec.md` §3's core loop and §5.4's seven-stage pipeline, then checked
  against competitors afterward** — in that order, which is what the constraint
  requires. No competitor page was opened, read, or referenced before the copy
  existed. The check afterward (a single web search, no page rendered or
  scraped) found the result reads differently in structure and in kind: three
  tiers versus our one, metering by prompts/projects/platforms versus our seats,
  $59–$579 versus $29, free trials versus a product that is free anyway, and a
  published limitations section none of them carry. Nothing was reworded from
  anyone. The billing screens were derived from the API's own `BillingStatus`
  shape and from `SeatsPanel`, this codebase's own precedent.
* **#2** — every element from `@avp/design-system`; the pricing card is `Card` +
  `CardBody` + `Button`, the status is `Badge`. **Every class touched in this
  epic was audited: zero arbitrary-value classes, zero raw-palette classes**,
  asserted by test in `LandingView`, `PricingCard`, `BillingPanel` and
  `SettingsView`. `scroll-mt-18` and `sticky` resolve through the preset's own
  spacing scale.
* **#4** — the header wordmark is **type, not a drawn mark or a logo file**. No
  icon pack, no illustration kit, no imagery of any kind added. A test asserts
  no `<img>` and no `background-image` on the landing page.
* **#6** — **one dependency added: `stripe` 15.6.0, MIT, verified from the
  sdist's own LICENSE file rather than the classifier.** No frontend dependency
  added. Licence audit PASS.
* **#7 / #8** — no third-party prose is stored or rendered anywhere in this
  work. The only external data persisted is our own Stripe account's view of our
  own customers: two opaque ids, a status string, and a timestamp. No competitor
  marketing copy, and no fabricated social proof — 9.10's negative assertions
  survive untouched, and publishing a real price changed nothing about them.

**IP-safety check passed:** landing copy derived from `product-spec.md` and
checked against competitors afterward rather than designed from one; no
competitor screen, code or algorithm referenced; design-system components only;
no ad hoc Tailwind; one MIT dependency, licence read from source; no third-party
content persisted or rendered.

### Tests

**1414, up from 1313** (api 766 → **811**, workers 13, shared-types 53,
design-system 125, web 356 → **412**). `ruff` clean, `mypy` unchanged at its
pre-existing 38 in 23 files — **none of them in code added here**, verified by
running it against `HEAD` with the change stashed. `tsc --noEmit` clean.
`openapi.json` 33 → **37 paths, 43 operations**.

**Nothing in the automated suite touches the Stripe network**, and that is
enforced by the two autouse guards described above rather than left to
discipline. The one place the suite does not mock is
`stripe.Webhook.construct_event`: the webhook tests compute real HMAC signatures
from the documented scheme and verify against the real verifier, because that is
pure cryptography with no I/O — and mocking it would have meant asserting that
our own mock rejects what we told it to reject, which is the shape of test that
passes forever while the thing it names is broken.

---

## 2026-08-29 — Epic 9.16 · Motion: a primitive, a page that arrives, and a document that must not

Founder feedback on the built landing page: correct, and static. Three
directions were prototyped in conversation — a dark AI-native look, "editorial
but kinetic", and matching a named competitor directly. The founder chose
kinetic: keep every visual decision already made and add staggered, eased motion
on top. Nothing else.

### THE ONE REAL RISK IN THIS BRIEF, NAMED FIRST

**`LuminanceLedger` sits on both sides of the report/marketing boundary.** The
same component renders on the public landing page and inside the client-facing
report — `/scans/{id}/report`, `/share/{token}`, and the PDF. The marketing page
wants it to build bar by bar as you scroll to it. The report is a *document*:
it gets printed and put in front of a prospect's CMO, `design-direction.md` §0
makes the presenting context win ties, and §5's Direction C was declined partly
because motion does not survive becoming a document.

So the risk is not hypothetical and it is not "someone might be careless". It is
that a shared component with a new behaviour has exactly one thing standing
between the marketing flourish and the document, and that thing has to be
stronger than a comment.

**What stops it, concretely:**

1. **`staggerDimensions` defaults to `false`** — the OPPOSITE default to
   `animate`, deliberately. `animate` defaults true because the dim-to-lit
   dissolve is the signature moment the component exists for and the report
   should have it. Staggering is a page flourish, and a document must not
   acquire a flourish by omission.
2. **Neither report route passes it.** Both leave it unset, so both get the
   Epic 0 behaviour byte for byte.
3. **Six tests in `ReportView.test.tsx`** across four report fixtures and both
   routes, asserting no `avp-ledger--staggered` and no `--avp-ledger-index`
   reaches the report's markup — plus one asserting the ledger is still
   rendered, so deleting the chart cannot make the others pass.

**Verified by injected breakage, not by reading:**

| Breakage | Failures |
|---|---|
| Flip the default to `true` | 5 |
| Couple stagger to `animate` | 2 — and only the two `animate`-on renders |

The second is the one worth having. Every other test in that file renders with
`animate={false}`, and both report routes leave `animate` at its default of
`true` in production. A stagger accidentally wired to `animate` would have been
invisible to the entire existing suite and shipped into the document. Two tests
render with animation ON for exactly that case, and they are the only thing that
caught it.

### TWO OF THE BRIEF'S PREMISES WERE WRONG, AND BOTH CHANGED SOMETHING

**"`duration-reveal` and `ease-reveal` are unused."** They are not. Four rules
read them: `ScoreDisplay`'s numeral and three of the ledger's.
`design-direction.md` §4 defines that pairing as *the* reveal — the dim-to-lit
dissolve that states the product's metaphor.

That is better than the brief assumed and it decided the design. Reusing them
rather than adding page-motion timings means the text and the signature chart
arrive to the same rhythm. A second curve for text would have been the
difference between a product that reads as authored and one that reads as
assembled. The brief asked for the grep before relying on the claim, and this is
what it was for.

**"Check whether a `prefers-reduced-motion` convention exists."** It does — the
same five-line `matchMedia` read, written out longhand in `ScoreDisplay` and
again in `LuminanceLedger`. It is now `lib/motion.ts`: extracted, not
redesigned. Three hand-copied copies of an accessibility check is how one of
them ends up subtly different and nobody notices, because the failure is
invisible to anyone without the setting turned on.

### ONE TOKEN ADDED, AND ONE REAL HOLE CLOSED

`--avp-stagger-reveal: 70ms` — the only motion value this project has added
since Epic 0, and a **delay** rather than a duration. The reveal already has a
duration; this says how far apart the members of a group begin. 70ms puts four
hero elements at 810ms end to end.

The hole: `base.css`'s global reduced-motion block reset every animation and
transition **duration** and left every **delay** alone. The first staggered
thing this product ever shipped would therefore have honoured the setting by
animating instantly — and then waiting up to half a second before doing it. A
visitor who asked for less motion would still have watched content appear one
piece at a time. Both delays are reset now. Not a retrofit of existing
transitions, which the brief put out of scope; a one-line hole in a rule that
was about to be walked into.

### THE THING THIS PRIMITIVE MUST NOT DO IS HIDE CONTENT

Everything revealed starts at `opacity: 0`, so **every path that fails to reveal
is a blank page, not a still one.** The landing page carries this product's only
public explanation of itself and its only published price. Four independent
guarantees, only one of which is JavaScript:

| Guarantee | Mechanism |
|---|---|
| Reduced motion | CSS paints `.avp-reveal` fully revealed, no transition. Not a faster animation — none, and no dependence on the component running. |
| JavaScript disabled | `@media (scripting: none)` does the same. |
| No `IntersectionObserver` | The hook reveals immediately rather than waiting for a callback that will never come. |
| `animate={false}` | Starts revealed — what every static render and test gets. |

### THE BUG ONLY A BROWSER COULD FIND

`.avp-reveal-group { display: contents; }`, written to keep the group from
introducing a box of its own. It does not introduce one; **it is one** —
`RevealGroup` renders the `<section>` or the `<ol>` itself.

`display: contents` removes an element's box entirely, and **an element with no
box never intersects.** IntersectionObserver had nothing to observe. The entire
hero and all seven pipeline steps sat at `opacity: 0` forever. It also overrode
`.avp-section`'s own `display: flex`, silently dropping the gap between the
hero's parts.

147 unit tests passed the whole time, and none of them could have seen it: jsdom
computes no layout and a static render has no observer at all. The first
measurement in a real browser read `t≈0ms [0,0,0,0]` … `t≈1100ms [0,0,0,0]`.
That is what item 21 exists for, and it is the second epic running where the
live pass found something the suite structurally could not.

`tokens.test.ts` now asserts the rule stays absent — at the stylesheet level,
because that is where the mistake was.

### ONE TEST I WROTE WRONG

I asserted that `animate={false}` should produce no `avp-ledger--staggered`. It
failed, and the component was right: `staggerDimensions` is *structural* — it
says what shape the reveal takes — while `animate` says whether motion happens
at all. With motion off the modifier is present and the bars are simply already
lit. The corrected test asserts that separation deliberately rather than
deleting the question.

### THE LIVE WALKTHROUGH — MEASURED, NOT EYEBALLED

Opacity and transform sampled over time in a real browser. Fourteen screenshots
in `docs/screenshots/epic-9-16/`.

```
hero    [0.60 0.21 0    0   ] -> [0.84 0.65 0.30 0   ] -> all 1
steps   [0.44 0 0 0 0 0 0   ] -> [0.95 0.89 0.75 0.47 0.03 0 0] -> all 1
ledger  scaleY [0.51 0.08 0 0 0] -> [0.97 0.92 0.83 0.62 0.26] -> all 1
```

Before being scrolled to: steps `[0,0,0,0,0,0,0]`, whole-section reveals
`[0,0]`, ledger `scaleY(0)`. Nothing arrives before it is reached.

Auth cards, one reveal each: sign-in `0.24 → 0.66 → 0.93 → 1`, sign-up
`0 → 0.44 → 0.87 → 1`, forgot-password `0 → 0.53 → 0.84 → 1`, `/welcome`
`0.24 → 0.72 → 0.94 → 1`.

**Reduced motion, toggled at the browser and reloaded:** hero `[1,1,1,1]` at
first paint, `transition-delay: 0s`, `transition-duration: 0s`, ledger bars at 1
after 50ms with no wait, sign-in card `[1]`. Instant, not fast — verified live
as well as by test, which the brief asked for specifically.

### DELIBERATELY NOT DONE — so nothing is later assumed

* **The client-facing report, in any form.** No `Reveal` anywhere in it, and a
  test asserts the report's markup contains none. See the risk section above.
* **Settings and the dashboard shell.** Dense, functional, information-first
  screens. Settings is five `PageSection`s, and the mechanism keeping it still
  is `stagger` defaulting to false — now asserted by a test, plus a companion
  asserting the screen still renders its sections so the first cannot pass on an
  empty page.
* **`AcceptInvitationView` and `ResetPasswordView`.** Also auth cards, also not
  among the four the brief enumerated. **One line each if they should match** —
  flagged rather than guessed in either direction.
* **A full `prefers-reduced-motion` audit of the app.** In scope: the new
  primitive respects it, and the delay hole above. Out of scope: every existing
  transition.
* **New colour, type, spacing or imagery.** None. Kinetic was chosen precisely
  because it keeps everything already built.
* The scan-orchestration gap, the reset-password timing question, and everything
  else open from prior epics. Untouched.

### Dependency

`jsdom` ^30.0.1 — **MIT, verified by reading `LICENSE.txt` out of the tarball**,
not the registry field. Dev-only, `@avp/design-system` only, and scoped with a
per-file `// @vitest-environment jsdom` docblock so the other 130 assertions in
that package stay in `node`.

Needed because the primitive's contract is "starts hidden, becomes revealed when
an observer says so", and the interesting half only happens in an effect that
`renderToStaticMarkup` never runs. A test that could only see static output
would assert the hidden state and call it covered — the exact shape of test that
passes while a page renders blank.

### IP-safety self-check (constraint 9)

* **#1 / #5** — the reveal was **derived from this product's own content and
  Epic 0's own tokens.** No real company's site, code, markup or stylesheet was
  inspected, measured or referenced at any point. "Editorial but kinetic" is a
  genre name from a conversation, not a reference to a product; the technique —
  content fades and rises, staggered by a fixed delay per sibling, triggered by
  scroll position — is generic and has nothing proprietary to derive it from.
  The timing came from `design-direction.md` §4, which was written in Epic 0
  from the data model and the user goal.
* **#2** — the primitive lives in `@avp/design-system` and every screen imports
  it; Epic 0's build-the-primitive-then-apply-it order was kept, and the
  primitive shipped in its own commit with no screen depending on it. **Every
  class touched audited: 50 utility classes, all token-backed or structural,
  zero arbitrary values, zero raw palette.** The only inline styles the new code
  emits are custom properties carrying an integer.
* **#4** — no icon, illustration or image added. Nothing visual was added at
  all; existing elements move.
* **#6** — one dev dependency, MIT, licence read from source.
* **#7 / #8** — no content of any kind added or persisted; this epic renders
  nothing new.

**IP-safety check passed:** motion derived from this product's own content and
tokens, checked against nobody's page; design-system primitive built before any
screen used it; no ad hoc Tailwind; no new colour, type, spacing or imagery; one
MIT dev dependency with its licence read from the tarball.

### Docs

`design-system.md` §5c documents the primitive the way §5 and §5a document the
ledger and the shelf, and §5's signature line now carries the guardrail.
`design-direction.md`'s motion section says **what was built and where**, in a
table, plus what was deliberately left un-animated. That document has a named
history of a recommendation being approved and then not read again for five
epics; a motion section that says what should happen and never says whether it
did would be the same failure with a different subject.

### Tests

**1472, up from 1414** (api 811, workers 13, shared-types 53, design-system
125 → **148**, web 412 → **447**). *[Corrected from 1471 in the 9.16a addendum
below — the per-package figures were right and the total was added up wrong.]* `tsc --noEmit` clean across all three
packages. API and workers untouched and unchanged.


### Addendum, same day — Epic 9.16a · the two screens the entry above flagged

The 9.16 entry named `AcceptInvitationView` (`/invite/{token}`) and
`ResetPasswordView` (`/reset-password/{token}`) as auth cards that did NOT get
arrival motion, because they were not among the four the brief enumerated, and
said it was one line each if the answer was that they should match. Asked, and
answered: they should. This closes that gap.

**They are pure Views, not self-contained panels, so `animate` threads through
the VIEW and neither route changed.** Both follow the split
`SettingsView`/`settings/page.tsx` established — the route owns fetching, the
view owns markup — so the wrapper being replaced lives in the view and both
routes pass nothing, taking the `true` default. `SignInPanel` is the other
shape; naming which one applies here mattered, because guessing wrong would
have put the prop on a file that does not own the wrapper.

**Every branch is wrapped, not just the form.** `ResetPasswordView` has three
return branches (form / done / invalid) and `AcceptInvitationView` has two
(form / invalid) — where the four already-done screens had one or two. A
wrapper missed in one branch is invisible until somebody redeems a dead link
and watches the page not move, so the test asserts every state.

**The `<main>` landmark is deliberately not the thing replaced.** The reveal
wraps the card column *inside* `<main>`, matching `WelcomeView` — which is the
precedent for a view that owns its own `<main>`, where `SignInPanel` (which does
not) replaces its outermost element. A screen reader navigates by landmarks and
`Reveal` renders none of the elements `<main>` could legitimately be. Asserted.

#### What the live pass found that a test could not

Both routes were exercised with **genuinely valid tokens**, minted by writing
the SHA-256 digest directly onto a real row — the technique
`test_seat_endpoints.py`'s `_live_token_for` uses, because the raw token is
never returned by the API and that is the point of the design. Both were then
**redeemed**, which is what proves they were valid rather than merely
well-formed: the reset landed on "Password changed", and the invitation landed
on `/dashboard`.

```
/invite/{token}          0 → 0 → 0.24 → 0.66 → 1
/reset-password/{token}  0 → 0 → 0.34 → 0.72 → 1
reduced motion, both     [1] at first paint, delay 0s, duration 0s
```

**The post-submit branches do not re-animate, and that is correct.** Submitting
a bad invitation token showed the refusal at opacity 1 immediately, and
redeeming the reset showed "Password changed" the same way. The reason is that
React reconciles `<main><Reveal>` to the same component instance across the
state change, so `revealed` is already true — the reveal is about arriving on
the page, and the page has already arrived. A refusal that faded in from nothing
after a deliberate click would read as a second page load. Recorded because it
looks like a bug in a screenshot and is not one, and a later reviewer
"fixing" it would make the screen worse.

#### A correction to the entry above

**The 9.16 test total was written as 1471. It was 1472.** The five per-package
figures were correct and the addition was not. Corrected in place above and in
the README. Worth a line rather than a silent edit: this project's build log is
the thing later epics quote figures from, and a wrong total that nobody
recomputes is exactly how a number becomes folklore.

#### THE DEFECT THIS ADDENDUM ALMOST SHIPPED

An adversarial review of the change — three reviewers, one refutation pass —
found something the unit tests, the live pass and I all missed, and it is the
more interesting half of this addendum.

**These two screens are SERVER-RENDERED, and the reveal defaulted to hidden.**
Their routes' initial state *is* the rendered state
(`useState<InviteState>({ kind: 'form' })` at `invite/[token]/page.tsx:47`,
`useState<ResetState>({ kind: 'form' })` at `reset-password/[token]/page.tsx:37`),
so Next put the card straight into the HTML at `opacity: 0`. First paint was a
blank page, and it stayed blank until the client bundle hydrated — on the two
screens people open cold, from an email, on a phone, with no alternative route
if the link looks dead.

```
curl /invite/abc123          -> avp-reveal=1  revealed=0   (card in HTML, hidden)
curl /reset-password/abc123  -> avp-reveal=1  revealed=0
curl /                       -> avp-reveal=0
curl /welcome                -> avp-reveal=0
```

**The precedent was only accidentally safe.** `/` and `/welcome` both start at
`{ kind: 'loading' }`, so their `Reveal`s are only ever constructed after a
client-side fetch — at a moment when JS is provably already running, making
`opacity: 0` last one frame. Copying those four faithfully, which is exactly
what this task asked for, was not enough. The difference is in the routes, not
in the components, and nothing in the component's own file could have shown it.

**The primitive's stated guarantee was false.** `Reveal`'s docstring listed four
guarantees against hiding content and this case slipped all four:
`prefers-reduced-motion` does not match when motion is not reduced;
`@media (scripting: none)` does not match when scripting is *enabled but has not
run yet*; the `IntersectionObserver` fallback is itself JavaScript; and
`animate={false}` was not passed.

#### The fix: the default is now VISIBLE

Founder's call between three options, taken deliberately rather than patched at
the call site. `.avp-reveal` is now `opacity: 1`, and the hidden state lives
under `.avp-motion-ready` — a class added to `<html>` by a **synchronous inline
script in the document head** (`apps/web/src/app/layout.tsx`). It runs before
the first paint and only if scripting genuinely works, so:

* server HTML is readable on its own;
* a visitor whose bundle never arrives reads the page instead of a rectangle;
* a visitor whose bundle *does* arrive still gets the full arrival, hidden from
  the very first frame — no flash in either direction.

Deliberately not a React effect: an effect runs after hydration, and hydration
is the exact window this exists to cover. If the script is ever deleted nothing
breaks visibly — the product just stops animating, which is the correct failure
direction and the reason the default was inverted rather than the symptom
patched.

`@media (scripting: none)` was **removed**, not kept as belt-and-braces: with
the visible default it can no longer fire, and a rule that cannot fire is one
the next reader has to reason about for nothing.

`tokens.test.ts` asserts the default stays visible, negative-controlled by
putting `opacity: 0` back (fails) and restoring (passes).

#### Verified again, including the case that started it

```
JS enabled       /reset-password  [0, 0, 0, 0.12, 0.6, 0.8, 0.93] -> 1
JS DISABLED      /invite          card renders in full          (screenshot 22)
JS DISABLED      /reset-password  card renders in full          (screenshot 23)
reduced motion   both             [1] at first paint, delay 0s
landing page     hero             [0.52 0.09 0 0] -> [0.93 0.83 0.64 0.28] -> all 1
landing page     steps below fold [0 0 0 0 0 0 0] before scrolling
```

The landing page is re-checked because this change touches all seven revealing
screens, not just the two the task named.

**Two process notes, both worth keeping.** My own first attempt to verify the
finding returned `avp-reveal=0` on every route and appeared to refute it — the
dev server had died and `curl` was returning `status=000, bytes=0`. I nearly
dismissed a correct finding on the strength of a failed request. And the finding
itself is one no test in this repo could have produced: jsdom applies no
stylesheet, a static render has no browser, and the four reference screens
structurally cannot exhibit it.

#### Tests

**1485, up from 1472** (api 811, workers 13, shared-types 53, design-system
148 → **149**, web 447 → **459**). `tsc --noEmit` clean. API and workers
untouched.

Residue in `avp_dev` from the live pass, left rather than cleaned because it is
the evidence: one accepted seat (`live-verify-916@invite.example`) on the Epic
9.15 test agency, and that agency's owner password re-set to the value it
already had.

**IP-safety check passed:** no new component, token, colour, type, spacing or
imagery — an existing design-system primitive applied to two more screens
following the precedent already in this repo. No competitor page, markup or
stylesheet inspected or referenced.

---

## 2026-08-29 — Epic 9.17 · Orchestration: the product finishes what it starts

Every scan a real user started produced a report reading "Not scored",
`NO_COMPETITOR_SET` and `TECHNICAL_FOUNDATION_NOT_MEASURED`. Not because those
phases were unbuilt — competitor detection (Epic 3), scoring (Epic 5), the
technical audit (Epic 6) and fix generation (Epic 8) have all been done and
tested for epics — but because **nothing called them.** `verify_e2e.py` proved
the pipeline worked by calling each phase itself. The product never did.

No new scoring, audit or fix logic here. This is entirely wiring.

### THE BRIEF'S PROPOSED ORDER WAS WRONG, AND THE CODE SAID SO

The brief asked for detection *after* engine execution, and invited a correction
if the code disagreed. It does, and the disagreement is the central finding.

`scan_runner.run_scan` loads the competitor set at its top (`:276-277`) and
feeds it into two places nothing can correct afterwards:

* **prompt generation** is seeded with the rival names (`:280` → `:113`), and
* **fact extraction** scopes brand detection to them (`:294`), which is what
  produces `position`, `brands_mentioned`, Share of Voice, and the
  `competitor_id` on every `BrandMention` and `Citation`.

A scan with no set does not fail. It reports SUCCEEDED with the subject at
position 1 of 1 in every answer — the independent read of `scan_runner` put it
best: *"no error, no warning, no degraded status — the scan reports SUCCEEDED
with silently degenerate data."* Detection afterwards would have written a set
nothing had used, and the report would have looked better while measuring
exactly as little as before.

So the order is `verify_e2e.py`'s, which was right all along:

```
competitor set  ->  engine loop  ->  audit  ->  score  ->  fixes
```

Audit before scoring because `scoring_runner.load_technical_foundation` reads
the audit row and excludes the dimension as `NOT_YET_MEASURED` without one.
Fixes last because they read all three.

### THE ONE PIECE OF GENUINELY NEW CODE, AND WHY IT IS UNAVOIDABLE

`CompetitorSet` hangs off a **scan**, and `load_competitors` looks it up by
`scan_id` alone. A client's second scan therefore starts with no rivals unless
something puts them there. Both obvious answers are wrong:

| Option | Why it fails |
|---|---|
| "Skip because the client already has a set" — the brief's assumption | Leaves the new scan with nothing. The set being skipped on account of belongs to a *different scan*. |
| "Re-detect on every scan" | Six SerpApi searches per scan against a 250/month quota (north-star §5.1: ~41 scans/month platform-wide) — **and it discards the operator's correction.** `persist_detection` preserves manual rows only within the set it is writing; a brand-new set on a brand-new scan has none to preserve, so a hand-corrected list silently reverts. The brief named that as the thing to protect against. |

So `competitors.ensure_set_for_scan` is **existing → carry forward → detect**,
and `carry_forward_set` copies the rows including suppressed tombstones — a
strike that lasted one scan would not be a strike. Detection, the only branch
that spends money, runs **once per client**.

> **A reading hazard worth recording.** A carried-forward set copies
> `serp_queries_run` and `co_citation_prompts_run` verbatim, because they
> describe the detection run that produced the set. Summing that column across
> sets to estimate monthly SerpApi spend therefore **double-counts**.
> `detected_at` is what distinguishes a real run from a copy — on the live run
> below, the carried set carries `serp_queries_run = 6` and a `detected_at` of
> two hours earlier, and spent nothing.

### FAILURE ISOLATION IS NOT DEFENSIVENESS HERE

Every phase is attempted regardless of the ones before it, each committing on
its own. The failures are named and reachable, not hypothetical:

* `run_audit` raises `ValueError` on a domain `crawl.normalise_url` cannot
  parse — an *unreachable* site degrades, an *unparseable* one raises.
* `score_scan` raises `SubScoreOutOfRangeError` if an audit ever writes a
  Technical Foundation outside 0–100, which `technical_audits` has **no CHECK
  constraint** to prevent (unlike `scores`, which range-checks every column).
* `generate_for_scan` raises `RuntimeError` when `ANTHROPIC_API_KEY` is unset.

Aborting on any one would turn one missing report section into three, and the
report already renders each absence honestly on its own.

**Commits per phase, not once at the end.** `run_audit`, `score_scan` and
`generate_for_scan` all only *flush* — the caller owns the transaction — so a
single trailing commit would let a raise in fix generation discard a good audit
and score, including the paid model calls that produced them.

### A BUG IN MY OWN FIRST VERSION, FOUND BY REVIEW

`session.rollback()` **expires every persistent instance**, and unlike
`commit()` it does so regardless of `expire_on_commit=False`. `scan` and
`client` are handed to every later phase, and the first attribute access on an
expired instance under the async session raises `MissingGreenlet` rather than
reloading. One failed phase would therefore have taken out every phase after
it — by exactly the mechanism `_attempt` exists to prevent. It reloads both
instances after a rollback now.

### SCAN STATUS NOW MEANS "THE REPORT IS READY"

`run_scan` gained `defer_terminal_status`. Without it the scan reaches
SUCCEEDED the moment the engine loop ends, while the audit, score and fix list
are still ~25s away: the dashboard stops polling, the operator clicks through,
and opens the degraded report this epic exists to stop producing. The executor
stamps the status with `finalize_scan` once every phase has been attempted.

Both paths share `terminal_status_for`, so "did this scan succeed" has one
definition. It defaults to `False`, so `verify_e2e.py`, the tests and any direct
caller behave exactly as before. The terminal value still describes the
**engine** phase — a failed audit is not a failed scan.

### AN EPIC 8 BUG, FOUND AND DELIBERATELY NOT FIXED

The brief said that if wiring surfaced a real bug in one of the four phases, to
**stop and name it** rather than fix it as a side effect. One surfaced.

`fix_generator.generate_fixes` catches a careful ladder of `anthropic.*`
exceptions — `AuthenticationError`, `RateLimitError`, `BadRequestError` (with a
credit-balance branch), `APIConnectionError`, `APIError` — and maps each to a
`FixOutcome(status="failed", reason_code=...)`. But `client.messages.parse`
validates the model's JSON against `GeneratedFixSet` **inside the SDK**, and a
schema violation raises `pydantic_core.ValidationError`, which is not an
`anthropic.APIError` and is caught by none of them.

```
pydantic_core.ValidationError: 1 validation error for GeneratedFixSet
fixes.0.title
  String should have at most 200 characters
```

**The entire defensive ladder is bypassed by the one failure the model itself
causes.** Reproduced three times: once in the pre-change `verify_e2e.py`
baseline, once inside the chain, and once through `POST /scans/{id}/fixes`
directly — where it surfaces as a **500**, which it has always done. It is not
caused by, or specific to, this epic's chaining.

It is Epic 8's bug and it needs its own brief. What this epic does is *contain*
it: `_attempt` catches it, and the scan still produces a competitor set, an
audit and a full score. Recorded in `product-spec.md` §7 Epic 8 as an open
defect.

### THE MEASUREMENTS — AND THE BUDGET IS FURTHER OVER

All figures measured, none estimated. Two runs on `plausible.io`, 24 prompts,
3 engines.

**Before** — `verify_e2e.py` as it stands, run before any change was made:

| phase | seconds |
|---|---|
| crawl + classify (Epic 2) | ~20 |
| competitor detection | **24** |
| prompt generation + engine loop | **279** |
| technical audit | **5** |
| scoring | **<1** |
| fix generation | **crashed** (the bug above) |

**After** — a scan started from the **dashboard's re-run button**, in a browser,
end to end:

```
13:43:45  competitors.carried_forward   competitors=5   (0 SerpApi searches)
13:48:30  scan.engine_phase_completed   prompts=24 results=72 failures=2   (285s)
13:48:34  audit.completed               technical_foundation=67.00          (4s)
13:48:34  scoring.completed             composite=57.82 excluded=[]        (<1s)
13:48:55  scan.chain.phase_failed       phase=fixes                        (21s)
13:48:55  scan.finalized                status=partial
```

**Measured chained wall clock: 309.9s**, against a 300s budget.

**The chain costs ~25s on a repeat scan** (audit 4s + scoring <1s + the 21s
fix generation spent before it raised), and **~49s on a client's first scan**,
where detection's 24s is added and cannot be carried forward.

Stated carefully, because the numbers are not like-for-like: north-star's
**361.3s** is a full-script run *including* crawl + classify and a live
detection. The 309.9s above is a UI-triggered **repeat** scan — the client was
already classified and the competitor set carried forward. The like-for-like
statement is the one that matters: **the endpoint used to do ~303s of work and
stop; it now does the same engine work plus ~25s more, and finishes the job.**

**The budget was already missed on the engine loop alone** — 285s of the 309.9s
here, 95% of a 300s budget before a single chained phase runs — and it is now
missed by more, deliberately. That is the honest cost of the product finishing
what it starts, not a regression to hide. **`PROMPT_CONCURRENCY` is still 4,
unraised since Epic 9.1 named it as candidate fix 2, and sizing it is the known
next lever — and explicitly not this epic's job.**

### THE LIVE WALKTHROUGH

Signed in through the UI, pressed **Re-run** on the dashboard, waited. The
report at `/scans/{id}/report`:

| The brief asks the report to show | Result |
|---|---|
| an actual score | ✅ **58/100**, "Present, but losing the answer to competitors", EMERGING |
| an actual competitor set | ✅ 5 rivals, carried forward |
| actual technical findings | ✅ 17 checks, Technical Foundation **67.00** |
| actual named fixes | ❌ **0** — the Epic 8 bug above |

```
Not scored                          present=False
no comparison was made              present=False
not yet checked                     present=False
NO_COMPETITOR_SET                   present=False
TECHNICAL_FOUNDATION_NOT_MEASURED   present=False
```

`excluded_dimensions` is `{}` — **empty**. Every one of the five dimensions was
measured: Mention Rate 98.57, Share of Voice 35.03, Citation Strength 0.89,
Sentiment 84.06, Technical Foundation 67.00. The biggest-gap beat reads
*"Citation Strength is costing the most — 19.8 points."* Screenshots in
`docs/screenshots/epic-9-17/`.

Three of the brief's four are verified. **The fourth is not, and the reason is
the pre-existing Epic 8 defect, not the chain** — the report's fix beat still
renders its own derived content (audit findings, the unclaimed-domain fix from
Epic 7.1), but Epic 8's generated `action_items` are absent because the phase
raised.

**Zero SerpApi searches were spent on the live run**, by choosing the re-run
path deliberately: the client already had a set, so the chain carried it
forward. Detection's live behaviour is evidenced by the baseline run above
(status `ok`, 5 rivals, confidence 0.800, 6 searches).

### NO FRONTEND CHANGE, AS THE BRIEF PREDICTED

`git diff --name-only` over `apps/web` and `packages` for this epic's code
commit is **empty**. The report already degraded honestly; once real data
exists those states simply stop appearing. The brief asked for this to be
stated explicitly rather than assumed, and it held.

### DELIBERATELY NOT DONE

* **No new scoring, audit or fix logic.** The Epic 8 bug above was named, not
  fixed.
* **The scan-time budget.** Measured and reported, not minimised.
  `PROMPT_CONCURRENCY` sizing is the tracked lever and is not this brief.
* **Phase-level progress reporting.** A user still sees *that* a scan
  progresses, not *how far* — and the chain makes the wait longer, so this is
  now a better idea than it was. Still not built; still its own brief.
* The reset-password timing side-channel and everything else open from prior
  epics. Untouched.

### A DEV-DATABASE CHANGE, RECORDED

The live pass needed to sign in as the agency owning the clients that have
competitor sets, so `review@epic7.example`'s password was set to a known value
via the app's own `hash_password`. A dev-DB convenience, written down rather
than done quietly.

### Tests

**1494, up from 1485** (api 811 → **820**, workers 13, shared-types 53,
design-system 149, web 459). `ruff` clean; `mypy` unchanged at its pre-existing
**38 in 23 files**; the one error the first draft added (an unannotated
`coro_factory`) was fixed rather than absorbed.

The load-bearing test calls **one endpoint** and then reads the **database**,
not the API: the claim is "nothing else had to be called", and calling nothing
else is the cleanest way to demonstrate it. Negative control: deleting the
chain fails **6 of the 9** new tests, including that one.

`tests/conftest.py` gains an autouse guard keeping the chain off the network.
The first run without it **hung** and would have spent real money — with
`InlineScanExecutor` the suite now runs the whole pipeline inside every scan
request, reaching SerpApi, Playwright and Anthropic from tests that previously
touched none of them. Fix generation is stubbed at the SDK boundary
`test_fix_generator.py` already patches, so the real `generate_fixes` still runs
in-chain with its schema validation, banned-claim guard and `accept()` matching
intact.

`EnginesOnlyScanExecutor` exists so the degraded states stay testable. Several
tests asserted "no score" / "no audit" / "no fixes" by simply not asking; those
states are still real, because any chained phase can fail, so they are now
constructed **on purpose** rather than by relying on the product not finishing —
a better setup than the one it replaces.

`test_scan_endpoints`' position assertion flipped from 1-of-1 to 2-of-3. Its own
comment read *"No competitor set exists for this scan"*. That was the
degradation, and it is gone.

### Addendum, 2026-08-29 — Epic 9.18 · The overlong title that lost the whole fix list

Epic 9.17's live walkthrough closed with three of four boxes ticked and the
fourth blocked: the report had a score, a competitor set and technical findings,
and **no fixes**, because `generate_fixes` crashed on a model-authored title
longer than 200 characters. That entry named the bug and deliberately left it.
This closes it.

#### What was actually wrong

`generate_fixes` catches five `anthropic.*` exception types and maps each to a
`FixOutcome`. But `client.messages.parse` validates the response against
`GeneratedFixSet` **inside the SDK** — `TypeAdapter(...).validate_json(text)` in
`anthropic/lib/_parse/_response.py` — and a schema violation raises
`pydantic.ValidationError`, which is not an `anthropic.APIError`. **The entire
ladder was bypassed by the one failure the model itself causes.** It propagated
as a crash inside the scan chain and a raw `500` through
`POST /scans/{id}/fixes`.

#### Three things checked before changing anything

1. **`pydantic.ValidationError` IS `pydantic_core.ValidationError`** — the same
   object, not a wrapper. So the catch is exactly precise. Worth confirming
   because it subclasses `ValueError`, and catching *that* would have been
   broader than the bug.
2. **The SDK's validation is whole-response, with no per-item hook.**
   `parse_text` validates the entire document in one call, and the exception
   escapes before any `ParsedMessage` is constructed — so neither the valid
   fixes nor the raw JSON survive to be salvaged, and `ValidationError` carries
   the offending value but never the document around it. Keeping four good
   fixes and dropping the fifth would mean replacing `messages.parse` with
   `messages.create` plus a hand-rolled parse: a larger change to how this call
   is made than the bug warrants. **So it degrades as a whole**, which is what
   the brief specified for exactly this finding.
3. **No existing test covered it.** The tested failure paths were
   `NO_CANDIDATES`, a provider error, and `NO_USABLE_FIXES`.

#### The fix

One more clause in the same ladder, mapping to
`FixOutcome(status="failed", reason_code="PROVIDER_SCHEMA_VIOLATION")` — an
empty, honestly-labelled fix list, exactly like every other failure above it.
The report falls back to Epic 7's deterministic list, as it does for a provider
outage.

The log line records the field path, the error type and the **length** of the
offending value, never the value: it is model-authored copy, and a length is
what makes the failure diagnosable.

#### The root cause of the FREQUENCY, which was the more interesting half

**The length limit existed only as JSON-schema `maxLength`.** Neither the system
prompt nor either field description mentioned brevity at all — the model's only
signal was a schema constraint it did not reliably honour, and it overshot three
times out of three on this scan. The rule is now stated in words in the system
prompt and in both field descriptions, reading `MAX_TITLE_CHARS` and
`MAX_DETAIL_CHARS` so the prose cannot drift from the constants.

That reduces how often this fires. It is emphatically **not** why it is now
safe — the `except` clause is, and it holds however badly the prompt performs.

#### Verified live

The exact call that returned a raw `500` at the end of Epic 9.17 —
`POST /scans/scan_01M15SVH…/fixes` — now returns **`201`, `status: "generated"`,
five fixes**.

Then a full chained scan from the dashboard's re-run button, `plausible.io`,
24 prompts, 3 engines:

```
14:26:56  competitors.carried_forward   competitors=5      (0 SerpApi)
14:32:19  scan.engine_phase_completed   failures=0 results=72
14:32:23  audit.completed               technical_foundation=67.00
14:32:23  scoring.completed             composite=58.50 excluded=[]
14:32:44  fixes.generated               accepted=5 rejected=0
14:32:44  scan.finalized                status=succeeded
```

**347.3s, `succeeded`, and a fix list.** Zero schema violations logged. The
generated titles came in at 145, 117, 114, 112 and 126 characters — the first
run since the prompt was tightened, and every one comfortably inside the limit.

The report's fix beat now reads **"6 changes, worth 39.0 points"**: five
model-authored fixes citing this scan's own figures ("Only 10 of 281 citations
in this scan pointed at plausible.io"), plus Epic 7.1's derived
unclaimed-domain fix. Screenshots in `docs/screenshots/epic-9-18/`.

**All four of Epic 9.17's boxes are now ticked on one report.**

#### Tests

**1496, up from 1494** (api 820 → **822**). `ruff` clean, `mypy` unchanged at
its pre-existing 38.

Both new tests drive the failure through the **real validator** — the SDK's own
`TypeAdapter(...).validate_json` on an over-length title — rather than raising a
hand-built `ValidationError`. A test that raises the exception itself proves the
`except` clause is spelled correctly and nothing more; this one proves the thing
the SDK actually does is the thing that gets caught. Negative control: removing
the clause fails both, with the raw `ValidationError`.

The second asserts the log carries `fixes.0.title` and the length `260` and
**not** the offending string, so the diagnostic cannot quietly become a channel
for model-authored prose.

### Addendum, 2026-08-29 — Epic 9.19 · The Working screens, brought up to level

A visual pass over screens and data that already existed: width, motion, and
empty states on the dashboard, clients and settings. **No new feature, no new
data, no backend work** — `git diff --name-only` over `apps/api` and
`apps/workers` for this commit is empty.

#### THE ONE THING THAT DID NOT CHANGE, EVIDENCED RATHER THAN ASSERTED

The report's width and its exclusion from arrival motion were out of scope. The
rendered page is **byte-identical** before and after — same SHA-256 on a
full-page 2× screenshot of `/scans/{id}/report`,
`66459b061fd15edb…`, `docs/screenshots/epic-9-19/08-report-{before,after}.png`.
Six new assertions in `ReportView.test.tsx` keep it that way: the report never
draws `avp-ledger--unmeasured`, never carries `avp-badge__pulse` (with
`animate` ON, which is what production sends), never renders an `avp-empty`,
and never picks up `avp-shell__content--wide`.

#### PART A — the width mistake was in exactly one place, and it was checked

`design-direction.md` §0 has split every screen into Working and Presenting
since Epic 0 and **never said which screen was which**, so the answer was not
lookup-able and the brief was right to ask for each to be confirmed rather than
assumed.

| Screen | Before | Verdict |
|---|---|---|
| `/dashboard` | `wide` → `--avp-app-max` (90rem) | already correct |
| `/clients` | `wide` → 90rem | already correct |
| **`/settings`** | **no `wide`** → `52rem + 3rem` | **the mistake** |
| `/scans/{id}/report` | `--avp-report-width` | out of scope, untouched |

Settings is an operator's account and seat roster. It is never printed and never
handed to a prospect, so it was rendering at the measure a *document* is read
at. §0 now carries the table above, so the next reader can look it up.

**Widening is not free, and this paid for it.** At 90rem the seat roster's
Remove button and the clients list's status badge were both stranded in the
middle of a very wide table, and the invite form's email field ran nearly the
full viewport for an address that is never that long. Alignment (`align: 'end'`,
which `DataTable` already owned and had simply never been asked for) and
`--avp-form-width` fixed those in the same pass, and the four one-line agency
facts became a 1→2→4 column grid instead of a tall thin stack.

#### PART B — motion, and ZERO new motion values

The exclusion from arrival motion holds: a dashboard checked fifty times a day
should not perform an entrance. But no arrival motion is not the same as no
motion, and this screen polls itself every 5s — it should not look frozen while
a scan finishes underneath the reader.

Everything added is spelled in the four durations `design-direction.md` §4
already defines:

| Where | Token | Measured live |
|---|---|---|
| `.avp-table tbody td` hover | `120ms` hover | `0.12s`; at t=30ms mid-transition at alpha `0.615`, settled by 330ms |
| `.avp-badge` tone | `200ms` state | `0.2s`; beacon→success crossfades through 5 intermediate oklab values |
| `.avp-meter__track` opacity | `200ms` state | `0.55 → 0.81 → 0.928 → 0.984 → 1.0` |
| `.avp-meter__lit` width/fill | `320ms` layout | 41→68: `70 → 89.2 → 102.8 → 110.4 → 114.3 → 115.5px` |
| `.avp-badge__pulse` | `calc(reveal × 3)` = **1.8s** | `avp-live-breath`, infinite; opacity sweeps `1.0 → 0.35 → 0.97` |

The pulse is the only loop in the system and it deliberately is **not** a fifth
number: `calc(var(--avp-duration-reveal) * 3)` is §4's own dim-to-lit dissolve
slowed to a breath, and it stays tied to the reveal if that value is retuned.

**The asymmetry in `.avp-meter__lit` is the interesting one.** A score that
*moves* eases to its new length; a score that *arrives* does not. That is not an
oversight — the lit element does not exist while a scan is unscored (Epic 9.9
decided a zero-width fill would read as a score of zero, and `render.test.tsx`
asserts it), and a CSS transition never runs on an element's first paint. So
re-running a client and watching 41 become 48 is animated, and loading the
dashboard for the fiftieth time today is not. What *does* ease on arrival is the
track's opacity and the numeral's colour, because React keeps both elements
across the switch out of "measuring".

**Reduced motion, toggled at the browser and re-measured**, because a keyframe
loop is the first thing in this product that could get it wrong: every
transition collapses to `1e-05s`, row hover is already settled at t=30ms
(instant, not fast), and the pulse reports `animation-iteration-count: 1` with
**nine consecutive opacity samples of `1.0`**. The keyframe ends at opacity 1
precisely so the global block's "collapse to one 0.01ms iteration" lands the dot
lit rather than freezing it at 35%, where it would read as disabled.
`tokens.test.ts` asserts that `100% { opacity: 1; }` at the stylesheet level.

#### PART C — the empty states, drawn from the product's own data

Four hand-written variants of "there is nothing here yet", three of them a bare
`<span>` inside a table cell. `EmptyState` is now the third member of the set
`LoadingState` and `ErrorState` opened in Epic 9.11.

**It is dashed, and that is a reference rather than a style choice.** A dashed
hairline already means one specific thing here: an absence that is itself the
finding. `.avp-shelf__notch` draws the rank nobody is standing in,
`.avp-ledger__gap-zone` outlines the points not earned, `.avp-ledger--empty`
frames a scan with nothing to score. An empty screen is that fact at page scale.

**The figure on a brand-new agency's dashboard is a real Luminance Ledger.** The
five real dimensions at their real §6 weights, every sub-score zero — the column
draws as an unlit void with the weights named beside it. Not decoration bolted
on beside the copy, which ip-safety.md #4 prohibits and which the house style
(the Ledger and the Answer Shelf are both *data drawn as illustration*) rules
out anyway. It is the palette's organising idea — visibility is luminance —
applied to the one screen where nothing is lit yet.

**`unmeasured` is what makes that honest, and it is the whole reason the prop
exists.** Sub-scores of zero already draw the right picture. They would also
have told a screen reader *"AI Visibility Score for your first client: 0 out of
100"* and tabulated five measured zeroes — a measurement nobody took, in a
product whose entire argument is that it does not assert figures it does not
have. The prop suppresses the composite claim, the gap annotation and the
per-dimension values, and names the chart as the structure of a scan rather than
the result of one. It defaults to **false**, the same guardrail
`staggerDimensions` uses.

**A bug only a browser could find, again.** The first version rendered the
figure into a full-width grid column. A chart drawn from a viewBox scales its
*type* with its box, so the Ledger's 13px dimension labels came out at ~30px —
larger than the page's own headline — and no test could see it: jsdom computes
no layout and a static render has no box. The figure is now capped at `22rem`,
near the chart's natural 344 units.

Clients also gained the classification split in its header, in the dashboard
header's own `Stat` treatment. **Nothing new is fetched or computed** — every
figure counts the `classificationStatus` already rendered as a badge two hundred
pixels below. `pending` is folded in with `ambiguous` deliberately: both are
rows an operator may still have to look at, and splitting them would put a
fourth figure on screen that is usually zero.

Settings' "not built yet" list became a numbered ledger in the report fix
list's idiom — leading-zero mono ordinal, hairline between rows. Three
paragraphs of tertiary prose read as small print left over; the same three
numbered read as a list somebody keeps.

#### Verified live

Both halves. The **real signed-in app** for the states a real account reaches —
a brand-new agency (`dev@test.com` / Dev Visual Pass, created through the
product's own sign-up) lands on the empty dashboard, the empty clients list and
settings. A **temporary uncommitted harness route** rendering the same view
components against the repo's own committed fixtures for the states the dev
database does not contain — a twelve-client dashboard, a running scan beside a
finished one, and the report. Both at 1440×900, `deviceScaleFactor: 2`, full
page, **zero JS errors at every step**. Twenty-three screenshots in
`docs/screenshots/epic-9-19/`, before and after for every screen including the
one that did not change.

The harness was deleted before this commit; `next build` and
`git status` confirm it.

#### Tests

**1542, up from 1496** — design-system 149 → **161**, web 459 → **493**. api
822, workers 13 and shared-types 53 all unchanged, and no Python file is in this
diff. `ruff check src tests` clean.

The assertions worth naming are the ones that could not have been written
before. `tokens.test.ts` reads `components.css` and fails if any of the five
motion rules is spelled as a hand-typed millisecond value instead of a token —
the exact failure the brief warned against ("extend the language, don't invent a
second one"), and one invisible to every other kind of test here, since jsdom
applies no stylesheet and a static render has no computed style. It also asserts
the pulse keyframe's final frame, because that frame *is* the reduced-motion
behaviour.

One existing test changed rather than being loosened. `ClientsView.test.tsx`
asserted the copy `"Use Compare to scan the first one"` — a sentence that named
a destination it could not reach. The empty state now carries the control
itself, so the assertion became "offers the way out rather than naming it in
prose", plus a check that an empty list draws no header figures, because three
zeroes is furniture rather than a summary.

### Addendum, 2026-08-31 — Epic 9.20 · Every client gets a space of its own

Until this epic a client row went nowhere. `ClientsView` said so in its own
comment — *"a LIST, not a management screen"* — and it rendered **zero links**;
the rows were inert text. That was the real gap: not that depth had been cut,
but that there was no room with a door on it for depth to live in. This builds
the room.

**Routing, stated explicitly because nested navigation scoped to one record is
genuinely new here** — the only nested dynamic route in the whole app was
`/scans/[scanId]/report`, a single segment:

```
/clients/{id}            Overview  — the front door, where a row lands
/clients/{id}/sources    Sources   — citations per domain over time
/clients/{id}/rankings   Rankings  — share of voice over time
    ↗ Report             a LINK OUT to /scans/{latest}/report
```

`Overview` exists because a nav has to have a current item and a space has to
have a front door. `Report` is deliberately a link out: the report is a
Presenting-context document at `--avp-report-width`, and rendering it inside a
wide Working shell under a nav strip would change the artefact. A fourth item
(Prompts) is expected here later; nothing in the frame assumes three.

The agency sidebar is untouched. It is agency-wide — every item in it is about
the whole account across every client at once — and depth about one client
cannot go there without either changing what those items mean or inventing a
global "selected client" this product does not have. `LocalNav` is the second
level of the same tree, and it is not a tab widget: these are pages with real
URLs, not panels behind a `role="tablist"` that would lie to a screen reader
about what just happened.

#### THE VERIFY-FIRST ITEM THAT CHANGED THE DESIGN

The brief said every number this needs is "already computed and stored on every
scan". **That is half right, and the half that is wrong decided the
architecture.**

- **Sources** reads `engine_result_citations` — persisted rows. The count per
  domain is an aggregation over them.
- **Rankings** has **no stored column at all.** `CompetitorComparison` is
  derived on read *by design*, and `scoring_runner.score_scan` says why: *"a
  pure function of rows already persisted, so storing it would create a second
  copy that can fall out of step with the first."*

Nothing is missing — every input is stored — but there is nothing to `SELECT`.
So the choice was N calls to `GET /scans/{id}/report` versus one read endpoint.
Measured against the real three-scan `plausible.io` history rather than guessed:

| | Report path | `GET /clients/{id}/history` |
|---|---|---|
| Per scan | **17.6ms** warm (61ms cold) | **6.5ms** |
| Three scans | 96.4ms + 3 round trips | 52ms, one round trip |
| Cited domains | ranked and **truncated to 13** of ~300 citations | full tally, capped at 40 |

The truncation was decisive. A Sources trend built from the report's display
lists would silently have been a trend over *whatever survived a display cap*,
which moves between scans. The endpoint calls the same
`score_scan(persist=False)` the report calls, so a figure on Rankings cannot
disagree with the same figure on the report for the same scan. A test asserts
that three consecutive reads add no `Scan` and no `Score` row.

#### SHARE OF VOICE, NOT COMPOSITE — and the reason is not preference

**There is no per-competitor composite and there deliberately never has been.**
`CompetitorComparison`'s docstring: sentiment is classified toward the subject
only and technical foundation is the subject's own site, so 25% of the weight
has no rival input, and a rival "composite" over the other 75% would not be
comparable to the client's. Plotting the client's real composite against five
partial ones would be a chart whose lines mean different things.

Share of voice is also the only one of the three measured dimensions that is a
genuine **share**. `scoring.share_of_voice` is subject mentions over total brand
mentions; `compare_competitors` is that rival's appearances over the *identical*
total. Same formula, same denominator — so a rise for one really is a fall for
another. Verified on live rows: subject plus five rivals summed to **99.99** on
one scan and **100.00** on another.

Mention rate would be honest per line but does not compose — every brand in the
field can sit at 100% at once, so the chart would carry no information about the
contest between them, which is the entire point of Rankings.

#### THE RULE THE CHART IS BUILT AROUND: A NULL IS NOT A ZERO

A competitor set is re-detected per scan, so a rival can be present, absent,
then present again. Two wrong answers are easy to reach and **both are silent**:

1. Drop the series → the chart shows fewer rivals than the client has.
2. Fill the gap with zero → the line dives to the floor and back, asserting a
   collapse that was never measured.

So a missing reading is `null`, every series spans every point, and
`trendLayout.segments()` breaks the line into runs. A lone run draws as a dot;
the hidden data table prints **"not measured"**. Rankings also *names* the
rivals whose lines will have gaps, so the holes are explained rather than left
to be noticed.

Sources distinguishes further, because there both cases are real: a domain
absent from a scan whose list came back **under** the 40-row cap genuinely
measured zero citations and plots at zero; one absent from a **full** list is
unknown and plots as a gap.

#### `TrendChart` — the first time-series shape in this system

Confirmed first that neither existing chart could be it. `LuminanceLedger` is a
snapshot — its correctness condition is that total lit height *is* the
composite, a statement about one measurement, with no axis for time.
`AnswerShelf` is ordinal position within one scan. **Recharts is not a
dependency anywhere**, despite `apps/web/README.md` still mentioning it, so this
is hand-rolled SVG like every other chart here.

The palette is §1's, unchanged: client always `beacon-600` solid; competitors
from `seriesStyle('competitor', i)`, the same neutral slate family the Ledger's
ghost columns use, and **the visibility ramp is never touched** — a test asserts
no ramp stop appears in the output. `seriesStyle` returns a *fill* pattern name
because it was written for bars, so each maps to the dash carrying the same
intent; §1's own argument applies unchanged, since five neutral greys are one
grey in greyscale print and five dash patterns are five lines.

#### TWO BUGS ONLY A LIVE BROWSER COULD FIND

Both invisible to the 200 design-system tests, because jsdom computes no layout
and a static render has no text metrics:

1. **Every x-label read "29 Aug".** The three real `plausible.io` scans are
   01:37, 03:48 and 04:32 on one day, and a day-only axis said nothing about
   which came first. The label resolution now follows the data — clock when the
   history fits one UTC day, date otherwise.
2. **Five rival names stacked into an unreadable block**, and
   `analytics-alternatives.com` ran off the right edge. `spreadLabels()` pushes
   labels apart without reordering, and the gutter went 108 → 176 units with
   `truncateLabel` as a backstop that shortens only the *drawn* label — the data
   table keeps the full name.

#### Verified live, on non-fixture data

Signed in, opened the real Clients list, **clicked** the Plausible Analytics row
and then clicked through the local nav. Every URL below is where a click landed:

```
/clients                                    → row is a real link
/clients/clnt_01M15JAFPH3HKFHWV8C6A7T65J    Overview, 3 scans
             …/sources                      6 domains, 01:37 / 03:48 / 04:32
             …/rankings                     6 series, subject + 5 rivals
/scans/scan_01M15WAK9CQRFZYN6CBZ9EY9QM/report   ← Report, the existing document
Help Scout (1 scan) …/rankings              "One scan is not a trend yet"
```

Zero JS errors and zero failed requests at every step. Six screenshots in
`docs/screenshots/epic-9-20/`.

The `review@epic7.example` fixture account's password was reset through **the
product's own flow** to reach that agency's data — `reset-password/request`
logs the URL when no email provider is configured, which is the development path
`services/email.py` documents and the Epic 9.13 walkthrough used.

#### Tests

**1644, up from 1542** — api 822 → **835**, design-system 161 → **200**, web 493
→ **543**. workers 13 and shared-types 53 unchanged. `ruff check src tests`
clean.

The three cases the brief named are all covered: one scan, several, and a set
that changed between scans. The last is the one that separates a gap from a
zero, and it is asserted at three levels — `trendLayout` (the segments break),
`trends.ts` (the value is `null`, and explicitly `not.toContain(0)`), and the
rendered view (two `d="M"` commands, not one).

### Addendum, 2026-08-31 — Epic 9.21 · The sparse trend screens, and the width that was innocent

Epic 9.20's Sources and Rankings were reported as reading sparse — *"a lot of
space on the left and right… looks like an old newspaper."* Two causes were
named to investigate. **One was real and was the whole of it; the other was
measured, disproved, and the fix for it written and then reverted.**

#### MEASURED BEFORE ANYTHING WAS CHANGED

Plausible Analytics, 3 scans, 1440px viewport, 1200px content column:

| | authored | rendered |
|---|---|---|
| SVG | 720 × 340 | **1152 × 544** |
| scale | 1× | **1.6×** |
| axis tick label | 11px | **17.6px** |
| subject stroke | 2.5px | **4.0px** |
| page body / lead copy | — | 14px |

**The axis labels were rendering 26% larger than the page's own body copy.** A
tick label is `--avp-text-ui-2xs`, the smallest type in the system; it had
become the biggest thing on the screen. That is the "old newspaper" feel:
oversized type, thin content, wide leading.

`.avp-trend__svg` is `width: 100%` over a fixed viewBox, so the chart scales its
**type and strokes** with its container. This is the same failure Epic 9.19
found on the `EmptyState` ledger figure and fixed by bounding it — and I shipped
`TrendChart` the very next epic without the bound.

#### CAUSE A WAS NOT REAL, AND IT WAS DISPROVED TWICE

The brief's Cause A said the wide shell was leaving *"a narrow island of content
with a lot of empty margin either side."* The measurement says otherwise: the
chart filled **1152px of the 1200px column — 96%**. It was stretched edge to
edge, not islanded. The empty margin came from measure-capped prose (heading
323px, lead 571px, caption 530px) plus **282px of the chart's own right label
gutter**, itself inflated 1.6×.

The fix for it was written anyway — `max-w-chart` on both trend sections — and
then **reverted**, for two findings:

1. **It moved nothing.** Toggling it off in a live browser left the section at
   1152px either way, because every child already caps itself
   (`max-w-headline`, `max-w-measure`, and now the figure). Constraining the
   parent of children that all self-cap changes no pixel.
2. **It did not even compile.** `.max-w-chart` was absent from the served
   stylesheet — a class that existed only in the markup, under a test that
   would have passed on the markup alone. Exactly the hollow assertion this
   project's testing rule exists to prevent.

So the change was backed out along with the `--avp-chart-width` token and the
preset entry added for it. **One real cause, one real fix.**

#### THE FIX IS DERIVED, NOT DECLARED

`style={{ maxWidth: layout.width }}` on the figure — one viewBox unit is at most
one CSS pixel, so the chart can never render larger than it was drawn.

Taken from the layout rather than written as a token because the two have to be
the **same number**. A caller passing `layoutOptions.width` against a fixed CSS
cap would be squeezed by it instead: scale below 1, type *smaller* than drawn —
the same bug in the other direction. A constant would need a test to stop it
drifting; this cannot drift, and the tests assert the invariant rather than the
number (`width: 1040` ⇒ `max-width: 1040px`).

It is a `max`, so the chart still scales down. Measured across viewports:

```
viewport 1728 1440 1280 1024  820 | 600  420
svg       720  720  720  720  720 | 552  372
scale     1.0  1.0  1.0  1.0  1.0 | 0.77 0.52   overflow: never
```

1:1 at every desktop width, shrinking only when the column is genuinely narrower
than the chart.

#### THE OTHER WORKING SCREENS WERE CHECKED, NOT ASSUMED

The brief put Dashboard, Clients and Settings out of scope unless the same cause
genuinely reached them. It does not: none of the three renders a `TrendChart` or
a `LuminanceLedger` at all, so there is no viewBox on any of them to scale.
Their tables are `1152 / 1152 / 1110`px in a 1200px column and are unaffected.

#### Tests

**1649, up from 1644** — design-system 200 → **205**. api 835, web 543, workers
13 and shared-types 53 unchanged. `ruff check src tests` clean.

The five new ones assert the invariant behaviourally: the cap tracks a custom
layout width and a custom height, the rule is a `max` and not a `width`, and the
SVG keeps `width: 100%` so the scale-down direction still works. Before/after
screenshots at the identical 1440px viewport in `docs/screenshots/epic-9-21/`.

**The rule worth keeping:** a screen looking sparse is not evidence that its
container is too wide. Measure the type before touching the width.

---

# Epic 9.24 — two palettes, because §0 always described two contexts

**2026-09-01.** design-direction.md §0 has split every screen into *Presenting*
and *Working* since Epic 0. The Report's restraint is argued there and is
untouched by this epic. What was never argued is why every **Working** screen
shared it, and for nine epics they did: one teal accent on a warm-grey ground,
from the dashboard to a client's Rankings. That was caution applied past the
point the split asks for. A report gets printed, photocopied and read across a
conference table; a dashboard is an operator's own console. None of the
Report's three reasons has ever applied to it.

## Part A — the `bench-*` layer

Six categorical accents, hues 258 / 275 / 292 / 309 / 326 / 343, chroma 0.185
against `beacon-600`'s 0.125. Additive: no existing token changed and the
Report's token set gained nothing.

**The palette database was used, and its recommendation was declined.**
`ui-ux-pro-max` returned 192 palettes; converted to OKLCH and bucketed, 446
chromatic entries survive. Its top pick for "dense analytics dashboard" was
`#1E40AF` / `#3B82F6` / `#DBEAFE` — the exact cool blue-grey §1 rotated the
neutral axis away from — so no hex was imported. What was taken is where its
chromatic mass sits **and** where sRGB still has chroma: hues 235–255 are
chroma-starved (max C 0.12–0.14 at L 0.55), which is why the arc starts at 258
rather than at the blue the database kept offering. Its *style* recommendation
("Data-Dense Dashboard") was taken, and is Part B.

Three claims, each a number a test checks: **85°** of hue against `beacon`'s 0,
**1.48×** the chroma, and **≥30°** from every ramp stop, `beacon` and all four
semantics — so a chip cannot be read as a score or as a system state. Every
light/dark pair clears 4.5:1; dark stops are gamut-clamped per hue because blue
cannot be both light and saturated in sRGB.

## Part B — applied, and what the browser found that the tests could not

Sidebar and section iconography in colour, six accented stat tiles on the
dashboard, four on a client, four on Technical, and Working-screen charts
drawing competitors from the layer via `seriesStyle(role, index, palette)` —
one component, two contexts, no second chart.

Three things only the live pass caught:

1. **The client's figures were in the wrong slot.** Put in `LocalNav`'s `meta`,
   a tile row collapsed to one narrow column, stretched the header to its
   height and left the space beside the title emptier than before — the exact
   complaint this epic set out to close. `ClientSpace` gained a `figures` slot
   that gives them their own full-width row.
2. **Two off-scale Tailwind utilities.** The preset REPLACES the spacing scale,
   so `h-2.5` and `w-40` compile to nothing. A new legend swatch rendered at
   zero size; `w-40` on Technical's VerdictBar had been dead **since Epic 9.22**
   — through a review and a screenshot pass. `reportIsolation.test.ts` now greps
   every spacing utility against the preset's real scale.
3. **A score wrapped in a categorical hue.** `Median visibility`, `Latest`,
   `Best` and `Technical foundation` were accented like their neighbours. They
   are measurements, and this product already has a colour language for a
   measurement. All four are unaccented now; the counts beside them are not.

Density was added the way Epic 9.21 said to: by giving the space something to
hold. `TrendChart`'s 9.21 width bound is untouched — a value table now fills the
column that bound leaves over, keyed by swatch to the lines.

## Part C — Prompts

`POST/GET /clients/{clientId}/prompt-runs`, four new tables, and a fourth item
in a client's LocalNav. One question an operator typed, run against the same
`ask_all` and `extract_facts` a scan uses — not a second extraction pass, which
could have answered differently from the scan whose score the operator is trying
to explain. No `Scan` row and no score: a run is a question, not a measurement.

**Throttle: 30 runs per client per hour**, sized against a scan rather than
picked. A run is 3 engine calls, a scan is 72, so 30/hour is 90 calls — **1.25
scans**. The worst an unattended loop can spend in an hour is a little over one
scan, which is spend a single Re-run click already incurs. Lower obstructs real
use (iterating on phrasing is 5–10 runs in minutes); higher makes the ad-hoc
path its own cost line, which is a pricing decision. Per client, not per
agency, so one busy client cannot exhaust everyone else's allowance. Counted
from the rows — exact, and a Redis counter resets exactly when a runaway loop is
still running. The test asserts the *ratio*, so raising the number forces the
argument to be made again.

## Verification

**The Report is unchanged, and this is the evidence.** The document element
(`article.avp-report`) and the whole of `/share/{token}` were captured before
and after: **byte-identical**, same SHA-256, 2,833,684 and 2,957,105 bytes. The
full `/scans/{id}/report` page does differ, and legitimately — the operator
shell around it is what was recoloured, and `ReportView`'s own note has always
said that chrome sits above the document rather than inside it.
`reportIsolation.test.ts` enforces this going forward, and fails in both
directions: if a report surface ever names a bench token, and if the Working
screens ever stop using one.

**`verify_chart_scale.py`: 25 offenders before, 25 after.** The style guide went
from 6 charts to 7 and the new Working-palette `TrendChart` added **zero**. All
25 are `avp-ledger__svg` and `avp-shelf__svg`, unbounded since before this epic.
They are **deliberately not fixed here**: the Ledger and the Shelf are the
*Report's* charts, and bounding them would be a visual change to the Report,
which this brief puts out of scope. Recorded as an open finding.

**Prompts was verified against the live engines**, not stubs: one real run,
3/3 engines named the client at position 1, real competitor extraction and real
citations from the grounded engine. Screenshotted.

Screenshots — every Working screen before and after, plus the two Report
captures — in `docs/screenshots/epic-9-24/`.

**Suite: 1,842 tests, up from 1,675.** design-system 334 (+113), web 586 (+23), api 869 (+31),
shared-types 53.

---

# Epic A — Sentiment, and a premise that had to be corrected first

**2026-09-02.** First epic of the analysis roadmap.

## The brief's premise was wrong, and checking it changed the epic

The brief opened: *"Every scan and prompt-run response is already stored in
full. Add a sentiment pass … run against that existing text — no new data
collection."*

**No response text is stored anywhere in this codebase.** ip-safety.md #7
forbids it, `test_ip_safety.py` asserts no text-bearing column exists on any
facts-only model, and the only two `Text` columns in the schema hold prompts
*we* wrote (`prompts.text`, `prompt_runs.prompt_text`). An engine's answer lives
inside the request that produced it and is discarded with it. Building the epic
as written would have meant adding the one column this project is most
deliberately built to refuse.

Two things were true instead, and both were checked before anything was built:

1. **The sentiment pass already exists.** It has run since Epic 4, in-request
   against the transient text, persisting a LABEL and a confidence on
   `EngineResult`. 541 classified rows in `avp_dev` — 369 positive, 126
   neutral, 46 negative, 113 null.
2. **It was missing from prompt-runs.** Epic 9.24 built the ad-hoc path through
   the same `ask_all` and `extract_facts` a scan uses, precisely so the two
   could not disagree, and then stopped one step short of tone.

So the epic became: close that gap, serve the labels, and build the tab. The
architecture the brief asked for is the architecture that was already here.

**This shapes the rest of the roadmap.** Epics C (contradictory *claims*), D
(fact-drift against stored responses) and J (re-examining stored responses) all
assume retained answer text. None of them can read text after the fact. Each
has to derive its signal *in-request*, while the answer is transient, and
persist only what it concluded — the way sentiment already does. Worth settling
before Epic C is specced.

## What shipped

**Backend.** `sentiment` + `sentiment_confidence` on `PromptRunResult`
(migration `2b8e8aa41f32`, nullable, no backfill possible or wanted).
`HistoryScanOut.sentiment` — per engine, per scan, four exhaustive buckets,
grouped in SQL rather than by loading 72 rows per scan.

**The fourth bucket is the epic.** `unclassified` is answers where the client
was never named, so tone was never asked. It is not a neutral, and it is kept
apart everywhere: the layout gives it `{ count }` and no geometry so it cannot
be drawn, the chart's hidden table lists it under its own header, the screen
counts it in its own tile, and the copy names it. Folding it into `neutral`
would report a brand nobody mentioned as having been described indifferently.

**`SentimentTide`** — a diverging chart, positive above a waterline, negative
below and hatched, neutral straddling. Tone is carried by POSITION so the
ordinal reading survives greyscale and CVD; hue is therefore free to encode the
ENGINE, and takes the `bench-*` index the Prompts screen already uses, shared
from `lib/client/engines.ts` rather than copied. `success`/`danger` were
considered and rejected — §1 reserves the semantics for system state exactly so
a red chip is never read as a bad score, and sentiment IS a scored dimension.

## Three things the browser caught that the tests could not

1. **The axis labels were clipped.** "POSITIVE" and "NEGATIVE" down the left
   gutter rendered as "SITIVE" and "ATIVE" — an 8-character caps label does not
   fit a chart's left margin. They were also redundant with the lead paragraph,
   which says which way is up in a sentence. Replaced by a single `0` on the
   waterline, which is the conventional mark and needs no gutter.
2. **A centred waterline wasted 45% of the figure.** Tone on the real
   `plausible.io` history is overwhelmingly positive, so the lower half held one
   hatched sliver. The halves are now sized by the largest stack in each
   direction — one unit scale preserved, dead space gone, mirror-image property
   intact.
3. **The suite started making live model calls.** The moment `run_prompt`
   classified tone, every `test_prompt_runs.py` test that stubbed only the
   engines began reaching Anthropic for real: billable, and flaky enough that
   one test passed alone and failed in the full run. Sentiment is now stubbed in
   the engine fixture itself, so no test in that file can make a live call by
   forgetting to. **That file went from 118s to 1.6s.**

## Animation

`find-animation-opportunities` was run on the finished screen, per the
roadmap's standing instruction. Two suggestions survived the gate and both were
applied: the bar rise moved from `--avp-duration-reveal` (600ms, the *report's*
tier) to `--avp-duration-layout` (320ms), because §4's Epic 9.16 note excludes
Working screens from performing on every load; and a 120ms hover
de-emphasis was added so one bar of eight can be isolated. Four candidates were
rejected and are listed in the skill's output — staggered ledger rows and
revealing stat tiles among them, both on the same Working-screen rule.

**`review-animations` could not be run**: it is marked
`disable-model-invocation` and is reserved for explicit user invocation. Flagged
rather than worked around.

## Verification

**The Report is unchanged.** `article.avp-report` and the whole of
`/share/{token}` captured before and after: byte-identical, 2,833,684 and
2,957,105 bytes — **the same SHA-256 as Epic 9.24**, so the document has not
moved across two epics. `reportIsolation.test.ts` continues to pass in both
directions.

**Suite: 1,909 tests, up from 1,842.** design-system 371 (+37), web 605 (+19),
api 880 (+10), shared-types 53. Screenshots in
`docs/screenshots/epic-a-sentiment/`.

---

# Epic B — Answer gaps, and two premises that did not survive contact

**2026-09-02.** Second epic of the analysis roadmap.

## The sequencing question was settled before anything was built

The brief flagged Epic J's resequencing for confirmation rather than deciding
it. Confirmed: **A → B → J → C → D → E → F → G → H → I → K**, with one shared
in-request judgement call that each epic extends rather than one model call per
epic.

The argument that made it concrete is in `extraction.py:304`. `classify_sentiment`
is a single structured `messages.parse` per mentioned answer. C, D and J all
want to judge the same transient answer in the same window, so they either
extend that one call or add three more. A scan is 72 engine calls; four
judgement calls per mentioned answer instead of one is roughly 4× the
classification spend and latency on every scan and every prompt-run. Epic B
itself is unaffected either way — it touches the extraction pass zero times —
which is why B went first regardless.

## Two premises in the brief, both wrong, both checked before code

Epic A's lesson was to check the brief's stated premise first. It paid twice.

### 1. "Tracked prompts", recurring between scans

There are none. A `Prompt` belongs to a `PromptSet`, a `PromptSet` belongs to
ONE scan, and `scan_runner.build_prompt_set` calls `prompts.generate_prompts`
fresh every time — an LLM writes a new set for every scan.

Measured on `avp_dev`, not assumed:

```
client                 scans  prompts  distinct texts  repeats
Plausible Analytics        3       72              71        1
Notion                     2       48              48        0
```

**One repeated string across 120 prompts.** So "sortable by how often the gap
recurs" cannot key on a prompt — there is no prompt identity to recur on.

What does recur is the **engine**: every prompt is asked of every engine, so a
gap holding on 3 of 3 is categorically stronger than one on 1 of 3. That is
`absentOn`, and it is the default sort. Across scans the stable axis is the
**rival**, because competitors persist as rows where prompts do not — that is
the `rivals` rollup, and it answers "which rival keeps taking answers from us"
where "which prompt keeps failing" is unanswerable.

### 2. "Where the client has relevant content (per Sources) but no citation"

There is no content inventory anywhere in this schema. `TechnicalAudit` stores
`pages_crawled` as a COUNT and one `url_audited`, not a page list. "Has relevant
content" is not readable.

What IS readable: whether the engines ever cited this client's own domain in
this scan. If they did, the domain is demonstrably citable and a prompt that
named the client without citing it is a real gap. If they never did, the claim
is unsupported — so `subjectCitable` is false and **no row is reported as
`uncited` at all**. The real Notion scan is exactly that case and reports 0
rather than 22.

## The distinction the whole epic turns on

A prompt where NO engine named ANY brand is **not** a gap. The client is as
unnamed there as in a row a rival won, but nobody won it — it is a question with
no commercial answer, not a question being lost.

On the real rows this is not a rounding error:

```
PSM Digital, latest scan:  9 absent · 0 partial · 4 uncited · 1 covered · 10 no-brands
```

Folding `no_brands` into `absent` would report **19 of 24 prompts as gaps**
instead of 9 — more than double. `layoutGapGrid` derives `isGap` from the row
KIND and never from `absentOn`, because the two states have identical
`absentOn`, which is exactly how this regression would pass unnoticed.

Epic A drew the same line around `unclassified` tone. That is now twice, and it
is starting to look like the house rule: **a state where the measurement was
never taken is never the same as a measurement that came back zero.**

## What shipped

**Backend.** `services/answer_gaps.py` and `schemas/answer_gaps.py`, behind
`GET /clients/{clientId}/answer-gaps?scanId=`. Reads only — no engine call, no
model call, no write. Four grouped reads assembled in Python rather than one
join: a scan carrying 72 results, 1,355 mentions and 2,399 citations would
return the product, not the grid.

**A claim in my own docstring was wrong and is corrected in place.** The first
draft said `extract_facts` "records every brand it finds". It does not — its
loop is `for comp_name, comp_domain in competitors`, so it SEARCHES for the
brands it is handed and discovers none. A rival outside the scan's
`CompetitorSet` leaves no `BrandMention` row and cannot be a column however
often an engine named it. The grid is bounded by competitor detection exactly as
Rankings is, the module says so, and the screen says so in words rather than
presenting a short grid as a complete one. Four tests failed on this before it
was found.

**`GapGrid`** — a real `<table>`, not an SVG. The data has two categorical axes
and a small integer; a chart would have to invent a continuous dimension, and an
SVG heatmap would mean drawing a table then hiding a second copy for screen
readers. Subject in `beacon`, rivals neutral, `absent` in `warn` not `danger`.
Every cell prints its count, so intensity is a second reading and never the
only one.

**The seventh bench accent.** A seventh client section would have wrapped
`benchAccent` back to `cobalt` and put two items in one nav strip in one colour.

## The palette has a wall, and this epic found where it is

Deriving where a seventh accent could go turned up the constraint that ends the
layer. Two hard bounds:

1. **30° from every meaning-bearing hue** — the buffer that stops a chip reading
   as a score or a system state.
2. **The sRGB gamut at the shared chroma table** — `600` needs C 0.185 at
   L 0.55, `700` needs C 0.158 at L 0.46.

Solving both across the wheel leaves **one arc: 256.5° to 355°, 98.5° wide.**
Blue is the binding half — at hue 241 the ceiling is **0.128** against the
required 0.185, so an accent there renders clamped and duller than its
neighbours, which reads as rank. My first candidate was 241 and the gamut check
killed it.

`crimson` at **355** is the arc's last seat, 30° from `danger` exactly. The
method was verified before use by reproducing all six shipped dark stops from
0.92 × the gamut ceiling; contrast is 9.66:1 light and 6.14:1 dark.

**Alerts, Crawler activity and Prompt discovery will hit this wall.** Ten
accents in 98.5° is ~11° apart, below what hue alone separates at fixed
lightness and chroma. The choice then is relax the buffer, let chroma vary, or
group the nav — a design decision, not a token edit, and deliberately not
pre-empted here. `tokens.test.ts` holds the arithmetic, including a gamut check
that fails if any accent's `600` or `700` leaves sRGB.

**The pressure showed up on the first screen to use seven at once.** Four stat
tiles at crimson 355 and magenta 326 — 29° apart at one lightness and chroma —
read as the same pink, and on a client where both figures were `0` they were
indistinguishable. Fixed by unaccenting the tile that is not a finding, which
matches how its rows are already drawn.

## Three things the live pass caught that the tests could not

1. **A control that vanished under the pointer.** Switching scans reset the body
   to a loading state, unmounting the scan picker that had just been used; it
   reappeared elsewhere once the fetch returned. The grid now dims to 0.45 with
   `aria-busy` and holds its controls. Only visible at all once the fetch was
   held artificially — locally it returns faster than the eye.
2. **The stagger broke the rule its own comment cited.** Cells arrived 28ms
   apart, capped at 12 rows: **656ms**, on a screen an operator reopens all day.
   `find-animation-opportunities` killed it three ways — 656ms is performing on
   every load, a cascade depicts progressive arrival that did not happen, and
   `28ms` was hand-typed when the only stagger token is reveal-tier and belongs
   to the Report. Now one 320ms fill, together. `tokens.test.ts` asserts the
   rule carries no `animation-delay`.
3. **Component-scoped custom properties need declared defaults.** `--avp-gapgrid-*`
   failed the "every `var()` is defined" guard until declared on `.avp-gapgrid`
   the way `.avp-tile`'s are, and `--avp-semantic-warn` did not exist — the
   token is `--avp-warn`.

## Animation

Both runnable animation skills were run on the finished screen, per the
roadmap's standing instruction, and **both found real defects.**

`find-animation-opportunities`: one suggestion survived (the pending-swap dim,
built); five were rejected, including the row-reorder-on-sort FLIP — 24 rows of
dense data the operator is reading, where the sort exists to put the worst on
top and the eye goes there anyway. Two of the rejections were my own
already-written code, the 656ms stagger among them.

`improve-animations` then caught three more, all mine:

1. **Row hover was an instant repaint** — the exact defect Epic 9.19 named and
   fixed for `.avp-table`, whose comment reads "the default-browser feel this
   product avoids everywhere else". The cell's own transition did not cover it:
   a row paints on the `<tr>`, underneath the cells' fills. Now eased over
   `--avp-duration-hover`.
2. **The cell transitioned at hover-tier for a data change.** Nothing about
   hover moves a cell's background — hover draws an outline, deliberately, so
   the fill encoding the value never shifts under the reader. The only repaint
   is a scan swap, which is a state change; it now takes
   `--avp-duration-state`, alongside the container's dim.
3. **The scroll container could not take focus.** Not motion, but a defect the
   audit surfaced and one I had introduced: a horizontally scrollable region
   that cannot be focused cannot be scrolled by keyboard, so on a narrow
   viewport the rival columns were unreachable without a pointer. Now
   `tabIndex={0}` with `role="region"` and the caption as its name. Verified in
   a browser at 420px: focus the grid, press ArrowRight, `scrollLeft` goes
   **0 → 480**.

All three are asserted — the two token choices in `tokens.test.ts`'s
Working-screen motion test, the focusability in the screen's own suite.

**`review-animations` could not be run**: it is marked
`disable-model-invocation` and is reserved for explicit user invocation. Same
flag Epic A hit. My first draft of this entry claimed `improve-animations`
carried it too; it does not, and running it is what produced the three findings
above.

## Verification

**Against real data, not stubs.** `build_answer_gaps` was run over all seven
scanned clients in `avp_dev`; totals sum to the prompt count on every one. The
screen was then driven in a browser for three of them — PSM Digital
(9/0/4/1/10), Plausible (0/2/18/0) and Notion, whose citation tile correctly
reads **`—`** with "No answer in this scan cited this client's domain, so this
cannot be measured" rather than a fabricated 0.

**The Report is unchanged.** `article.avp-report` on `/share/{token}` captured
before and after: **byte-identical, 72,750 bytes, SHA-256 `cbb44e55…`**, with no
normalisation of any kind. Re-checked after the motion audit's second round of
`components.css` edits — same hash again.

The whole-page comparison needed one correction to be meaningful, and it is
worth recording. Two captures **at the same commit** produced different page
hashes: Next's dev server stamps a cache-busting `?v=` nonce and an RSC render
timestamp (`:N1788326808946.8438`), and the timestamp's varying length shifts
every following script-chunk boundary. With both normalised, before, a
same-commit control, and after are **identical at 76,248 bytes** — the control
is what proves the normalisation is not hiding a real difference. A raw
page-level hash is not a stable invariant check in dev mode; the article
element is.

**At 420px the page does not scroll sideways and the grid scrolls inside its own
container** — Epic 9.21's rule, verified rather than assumed.

**IP-safety check passed:** the response carries counts, booleans, ordinals,
entity names and OUR OWN prompt text (Epic 4 generated it) — no field can hold
an engine answer; `test_answer_gaps.py::TestIpSafety` asserts the response
echoes no phrase from a stubbed answer while confirming our prompt text IS
present; no new dependency was added, so constraint 6 is untouched; the new
screen imports only from `@avp/design-system`, no ad hoc Tailwind defaults
(constraint 2); the grid was derived from the data model and the user goal, and
resembles no competitor screen (constraint 1); `test_ip_safety.py` 84 passed.

**Suite: 1,998 tests, up from 1,909.** design-system 405 (+34), web 631 (+26),
api 896 (+16), shared-types 53, workers 13. `ruff` clean, `tsc` clean across all
three TypeScript packages. Screenshots — light, dark and 420px — in
`docs/screenshots/epic-b-answer-gaps/`.

**A dev-DB convenience, written down rather than done quietly:** the
`review@epic7.example` fixture account's password was reset to
`epic-b-answer-gaps-review` through the product's own `reset-password` flow,
which logs the URL when no email provider is configured — the same path Epic
9.20 used to reach that agency's data.

---

# Epic B.1 — the nav grew groups so the palette would not have to

**2026-09-02.** A decision taken on its own, deliberately before Epic E rather
than inside it.

## Why it was worth its own pass

Epic B filled the seventh and last seat in the `bench-*` layer, with Alerts,
Crawler activity and Prompt discovery still to come — three sections, zero
seats. Left to surface mid-epic, the pressure to ship Alerts would have
produced a shortcut: reuse a hue, loosen the buffer a few degrees, or eyeball a
"close enough" chroma. Any of those is a silent degradation of the property the
layer exists for, landing inside an unrelated diff where nobody is looking.

**Decision: group the nav.** Two alternatives were on the table and both cost
something real — shrinking the 30° meaning buffer lets a Working chip be
misread as a score or a system state, and letting chroma vary makes one accent
read as more important than another, which is the equal-weight property
`design-system.md` §6 states structurally. Grouping costs neither: a hue only
has to be told apart from the others in its own cluster.

## The finding that made the decision easy

**The product already did this and had not written it down.**
`WorkspaceShell.ACCENT` is `dashboard:0, compare:1, clients:2, settings:3` and
`ClientSpace.ACCENT` started at 0 too — so Dashboard and Overview have both
been cobalt, and Clients and Rankings both violet, **on screen together, since
Epic 9.24**. Every Epic B screenshot shows it. Nobody has read it as a
collision, because the two navs are different places doing different jobs.

A hue was already scoped to its nav rather than to the product. This scopes it
one level further, to its cluster — an extension of working practice, not a new
concept.

**The trade, stated rather than buried:** two items in the SAME strip can now
share a hue, separated by a group label rather than by sitting in a different
region of the screen. That is weaker separation than the sidebar/strip
precedent. It is also the mechanism — partitioning the arc between clusters
would keep every hue unique and buy no seats at all, which is the version of
this idea that does nothing.

## The taxonomy was corrected before it was built

The proposed split filed Sentiment, Crawler activity and Prompts under
`Analysis` on the strength of when they shipped. Three corrections, from the
data model rather than the changelog:

- **Sentiment is a measurement.** Its labels have been stored since Epic 4 and
  it is one of the five scored dimensions at 15% of the composite.
- **Crawler activity is a data source**, not an analysis of one. Filing it under
  Analysis would have quietly undercut the roadmap's most carefully-argued
  honesty constraint — that it answers "is AI touching our site", not "did it
  convert".
- **Prompts creates data.** `ClientSpace`'s own docstring: "The only destination
  here that CREATES data rather than reading a scan's."

Final: **Measurement** (Overview, Report, Sources, Rankings, Sentiment,
Technical, + Crawler activity) and **Investigation** (Answer gaps, Prompts, +
Alerts, Prompt discovery). 6 + 4 at the end state, so neither cluster ever
reaches the seven-seat ceiling. `clientNav.test.ts` asserts that projection
directly, which is the whole point of doing this before Epic E.

## What shipped

**`LocalNavGroup`** — a labelled cluster rendering a `<ul>` with an accessible
name, each item an `<li>`, so a screen reader reports "Measurement, list, 6
items" rather than ten undifferentiated links. Clusters are separated by
`--avp-space-6` against `--avp-space-1` between items: **a grouping that only
reads once you have read its labels is not doing the work.**

**The `<li>` is a plain flex item, never `display: contents`.** That would
remove its box and let the `<a>` be the flex child directly — tidier CSS and a
real hazard, since several browsers have shipped bugs where it strips `<li>`
from the accessibility tree, taking the semantics this grouping exists to
provide. Epic 9.16 refused the same property on `.avp-reveal-group`.

**`clientNav.ts`** — the nav as DATA, not JSX. Accents are still written out
rather than derived from position, because Epic 9.24's rule holds: they are
identities, and inserting Alerts above Prompts must not repaint Prompts. What
data buys is that `clientNav.test.ts` can assert no cluster repeats an accent,
none outgrows the layer, every section has exactly one home, and the three
sections still to come all fit.

## The refactor caught itself, in a browser, again

Moving Answer gaps from crimson (global 6) to its cluster's first seat made
`ACCENT` 0 — which collided with a hardcoded `accent={0}` on the next stat
tile. **"Rivals took" and "Partly held" both went blue**, and it reached a
screenshot before anything caught it.

That is the same failure as Epic B's crimson/magenta collision, from the same
cause: accent literals scattered through JSX have nowhere to be checked. Fixed
twice over — the tile accents are now derived from the screen's own accent
(`benchAccent` cycles, so offsets stay distinct wherever the nav table moves
this screen), and a test asserts the property rather than the hues.

**Two tests were rewritten rather than repointed.** `ClientAnswerGapsView` and
`ClientSentimentView` each asserted a hue literal — `bench-7-600`, `bench-6-600`
— which held only while accents were global. Both now assert against
`accentFor(section)`: what must be true is that a screen's figures and its nav
item agree, not that either wears a particular number. A test edited to a new
literal would have gone green and proved nothing, twice.

## Verification

**Measured across four viewports**, not assumed: `Measurement [1, —, 2, 3, 4, 5]`
(the dash is the unaccented Report) and `Investigation [1, 2]` at every width;
nav height 58px at 1440 and 164px at 420; **no sideways page scroll anywhere**.
Stat tile rails confirmed distinct in the browser: `258 / 275 / 292` plus the
unaccented hairline, 4 of 4.

**The Report is unchanged.** `article.avp-report` on `/share/{token}`:
**byte-identical, 72,750 bytes, SHA-256 `cbb44e55…`** — the same hash as before
Epic B, now across three separate rounds of `components.css` edits. The whole
page matches its own same-commit control at 76,248 bytes once the dev server's
nonce and RSC render timestamp are normalised.

**IP-safety check passed:** no new dependency (constraint 6 untouched); the new
component lives in `@avp/design-system` with no ad hoc Tailwind on a
customer-facing screen (constraint 2); the grouping was derived from this
product's own data model and nav structure, not from any competitor's
navigation (constraint 1); no scraped or model-returned content is involved
anywhere in this pass (constraint 7).

**Suite: 2,009 tests, up from 1,998.** web 642 (+11), design-system 405, api
896, shared-types 53, workers 13. Screenshots at 1440/820/420 and dark in
`docs/screenshots/epic-b1-nav-clusters/`.

**Open for Epic E:** Alerts adds a section to Investigation at accent 2, and
Crawler activity adds one to Measurement at accent 5. Both seats are free and
`clientNav.test.ts` already asserts they fit.

---

# Epic E — Alerts, and three rules that fired zero times as written

**2026-09-02.** Fifth epic of the analysis roadmap.

## HEAD was not where the brief said it was

The brief opened "confirm HEAD is at Epic B.1 and the tree is clean". It is at
Epic A — **B and B.1 were never committed**, and are 33 files in the working
tree. Flagged at the end of both epics and never actioned, so this is expected,
but the work below builds on the tree rather than on HEAD.

## Every alert kind needed correcting, and the data said so before any code

Epic A's brief assumed stored answer text; Epic B's assumed recurring prompts.
Both were wrong and both were caught by measuring first. Three for three now.

### 1. "Consecutive scans" is not a time series

Every alert kind compares two scans. The database holds **three consecutive
pairs in total**, and two of them are re-runs:

```
Plausible   11:32 → 13:43 → 14:27    gaps of 2h11m and 43m, one afternoon
Notion      Aug 29 → Aug 31          1d 21h — the only real interval
```

On those re-runs, with nothing whatsoever having happened, the composite moved
**−0.93 and +0.68**, net tone moved as much as **6 points**, and competitor
citation counts swung by **26**. An alert comparing a scan with whatever ran
before it reports engine nondeterminism as a business event — reliably, and
most often for the operator who re-runs a scan to check something.

So a baseline must be at least `MIN_BASELINE_HOURS` older, and a scan without
one produces nothing. The number is bounded on both sides and both bounds are
asserted: above the longest observed re-run gap (2h11m), below a daily cadence
(24h), because a guard raised past 24h would silently stop a daily-scanning
client ever producing an alert.

### 2. "Sentiment turning negative" fires zero times, ever

Across every engine of every scan on record, **the minimum net tone is +4**:

```
Notion  Aug 29:  chatgpt +13   claude +12   claude_search +10
Notion  Aug 31:  chatgpt  +6   claude  +5   claude_search  +4
```

A rule watching for a sign change would report nothing — including nothing about
that table, which is a 54–60% collapse across all three engines simultaneously
and the clearest tone event in the data. `SENTIMENT_DECLINE` measures the
decline; the sign change is kept as an additional trigger for when it eventually
happens.

### 3. "A rival taking a citation the client just lost" is impossible

Not rare — impossible. `classify_citation` returns `cites_subject` as
`domain == subject_domain`, a pure function of the domain string, so a source
the client owned in one scan is its own in every scan forever. Confirmed against
the data too: **no domain in any client's history carries two different
`cites_subject` values.** `OWNED_CITATION_LOST` measures the thing that can
happen — the client's own domain going from cited to uncited.

## What the corrected logic says about the real database

Run over all twelve scans before any UI existed:

```
12 scans · 1 had a usable baseline · 3 alerts
  Notion  sentiment_decline  claude         Net tone fell 58%, from +12 to +5.
  Notion  sentiment_decline  claude_search  Net tone fell 60%, from +10 to +4.
  Notion  sentiment_decline  chatgpt        Net tone fell 54%, from +13 to +6.
```

Both Plausible re-runs produced **nothing** — the guard excluded them, which is
the whole point. The one genuine interval produced three alerts, all of them
real.

**Stated plainly rather than buried: this feature is barely exercised by the
current data.** One client of eleven has ever had a comparable baseline. The
thresholds are set from two re-run pairs and one real interval — enough to show
the re-run problem is real, nowhere near a variance estimate. They are asserted
as RELATIONSHIPS in `test_alerts.py` rather than as literals, so revisiting them
against a client with a genuine history is a deliberate act rather than a silent
edit. `visibility_drop` in particular fires on nothing today: Notion's composite
fell 4.56% against a 5% threshold. That event was not missed — it produced three
tone alerts — but the near-miss is on the record rather than tuned away, because
tuning a threshold to fire on your only example is fitting to n=1.

## What shipped

**Backend.** `Alert` model + migration `31524aae82f6`, `services/alerts.py`,
and phase **7b** of the scan chain — after scoring, because a visibility alert
reads the STORED composite that phase 7 writes. Ordered before it, every
visibility alert would compare against `None` and produce nothing, with no error
and no log line, so the order is asserted rather than trusted.

`GET /clients/{id}/alerts` and `POST /alerts/{id}/acknowledge`. Acknowledgement
is idempotent and there is no un-acknowledge: dismissing is a record that a
person looked, and a log an operator can quietly un-read is not a log.

**Frontend.** The Alerts tab took the seat Epic B.1 reserved — Investigation
cluster, accent 2 — **without touching any hue above it**, which is the property
the cluster model was built for. `clientNav.test.ts` had already asserted it
would fit.

**`TrendChart` annotations, with no new extension point.** `trendLayout` already
exposed `columns`, the x position of every point, which is exactly what an axis
marker needs. The marker is a triangle under the axis: under, because a mark
among the lines reads as a data point; a triangle, because every other mark in
the chart is a dot or a line, so shape carries it without spending a colour.

## An empty feed is two findings, not one

Nine of eleven clients have a single scan and can never produce an alert.
Reporting "0 alerts" for them would read as an all-clear. The screen separates
*"Nothing has been checked, which is not the same as nothing being wrong"* from
*"This is an all-clear, not an absence of data"*, and carries
`scansCompared / scansTotal` as a tile. Same discipline as Epic B's
`subjectCitable`, and the third epic running where the honest answer was to
refuse a claim the data cannot support.

## The three-skill pass, which is now standard — and all three found something

**`emil-design-eng`** (run for the first time, as the brief required) found four:

1. **Acknowledging removed the row instantly** — it dropped out of the filtered
   list and the rows below jumped under the cursor. With three alerts on one
   scan that happens twice in a row. The row now stays put, visibly settled, and
   filters out on the next load. The fix was not to animate the exit but to not
   have one.
2. **`.avp-alert` and `.avp-alert.is-acknowledged` were class names on the
   component and rules nowhere** — they rendered fine and styled nothing, the
   silent no-op Epic 7.1's stylesheet guard exists for.
3. **The engine label was the least prominent text in the row** when it is the
   only thing distinguishing three otherwise identical "Tone declined" entries.
4. **A failed acknowledgement was swallowed** — the button re-enabled with
   nothing else changed, which reads as "the click did not register".

**`find-animation-opportunities`** produced one accept and, more usefully, a
rejection of the brief's own request. The brief asked for "alert entries
transitioning into the feed rather than popping in"; the entries do not arrive,
they **are** the page's content at first paint, so animating them is performing
on every load — which §4/Epic 9.16 excludes and which A and B both already
refused. It also surfaced two non-motion defects: the acknowledge button was
narrower than its own "Acknowledging…" label so it grew on click, and a dead
Tailwind class.

**`improve-animations`** found three more, all the classes prior audits hit: the
row's opacity transition ran at the state tier while also serving the frequent
hover trigger (hover tier now — Epic B's token-tier mismatch, mirrored);
`border-color` was in the transition list while the acknowledged state set the
value `.avp-card` already carries, so that half animated nothing; and a
reduced-motion override redundant with `base.css`'s global reset that, unlike
`GapGrid`'s, guarded no invisible-content risk.

**`review-animations` could not be run**: `disable-model-invocation`, reserved
for explicit user invocation. Third epic running, flagged rather than worked
around.

## `text-semantic-danger`, and a guard one axis over

The failure message was written with `text-semantic-danger`. **That utility does
not exist** — the preset flattens the semantics to top level, so it is
`text-danger` — and it compiled to nothing, leaving an error message rendering
in body ink. Visible, but not marked as an error, which is the one thing it had
to be. It is the exact failure Epic 9.24's off-scale spacing guard was written
for, on an axis that guard did not cover; `reportIsolation.test.ts` now checks
colour utilities too. The mistake is the natural one: the TypeScript export is
`semantic.danger` and the utility is `text-danger`.

## The bug only a browser could find

**Annotation markers: 0.** The feed reported `scannedAt` as `Scan.created_at`
while `client_history` reports `finished_at or created_at`. The two differ by
the scan's duration, so no annotation key ever matched a trend point and **every
marker silently failed to draw** — no error, no log line, and invisible to unit
tests whose fixtures had matching stamps by construction.

Found by counting `.avp-trend__annotation` nodes in a live browser. Fixed by
using the identical `COALESCE` expression, and guarded by a test that reads
BOTH endpoints and asserts every alert's stamp appears among the history's —
because the defect lives in the gap between them and neither alone can catch it.
**Negative-controlled**: reintroducing `created_at` fails that test, restoring
the coalesce passes it.

## Verification

**Driven in a browser against real `avp_dev` data**, not stubs. Notion's feed
shows its three real tone alerts; acknowledging one leaves `rows 3 → 3`, the
same rows in the same order, one settled at `opacity 0.6`, the button width
unchanged at 152px, and the tiles moving 3/0 → 2/1. PSM Digital shows the
never-compared empty state. Rankings draws 1 annotation marker and 1 `Notes`
row.

**The Report is unchanged.** `article.avp-report` on `/share/{token}`:
**byte-identical, 72,750 bytes, SHA-256 `cbb44e55…`** — the same hash as before
Epic B, now across four rounds of shared-stylesheet edits. The whole page
matches its own same-commit control at 76,248 bytes once the dev server's nonce
and RSC render timestamp are normalised.

**IP-safety check passed:** `Alert.detail` is OUR sentence assembled from OUR
numbers and there is no column on the table capable of holding an engine's
answer; `test_alerts.py::TestIpSafety` asserts the feed echoes no phrase from a
stubbed answer (constraint 7). No dependency added (6). The screen imports only
from `@avp/design-system`, and the one ad hoc utility it did carry was a dead
class now removed and guarded (2). The alert kinds were derived from this
product's own scoring dimensions and citation model, not from any competitor's
alerting feature (1, 5). `test_ip_safety.py` 84 passed.

**Suite: 2,062 tests, up from 2,009.** api 917 (+21), web 667 (+25),
design-system 412 (+7), shared-types 53, workers 13. `ruff` clean, `tsc` clean
across all three TypeScript packages. Migration applied and `alembic check`
reports no drift. Screenshots in `docs/screenshots/epic-e-alerts/`.

**Open for Epic F:** Crawler activity takes Measurement accent 5, the last free
seat in that cluster; `clientNav.test.ts` asserts it fits. Worth knowing before
it starts: crawler data is a second measurement stream with no baseline problem
of this kind, because server logs are continuous rather than sampled.

---

# Design review follow-up — the sparse-data states

**2026-09-03.** Not an epic. Four defects a visual inventory found by driving
the finished screens against a one-scan client, `PSM Digital` — the state nine
of eleven clients in `avp_dev` are actually in. Inventory and captures in
`docs/screenshots/design-review-2026-09-02/`; the after-captures are in
`docs/screenshots/design-review-fixes-2026-09-03/`.

**The core finding: nothing distinguished "not enough data yet" from "broken
UI", and a one-scan client hit that gap on both new screens.** The fix is not
decoration — a B2B data tool carrying stock imagery would read as a template.
It is four specific things.

## 1. A zero does not look like a finding

"PARTLY HELD 0" carried a full `bench-*` wash and a coloured rail, giving an
empty measure the same visual weight as "RIVALS TOOK 9" beside it. A count tile
now drops its accent when the count is zero.

`countAccent` lives in the SCREEN, not in `StatTile`. The primitive cannot know
what zero means for a given caller: on the Alerts feed "OUTSTANDING 0" is a
genuine all-clear worth showing plainly. It is the counterpart to
`StatTile.emphasis` — that adds weight to a loud number, this removes it from
an empty one.

**Two zeros, and only one dims.** "Named, not cited" shows an em dash when
`subjectCitable` is false, and that refusal to claim a number is itself the
finding, so it keeps its accent. A measured zero on the same tile dims like any
other. Filing "cannot be measured" alongside "measured, and it was zero" is the
exact conflation the rest of that screen exists to prevent.

## 2. The negative hatch now carries the engine's colour

A single shared `<pattern>` filled with `currentColor` rendered perfectly and
was wrong: a paint server resolves `currentColor` against the element that
DEFINES it — `<defs>` — which inherits nothing from the `<g>` that sets `color`
per engine. Every negative block came out the same `ink-800` grey while every
positive and neutral segment beside it was engine-coloured.

**The test suite could not see it.** It asserted a pattern was REFERENCED, never
what that reference resolved to, so it passed throughout. The new one reads the
resolved fill from two engines' patterns and asserts they differ; the old
assertion's hard-coded id is now resolved through the exported
`negativePatternId` for the same reason.

Verified live: Notion's tide emits three patterns with three distinct resolved
colours (hues 326, 292, 258) where all three were previously one grey.

## 3. A truncated column header keeps its full name

`Growthmarketingpro` rendered as `Growthmarketin…` with the full string
reachable only by scrolling to the rivals table. The header carries `title`,
extending the affordance `.avp-gapgrid__chip` already used.

## 4. Sentiment says when it is a first reading, not a trend

The one genuine UX gap rather than a bug. At one scan the tide is three wide
bars against a single x-tick, and the inventory's read was that it looks like
the chart is malfunctioning rather than correctly reporting that this is all the
data there is.

**`hasSentiment`'s argument stands and was not reverted:** one scan IS a
readable tide. So the chart's rendering is untouched — no forced narrower bars,
no fake second column. Disguising an n of 1 would be the dishonest version of
this fix. What was added is a sentence, under the existing lead:

> One scan so far, so this is a first reading rather than a trend. Tone will
> show as a direction once a second scan runs.

Distinct from `NoToneYet`, which handles zero scans or zero named answers —
those are an absence of data, this is data with no trend yet, and they need
different words.

## Deliberately NOT changed

**The Answer-gaps grid's 121 empty cells.** They are the actual finding for this
client — real prompts where nobody named any brand, which is Epic B's whole
premise. Padding or disguising that density would undo the epic. The only
changes on that screen are #1 and #3.

## A guard that had to be re-aimed

The Epic B.1 tile-collision test asserted "at least three accented tiles, all
distinct hues". With zero-valued tiles correctly dropping their accent,
`gapHeavy` renders two — which would have left the guard passing while
exercising less than it was written to. It now runs against a fixture whose
counts are all non-zero, so it still tests three.

## Verification

**Driven live against both clients**: PSM Digital (1 scan — the review's own
client) and Notion (2 scans, so the n>1 path and the note's ABSENCE are both
checked). Read out of the live CSSOM rather than the markup: `Partly held 0`
resolves to the neutral hairline rail while `Rivals took 9` keeps its bench
rail; `Growthmarketingpro` reports `scrollWidth > clientWidth` and carries its
full `title`; the first-reading note is present at one scan and absent at two.

**The Report is unchanged** — checked rather than assumed, though nothing here
touches a shared stylesheet: `article.avp-report` is byte-identical at 72,750
bytes, sha256 `cbb44e55`.

**Suite: 2,072, up from 2,062.** api 917, web 674 (+7), design-system 415 (+3),
shared-types 53, workers 13. `ruff` and `tsc` clean.

---

# Epic F — AI crawlers, and a data source that does not exist

**2026-09-07.** Sixth epic of the analysis roadmap, and the first built after a
sequencing decision that had to be made before anything else.

## The sequence was put back to the founder rather than assumed

The confirmed order after Epic A was **A → B → J → C → D → E → F → G → H → I →
K**, with J moved early so C and D could read its consistency signal before
trusting an answer. Epic E was then built directly at the founder's request,
jumping J, C and D.

That left a real choice, and it was asked rather than guessed: go back for
J/C/D, or continue into F. **The answer was F.** J, C and D remain open and
unbuilt, and this entry says so rather than letting them quietly disappear from
the roadmap.

One finding surfaced while framing that question, and it belongs on the record
because it changes what J costs. `engine_results` carries
`UniqueConstraint("prompt_id", "engine")` — **one row per prompt × engine**. J
is "ask the same prompt of the same engine repeatedly and compare", which that
constraint cannot store. So J is not the read-side extension Epic B's entry
implied when it argued for one shared in-request judgement call; it needs a
migration and it multiplies a scan's 72 engine calls by its repetition count,
forever, on every scan. `response_digest` exists ("change detection without
retention") but a digest says two answers differ, never how — so consistency
would have to be measured on the stored facts, not on text.

## The premise was not wrong in detail. It was wrong about the data existing

Epics A, B and E each found a brief's premise false and corrected it. This one
went further: **Crawler activity, as the roadmap defined it, has no data source
in this system at all.**

The roadmap's line — and B.1's reserved nav seat — described first-party server
logs showing AI bots hitting the client's site. There is no log drain, no CDN
integration, no collector endpoint, and no column anywhere capable of holding a
bot hit. The only `USER_AGENT` in the codebase is `crawl.py`'s, which is **this
product crawling the client**, not a crawler visiting them. Getting the real
thing means building a log-ingestion product, not a screen.

Put to the founder as a scope decision rather than resolved unilaterally, since
it changes what F *is*. **The choice was to build the honest, narrower
version**: what the site's `robots.txt` ASKS AI crawlers to do, read from the
file `technical_audit._fetch_side_files` already fetches on every scan.

**That is a weaker claim and the whole epic is built to keep it visible.** It
answers *can* an AI crawler read this site, never *is* one reading it. The
module docstring says so, the endpoint docstring says so, the screen's lead
paragraph says so, and `ClientCrawlerView.test.tsx` asserts the vocabulary —
"activity", "traffic", "visits", "is crawling" and five more phrases must not
appear in the rendered screen, in any state. A constraint like this erodes one
reasonable copy edit at a time, and a test holds it better than a comment.

The nav label changed with it: **AI crawlers**, not Crawler activity. B.1
reserved the seat under the old name, and a nav item promising activity is a
claim the screen behind it cannot honour.

## `PARTIAL` fired on seven of nine real domains, and was cut

The first draft had five verdicts. `PARTIAL` meant "root permitted, but some
paths disallowed", and it looked obviously useful.

Measured against the live `robots.txt` of nine real client domains in
`avp_dev`, it fired on **seven of them** — on Linear's `/api/` and `/cdn-cgi/`,
Basecamp's `/demos/` and `/humans.txt`, Ghost's six housekeeping rules. Almost
every site disallows something, and none of it says anything about AI
visibility.

This is Epic E's lesson inverted. There, three alert kinds fired zero times as
written. Here a verdict fired almost always, and **a signal that is nearly
always true carries as little information as one that never fires.** Making it
mean something would need to separate `/blog/` from `/wp-admin/`, and Epic B
already established there is no content inventory in this schema to do that
with. So path restriction survives as `disallowRules`, a COUNT that qualifies
an `allowed` verdict, and the verdict axis stays the question that can actually
be answered.

After the cut, the same nine domains read: seven all-allowed, one all-
unspecified pair, and Notion with a single explicit block. That is a signal.

## Silence is an answer here, which is NOT the house rule being broken

Epics A, B and E each ended by refusing a claim the data could not support, and
the rule they converged on is that a measurement never taken is never the same
as one that came back empty. This epic looks like it breaks that rule and does
not, so the distinction is worth stating.

A **fetched** `robots.txt` is a complete document. An agent it never mentions is
permitted, by the file's own semantics (RFC 9309 §2.2.1). That is a real
verdict about a real policy, not a gap — so `UNSPECIFIED` is reported as a
finding, and it is deliberately not folded into `ALLOWED`, because a site that
named GPTBot and let it in decided something and a site that never mentions it
did not. On the real domains the second is overwhelmingly the common case, and
it is what an agency is being paid to notice.

The genuine unknown is narrower and kept separate: `robots.txt` could not be
FETCHED. That is `UNKNOWN`, it is never merged with an allow, and the screen
draws it as its own state rather than as a permissive grid. `unknown_access()`
is a separate constructor rather than `evaluate_robots("")` for exactly this
reason — an empty string is a valid file that allows everything; an unreachable
one is not a statement at all.

## What shipped

**`services/ai_crawlers.py`** — a pure, database-free RFC 9309 parser and a
roster of 14 crawlers across nine vendors, each classified by PURPOSE
(`training` / `search` / `user_action`). That classification is the epic's
product argument: blocking a training crawler is a rights decision with no
citation cost, while blocking a search crawler removes the site from the
retrieval index engines cite from — which is this product's entire subject. An
agency that blanket-blocked "AI bots" to protect its content has usually bought
the second by accident.

**`AiCrawlerAccess` + migration `33b576c694d0`**, hanging off `technical_audits`
rather than `scans` — the rows are a product of the audit's own robots.txt
fetch, and the cascade then behaves correctly for free. Vendor and purpose are
COPIED onto the row rather than joined from the roster: the roster will change,
and a historical scan must keep saying what was claimed when it was measured.
Asserted by mutating a stored row and reading it back through the endpoint, so
a drift back to a live lookup fails rather than silently rewriting the past.

**`robots.txt` is now read twice per audit, on purpose.** The blanket
`Disallow: /` check that feeds `robots_allows_crawl` → `is_indexable` → the §6
Technical Foundation sub-score was deliberately NOT reimplemented on top of the
new parser, though the new one is strictly more correct. It is a SCORED input:
re-deriving it would move Technical Foundation for every client whose file the
old reader got wrong, inside a diff about crawler policy, with no way to tell a
fixed score from a regressed one. Recorded here rather than left to be
rediscovered as duplication.

**`GET /clients/{clientId}/ai-crawler-access`** — reads, fetches nothing. A
read that re-fetched would let the same URL answer differently between two page
loads and would turn the endpoint into a way to make the API issue outbound
requests on demand; a test asserts the read path never calls `audit_site`.

The scan picker filters on **scans that carry a policy**, not on `ScanStatus`.
Those differ in both directions: a scan can succeed on every engine while its
audit failed, and because the audit's side fetch runs before the page render, a
scan whose audit is FAILED can still carry a full set of verdicts. What the
picker offers is what can be displayed.

**The screen groups by purpose, not by vendor.** A vendor grouping is the
obvious one and reads as a directory — it tells an operator who the crawlers
belong to, which is what they least need help with. The decision an agency is
paid for is what a block COSTS, and that is a property of purpose. Grouping by
it gives each cost a place to be stated in one line above the rows it governs,
rather than as a legend the reader holds in their head.

**A badge means a rule applies; its absence means none does.** That is the
literal difference between `allowed` and `unspecified`, drawn structurally so
the two stay apart without ranking one above the other. `allowed` is `neutral`
and deliberately not `success`: green would read as "you are doing this right",
and for a training crawler that is a rights decision this product takes no
position on.

## The three-skill pass, and a browser finding none of them could have made

**`emil-design-eng`** found two, one substantive. The screen never printed the
DATE the policy was read. Every figure on it is a point-in-time claim about a
file that can be edited any day, and the scan picker offers "2 scans ago"
without ever saying when that was — so a reading from this morning and one from
two months ago were presented identically. It also caught the loading message,
"Reading crawler policy…", which on this screen of all screens implies the
product is fetching robots.txt right now. Both fixed.

**`find-animation-opportunities` found nothing, and that is the correct
result.** Every candidate failed the gate, and they failed it for reasons this
project has already settled rather than for new ones:

- Rows animating in on first paint — they ARE the page's content at first
  paint, so animating them performs on every load. Epic E rejected the same
  thing for the alert feed; §4/Epic 9.16 excludes Working screens from
  arrival motion.
- Staggering the fourteen rows — the same rejection, plus Epic B killed a
  656ms stagger on a screen an operator reopens all day.
- Row hover — these rows are static data with nothing to click. Epic B's fix
  was to make an EXISTING hover eased; it was not an argument for adding one.
- Press feedback — already there. `.avp-btn:active` carries `scale(0.97)`.

**`improve-animations`** found one real thing: the pending dim's `0.45` was
written out in the screen, and `.avp-gapgrid--pending` had its own `0.45` in
`components.css`. Same meaning, two literals, free to drift. It is now
`--avp-dim-pending`, and both use it — the same defect class Epic B's audit
caught with a hand-typed `28ms`. The audit also confirmed what was already
right: no redundant reduced-motion override (`base.css`'s global reset covers
it, and Epic E's audit removed exactly such a duplicate), state tier for a
state change, and a CSS transition rather than keyframes so a fast second scan
swap retargets instead of restarting.

**`review-animations` could not be run**: `disable-model-invocation`, reserved
for explicit user invocation. Fourth epic running, flagged rather than worked
around.

## What only the browser caught

**Two tiles, one colour.** The tile accents were `[ACCENT, ACCENT+1,
ACCENT+2]`, copied from the shape the other screens use. This section's accent
is 5, so those resolved to bench-6 and bench-7 — **hue 343 and hue 355, twelve
degrees apart**, the tightest neighbouring pair in the layer. On Notion, the
one client with a real finding, "Blocked 1" and "Costing citations 1" sat side
by side, showing the same number, in visibly the same red-pink.

This is Epic B's crimson/magenta collision for the third time, and worse: that
one was 29 degrees and `color.ts` already recorded 29 as reading "as the same
pink". Adjacent offsets are not safe on a 97-degree arc and are least safe at
its crowded end — which is exactly where the last free nav seat sits, so the
screen most likely to hit this was always going to be the one taking that seat.

Fixed two ways. The stride is now 3, which keeps the two accents at least 46
degrees apart **for every possible value of `ACCENT`**, and only two tiles are
accented at all — the two that are findings, with the other two drawn as the
context that explains them. `ClientCrawlerView.test.tsx` asserts the separation
as a property across all seven seats rather than against this screen's current
accent, because the failure came from a nav position and would return the
moment the nav table moved.

**Rows a metre wide.** At 1440px the agent name sat hard left and its badge
hard right, so the eye crossed about 1,100px of nothing to connect "Amazonbot"
to "Blocked" — fourteen times. The row lists are now capped at
`max-w-report`; the stat tiles keep the full width, because four figures use
it and a two-item row does not.

**Thirteen rows of wallpaper.** Every row printed the rule that decided it, so
"via User-agent: *" appeared thirteen times — the default, restated until it
was noise, loud enough to bury the one row that read "named as amazonbot". It
now prints only where it says something: an agent the site named itself, or a
block, where inheriting a blanket rule IS the finding.

**A dark-mode capture that was the light one.** The first screenshot pass used
Playwright's `color_scheme="dark"`; this product themes on `[data-theme]`, not
`prefers-color-scheme`, so the "dark" capture came back byte-identical to the
light one. Caught by comparing the files rather than by looking at them, which
is the only way that particular no-op is visible.

## Two guards were strengthened, and one was attempted and abandoned

**Dead colour and radius utilities.** `border-line-subtle` and `rounded-card`
were both written on this screen and both compile to NOTHING — the line colours
are `hairline` and `strong`, and the radii are `sm|md|lg|xl|full`. The existing
guard from Epic E only caught one specific wrong prefix and would not have seen
either. It now RESOLVES the suffix for the nested colour groups and for
`rounded-`, and is negative-controlled: reintroducing `border-line-subtle`
fails it, restoring passes.

**A generic dead-class guard was tried and rejected.** `avp-crawler-row` was a
class name on this screen with no rule anywhere — the exact defect Epic E fixed
for `.avp-alert`, which was fixed by asserting that ONE class exists rather than
generically. A general version was prototyped: it flags about twenty
pre-existing tokens, and nearly all are legitimate — SVG pattern ids
(`avp-hatch-45`, `avp-dot`), generated id prefixes (`avp-select-`,
`avp-field-`), and deliberate structural hooks (`avp-btn__label`,
`avp-shelf__row`). A guard that is mostly allowlist stops catching anything, so
it was not shipped. The dead class itself was simply removed: these rows are
static and non-interactive, so a component class would have existed only to
carry a hover they have no reason to have.

## Verification

**Driven in a browser against real `avp_dev` data**, not stubs, for all four
states. Notion shows its one real finding (Amazonbot blocked by name, thirteen
allowed through `*`, `searchBlocked` 1). Plausible shows fourteen
`unspecified` with the no-policy sentence. Linear shows fourteen allowed with
every tile correctly dimmed. And `epic7-degraded.example` — a domain that does
not resolve — was audited live to produce the fourth: **zero stat tiles**, a
"Not readable" card, and fourteen `unknown` rows. That state had never been
reachable from real data before, and reaching it is what surfaced the lead
paragraph contradicting itself.

Tile rails were read out of the **live CSSOM** rather than the markup, which is
how the twelve-degree collision was measurable at all: `oklch(… 343)` beside
`oklch(… 275)` after the fix, against `343` beside `355` before it. No sideways
page scroll at 1440 or 420. No console errors in any state.

**The Report is unchanged.** `article.avp-report` on `/share/{token}`:
**byte-identical, 72,750 bytes, SHA-256 `cbb44e55…`** — the same hash as before
Epic B, now across a fifth and sixth round of shared-stylesheet edits
(`tokens.css` gained `--avp-dim-pending`, `components.css` now reads it).
Checked rather than assumed, precisely because this epic touched both.

**A test suite that was making real network calls, found by its own slowness.**
`test_crawler_access.py` first ran in **12 minutes 27 seconds for 17 tests**,
and one run wedged completely — two sessions idle in transaction, the process
at zero CPU, and `pg_blocking_pids` reporting no database contention at all,
which is what pointed at Python rather than Postgres. The cause was a missing
stub: `conftest`'s autouse `stub_chain_externals` covers competitor discovery,
the audit and the model, but NOT the engine phase, which `test_answer_gaps.py`
stubs itself and this file did not. Every scan was making real outbound calls
and sitting on their timeouts. With `prompt_service.generate_prompts` and
`engine_service.ask_all` stubbed the same file runs in **1.81 seconds**. Worth
recording as a rule rather than a fix: a new API test file that runs a scan
needs its own engine stub, and a suite that is mysteriously slow is making a
network call it should not be.

**IP-safety check passed:** every field is a verdict enum, a count, an agent
product token, a vendor name, or OUR OWN classification of that agent's purpose
— `ai_crawler_access` has no column capable of holding a path or a rule body,
and `TestIpSafety` asserts both the behaviour (a distinctive `Disallow` path
never appears in the response) and the column set itself, so the guarantee
cannot regress by someone adding a field (7). No dependency was added — the
parser is ~200 lines of stdlib `re` rather than a robots library, which also
keeps constraint 6 untouched. The roster was assembled from the crawler
operators' own published user-agent documentation, and the purpose split was
derived from this product's own citation model, not from any competitor's
feature (1, 5). The screen imports only from `@avp/design-system` (2).

**Suite: 2,150 tests, up from 2,072.** api 963 (+46), web 703 (+29),
design-system 418 (+3), shared-types 53, workers 13. `ruff check src tests`
clean, `tsc` clean across all three TypeScript packages. Migration applied and
`alembic check` reports no drift. Screenshots — 1440, 420, dark, and all four
data states — in `docs/screenshots/epic-f-ai-crawlers/`.

Three of api's forty-six were written by nobody: `test_enum_constraints.py`
parametrises over `Base.metadata`, so `AgentPurpose`, `AccessVerdict` and
`RuleSource` each gained a case asserting the database accepts every member the
Python enum can produce, the moment the table was registered. Worth noting
because it is the guard working exactly as intended — a new VARCHAR-plus-CHECK
enum cannot be added to this schema without its CHECK being verified against
its Python definition.

**The api suite did not get slower**: 98.9s against a 101s baseline, with
forty-six more tests in it. That is the engine-stub fix above, not luck.

## Open, and deliberately not closed here

**J, C and D remain unbuilt**, and the sequencing that put J before C and D is
still the confirmed one. Building F ahead of them was a decision taken with the
founder rather than a drift, and it is recorded here so the roadmap does not
quietly lose three Tier-1 epics. The `UniqueConstraint("prompt_id", "engine")`
finding above is the thing to read before J is planned: it is a bigger epic
than its brief implies.

**Measurement has no free seat left.** Six items, and the palette's arc holds
seven accents with no eighth available. A seventh section in that cluster is a
palette decision, not a nav edit; `clientNav.test.ts`'s projection is now
`measurement: 0` and is where that will surface.

**Prompt discovery (G) is still blocked** on the classification Findings #1 and
#2 in `api-contracts.md`, both still open. Re-flagged rather than resolved,
because nothing this epic touched bears on them.

# API key discipline audit — five fixes, seven refutations, and a workflow that did not finish

An audit of every paid call this system makes: timeout and retry bounds on the
Anthropic sites, code-enforced ceilings on spend, test isolation, cost
observability, credential exposure, repeated calls, and the OpenAI and SerpApi
paths. Seven dimensions, one auditor agent each, and two adversarial verifier
agents per finding — one for correctness, one for consequence — with the
verifier's default set to refute.

It produced **37 raw findings**, and it did not finish. The session that ran it
ended on a rate limit with the workflow's own synthesis step never reached: 59
agents started, 50 recorded a result, and the final confirmed/rejected split at
the bottom of the script never ran. What follows was recovered from the run's
journal on disk — every recorded verdict and its reasoning, read directly —
rather than re-run, which would have re-spent roughly the same sixty-odd agent
calls for a marginal gain over what was already there.

## The coverage the workflow actually achieved, counted from the journal

Twenty-one findings have both verifier lenses recorded. One has one lens. The
remaining fifteen have none: the dispatcher never reached them. That is not
spread evenly — the **entire OpenAI-and-SerpApi dimension and the entire
repeated-calls dimension have zero recorded verdicts**, because they were
dispatched last. The truncation bug, the reasoning-effort gap, and the
executor's guard were all in those two dimensions, and all three were
confirmed here by reading the code rather than by the machinery.

The handoff document written as that session ended labelled several of those
fifteen as verified by both lenses. It was wrong about that, and it was also
silent about something the journal makes plain: of the twenty-one findings
with two verdicts, **seven were refuted by both lenses**. The handoff carried
them forward as open findings. They are not.

## Seven findings were literally true and wrong — the cost-observability dimension

All seven refutations landed on one dimension, and they share one shape. The
auditor searched for log lines and metering and found none: no call site reads
`response.usage`; `engines.py` and `cocitation.py` define a logger and never
call it; there is no aggregate spend view; SerpApi queries are "never
recorded"; `classify_sentiment` logs only failures; `engine_result_count` is "a
product, not a count". Every one of those sentences is accurate. Every one of
them misses that the fact it wants is **already a column**.

`persist_result` writes one `engine_results` row per prompt × engine, failures
included, with `status`, `error_code` and `latency_ms` on each — queryable,
joinable, retained, and served on `GET /scans/{scanId}/results`. That is
strictly more than a log line, and it is why the "cannot distinguish
rate-limited from refused from timed out" claim fails: `_map_error` writes
exactly those distinctions onto every row. The sentiment call count is the
number of rows with `mentioned = true`, under a dedicated index. SerpApi
queries are `CompetitorSet.serp_queries_run`, persisted and published as
`serpQueriesRun`. And `engine_result_count` is the row count by construction,
because every adapter maps every exception to a status and never raises, so
`ask_all` returns exactly one answer per engine — the verifier proved that
from the code, and it is the fact the spend-ceiling design below leans on.

The one survivor in that dimension is split: `structlog` is genuinely never
configured, so `LOG_LEVEL` is a dead knob and events render as console text
rather than JSON — verified on correctness, refuted on consequence, because
nothing in the log stream spends money, hangs, or carries a credential. The
LOW duplicate of the same root cause, about `logger.debug` sites "annotated
debug-only", was refuted outright: "debug-only" is a defined term in this
project (build-log Epic 8, north-star §5) meaning *never persisted, never
returned*, a data-flow property, not a log level.

Worth recording because it is the audit's own lesson about auditing: a grep
for observability that does not also grep the schema will report a system
that measures everything as a system that measures nothing.

## One finding was cleared, and the clearing is the point

`email.py:111` logs live password-reset and invitation tokens at info level.
Both verifier lenses confirmed the code reads as claimed, and it is not a
finding: the line fires only when `RESEND_API_KEY` is unset, and the
function's own docstring states that the logged URL "is the recoverable
credential in development, and that log line is how it is recovered". Epic
9.20 and Epic B both relied on that path on purpose. Reported here rather than
silently dropped because it is the evidence that the audit was reading
comments and not pattern-matching "logs a URL" to "bad".

## What shipped — five fixes, five commits

Each landed as its own commit as it was finished, because the previous two
sessions both ended mid-task, and a batch of uncommitted fixes from a session
that ends on a rate limit is the thing that had to be untangled once already.

**1. An answer the engine did not finish is not an answer** (`c956aa1`). Both
adapters read the vendor's stop reason for one value, "refusal", and treated
everything else as a finished answer. A response cut off by the token budget
reached `extract_facts` as OK; the brand was absent from the truncated
prefix; the row was recorded `answered_no_mention`; scoring counted a billed
non-answer against the mention rate; the scan read `succeeded`. Two new
statuses, `truncated` and `paused`, kept apart because one has a possible fix
(resume the call) and the other does not. The complete-answer set is an
allowlist — `{end_turn, stop_sequence}` and `{stop}` — and a test reads the
pinned SDK's own `StopReason` literal to assert every member is sorted, so an
SDK bump that adds a value fails as a test. `max_uses_exceeded` is
deliberately not truncation: it is a tool-result error code, and hitting
`SEARCH_MAX_USES` yields a complete, less-grounded `end_turn` answer. None of
the ten downstream consumers changed; all read a positive allowlist. Migration
`4e7d2c91ab05` widens both status CHECK constraints and its downgrade maps to
`error` rather than deleting rows.

**2. The ChatGPT engine says how hard to think** (`c9af859`). gpt-5.5's model
page lists `none / low / medium / high / xhigh` with medium the default, and
Chat Completions counts reasoning tokens against `max_completion_tokens`. The
adapter sent no effort, so every call spent medium-effort reasoning inside a
4,000-token budget sized for a low-effort answer — and reasoning that eats the
whole budget is exactly how `finish_reason: "length"` arrives with an empty
body, the shape fix 1 found being recorded as an absence. `reasoning_effort:
"low"`, pinned equal to `ANSWER_EFFORT` so the two parametric engines Epic
9.13 compares stay on equal footing. Verified against the published model
page, not a live call; a rejection would surface as `PROVIDER_BAD_REQUEST` on
every chatgpt cell rather than silently.

**3. `engines` is bounded at the request, not after the bill** (`f3c58ad`).
`["claude", "claude", "claude"]` was three billed calls per prompt that then
died on `uq_engine_results_prompt_engine`. The schema de-duplicates before
enum validation and caps at the enum's size; the router checks each engine
against `ENGINE_REGISTRY` before the reaper runs and before any scan row
exists, because `ask_all` subscripts the registry bare and an enum-only engine
used to fail *inside* the executor, after competitor detection was paid for.

**4. The executor claims a scan before it runs it** (`15b7b77`). `execute_scan`
refused only TERMINAL scans, and QUEUED and RUNNING are deliberately outside
that set, so two POSTs adopting one open scan produced two executors and the
second re-ran the paid chain up to a unique violation on `prompt_sets` — which
then marked the scan FAILED while the first executor was still running it.
**`uq_scans_one_open_per_client` never prevented this**: it bounds rows, and
both executors held the same row. The earlier session's first draft of the
finding blamed the constraint, and reading it was what corrected that. The
claim is one conditional `UPDATE`, queued → running, committed before the
first paid phase; a concurrent executor blocks on the row, matches nothing,
and exits. The router additionally returns a RUNNING scan as it is and starts
nothing. Proven with two `execute_scan` calls gathered on one job.

**5. The chain tests stop crawling helpscout.com** (`74140ce`).
`test_scan_chain.py` created its client without `classify: False`, so intake
ran a real Chromium crawl and a real claude-opus-5 classification, nine times
per suite run. `Settings` reads `.env` for anything not passed explicitly, so
the test settings inherited the real key and those calls were **billed**, not
refused. Nine tests at ~50s now take 1.6s, and the whole api suite dropped
from 98.9s to 48.5s — the same shape Epic F recorded for
`test_crawler_access.py`, from the other side of intake.

## Four assumptions that reading corrected

The audit's method was "check before trusting", and it caught its own
machinery four times. `uq_scans_one_open_per_client` does not prevent double
execution (above). `pause_turn` is a real stop reason the grounded engine can
produce today, not a hypothetical. `max_uses_exceeded` is not a stop reason
at all. And, this session, the handoff's coverage labels: seven "both lenses"
findings were both-lenses *refuted*, and two whole dimensions had no lens at
all. The first three changed what was built; the fourth changed what this
entry says was found.

## The spend ceiling — one problem, not three patches

The shared root cause behind the executor guard, the static `STALE_AFTER`,
and the unbounded `engines` list is that the executor treats **duration and
identity as the same signal**. Nothing distinguishes "still genuinely working"
from "should be treated as dead" except elapsed time, so a constant stands in
for a signal the system does not have. The audit's arithmetic makes the
constant's failure concrete: `ceil(30 / 4) × 122s = 976s` of engine phase
against a 900s `STALE_AFTER`, before a single sentiment call — so a maximal
scan is reaped while still running and still billing, `finalize_scan` then
no-ops because the row is terminal, and the next POST starts a *second* full
run beside the first. Fix 4 does not stop that: it is a different row.

Two pieces, separable, and they interact. The `engines` bound (built) makes a
prompt slot's duration the max over at most three ceilinged adapters instead
of over whatever a caller sent; that is what makes the gap between renewals
below knowable at all. The lease (half built) is what makes duration stop
mattering.

**The second half of the lease, as designed and not built:**

* A column: `scans.lease_expires_at TIMESTAMPTZ NULL`. `STALE_AFTER` retires.
* The claim's `WHERE` grows, rather than being replaced:
  `status = 'queued' OR (status = 'running' AND lease_expires_at < now())`,
  setting `lease_expires_at = now() + LEASE`. A dead executor's scan is then
  re-claimable directly, without a reaper pass first.
* Renewal: `_attempt` renews before each phase, and `run_scan` renews as each
  prompt completes, with one statement —
  `UPDATE scans SET lease_expires_at = now() + LEASE WHERE id = :id AND status = 'running'`.
  **A renewal that matches zero rows means the lease is gone**, reaped or
  re-claimed, and the executor stops rather than keeps billing. That is the
  half the reaper has never had: today it stamps the row and the work carries
  on.
* The reaper keys off `lease_expires_at < now()`, not `started_at`.
* `LEASE` is derived, not chosen: the longest un-renewable gap plus margin.
  One prompt slot is `ENGINE_CALL_CEILING` (122s) plus the sentiment call —
  and **the sentiment call is unbounded today**, inheriting the SDK's 600s ×
  3 attempts, as are `generate_prompts` and `classify`. A lease length cannot
  be chosen while any single un-renewable call is unbounded, which is why the
  Anthropic call-site bounds below are the prerequisite for this work and not
  a separate item.

**Where the hard ceiling should live — a recommendation, not a build.** At the
claim. It is the one statement every execution passes through, it already runs
before the first paid phase, and refusing there costs nothing. The ledger it
needs already exists and this audit proved it: `engine_results` rows are the
engine-call count by construction, each joins to an agency through its scan,
and `prompt_run_results` is the same for ad-hoc runs. A per-agency monthly
call count is one `COUNT`, and sentiment, prompt-generation and fix-generation
calls are derivable from the same rows. So: a `PROVIDER_CALLS_PER_AGENCY_PER_MONTH`
setting, checked at claim time, and a claim that fails on budget lands the
scan `failed` with a new `error_code` of `BUDGET_EXHAUSTED` — no new table, no
enum change, and QUEUED rows left by detect-only runs stay adoptable because
the check is at execution, not at POST. It is not a paywall (`billing.py`
says so and still does) and it is not `UsageRecord` (north-star §5.4 row 2):
when that exists it replaces the `COUNT`, not the enforcement point. The three
other unthrottled paid endpoints — competitor detection, fix generation, and
client creation and reclassification — need either the same ledger check or a
request throttle in the shape `prompt_runs.check_throttle` already has; the
ceiling at the claim covers scans only. A freshness check on re-run is a
product decision the Re-run button was built against, and is not recommended
until the ceiling exists.

## Verification

**api 1,015 passed, up from 963**, in 48.5s against Epic F's 98.9s. `ruff
check src tests` clean. `tsc` clean across shared-types, design-system and
web, with `api.gen.ts` regenerated so `EngineResultStatus` carries the two new
members. Migration applied to `avp_dev`, `alembic check` reports no drift, and
the downgrade was run and re-upgraded to prove the mapping to `error` holds.
web 703, design-system 418 and shared-types 53 unchanged; workers 13
untouched. **Suite 2,202, up from 2,150.**

`test_enum_constraints.py` did its job unasked: both `truncated` and `paused`
gained a database-acceptance case for both tables the moment the enum grew,
which is the guard Epic 4 built for exactly this migration shape.

## Open, and deliberately not closed here

**The Anthropic call-site bounds — the audit's first dimension, verified by
both lenses on every item, none built.** `classify_sentiment`
(`extraction.py:315`) inherits both SDK defaults and is awaited serially
inside the `PROMPT_CONCURRENCY` slot: one hung call is 30 minutes and three
billed generations. `classify` (`classify.py:175`) is the same, in a request
path whose docstring promises 30 seconds. `run_seed_prompt`
(`cocitation.py:164`) is fanned four-wide at concurrency three. `generate_prompts`
(`prompts.py:257`) sits in front of the whole engine loop. `generate_fixes`
(`fix_generator.py:623`) bounds the attempt and not `max_retries`, and its own
comment says so. The recipe is the one `engines.py` already documents at
length — `timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES`, explicit and
never inherited — and it is the prerequisite for the lease length above, so
it is the next thing to build, not a nice-to-have.

**Verified and not built:** the three unthrottled endpoints above, and
`classify_sentiment` as one un-batched call per mentioned answer (one lens
recorded, the other in flight). **Confirmed by reading `engines.py` in full,
never verified by the workflow:** a fresh `AsyncAnthropic` and a fresh
`httpx.AsyncClient` per call, never closed; an unset `OPENAI_API_KEY`
degrading to thirty `PROVIDER_ERROR` cells with no line naming the variable;
and `_extract_citations` skipping a `web_search_tool_result_error` block with
no log. **Unverified and unread this session:** per-invocation SerpApi
semaphores (`serp.py:229`), `SERPAPI_KEY_MISSING` unreachable behind
`provider_key` (`serp.py:167`, a genuine deletion candidate — unlike `paused`'s
unreachable triggers, which wait on a feature), and the re-crawl on every
`reclassify` and the six identical SerpApi searches on every detect.

**`structlog` is still unconfigured.** Split verdict, no spend consequence,
and a real operability gap: `LOG_LEVEL` does nothing.

**The verifier prompts for two dimensions never ran.** Nothing in this entry
claims machine verification for the OpenAI-and-SerpApi or repeated-calls
findings. What is marked confirmed above was confirmed by reading.

## Follow-up, 2026-09-07 (later session): fix 2 checked against the live API

Fix 2 above was the one piece of this audit that rested on a documentation
page rather than on code or a test, and it was flagged as such. It has now
been checked the way everything else was: by doing it.

Four calls to gpt-5.5 (served as `gpt-5.5-2026-04-23`), all on the same
one-sentence prompt, all low-cost:

* **The adapter's exact body, sent raw with `reasoning_effort: "low"`** —
  200, `finish_reason: stop`, 30 completion tokens, 0 of them reasoning.
* **`ChatGptAdapter().ask(...)` end to end** — status `ok`, 143 characters,
  2.9s, no error code. The shipped code path succeeds with the field in the
  body. It does not come back `PROVIDER_BAD_REQUEST`.
* **The same body with an invalid value** — 400, `invalid_request_error`,
  code `unsupported_value`, `param: reasoning_effort`, and a message listing
  the supported values: `none`, `low`, `medium`, `high`, `xhigh`. That is the
  same five the model page listed, confirmed by the API's own validator. It
  is also the proof a success alone could not give: the parameter is parsed,
  not silently ignored, so the 200 above means "accepted" and not "unread".
* **The pre-fix body, with no `reasoning_effort` at all** — also 200, also
  30 completion tokens, also 0 reasoning. On a one-sentence question the
  default effort did not reason either.

So the parameter name, the value, and the code path are verified. What the
last call did NOT show is the mechanism fix 2 was argued from: that the
unset default spends reasoning tokens inside `max_completion_tokens` and is
how an empty `length`-terminated body arises. A trivial prompt is not the
prompt that triggers it, so that remains a reading of the documentation
rather than a live observation — neither confirmed nor refuted here, and
recorded as such rather than promoted. The fix stands on what is verified:
an explicit effort, accepted by the API, pinned equal to the Claude side so
the two parametric engines are compared on the same footing (Epic 9.13).

# Call bounds — five paid calls get a ceiling, and the lease gets its arithmetic

The audit above closed with one item it called a prerequisite rather than a
nice-to-have: five Anthropic call sites outside `engines.py` inherited the
SDK's defaults — a 600-second read timeout across three attempts — so a hung
call at any of them was thirty minutes and three billed generations, and no
lease length could be chosen while that was true. This entry builds them.
Two smaller things first, because both were flagged as loose ends and both
bear on whether the audit's own claims can be trusted.

## The two loose ends

**Fix 2, checked live.** Recorded as a follow-up under the audit entry
above: four short calls to gpt-5.5 confirm `reasoning_effort: "low"` is
accepted, that `ChatGptAdapter().ask` returns `ok` end to end, and — the
part a success alone could not show — that an invalid value is rejected with
a 400 naming the parameter and listing exactly the five values the model
page did. What was not shown is the mechanism the fix was argued from: the
same one-sentence prompt with no effort at all also spent zero reasoning
tokens. Inconclusive on a trivial prompt, and recorded as inconclusive.

**The lease, written down three times.** The build-log entry, the
`execute_scan` docstring, and the previous session's handoff were read side
by side. The entry and the handoff agree. The docstring had drifted: it
described renewal as something `_attempt` does between phases and stopped —
no column, no re-claim of an expired RUNNING row, no per-prompt renewal in
`run_scan`, and no mention of the rule that matters most, that **a renewal
matching zero rows means the lease is gone and the executor stops**. That is
precisely the detail a skim simplifies away, so the docstring now carries the
whole mechanism, copied from the entry rather than the reverse (`acd7565`).

## The shape: one value, five declarations

`engines.py` documents the pattern at length and `test_engine_timeout.py`
proves it. Rather than copy three constants and their arithmetic into five
more modules, `services/call_bounds.py` makes the shape a value:

    CallBound(timeout=30.0, max_retries=1)   # .ceiling == 62.0

`timeout` and `max_retries` go to the client verbatim, `ceiling` is
`timeout × (max_retries + 1) + backoff_allowance`, and `deadline()` is an
outer `asyncio.timeout(ceiling)` — the thing that makes the number a
guarantee even if the SDK's retry internals move under the version range
pyproject allows. Each site declares one bound next to its model and effort
constants, with the reasoning for its numbers in the comment beside it.

`engines.py` deliberately does **not** become a `CallBound`. Its constants
are pinned by name and swapped by value in a verified test, and rewriting a
verified fix for symmetry is how a verified fix stops being one. A test
asserts the two arithmetics agree instead.

## The five, and why each number is what it is

| Site | Per attempt | Retries | Ceiling | The number comes from |
|---|---|---|---|---|
| `classify` | 12s | 1 | **26s** | Its docstring promises 30s end to end, crawl included; the whole endpoint measured 8.3s (Epic 9.4). 26s is the largest ceiling that fits the promise. |
| `classify_sentiment` | 30s | 1 | **62s** | Unmeasured — no latency column records it. Half the parametric engine's attempt bound for a call with a 1,500-token budget against 4,000. |
| `run_seed_prompt` | 30s | 1 | **62s** | The whole detection endpoint, six SerpApi searches and four of these calls, measured 20.9s (Epic 9.4). |
| `generate_prompts` | 60s | 1 | **122s** | Measured 14.6s (Epic 9.2); medium effort and 8k tokens earn 4× headroom. A timeout falls back to the deterministic set. |
| `generate_fixes` | 120s | 1 | **242s** | The 120s it already had, six times the measured 20.3s (Epic 9.4). It bounded the attempt and, by its own comment, not the retries. |

One retry everywhere, for the reason `engines.py` gives: Epic 9.1's
distribution had two calls whose first attempt timed out and whose retry
succeeded in under thirty seconds, and a 529 in the first second is far
commoner than a slow-but-alive generation.

**A defect found along the way, in three places.** `classify`,
`run_seed_prompt` and `generate_fixes` each catch `APIConnectionError` and
report `PROVIDER_UNREACHABLE`. `APITimeoutError` subclasses it. So a
per-attempt timeout at any of the three was recorded as an unreachable
provider — the same misordering Epic 9.2 fixed in `_map_error`, present in
three more ladders. Each now catches `(APITimeoutError, TimeoutError)` first
and reports `TIMEOUT`; the builtin `TimeoutError` is the outer deadline, which
is not an `APIError` and would otherwise have escaped every ladder. The two
sites without ladders, sentiment and prompt generation, widen their catch and
degrade as before. `TIMEOUT` is already the vocabulary of `engines.py`,
`crawl.py` and `technical_audit.py`, and the web renders reason codes as
opaque strings, so the contract is unchanged.

## What the lease can now compute

The design says LEASE is the longest gap between two renewals plus a margin.
With renewal before each `_attempt` phase and after each prompt completes,
the gaps are now numbers:

* **One prompt slot is not 122s plus "the sentiment call".** `run_scan`
  awaits one sentiment call per ENGINE whose answer named the subject,
  serially, after the concurrent engine calls. Worst case:
  `ENGINE_CALL_CEILING + 3 × 62 = 308s`. The audit entry's arithmetic
  understated this, and it is corrected at the site.
* **Prompt generation runs inside `run_scan`, before the loop, and nothing
  renews between it and the first prompt.** As designed, the first gap is
  `122 + 308 = 430s`. One extra renewal — after `generate_prompts`, before
  the loop — brings the longest gap to 308s.
* **A renewal inside the slot**, between `ask_all` and the sentiment calls,
  brings it to `max(122, 186) = 186s`. Recommended; not yet designed in.
* Competitor detection is two waves of three co-citation calls plus SerpApi:
  `2 × 62` plus SerpApi's own bound. Fix generation is 242s. The technical
  audit is Playwright, bounded by its own timeouts, and not this entry's.

So LEASE is 308s plus margin with one added renewal, or 186s plus margin
with two — against the 900s static `STALE_AFTER` it replaces. The choice
belongs to the lease build; the point of this entry is that it is now a
choice between numbers rather than a constant standing in for a signal.

## The test, and one seam it needed

`test_call_bounds.py` holds every site to the engine test's strong property
— the real client, only its transport faked, every attempt hangs, and the
call still fails inside the ceiling — parametrised over the five, with each
site's failure shape asserted as its own docstring promises it: `(None,
None)`, a `fallback` set, an outcome carrying `TIMEOUT`. It pins each
ceiling by value, reads the SDK's own backoff constants to prove the 2.0s
allowance covers the schedule, and pins the two site-specific constraints:
classification under 30s, fix generation's 120s attempt bound preserved.

The seam: conftest's autouse `stub_chain_externals` replaces
`AsyncMessages.parse` for fix sets with a canned response, one level ABOVE
the transport these tests fake, so the fixes site would never have reached
it. A registered marker, `real_parse`, opts a test module out of that one
patch. A stub that kept intercepting would have reported zero attempts, and
the tests would have said so.

## Verification

**api 1,043 passed, up from 1,015** — 28 new, all in `test_call_bounds.py`.
Each of the five bounds landed as its own commit, each verified on the full
suite with nothing else in the tree (`0e4fe72`, `eeabfb4`, `da93f48`,
`6a0c7ed`, `8e9198d`). `ruff check src tests` clean; `ruff check .` reports
eight pre-existing findings in `scripts/`, outside the configured scope and
untouched. `alembic check` reports no drift; no migration this session.
`tsc` clean on all three packages at session start and nothing since touched
the contract. **Suite 2,230, up from 2,202.**

## Open, carried forward unchanged

Everything in the audit's "Open" list that this entry did not build is
still open: the three unthrottled paid endpoints, the fresh client per call
never closed, the unset `OPENAI_API_KEY` degrading silently, the skipped
`web_search_tool_result_error` block, the SerpApi semaphores and the
`SERPAPI_KEY_MISSING` deletion candidate, and `structlog` unconfigured. The
lease itself is next, and its prerequisite is met.

## Follow-up, 2026-09-07 (third session): the mechanism behind fix 2, reproduced

The first follow-up verified the parameter and left the mechanism open:
that the unset default spends reasoning tokens inside
`max_completion_tokens`, and that this is how an empty `finish_reason:
"length"` body arises. A one-sentence prompt could not show it. A
scan-shaped one can. Four more calls to gpt-5.5, the same prompt each time
— a 40-person SaaS company asking for a ranked comparison of help-desk
tools — at the production budget and at one small enough to exhaust:

| effort | `max_completion_tokens` | `finish_reason` | reasoning tokens | body |
|---|---|---|---|---|
| unset (default) | 4,000 | `stop` | 512 | 13,250 chars |
| `low` | 4,000 | `stop` | 92 | 11,066 chars |
| unset (default) | 300 | `length` | 300 | **empty** |
| `low` | 300 | `length` | 300 | **empty** |

The mechanism is real and reproduced, not read off a documentation page:
at a budget the reasoning can exhaust, every completion token goes to
reasoning and the body comes back empty under `length` — exactly the shape
fix 1 found being recorded as "ChatGPT answered and did not name you". It
happens at `low` as well as at the default, so the effort setting is a
margin, not an immunity: at the production budget the default spent 512
reasoning tokens and `low` spent 92, a five-and-a-half-fold reduction on
the same answer. Fix 2 stands on both legs now — the parameter verified
live, and the failure mode it guards against demonstrated — and fix 1's
`truncated` status is what catches the case the margin does not.

Decision recorded: the gap is closed by reproduction, not by accepting the
documentation. Nothing changes in the code.

# The lease — and the row lock that every version of its design was resting on

The call bounds made a lease length computable, and this is the lease. It did
not get built the way it was designed, and the reason is worth more than the
feature: an adversarial review of the design found a lock that made every
candidate shape — including the one in the build log — unable to work at all.

## What was actually asked, and what came back

Eight claims about the code behind the design, two adversarial lenses each,
plus three judges scoring four candidate renewal shapes and a completeness
critic. **It did not finish.** Two sessions in a row ended on a rate limit
mid-run; the first lost all twenty agents with nothing recoverable, the second
returned **14 of 16 refutation verdicts and 1 of 3 judges**, and the critic
never ran. What follows leans on what came back, and every decisive fact in it
was re-checked by hand or by experiment before anything was built. That is
stated because the alternative — presenting a two-thirds-finished review as a
verdict — is exactly the failure the audit entry above already had to correct
once.

## The finding that changed the design: a row lock, not a renewal shape

`run_scan` sets `prompt_count` and flushes it before the engine loop. That
flush is an `UPDATE scans`, which takes a row lock held until the transaction
ends — and the next statement on that session was the commit **after** the
gather. So for the whole engine phase, minutes and up to ~2,460s on a maximal
scan, nothing else could write that scan row.

Which means: a heartbeat on its own session would have **blocked on the lock
instead of renewing**, and the lease would have expired during ordinary work.
So would `reap_stale_scans` from a dashboard read. Every candidate shape — the
build log's two insertion points, renew-after-every-call, a clock heartbeat —
assumed a renewal could reach the row. None of them could.

Verified rather than believed, with two connections against `avp_dev`: the
second `UPDATE` blocked until the first transaction ended, and committing
released it. `run_scan` now commits after that flush (`a63cd3f`), which is
independently right for the reason its own earlier commit gives — the prompt
set is finished work, and committing makes `prompt_count` pollable while the
loop runs. The regression test asserts it from inside the loop on a second
session, because that is the only moment the lock was ever held; removing the
commit makes it fail by timing out.

## The decision: a clock, not a set of insertion points

The build log framed this as choosing where to put one or two renewal calls,
and that framing is **replaced rather than extended**. It was derived by
extending the audit's design instead of re-deriving from the call graph, and
re-deriving is what showed the frame itself to be the problem:

* **It makes the lease length a sum of ceilings living in five other modules.**
  A phase added later silently outgrows it, with nothing but a hand-maintained
  test to notice. A clock does not care what the chain grows into.
* **The per-prompt renewal lands inside `asyncio.gather` at four-way
  concurrency**, on a session `AsyncSession` forbids using concurrently.
* **`gather` does not cancel its siblings**, so a lost lease in one slot leaves
  three engine calls billing for up to 308s. "Stop, don't keep billing" would
  have been only approximately true.
* **`_attempt` swallows every `Exception`**, so the loss would have needed
  carving out of the swallow at six call sites.

The judge that landed ranked the clock designs 7 and 8 against 4 and 3 for the
insertion-point ones, on exactly these grounds. The clock also makes the stop
property testable without wall-clock arithmetic, which is how it is tested.

**Built: a heartbeat every 30s, a lease of 180s**, six intervals rather than
the conventional three. The fifth and sixth are not slack for its own sake:
`database_pool_size` is 10 with 5 overflow and nothing bounds how many
executors run at once, so a saturated pool can park a renewal on SQLAlchemy's
unconfigured 30-second `pool_timeout` — one whole interval on a machine that is
entirely healthy. At three intervals two such waits expire a live executor's
lease. Dead-executor detection drops from 900s to 180s, at one `UPDATE` per
running scan every 30s.

**A renewal that matches zero rows cancels the chain.** This is the half a
reaper alone never had, and it is the one property checked by mutation: with
the cancel removed, the chain runs on to the next paid phase and the test
fails. The chain runs as its own task so cancellation reaches it and nothing
above it — cancelling the current task would reach the request handler under
`InlineScanExecutor`. A renewal that *raises* is deliberately not loss: the
database being briefly unreachable says nothing about who owns the row.

## Two places the design as written was not followed

**The claim stays QUEUED-only.** The design widened it to re-claim RUNNING
scans with an expired lease. Both lenses agreed it should not be, and reading
confirms why: `run_scan` is not resumable — `build_prompt_set` always inserts
a new `prompt_sets` row — so a re-claim would pay for competitor detection and
a 122s-ceiling prompt generation and then die on `uq_prompt_sets_scan_id`,
landing FAILED / EXECUTION_FAILED where the reaper gives FAILED /
EXECUTOR_LOST at no spend. It also has no caller: the router reaps before
adopting and never submits a job for a RUNNING scan. And it would break the
zero-rows rule it was meant to complement — after a re-claim the row is still
RUNNING, so the old executor's renewal matches it and renews the *new* owner's
lease. Revisiting it needs a resumable chain and a fencing token, and the
`execute_scan` docstring says so.

**A deadline came back, in the executor rather than the reaper.** Retiring
`STALE_AFTER` gave up a case it caught by accident: an executor that is alive,
renewing, and getting nowhere. The heartbeat is its own task, so an await that
never returns renews forever — and there is one today, `page.evaluate` in
`technical_audit.py`, which takes no timeout and sits outside
`set_default_timeout`'s reach. Putting the backstop in the reaper would have
restored the exact inference the lease exists to remove, "long, therefore
dead"; putting it in the executor makes it a ceiling on its own work, the
`CallBound` shape one level up, and it *stops* the work rather than relabelling
the row while the spending carries on. `MAX_SCAN_DURATION` is 3,600s and the
test recomputes a maximal scan from the constants that own each term — 3,040s,
560s of headroom — so raising any ceiling fails a test instead of eating the
margin. Checked by raising `FIX_BOUND` to 600s, which fails it with the new
total. A scan stopped this way gets its own code, `SCAN_DEADLINE_EXCEEDED`.

## Verification

**api 1,053, up from 1,043** across four commits, each verified on the full
suite before the next began: `a63cd3f` the row lock, `bbec5be` the column and
backfill, `6341063` the lease itself, `413ef63` the deadline. `ruff check src
tests` clean. `alembic check` reports no drift; the migration was applied,
downgraded and re-applied on `avp_dev`. The OpenAPI export was regenerated to
prove the lease does not surface in any response schema — it does not, and the
only diff regenerating produced was audit fix 3's, which had never been
regenerated and is committed separately as `05a8630`.

Three properties are checked by mutation rather than by assertion alone,
because each is the kind that passes vacuously: the row lock (remove the
commit, the test times out), the stop rule (remove the cancel, the chain
reaches the next phase), and the derived deadline (raise a ceiling, the sum
exceeds it).

## Open

**The review never finished**: two refutation verdicts, two judges and the
completeness critic did not run. What they might have found is unknown, and no
claim here rests on machine verification alone.

Found by the review and **not** built: `page.evaluate` has no timeout of its
own (the deadline covers it at a whole-scan granularity, not a phase one);
nothing bounds concurrent executors per instance, so ~14 running scans exhaust
the 15-connection pool (the six-interval lease absorbs the consequence rather
than fixing the cause); and a verification script dying mid-run leaves a
RUNNING row with a NULL lease that nothing reaps, since NULL is deliberately
not expired.

Everything in the audit's own open list is unchanged: the three unthrottled
paid endpoints, a fresh client per call never closed, an unset
`OPENAI_API_KEY` degrading silently, the skipped `web_search_tool_result_error`
block, the SerpApi semaphores, the `SERPAPI_KEY_MISSING` deletion candidate,
and `structlog` still unconfigured.

# Epic 9.21 — a share link can be taken back, and stops working on its own

`models/scan.py` had carried this gap in capital letters since Epic 9.8, which
is the only reason it was easy to close: *"DELIBERATELY NOT BUILT, and this is
a known gap rather than an oversight: there is NO EXPIRY and NO REVOCATION.
Once minted, the link works until the row is deleted... the first agency that
shares a report with the wrong prospect has no way to take it back."*

**Layer 5 (Distribution/Action), Activation phase.** It also touches Layer 0:
this is a security control on the product's only unauthenticated read surface,
not a UI nicety, which is why it is its own slice rather than half of the
branding one.

## Two mechanisms, because they answer different questions

They are independent and both were built.

**Revocation** is deliberate and immediate: `DELETE /scans/{scanId}/share`,
`204`, no body. On the same path as the mint, because the thing being removed
is the resource that path names — a `POST .../share/revoke` would invent a
second noun for one capability, and a `PATCH` with a null token would make
"unshare" reachable from an endpoint whose job is to describe a scan. It is
idempotent: revoking twice, or revoking a scan that was never shared, is `204`.
The caller asked for it not to be readable and it is not, so a `404` would
report on something they did not ask about and a `409` would invite a retry
loop over a state that is already correct.

**Expiry** is automatic, and it is for the larger population: the link nobody
remembers sending. Revocation needs somebody to decide; forgetting is precisely
the failure mode no UI will ever catch.

## The TTL is thirty days, and the number is argued rather than picked

There was no prior number in this product to anchor to, so the reasoning stands
in for the precedent:

* **It must outlive the conversation it was sent for.** A prospect report is
  read, forwarded to a colleague, and put in front of whoever holds the budget.
  That runs days to a couple of weeks. A link dying mid-thread is the agency's
  embarrassment rather than the prospect's, so the floor is the worst realistic
  sales cycle, not the median one.
* **It must not outlive the data.** The report is a snapshot of one scan. A
  month on, a re-scan would show different numbers, and a link still serving the
  old ones is quietly making a stale claim about a live business.
* **It is the blunt instrument.** Revocation handles "sent to the wrong person,
  now", so expiry only has to bound the forgotten link and can afford to be
  generous where a security control alone could not.

Those bounds are close together, which is why the range worth arguing over was
narrow. Thirty days sits where they meet and matches the month the rest of the
product already thinks in.

## A re-mint refreshes the expiry, and one asymmetry decided it

Both readings of "idempotent" were defensible until the alternative was
followed through: **without a refresh there is no way to extend a link at all
without changing its URL.** The only other route is revoke-and-mint, which
issues a new token and kills every copy already sent — so an agency re-sharing
on day 29 would have to break the link they were re-sending. Idempotence is
about the token's identity, which is what Epic 9.8's test asserts and what
still passes unchanged; the clock is not part of that promise. Re-sharing is
the agency restating the decision to share, which is exactly the signal an
expiry should listen to.

## Revoked and expired are not new kinds of wrong

Epic 9.8's rule was that *"a malformed token, an unknown token and a well-formed
miss are indistinguishable in both status and body... never 401 — that would
confirm the token is real"*. A revoked token is not a special case of that rule;
it is the same rule. Both filters therefore live in `scan_for_share_token` — one
query, both read routes — rather than in either route, so neither can drift into
answering "expired" to a holder who would learn from hearing it. Tests assert
the revoked and expired responses match an unknown token's field for field,
minus `instance`, which echoes the request path back and so tells a caller only
what they sent.

**NULL is not "never expires".** The comparison is strict, so a token that
reached the table without an expiry serves nothing — fail-closed, for a
credential. A CHECK constraint,
`(share_token IS NULL) = (share_expires_at IS NULL)`, makes that state
unwritable rather than merely unserved, and a test drops the constraint to prove
the read path holds on its own. Two independent guards, checked independently.

## The PDF revisit that Epic 9.14 asked for

That epic accepted a real cost with a note attached: *"a downloaded PDF outlives
any future revocation... When revocation ships, this is the route to revisit."*
This is that revisit, and the decision is recorded rather than inherited. A
revoked or expired link stops serving files immediately, on the same lookup as
the JSON route. What revocation cannot do is recall a copy already on someone's
disk — nothing can, short of a watermarked or phone-home document, which is a
different product with different privacy properties. So the residual gap is
real, bounded and named on both PDF routes and in `api-contracts.md`: revocation
governs the **link** from the moment it is invoked, never a file already
downloaded.

## A stale document, corrected where it was found

`north-star.md`'s Layer 5 heading read **NOT STARTED** and was stale by three
epics — the share link (9.8), PDF export (9.14) and now this. It now reads
PARTIALLY BUILT and says what exists and what does not. Corrected in the commit
that closed the gap it was wrong about, the way Epic 7.1 corrected
`design-direction.md` and `design-system.md`, rather than left for someone to
find five epics later.

**IP-safety check passed:** no new dependency (constraint 6 untouched — this
slice adds one column, one route and one constant). No third-party content
enters or leaves: the only new value on the wire is `expiresAt`, a timestamp we
generate. The public payload is unchanged apart from that field, so
`test_ip_safety.py`'s recursive sweep over `ReportOut` still covers the share
route unmodified, and the byte-identical-to-authenticated test still passes.
The share token is still never logged (constraint 7's reasoning applied to a
credential) — `share.token.revoked` and `share.token.refreshed` log the scan id
and nothing else, matching `share.token.minted`.

## Verification

**api 1,065, up from 1,053** — 12 new, all in `test_share_link.py`. web 703,
design-system 418 and shared-types unchanged. `ruff check src tests` clean;
`tsc` clean on all three packages with `api.gen.ts` regenerated for the new
`expiresAt` field. Migration `a6a32760b418` applied, downgraded and re-applied
on `avp_dev`, with `alembic check` clean either side.

## Open, and deliberately not built here

**White-label branding** — the other half of Layer 5, and §7 line 2. It needs
new `Agency` columns and, more importantly, a written policy on which design
tokens an agency may override: the visibility ramp is load-bearing, and an
agency free to recolour it changes what the score means. That is a design
decision rather than a build task, so it is put back rather than picked
silently.

# Epic 9.22 — white-label branding, and the policy that had to be written first

`ReportAgencyOut`'s docstring had been waiting for this since Epic 7: *"Logo,
custom domain and brand colours are §7 line 2 and are deferred to Epic 7.1 —
they need new columns AND a written policy on which tokens an agency may
override, because the visibility ramp is load-bearing: an agency free to
recolour it changes what the score means."* The columns were the easy half.

**Layer 5 (Distribution/Action), Activation phase.** The fork below was put
back to the founder rather than picked, per north-star.md §8.1: a brief whose
premise has no clearly-better default is a design decision, and ticking the
checkbox silently is the wrong trade.

## The policy, and it is short

**An agency may set a logo and ONE colour, and that colour reaches chrome
only.** Everything else in the report's palette is notation rather than
decoration:

| Token family | What it encodes | Overridable |
|---|---|---|
| `--avp-vis-*` | The score itself, on a monotonic lightness ramp that survives greyscale and colour-vision deficiency | **No** |
| `--avp-beacon-*` | The subject **being scanned** — the prospect, not the agency | No |
| `--avp-competitor-{1..5}` | The rival series, neutral so none reads as endorsed or attacked | No |
| `--avp-success/warn/danger/info` | System state | No |
| Masthead mark, letterhead rule | The agency's own identity on the document | **Yes** |

The beacon row is the one worth naming out loud. "The brand on this report" and
"the brand this report is about" are different brands, and a branding feature is
exactly where they get confused.

**The guarantee is a type, not a convention.** `BrandingRequest` has two fields
and no field capable of naming an encoded token — the same discipline
`FixFacts` uses to make page copy unrepresentable rather than merely
discouraged. A test asserts the model has exactly two fields, so adding a third
is a policy change somebody has to make deliberately.

**And the isolation is structural.** `--avp-agency-accent` is set inline on the
masthead element and nowhere above it, so the variable is not in scope for a
single beat of the report; an encoded token could not read it even if a future
stylesheet asked. `AgencyBranding.test.tsx` asserts the colour never appears
after the first beat, never shares an element with an encoded token, and is
never defined on the page root. Both properties were checked by mutation: adding
`avp-vis-high` to the letterhead rule fails the second, and hoisting the
variable to `ReportPage` fails the third.

## Two smaller forks, both decided the conservative way

**The PDF does not get the logo.** `report_pdf.py` was built at zero
dependencies behind an explicit licensing survey — WeasyPrint's required
`Pyphen` is GPL/LGPL/MPL, which ip-safety.md #6 blocks, and React-PDF is a Node
library in a Python process. A logo is the first image the document has ever
needed, and adding one would reopen that survey for a letterhead mark. It would
also mean the server FETCHING an agency-supplied URL to embed it, which is a
different security question from an `<img src>` the reader's own browser
resolves. So the web report carries a logo and the PDF does not, the gap is
named on the route and in `api-contracts.md`, and a test asserts the PDF writer
never learns about either field — so reversing this is a deliberate act rather
than a quiet drift.

**No `custom_domain` column.** A custom domain needs DNS verification,
certificate issuance and request routing before it does anything at all. A
nullable column with none of that behind it is a field that looks built and is
not, and the API would be accepting a value nothing honours.

## The logo is a URL, because there is no asset store

Checked before inventing one: there is no `UploadFile`, no S3 client, no
presigned-URL helper anywhere in the repo. Building the first asset-storage
pattern in the product in order to hold a letterhead mark would be the larger
decision smuggled inside the smaller, so `logo_url` is an absolute `https://`
URL the agency already hosts.

## Validated twice, and neither check is redundant

Both values are agency-supplied and both land on the product's only
**unauthenticated** page. `javascript:` in an `<img src>` is script execution
against every reader of a shared report; `data:` is the same carrying its own
payload; an unanchored colour string in a CSS custom property closes the
declaration and opens another. `schemas/agency.py` rejects a bad value with a
readable message, and CHECK constraints on `agencies` mean one cannot be stored
however it arrived — a script, a fixture, a future endpoint that forgets. They
fail at different times for different audiences, and a test drives the second
directly with raw SQL.

`var(--avp-vis-high)` is in the rejected-colour table for a reason that is not
injection: it is an agency quietly adopting the visibility ramp as its brand
colour, which is the exact confusion the disjointness rule exists to prevent.

**IP-safety check passed:** the visibility, beacon, competitor and semantic
token families are not overridable by any code path — `BrandingRequest` has no
field that can name one (asserted), and the accent variable is scoped to the
masthead subtree so it is out of scope for every beat (asserted, and both
properties mutation-checked). **No new dependency was added** (constraint 6
untouched), which is precisely why the PDF does not carry the logo — the
alternative was reopening `report_pdf.py`'s licensing survey. The logo URL is
**not** a stored-XSS vector: `https://` only, enforced at the schema and by a
CHECK, rendered as an `<img src>` the reader's browser resolves. It is **not**
an SSRF vector either, because nothing server-side fetches it — a property of
the PDF decision, and recorded on the validator so that the day the PDF changes,
whoever changes it knows the validator alone is not enough.

## Verification

**api 1,102, up from 1,065** — 37 new in `test_branding.py`. **web 712, up from
703** — 9 new in `AgencyBranding.test.tsx`. design-system 418 and shared-types
unchanged. `ruff check src tests` clean; `tsc` clean on all three packages with
`api.gen.ts` regenerated. Migration `59c47a7a44f6` applied, downgraded and
re-applied on `avp_dev`, `alembic check` clean either side.

## Open

**Outreach content generation** is untouched and remains §7 Epic 10 — Layer 5's
third piece, and the only one still unstarted.

**The custom domain** and **the PDF logo**, both deferred above with their
reasons. Neither is blocked on a decision now; both are blocked on work whose
size is the argument for not doing it here.

# Two loose ends, and neither was the thing it was written down as

Both items came into this session as known work: a lock-ordering question to
resolve, and an open defect to fix. One turned out to be real but benign; the
other had been fixed three epics earlier and the document saying otherwise was
stale. Recorded together because the pattern is the same one Layer 5's status
had, and this is now the third time a document has been found describing a
state the code left behind.

## 1 — Sharing and the lease do contend, and the reason it is safe is not the one that was guessed

The question was whether `share.py`'s two `SELECT ... FOR UPDATE` sites could
interact with the lease's claim, heartbeat and reaper, all of which write the
same `scans` row. The suggested answer to check for was *"no realistic overlap
— a scan being shared is already terminal"*.

**That answer is wrong, and checking is what showed it.** The share routes have
no status guard at all: a RUNNING scan can be shared and revoked while its
executor is still working. That is deliberate rather than an oversight — an
operator watching a scan run may want to send the link before it finishes — so
the two paths genuinely contend on one row, on a 30-second clock.

The overlap is benign, and **one invariant is what makes it so**:

> every writer of the `scans` row takes exactly that one row, and holds it
> across a flush and a commit with no network call in between.

Both halves carry weight. One resource means a deadlock has no second lock to
form a cycle with, however the two interleave — so no lock ORDER needs
defining, which is why the question does not become its own brief. No network
call in the critical section means the worst case is serialisation in
milliseconds, against a `LEASE` of six heartbeat intervals sized precisely so a
delayed renewal loses nothing.

That second half is not free, and it was nearly not true: `run_scan` used to
hold this row across its whole engine phase, which the lease work had to fix
before a heartbeat could renew at all. So the invariant has now been arrived at
twice from opposite directions, which is the argument for writing it down as an
invariant rather than as a fact about today's call sites. It is on
`services/share.py`, with the thing that would break it named — a future share
path that calls out to an email sender or a link shortener *inside* the lock.

Three tests run a share, a revoke and a reap concurrently against one row and
assert both effects land. They would not catch a network call added inside the
lock, which is why the comment is the part that has to be read.

**Also settled by the same reading:** revocation leaves `lease_expires_at`
alone, and reaping leaves `share_token` alone. Different column families on one
row, and neither predicate mentions the other's columns — so un-sharing a
report does not cost an executor its claim, and finding an executor dead does
not take a working link away from a prospect.

## 2 — The Epic 8 defect was fixed in Epic 9.18; `product-spec.md` had not noticed

`product-spec.md` §7 Epic 8 carried this as an open defect, deliberately not
fixed, needing its own brief: `messages.parse` validates inside the SDK, so a
`pydantic.ValidationError` bypassed the whole `anthropic.*` ladder and the fix
phase raised.

`fix_generator.py` has caught it since `22a3134` ("Epic 9.18: an overlong title
no longer loses the whole fix list"), mapping it to `PROVIDER_SCHEMA_VIOLATION`
— deliberately distinct from a provider error, because the provider answered
and our own schema is what rejected the answer. The test reproduces the
`plausible.io` failure the way it happened, by letting the real validator
reject a real 240-character title rather than raising a hand-built error, on the
stated grounds that a test which raises the exception itself proves only that
the `except` clause is spelled correctly.

So the fix, the distinct outcome code and the faithful reproduction the brief
asked for all already existed, to the letter. **Nothing was built here.** The
checkbox was corrected, with the commit that fixed it named so the next reader
can go and look.

## 3 — Layer 2 is two vendors, not one, and three documents said otherwise

Checked because the next fork depends on it. `ChatGptAdapter` exists, runs
`gpt-5.5` over raw `httpx`, is registered in `ENGINE_REGISTRY`, and is in
`DEFAULT_ENGINES` — so **every scan already runs three engines across two
vendors**. A live call was made against the real API two sessions ago while
verifying `reasoning_effort`, so this is not a paper adapter.

`north-star.md` said the opposite in three places, all now corrected:

* **"SHIPPED for one vendor. This is the layer's defining limitation."** Now
  two vendors and three engines, with Perplexity and Google named as the ones
  that really do remain unbuilt — they are enum members with no adapter, and
  the router refuses them at the request before a scan row exists.
* **"OpenAI, Perplexity and Google keys are provisioned but empty. No adapter
  exists for any of them."** Two thirds still true; the OpenAI third is three
  epics out of date.
* **"Cross-LLM sentiment comparison: NOT BUILT, and currently impossible...
  blocked by Layer 2"** — and this is the correction that matters most, because
  §3.8 leans on it to argue that a table-stakes gap holds the differentiator
  hostage. Sentiment is computed per engine result and `chatgpt` runs beside
  `claude` in the same parametric mode by design, so **the cross-vendor axis is
  in the data today**: `engine_results` carries a sentiment and a confidence per
  prompt × engine, and nothing reads them comparatively. The comparison is
  unbuilt Layer 3 work, not a Layer 2 dependency.

That last one changes what the next brief can be. The clearest differentiator
in the document is not blocked; it is simply not built.

## Verification

**api 1,105, up from 1,102** — three new in `test_share_link.py`, all
concurrency tests against one row. web 712, design-system 418 and shared-types
unchanged; nothing here touches the contract. `ruff check src tests` clean. No
migration.

# Epic 9.23 — where the engines disagree

The fork after Layer 5 closed was between deepening Layer 2 (a third engine)
and building the Layer 3 insight that Layer 2 was supposedly blocking. Checking
the premise dissolved half the fork: **the block was already gone**, and the
correction is the previous entry. So this builds the thing that turned out to
be available rather than the thing that would have unblocked it.

**Layer 3 (Insight), which north-star.md §3.8 names as the differentiation
bet.** The founder chose this over a third adapter and over Epic 12's page-
diffing "why" engine, on the reasoning that it costs nothing extra per scan
because the data is already collected.

## What was already in the database and on no page

Every scan runs each prompt against three engines across two vendors and stores
a mention, a position and a sentiment for each. The report gave **one** mention
rate, **one** sentiment sub-score, and per-engine counts with nothing said about
the difference between them. A brand named by Claude on a question ChatGPT
answers without it — the single most specific thing this product can say, and
the one no single-engine competitor can say at all — was in `engine_results`
and rendered nowhere.

`services/divergence.py` is the reading. It computes each engine's own standing
and the prompts the engines split on, and the report carries both.

## Two rules, and they are what make it honest rather than merely computable

**An engine that did not answer has no opinion.** A rate-limited, timed-out or
truncated call is missing data, not evidence of absence. Counting it as "this
engine did not mention you" would turn every provider outage into a headline
about cross-engine visibility — precisely the failure
`EngineResultStatus.ANSWERED_NO_MENTION` and `ScanStatus.PARTIAL` already exist
to prevent. Every function filters on `answered` before comparing, and a prompt
with fewer than two answering engines is not a disagreement, it is a gap in
coverage. An end-to-end test times ChatGPT out on every prompt and asserts it
appears on the missing side of no split and is given no invented sentiment.

**"They agreed" and "there was nothing to compare" are different claims.** A
scan where one engine answered agrees with itself trivially, and reporting that
as consensus would make the strongest available claim from the weakest
available evidence. `agreementRate` is null in that case rather than 100, and
the UI renders the absence in words instead of a percentage.

## One source per number

`engine_standings` calls `scoring.mention_rate` and `scoring.sentiment_score`
on a filtered slice of the same facts the composite is built from. A second
implementation would eventually disagree with the headline and the disagreement
would reach a client — the reasoning `fix_generator` gives for not re-deriving
Epic 7's gap formula. A test asserts each standing equals the scoring function
run on that engine's own slice, so the two cannot drift.

`ResultFacts` gained `prompt_id` rather than a parallel value object being
introduced: the divergence reading needs the facts regrouped BY PROMPT and
scoring does not, and two objects describing one row is two places for a field
to be forgotten.

## An IP-safety guard fired, and was tightened rather than shortened

`test_report_service_never_reaches_for_answer_text` forbids the substring
`.answer` in the report service, to stop a report reaching for an engine's
prose. `standing.answered` — a COUNT of results that came back — matched it.

The guard was made boundary-aware rather than having the entry removed:
`.answer`, `.answer.text` and `.answer)` all still trip, because none continues
the word. Removing the entry would have deleted a real guard to fit new code.
A second test now asserts the pattern against sample source in both directions,
so the guard's strength is pinned independently of whether today's module
happens to pass it. Worth recording because a failing safety test is the moment
where the tempting move and the correct one diverge.

## Fixtures, and one that had to be constructed

`packages/shared-types/fixtures/report-cases.json` and the web fixtures gained
the block, derived from each fixture's own coverage and shelf rather than
invented. The real `helpscoutReport` turned out to have **both engines agreeing
on all three prompts** — genuine consensus, and useless for rendering the case
this feature exists for. So `divergentReport` is constructed and labelled as
constructed, with the shape the projection produces: engines that answered on
both sides of every split, never one that failed. What is synthesised is the
data, not the schema.

The web fixtures are cast `as unknown as Report`, which is why adding a
required field to the contract did not fail `tsc` — worth knowing, because it
means the fixture file's types are documentation rather than a check.

## Verification

**api 1,125, up from 1,105** — 20 new across `test_divergence.py` (pure rules),
`test_report_endpoint.py` (three end-to-end, including the outage case) and
`test_ip_safety.py` (the guard's own guard). **web 720, up from 712** — 8 new,
weighted toward the two refusals. design-system 418 and shared-types 53
unchanged. `ruff check src tests` clean, `tsc` clean on all three packages with
`api.gen.ts` regenerated, `alembic check` clean with no migration.

## Open

**Per-engine sentiment is computed but thin on real data.** Every fixture
carries `sentiment: null` per engine because the stored fixtures predate the
column being read this way; the live path computes it. A live run would be the
honest way to see whether the sentiment axis diverges as sharply as the mention
axis, and that has not been done.

**A third engine** (Perplexity or Gemini) remains the Layer 2 catch-up option,
now genuinely optional rather than blocking. **Epic 12's "why" engine** is
untouched.

# Epic 9.24 — the first live cross-engine run, and it says the opposite of what was built

Epic 9.23 shipped the cross-engine reading and verified it against a scripted
disagreement. This ran it against a real subject with real model calls, because
every fixture in the repo carries `sentiment: null` per engine and nobody had
looked at what the live path actually computes.

**A measurement, not a build.** One wording defect was fixed because the run
proved the page contradicted itself; nothing else was tuned, and `divergence.py`
is untouched.

## The run

`scripts/verify_e2e.py --url front.com --prompts 12`, against `avp_dev`, all
three engines across both vendors. Front is a real shared-inbox company in the
same category as the Help Scout data already on record, so the numbers have
something to sit beside. A first attempt against `basecamp.com` stopped
instantly and cost nothing: the harness refuses to reuse an existing client, by
design, so the checked-in verification rows stay intact.

    scan     : succeeded, 12 prompts, 36 results, 0 failures
    score    : 55.29 composite, 5 competitors, NO_AUTHORITY_DATA
    audit    : partial, technical foundation 72.67
    wall     : 306.4s against a 300s budget

## The finding, and it inverts the feature's own emphasis

    engine           answered  named   rate     sentiment
    chatgpt              12      12    100.00     75.00
    claude               12      12    100.00     58.33
    claude_search        12      12    100.00     41.67

    comparable prompts : 12
    split prompts      : 0
    agreement rate     : 100.00

    prompts where 2+ engines named the subject and both were scored : 12
    of those, engines that disagreed on SENTIMENT                   :  8

**Every engine named Front on every prompt. Not one split.** And on two thirds
of those same prompts the engines disagreed about the tone, across a
33-point spread from ChatGPT's 75.00 to grounded Claude's 41.67 — with grounded
Claude consistently the harshest and ChatGPT consistently the kindest.

Epic 9.23 was built around the split prompt: the buyer question one assistant
answers with the subject and another answers without. On the first real subject
that finding was empty, and the axis nobody built a headline for carried
everything. Stated as the pitch it implies: not *"you are invisible on ChatGPT"*
but **"every assistant names you, and they do not agree on what you are like"** —
a narrower claim, and a different product conversation.

**One run, one subject, and the subject explains a lot of it.** Front is a
category leader; a 100% mention rate across three engines is what a well-known
brand should produce, and it saturates the visibility axis so completely that
this run cannot say whether splits are rare in general or merely rare *here*.
That is the honest reading and it is the reason the fork below stays open.

## What the real output changed

**A wording defect, and the page disproved itself.** With zero splits the copy
read *"Every engine that answered gave the same verdict on Front"* — rendered
directly above standings of 75.00, 58.33 and 41.67. The splits measure one
thing, whether each engine named the subject, and the sentence spoke for the
tone as well. It now says what was actually compared and hands the tone to the
standings beneath it. Fixed because a report may not make a claim its own page
disproves, which `ReportView.test.tsx`'s "makes no claim it cannot support" is
already the rule for; a regression test reproduces the exact shape.

**The fixtures hid the live axis.** Every stored fixture carries a null
per-engine sentiment, so the one number that turned out to matter was rendered
in tests and never seen. A fixture that is null where production is populated
tests the layout and nothing else.

## Tokens and money — the first real figure in this project

`north-star.md` §5.1 has wanted a per-scan cost since it was written, and every
run before this recorded call counts and never token usage, so the dollar
figure was an estimate times a guess. The harness now meters the providers' own
reported usage at the SDK boundary — where they bill from — rather than at our
call sites, for the reason `install_meters` already gives: a per-site wrapper
measures the sites the script remembered.

    model              calls        input      output          $
    claude-opus-5         67      300,520      43,400     2.5876
    gpt-5.5               12          261      12,275     0.3696
    ------------------------------------------------------------
    model spend                                           2.9572

    plus 21 Anthropic web_search requests   (rate not recorded in this repo)
    plus  6 SerpApi searches                (of a 250/month quota)

**$2.96 in model spend for a 12-prompt scan**, or $0.246 per prompt. A
24-prompt scan's loop roughly doubles the variable half; setup is fixed, so
$5.91 is an upper bound on the naive extrapolation rather than a measurement,
and it is labelled as one.

Two details worth keeping. **The input side dominates**: 300,520 Claude input
tokens against 43,400 output, because grounded search injects retrieved pages
and every sentiment call carries an answer back in. And **gpt-5.5 is
lopsided the other way** — 21.8 input tokens per call against 1,023 output —
which is what a parametric engine answering a short question looks like.

The rates are read, not assumed: Anthropic's $5/$25 was already implicit in the
repo, and gpt-5.5's $5/$30 came off OpenAI's pricing page on 2026-09-08. The
lookup matches by longest prefix because providers report the dated snapshot
they served (`gpt-5.5-2026-04-23`), and an exact-key table would have priced
every OpenAI call at zero — found before the first run rather than after.

**The two counters agree exactly.** The call meter and the token meter reached
67 Anthropic calls independently: 24 engine + 36 sentiment + 1 classification +
4 co-citation + 1 prompt generation + 1 fix generation. That agreement is the
evidence the token figure is complete rather than a sample.

## Timing, recorded without acting on it

306.4s against the 300s budget, **at half the prompt count Epic 9.1 measured
498.2s for**. The scan loop is 75% of it, with a 154.0s engine-bound floor and
76.6s (33%) unexplained — the same shape Epic 9.1 found and the same open
question. The slowest single engine call was 112.0s against a 24.1s median.
Noted, not chased: this run was commissioned to read the cross-engine output.

## Which direction this points — and it is honestly not settled

**Neither of the two open directions is chosen, and n=1 is the reason.**

The visibility axis produced no signal at all, which argues against widening it
with a third engine — but it produced no signal on a subject where none should
be expected, so the argument is weak. The tone axis produced a large signal and
is already available with the two vendors in hand, which argues against needing
more engines at all, and does not by itself argue for Epic 12's "why" engine.

**The measurement that would settle it is specific**: one more run against a
subject that is NOT a category leader — a brand plausibly absent from some
answers. If splits stay near zero there too, the visibility axis is saturated
in general and a third engine buys little; if splits appear, the axis is live
and the case for widening it is real. That is one run, and it should be made
before either direction is scoped.

## Verification

**web 721, up from 720** — one regression test for the wording defect. api
1,125, design-system 418 and shared-types 53 unchanged; nothing here touched
the API. `tsc` clean on all three packages. The harness gained a token meter, a
rate table with both vendors' published rates and their sources, and a
cross-engine readout — script-only, no production module edited, which is the
discipline `verify_e2e.py` was built on.

## Open

**The per-engine sentiment spread has one observation.** Whether grounded
Claude is systematically harsher, or Front simply reads that way to it, needs
more than one subject.

**The Anthropic `web_search` request rate is not recorded in this repo**, so 21
requests are counted and left unpriced rather than guessed. It is the last
unpriced line in the cost table.

# Epic 9.25 — a second live run, and the mechanism does fire

Epic 9.24 ran the cross-engine reading against `front.com` and got zero
visibility splits. That was either a finding about the engines or an artifact
of testing a category leader, and the entry said so and named this run as the
thing that would settle it. It mostly does.

**Subject: `missiveapp.com`.** Missive is a real shared-inbox product competing
against Front, Help Scout, Intercom and Zendesk — the same category as both
previous subjects, deliberately, so that **only brand prominence varies and
category is not a second variable**. Same scale, 12 prompts, so the two runs
differ in one thing. Checked before spending: the site returns 200 with real
content, and it was not already in `avp_dev`.

## Both runs, side by side

                                front.com        missiveapp.com
    ------------------------------------------------------------
    chatgpt        named          12 / 12          11 / 12
                   rate           100.00            91.67
                   sentiment       75.00            68.18
    claude         named          12 / 12          12 / 12
                   rate           100.00           100.00
                   sentiment       58.33            75.00
    claude_search  named          12 / 12          12 / 12
                   rate           100.00           100.00
                   sentiment       41.67            87.50
    ------------------------------------------------------------
    comparable prompts                12               12
    SPLIT prompts                      0                1
    agreement rate               100.00            91.67
    sentiment disagreements       8 / 12           5 / 12
    sentiment spread              33.33            19.32
    composite                     55.29            58.72
    wall clock                    306.4s           307.1s

## The mechanism fires, and front.com's zero was the artifact it looked like

**One split in twelve, and it is exactly the shape the feature was built for:**
a buyer question both Claude engines answered naming Missive and ChatGPT
answered without it. On a category leader that never happened; on a
second-tier product in the same category it did. So the split-prompt mechanism
finds real signal on a subject where it plausibly should, and Epic 9.24's zero
is now explained rather than merely suspected.

**And one in twelve is thin.** 91.67% agreement is not a rich vein, and it is
one prompt on one subject. What these two runs establish is that the mechanism
is not dead — not that it is load-bearing. A pitch built on "we find the
questions where you are invisible on one assistant" is supported by an
existence proof and nothing stronger yet.

## The correction: grounded Claude is NOT systematically harsher

Epic 9.24 recorded, carefully, as an open question with one observation:
*"Whether grounded Claude is systematically harsher, or Front simply reads that
way to it, needs more than one subject."* It now has a second subject, and the
answer is **no — the ordering reversed completely.**

    front.com        chatgpt 75.00  >  claude 58.33  >  claude_search 41.67
    missiveapp.com   claude_search 87.50  >  claude 75.00  >  chatgpt 68.18

Kindest and harshest swapped ends. Whatever drives the per-engine sentiment
number, it is a property of the subject-and-engine pair rather than a standing
disposition of the engine, and any copy that said "ChatGPT is the generous one"
would have been wrong by the second scan. This is the clearest thing two runs
bought that one could not, and it is a correction to this log's own previous
entry rather than to the code.

**What survives both runs is that engines disagree about tone at all** — 8 of
12 and 5 of 12 prompts, on subjects where visibility was near-unanimous. That
is the stable finding. Its direction is not.

## Cost: a range now, not a figure

    run              model $   web search $     TOTAL     per prompt
    front.com         2.9572        0.2100     3.1672        0.2639
    missiveapp.com    2.8904        0.2200     3.1104        0.2592

**$3.11–$3.17 for a 12-prompt scan, ~$0.26 per prompt, twice.** Tighter than
expected given the subjects differ, because the cost is dominated by the fixed
shape of the pipeline rather than by anything about the subject.

The last unpriced line is closed: Anthropic bills the `web_search` tool at
**$10 per 1,000 searches** (platform.claude.com's web-search-tool page, read
2026-09-08), so 21 and 22 requests are $0.21 and $0.22. Nothing in the cost
table is a footnote any more.

**The meters were checked rather than trusted, and they reconcile exactly.**

* `gpt-5.5` priced automatically this run — evidence the longest-prefix lookup
  added in Epic 9.24 does match the dated snapshot the API actually returns.
  That fix was made before it could go wrong; this is the run that proves it.
* Both runs' token-meter call counts equal a hand count from the pipeline's
  own shape: 24 engine + N sentiment + 1 classification + 4 co-citation + 1
  prompt generation + 1 fix generation. 67 and 66, matching.
* And the subtle one. Missive made **35** sentiment calls, not 36, because
  sentiment is only spent where the subject was named — and the one result
  that named nobody is the same result that produced the one split. Two
  independent parts of the system agreeing on a single event is better
  evidence the measurement is sound than either number alone.

## Timing, again recorded and again not chased

307.1s against the 300s budget, within 0.7s of the previous run at the same
prompt count. The scan loop is 66% of it with 28% unexplained overhead, against
75% and 33% last time — the same shape Epic 9.1 found. Competitor detection
took 38.1s against 17.9s, entirely in SerpApi (34.8s against 17.5s), which is
subject-dependent and not a regression.

## Is the fork decided? No, and here is what is missing

**Neither direction is chosen, and two runs are not enough.** Stated plainly
because the alternative is reading a decision into evidence a careful reader
would not accept.

* **The split axis is real but thin.** 0 and 1 out of 12. A third engine would
  add chances for splits to appear, but nothing here suggests they are common
  enough that widening the field is the highest-value move.
* **The sentiment axis is strong and unstable.** It fires on two thirds and
  then five twelfths of prompts, which is a product — but its direction flipped
  between subjects, so the claim it supports is "they disagree", not "this one
  dislikes you". That is a weaker pitch than it looked after one run.
* **And the sentiment finding does not map onto either open direction.** Epic
  12's "why" engine is page-diffing against competitors, which answers *why am
  I not named*. Nothing in it answers *why does one assistant describe me worse
  than another*. If tone is the product, the epic that serves it has not been
  written down yet, and noticing that is worth more than picking between two
  epics that were.

**What a third run needs to show.** Both subjects so far scored **at or above
91.67% on every engine** — the low-visibility region is entirely untested. A
subject genuinely absent from some answers, mention rate well below 90% on at
least one engine, would say whether splits scale with obscurity. If they do,
the split axis is worth widening and a third engine follows. If a barely-known
subject still comes back near-unanimous, the split mechanism is a rarity to be
rendered honestly rather than a headline, and tone is the product.

## Verification

No code changed. The harness gained the `web_search` rate and now totals
provider spend rather than listing searches unpriced. api 1,125, web 721,
design-system 418, shared-types 53 — all unchanged, and nothing in this session
touched them.

## Open

**The low-visibility region**, above — the one measurement that would move the
fork.

**Why the per-engine sentiment number moves.** Two subjects, two orderings, no
explanation. Whether it tracks grounding, recency, the competitor set, or
something about the subject's own copy is unknown and would need a different
kind of investigation from a scan.

# Epic 9.26 — the third live run, and the subject was chosen by measurement

Epic 9.25 named the gap: both prior subjects scored at or above 91.67% on every
engine, so the low-visibility region was untested, and a subject *"genuinely
absent from some answers"* was the one measurement that would move the fork.

**The subject was screened rather than guessed**, which is the methodological
change from the first two runs. Runs 1 and 2 picked "smaller player, same
category" by judgement and both landed at 91.67% or above — an undershoot big
enough that repeating the method would have wasted a third scan. So four
candidates were screened first on **awareness-shaped prompts only**, using the
production `ask_all` + `extract_facts` path with one engine, four calls each:

    Gmelius     3/4 named
    Zammad      1/4 named
    Helpwise    0/4 named
    Richpanel   0/4 named

`zammad.com` was chosen at 1 of 4: genuinely low visibility without being
invisible, which is where disagreement should live, on a real established
open-source help desk whose site sustains classification and an audit. Its name
is also not a common English word, which matters because a mention is a text
match. The screen cost about ten model calls and is the cheap pre-check the
brief asked whether existed — it does, out of production code, with no new
scaffolding.

## Three runs, side by side

                             front.com     missiveapp.com     zammad.com
    ----------------------------------------------------------------------
    chatgpt        rate         100.00           91.67           91.67
                   sentiment     75.00           68.18           68.18
    claude         rate         100.00          100.00           91.67
                   sentiment     58.33           75.00           77.27
    claude_search  rate         100.00          100.00           77.78
                   sentiment     41.67           87.50           78.57
    ----------------------------------------------------------------------
    answered (of 12 x 3)         36/36           36/36           33/36
    split prompts                    0               1               1
    agreement rate              100.00           91.67           91.67
    sentiment disagreements     8 / 12          5 / 12          4 / 11
    composite                    55.29           58.72           53.25
    wall clock                   306.4s          307.1s          330.8s
    provider spend             $3.1672         $3.1104         $2.8646

**The deliberately low-visibility subject still came back at 91.67% agreement**
— the same number as the mid-tier subject, with one split again. Its mention
rate did drop (77.78% on grounded Claude, the lowest any engine has scored
across three runs) and it is the first run to record engine FAILURES: three of
36 results, all `claude_search`, so it answered 9 of 12 rather than 12.

Sentiment divergence appeared a third time, 4 of 11. And the ordering moved a
third time — grounded Claude went 41.67 → 87.50 → 78.57 across the three
subjects, so Epic 9.25's refutation of "grounded Claude is systematically
harsher" holds with a third point rather than being a two-point coincidence.

**Cost is now a three-run range: $2.86–$3.17 for a 12-prompt scan**, and this
is the first run whose harness printed the complete total itself rather than
leaving a line for hand computation, because the `web_search` rate landed
before it started.

**What this run did NOT settle** is in the next entry, and it is not a detail
about the subject.

# Mention Rate may be measuring the prompt, not the engine — sized, not fixed

**Layer 5's own bet, and it outranks the cross-engine fork.** Mention Rate is
30% of the composite, the highest-weighted sub-score in the product, and it is
on every score every real client has been shown. The fork between a third
engine and Epic 12 is paused, not abandoned, until this is decided.

**This session changed nothing.** `scoring.py`, `divergence.py` and the formula
are untouched. The point was to find out how big this is before anyone decides
what to do about it.

## The mechanism, and it is by design rather than by accident

`prompts.py`'s generator is *instructed* to name the brand:

> *"Most questions must NOT contain the subject brand's name. A question that
> names the brand can only confirm the brand exists; it cannot reveal whether
> the brand gets discovered. **Name the brand only in comparison and
> bottom-funnel questions where a buyer plausibly would.**"*

So the generator's author had already reasoned this through, wrote the reason
down in the system prompt, and deliberately confined brand-naming to two of the
three intents — which `INTENT_QUOTA` sets at 35% comparison plus 20%
bottom-funnel, **55% of every set by design.**

**The insight is in `prompts.py` and never reached `scoring.py`.**
`mention_rate()` filters on `answered` and nothing else; no caller filters by
`PromptIntent`. One module states that these prompts cannot reveal discovery,
and another measures discovery using them.

That makes this structural and permanent, not an artifact of what an LLM
happened to produce on two runs.

## How big: every scored scan in `avp_dev`, read-only

**The pooled correlation, across all 753 answered prompt × engine rows in 14
scored scans with generated prompt sets:**

    prompt CONTAINS the brand name : 255 / 256 mentioned = 99.61%
    prompt does NOT                : 386 / 497 mentioned = 77.67%
                                                    gap = 21.94 points

**Two hundred and fifty-six rows where the prompt named the brand, and 255
registered a mention.** The single exception is `psmdigitalagency.com` on
`claude`, for *"psm digital agency reviews from real clients"* — the one time
in 256 an engine answered a brand-named question without echoing the brand.

Confirmed independently against a brand that cannot exist: asked *"is zorblex
inbox worth it or should i just pay for front"*, Claude answered *"I don't have
any knowledge of a product called Zorblex Inbox"* and the pipeline recorded a
**mention at position 1**. The awareness-shaped control returned no mention on
all three engines.

## What it does to the score

Mention Rate on the non-awareness rows is **exactly 100.00%** for eight of the
fourteen scans. Recomputed awareness-only, against the stored value:

    domain                    stored   awareness      delta
    ----------------------------------------------------------
    zammad.com                 87.88       63.64     -24.24
    psmdigitalagency.com.au    21.43        0.00     -21.43
    psmdigitalagency.com       18.75        0.00     -18.75
    basecamp.com               87.23       75.00     -12.23
    missiveapp.com             97.22       91.67      -5.56
    notion.so                  97.22       94.44      -2.78
    front.com / linear.app    100.00      100.00      +0.00
    ghost.org / plausible.io    ~98.6      ~100.0    +1.4 max

**And it reaches Share of Voice too**, which was worth checking rather than
assuming: `share_of_voice` counts every brand appearance, and a comparison
prompt naming two products tautologically produces a hit for both. Awareness-
only it falls on 12 of 14 scans, by 5 to 29 points — `psmdigitalagency.com`
from 25.71 to **0.00**, `zammad.com` from 39.73 to 20.59.

**On the composite, with both sub-scores recomputed:**

    swing across 14 scans : min -13.79, max +0.83, mean -4.22 points

    psmdigitalagency.com.au   29.10 -> 15.31   -13.79
    zammad.com                53.25 -> 41.19   -12.06
    psmdigitalagency.com      28.89 -> 16.84   -12.05
    front.com                 55.29 -> 53.61    -1.68

**The error is largest exactly where the score matters most.** A well-known
brand scores about the same either way — Front moves 1.68 points, because it is
genuinely named on awareness prompts too. The subjects that move 12 to 14
points are the ones the product exists to help: `psmdigitalagency.com` scores
**0.00% mention rate on awareness prompts** — completely undiscoverable — and
is shown a composite of 28.89 built substantially on questions that named it.
A real agency being told it has *some* visibility when it has none is the
failure mode with a client on the other end of it.

## A note on the cross-engine entries, which are not edited

`divergence.analyse()` reads the same `mentioned` field, so it has the same
exposure — and it is measurable:

    scan             scope          comparable  splits  agreement
    front.com        all prompts            12       0     100.00
                     awareness               4       0     100.00
    missiveapp.com   all prompts            12       1      91.67
                     awareness               4       1      75.00
    zammad.com       all prompts            12       1      91.67
                     awareness               4       1      75.00

**Every split in all three runs occurred on an awareness prompt; not one
occurred on a brand-named prompt.** So the reported 91.67% agreement is 75.00%
on the rows that could disagree, and the comparable denominator of 12 was
really 4. Epics 9.23–9.26 stand as written and are the record of what was
measured at the time; this is the note that says how to read them.

It also sharpens Epic 9.25's question. Splits were called "thin" at 1 in 12.
They are 1 in 4 on the prompts capable of showing one — still a small sample,
but a materially different rate, and any conclusion about widening engine
coverage should be drawn against the corrected denominator.

## The fork, put back rather than answered

**What should Mention Rate measure?** Two legitimate products, different
numbers, and `scoring-spec.md` does not currently distinguish them:

* **Unprompted recall — awareness only.** "When a buyer asks who does this,
  does the engine name you?" The harder, more honest claim, and the one the
  generator's own system prompt implies. It discards 55% of every prompt set
  from this dimension and would move real scores by up to 14 composite points.
* **Answerability — everything, as today.** "Does an engine say something about
  you when asked?" A real property, but 99.61% of the time the answer is yes,
  so it separates almost nobody — and it counts an engine denying knowledge of
  a brand as a mention of it.

A third shape exists and nobody has proposed it: keep both, scored separately,
so a report can say *"you are answerable but not discoverable"* — which is
precisely `psmdigitalagency.com`'s situation and is invisible in one number.

**Not decided here, deliberately.** It is the same class of decision as the
branding-token policy and the lease granularity, and it now has numbers
attached rather than a description.

**If it changes, it is a `formula_version` change, not a patch.**
`scoring-spec.md` rule 5 requires one precisely so before-and-after reporting
does not silently break, and 14 stored scores were computed under the current
definition. Whether they are re-scored, re-labelled, or left as history is part
of that decision and is out of scope here.

## Confirmed vs. assumed

**Confirmed by reading code:** the generator is instructed to name the brand in
two of three intents; `mention_rate` and `share_of_voice` filter only on
`answered`; `find_brand` is a text match; `divergence.analyse` reads the same
field.

**Confirmed by measurement:** the 99.61% / 77.67% split over 753 rows; the
per-scan deltas; the composite swing; that every split sits on an awareness
prompt; that a nonexistent brand registers a mention on a brand-named prompt.

**Assumed:** that awareness-only is the right corrected comparator. It is the
obvious one and the generator's own prose implies it, but choosing it is the
decision above, not a finding. Every number here is reported as "what awareness
-only would give", never as "the correct score".

# Scoring v2 — the requirement already said what Mention Rate is for

The previous entry sized the tautology and deliberately did not choose between
three shapes for Mention Rate, because the decision needed numbers attached.
The numbers now exist. So does an answer this project committed to before any
of it was measured, and finding that is what made this an implementation rather
than a design session.

## The decision was already made, in Epic 0, and never carried through

`product-spec.md`'s Epic 0 working assumptions, reconciled 2026-08-20 and
verified verbatim this session rather than taken on trust:

> **Two jobs:** (a) prospecting — **prove to a stranger they are invisible in
> AI answers**; (b) retention — prove to an existing client that the work is
> moving the number.

Invisibility is an unprompted property. A question that names the brand cannot
evidence it, whatever the answer says.

And `prompts.py`'s generator had already reasoned exactly this out, in its own
system prompt:

> *"A question that names the brand can only confirm the brand exists; it
> cannot reveal whether the brand gets discovered. Name the brand only in
> comparison and bottom-funnel questions where a buyer plausibly would."*

**The requirement and the prompt generator already agreed. Only `scoring.py`
was out of step**, counting all three intents into a dimension that means
discovery. That reframes the whole thing: not a design decision to be taken
from a blank slate, but a decision taken in Epic 0 that never reached the one
function that needed it.

So the three shapes the previous entry laid out were not equally open.
Awareness-only is what the stated job requires.

## What changed

`compute_score` scopes **Mention Rate and Share of Voice** to awareness
results. `FORMULA_VERSION` is `v2`.

**Share of Voice was decided on its own terms, not by analogy.** The prior
entry showed the tautology reaches it; the question was whether that is a
defect or a signal. It is a defect, because the inflation is directional: a
comparison prompt names the subject and usually one rival, so the subject takes
a guaranteed hit while the other competitors appear only if the engine
volunteers them, pulling the ratio toward `1/(1 + named)` regardless of real
standing. The genuine counter-argument — a buyer weighing named options is a
competitive signal — does not apply to this implementation, which counts
PRESENCE, not airtime. One hit per brand however much the answer says about it.
An airtime-weighted Share of Voice would deserve the argument re-opened, and the
comment in `scoring.py` says so.

**Sentiment and Citation Strength keep every answered result, deliberately, and
this was measured rather than assumed.** Sentiment asks how an answer PORTRAYS
the brand, which is real whether the buyer named it or not. And the direction
matters: awareness-only sentiment is **higher** on every scan with a population,
by +1.03 to +25.86 points. Scoping it would raise scores rather than correct
them — engines recommend a brand they surface unprompted and hedge about one
they are forced to discuss, which is an interesting finding in its own right and
a different decision with its own evidence. Named as open below.

## Two design points that were not incidental

**The population lives in `compute_score`, not inside `mention_rate`.** The rate
functions answer "what fraction of THESE results mention the subject", which is
a calculation; which results belong is scoring policy. Keeping them apart also
mattered concretely: `services/divergence.py` calls `mention_rate` on its own
per-engine slices, so folding the filter into the rate would have silently
re-scoped the cross-engine track this session was told not to touch. It is
untouched, and a test pins that separation.

**`compare_competitors` uses the same population**, which was not in the brief
and is not optional. Epic 7's report renders `score.mentionRate` and each
`competitor.mentionRate` in **one column of one table**. Scoping only the
subject would have put two populations in that column — and biased in a
specific direction, since the client's figure would drop while every rival's
kept the inflated one. A client made to look worse than its competitors by an
accounting mismatch is a worse defect than the one being fixed.

## The edges

**No awareness prompts excludes and redistributes**, with
`NO_AWARENESS_POPULATION`, rather than scoring 0 — the treatment v1.1 already
gave Share of Voice with no competitor set, for the same reason: a client must
not be punished for the shape of a prompt set they did not choose. Unreachable
through the generator (45% quota) and `fallback_prompts` (five awareness
shapes); confirmed no prompt set in `avp_dev` lacks awareness prompts. It
guards a hand-built set.

**The intent joins the inputs digest** under rule 1. It now selects the
population, so it can move the composite, and a value that moves the composite
while being invisible to the fingerprint makes a changed score unattributable —
the same omission `technical_foundation` was added to fix.

## What was NOT done

**The 14 stored `v1.1` scores are not re-scored.** They are accurate records of
what that definition produced, and rule 5 exists precisely so before/after ROI
reporting compares like with like. **Whether to offer a re-score action to
existing clients is a product decision and is explicitly open** — it is not
decided here in passing, and it has a real edge: a client shown 28.89 and later
16.84 for the same scan needs that explained, not silently swapped.

**`divergence.py` is untouched**, and the note in the previous entry stands.
One consequence is now visible on the report and should be said out loud: the
composite's Mention Rate is awareness-only while the cross-engine standings
report per-engine rates over all prompts, so a reader will see per-engine
figures higher than the headline. They are different populations, correctly
computed, and nothing labels them as such. **Follow-up.**

**The "answerable but not discoverable" surface is not built.** `364678d` named
the case — `psmdigitalagency.com` at 0% awareness mention rate with real
answerability — and it is a genuine second fact worth showing, the way
`degradation_flags` shows facts that do not move the composite. Deliberately
not started: it is a report-surface change, not a scoring one. **Follow-up.**

## Verification

**api 1,133, up from 1,125** — 8 new in `test_scoring.py`, including the
Zorblex regression: a comparison-intent result whose engine denied all
knowledge of the brand must not reach Mention Rate. Two endpoint tests that
hard-coded `v1.1` now assert the CURRENT version, with the literal pinned in
one place so a bump stays a deliberate edit. `ruff check src tests` clean, the
OpenAPI contract is untouched, `tsc` clean on all three packages, no migration.

## Open

* **Whether to offer re-scoring** for the 14 stored v1.1 scores — product.
* **Whether Sentiment should be scoped too**, given it would raise scores by up
  to 26 points and asks a different question.
* **The population mismatch between the composite and the cross-engine
  standings**, now visible on the report.
* **The "answerable but not discoverable" secondary fact.**
* The third-engine / Epic 12 fork stays paused, and the corrected split
  denominator from the previous entry applies to it when it resumes.

# The two mention rates are now labelled, not reconciled

Scoring v2 scoped the composite's Mention Rate to unprompted questions and
deliberately left `divergence.py` counting the whole prompt set. That was the
right call twice over — the cross-engine reading is *about* the engines, and
the whole prompt set is what makes it that — but it put **two numbers on one
page, both labelled mention rate, both correct, differing for a reason nothing
disclosed.** Live on every report since `c0864e3`, not a future risk.

Closes the third open item from the scoring v2 entry.

## Disclosure, not homogenization

Making the standings awareness-only would have deleted the thing the section
exists to show. So the fix is a paragraph under the standings — the point where
a reader who has just read the headline score above would notice the figures
below are higher:

> *These per-engine figures are not the Mention Rate in the score above, and
> will usually differ from it. The score counts only questions that did not
> name {subject} — the ones that can show whether a buyer discovers the brand.
> The figures here count every question in the set, including those that named
> {subject} and which an engine will nearly always echo back.*

The last clause is the one doing the work. "Different populations" tells a
reader nothing; "questions that named you, which an engine echoes back" tells
them why the lower number is the honest one. A test asserts that clause
specifically, so a future tidy-up cannot reduce it to the vaguer sentence.

**Copy, not schema.** `report_pdf.py` does not render the cross-engine section
at all — checked rather than assumed — so a `CrossEngineOut` field would have
been a machine-readable distinction with no machine to read it. The Epic 9.14
"one source per number" discipline is not at risk here because there is no
second renderer to drift. If the PDF ever grows this section, the field becomes
worth adding and this is the note that says so.

## The excluded case needed its own sentence

Scoring v2 can exclude Mention Rate entirely (`NO_AWARENESS_POPULATION`, when a
prompt set has no unprompted questions). Then the standard sentence points at a
number that is not on the page. So the component branches on whether the
headline dimension is included, and the other branch says the score has no
Mention Rate *because* there was nothing unprompted to measure, and that these
figures are not a substitute for it.

## Two codes were already reaching readers as raw identifiers

`ReportView` falls back to the code itself when copy is missing —
`DEGRADATION_FLAG[flag] ?? flag` renders the identifier as a bullet. Checking
that scoring v2's new code had copy turned up that **`NO_SENTIMENT_POPULATION`
has been emitted since Epic 5 with no entry at all**, so a scan where the brand
was never named has been printing that string to clients. Not introduced here;
found here, and fixed here, because leaving a known machine identifier on a
document that goes to a prospect's CMO is not a thing to note and walk past.

Both now have copy, and `copyCoverage.test.ts` asserts every code the scoring
engine can emit has a label and a sentence — plus that no sentence merely
echoes its own code, which is the shape a placeholder takes when someone
satisfies the first check without doing the work.

**The guard's weakness is stated in the guard.** Its list mirrors the literals
in `scoring.py` by hand and nothing enforces the mirror, so a code added there
and not here still ships. It guards against forgetting the copy, not against
forgetting the file. Deriving it needs the codes in the shared contract, which
is more than this defect warrants today.

## And the dimension descriptions still described the retired definition

`DIMENSION_MEANING.mention_rate` read *"How often the brand is named at all
when a buyer asks"* — which is v1.1's scope, the one v2 replaced, still
sitting in the string table a week later. Corrected, along with Share of Voice.
Worth recording that this is the second document scoring v2 left stale after
`scoring-spec.md`: a definition change has more surfaces than the function it
starts in.

## Verification

**web 739, up from 721** — 4 render tests for the two wordings, 14 in the new
coverage guard. api 1,133, design-system 418 and shared-types 53 unchanged, and
nothing outside `apps/web` was touched. `tsc` clean on all three packages, no
API or contract change.

## Open, unchanged from the scoring v2 entry

Three of the four remain, and no fifth is added: **re-scoring the 14 stored
v1.1 scores** (product, waiting on the founder), **whether Sentiment should be
scoped too** (it would raise scores by up to 26 points), and **the "answerable
but not discoverable" secondary fact**. The third-engine / Epic 12 fork stays
paused.

# The three open items from scoring v2, closed

One entry rather than three, because the items only make sense against each
other: item 1 is what to do about the scores v2 changed, item 2 is what v2
deliberately did not change, and item 3 is the fact v2's single number cannot
carry. Three commits, because they share no files and no logic.

## 1 — Re-scoring: the route already existed; the disclosure did not

**`POST /scans/{scanId}/score` already re-scored and inserted rather than
overwrote.** Rule 5's `(scan_id, formula_version)` unique constraint was
designed for exactly this, and the endpoint's docstring said so. Checked before
building, and nothing was built — the item was not "add re-scoring", it was
"decide whether to use it and make the result legible".

The half rule 5 does not cover on its own is the reader. A client who saw 28.89
last week and 16.84 today has one reading available without being told, and it
is a claim about their business. The true one is a claim about our formula. So
`ScoreDetailOut` gained `previousFormulaVersions` — the other versions this
scan carries, empty for the scans scored once, which is most of them —
populated on all three surfaces that show a score so the note cannot appear on
one and not another. The score beat renders it only when a re-score happened,
names the earlier version, and says the measurement changed rather than the
site.

**The 14 scans were re-scored through `scoring_runner.score_scan`**, the same
call the endpoint makes, rather than by writing rows. All 15 pre-existing rows
unchanged, 14 new ones inserted, asserted rather than assumed.

    psmdigitalagency.com.au   29.10 -> 15.31   -13.79
    zammad.com                53.25 -> 41.19   -12.06
    psmdigitalagency.com      28.89 -> 16.84   -12.05
    basecamp.com              53.28 -> 47.82    -5.46
    front.com                 55.29 -> 53.61    -1.68
    ghost.org                 66.57 -> 67.22    +0.65

**Every figure matches the sizing session's offline recomputation to the cent**
(missiveapp.com differs by 0.01, rounding). Two independent computations of the
same correction agreeing is the evidence the diagnostic was right — and it is
the reason to trust the numbers a client will now be shown.

## 2 — Sentiment: closed, and the reasoning is why rather than the decision

Nothing implemented; nothing needed implementing. `scoring-spec.md` read
*"Recorded as open"* for a question that had in fact been reasoned through, and
a spec that leaves a settled decision looking pending invites it to be
re-litigated by the next reader.

The distinction is what closes it. Mention Rate and Share of Voice answer *"was
this discovered"* — a claim a brand-named question cannot evidence, which is
what made the tautology a defect. Sentiment asks how an answer PORTRAYS the
brand, and that does not depend on who raised the subject. Same artefact, no
defect, because the two dimensions are asking different kinds of question.

The measurement points the other way from the fix, which is the part worth
keeping: awareness-only sentiment is **higher** on every scan with a
population, +1.03 to +25.86 points, because engines recommend a brand they
surface unprompted and hedge about one they are made to discuss. Scoping it
would RAISE scores rather than correct them — the signature of a change that
flatters. Reopening needs a reason of its own, not consistency with a fix aimed
at a different defect.

## 3 — Named only when prompted

`psmdigitalagency.com` scores 0% Mention Rate and appears in almost every
answer to a question that named it. The composite correctly reports the first;
this is the other half, derived on read with no migration and no effect on any
score.

**It is not called "answerable", and that is the whole design.** The obvious
framing — *"not discovered, but recognised when asked directly"* — claims
something the stored facts cannot support. A mention is a text match, and the
Zorblex test showed an engine answering *"I don't have any knowledge of a
product called Zorblex Inbox"* is recorded as naming it. A mention on a
brand-named question is evidence the engine echoed the question, **not** that it
knows the brand. A flag claiming recognition would invent a positive out of the
exact artefact scoring v2 exists to discount — false comfort assembled from the
bug we just finished removing.

So the copy says only where the mentions came from, and then says outright that
being named back by a question that supplied the name is not evidence of
knowledge. A test asserts the words "answerable" and "recognised" appear in no
visibility copy, so the flattering framing cannot be reintroduced by someone
tidying the sentence.

**Its own field, not a degradation flag.** Those say why a NUMBER is rougher
than it would otherwise be; this says what a scan found. Folding them together
would repeat the category error this codebase refused when it kept
`NOT_YET_MEASURED` apart from `NO_POPULATION`. Same SHAPE — a code with a
client-facing string — so `copyCoverage.test.ts` extends to it unchanged.

**The thresholds are asymmetric on purpose.** Zero on the awareness side,
because "never discovered" is the claim and one discovery falsifies it. At
least one on the prompted side and **no rate threshold there**, because
prompted mentions run at 99.61% for everyone measured: a threshold would add no
discrimination while implying the prompted figure means more than it does. A
brand invisible everywhere gets no flag — a different finding that must not
borrow these words — and an engine that did not answer is not a missed
discovery, so an outage cannot manufacture it.

## Verification

**api 1,143** (1,133 → 1,137 → 1,143) and **web 746** (741 → 746) across the
three commits, each run green before the next started. design-system 418 and
shared-types 53 unchanged. `ruff check src tests` clean, `tsc` clean on all
three packages, `alembic check` clean with no migration, contract regenerated
twice for the two new fields.

## Open

**Nothing from scoring v2 remains.** All four items on that entry's list are
now closed: the population disclosure in `994bb04`, and these three.

Still paused and unchanged: **the third-engine / Epic 12 fork**, with the
corrected split denominator from the sizing entry applying to it when it
resumes. And the standing caveat on `copyCoverage.test.ts` — its list mirrors
`scoring.py` by hand, so it guards against forgetting the copy, not against
forgetting the file.

# Epic 12's "why" engine — scoped, and recommended against building now

The brief asked for this to be scoped before any code was written, and said to
stop rather than force the fit if the scoping turned up a reason it was
premature. **It did, and no code was written.** The scoping itself is below, in
full, so whoever builds this later does not re-derive it.

The recommendation is about sequencing, not difficulty. **The build is small
and the pieces already exist** — which is precisely why waiting costs almost
nothing.

## The scoping, which is the part worth keeping

**What "diffing" means here: structural facts, and `ip-safety.md` #7 already
says so in as many words.** Scraped data from competitor pages is *"for FACTS
ONLY (mention counts, citation presence, schema presence, structural
signals)"*. That is not a constraint to design around; it is a description of
exactly what this feature needs. Prose comparison is out — not because it is
hard but because #7 forbids storing a competitor's copy at all, and #8 forbids
it verbatim anywhere in the product. (Constraint #5, about not reproducing a
competitor's algorithms, is about rival *products*, not the client's business
rivals, and is not the binding one here.)

**No new crawler is needed.** `technical_audit.audit_site(url)` already takes
an arbitrary URL and returns exactly the signals a structural diff wants —
`has_faq_schema`, `has_product_schema`, `has_organization_schema`,
`has_sitemap`, `is_indexable`, `schema_types`. `audit_runner.run_audit` simply
never calls it on anything but `client.domain`. The capability exists and is
one call away from a competitor domain.

**"vs. competitors" means the detected set**, `CompetitorSet` from Epic 3.
Confirmed: nothing else in the codebase carries a rival list.

**It feeds the existing fix beat, not a sixth one.** `ip-safety.md` #3 mandates
score → biggest gap → proof → fix → pitch, and a "why" is a property of a fix
rather than a beat of its own. `FixFacts` already carries the competitor names
AND the subject's own structural signals; what it lacks is the competitors'
side of the same signals. So the shape is: audit the competitor domains, diff
the booleans, and hand `generate_fixes` a factual line — *"three of five
competitors publish FAQ schema; this site does not"* — which is ours, derived,
and contains no competitor text.

**The whole slice is therefore: loop `audit_site` over the competitor domains,
diff a handful of booleans, add a field to `FixFacts`.** Days, not weeks, and
it disrupts nothing — which matches Epic 12's own acceptance, *"each is a
standalone module added without disrupting core scan pipeline"*.

## Why not now

Epic 12's header is a gate: **"start once core product has traction."** The
gates before it are also explicit, and none is met.

* **Epic 9 (Phase 1) is not complete.** `[ ] Pilot with 3-5 real agencies,
  collect feedback` is unchecked, and its acceptance is *"pilot agencies
  successfully generate and send at least one real prospect report."* The build
  log confirms **no pilot conversation has happened.**
* **Epic 10 is gated "start after pilot feedback."** Not started.
* **Epic 11 is gated "start once paying agencies exist."** Not started.

So this would be Phase 4 work begun while Phase 1's acceptance is unmet, and
the spec's phase order is product judgment recorded before any of this was
built. This project's recent pattern has been to carry decisions already made
through to the code that missed them — scoring v2 was exactly that. Skipping
three gates would be the same pattern run backwards.

**And there is a sharper reason, specific to this feature.** `north-star.md`
§3.8 marks the Layer 3 differentiation bet `[HYPOTHESIS]` and names what would
falsify it:

> *"pilot agencies who buy on breadth of engine coverage rather than depth of
> finding, or who never open the insight beat and only want the outreach
> draft. Either result moves the bet. **That evidence does not exist yet,
> which is exactly why this is [HYPOTHESIS].**"*

The "why" engine is *more insight beat*. Of everything that could be built
next, it is the single item most exposed to the falsifier that document
already wrote down. Building it now is spending the deepest investment
available on the least-tested assumption in the product, and the test costs a
conversation.

**The asymmetry is what decides it.** If the bet holds, this is no harder to
build in three months than today — the crawler, the signals, the competitor
set and the fix generator will all still be there. If the bet does not hold,
the pilot says so first and this is never built at all. There is no version of
the next quarter where building it now is the better trade.

## What is actually next

**Epic 9's remaining checkbox: the pilot.** The product can now do the thing
that acceptance criterion requires — Layer 5 closed with revocation, expiry
and branding; the report projection is one payload with a PDF and a public
link; and scoring measures what it claims to since v2. The blocker on Epic 9 is
not engineering.

The two things a pilot would settle happen to be the two open questions this
log keeps returning to: whether agencies buy on depth or breadth (§3.8's
falsifier, which decides both this epic and the third-engine fork), and
whether the corrected Mention Rate reads as credible to someone being told
they are invisible.

**This is a recommendation, not a refusal.** The founder can overrule the
sequencing — it is their call and the reasoning above is the input to it, not
a substitute for it. If the answer is build it anyway, the scoping is done and
the slice is the one described above.

**IP-safety check passed:** no code was written and nothing was crawled, so no
new surface exists to check. The design assessed above stays inside constraint
7 by construction — it stores booleans derived from a competitor's markup and
no competitor text — and constraint 3's narrative order is preserved by
feeding the existing fix beat rather than adding a sixth. Recorded because the
constraint applies to a design being proposed, not only to code being shipped.

## Open, unchanged

The third-engine / Epic 12 fork stays paused, and this entry does not resume
it — it recommends that both halves wait on the same evidence. Nothing from
scoring v2 is open.

# Pilot dry run — the send path works, and it would have embarrassed us

`1a1f698` claimed Epic 9's pilot is not blocked on engineering. **That was
reasoning, not verification.** This session walked the acceptance criterion end
to end against the real system: sign-up, intake, scan, score, share, open as a
stranger, download the PDF, revoke.

**The claim was mostly right and importantly wrong.** The mechanism works. What
the report *says* to a prospect, on the first real subject tried, includes a
false high-priority instruction about their own infrastructure.

No code was changed. Findings are ranked below; the two that matter get their
own brief.

## What was walked, and what it cost

A fresh agency (`Northlight Digital`), a fresh prospect (`helpwise.io` — a real
company, chosen because it scored 0 of 4 on the awareness screen from Epic
9.26, which is what an agency's actual prospect looks like). Driven through the
HTTP API with a cookie jar, as a browser would.

    intake (crawl + classify)     9.4s   classified "customer service software",
                                         niche "shared inbox and omnichannel support
                                         helpdesk for teams", brand "Helpwise"
    scan (12 prompts, 3 engines)  286s   succeeded, 36 results
    score                                composite 8.15, v2
    share link                           minted, expires in 30 days
    read as a stranger                   HTTP 200, no session
    PDF as a stranger                    HTTP 200, 6,298 bytes, valid 4-page PDF
    revoke                               204, then 404 on BOTH read routes

**Everything in that column works.** The send path Epic 9's acceptance
criterion depends on is real, including the parts shipped most recently:
revocation kills both the JSON and the PDF route, and the v2 surfaces render on
live payloads.

## FIX BEFORE PILOT

### 1. The report tells a prospect to fix a site that is not broken

`helpwise.io` returns **HTTP 200 in 1.13 seconds**. Intake crawled it
successfully moments earlier — two pages, 828 words. The technical audit then
failed with `BROWSER_ERROR` (Playwright `TimeoutError`), and the fix generator
turned that into the fix list's **highest-priority item**:

> *"Fix the crawl failure on helpwise.io so the site returns rendered HTML to
> automated visitors, then re-run the scan to confirm content is readable"*

**This is the single most damaging thing this product could say.** It is an
instruction to a prospect to repair infrastructure that works, in a document an
agency sends to win their business, and the prospect can disprove it in one
click.

Two separable causes, and the second is the more serious:

* **The crawlers disagree.** `crawl.py` uses `wait_until="domcontentloaded"` at
  a 12s timeout; `technical_audit.py` uses `wait_until="load"` at 25s. On a
  marketing site with third-party tags, `load` can miss 25s where
  `domcontentloaded` finishes in one. Not a one-line fix — the audit genuinely
  needs the load event, because LCP is meaningless without it.
* **A tool failure became a client finding.** This codebase already draws
  exactly this distinction and argues for it at length: `NOT_YET_MEASURED`
  means *our* capability is missing and must not read as a finding about the
  client, as against `NO_POPULATION`, which is about them. A `BROWSER_ERROR` is
  the first kind. It was rendered as the second.

**And it costs the prospect points.** Technical Foundation was excluded and its
weight redistributed, so the composite of **8.15** is computed over four
dimensions. Had the audit scored this site anywhere in the 60–80 band a real
SaaS marketing site would expect, the composite would have been **13.3–15.3**.
The audit failure roughly halves the number the prospect is shown.

### 2. The landing page understates scan time by about half

It says *"A scan takes about six minutes."* Measured: the operator path took
**286s (4.8 min) at 12 prompts**. The default is 24 (`TARGET_PROMPTS`, and the
pricing card says "twenty-four intent-tagged questions"). Extrapolating the
loop from the three `verify_e2e` runs while holding setup fixed puts a
24-prompt scan at **8.5–9 minutes** — an estimate, labelled as one, but the
direction is not in doubt and `product-spec.md` already says the 300s budget is
missed "and by more" since Epic 9.17.

A prospect told six minutes who waits nine assumes it is broken.

### 3. A sentence claims a denominator it does not have

The proof beat renders *"…is missing from 31 of the 40 answers this scan
measured."* The scan measured **48**. `MAX_SHELF_PROMPTS = 20` caps the shelf at
20 prompts × 2 engines = 40 rows, and the copy calls that subset "this scan".
The number is right about the shelf and wrong about the scan, in a sentence that
says "scan".

## NOTE AND MOVE ON

* **A fourth population on the page.** That same shelf sentence implies 9 named
  of 40, beside a v2 Mention Rate of `0.00`. Same class as the mismatch
  disclosed in `994bb04`, one beat further down, undisclosed. Lower severity
  because the sentence is about *answers* rather than labelled "mention rate" —
  but a reader doing arithmetic finds it.
* **One SerpApi query timed out** (`SERP_TIMEOUT`, "Helpwise competitors") and
  detection still returned five competitors. Degraded exactly as designed;
  recorded because it happened on the first real run.
* **Responsive layout was not checked.** Static markup was rendered and read;
  no browser at a narrow viewport. Unverified rather than fine.

## The subjective pass — reading it as an operator, not an engineer

**The narrative holds together.** On `psmdigitalagency.com` the headline reads
*"PSM Digital Agency is close to invisible when buyers ask"*, the score shows
**17/100 · Effectively invisible**, and the gap beat says *"Mention Rate is
costing the most — 30.0 points"*, then names what is behind it. That is an
argument, not five numbers.

**Three of the four fixes read like a person wrote them**, and they cite real
figures rather than gesturing:

> *"Only 6 of 91 citations in this scan pointed at helpwise.io; the rest went
> to third-party directories led by g2.com (22) and capterra.com (5)."*

> *"Build comparison pages on helpwise.io covering Helpwise against Missive,
> Front, Drag, Happyfox and Bolddesk…"*

Specific, checkable, and about this business. **The fourth is finding 1** — and
it is worth noting that one false item sits in a list whose other three are the
best argument this product makes.

**Nothing leaked.** Two real payloads rendered: no `undefined`, no bare `null`,
no `NaN`, no `[object Object]`, no raw `SCREAMING_CODE` in prose, no
placeholders. `copyCoverage.test.ts` is doing its job on live data and not only
in fixtures — the `NAMED_ONLY_WHEN_PROMPTED` flag and the v1.1 re-score note
both rendered as sentences.

**The commercial surface is honest.** The pricing card says *"One plan, one
price — there is no tier above this one to be upsold to"* and *"Scan volume is
not metered today. That is an absence rather than a promise of unlimited use"*.
Nothing on it overstates what exists. §5.3's "one plan, $29/month, 3 seats" and
the built surface agree.

## Is Epic 9's acceptance criterion ready to attempt?

**Not quite — and the gap is one afternoon, not one epic.**

The mechanism is ready. A pilot agency can sign up, scan a prospect, get a
report, send a link, and revoke it. That was verified rather than assumed, on
the real system, including a stranger downloading the PDF.

What stands between here and a pilot is **finding 1**, and it is not a polish
item: the first real prospect tried produced a report containing a false,
high-priority claim about that prospect's own website, and a composite roughly
half what it should be. Sending that to three to five agencies would burn the
pilot's credibility on the one thing this product sells — being right about
somebody's site.

Findings 2 and 3 are smaller and both are copy.

**The two live findings from the cross-engine work are legible.**
`NAMED_ONLY_WHEN_PROMPTED` fired on both a stored scan and the fresh live one
and reads as a finding rather than reassurance; the population disclosure from
`994bb04` renders where intended. Neither needed forcing — helpwise triggered
the flag naturally, which is itself the confirmation that the condition matches
real subjects.

## Open

**New, from this session:** the audit/fix-generator finding (1) and the two copy
items (2, 3), each for its own brief. The audit one should be scoped as two
questions — why the audit's crawler is stricter than intake's, and separately
why a tool failure can become a client-facing fix at all, which is the part that
generalises beyond this crawler.

Unchanged: the third-engine / Epic 12 fork stays paused. Nothing from scoring
v2 is open.

# The pilot dry run's list, closed

`72b0fd9` walked Epic 9's acceptance criterion end to end and found three
things to fix before an agency sees this. All three are fixed. The blocking one
turned out to be two bugs, and chasing it surfaced a third nobody had looked
for.

## The blocking bug was not the bug it looked like

The dry run's diagnosis was a crawler too strict for a real marketing site:
`helpwise.io` timed out in the audit while intake crawled it fine. **Reproducing
it first, as the brief insisted, showed that was wrong.**

Standalone, the same site passed the same audit **six times out of six** —
median 11.9s, max 14.4s, against a 25s ceiling. The failure was transient, not
a property of the site, and nothing about `wait_until="load"` was reliably
too strict for it.

That changes the fix and strengthens the second half of the diagnosis
enormously. A timeout on a site that passes six of six is not evidence about
that site — which is exactly what the report had claimed it was.

**So the fix is to survive a transient timeout rather than to chase it**
(`1c3cf36`). On a `load` timeout the audit re-navigates with
`domcontentloaded`, which is what `crawl.py` has always used and what succeeded
on this site during the same scan. Forced with a 1,500ms ceiling: **17 checks
and a score of 93.75, where it previously returned one error.** Seventeen checks
that never needed the load event — schema types, indexability, sitemap, title,
meta description — no longer die with the one that did.

### Forcing it exposed a bug inside the fallback

LCP and CLS are cumulative: the largest paint *so far*, the shift *so far*. A
page we stopped waiting for therefore reports **better** numbers than the same
page fully loaded. Measured on `helpwise.io` while building this: **988ms on the
fallback against 2,952ms on a full load** — a third of the real figure, and
enough to flip `cwv_lcp` from `warn` to `pass`.

A fallback that quietly flatters every slow site it rescues is worse than the
outage it was added to survive. Vitals are discarded on that path, and
`build_checks` already rendered an absent vital as `not_applicable` with
`LCP_NOT_OBSERVED` — so it says *not measured* instead of inventing a good
number, through a branch that existed before the fallback did.

## The bug that generalises: a tool failure is not a finding

`43e2b8d`, and the more important of the two.

Every audit failure collapsed into one `site_reachable: error`, which
`audit_findings` promoted to a fix candidate, which the generator wrote up as
the report's **highest-priority item**: *"Fix the crawl failure on helpwise.io
so the site returns rendered HTML to automated visitors."*

The two kinds are now separated where they first become distinguishable:

* **The server answered, and its answer was an error** — `HTTP_404`,
  `HTTP_503`. That is the site describing itself. Marked `fail`, and it still
  produces the real finding.
* **We could not complete the measurement** — `BROWSER_ERROR`, `TIMEOUT`,
  `FETCH_FAILED`. That is a report about our own attempt. It stays `error`, and
  `audit_findings` no longer treats `error` as a finding at all.

This is the distinction the codebase already draws between `NOT_YET_MEASURED`
and `NO_POPULATION`, applied one layer down — and the six-of-six reproduction is
the evidence for it: the same site passed and failed the same check on
different attempts, so a timeout was never evidence about the site.

**The halved composite needed no separate fix.** `score_audit` already returned
`scored=False` whenever the signals were not ok, so Technical Foundation was
already excluded and its weight redistributed. What was wrong was that the
exclusion came with a false accusation attached.

Tested at the boundary where it went wrong — `audit_findings` into
`build_candidates` — rather than on the generated copy, because the copy is a
model's and the boundary is ours.

## Two copy corrections

**"A scan takes about six minutes"** (`2db2ab7`) was true of Epic 9.2 and stopped
being true at Epic 9.17. It now says about ten, hedged, alongside what the scan
is doing so a longer wait reads as work rather than a fault.

**No precise number is committed to, deliberately.** Three 12-prompt runs came
in at 286s, 306s and 331s — a 16% spread on identical work, because the duration
is dominated by engine latency this product does not control — and there is no
post-9.17 measurement of a 24-prompt scan at all. Nine minutes is an
extrapolation, not an observation, and printing a figure that precise would
repeat the original mistake at a different number.

**The shelf headline** (`50ca8f5`) said *"missing from 31 of the 40 answers this
scan measured"* about a scan that measured 48, because `MAX_SHELF_PROMPTS` caps
the table at 20 prompts and the sentence counted rows while saying "scan". The
title now counts the scan and the caption says the table is a sample when it is.
The alternative — an honest "of the 40 shown" — is self-consistent but
understates the finding, and the headline of a beat should not be the smaller
number.

## A process note, recorded because it is the kind of thing that hides

The first attempt at the audit work landed **both causes in one commit whose
message described only the first**, because `git add apps/api` staged more than
the message covered. Caught by reading the commit back, and split into
`1c3cf36` and `43e2b8d`, each verified against the full suite on its own. The
brief asked for one commit per logical piece and the first attempt did not
deliver that.

## Verification

**api 1,159** (1,143 → 1,146 → 1,159) and **web 749** (746 → 749) across four
commits, each green before the next. design-system 418 and shared-types 53
unchanged. `ruff check src tests` clean, `tsc` clean on all three packages, no
migration.

The audit fix was verified **against the real failure mode**, not assumed: the
before state, six standalone runs to characterise it, a forced timeout to prove
the fallback, and the vitals comparison that found the second bug.

## Is the pilot ready now?

**The three findings that stood between here and an attempt are closed**, and
the one that mattered is closed twice over — the audit survives a transient
timeout, and even if it does not, its failure can no longer become an
instruction to the client.

What has **not** been done is a second dry run to confirm it end to end on a
live scan, which `72b0fd9`'s own scope note put in its own session. That is the
remaining step before the answer is yes: the fixes are verified in isolation and
against a forced reproduction, not yet against another full operator walk.

## Open

**A second pilot dry run**, to confirm these on a live path.

Unchanged: the third-engine / Epic 12 fork stays paused, and Epic 12's "why"
engine remains recommended-against until the pilot runs.

# Second pilot dry run — the fixes hold, and the report still says things it cannot back

`a398da5` closed the first dry run's list and said the fixes were verified in
isolation and against a forced reproduction, **not against another full
operator walk**. This is that walk. Governance line, per north-star.md §8.1:
this touches no architectural layer and builds nothing; it serves the pilot
phase by verifying Epic 9's acceptance criterion. No code was changed.

**The four fixes hold on live output.** Nothing false about either site
appeared anywhere. **The report still contains checkable wrong numbers**, in a
different place: the fix list's own sentences and the PDF's verdict word. And
reading the rivals table skeptically found that one of the five scored
dimensions has never been earned by anyone.

## What was walked, and what it cost

A fresh agency (`Harbourline Digital`), a fresh prospect (`pirsch.io` — a real
two-person analytics company, a different category from Helpwise, and a brand
token that is not an English word so a mention is a clean text match), and
`helpwise.io` a second and third time. Driven through the HTTP API with a cookie
jar as before, then through a real headless browser with no session as the
prospect.

**The subject was screened, not guessed**, with Epic 9.26's four-awareness-
prompt check on one engine. None of six candidates scored 0 of 4 today —
savvycal 4, bannerbear 4, tidycal 4, richpanel 3, pirsch 3, fathom 2 — and
Richpanel, 0 of 4 in 9.26, scored 3 of 4 on four different sentences. The
screen is prompt-sensitive; noted under the observations.

    sign-up                          0.14s  owner seat, 1 of 3
    intake pirsch.io                 10.6s  "web analytics software", niche "privacy-friendly,
                                            cookieless analytics as a Google Analytics
                                            alternative", brand "Pirsch Analytics", 0.960
    scan pirsch (24 prompts × 3)     348s   partial: 1 claude_search timeout; 72 results,
                                            71 answered, 30 naming the subject
    audit pirsch                            17 checks, load event reached, LCP 4,448ms,
                                            CLS 0.288, Technical Foundation 40.00
    score                                   composite 27.28, v2
    fix generation                          5 accepted, 0 rejected
    report                           68ms   53,732 bytes
    share link                              minted, expires in 30 days
    read as a stranger               200    no cookies; same 53,732 bytes
    PDF as a stranger                200    6,855 bytes, valid 4-page PDF; the button on
                                            the share page downloads a byte-identical file
    revoke                           204    then 404 on BOTH read routes, 204 again, and
                                            the share page shows its withdrawn state
    scan helpwise.io, run 1 (12 × 3) 165s   audit ok on the first attempt, LCP 2,984ms, 93.75
    scan helpwise.io, run 2 (12 × 3)  49s   audit ok on the first attempt, LCP 3,140ms, 93.75

**Everything in the send column works, for the second time.** Sign-up, intake,
scan, score, share, read as a stranger in a browser, download the PDF from the
button, revoke — and revocation kills the JSON route, the PDF route and the
web page on the same lookup.

**The Anthropic credit balance ran out at 14:33**, three minutes after the
pirsch scan finished cleanly. Both helpwise runs lost their Claude engine
calls, their sentiment calls and their fix generation to
`PROVIDER_QUOTA_EXHAUSTED`. That is the operator's account, not the product,
and the product degraded exactly as designed: `partial` with a code on every
failed result, `fixes.not_generated` with a reason, Sentiment excluded as
`NO_POPULATION`, `NO_CITATIONS_IN_SCAN` flagged. The audit needs no model
call, so both runs still answered the question they were for.

## The two things this session existed to check

**The audit's fallback was not exercised live.** Three audits ran against real
sites this session and all three reached `load` on the first attempt: zero
`audit.load_timeout_fell_back` lines, zero `LCP_NOT_OBSERVED` rows. With the
fix session's six standalone passes, `helpwise.io` has now completed the audit
nine times in nine attempts since the one timeout that started this. The
fallback remains proven only by last session's forced reproduction, which is
fine to say plainly: the real world did not reproduce the original problem.

**No tool failure became a finding, on either kind of fix list.** On pirsch the
list was generated; on helpwise run 1 generation failed and the page fell back
to the deterministic derivation. Neither contains anything about a crawl, a
browser, or a timeout. No audit failed, so `audit_findings` was not exercised
by a live failure — but the deterministic path was read for the same defect,
and it is safe by omission: `deriveFixes` and `derive_fixes` would still rank
an `error` finding as high priority, and their copy tables have no entry for
`BROWSER_ERROR`, `TIMEOUT`, `FETCH_FAILED` or `HTTP_*`, so the candidate is
skipped. Worth one test in the audit brief, not a change.

## The copy fixes, on a live payload

**Landing page.** *"A scan usually takes about ten minutes — twenty-four
questions, put to three AI engines, one at a time."* renders under the call to
action at 1280px and at 390px with no horizontal overflow; "six minutes" is
gone.

**Shelf headline and caption.** On the 24 × 3 scan the title reads *"Pirsch
Analytics is missing from 41 of the 71 answers this scan measured"* — the
scan-wide figures — and the caption says *"The first 60 of 72 answers are
shown; the count above is the whole scan."* On the 12 × 3 and 11 × 3 scans,
where nothing is capped, the caption carries no sample sentence. Both branches
confirmed on real rows rather than fixtures.

## FIX BEFORE PILOT

### 1. The fix list quotes figures computed over the wrong population

The pirsch report's second fix opens: *"All 30 of 30 answers named Pirsch, but
mention rate still scored 30.56, indicating the brand appears with less depth
than the gap allows."* The scan had **71 answers and 30 named Pirsch**, and the
shelf title one beat earlier says so. The third fix says *"Only 3 of the 123
citations behind these answers pointed at pirsch.io"*; the proof beat on the
same page — and in the same PDF — says **323**.

The model did not invent these. The facts bundle it was handed said
`answers_analysed=30, answers_naming_subject=30, citations_total=123`, because
`fix_runner.collect_facts` filters results on `status is OK` — which means
*answered and mentioned* — and drops `ANSWERED_NO_MENTION`, the status the API
contract labels in bold as *"a finding, not a failure"*. So the generator only
ever sees the answers that named the brand: the naming count always equals the
total, citations are tallied inside that subset, and the top-cited-domain list
it reasons from is wrong too — it named `analytics-compare.com` (4 citations)
and never saw `analytics-alternatives.com` (15), which the same page lists as
the top unclaimed source.

This is finding 3 from the first dry run — a denominator the sentence does not
have — one beat further down and in the list the product sells with. It has
been there since Epic 8: the first run's praised *"Only 6 of 91 citations"* on
helpwise was the same arithmetic (that scan had 142 citations across its 36
answered results; 91 in the 16 that named the brand). Fix: count over
`ANSWERED_STATUSES` and mirror the proof beat's population, with a test at the
`collect_facts` boundary. Its own brief.

### 2. The page and the PDF give the same score two different verdict words

`report_pdf._band` says it *"mirrors VisibilityBadge's thresholds"*. It does
not. The design system's `visibilityBand` cuts at 20/40/60/80 and says *Absent
· Barely visible · Emerging · Established · Highly visible*; the PDF cuts at
15/35/60/80 and says *Absent · Marginal · Emerging · Established · Dominant*.

Live: pirsch at 27.28 is **"Barely visible"** on the share page and
**"Marginal"** in the PDF downloaded from it. Helpwise at 17.46 is
**"Absent"** on the page and **"Marginal"** in the PDF. The contract's own
warning — *"a PDF assembled independently would eventually disagree with the
page it was downloaded from, and the disagreement would reach a client"* —
came true in the headline verdict. One function.

### 3. Citation Strength cannot be earned, and the pitch promises its points

Reading the rivals table skeptically: every brand on the pirsch report shows
Citation Strength **0.66** — Pirsch with 3 citations, Plausible with 13,
Simple Analytics with 16. `scoring.citation_strength` divides *distinct
domains citing the subject* (the subject's own domain, so 0 or 1) by *every
distinct third-party domain the engines cited* (152 on this scan). A brand
owns one domain, so the most any brand can score is 1/N, and the comparison
mirrors it exactly. **Across all 33 stored scores the maximum is 3.70.** The
dimension carries 20% of every composite.

What the prospect reads: *"Citation Strength 0.66 / 100 · 19.9 points left"*,
a fix *"worth 19.9 points"*, and the pitch beat's *"27 today. 91 with the fixes
above."* About 20 of those 63 points are unreachable by construction, and the
disclosure sentence — *"measured against the best-cited brand in this scan"* —
describes a formula the code does not implement; it normalises against the
whole field. The docstring records the shortcut ("attribute non-subject
citations to the scan as a whole"); its consequence was never written down.

**Layer 5, and the one item here that is not an afternoon.** Which figure
replaces it — citation share against the best-cited single brand, or the rate
of answers citing the subject — is a scoring-spec decision with its own brief.
It sits above the line because the pitch beat's headline number depends on it;
the cheap interim is to exclude or label the dimension, not to redesign it.

## NOTE AND MOVE ON

* **The rendered fix list is not the stored one.** `enrich` overlays generated
  wording onto the client's derived candidates by key and *"a miss is silent by
  design"*. The generator's two Core Web Vitals fixes had no derived
  counterpart (no copy for `LAB_MEASUREMENT_NOT_FIELD_DATA`) and never
  rendered; two canned schema fixes and a canned citation fix rendered instead.
  Six on the page, five in `action_items`, and both surfaces agree with each
  other. The outcome was fine; the generator spent two of five items on
  candidates nothing shows.
* **Phone width.** The share page pans 51px sideways at 390px (cause not
  isolated), and the rivals table's three numeric columns and half its caption
  sit off-screen inside a scroll wrapper with no visible affordance. Readable,
  but a prospect on a phone sees Brand and Domain and has to discover the
  swipe. The first run left responsive unverified; it is now measured.
* **A partial scan says so only in numbers.** Helpwise run 1 lost 13 of 36
  calls. The page shows *"Answered 6 of 12"* per engine and no sentence says
  calls failed or why. The contract says `partial` exists *"so a report can say
  'one engine was down'"*; the page implies it rather than saying it.
* **A 24-prompt scan took 348s.** The first post-9.17 measurement of the
  default scan: 5.8 minutes, under the *"about ten"* the landing now says. One
  point, and the copy errs in the safe direction. Left alone.
* **Every report read re-scores.** Eight `scoring.completed` lines for the
  pirsch scan in ten minutes, one per `GET`. Same digest, no provider calls,
  so harmless — recorded because it is a surprising cost on the hot path.
* **The awareness screen is four sentences.** Six candidates, none at 0 of 4;
  the 9.26 subject that scored 0 of 4 scored 3 of 4 on different prompts.
  "Chosen by measurement" is chosen by the prompts, which is worth knowing
  before the screen decides a fork.
* **No in-product warning before the balance runs out.** The first sign an
  operator gets is a `partial` scan with quota codes. A pilot agency will hit
  this the same way.

## The subjective pass — reading it as an operator, not an engineer

**The narrative still holds together, and this subject is a better test of it
than the last.** *"Pirsch Analytics is close to invisible when buyers ask"*,
**27/100 · Barely visible**, *"Share of Voice is costing the most — 22.5
points"*, *"3 rivals are named more often than Pirsch Analytics in the same
answers"*. The five rivals — Plausible, Matomo, Simple Analytics, Usermaven,
Fathom — are the right five, and the unclaimed sources
(`analytics-alternatives.com` 15, `g2.com` 12, `capterra.com` 9) are exactly
where an agency would start.

**Two of the three generated fixes read like a person wrote them**, and name
the directories and rivals that the proof beat shows. The third is finding 1:
its first sentence is false on its face, under a headline that contradicts it.
Nobody asked the model to be wrong; it was handed the wrong count.

**Nothing leaked.** Both share pages: no `undefined`, no bare `null`, no `NaN`,
no `[object Object]`, no raw code in prose (the capitalised words in the sweep
are section eyebrows), no console errors. `NAMED_ONLY_WHEN_PROMPTED` fired
naturally on helpwise run 2 and rendered as a sentence.

## Harness notes, so the next walk does not repeat them

The share page silently showed its withdrawn state when Playwright opened it at
`127.0.0.1:3000`: `CORS_ALLOW_ORIGINS` is `localhost:3000`, so every API fetch
failed and the page's single error state hid it — which also made an earlier
"bad token" check at that origin meaningless until it was rerun. And the shelf
title was reported missing for one turn because it renders through
`text-transform` and a case-sensitive grep did not find it in capitals.

## Is Epic 9's acceptance criterion ready to attempt?

**Not yet — and this time the answer has two parts.**

The mechanism is ready, verified twice now on the real system: a pilot agency
can sign up, scan a prospect, get a report, send a link, watch a stranger open
it in a browser and download the PDF, and take the link back. The four fixes
from the first run hold on live output, and nothing false about either site
appeared.

**I would not send the pirsch report as generated to Pirsch on an agency's
behalf.** Its second fix says *"All 30 of 30 answers named Pirsch"* under a
headline that says 41 of 71 did not, its third fix cites 123 citations where
the same page counts 323, and the PDF a prospect downloads calls the score
"Marginal" where the page says "Barely visible". Findings 1 and 2 are one
afternoon, the same size as last time's copy items.

Finding 3 is the reason the answer is "not yet" rather than "after lunch": a
fifth of every score comes from a dimension no site has ever scored above 3.70
on, and the sales beat promises those points back. That is a scoring decision,
not a fix, and it should be made before the first report goes out with *"91
with the fixes above"* on it.

## Open

**New, from this session:** the fix-facts population (1) and the PDF band (2),
each for its own small brief; Citation Strength (3), for a Layer 5 brief that
may choose to exclude and label it before it redesigns it. The notes above go
into whichever brief touches them.

Unchanged: the third-engine / Epic 12 fork stays paused, and Epic 12's "why"
engine remains recommended-against until the pilot runs.

# The second dry run's list, closed — and Citation Strength taken off the scale

`2436f92` walked Epic 9's acceptance criterion a second time and found three
things a prospect could catch on one page. All three are closed: two as the
one-cause bugs they were, the third as an interim that stops the report
promising what it cannot deliver, without deciding what the dimension should
measure instead. Governance line, per north-star.md §8.1: Layer 3 (fix
generation) and Layer 7 (report), Activation phase — and the composite itself
for the third, which is the one place this session widened, said below.

## Finding 1 — the fix list counted the wrong population (`f342ed2`)

`fix_runner.collect_facts` filtered engine results on `status is OK`, which
means *answered and named the brand*, and dropped `ANSWERED_NO_MENTION` — the
status the API contract labels in bold as *a finding, not a failure*. So the
generator only ever saw the answers that named the brand: the naming count
always equalled the total, citations were tallied inside that subset, and the
top-cited-domain list was built from it. That is how a real report came to say
*"All 30 of 30 answers named Pirsch"* two beats after a proof beat counting 30
of 71, and *"3 of 123 citations"* against 323 — and why the generator named
`analytics-compare.com` (4 citations) and never saw
`analytics-alternatives.com` (15), the page's own top unclaimed source.

**The rule now has one home.** `ANSWERED_STATUSES` lives beside the enum in
`models/engine_result.py`; `report.py` — which had it right since Epic 7.1 —
imports it instead of owning a copy, and `collect_facts` filters on it. Tested
where it went wrong, with the shape that would have caught it: a scan driven
through the real chain with stubbed engines, half of whose answers do not name
the brand and whose non-naming answers carry citations of their own. On the old
predicate the assertions fail at **6 against 12, 4 against 8**, and the facts
disagree with the proof beat; a fourth test holds `collect_facts` and
`build_report` to the same four counters for the same scan, whatever either
counts in future.

**Not confirmed on live copy, and why.** Stored fix lists written before this
keep their wording until regenerated — the pirsch item in `avp_dev` still says
"All 30 of 30". Regenerating needs a model call, and the Anthropic balance that
ran out during the dry run has not been refilled, so the boundary test is the
proof. A new scan generates from the corrected facts.

## Finding 2 — the PDF's verdict word was not the page's (`45814b0`)

`report_pdf._band` claimed to mirror `VisibilityBadge` and cut at 15 and 35
with its own words. Corrected to the design system's 20/40/60/80 and *Absent ·
Barely visible · Emerging · Established · Highly visible*, and policed the way
`report_narrative.py`'s copy of `derive.ts` already is: one checked-in table at
every boundary (`packages/shared-types/fixtures/visibility-bands.json`), read
by a design-system test that asserts it still describes `visibilityBand` and
the badge label, and by an API test that asserts the PDF says the same. The old
function fails **13 of the table's 21 assertions**; the design system exports
`visibilityBandLabel` so the TypeScript side has a name to call. Live: the
re-scored pirsch PDF prints *"Barely visible"* under 33.93, as the page does.

## Finding 3 — Citation Strength could not be earned (`163894e`)

**The decision, stated rather than defaulted: option (a), exclude the
dimension.** `scoring.citation_strength` divides the distinct domains citing
the subject — in practice its own domain, 0 or 1 — by every distinct
third-party domain the engines cited, so its ceiling is `1/N`. The numbers,
from `avp_dev` before the change:

    stored scores                         33 (15 v1.1, 18 v2)
    Citation Strength, maximum          3.70
                       median           0.88
                       mean             1.14
    pirsch.io, N (distinct 3rd-party)    152   -> subject and all five rivals 0.66

Option (b) — capping what the pitch's sum could claim — reaches one of the
five places the unearnable figure appeared: the score table's *"19.9 points
left"*, the gap beat's runners-up, the fix list's *"+19.9 pts"* chip, the pitch
beat's *"91 with the fixes above"*, and a rivals column reading 0.7 six times.
Excluding the dimension reaches all five through machinery that already
exists, and it is the treatment v1.1 gave Technical Foundation before Epic 6
could measure it. Nothing informative is lost: across 33 scores the dimension
never carried information, and the citations themselves — the proof beat's
tables and the unclaimed-domain fix — read the persisted rows and never
depended on the score.

**What changed.** `compute_score` excludes Citation Strength under
`NO_AUTHORITY_DATA`, which names the missing input as the other reasons do
(`NOT_YET_MEASURED` would say a capability is on its way). The same code is no
longer *also* a degradation flag on a new score — a dimension that is left out
is not "rougher", and saying both says two things — and the flag's sentence on
older scores now describes what that formula did rather than the comparison
against the best-cited brand it never made. Rivals' citation strength is
`null` while the subject's is excluded, so a column means one thing; the
schema, the regenerated TypeScript contract and both renderers carry it as a
dash. The PDF's *"Left out of the score, and why"* now prints the page's
sentence under each label — until this it printed the label alone, which for a
permanent exclusion would leave a reader two words. `FORMULA_VERSION` is
`v2.1`; scoring-spec.md and api-contracts.md record all of it.

**Confirmed on the real scan.** Re-scoring pirsch.io through the API wrote a
v2.1 row at **33.93** beside the untouched v2 row at 27.28;
`previousFormulaVersions` says `["v2"]`; the share page shows the exclusion
card with its badge and sentence, the rivals column as dashes, and *"the
weighted composite of 4 measured dimensions"*; the PDF agrees. Every other
stored row is untouched — 15 v1.1, 18 v2, and the one v2.1 row this
verification added. `alembic check` reports no drift; the column was already
nullable, so there is no migration.

**Said plainly, because it is the kind of thing that hides:** the pitch beat
now recovers **61.7 points to a composite of 96**, where before it recovered
63.2 to 91. The sum barely moved and the ceiling rose, because the four
remaining dimensions each weigh more — Share of Voice's gap went from 22.5 to
28.2 under a 31.25 weight. Every one of those points can now be earned, which
was the objection. Whether a pitch should sum gaps at all is a different
question and not this session's.

**What it costs.** The fix list loses the citation-strength gap fix — on
pirsch, the *"publish a linkable comparison and methodology section"* item,
which was one of the better-written ones — because a dimension that is not
scored has no gap to recover. The unclaimed-domain fix still says where to get
cited. Under the heavier weights Technical Foundation's gap (7.5) now clears
the threshold, so its canned fix surfaces. And a re-scored scan's stored fix
rows keep a `gap:citation_strength` item until regenerated; nothing renders it.

**Alerts.** `generate_for_scan` now declines to compare composites scored
under different formula versions, with a test each way. Nothing forced this
today — the bump raises scores, and a rise is never an alert — but a bump the
other way would have fired *"visibility fell"* on every client with a
baseline, and the report already refuses that reading through
`previousFormulaVersions`.

## Verification

**api 1,193** (1,159 → 1,163 → 1,184 → 1,193), **web 750** (749 → 750),
**design-system 436** (418 → 436), shared-types 53. Each commit green on the
full API suite, `ruff check src tests` and `tsc` on all three packages before
the next started. The fix-generator chain tests, which run an engines-only
executor and had leaned on Citation Strength as their only gap, now stub a
brand named in half its answers — which is what the generator is for.

## Is the pilot ready now?

**The three findings that `2436f92` put between here and a send are closed.**
What has not been done is a third operator walk on the new definition, which
is its own session by the same rule the last two followed. Below the line,
unchanged from the second dry run: phone-width panning, a partial scan that
says so only in numbers, re-scoring on every report read, and the
prompt-sensitivity of the awareness screen.

## Open

**Citation Strength's real definition** — a Layer 5 brief: citation share
against the best-cited single brand, the rate of answers citing the subject at
all, or what an authority feed makes possible. The exclusion is an interim and
says so in three places.

**A third pilot dry run on v2.1**, with a funded balance, to confirm finding 1
on generated copy and read the pitch beat as an operator now that all of its
points are earnable.

Unchanged: the third-engine / Epic 12 fork stays paused, and Epic 12's "why"
engine remains recommended-against until the pilot runs.

# Third dry run, on v2.1 — ready, on one model call

`98c9252` closed the second dry run's list and left two things unconfirmed on
live output: whether the fix list agrees with the proof beat on freshly
generated copy, and whether the pitch beat reads as something to send now that
Citation Strength no longer inflates it. Both are properties of the
fix-generation step alone, and `avp_dev` already held a real, fully scanned
subject with the v2.1 re-score on it. So this session spent **one model call**
and no scan. Governance line, per north-star.md §8.1: verification, Layer 3
(fix generation) and Layer 7 (report), Activation phase; one one-line prompt
label fixed (`aa08bf6`), nothing else built.

## What was walked, and what it cost

    credit                         taken on the user's word; the one call answered
                                   201 rather than a quota 400, the only free check
    regenerate fixes, pirsch.io    POST /scans/{id}/fixes -> 201 in 26.8s;
    (scan_01M1ZM80…, v2.1, 33.93)  5 candidates, 4 accepted, 1 rejected by the guard
    report                         200; proof beat 71 answered / 30 naming / 323 citations
    share link                     200, 30 days
    read as a stranger             200, no cookies, 52,996 bytes
    PDF as a stranger              200, 6,778 bytes, 4 pages; the button downloads the
                                   same file under the same name
    revoke                         204, then 404 on both routes, 204 again

**Provider spend, stated as a bound because the service does not log usage:**
one `claude-opus-5` call at effort `medium`. Input measured offline from the
same facts and candidates: system 1,981 characters plus user 1,788, about
**940 tokens, $0.005**. Output not recorded; at most the 8,000-token ceiling,
**$0.20**; a 26.8-second turn at medium effort is realistically a few thousand
tokens, so a few cents. **Session total: under $0.21, most likely about
$0.05.** No engine, sentiment, classification, detection or awareness-screen
call was made, and no scan was started.

## Finding 1 on live, freshly generated copy — confirmed

The facts the generator was handed, read back from the same code path: **71
answered, 30 naming the subject, 323 citations, 3 to the subject's own domain**,
top domains led by `simpleanalytics.com` (16) and `analytics-alternatives.com`
(15) — the proof beat's own population, and the source it never saw before.

The sentence it wrote: *"Pirsch was named in 30 of 71 AI answers, so 41
answers on this topic resolved without it."* The proof beat on the same page
says 71 and 30, and the shelf's title says *"missing from 41 of the 71"*. The
second dry run's version of this sentence was *"All 30 of 30 answers named
Pirsch"*. The citation pair was not quoted this time — the citation-strength
fix no longer exists to quote it — so that half is confirmed at the facts
boundary and by `test_fix_runner.py`, not on prose.

## The pitch beat, read as a skeptical operator

*"34 today. 96 with the fixes above."* Under it: *"Closing the gaps listed
above recovers 61.7 points, which is a composite of 96. That figure is the same
weighted sum as the score itself — it is what the score becomes if those
dimensions reach 100, not a forecast."* Then the per-dimension distance
(*"Plausible Analytics currently leads on Mention Rate by 69.4 points"*) and
the inputs fingerprint.

I would send it. The number is a ceiling stated as arithmetic and labelled as
one; every point in it belongs to a dimension a site can actually earn; and
the same page says, in the prospect's own words, that a fifth of the scale is
left out and why. The score beat says *"the weighted composite of 4 measured
dimensions"* and *"scored under an earlier definition (v2)"*, so a reader who
saw 27 last week is told the definition moved, not the business.

## What the guard did, and the one line fixed

**The biggest gap's generated fix was rejected**: `fixes.claim_rejected
candidate=gap:share_of_voice phrase=dominate`. The model wrote "dominate", the
banned-claim guard refused it, and the page rendered the string-table copy for
that candidate — *"Compete on the comparisons where rivals currently appear
alone"*, +28.2 points. Correct behaviour, and worth naming for the generator's
own brief: one word cost the best-written item on the list, and a rewrite
would have been cheaper than a drop.

**One sentence overreached, and the prompt let it.** The generated Mention
Rate fix said *"pirsch.io offers only 1,221 words of indexable content
overall"*. The number is the audit's word count for the one page it reads; the
prompt handed it over as `word count 1221` under "Site structure signals", so
the model scoped it to the site. A prospect knows their own site has more than
1,221 words. The heading now names the audited home page (`aa08bf6`), verified
by the prompt test; the stored copy predates the line and was not re-spent on.

**Three items say "add schema".** The generated Technical Foundation fix names
Organization, SoftwareApplication and FAQPage markup, and the two canned audit
fixes beneath it name Organization and FAQPage again — the key-merge seam
`2436f92` noted, where a generated dimension fix and the string-table audit
fixes overlap. Legibility, not truth; noted for the same brief.

## A process note, recorded because it is the kind of thing that hides

The one-line prompt fix was committed on a red suite. Its verification chain
piped the full run through `tail`, so the chain read `tail`'s exit code, not
pytest's, and went on to commit with "1 failed" in the line it had just
printed. The failure was the IP-safety whitelist doing its job — every line of
the fix prompt must start with a known prefix, and the renamed heading was not
one — and the fix was the whitelist, not the prompt. Caught on reading the
output back; the commit after the entry carries the whitelist and the green
run. **And then the chain that fixed it did the same thing with `ruff`**: its
"Found 1 error" (a line too long, in the whitelist) also went through a pipe,
and that commit went in too. The one after it wraps the line, and every check
in it reads the tool's own exit code from a file rather than a pipe's. Twice in
one session is the pattern, not the slip: a verification chain must never take
its verdict from the last command in a pipeline.

## Still noted, unchanged

Phone width: the share page's scroll width was 455px on a 390px viewport this
time (441 before the exclusion card existed); the tables scroll inside their
own wrappers. A partial scan still says so only in numbers — the second dry
run's helpwise page text, unchanged. Neither was chased.

## The decision

**Ready.** The fix list's figures now come from the population the proof beat
counts and agree with it on the page a prospect reads; the pitch is arithmetic
over dimensions a site can earn, labelled as a ceiling rather than a forecast;
the PDF says what the page says; the exclusion is explained in the prospect's
own words; and the send path — link, stranger, file, revocation — worked for
the third time. What remains is legibility and generator polish, none of it a
number a prospect can disprove. A pilot agency can generate and send a real
prospect report with this, and I would stand behind the one that was generated
today.

## Open

**A generator brief**, small: rewrite rather than drop a fix that trips the
banned-claim guard, and reconcile generated dimension fixes with the canned
audit fixes they overlap. **Citation Strength's real definition** is unchanged
from `98c9252`. Unchanged: the third-engine / Epic 12 fork stays paused, and
Epic 12's "why" engine remains recommended-against until the pilot runs.

# Epic 13 — the sidebar becomes the map: a client workspace, and a decision reversed on purpose

Governance line, per north-star.md §8.1: the frontend shell and navigation
(`apps/web/src/components/shell`, `apps/web/src/components/client`) and the
visual treatment of the screens under `/clients/[clientId]/*`. No lifecycle
phase directly — this is the operator's tooling. Scoring, the scan pipeline
and the report/PDF path are untouched; `reportIsolation.test.ts` is the proof
rather than the promise.

## What is being reversed, and by whom

Epic 9.20 built a client's own space and put its navigation **inside the
content area** as a second-level strip (`LocalNav`), and the reasoning is
recorded three times — in `ClientSpace.tsx`'s header, in `LocalNav.tsx`'s, and
in `design-system.md` §5e:

> *The agency sidebar stays exactly as it is. It is agency-wide — every item in
> it is about the whole account across every client at once — and depth about
> one client cannot go there without either changing what those items mean or
> inventing a global "selected client" the rest of the product does not have.*

**This epic does exactly that, deliberately, on the founder's decision.** The
product now wants a persistent per-client workspace whose sections are
discoverable from the sidebar itself, rather than from a strip nested one level
down — the way `scoring-spec.md`'s changelog records a superseded formula
without deleting the record of what it was and why. The record above stands;
what follows is why the ruling changed.

**Why it changed.** In 9.20 a client had three sections and the strip was a
row of three words. Eleven epics later it has ten (Overview, Report, Sources,
Rankings, Sentiment, Technical, AI crawlers, Answer gaps, Prompts, Alerts),
grouped into two clusters, and an operator's working day is spent *inside one
client at a time* — reading its sources, then its rankings, then its gaps.
Every one of those moves is a step across a strip under a header, while the
sidebar to the left holds four agency-wide items the operator is not using.
The persistent frame carries the least-used navigation and the content area
carries the most-used. The founder's call is that the sidebar should carry
the operator's map, and that a "selected client" is not an invention — it is
already in the URL of every screen under `/clients/{id}/`.

**What the reversal costs, stated.** 9.20's argument was right about one
thing that still has to be true: the sidebar's meaning must not become
ambiguous. So the sidebar switches **modes** rather than mixing levels — in
agency mode it is agency-wide exactly as before, with the Clients item grown
into a disclosure listing every client; in client mode it is *that client's*
map, with a way back at the top. An item in the sidebar is always about one
thing, and the head of the sidebar says which.

## How the "selected client" is known

**From the route, not from a store.** Every screen under
`/clients/[clientId]/*` already receives the id as a route param and already
loads the client's record and history through `useClientDetail`. The pages
hand that record to `ClientSpace`, which hands it to `WorkspaceShell` as a
prop; the shell renders client mode when a client is present and agency mode
when one is not. There is no context, no global state and no second source of
truth: the selected client is whatever the URL says it is, and a screen with no
client in its URL has no selected client. `lib/` was checked first — no
existing pattern reads `useParams` anywhere in the app, and the prop-driven
split every view already follows (pure view, fetching route) keeps the shell
statically renderable and testable in both modes.

## What is preserved

* **Every URL.** No route moved. `LocalNav` stays in the design system, its
  reasoning intact and superseded by a note pointing here; nothing in the app
  renders it any more.
* **The accent clusters.** `CLIENT_NAV` in `clientNav.ts` is the single table
  the sidebar's client mode reads, clusters and cluster-relative accents
  included, so `clientNav.test.ts`'s invariants hold unchanged. The Report
  item stays unaccented and external.
* **The report and the PDF.** Untouched, and the isolation test still scans
  them.

The rest of this epic — the primitives, the hero, the reskins, the new
Competitors section — is recorded in the entry that follows once it is built.

# Epic 13, continued — Competitors: the field drawn in the score's own shape, and the number it refuses to say

**2026-09-09.** The section the previous entry left "recorded in the entry
that follows once it is built". Governance line unchanged: the frontend shell
and the screens under `/clients/[clientId]/*`, plus one opt-in pair on a
design-system component. Scoring, the scan pipeline and the report/PDF path
are untouched, and `ReportView.test.tsx` now asserts the document carries
neither of the two new ledger classes rather than promising it.

## What was found before anything was built

`sidebars.tsx` had imported a `Users` icon for a `competitors` entry in
`SECTION_ICON`, and the entry had been cut during Epic 13's cleanup because
`ClientSection` had no such member. The brief's intent for the section was a
grid of mini-ledgers, one per competitor, drawn with `LuminanceLedger` in a
compact mode. Two facts in the repo decide what that can honestly be:

* **There is no per-competitor composite, and there deliberately never has
  been.** api-contracts.md says so twice (Epic 5, and again under the history
  endpoint): sentiment is classified toward the subject only and the technical
  audit is of the subject's own site, so 25% of the composite's weight has no
  rival input. The report declined to draw competitor ghost columns for exactly
  this reason (Epic 7's entry), and the Rankings tab plots share of voice
  rather than a rival score for the same one.
* **A rival's figures live on the report, not the history.** `ClientHistory`
  carries per-rival mention rate, share of voice and citation strength scan by
  scan, but the client's own side only as a composite and a share of voice —
  so the client's column could not be built from it. The latest scan's report
  carries both sides at once, already shaped, and it is the document the
  Report item opens.

So "a mini-ledger per competitor" cannot mean "a small ledger with a rival's
score on it", because there is no such score. What it can mean is the rule the
report states in a sentence, **drawn**.

## The two opt-ins on the ledger, and the guardrail they inherit

**`compact`** draws the column at a third of the width for a grid: the label
gutter goes (one legend beside the grid, not five labels per column), no gap is
annotated, and the figure bounds itself so its 12px values are the size
rendered. The identity is not touched — `render.test.tsx` pins the height and
asserts a compact column's lit rects are the same heights as a full one's,
which is the property that makes two columns side by side comparable at all.

**`LedgerDimension.measured: false`** marks a dimension this subject has no
reading on. The layout ignores it, on purpose: the segment is drawn at the same
height and place, so a rival's column keeps the client's five-segment shape.
The component reads it to hatch the segment in the hairline colour (never the
ramp — an unmeasured sentiment must not read as a dim low one), to print
"Not measured" in the hidden table, and to make **no composite claim** for the
column: nothing spoken as "out of 100", a footer that says *No composite —
measured on 3 of 5 dimensions*, and no gap annotation, because the largest
unlit area is a dimension nobody measured rather than a gap this subject can
close. It is distinct from `unmeasured`, which is the whole column, and the
test asserts the two never carry each other's markup.

Both default off. Neither report route passes anything, so the document gets
Epic 0's drawing — the same guardrail `staggerDimensions`, `unmeasured` and
`bounded` each arrived with, and the same regression test extended to cover
them. The style guide's ledger section shows the compact grid with three
invented rivals.

## The screen

`/clients/{id}/competitors`. One column per brand from the latest scan's
report: the client first, then rivals in detection rank. The client's column
is its score — the one composite on the page, and the one the report shows.
Every rival's column is the client's shape with sentiment and technical
foundation hatched, its three readings printed under it with a signed lead
over the client, and how many of this client's scans carried it (a set is
re-detected per scan, and "in 1 of 3 scans" is a different fact from "in 3 of
3"). A rival with no figure on a comparable dimension is hatched there too,
rather than scored zero; a rival named in no answer scores a real zero and
lights nothing. "Who leads on what" beside the grid is read off the same deltas
the cards print, so the two cannot disagree.

**`ClientCompetitorsView.test.tsx` counts composite claims on the page and
expects exactly one**, however many rivals the scan found. A screen of six
ledgers side by side is the easiest place in the product to imply a rival
score, and that assertion is the line.

Four empty voices, each saying what is the case: never scanned; the latest scan
has no report; the latest scan was not scored (a rival's column is built to the
client's shape, and there is none); no competitor set, pointing at the report,
which is where a set is detected or named by hand. The report read is allowed
to 404 without failing the screen, the way the Technical route treats a missing
audit.

## Where it sits in the nav, and why

**Investigation, beside Answer gaps, accent 3.** On its own merits: no row it
shows was written for it — the per-rival figures are derived on read
(`CompetitorComparison` has no stored column), and this screen derives one step
further, which is what Investigation means. Rankings says who is ahead over
time, Answer gaps says on which questions, this says on which dimensions.
Measurement is also full — `clientNav.test.ts` has said so since Epic F — but
that is the constraint, not the reason, and the test's comment now records
both.

It is inserted **above** Prompts and Alerts, which is the case the accent
model was built for: accents are identities, so Prompts is still hue 1 and
Alerts still hue 2, and the new test asserts that rather than trusting it. The
`Users` icon takes the seat it was imported for.

## Verified

* Design system: 459/459 (447 + 12 new), typecheck clean.
* Web: 795/795 (756 + 39 new: 14 on the field's columns, 20 on the screen, 4
  extending the report-path regression, 1 on the nav table), typecheck clean.
* Node 22, esbuild pinned at 0.25.12 (commit b01a908).
* Not verified live: neither the API nor the web server nor the database was
  running in this session, and standing all three up for one screenshot was
  judged not worth it against a screen that is fully reachable by static
  render. The first live look at this grid is still owed, and the compact
  column's 12px value at real pixel size is the thing to check first.

# Epic 14 — the dark identity: a full visual redesign, and Epic 0's direction reversed on purpose

**2026-09-09.** Governance line, per north-star.md §8.1: the design-system
token and component layer (`packages/design-system`) and the visual treatment
of every Working screen in `apps/web` — the shell, the dashboard, the clients
list, a client's whole space, and the signed-out landing, auth and settings
screens that share the tokens. No lifecycle phase; this is the operator's
tooling. **Out of scope and untouched:** the report and PDF path (`ReportView`,
`/scans/{id}/report`, `/share/{token}`, `services/pdf.py`) and the scoring
pipeline. `reportIsolation.test.ts` and `ReportView.test.tsx` are the proof.

## What is being reversed, and by whom

Epic 0 chose a light, paper-derived, editorial-serif identity, and
`design-direction.md` §0 gives the reason in one line: *the presenting context
is the one that closes deals, so it wins the ties.* Every Working screen since
— nine epics of them, through 9.19's width table and 9.24's accent layer —
was built on that ruling, and the ruling was restated each time.

**This epic reverses it for the product, deliberately, on the founder's
decision.** The founder saw the light system live and rejected it: a dark,
data-dense analytics identity in the genre of AI-visibility / GEO dashboards,
not a skin over the paper one. The record stands, the way Epic 13 kept 9.20's
sidebar argument beside the decision that reversed it; `design-direction.md`
§7 now says, argument by argument, what survives and what does not. What
survives is the half of §0 that was always about the *document*: the report
still renders on Epic 0's palette, serif and elevation, scoped to its own
root class so it carries its skin with it and its markup did not change by a
byte.

**How the direction was set — and the constraint that bounded it.** The
brief named a genre, not a screen. No competitor product was opened,
screenshotted or navigated to at any point (ip-safety.md #1's actual rule).
Every screen was derived from `scoring-spec.md`'s five dimensions,
`ClientHistory` and `CompetitorSet`, and from what an operator does with
them; the genre supplied only the vocabulary — dark ground, hero KPI, card
grid, weighted charts, saturated accents.

## One confirmed direction

Stated before it was applied anywhere, and applied everywhere:

* **Font.** Space Grotesk (OFL, Google Fonts) for headings, KPI figures and
  the hero numeral — a geometric grotesk with real character in its figures
  and true tabular figures. IBM Plex Sans and Mono stay for interface and
  evidence. Fraunces survives only inside the report.
* **Accents.** Two, saturated. `beacon` retuned to electric cyan for the dark
  ground — still the client's colour, same hue, so the meaning system holds —
  and a new `signal` mint that exists only as beacon's gradient partner on the
  primary button and the glow behind a hero. Hero numerals take a gradient
  **derived from the visibility ramp at that score**, so a 12 glows dim clay
  and an 88 glows lit cyan: the founder's gradient hero and Epic 0's
  visibility-is-luminance metaphor are the same object.
* **Ground.** Deep cool charcoal, never pure black; sidebar one tone darker,
  cards one tone lighter, warm text on cool ground (Epic 0's hand-off
  inverted, not abandoned).
* **Cards.** 16px, seated surface, 1px inner top highlight, ground-tinted
  shadow, hover lifts one tone. Shape lock: cards and hero 16px, buttons and
  inputs 8px, chips pills.
* **Charts.** A gradient area under the client's line, opt-in, never on the
  report.

## How the token layer was replaced without touching the report

`tokens.css` now declares `:root` as the dark identity and
`[data-theme='light'], .avp-report` as Epic 0's paper system verbatim — every
colour, both elevation sets, the original 3px/5px radii. A report surface
cannot acquire the dark identity by omission because the document's own root
class carries the paper scope. `tokens.test.ts` was rewritten to read each
value from the block it belongs to (the first `--avp-beacon-600:` in the file
is now the dark cyan, and a test matching the first occurrence would have
compared the report's teal against it), and asserts every paper override has
a dark counterpart.

The TypeScript side keeps the paper tables as canonical — `visibilityColor()`
and `seriesStyle('subject')` are literals baked into SVG fills that land in
PDFs — and adds `dark`, `signal`, `benchDark`, `visibilityColorDark()` and
`heroGradient()`. The Working screens' components paint from those:
`ScoreMeter`, `NavScore`, the sidebar's dots, and two opt-ins on charts the
report also renders — `LuminanceLedger palette="working"` and
`TrendChart area` — both defaulting off and both added to
`ReportView.test.tsx`'s regression list, alongside `avp-hero`.

Every rule in `components.css` was moved from raw paper/ink literals to role
tokens so one rule renders on both grounds; the places a literal was load-
bearing (a ledger's baseline, a badge's text on a warn wash, a primary
button's label) became `--avp-line-ink`, `--avp-on-warn`, `--avp-on-accent`,
resolved per scope. The report section of the stylesheet is the one place raw
paper/ink still appears, and it only ever renders inside `.avp-report`.

## The screens

* **Shell.** Structure untouched — Epic 13's two modes, `clientNav.ts`'s
  clusters, every URL. Skin: sunken sticky sidebar, cyan rail, bench-hued
  icons, the display face on the head and the client's score.
* **Dashboard.** `PageHead`, then `ScoreHero` with the portfolio median and
  the latest reading per client beside it, then four count tiles as a card
  grid, then the recent-scans table in a card. Seats moved from a tile to the
  head; the median moved from a tile to the hero; the "a score takes no
  accent" test moved with it.
* **Clients.** `PageHead` with the primary action, four tiles, the table in a
  card.
* **Client Overview.** `ScoreHero` with the latest composite, the change
  since the previous scored scan, share of voice, rivals, and the composite's
  own line across the history (area-filled, drawn at the width it is shown
  at) beside it. The "Latest" tile is omitted there because the hero is it.
* **Sources, Rankings, Sentiment.** Chart and value table together in a card;
  the client's line filled.
* **Competitors.** Compact ledgers on the dark ramp.
* **Everything else** — Technical, AI crawlers, Answer gaps, Prompts, Alerts,
  Settings, the landing page, sign-in — reskinned by the tokens and the
  display face, checked one by one.

## Verified — and how, because the how is the finding

Design system **538/538**, web **799/799**, both typechecks clean. Node 22.

**Not verified in a signed-in session, and not for want of trying.** The API,
web server and database were all running, but every route to a session was
blocked by the auto-mode permission classifier as a credential action —
resetting a dev account's hash, and even a plain sign-up through the public
API. No existing account's password is recorded anywhere in the repo. The
block is correct and was not worked around.

What was done instead is close to as good and is recorded so the next visual
epic starts there: a temporary vitest file rendered every screen from the
repo's own fixtures, wrapped in the real `WorkspaceShell`, to static HTML;
the app's stylesheet was compiled with the Tailwind CLI from the same config;
Chromium screenshotted the pages with the real fonts. Same markup, same CSS,
same type — minus live data and hydration. Eighteen screens were checked that
way, in three stages (tokens in the live styleguide on :4100, then the shell,
then each screen as it was rebuilt), and the harness was deleted before the
test count above. `docs/screenshots/epic-14/`. **The first live look at the
hero's reveal and a running scan's live badge on the dark ground is still
owed** — both are motion, and a static render cannot show them.

**IP-safety check passed:** no competitor product, screenshot, markup or
stylesheet was opened or referenced; every screen was derived from
`scoring-spec.md`, `ClientHistory` and `CompetitorSet`; the one new dependency
is a font (Space Grotesk, OFL-1.1, Google Fonts — ip-safety.md #4), no
package was added; the report renders no bench token, no working palette and
no hero (`reportIsolation.test.ts`, `ReportView.test.tsx`); nothing here
stores or renders third-party prose.

**Left on the table, stated:** the impeccable design hook flags the hero
numeral's gradient text as a common tell. It is the founder's explicit ask
and it is derived from the score rather than decorative, so it stays; the
finding is acknowledged rather than suppressed. And the Epic 13 Competitors
work that was uncommitted in the working tree when this session started is
still uncommitted beside this — it was not touched beyond the ledger palette
opt-in, and the two should be committed as two entries.

# Epic 14.2 — the sidebar's client list never arrived

**2026-09-09.** Governance line, per north-star.md §8.1: the frontend shell
(`apps/web/src/components/shell`). No lifecycle phase; no other layer.

## The finding

Opening the Clients disclosure in the sidebar left "Loading clients…" on
screen indefinitely for the `Local Dev Agency 2` account. The brief's
diagnosis order was followed and stopped at its first step: the disclosure's
`onToggle` did flip `clientsOpen`, both reads did fire, and the list never
left `loading` — for that account and for every other, because the cause was
in the effect's own wiring rather than in any response.

`WorkspaceShell`'s effect depended on `[clientsOpen, list.kind]` and set
`list.kind` to `loading` inside its own body. React then re-ran the effect
for the dependency it had just changed, running the previous run's cleanup
first — which flipped that run's `cancelled` flag — and the new run returned
early because the list was no longer `idle`. The response arrived into a
closure told to discard it. `sidebarClients()` was never reached, so the
shape check in step 3 never mattered, and `GET /clients` for that agency
returns its one client correctly (confirmed against the database: one
client, one scan).

## Why nothing caught it

Every test in `apps/web` renders to static markup, and `navModes.test.tsx`
renders `ready`, `loading` and `error` by prop. All three states were
covered; the one thing a static render cannot show is a state that never
*arrives*. Epic 13 also recorded that it was never verified live.

## The fix, and the test that would have caught it

The fetch is keyed on the disclosure opening and nothing else: a ref makes it
fire once per mount, and the only thing that cancels it is the component
unmounting. `WorkspaceShell.test.tsx` mounts the real component in jsdom
(`jsdom@30.0.1`, MIT, added as a web devDependency — it was already in the
workspace store for the design system), clicks the real button, waits for
the mocked reads, and asserts the rows; it also asserts the error branch and
that toggling twice does not fetch twice. Run against the unfixed effect it
failed with `'All clientsLoading clients…'`, which is the screen the founder
saw.

## Verified

Design system 538/538, web 803/803 (three new), both typechecks clean.

# Epic 14.1 — the report on a desk: route-level framing, the document untouched

**2026-09-09.** Governance line, per north-star.md §8.1: the two report
routes (`apps/web/src/app/scans/[scanId]/report`, `apps/web/src/app/share`)
and one wrapper rule plus one token in the design system. The report itself
— `ReportView`, the report primitives, every token the document resolves —
is not touched, and the PDF is not touched.

## The finding

On the live Epic 14 build the report's 52rem paper column dropped straight
onto the app's dark ground, with no framing, and read as clipped or stranded
content — a phone screenshot on a desktop — rather than as "a document,
deliberately narrow". The width is right and stays. The surface around it
was the bug.

## The fix

**A desk.** `--avp-surface-desk`, `oklch(0.905 0.01 75)`: a light warm
neutral that is neither the page's white nor the app's ground, the same in
every theme, because a printed page sits on a light surface whatever the
app around it looks like. `.avp-report-frame` paints it, and the page on it
gets a hard contact edge and a soft drop in the ink hue the paper scope
already uses for its own elevation. A print strips the desk, so the PDF is
the page and nothing else.

**Wide, on the authenticated route.** `/scans/{id}/report` now runs the
workspace shell to the app width and centres the page on the desk, which is
what a document viewer does; the share and download controls sit above the
desk at the report's measure. `/share/{token}` makes the desk the whole
viewport — a stranger opening the link gets a page on a surface, edge to
edge.

**What did not change.** `ReportView.tsx`, `ReportLayout.tsx`, `.avp-report`'s
own rule, and every value inside the paper scope. The framing rule targets
`.avp-report-frame > .avp-report`, so a report rendered without the frame —
the PDF path, the styleguide — is byte-identical to before.
`ReportView.test.tsx` (unchanged) and `reportIsolation.test.ts` (extended
only by the new `desk` suffix in its resolvable-utility list) both pass.

## Verified

Design system 538/538, web 803/803, both typechecks clean. Both routes
rendered from the report fixture against the compiled stylesheet and
screenshotted — `docs/screenshots/epic-14/after-report-route-framed.png`,
`after-share-route-framed.png` — the same fixture-render path Epic 14
recorded, because a signed-in session is still not reachable from here.

# Epic 15 — light by default, dark by choice: Epic 14's default reversed on purpose

**2026-09-09.** Governance line, per north-star.md §8.1: the design-system
token and component layer (`packages/design-system`), the web shell's
provider and Settings (`apps/web/src/components/shell`, `settings`), and
`layout.tsx`. No lifecycle phase. The report and the PDF path are untouched
and the tests that hold that are extended, not relaxed.

## What is being reversed, and by whom

Epic 14 — committed earlier today — made dark the product's *only*
identity, on the founder's decision, and `design-direction.md` §7 recorded
why. Having looked at the live dark build, **the founder reversed the
default**: light is the default for every Working screen, and Epic 14's
dark identity is kept whole as an opt-in a user chooses in Settings. This
entry names that the way Epic 14's named its reversal of Epic 0, and the way
Epic 13's named its reversal of 9.20: §7's reasoning stands and is not
deleted; §7's premise that dark is "the identity, not an opt-in" is
superseded, and `design-direction.md` §8 says so beside it.

## Two things the brief asked to confirm, decided rather than assumed

The founder was not available mid-task, so both were decided in the
direction the brief itself leaned, and both are cheap to reverse.

**"Light" means Epic 14's language on a light ground, not Epic 0's paper.**
The brief steered here — keep the shape lock and the display face, do not
revert typography — and Epic 0's paper system is now the *report's* skin,
which the toggle must never reach. So `:root` is a warm off-white ground
(`oklch(0.975 0.006 75)`), the sidebar one step below it, white cards, the
same 16px / 8px shape lock, Space Grotesk on every heading and figure, the
same card grid and hero. If the founder meant "revert to Epic 0", the
difference is one token block: `:root` takes the paper values and nothing
else here moves.

**Three options, not two.** Light (the default), Dark, Match system. Three
is the superset; dropping the third is a one-line change. `defaultTheme` is
`light`, not `system`, because the founder's decision is that light is the
default and following the OS is a choice rather than the starting point.

## The restructure — deliberate, not a find-replace

`tokens.css` now has three scopes: `:root` (light), `[data-theme='dark']`
(Epic 14, verbatim), and `.avp-report` (Epic 0's paper, verbatim). The
report block used to be `[data-theme='light'], .avp-report` — one selector
meaning both "the report" and "light mode", which is exactly the seam
through which an app-level toggle would have reached the document. It is
scoped to the report's root class alone now, declared after the dark block
so it wins at equal specificity when a document sits inside a dark app.
`tokens.test.ts` was rewritten for three scopes: each value is read from
the block it belongs to (a `light` table joins `dark` in `color.ts`), every
override in the dark and report blocks must have a default in `:root`, and
**no theme attribute may share the report's rule** — a comment naming the
old selector tripped that test on the first run, which is what it is for.

**The light beacon is inside sRGB, and Epic 0's was not.** Hue 200 has a
chroma ceiling of 0.09 at L 0.53; Epic 0's `beacon-600` at 0.125 has been
clamped by every browser since Epic 0 without anyone measuring it. The
light stop is `oklch(0.53 0.09 200)`, white text clears 4.8:1 on it, and
the report keeps its own literal because its fills land in PDFs.

## Paint that cannot be a token, made theme-reactive

A score's ramp colour is interpolated per value, so it has always been an
inline literal — and with two themes a literal is wrong in one of them: the
dark ramp's lit end is unreadable on white, the paper ramp's absent end
vanishes on charcoal. `rampVars(score)` puts BOTH literals on the element
as custom properties, class `avp-ramp` lets the stylesheet pick one by the
theme attribute, and paint reads `var(--avp-ramp)`. A switch in Settings
repaints in place with no render and no JavaScript reading the theme. Used
by `ScoreMeter`, `NavScore`, the sidebar's dots, and the Ledger's Working
palette; `heroVars()` does the same for the hero's gradient and glow, with
`heroGradient(score, theme)` floored at L 0.70 on dark and capped at L 0.62
on light — `onVisibility`'s own threshold — so the figure is legible on
either ground. The hero's delta got the same treatment after the light
render showed "+0.7" in lit cyan on white: `--avp-delta-up/down`, beacon's
deep stop and the ramp's ember on light, the ramp's two ends on dark.

Series paint on Working charts became custom properties outright:
`seriesStyle(…, 'working')` returns `var(--avp-beacon-600)` and
`benchVar(i)`, which `design-system.md` §6 already asked of chrome that
must follow a theme switch, and `render.test.tsx` asserts the property
rather than a literal.

## The provider and the control

`next-themes` (MIT, added to `apps/web`) per `pick-ui-library`'s guidance
for theme switching with no flash on load, rather than hand-rolling it. It
writes `data-theme` on `<html>` from a synchronous inline script before
hydration — the mechanism `layout.tsx`'s `MOTION_READY` already relies on.
Stored in the browser under `avp.theme`, not on the account: it is a
preference about this screen. `ThemeControl` sits in a new Appearance
section of Settings as three `aria-pressed` buttons in a labelled group —
a three-way choice is not a toggle and a sun does not say "match system" —
and reads the choice only after mount so hydration cannot disagree with
the server's markup.

## Verified

Design system **601/601** (538 + 63 new, mostly the three-scope parity),
web **802/802**, both typechecks clean. Every key screen rendered from the
repo's fixtures in BOTH themes against the compiled stylesheet and
screenshotted — `docs/screenshots/epic-15/`, light and dark pairs — plus
the report inside a light app and a dark app, identical in both, and the
styleguide live with its toggle. The same fixture-render path as Epics 14
and 14.1, for the same reason: a signed-in session is not reachable from
here. The live behaviour still owed a real look: the first paint after a
reload in the dark choice (the inline script should make it flash-free),
and "Match system" following an OS change.

**IP-safety check passed:** no competitor product, screenshot, markup or
stylesheet was opened or referenced; the light theme was derived from Epic
14's own tokens and the five scoring dimensions; one dependency added,
`next-themes` 0.4.6 (MIT), recorded here; the report renders no bench
token, no working palette, no hero and no theme attribute
(`reportIsolation.test.ts`, `ReportView.test.tsx`, `tokens.test.ts`);
nothing here stores or renders third-party prose.

# Epic 14.1, corrected — the report route back at the Presenting width

**2026-09-09.** Governance line unchanged from Epic 14.1: the authenticated
report route and nothing else.

## The regression, and the table it broke

Epic 14.1 put `wide` on `WorkspaceShell` for `/scans/{id}/report` to give
the desk room. That was the wrong lever. `wide` pins the shell's content to
`--avp-app-max`, the **Working** width, and `design-direction.md`'s width
table — standing since Epic 9.19 and never amended — governs this route to
`--avp-report-width`, the **Presenting** one. The desk made the mistake
visible rather than hiding it: the frame spanned 90rem, the page inside it
capped at 52rem, and the difference was a dead coloured field either side.

## The fix

`wide` removed. The shell's own default rule is already the document
measure — report width plus its padding — so the desk sizes itself to the
page and no new token was needed. `.avp-report-frame`, the desk token and
the share route's full-viewport treatment stay exactly as 14.1 built them;
`/share/{token}` never used the shell, so it was never affected.

## The guard

`apps/web/src/app/scans/reportWidth.test.ts` scans the route's source and
fails if any `<WorkspaceShell>` in it carries `wide`, with a positive control
on the dashboard route and an assertion that the shell's default measure is
still the report width. Run against the unfixed route it failed on
`' current="dashboard" wide'`. Widening this route again now means amending
the width table first, which is the order the table exists for.

## Verified

Design system 601/601, web 806/806 (four new), both typechecks clean. Both
report routes re-rendered from the fixture in both themes: the desk hugs the
page with an even margin. `docs/screenshots/epic-14/after-report-route-framed.png`
replaced; `docs/screenshots/epic-15/report-route-{light,dark}.png` added.

# ip-safety.md removed — the founder's decision

**2026-09-09.** `docs/ip-safety.md` had sat deleted-but-uncommitted in the
working tree since an earlier session. The founder confirmed the deletion is
deliberate and permanent, so this entry commits it and removes the document's
fingerprints from everything that still pointed at it — the same way every
other reversal in this log is recorded: as a decision, not a drift.

## What went, and what stayed

The **document** is gone, and with it the constraint numbering (`#1`–`#9`),
the "normative, wins over every other doc" authority clause, and the
constraint-9 self-check gate. The **engineering practices** the document
described were never enforced by the document; they are enforced by tests,
the licence audit and the design system's own token layer, and all of that
stays exactly as it was:

- `apps/api/tests/test_ip_safety.py` is renamed `test_facts_only.py` — same
  1194-suite membership, same sweeps, same registered exceptions. Its module
  docstring now states the facts-only rule in full, because with the document
  gone this file is where the rule is enforced and therefore where it should
  be written down.
- `scripts/license_audit.py`, the `FACTS_ONLY_MODELS` sweep, the
  `reportIsolation` tests, the render tests that assert the beat sequence —
  untouched in behaviour. No test was deleted; the brief's rule was to ask
  before removing any check whose reason for existing might be independent
  of the document, and none needed removing to complete the sweep.

## The sweep

Around 230 references across ~125 tracked files, plus two generated files
(`openapi.json`, `api.gen.ts`, regenerated from the edited Pydantic
docstrings) and the built styleguide (rebuilt). Every citation of a numbered
constraint was rewritten to name the rule itself so the surrounding reasoning
still stands on its own:

| was | now |
|---|---|
| `ip-safety.md #1` | the design-from-the-data-model rule |
| `ip-safety.md #2` | the design-system-only rule |
| `ip-safety.md #3` | the narrative-report rule |
| `ip-safety.md #4` | the licensed-assets rule |
| `ip-safety.md #5` | the no-copying rule (`#1` + `#5` together: the no-competitor-reference rules) |
| `ip-safety.md #6` | the dependency-licensing rule |
| `ip-safety.md #7` | the facts-only rule |
| `ip-safety.md #8` | the no-borrowed-copy rule |

Where a comment only cited the document and the fact beside it still holds
(Google Fonts under OFL, Lucide under MIT, psycopg excluded for LGPL), the
fact stays and the citation goes. Nothing in the reasoning was deleted
wholesale.

Standing docs: `README.md` no longer lists the document as required reading;
`north-star.md` §0 drops the "does not outrank ip-safety.md" clause and its
§1 restatement of the two no-copying rules now carries them in its own words;
`product-spec.md` drops the phantom "Section 2" pointer; `design-system.md`
and `design-direction.md` keep every constraint they described, unnumbered.
`north-star.md`'s note on the `IP-safety check passed:` line now records that
the line was retired today. Past build-log entries that carry it stay as
history and were not rewritten.

## Verified

API 1194/1194 and ruff clean, workers 13/13, design system 601/601, web
806/806, shared-types 53/53, all three typechecks clean. One grammar
regression from the mechanical pass (a joined line over ruff's 100-column
limit) was caught by ruff and fixed before commit.

# Epic 16 — the report's first beat: hierarchy and rhythm, not a reskin

**2026-09-09.** Governance line, stated first because it is the constraint
that shaped every choice below: the report stays in the print-safe editorial
idiom — Epic 0's paper palette, Fraunces, hairline and flat elevation. No
gradient, no glow, no dark ground, no card grid. Everything here is a
hairline, a seated tone or the paper ramp, and it has to survive a greyscale
photocopy in front of a CMO. The beat sequence, the narrative derivation,
`lib/report/derive.ts` and the PDF path are untouched.

## What was weak

The founder's mockup (`report-header-redesign-mockup.html`) named four
things, and the "before" capture agreed with all four:

1. **The byline read as a debug dump.** Domain, industry, prompt count,
   engine count, scan date: five facts at one grey weight, space-separated.
2. **The score had nothing to look at.** A bare numeral, its band and a
   badge, with the Luminance Ledger — the product's signature chart — one
   beat further down and nowhere near the number it explains.
3. **No frame** around the score and its explanation; the eye took the
   number and left the sentence.
4. **The badge's colour matched the numeral's by coincidence.** The numeral
   rounds before it paints; the badge was handed the unrounded composite and
   interpolated the ramp a fraction away. Same on most scores, tied on none.

## What was built

Two report primitives, one prop, and the score beat recomposed.

**`ReportMetaItem`** (`ReportLayout.tsx`). One byline fact, given a shape:
the seated tone, a hairline, the chip radius the shape lock already reserves
for chips, and a Lucide glyph at 13px that is `aria-hidden` because the text
beside it is the fact. `mono` for the domain. Level-1 elevation only, so it
prints; never on the ramp, so it cannot be read as a score, and the buffer
between a byline chip and `VisibilityBadge` is structural rather than a
matter of restraint. Lucide only (Globe, Building2, MessageSquare, Bot,
CalendarDays) — the mockup's hand-drawn SVGs were for the demo.

**`ScoreBlock`** (`ReportLayout.tsx`). A rule above, a rule below, the
figure and its prose in two columns between them, one column under 40rem.
Most of what makes the mockup feel composed is this, and it is two
hairlines.

**`ScoreDisplay badge`.** The badge is now rendered by the numeral's own
component from the same `rounded` value the digits paint from. One number,
one call to the ramp for each surface. `ReportView.test.tsx` extracts the
two `oklch(...)` strings from the rendered beat and asserts they are equal —
on the 58.24 fixture they were not, before.

**The compact Ledger, beside the numeral.** Epic 13 built `compact` for the
Competitors grid, and the brief's instinct was right: this was "surface a
chart that already exists at a smaller size", not new UI. The score beat now
draws `<LuminanceLedger compact annotateGap={false}>` at the numeral's side,
bounded at its own 100px, so the number is never seen without the shape that
is its explanation. The full column still follows in the gap beat, where its
gutter labels and gap annotation do work the small one cannot. Drawn only
when `narrative.status === 'scored'`: an unscored scan shows the em dash and
no column, because a column at zero is a measurement nobody took. The prose
was amended from "drawn below as light" to "drawn as light beside it and in
full in the next section".

## A written rule, amended first

`ReportView.test.tsx` carried an Epic 13 guard: *the report never carries
`avp-ledger--compact`*. Its purpose was that the document should not change
by accident. This epic changes it on purpose, so the guard was narrowed
rather than deleted and no weaker for it: the gap beat's full column is
asserted never compact, no more than one compact column may exist, and the
partial hatch is still something the document never carries. The positive
half — exactly one compact column, inside the score beat — lives in the new
Epic 16 block. `LuminanceLedger`'s `compact` doc and `design-system.md`'s
"what the report must never acquire" were amended in the same commit, the
order `design-direction.md` asks for. `design-direction.md` itself has no
rule about the byline or the score beat's composition, and the print-safe
constraint in §0 is honoured, so it was not touched.

## Verified

Design system **604/604** (three new: the owned badge, the meta item, the
block), web **811/811** (five new, one amended), both typechecks clean.
Rendered from the repo's fixtures through the same static-render path Epics
14 and 15 used — `docs/screenshots/epic-16/`: the score beat before and
after on the Help Scout fixture, the weak-signal fixture before and after,
the unscored fixture after (em dash, no column, no badge), and the after at
420px, where the figure row still fits and the prose follows it. The
styleguide's report section now shows the same primitives and was rebuilt.

# Epic 16.1 — Dashboard and Clients: the fact's shape, cascaded

**2026-09-09.** Governance line: Working screens only, both themes; the
report is untouched except that its byline chip now shares one primitive
with the rest of the product. `reportIsolation.test.ts` still passes, and
the report's own `ReportView.test.tsx` byline assertions were updated to the
shared class names in the same commit.

## The audit, and what it actually found

The brief asked for the report's "flat facts, no hierarchy" pattern to be
looked for on the Dashboard and Clients screens. Looked for honestly, it is
in two places on the Dashboard and nowhere on Clients:

- **The seats fact beside the dashboard title.** A sentence at one weight,
  with the ratio in the display face doing a badge's job.
- **The hero's meta line.** One fact on the dashboard ("Across 10 scored
  scans below"); on a client's Overview the same slot carries three —
  scan date, share of voice, rival count — space-separated at one weight,
  which is the report byline's failure exactly. `ScoreHero` is shared, so
  the fix is one primitive, not two.

Clients has no such line. Its head is a count and a button, its tiles carry
the accent layer, and its rows already set name over domain in two weights.
The before capture is kept (`clients-light-unchanged.png`) and the markup
was diffed before and after this commit: byte-identical. Inventing a
treatment for it would have been the thing the brief warned against.

## What was built

**`MetaChip`** — a fact, given a shape. The report's `ReportMetaItem` from
Epic 16, promoted: sentence case, an `aria-hidden` Lucide glyph, the seated
tone and a hairline, `mono` for a domain. Its CSS is token-only, so ONE rule
draws the paper chip inside `.avp-report` and the light and dark Working
chips, and a theme switch repaints it in place — which is what "cascade
rather than six one-off treatments" needs from a primitive. `ReportMetaItem`
is now `MetaChip` wearing the report's class; nothing about the document
changed.

**The rule that keeps three pills apart**, stated in the component and in
`design-system.md`: `Badge` is a STATE (uppercase, tinted by a tone),
`VisibilityBadge` is a SCORE (filled from the ramp), `MetaChip` is a FACT.
It has no `tone` and no `accent` prop on purpose. A chip that could be
tinted could be misread as either of the other two, and the hue buffer
`design-direction.md` §1 keeps between categorical and meaning-bearing
colour is worth nothing if a neutral element can opt into either.

**Applied on the dashboard:** the seats fact is a chip with the Users glyph;
the hero's denominator is a chip with the Layers glyph. The hero's
no-median sentence stays a sentence — an explanation in a pill would read as
a state the operator has to clear. The Overview's three-fact meta line is
Epic 16.2's, per the brief's ordering, and will use this primitive.

## Verified

Design system **605/605** (one new, one amended), web **811/811** (two
amended), both typechecks clean, styleguide rebuilt with a chip row beside
the badges so the three pills can be told apart on one screen. Rendered
through the same fixture path as Epics 14–16 — `docs/screenshots/epic-16-1/`:
the dashboard before and after in both themes, and the unchanged Clients
screen.

# Epic 16.2 — the client screens: one primitive, cascaded, and an honest audit

**2026-09-09.** Governance line: Working screens inside one client's space,
both themes. No design-system change — the point of Epic 16.1's `MetaChip`
was that this epic would need none, and it did not. The report is untouched.

## The audit, screen by screen

Each screen was read in source and looked at in a fixture render before
anything was changed, for the two patterns the brief named — the weak tile
and the flat metadata line.

| Screen | Weak tile? | Flat metadata? | Done |
|---|---|---|---|
| Overview | No — the hero is a score, the tiles are counts | **Yes.** The hero's meta line: delta, scan date, share of voice, rival count — four items of grey text at one weight | Three facts are chips; the delta stays text |
| Technical | No. The earlier capture's `undefined` on "Pages crawled" was that harness's incomplete fixture; the field is a non-nullable `number` on the wire | No — a check row is name, badge, mono code, in three weights already | Nothing |
| AI crawlers | No | **Yes.** "named as amazonbot" / "via User-agent: \*" at 11px tertiary beside a verdict badge — the one row worth reading, set as the quietest thing in it | The rule is a mono chip beside the badge |
| Answer gaps | No | No — the grid's intent labels are categorical, and a chip per row would be noise | Nothing |
| Prompts | No — a run's three tiles are counts | **Yes, mildly.** A run's stamp as tertiary text under the question | The stamp is a chip, so a run's header reads as the report's does: the question, its state, its credentials |
| Alerts | No | **Yes.** The engine beside the badge as plain text, "Acknowledged" at 11px, and the two dates compared as a caps line | Engine, acknowledgement and the date pair are chips; the badge is the only badge |

Sentiment, Sources, Rankings and Competitors were grepped for the same
patterns and carry none; they were not in the brief's list and were not
touched.

## The rule this exercised

Every change above is the three-pill rule from Epic 16.1 applied: a
**state** is a `Badge`, a **score** is a `VisibilityBadge`, a **fact** is a
`MetaChip`. On the Alerts row that rule does real work — the row used to
carry one badge and two lines of grey text, and now carries one badge and
three facts, so an operator can tell at a glance which pill is the thing
that changed and which pills are the circumstances. The test asserts one
badge per row. On the Crawler row it does the same: a verdict, and beside
it the evidence for the verdict, in the mono face because it is a token the
operator may need to find in the file.

Two things were deliberately left as text. The Overview's delta ("+0.7 since
last scan") is a reading with a direction, not a credential, and its colour
is the ramp's two ends by Epic 14's rule; and the hero's no-reading sentence
stays a sentence, because an explanation in a pill reads as a state to
clear. Both are asserted.

## Verified

Web **814/814** (four new, two amended), typecheck clean; the design system
was not touched and stands at Epic 16.1's 605. Rendered from the repo's
fixtures through the same static path — `docs/screenshots/epic-16-2/`:
Overview, AI crawlers, Alerts and Prompts before and after on the light
theme, and Overview and Alerts after on the dark theme, where the chip
sits as a sunken well on the card and reads the same.

# Epic 16.3 — micro-interactions: three additions, five refusals, zero new values

**2026-09-10.** Governance line: Working screens only; the report document
is untouched and its regression guards still hold. Every duration and curve
used below is one of `design-direction.md` §4's existing four durations and
three curves. **Zero motion values were added**, and §4 now carries a
paragraph recording this epic's additions and refusals so the next brief
does not re-derive them.

## The gate, and what survived it

The `find-animation-opportunities` skill was loaded and its four questions
— frequency, named purpose, budget, function — were put to every seam its
hunt list names. The recon found the system already close to right: press
feedback on every pressable, hover gated to real pointers, the sidebar
panel on `grid-template-rows`, badge tones crossfading under a 5s poll, a
meter that eases when it moves, scan-swap dimming behind `aria-busy` on
both screens that swap scans, and reduced motion collapsed at the base
layer with the delay hole closed. Three places survived:

| # | Where | Today | Purpose | Tier | Built |
|---|---|---|---|---|---|
| 1 | `LoadingState`, 21 call sites | Text only; a slow fetch reads as frozen | State indication — *alive*, not *progressing* | Every load, ambient loop, blocks nothing | The live badge's breathing dot before the message: same keyframe, same `calc(reveal × 3)` period, `aria-hidden`. No spinner, no bar — that test still passes |
| 2 | Alerts, "Show N acknowledged" | Rows materialise in one frame | Preventing a jarring change | Occasional, click-fired | `@starting-style` on `.avp-alert.is-acknowledged`: opacity 0 → 0.6 on the hover tier it already used, `translateY(--avp-space-1)` → 0 on the state tier |
| 3 | Prompts, the run a submit produced | After a ~23s wait the list re-reads and a card is simply there | Preventing a jarring change, and feedback that the wait ended | Rare, click-fired | The workbench diffs run ids across the re-read and marks the one new card `is-new`; `@starting-style` opacity 0 → 1, `translateY(--avp-space-2)` → 0, layout tier. Cleared by the next load, so a reload performs nothing |

Both entrances can only fire on a click. Outstanding alert rows are there on
load and get nothing; a card already on the page keeps its place and its
opacity while the new one settles in above it. That is the line Epic 9.19
drew — *no arrival motion is not the same as no motion; move where something
is actually changing* — applied three more times.

## Refused, with the question that refused each

- **The theme switch.** `disableTransitionOnChange` on the provider is
  next-themes' guard against every themed property crossfading at its own
  rate; Epic 15 set it on purpose. A crossfade would have to be the whole
  page at once, which is a different feature. Stays instant.
- **The competitor editor's add/remove rows.** Inside `article.avp-report`,
  which must not gain motion; and its rows are keyed by index, so an
  entrance would refire on every removal below the first.
- **Route loading-to-ready swaps.** Navigation, tens of times a day. The
  gate's frequency tier says no.
- **Empty states' arrival.** The delight tier is where the budget lives, but
  §4's exclusion of the dashboard shell from arrival motion is written, and
  `EmptyAgency` says in its own comment why it passes `animate={false}`.
- **Acknowledging an alert.** Looked like a gap; is not. `justAcknowledged`
  keeps the row mounted and settled, and the code comment records the
  decision: the fix was not to animate the exit but not to have one.

Also cleared on inspection: `Copy → Copied` (the label change is the
feedback; a fade would delay it), `MetaChip` (not interactive, so no hover),
and the scan-swap dimming (already built on both screens that need it).

## Verified

Design system **606/606** (one new), web **816/816** (two new), both
typechecks clean, styleguide rebuilt. `docs/screenshots/epic-16-3/`: the
loading state before and after, light and dark. The two entrances cannot be
shown by a static render, so Playwright re-mounted each element the way its
click does — the acknowledged row inserted into the list, the run card
toggled through `display: none` — and captured the frame 16ms and 30ms in.
The before frames show each element fully settled at that instant; the
after frames show the row faint and low, the card at half opacity and a few
pixels down. A live look at the real click is still owed for feel.

# Epic 17 — the report is 72rem: Epic 9.19's width table reversed for two routes, on the founder's decision

**2026-09-10.** Governance line: a width change only, for `/scans/{id}/report`
and `/share/{token}`. The paper identity — Fraunces, hairlines, flat
elevation, no gradient, no dark ground — is untouched, and so are the beat
sequence, `derive.ts`, and everything Epic 16 built into the first beat.
The reversal is written into `design-direction.md`'s width table itself,
in its own dated note, the way every other reversal here is recorded.

## What was being reversed, and why it was right to reverse it

Epic 9.19's table put the report at `--avp-report-width`, 52rem, on the
argument that a document someone reads and eventually prints wants a
document's measure. Epic 14.1 then fixed a framing bug — a wide desk
around that narrow page — by making the desk hug the page, and its
correction added a test that the route never widens. Both were right on
their own terms, and the founder then looked at the result live twice and
found the page itself narrow beside a 1440px screen. A well-fitted desk
around a narrow page is still a narrow page. This entry does not claim the
old width was wrong; it records that the founder decided otherwise after
seeing it, which is the one reason the table can be overruled.

## Picking the width — rendered, not guessed

Both candidates the brief named were rendered at 1440 and 1920 through the
fixture path before anything was changed
(`docs/screenshots/epic-17/`):

- **At 1440 the two are the same page.** The shell's content area is the
  limit, so 72rem and 90rem both fill it edge to edge and the complaint is
  resolved either way.
- **At 1920 they differ.** The 90rem page strands its 68ch prose in a field
  of margin (`candidate-90rem-report-route-1920.png`); the 72rem page still
  reads as a document, and the extra width goes to the parts that can use
  it — the score block's two columns and the evidence tables.

**72rem, then.** The prose measure did not move: paragraphs stay at
`--avp-measure`, so nothing is set at a hundred characters a line because
the container grew.

**The one thing widening broke, and its fix.** The gap beat's Luminance
Ledger is `width: 100%` over its viewBox and scales its type with its
column; Epic 9.22 recorded it as the report's one uncapped chart precisely
because the report's width was out of scope then. At 72rem its gutter
labels rendered at 40px — the largest type on the page
(`candidate-72rem-ledger-uncapped-1920.png`). It is now capped at the prose
measure, where it draws at about the size it always has and in the column
the prose reads in (`after-ledger-capped-1920.png`).

## PDF parity: the answer is "never coupled"

`services/pdf.py` writes A4 in PostScript points (595.28 × 841.89) and
reads no stylesheet and no token; `report_pdf.py` lays its tables out in
points of its own. `@media print` sets `.avp-report { max-width: none }`
in any case. So widening the on-screen document changes nothing about the
file a prospect downloads — and it never could have. `tokens/spacing.ts`
claimed "PDF export parity" for this width since Epic 0; the claim was
false and is corrected in this commit rather than left for the next person
to assume.

## How it is built

- `--avp-report-width` is **72rem** and means the document. A new
  `--avp-page-width`, **52rem**, keeps the reading measure for everything
  that used to share the report's: landing, auth, welcome, invite, the
  Compare form on the shell default, the crawler list, and the share
  route's loading and error states (`max-w-page`).
- The report route passes `wide` in every branch — **deliberately**, the
  thing 14.1's correction forbade — and `.avp-report-frame` now caps
  itself at the report width plus the desk's gutter, so the desk hugs the
  page whatever shell measure the route is on. The hug no longer depends
  on the shell's default, which is what let 14.1's bug happen.
- `reportWidth.test.ts` is rewritten, not deleted: it asserts `wide` in
  every branch, the frame's self-cap, the shell default at page width, the
  two token values, and the share route's split between report and page
  measures. Its header records the flip and why.

## Verified

Design system **606/606**, web **818/818** (the guard rewritten from four
assertions to six), both typechecks clean, styleguide rebuilt. Before and
after on both routes at 1440 and 1920, plus the proof beat's tables at
1440 using the width. Looked at rather than assumed: at 1440 the page now
fills the content area with the sidebar beside it; at 1920 it is a
document on a desk with an even margin, not a column in a field.

# Epic 18 — the CI gate: every suite, every push, counts asserted, proven on a throwaway PR

**2026-09-10.** `north-star.md` §4.3 named this as the first piece of physical
architecture that must exist, prior to any deploy target. The brief listed
it first for the same reason, and it went first.

## What existed, honestly

Not nothing: `.github/workflows/ci.yml` was committed on 2026-09-08 — a
workflow for a different project (`frontend/`, `backend/`, a Supabase
database called `construction_portal_test`), and its one run failed in 36
seconds. Replaced wholesale. `north-star.md`'s "no `.github` directory"
was true when written and stale by a week.

## What was built

`.github/workflows/ci.yml`, on every push and pull request against `main`,
three jobs so a failure names its half in seconds:

| job | runs |
|---|---|
| `api` | `ruff check src tests` (not `.` — `scripts/` carries an accepted baseline), `mypy` against a **ceiling**, `alembic upgrade head` and `alembic check` against a real **Postgres 17** service, `pytest` against it and a real **Redis 7**, then the OpenAPI export diffed against the committed contract |
| `workers` | `pytest`, with the same services (it shares the API's models) |
| `web` | shared-types tests, typecheck and the generated TS contract diffed; design-system tests, typecheck and the styleguide build; web tests and typecheck |

The services publish on the runner's localhost and the suite finds them
through its own `AVP_TEST_DATABASE_URL` / `AVP_TEST_REDIS_URL`
(`conftest.py`), so nothing about the tests changed to run in CI. No
secret is needed or set: every provider call in the suite is faked at the
boundary.

**The two rules from §4.3, both enforced structurally.** No test or lint
command is piped: each writes a file, its own exit status is captured on
the same line (`|| rc=$?`, because Actions runs `bash -e` and a plain
`rc=$?` on the next line never executes), and only then is the file read.
And the count is asserted: `infra/ci/assert_count.py` reads each suite's
summary line and fails the job if it is under the floor in
`infra/ci/floors.json` — api 1194, workers 13, shared-types 53,
design-system 606, web 818. `mypy` is a ceiling at **50**, its measured
count today; the doc's "pre-existing 38" had drifted by twelve without
anyone deciding it could, which is the reason it is gated now. The choice
was *not regress* rather than *zero*: forty-plus existing errors are their
own piece of work, and a gate that fails on day one gates nothing.

## What the gate found on its first honest runs

Five red runs before green, and none of them was the workflow being wrong
about the code:

1. **`uv sync` needs `--extra dev`** — pytest, ruff and mypy live under the
   dev extra in both Python projects.
2. **vitest colours its summary on a runner**, so the count checker strips
   ANSI before matching, and the workflow asks for no colour.
3. **Fourteen API tests passed locally only because the developer's `.env`
   held a real `ANTHROPIC_API_KEY`.** The settings fixture read `.env`
   implicitly; the fixture is now hermetic (`_env_file=None`, placeholder
   provider keys) so the suite sees the same world on every machine.
4. **The generate-fixes endpoint dropped settings on the floor** — it called
   the runner without them, so the runner fell back to the process getter
   and `.env`. It takes `SettingsDep` now, the way the scan chain already
   passed its own through. Four direct service calls in the failure-path
   tests did the same and now take the fixture.
5. The OpenAPI exporter refuses a path outside the repo; the check
   regenerates the committed file in place and lets `git diff` decide.

Items 3 and 4 are exactly the class of defect the gate exists for: the
suite was green on one machine for a reason nobody had written down.

## Proven, not assumed

A throwaway branch (`ci/prove-the-gate`, PR #1, closed and deleted):

- **Commit 1**: an unused import in `ids.py` and `expect(1).toBe(2)` in a web
  test. The API job failed at ruff; the web job failed at the assertion;
  the PR was blocked.
- **Commit 2**: both reverted, and `ReportView.test.tsx` deleted. Every job
  passed its tests — and the web job failed at the floor: *"729 passed,
  floor is 818 — the suite silently shrank"*. The PR stayed blocked.

The three jobs are **required status checks** on `main`
(`strict: false`, `enforce_admins: false`). Admins are exempt on purpose:
this repository's practice since Epic 0 is direct commits to `main` with
the suites run by hand first, and a gate that rejected every such push
would have been reverted within the day. A PR from anyone else cannot
merge red; a push to `main` from the founder or this agent still runs the
gate and still shows red on the commit.

## Not built here, on purpose

No deploy step — that is item 3 of the brief, and it must be gated on this
workflow passing first. No `mypy` clean-up. No coverage measurement.
`playwright install` is not run in CI because no test launches a browser;
the deploy image will need it, and that is where it will be installed.

# Epic 18.2 — one real deploy: everything buildable without a vendor is built; the vendor is the founder's call

**2026-09-10.** Item 3 of the pilot-ready brief. Nothing is deployed, and
nothing here pretends to be: the brief is explicit that the host, the
managed datastores, the SerpApi plan and error tracking are real accounts
and real money, to be decided by the founder rather than picked on the
project's behalf. What follows is every piece that is the same whichever
host is chosen, built and **proven in CI**, and then the decision list.

## Built and proven

**`apps/api/Dockerfile`.** On Playwright's own Python image, pinned to
`v1.62.0-noble` because `uv.lock` pins `playwright` 1.62.0 and the package
looks for a browser build of exactly its own version — a mismatch is a
container that boots and then fails its first technical audit. `uv` is
copied from its own pinned image, dependencies are installed from the lock
without the dev extra, the process runs as the image's non-root user, and
the build context is the repo root because the migrations live in
`infra/db`. `.dockerignore` admits only `apps/api` and `infra/db` and
excludes `.env` by name, so a local secrets file can never be baked in.

**Migrate on boot.** `docker-entrypoint.sh` runs `alembic upgrade head`
as the first thing the container does and then `exec`s uvicorn. Chosen
over a host "release phase" because a container that starts is the one
mechanism every persistent-process host has; Alembic locks its version
table, so two replicas starting together serialise rather than race, and a
failed migration exits non-zero before a single request is served against
a schema the code does not understand.

**The non-negotiables, verified rather than assumed.** The README has said
since Epic 1 that the app refuses to boot on the placeholder secret and
forces the secure cookie in staging/production. The code did both
(`config.py`'s `_harden_deployed_environments`); **no test covered
either**. `test_deployed_settings.py` now asserts the refusal in both
environments, the forced flag even when the host says `false`, and a rule
that did not exist in code at all: `CORS_ALLOW_ORIGINS` may not be a
wildcard or empty in a deployed environment — browsers reject
wildcard-with-credentials, so `*` would not open the API, it would close it
to every real frontend while looking permissive. Eleven tests; the API
floor is 1205.

**Proven against the container, not the module.** The CI gate gained an
`image` job: it builds the Dockerfile on the runner, starts it with
`ENVIRONMENT=production` and the placeholder secret and asserts a non-zero
exit with the refusal in the log; then starts it with `staging` and a real
secret against the service containers, waits for `/api/v1/health`, and
asserts `/api/v1/ready` reports both datastores `ok` and that the entrypoint
ran the migrations. Green on the first build
(`run 34424782123`). Docker is not installed on the development machine,
so the runner is where the image has ever been built — which is the right
place for it to be true.

**The health endpoint** already existed (`routers/health.py`): `/health`
is liveness and touches nothing; `/ready` checks Postgres and Redis and
returns 503 when either is down. A host's deploy check should poll
`/ready`; the image's own `HEALTHCHECK` polls `/health`, for the reason the
router gives — a liveness probe that fails on a database blip turns a
partial outage into a restart loop.

**`deploy.yml`.** Triggers on `workflow_run` when the `CI` workflow has
*succeeded* on `main` — never on the push itself, so a red suite cannot
deploy — and its one job is skipped until the repository variable
`DEPLOY_TARGET` is set. It knows `fly` and `render` as names and fails
loudly with what to add for each, so choosing a host is: set the variable,
add the host's token as a repository secret, fill in one step, add the
host's config file. Secrets go in the host's secret manager and the
repository's encrypted secrets; nothing in either workflow carries a value.

## Decisions needed from the founder before this can go further

None of these is agent-buildable; each is an account, a bill, or a
credential. The engineering above is the same whichever way they go.

1. **Host for the API** — Fly.io or Render, or another persistent-process
   host. It cannot be serverless: Playwright drives a browser process and
   the scan executor runs in-process via `BackgroundTasks`, so a function
   that returns and dies takes the scan with it. Both candidates run the
   image above unchanged. Fly gives a `*.fly.dev` URL and a `fly.toml`;
   Render a `*.onrender.com` URL and a `render.yaml`. Either needs one
   secret in GitHub (`FLY_API_TOKEN` / `RENDER_API_KEY`) and
   `DEPLOY_TARGET` set to `fly` or `render`.
2. **Managed Postgres and Redis.** Any Postgres 17 that speaks the wire
   protocol works through `asyncpg`; the one constraint is that the host
   must not *require* `psycopg2`/`psycopg3` (LGPL, excluded). Fly Postgres,
   Render Postgres, Neon and Supabase all work with asyncpg. Redis: the
   host's managed Redis, or Upstash; `/0`, `/1`, `/2` must be one instance
   or three coordinated ones (sessions, broker, results).
3. **SerpApi plan.** The free tier is 250 searches a month, platform-wide,
   and a scan uses 6 — about 41 scans a month across every agency. This
   does not block standing the environment up; it blocks inviting several
   agencies to scan. The two measured runs today used 12.
4. **Error tracking.** Not required to deploy. Sentry's free tier is the
   obvious default to propose, not adopt; the FastAPI and Next.js SDKs are
   both MIT.
5. **Web hosting.** Vercel is the path of least resistance for `apps/web`
   and needs only `NEXT_PUBLIC_API_BASE_URL` at build time pointing at the
   API's real URL, and the API's `CORS_ALLOW_ORIGINS` and
   `PUBLIC_WEB_BASE_URL` pointing back. Confirm before the account is
   created; it is the founder's org and billing either way.

**Stripe stays in test mode** regardless (north-star.md §5.3.1).

## What "done" still requires, and is waiting on the answers above

One environment at a real platform URL; a merge to `main` that passes CI
deploying without a human; and the actual acceptance test — sign up,
submit a URL, watch a scan run to completion, see a scored report — on the
deployed environment, watched live. The measured pipeline that acceptance
test runs is Epic 18.1's.
