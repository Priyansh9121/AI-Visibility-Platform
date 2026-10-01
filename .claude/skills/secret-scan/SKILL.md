---
name: secret-scan
description: Sweeps AVP's tracked files and full git history for committed credentials (Stripe, Anthropic, Supabase, Render, Resend, DB URLs, private keys) without ever printing a value, and guides rotation. Use for a pre-launch or pre-publish sweep or after a suspected leak; not for reviewing auth code (use security-review) and never for opening untracked secret files.
---
# secret-scan

Find committed credentials in AVP and get them rotated, without exposing them further.

**The repo is PUBLIC** (treat it as public; this cannot be checked offline). Treat every pushed branch as public: anything ever pushed is readable and probably cloned or scraped. A finding in history means the key must be **rotated**; deleting the line does not un-leak it.

## Absolute rules
- Never print a secret value: not in output, notes, commits, PRs or the report. Refer to a hit as `<rule> in <path>:<line> (commit <sha>)`. Never put a fingerprint in chat or the report.
- Out of scope, never opened: untracked secret files (`apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `.devdata/`, `dump.rdb`). If they need checking, the founder does it. `apps/api/.env.example` is tracked and is a template; its values should be placeholders.
- Results go to a private temp file. Every file this skill creates is deleted at the end (step 5).
- Never open a hit's line: no `git show`, `cat`, Read, `git grep`/`git log -p` output, diff, or any command that prints file text. The value would land in the transcript. Use `scan.py triage` (step 3); a hit it cannot settle goes to the founder. No commit, push, PR, or history rewrite unless the founder asks.
- Never pipe a scan command; redirect and read the file.

## 1. Scan
Uses only Python 3 (already installed) and git. Run from the repo root:
```bash
LOG="${LOG:?set LOG to an absolute scratch dir outside the repo}"
R="$(mktemp "$LOG/secret-scan.XXXXXX")"            # mktemp is mode 0600
rc=0; python3 <library>/secret-scan/references/scan.py all "$R" > "$LOG/scan.log" 2>&1 || rc=$?; echo "scan rc=$rc"
```
`<library>` is where this skill folder lives. Modes: `tree`, `history`, `all` (one run, one fingerprint key). `scan.py` reads git objects only: tracked blobs at HEAD via `git ls-tree --full-tree`/`cat-file` (symlinks are not followed, uncommitted edits are not seen), and every added line of `git log --all --root -p` (files deleted since are covered; the stash is excluded). The git invocation pins its own flags (`--root --inter-hunk-context=0 --no-relative --text --no-renames --no-textconv --no-ext-diff --no-show-signature --default-prefix`), so `log.showRoot`, `diff.interHunkContext`, `diff.relative`, `-diff` attributes and similar user config cannot hide or mis-number a finding. A non-zero rc means git failed: report it, do not treat the sweep as clean. Per finding it writes: `tree|history`, rule id, `path:line`, commit short-sha (history), and a fingerprint (HMAC under a per-run random key, 8 hex). The fingerprint only separates "same value" from "different value" inside `$R`; never quote it in chat or reports. The matched text is never written. Rules: Stripe secret/restricted keys and `whsec_`, Anthropic, OpenAI, Google, AWS, GitHub, Slack (token and webhook URL), Discord webhook URL, Render, Supabase, Resend tokens, private-key blocks, JWTs, URLs with a password, and the keyword rules: `keyword-assignment` (password/secret/token/api key/`*_KEY`/credential/auth/authorization followed by `:` or `=` and a quoted or bare value; bare `true`/`false`/`none`/`null` values are skipped), `typed-default-assignment` (`api_key: str = "x"`, `app_secret: SecretStr = SecretStr("x")`, `Field(default="x")`), `setenv-literal` (`setenv("X_SECRET", "x")`, `setdefault`, `putenv`), `environ-assignment` (`os.environ["X_PASSWORD"] = "x"`), `process-env-assignment`, and a noisy `generic-assignment`. Values starting with `$ < { ( [` are treated as references and skipped.

Read `$R` with the Read tool. Keep `$R` as written by this single `all` run: `triage` compares fingerprints inside it, and fingerprints from separate runs use different keys.

Limits of the sweep, say them in the report: regexes only (no entropy check, no secrets split across lines, nothing inside binaries); lines are cut at 4000 characters and only the first match per rule per line is kept; merge-commit diffs are not scanned; `--all` also scans local-only branches (the stash is excluded), so history hits may not be pushed yet (check before calling the repo exposed); the keyword rules, `generic-assignment` and `url-with-password` mostly hit test fixtures and local dev URLs; the history pass skips `.lock`, images and fonts.

## 2. External scanner (gitleaks, trufflehog): founder decision
Neither is installed here. Do not install one. If the founder wants one, propose a **pinned version** and its checksum, and let them install it. If they do, always pass `--redact`, write the report inside the private temp dir, and delete it after reading. Gitleaks includes the matched value in its report unless `--redact` is used. Until then say the sweep was the regex sweep above.

## 3. Triage
Run `scan.py triage` on the same report. It re-reads each hit's line **in memory** and prints one tab-separated line of fixed fields per hit, with no text from the file (the only bytes it copies out of a blob are an identifier-shaped name and a safe host form):
```bash
rc=0; python3 <library>/secret-scan/references/scan.py triage "$R" > "$LOG/triage.txt" 2>&1 || rc=$?; echo "triage rc=$rc"
```
Read `$LOG/triage.txt`. Fields, in order:
- `src` (tree or history), `rule`, `loc` (path:line), `commit` (short sha; HEAD for tree hits), `remote` (`yes`/`no`/`unknown`: is that commit on a remote-tracking branch, from `git branch -r --contains`; `unknown` when the repo has no remote-tracking branches).
- `cat`: by path only. `test`, `ci`, `docs`, `example` (`*.example`, `.env.example`, `*.sample`), `src`, `other`.
- `name`: the variable or key name, only when it is the FIRST token on its line: it may follow only indentation, `export`, `-`, `{`, `[`, `(`, `,`, a receiver such as `self.` or `os.environ.`, or its own opening quote; anything else before it (a command, `docker run -e`, a call such as `connect(host, password=...)`, an earlier assignment or value, `curl -u user:`) gives `none`. Taken only from the key side of the assignment and only when it is a plain identifier: the leading identifier of a keyword match (`STRIPE_SECRET_KEY`, `api_key`); the first quoted token of `setenv(`/`setdefault(`/`os.environ[` (the call must itself start the line); `process.env.NAME`; for key-shaped rules the identifier right before the first `=`/`:` that is outside quotes. Never anything inside quotes on the value side (a list or JSON item), and never one that looks like a secret.
- `len`, `ref` (value is a reference: `${..}`, `$VAR`, `os.environ`, `process.env`, `secrets.`, `{{ }}`), `placeholder` (the WHOLE value only: it equals a placeholder word such as changeme, password, postgres, example, test, dummy, or is ASCII words joined by `-`, `_`, space or `.` that are all such words (`dev-only-insecure-secret-change-me`, `Change.Me`), or is a whole-value template made of plain words (`<your-token>`, `{{ secrets.X }}`, `your-..-here`, `xxx`), or has eight or more of one repeated character; never `yes` for a value with non-ASCII letters or symbols mixed in (`admin!@#$`, `<Xk9#mP2!qLz7>`), never when it contains a 12+ character run mixing letters and digits, and a word inside a longer value (`django-insecure-<random>`, `MyExampleCorp2024`) does not count), `pw_eq_user` (URL hits: password equals username). All compared in memory; only yes/no or a number is printed.
- `host` (URL hits): printed as-is only if it is a test host: loopback (127.0.0.0/8, ::1, localhost) or an RFC 2606/6761 reserved name (`*.test`, `*.example`, `*.invalid`, `*.localhost`, example.com/.net/.org). `.internal` and single-label names (`db`, `redis`) are real private networks: printed as `private:<n labels>`, never as the name. Anything else is `remote:<registrable domain>` (last two labels, three for `co.uk`-style; approximate, no public-suffix list) or `remote:ip`.
- `in_example`: the same fingerprint appears on a hit in an `*.example`/`*.sample` file (the fingerprint covers the whole match, name included; a hit that is itself in such a file is `yes`). Informational only: being in an example file never settles a verdict, for any rule.
- `verdict`: `likely-placeholder` only when the facts settle it, else `unclassified`. URL hits: a test host (as above) AND (`placeholder=yes` OR user equals password). Other hits: `placeholder=yes` in a `test`, `ci`, `docs` or `example` path. Private and remote hosts never settle a URL hit. Nothing else is ever `likely-placeholder`.

**`unclassified` hits go to the founder**, who looks at the line in their own terminal. The agent never opens the line. In the report, call a `likely-placeholder` hit "likely placeholder per triage facts" and name the facts; it is a hint from short lists, never "confirmed". `remote=yes` means the content is on a pushed branch, so treat it as public.

Limits, say them in the report: triage cannot tell a real weak credential from a placeholder (a real password `postgres` is `placeholder=yes`); the placeholder and category lists are short and path-based; a different name for the same value is not linked by `in_example`; value facts are for the rule's first match on the line; a hit whose line moved or whose blob is missing prints `n/a` facts and is `unclassified`. Triage only sees what the sweep flagged: shapes the regexes miss (a `- name: X` line with `value: y` on the next, CSV or table rows under a `password` header, `redis-cli -a x`, `docker login -p x`, SQL `N'x'`, `accesskey=x`) are not in `$R`, so say the sweep is not complete rather than clean. Advice for the founder, not done (the scan rules are frozen here): before AVP gets a `render.yaml`, add a two-line rule for the Render env-var shape (`- key: NAME` followed by `value: x`); later add cheap rules for run-on `*key=` words, SQL `PASSWORD '...'`, `redis-cli -a` and `docker login -p`.

- Real credential, test fixture, or placeholder? Triage gives facts; the founder decides what stays `unclassified`. AVP fixtures and placeholders exist on purpose (for example CI boots with placeholder secrets, `.github/workflows/ci.yml`; `apps/api/tests/test_secrets_stay_out_of_logs.py` plants fake values); confirm, do not assume.
- For real ones: what does it grant, and is it plausibly still valid? A Stripe test key still touches the test account; a live key, Supabase service key or Render token is an incident.

## 4. For every real secret, in this order
1. **Rotate first.** Tell the founder where: Stripe Dashboard (API keys, webhook signing secret), Anthropic Console, Resend, Supabase project settings (DB password, service key), Render dashboard (env vars, API keys), Google Cloud Console (OAuth client). New values are set by the founder on Render and in their local `.env`, never pasted into chat or the repo.
2. **Remove from code**: read it from settings/env as the codebase already does (`apps/api/.env.example` lists the names).
3. **Prevent a repeat**: check `.gitignore` covers local secret files; offer a pre-commit scan or CI step as a proposal for the founder.
4. **History purge** (`git filter-repo`/BFG) rewrites shared history, breaks open PRs and every clone, and does not remove copies already taken from a public repo. Describe the trade-off; proceed only if the founder explicitly asks. `CLAUDE.md` bars force-pushes and pushes to `main` without the founder's OK.

## 5. Deliver
A hit list (rule, location, name, category, remote?, triage verdict, rotation status) with no values, what was and was not covered, and the prevention proposal. Counts of findings may be stated. Commit nothing.

Cleanup, last: `rm -f "$R" "$LOG/scan.log" "$LOG/triage.txt"` and any other file you created here, then confirm with `ls` that none remain.
