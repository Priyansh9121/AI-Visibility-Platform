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
