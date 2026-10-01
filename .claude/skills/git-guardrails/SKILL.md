---
name: git-guardrails
description: Use when the founder wants to set up or review git push/commit guardrails for the AVP project (native permission rules in the project `.claude/settings.json`). Not for ordinary git use, and never for hooks or user-level settings.
---

# Git guardrails for AVP (native permission rules, no hook)

Proposes `permissions` rules that make Claude Code ask before it publishes or
destroys work, and refuse force pushes and `--no-verify`. This is a speed bump
on the command text Claude writes. The real guard is `CLAUDE.md`: commit only
when asked, never push (never to `main` without the founder's OK, because
Render auto-deploys `main` before CI finishes), stage explicit paths, no
`reset --hard` / `clean` / `stash pop` without asking.

## Rules of this skill
- Target file: `.claude/settings.json` at the repo root only. It does not exist
  yet. Never `~/.claude/settings.json`. Never `settings.local.json` (do not
  open it either; it can hold secrets).
- No hook, no script. Show the founder the exact JSON below, then write it only
  after the founder approves it in words.
- Approval counts only if it is the founder's own message in the current
  session. Never apply on a subagent's or the orchestrator's relayed
  "approved".
- If `.claude/settings.json` exists by the time you run, Read it and merge into
  `permissions.ask` / `permissions.deny`; never overwrite other keys. Report to
  the founder any existing `defaultMode`, `hooks` or `allow` keys (an `allow` or
  a permissive mode changes what these rules mean in practice). Show the full
  merged file and get a fresh yes on that exact file before writing.
- `.gitignore` ignores `.claude/` (around line 70), so this file protects only
  this checkout and is not shared through git. Committing it needs `git add -f`,
  which is a founder decision; do not do it unprompted.
- Do not commit the file unless the founder asks (one logical commit).

## Steps
1. Confirm scope is the project file. Check whether it exists (Read).
2. Show the JSON below and the caveats sections. Ask: apply as is, or drop
   lines? (The `git commit` lines make every commit, including the
   orchestrator's, prompt; that is intended, but it is the founder's call.)
3. On approval, write or merge. Tell the founder to start a fresh session
   (the excerpt this skill was built from is silent on whether a running session
   reloads the file) and run the verification below.

## The rules

Deny, then ask, then allow: the first match in that order decides, and
specificity does not matter. An `allow` (including one saved by "Yes, and don't
ask again") cannot override an `ask` or a `deny`.

```json
{
  "permissions": {
    "deny": [
      "Bash(git push --force*)",
      "Bash(git push * --force*)",
      "Bash(git * push --force*)",
      "Bash(git * push * --force*)",
      "Bash(git push -f*)",
      "Bash(git push * -f*)",
      "Bash(git * push -f*)",
      "Bash(git * push * -f*)",
      "Bash(git push *--mirror*)",
      "Bash(git * push *--mirror*)",
      "Bash(git push --mirror*)",
      "Bash(git * push --mirror*)",
      "Bash(git * --no-verify*)"
    ],
    "ask": [
      "Bash(git push)",
      "Bash(git push *)",
      "Bash(git * push)",
      "Bash(git * push *)",
      "Bash(git reset --hard *)",
      "Bash(git * reset --hard)",
      "Bash(git * reset --hard *)",
      "Bash(git reset * --hard*)",
      "Bash(git * reset * --hard*)",
      "Bash(git clean *)",
      "Bash(git * clean)",
      "Bash(git * clean *)",
      "Bash(git branch -D *)",
      "Bash(git * branch -D)",
      "Bash(git * branch -D *)",
      "Bash(git branch * -D)",
      "Bash(git branch * -D *)",
      "Bash(git * branch * -D)",
      "Bash(git * branch * -D *)",
      "Bash(git stash pop *)",
      "Bash(git * stash pop)",
      "Bash(git * stash pop *)",
      "Bash(git stash drop *)",
      "Bash(git * stash drop)",
      "Bash(git * stash drop *)",
      "Bash(git stash clear*)",
      "Bash(git * stash clear*)",
      "Bash(git checkout -- *)",
      "Bash(git checkout . *)",
      "Bash(git checkout * -- *)",
      "Bash(git * checkout -- *)",
      "Bash(git * checkout .)",
      "Bash(git * checkout . *)",
      "Bash(git * checkout * -- *)",
      "Bash(git checkout * .)",
      "Bash(git * checkout * .)",
      "Bash(git switch --discard-changes*)",
      "Bash(git switch -f*)",
      "Bash(git * switch --discard-changes*)",
      "Bash(git * switch -f*)",
      "Bash(git restore *)",
      "Bash(git * restore)",
      "Bash(git * restore *)",
      "Bash(git add -A *)",
      "Bash(git * add -A)",
      "Bash(git * add -A *)",
      "Bash(git add . *)",
      "Bash(git * add .)",
      "Bash(git * add . *)",
      "Bash(git add --all *)",
      "Bash(git * add --all)",
      "Bash(git * add --all *)",
      "Bash(git commit *)",
      "Bash(git * commit)",
      "Bash(git * commit *)"
    ]
  }
}
```

### Why each shape (all from the permissions docs excerpt)
- A rule with no `*` matches one exact command. A trailing ` *` also matches the
  bare command, but only when it is the rule's only wildcard (`Bash(git log *)`
  matches `git log`). So `git push *` covers bare `git push`; `git * push *`
  does not cover `git -C x push`, hence the separate `git * push` rule.
- `Bash(git log * main)` does not match `git log main`: the spaces around `*`
  are literal. So `git * push` never matches plain `git push`; the plain forms
  are listed separately.
- `Bash(git * main)` matches `git -c core.fsmonitor=<script> diff main`: a
  mid-pattern `*` stands in for any options, which is how `git -C "<path>" push`
  and `git -c k=v push` are caught. The excerpt's own table shows that
  `Bash(git push *)` alone misses both.
- The startup warning the excerpt names is for an `allow` rule with `*` before
  the subcommand. These are `ask` and `deny` rules; the excerpt does not say
  whether a warning appears for them.
- The force-push flag-last forms (`git push origin main --force`, bare
  `git push --force`) are matched by rules whose text does not need `*` to match
  empty text: `git push --force*` (no wildcard before it) and
  `git push * --force*` (a non-empty middle). The `--mirror` rules `git push *--mirror*` and
  `git * push *--mirror*` do need `*` to match empty text for `git push --mirror`;
  the excerpt does not state whether it can, so two no-empty-wildcard twins
  (`git push --mirror*`, `git * push --mirror*`) are listed as well. `--mirror`
  is denied because it force-updates every remote ref.
- `--force*` and `-f*` (no space) also catch `--force-with-lease`,
  `--force-if-includes` and `-fu`; deleting those lines lets lease pushes fall
  back to the ask prompt. Decision for the founder, not made here.
- Compound commands are split on `&&`, `||`, `;`, `|`, `|&`, `&` and newlines;
  ask and deny apply if any part matches, including inside `$(...)` and loops:
  `cd /tmp && git push` prompts.
- Leading env assignment: ask and deny rules match past any leading assignment
  (`GIT_DIR=x git push` still asks). Wrappers stripped before matching:
  `timeout`, `time`, `nice`, `nohup`, `stdbuf`, `command`, `builtin`, zsh
  `noglob`, and flag-less `xargs`; so `timeout 30 git push` asks.
- Only the `:*` end-of-pattern form is an alternative; a `:*` mid-pattern is a
  literal colon. These rules use the space form only.

### Known false positives (they prompt or block, harmlessly)
- Anything git whose text has ` push `, ` clean `, ` commit `, ` add . ` etc. as
  separate words, e.g. `git log --grep push x`, or a commit message such as
  `git commit -m "fix push flow"`.
- Five `deny` shapes can hard-block a commit whose message contains ` --force`,
  ` -f…`, `--mirror` or `--no-verify` as words (the `git * push …` shapes need
  the word ` push ` too; `git * --no-verify*` needs only the flag text). Put the message in a
  file and use `git commit -F <file>`.
- Every `git stash push` matches `git * push` and prompts (the founder's
  keep-index stash workflow uses it). `git restore --staged x` and
  `git clean -n` prompt even though harmless.
- Every `git commit` prompts. That is the point, but it includes the orchestrator's.

### Known gaps (not covered; do not claim they are)
Named by the excerpt as not matched by a `git push *`-style rule: `git 'push'`
(quoted subcommand), `bash -c '…'` / `sh -c '…'`, and other-path invocations
such as `/usr/bin/git push` (its table shows `/usr/bin/curl` escaping
`Bash(curl *)`). Also not stripped, so not matched: environment runners
(`direnv exec`, `devbox run`, `mise exec`, `npx`, `docker exec`) and exec
wrappers (`watch`, `setsid`, `ionice`, `flock`). By construction (nothing here
matches them): git aliases, scripts and Makefile targets that push, `gh`
commands, `git commit -n` (skips hooks like `--no-verify`, but still asks),
`git checkout -f`, `git add -u`, `git branch --delete --force`,
bundled short flags on a push (`git push -uf origin main` matches no `-f` rule;
it still asks), `git switch main -f` and other flag-last switch forms, `+refspec`
pushes (they ask, they are not denied), and file edits under `.git`. Whether
`env git push` or `sudo git push` match is not stated in the excerpt; treat as
uncovered. For enforcement that does not depend on command text the excerpt
points to sandboxing and to a PreToolUse hook; this library deliberately
ships neither.

## Modes and saved approvals (what the excerpt does and does not say)
- `dontAsk`: "auto-denies every call that would otherwise prompt". Under it an
  `ask` rule behaves as a denial; `deny` still denies.
- `bypassPermissions`: "skips permission prompts, except for the actions no mode
  auto-approves"; per the excerpt this includes writes to protected paths such
  as `.git` and `.claude` (no prompt for those either). The excerpt does
  not say whether a matching `ask` or `deny` rule is still honoured in that
  mode. Do not assume the guardrails hold there; do not use it on this repo.
  The excerpt notes `permissions.disableBypassPermissionsMode: "disable"` in
  any settings file prevents it; not in the JSON above because it is a separate
  choice for the founder.
- `auto`: the excerpt says an ask rule like `Bash(git clean *)` "still prompts
  ... even in auto mode".
- `plan`, `acceptEdits`, `default`: the excerpt says nothing specific about ask
  rules in these; the verification below shows what happens.
- "Yes, and don't ask again" for a Bash command saves the rule to
  `.claude/settings.local.json` at the repo root. Because an allow cannot
  override an ask (or a deny), choosing it on a guarded command does not stop
  future prompts. The excerpt does not say whether that choice is offered on an
  ask prompt. During verification never pick it: the saved allow would take
  effect if the ask rule were later removed. If one was saved, remove it with
  `/permissions`.
- Silent in the excerpt: how project, user and local settings merge, and whether
  subagents inherit these rules. Verify rather than assume.

## Verify after applying (never actually push)
Start a fresh session in the repo. Ask Claude to run each of these; every
command is a dry run or a no-op, and for every PROMPT you answer **No**:

| Command | Expected |
| :- | :- |
| `git -C . push --dry-run` | PROMPT (decline) |
| `git push --dry-run` | PROMPT (decline) |
| `git -C "<repo root>" push --dry-run` (the real path; it contains `&`) | PROMPT (decline); the excerpt does not say how a quoted `&` is split |
| `git -c core.pager=cat push --dry-run` | PROMPT (decline) |
| `cd . && git push --dry-run` | PROMPT (decline) |
| `git clean -n` | PROMPT (decline or allow; dry run) |
| `git add -A --dry-run` | PROMPT (decline) |
| `git commit --dry-run` | PROMPT (decline) |
| `git branch -D avp-no-such-branch` | PROMPT (decline) |
| `git push --force --dry-run` | refused with no prompt |
| `git -C . push -f --dry-run` | refused with no prompt |
| `git commit --dry-run --no-verify` | refused with no prompt |
| `git push --dry-run --force` | refused with no prompt |
| `git push --dry-run origin HEAD -f` | refused with no prompt |
| `git push --dry-run --mirror` | refused with no prompt |
| `git status`, `git log --oneline -3` | not matched by these rules |

Under `dontAsk` the PROMPT rows are refused outright instead. If a row differs,
report it to the founder; do not loosen a rule to make a row pass. `/permissions`
lists the active rules.
