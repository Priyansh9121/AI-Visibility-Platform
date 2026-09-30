# Floor guard for AVP

A diff-scoped check for the five moves in `SKILL.md`. It reads git only, installs nothing, and writes its scratch files outside the repo. The pipeline is fixed so two runs give the same verdict.

## Contract

- **Input:** the diff between the merge base with `origin/main` and the working tree, **plus untracked files** (`git diff` cannot see them). Renames are turned off (`--no-renames`) so a moved test file reads as a deletion plus an addition, not as an empty rename.
- **Exit codes:** `0` clean, `1` at least one violation, `2` the guard could not run (no merge base, a git command failed or wrote to stderr, the diff had content but nothing could be read from it, a scratch file could not be written). Never let a `2` read as a `0`.
- **Only NEW lines count.** Findings come from lines this diff adds (or, for removals, lines it removes). Pre-existing suppressions and skips are not reported.
- **Reads its own exit codes.** Nothing here is piped. Each command writes to a file, its own status is read, then the file is read. grep exits `0` on a match (a violation here), `1` on no match (clean), `2` on error (the guard failed, not clean).
- **Reports the rule, the location and the matched token only, never the whole line.** A line may hold a secret.
- A clean run says nothing about whether the suites pass. Run the count gates separately.

## The script

Save it outside the repo (for example `"$LOG/floor-guard.sh"`, with `LOG` a scratch directory) and run it from the repo root: `bash "$LOG/floor-guard.sh" > "$LOG/guard.out" 2>&1; echo "guard rc=$?"`, then read `"$LOG/guard.out"`.

The script keeps its scratch files in `$FLOOR_GUARD_LOG` if set, else in a fresh `mktemp -d` directory, and prints that path when it exits. The directory holds a full copy of the diff (`diff.txt`, `lines.tsv`, `*.txt`). **Delete it after reading**: `rm -r "$path"` on the exact path it printed.

```bash
#!/usr/bin/env bash
# Usage (repo root): bash floor-guard.sh [base-ref]     default base: origin/main
BASE_REF="${1:-origin/main}"
LOG="${FLOOR_GUARD_LOG:-$(mktemp -d)}"
trap 'echo "floor-guard: scratch dir (holds a copy of the diff; delete it after reading): $LOG"' EXIT
viol=0
die()  { echo "floor-guard: COULD NOT RUN: $*"; exit 2; }
hit()  { echo "VIOLATION [$1]"; viol=1; }
# grep -no output ("N:token") + a location file ("file:line" per input line) -> "file:line  token"
locate() { awk 'NR==FNR{loc[FNR]=$0; next} {i=index($0,":"); print loc[substr($0,1,i-1)+0] "  " substr($0,i+1)}' "$2" "$1"; }

git rev-parse --show-toplevel > /dev/null 2>&1 || die "not a git repo"
BASE="$(git merge-base "$BASE_REF" HEAD)"; rc=$?
{ [ "$rc" -eq 0 ] && [ -n "$BASE" ]; } || die "no merge base against $BASE_REF"

# 1. The diff, in a pinned format: tracked (base..working tree), then untracked files.
FMT=(--no-renames --no-color --no-ext-diff --src-prefix=a/ --dst-prefix=b/ --unified=0)
git diff "${FMT[@]}" "$BASE" -- > "$LOG/diff.txt" 2> "$LOG/diff.err" || die "git diff failed"
git ls-files -z --others --exclude-standard > "$LOG/untracked.z" 2>> "$LOG/diff.err" || die "git ls-files failed"
while IFS= read -r -d '' f; do
  git diff "${FMT[@]}" --no-index -- /dev/null "$f" >> "$LOG/diff.txt" 2>> "$LOG/diff.err"
  r=$?; [ "$r" -le 1 ] || die "git diff --no-index failed for an untracked file (rc=$r)"   # 1 = differs = normal
done < "$LOG/untracked.z"
[ -s "$LOG/diff.err" ] && die "git wrote to stderr (read $LOG/diff.err); the diff may be incomplete"

# 2. Flatten to  A|R <TAB> file <TAB> line <TAB> text.  Header lines only count right after
#    "diff ", so an added "++x" or a removed SQL "-- comment" is not mistaken for a header.
#    Git's trailing tab after a path with spaces is cut; tabs in code text become spaces.
awk '
function clean(s) { gsub(/\t/, " ", s); return s }
/^diff /  { hdr=1; next }
/^@@/     { hdr=0
            s=$0; sub(/^@@ -/,"",s); sub(/[ ,].*/,"",s); ol=s+0
            s=$0; sub(/^@@ -[^ ]* \+/,"",s); sub(/[ ,].*/,"",s); nl=s+0; next }
hdr && /^--- /    { o=substr($0,5); sub(/\t.*$/,"",o); sub(/^a\//,"",o); next }
hdr && /^\+\+\+ / { f=substr($0,5); sub(/\t.*$/,"",f); sub(/^b\//,"",f); next }
hdr       { next }
/^\+/     { print "A\t" f "\t" nl "\t" clean(substr($0,2)); nl++ }
/^-/      { print "R\t" o "\t" ol "\t" clean(substr($0,2)); ol++ }
' "$LOG/diff.txt" > "$LOG/lines.tsv" || die "awk flatten failed"
[ -s "$LOG/diff.txt" ] && [ ! -s "$LOG/lines.tsv" ] && die "the diff has content but no lines could be read from it (a binary-only diff also lands here: read it by hand)"

# Added lines as text + location, for grep -o: all files, and code files (no .md .mdx .txt .lock).
awk -F'\t' -v d="$LOG" '
$1=="A" { print $4 > (d "/all.txt"); print $2 ":" $3 > (d "/all.loc")
          if ($2 !~ /\.(md|mdx|txt|lock)$/) { print $4 > (d "/code.txt"); print $2 ":" $3 > (d "/code.loc"); print > (d "/added-code.tsv") } }
' "$LOG/lines.tsv" || die "awk split failed"
for x in all.txt all.loc code.txt code.loc added-code.tsv; do : >> "$LOG/$x"; done   # exist even when empty

# 3. Move 3 and the skip half of move 2: new suppressions and skips (code files).
PAT='# *noqa|ruff: *noqa|# *type: *ignore|mypy: *ignore-errors|pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail|importorskip)\(|(^|[^[:alnum:]_])(it|describe|test)\.skip|\.only\(|@ts-expect-error|@ts-ignore|@ts-nocheck|eslint-disable'
grep -noE -e "$PAT" "$LOG/code.txt" > "$LOG/supp.raw"; rc=$?
case "$rc" in
  0) hit "new-suppression-or-skip"; locate "$LOG/supp.raw" "$LOG/code.loc" > "$LOG/supp.txt" || die "locate failed"; cat "$LOG/supp.txt" ;;
  1) : ;;
  *) die "grep failed on suppressions (rc=$rc)" ;;
esac

# 4. Move 2: deleted test files (with --no-renames, a moved test file is a deletion here).
git diff --no-renames --diff-filter=D --name-only "$BASE" -- > "$LOG/deleted.txt" || die "git diff --name-only failed"
grep -nE '(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[jt]sx?|(tests?|__tests__)/)' "$LOG/deleted.txt" > "$LOG/deleted-tests.txt"; rc=$?
case "$rc" in
  0) hit "test-file-deleted (or moved: confirm it reappears with the same tests)"; cat "$LOG/deleted-tests.txt" ;;
  1) : ;;
  *) die "grep failed on deleted files (rc=$rc)" ;;
esac

# 5. Move 2: fewer test functions or assertions in a test file that stayed (counts only).
awk -F'\t' '
function istest(p) { return p ~ /(^|\/)(test_[^\/]*\.py|[^\/]*_test\.py|conftest\.py|[^\/]*\.(test|spec)\.[jt]sx?)$/ || p ~ /(^|\/)(tests?|__tests__)\// }
istest($2) {
  t = ($4 ~ /def test_/ || $4 ~ /(^|[^A-Za-z_.])(it|test)\(/)
  a = ($4 ~ /(^|[^A-Za-z_])assert([ (_]|$)/ || $4 ~ /expect\(/ || $4 ~ /pytest\.raises/)
  if ($1=="R") { rt[$2]+=t; ra[$2]+=a } else { at[$2]+=t; aa[$2]+=a }
  seen[$2]=1
}
END { for (p in seen) {
  if (rt[p] > at[p]) print p ": tests " rt[p] " removed, " (at[p]+0) " added"
  if (ra[p] > aa[p]) print p ": assertions " ra[p] " removed, " (aa[p]+0) " added" } }
' "$LOG/lines.tsv" > "$LOG/fewer.txt" || die "awk test-count failed"
[ -s "$LOG/fewer.txt" ] && { hit "fewer-tests-or-assertions"; cat "$LOG/fewer.txt"; }

# 6. Move 1: a lowered floor or a raised ceiling in infra/ci/floors.json.
git show "$BASE:infra/ci/floors.json" > "$LOG/floors.base.json" 2> "$LOG/floors.err" || die "cannot read floors.json at the base"
python3 -I - "$LOG/floors.base.json" infra/ci/floors.json > "$LOG/floors.out" 2>&1 <<'PY'
import json, sys
try:
    old = json.load(open(sys.argv[1])); new = json.load(open(sys.argv[2]))
    bad = []
    for k, v in old.get("floors", {}).items():
        n = new.get("floors", {}).get(k)
        if n is None: bad.append(f"floor removed: {k}")
        elif n < v:   bad.append(f"floor lowered: {k} {v} -> {n}")
    for k, v in old.get("ceilings", {}).items():
        n = new.get("ceilings", {}).get(k)
        if n is None: bad.append(f"ceiling removed: {k}")
        elif n > v:   bad.append(f"ceiling raised: {k} {v} -> {n}")
    print("\n".join(bad))
    sys.exit(1 if bad else 0)
except Exception as e:
    print("floors check error:", type(e).__name__)
    sys.exit(2)
PY
rc=$?
case "$rc" in
  0) : ;;
  1) hit "floor-lowered-or-ceiling-raised"; cat "$LOG/floors.out"
     git diff --quiet "$BASE" -- docs/build-log.md; r=$?
     case "$r" in
       0) echo "  docs/build-log.md is unchanged: no written reason" ;;
       1) echo "  docs/build-log.md changed: read it for a reason naming which tests went and why" ;;
       *) die "git diff on build-log failed (rc=$r)" ;;
     esac ;;
  *) die "floors comparison failed (rc=$rc)" ;;
esac

# 7. Move 4: any removed line in the five guard tests (file and old line number only).
awk -F'\t' '
$1=="R" && ($2=="apps/web/src/components/legal/providers.test.ts" ||
            $2=="packages/design-system/src/tokens/reportIsolation.test.ts" ||
            $2=="apps/web/src/app/referrerPolicy.test.ts" ||
            $2=="apps/api/tests/test_facts_only.py" ||
            $2=="apps/api/tests/test_secrets_stay_out_of_logs.py") { print $2 ":" $3 "  line removed" }
' "$LOG/lines.tsv" > "$LOG/guardtests.txt" || die "awk guard-test check failed"
[ -s "$LOG/guardtests.txt" ] && { hit "guard-test-lines-removed (read them at the base: a reworded line is fine, a dropped case is not)"; cat "$LOG/guardtests.txt"; }

# 8. Move 5: piped or "|| true" test/lint commands in CI and scripts (location only) ...
awk -F'\t' '
$2 ~ /(^|\/)(\.github\/workflows\/|infra\/|scripts\/|Makefile$|package\.json$)|\.sh$/ &&
$4 ~ /(pytest|vitest|ruff|mypy|tsc|assert_count|alembic|pnpm (test|lint|typecheck)|node --test)/ &&
($4 ~ /\|\| *true/ || $4 ~ /\|[[:space:]]*(tail|head|grep|tee|wc)([[:space:]]|$)/) { print $2 ":" $3 "  pipe or || true after a test/lint command" }
' "$LOG/added-code.tsv" > "$LOG/piped.txt" || die "awk pipe check failed"
[ -s "$LOG/piped.txt" ] && { hit "piped-or-true-test-command"; cat "$LOG/piped.txt"; }
# ... and --no-verify in ANY added line, .md included (CLAUDE.md and .claude/agents/*.md are how agents get instructions).
grep -noE -e '--no-verify' "$LOG/all.txt" > "$LOG/noverify.raw"; rc=$?
case "$rc" in
  0) hit "no-verify"; locate "$LOG/noverify.raw" "$LOG/all.loc" > "$LOG/noverify.txt" || die "locate failed"; cat "$LOG/noverify.txt" ;;
  1) : ;;
  *) die "grep failed on --no-verify (rc=$rc)" ;;
esac

if [ "$viol" -eq 0 ]; then echo "floor-guard: clean (diff only; run the count gates separately)"; exit 0; fi
echo "floor-guard: violation(s) above. Each lowers the bar: fix the code, or stop and ask the founder."
exit 1
```

## Reading the result

- **rc 0:** clean for the diff. Still run the count gates (`CLAUDE.md`, "The verification bar").
- **rc 1:** read `guard.out`. Each finding is `file:line  token`; open the line yourself. For each, say new or moved, and what you did (fixed the code, or asked the founder). A line that reappears with the same text in the same file (`R` and `A` in `lines.tsv`) was moved, not added.
- **rc 2:** the guard did not run. The report says "guard could not run", never "clean".
- Afterwards delete the scratch directory the script printed (it holds a diff copy).

## Limits (be honest in the report)

- It is regex-shallow. It catches the cheap moves agents make, not a determined hider. A hit inside a string or comment is still worth a look; judge it, do not auto-ignore it.
- Pipes and `|| true` are only caught on one line. A YAML `run: |` block that continues a test command over lines needs a manual read of the CI diff (`git diff "$BASE" -- .github/workflows/ci.yml`).
- A binary-only diff (no text lines) exits 2 by design; read it by hand.
- Also read by eye, not scripted: `git diff "$BASE" -- infra/ci/assert_count.py .github/workflows/ci.yml` for a regex loosened or a step dropped; and `pytest` selection flags (`--deselect`, `--ignore`, `-k "not ..."`, `continue-on-error`) added to CI or config.
- An optional secret scanner (gitleaks) is a founder decision. It is not installed here, and this skill does not add it.
