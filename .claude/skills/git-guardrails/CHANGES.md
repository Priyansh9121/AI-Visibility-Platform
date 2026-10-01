# Changes from upstream

Paths are relative to the source skill folder.

- Hook removed: no PreToolUse hook, no `.claude/hooks/` copy, no `chmod +x`
  (`SKILL.md:8`, `:26-35`, `:37-79`) -> hooks are out of this library; a hook
  runs arbitrary shell on every Bash call.
- `scripts/block-dangerous-git.sh` not copied; the `echo ... | <script>` test
  is dropped (`SKILL.md:87-95`) -> the script is not in the library and is never
  executed.
- Global install option removed (`SKILL.md:24`, `:30-33`, `:61-79`,
  `~/.claude/settings.json`) -> user-level settings would guard every repo with
  one set of rules and were not audited for AVP; project scope only.
- Mechanism replaced with native `permissions.ask` / `permissions.deny` rules in
  the project `.claude/settings.json`, shown as exact JSON and applied only after
  the founder approves (`SKILL.md:37-81`) -> the source's own edit-settings step
  was unguarded; here it is gated and merge-only. This is the one deliberate
  exception to "no settings.json edit": the skill writes the project file only
  with explicit founder approval, never user-level or local files.
- Hard block turned into "ask" for push, reset --hard, clean, branch -D
  (`SKILL.md:10-18`) -> AVP wants a confirmation, not a wall; force pushes and
  `--no-verify` are `deny` instead.
- Push coverage widened: `git * push` / `git * push *` added (`SKILL.md:12`,
  script patterns) -> `git -C <repo> push` and `git -c k=v push` escape a
  `git push *` rule (excerpt table, "What a Bash rule doesn't match").
- Added ask rules for `stash pop`, `stash drop`, `add -A`, `add .`, `add --all`,
  `commit`, `checkout --`, and `restore` (`SKILL.md:16` covered only
  `checkout .` / `restore .`) -> matches `CLAUDE.md` git rules (explicit staging,
  commit only when asked, no stash pop without asking).
- Added deny for `--force*`, `-f*`, `--no-verify*` (`SKILL.md:12` "including
  `--force`") -> AVP never force-pushes or skips hooks.
- Added the excerpt-sourced caveats: false positives, known gaps, mode behaviour
  (`dontAsk`, `bypassPermissions`), the `settings.local.json` save on "don't ask
  again", and where the excerpt is silent -> the source claimed "blocks"; these
  rules are a speed bump, and the house rules remain the guard.
- Verification replaced: dry-run commands the founder can decline
  (`SKILL.md:87-95` piped JSON into a script) -> nothing is pushed, nothing runs
  the source script.
- Frontmatter description narrowed to setting up or reviewing AVP push/commit
  guardrails (`SKILL.md:3` "Use when user wants to prevent destructive git
  operations…") -> no triggering on ordinary git use.
- `agents/openai.yaml` not copied -> folder contract.

## Security review round (2026-09-30)

- Precedence line corrected to "deny, then ask, then allow" (SKILL.md "The rules") -> the first draft said "ask, then deny"; the excerpt (line ~4) says deny first. The verification rows (force, `-f`, `--no-verify` refused without a prompt) already agree.
- Added deny rules `git push *--mirror*` and `git * push *--mirror*`, plus twins `git push --mirror*` and `git * push --mirror*` -> `--mirror` force-updates all remote refs; the twins do not depend on `*` matching empty text, which the excerpt does not state. Deny count 9 -> 13.
- Known gaps: bundled short flags (`git push -uf origin main`) and flag-last `git switch` forms listed -> no rule matches them (pushes still ask). Force flag-last forms explained (which rules need an empty-matching `*`, and that the excerpt is silent on it).
- Added verification rows `git push --dry-run --force`, `git push --dry-run origin HEAD -f`, `git push --dry-run --mirror`, and `git -C "<repo root>" push --dry-run` with the real path (contains `&`; the excerpt does not say how a quoted separator splits) -> exercises flag-last and quoted-path forms.
- Added ask rules for `git reset * --hard*`, `git checkout * .`, `git stash clear*`, `git switch --discard-changes*` / `-f*`, `git branch * -D…` (each with `git *` forms) -> destructive forms the first set missed (`git reset -q --hard`, `git reset HEAD~1 --hard`, `git checkout HEAD .`, `git branch -q -D x`). Ask count 41 -> 55.
- Approval gate tightened: only the founder's own message in the current session counts; a merge needs a fresh yes on the full merged file; report existing `defaultMode`, `hooks` and `allow` keys -> a relayed "approved" or a silent merge must not write the file.
- Wording: `bypassPermissions` line now says protected-path writes (`.git`, `.claude`) are skipped too; the commit-message false positive is five deny shapes, not three; added that `git stash push` matches `git * push` (keep-index workflow) -> accuracy against the excerpt and AVP practice.
- Added that `.gitignore` ignores `.claude/` (line 70), so the file is not shared via git and committing it needs `git add -f` (founder decision) -> avoids a false belief that the team is protected.
- Added: never choose "Yes, and don't ask again" during verification; remove any saved allow with `/permissions` -> a saved allow in `settings.local.json` would take effect if an ask rule were later removed.
