---
name: wizard
description: Generates an interactive bash wizard the founder runs by hand for steps only a human can do at AVP's launch cutover (Stripe keys and webhook, Render environment variables, the Google OAuth client, Supabase dashboard steps). Do not use for anything the agent can do itself, and never to run the wizard.
---

# Wizard

A wizard is a bash script that walks the founder through a manual procedure: it opens each dashboard URL, says what to click and copy, confirms each stage, and shows the stages left. [template.sh](template.sh) holds the library (progress, confirmation gates, URL opening, hidden secret entry, name-only reporting). **Your job is to scope the procedure and author the stages below the `STAGES` marker.** Do not edit the library above it.

**You generate the script. You never run it.** It opens a browser and blocks on the founder's keyboard, and it handles secrets an agent must not see.

## Secret rules (non-negotiable)

- Secrets are typed with `ask_secret` (hidden input), never `ask`. `ask` refuses secret-looking names.
- Reports name variables only, never values. No `set -x` (the library refuses it), no `echo "$VAR"`, no logging of captured values.
- `write_env` writes only test-mode or local-dev values, and only to an untracked, git-ignored file. The default is `apps/api/.env`, which `.gitignore` covers. The library refuses a tracked or un-ignored file. `write_env` takes only the variable NAME (it reads the value by indirection, so a trace never holds it), and any value containing `whsec_` needs a confirm that it came from `stripe listen` or a TEST-mode endpoint. Never point `ENV_FILE` at a tracked path.
- **Live keys never pass through the script.** `sk_live_` / `rk_live_` values are refused. Stripe live keys are the founder's to set on Render (`CLAUDE.md`, "Secrets"). For anything that belongs on Render, use `dashboard_set`: the founder copies from the provider's dashboard and pastes into the Render dashboard, then confirms. Nothing here calls Render, Stripe or GitHub APIs.
- Do not read `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `.devdata/` or `dump.rdb` while scoping. Take variable names from `apps/api/.env.example` and `apps/api/src/avp_api/config.py`.
- Going live is a founder decision made after Stripe's business verification (`apps/api/.env.example`, billing block). A wizard may walk the steps; it does not decide the price, the limit or the cutover date.

## Process

### 1. Scope the procedure

Read the repo first: `apps/api/.env.example` (variable names and which are secret), `docs/runbook.md` (existing manual steps, e.g. "Live Stripe test"), `.github/workflows/ci.yml` (any `secrets.*`). Show the founder the ordered stages and the value each produces; let them add, drop or reorder.

Done when, for each value, you know (a) where the founder gets it, (b) where it goes: untracked local file, a dashboard (Render), or nowhere, and (c) whether it is secret.

### 2. Map each stage

Write the exact path: URL, click path, where the value appears, which variable it fills. Where you do not know the current dashboard UI, say so and ask or check the provider docs; never invent steps. Use `https://` URLs only.

### 3. Author the script

Copy `template.sh` to a path the founder chooses (default: a scratch path outside the repo; a repo path such as `scripts/` only if the founder asks, and then commit only when asked, staging explicit paths). Replace the two example stages with one `stage` per step in dependency order; set `TOTAL_STAGES`. Use `open_url` before asking for a value, `ask_secret` for secrets, `write_env NAME` (name only, never the value) then `forget` for local values, `dashboard_set` for Render values, and `confirm` before any irreversible action. One focused task per stage.

### 4. Verify and hand off

- `bash -n <script>`; `shellcheck <script>` if present. These only parse; they do not run it.
- `chmod +x <script>`.
- Trace it statically: every value from step 1 is captured and lands where step 1 said; every name matches `apps/api/.env.example`; no value is printed.
- Tell the founder how to run it, from the repo root, and that it will ask before each step. Do not run it yourself.
