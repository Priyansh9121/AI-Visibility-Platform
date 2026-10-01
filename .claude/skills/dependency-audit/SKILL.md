---
name: dependency-audit
description: Audits AVP's three lockfiles (apps/api/uv.lock, apps/workers/uv.lock, pnpm-lock.yaml) for known-vulnerable packages, triages each advisory by real reachability, and proposes the smallest fixing upgrade. Use before launch or on a request to check dependency advisories; not for licence questions (CI already runs license_audit.py and js_license_audit.py) and not for applying upgrades.
---
# Dependency audit (AVP)

Separate what is exploitable from noise, then propose fixes. This skill reports and proposes. It never edits a manifest or lockfile.

## Lockfiles
Confirm each exists before use: `apps/api/uv.lock`, `apps/workers/uv.lock`, and the root `pnpm-lock.yaml` (one pnpm workspace: `apps/web`, `packages/design-system`, `packages/shared-types`). Other ecosystems are not present; do not audit them.

## 1. Pick scanners that already exist
Check, do not install. Checked 2026-10-01: `uv` 0.12.3 has an `audit` subcommand (OSV-based, `--output-format json`); `corepack pnpm audit --json` exists; `osv-scanner`, `pip-audit` and `trivy` are not installed. Re-check with `uv audit --help` and `command -v osv-scanner`.
- Installing osv-scanner, pip-audit or trivy is a new tool: a founder decision. Propose it, never install it, and never add it to `pyproject.toml` (that would be a new dependency).
- Both commands make outbound lookups from the machine running them, not from the app. Say so in the report. `uv audit` defaults to OSV at `https://api.osv.dev/` (per `uv help audit`; whether deprecation or quarantine lookups also hit the package index is unconfirmed). `corepack pnpm audit` posts the lockfile-derived dependency tree to `registry.npmjs.org` (there is no `.npmrc` in the repo; a user-level one may exist and must never be read); that likely includes the `@avp/*` workspace package names (unconfirmed). They send package names and versions, not source code.

```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
# run from the repo root; each in its own subshell; never pipe
rc=0; ( cd apps/api && uv audit --frozen --output-format json ) > "$LOG/audit-api.json" 2> "$LOG/audit-api.err" || rc=$?; echo "api audit rc=$rc"
rc=0; ( cd apps/workers && uv audit --frozen --output-format json ) > "$LOG/audit-workers.json" 2> "$LOG/audit-workers.err" || rc=$?; echo "workers audit rc=$rc"
rc=0; corepack pnpm audit --json > "$LOG/audit-pnpm.json" 2> "$LOG/audit-pnpm.err" || rc=$?; echo "pnpm audit rc=$rc"
```

Runtime-only view of the Python side (excludes the `dev` extra; same classification rules):
```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
rc=0; ( cd apps/api && uv audit --frozen --no-extra dev --output-format json ) > "$LOG/audit-api-runtime.json" 2> "$LOG/audit-api-runtime.err" || rc=$?; echo "api runtime audit rc=$rc"
rc=0; ( cd apps/workers && uv audit --frozen --no-extra dev --output-format json ) > "$LOG/audit-workers-runtime.json" 2> "$LOG/audit-workers-runtime.err" || rc=$?; echo "workers runtime audit rc=$rc"
```
Classify each result three ways, whatever the rc: CLEAN = rc 0, the JSON parses and lists zero adverse findings; FOUND = the JSON parses and lists at least one adverse status of any kind (a vulnerability, or a deprecation or quarantine status where the tool reports one; whether `uv audit` reports quarantine is unconfirmed); NOT AUDITED = anything else (empty or unparseable JSON, an `error` key, an error in the `.err` file). Never report a tool failure as "advisories found" or as clean. `--frozen` keeps uv from re-locking. Never use `pnpm audit --fix` (it writes overrides into `package.json`). Do not add `--ignore-registry-errors`: a registry failure must show as a failed audit, not a clean one.

## 2. Deduplicate and triage
Key on advisory id + package + lockfile. For each advisory, by reading the code:
- Direct or transitive? Direct: named in `apps/api/pyproject.toml`, `apps/workers/pyproject.toml` or a `package.json`. Transitive: `uv tree --frozen --invert --package <pkg>` (in the project dir; without `--frozen` it locks the project and can rewrite `uv.lock`) or `corepack pnpm why <pkg>`.
- Runtime or dev-only? The `dev` extra (`[project.optional-dependencies] dev` in both `pyproject.toml` files; CI installs it with `--extra dev`; it is not a uv dev group) and pnpm `devDependencies` (test, lint, build tools) are LOW unless they run on Render or in CI with secrets.
- Is the vulnerable function used? Grep `apps/` and `packages/` for the affected API. Unreachable in a dev-only tool is LOW whatever the CVSS.
- AVP exposure context: the API takes untrusted input (authenticated scan creation `POST /clients/{clientId}/scans`, public token routes such as `POST /reports/shared`, the Stripe webhook, and URL fetching and page parsing in `apps/api/src/avp_api/services/crawl.py` via httpx and Playwright); workers run only health tasks today (`apps/workers/src/avp_workers/celery_app.py:70` discovers `avp_workers.tasks`; no worker code imports `avp_api`), so re-check when real tasks land; web renders untrusted names. Use the runtime-only snippet in step 1 for runtime-only results. A flaw in request parsing, URL handling, HTTP clients, auth/session/JWT, crypto or the Stripe SDK is higher than one in a build tool.
- Verdict per advisory: fix-now / fix-soon / accept-with-note, one line of why.

## 3. Smallest fix
From the advisory's fixed-in version, name the smallest upgrade that closes it. If only a major closes it, say what the migration involves. If no fix exists, say so and propose accept-with-note plus a recheck date.

## 4. Deliver
A table: advisory · package · lockfile · direct? · runtime/dev · reachable? · verdict · smallest fix, ordered by real priority. State which scanners ran, which lockfiles were covered, rc per command, and the date. A lockfile with no audit result is reported as "not audited", never as clean. Record only ids, names, versions and counts (facts only); do not paste advisory prose.

## 5. Upgrades are proposals
Never apply them. For each fix-now, give the exact command the founder can run, and the gates that must pass afterwards:
- `apps/workers` depends on `avp-api` by path, so any lock change (including `uv lock --upgrade-package X`) runs in BOTH projects, then re-audit both. Any `pyproject.toml` change: re-lock BOTH `apps/api` and `apps/workers` (`uv lock`), then `uv lock --check` in both (CI runs it).
- A new runtime dependency (or a transitive one that adds an outbound host) must be classified in `apps/web/src/components/legal/providers.test.ts` and named on `/privacy`. Flag it; do not edit the legal page.
- Licences: CI's `apps/api/scripts/license_audit.py` and `apps/api/scripts/js_license_audit.py` already cover them. Reference, do not re-run or duplicate. Open questions are in `infra/ci/licence-pending-decisions.json`.
- Then the normal bar (`.github/workflows/ci.yml`, `infra/ci/README.md`): suites with counts against `infra/ci/floors.json` via `infra/ci/assert_count.py`, never piping output. Delegate full runs to the test-runner agent.
- Commit only when the founder asks; push only when asked; never to `main` without explicit OK (Render auto-deploys `main`).

## 6. CI guard
`.github/workflows/ci.yml` is gated and already runs licence checks; it has no vulnerability step today (confirmed 2026-10-01). If asked, propose one as a separate change for the founder: a job that runs the same `uv audit` / `pnpm audit` commands with output to a file and its own rc checked, following the existing step style. Decide with the founder whether it blocks merges or only reports (a new advisory appearing upstream would otherwise fail unrelated PRs). Do not edit the workflow from this skill.
