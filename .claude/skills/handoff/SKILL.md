---
name: handoff
description: Use only when the user asks for a handoff or a summary for a fresh session, e.g. before a context or usage limit. Writes a handoff document for the next agent; not for routine status updates or build-log entries.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Create the file with `mktemp -t avp-handoff` (per-user temp directory, not a predictable /tmp name), never in the workspace or the AVP repo, and tell the user its absolute path.

Include a "suggested skills" section naming which skills the next agent should call the Skill tool for (installed or library names only).

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs, `docs/build-log.md`). Reference them by path or URL instead.

If the user passed arguments, treat them as what the next session will focus on and tailor the doc to it.

## What an AVP handoff must carry
- Current branch and any unpushed commits (hashes and subjects): `git log --format='%h %s' HEAD --not --remotes` (works with no upstream; prints no author emails). "No upstream" is not "nothing unpushed". Never push; say so in the doc.
- Working-tree state: modified and untracked paths. The next agent stages explicit paths only, never `git add -A` or `git add .`.
- The verification bar's last state, with counts (before to after) and the log file paths. Never just "green". Say which suites were not run.
- Which floors in `infra/ci/floors.json` changed, or "none".
- Open founder decisions (a price, a limit, retention, user-visible behaviour), stated as questions.
- Anything "found, not fixed", pointing to the relevant `docs/build-log.md` section rather than restating it.
- Running background agents or workflows, with their output file paths and whether results survive a session boundary.
- The next step, one concrete action.

## Redaction
Redact API keys, passwords, tokens and personal data. Names only, never values. Specifically:
- reset, invite and share URLs (`/reset-password#...`, `/invite#...`, `/share/...` tokens);
- `avp_session` or any `Cookie:` value, and `Authorization` headers;
- Stripe keys (`sk_...`, `whsec_...`);
- database and Redis URLs containing `user:pass@`;
- output of `apps/api/scripts/issue_password_reset.py`;
- environment variable values (names only) and email addresses.

Never read or quote `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `dump.rdb` or `.devdata/` (the dev Postgres cluster holds user emails and plaintext tokens).
