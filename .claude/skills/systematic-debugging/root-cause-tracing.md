# Root Cause Tracing

Bugs surface deep in the stack (a bad row written by a worker, a wrong URL fetched by the crawler). Fixing where the error appears treats a symptom.

**Core principle:** trace backward through the call chain to the original trigger, then fix at the source.

## Use when
- The error is deep in execution, not at the entry point.
- The stack trace shows a long chain, or it is unclear where the bad value came from.
- Something leaks between tests and you need to know which one causes it.

## The process
1. **Observe the symptom.** E.g. a scan row stuck `QUEUED`, a `None` where a domain was expected, a page fetched that `RobotsGate` should have refused.
2. **Find the immediate cause.** What code directly produces it?
3. **Ask what called it.** Walk up: task body <- Celery task <- API router that enqueued it <- request handler.
4. **Ask what value was passed** at each hop, and keep going up.
5. **Find the original trigger**, where the bad value or state first appears. Fix there.

Example shape (illustrative): `crawl` fetched a URL with an empty host -> called from the audit task with `domain=""` -> the router built the task args from a scan whose `domain` was normalised to empty -> the normaliser accepted whitespace. Root cause: the normaliser, not the crawler.

## Adding instrumentation when you cannot trace by hand
Log before the operation, not after it fails, and include the call chain. Log ids, counts and names only; never tokens, emails, env values or third-party text.

```python
import sys
import traceback

def fetch(url: str) -> None:
    # temporary; remove after the trace
    print("DEBUG fetch:", {"host_present": bool(url), "stack": "".join(traceback.format_stack(limit=8))}, file=sys.stderr)
```

In pytest, `print` to stderr or use `capsys`/`caplog`; the app logger may be suppressed. Capture with a redirect, then read the file:

```bash
cd apps/api && uv run pytest -q -p no:cacheprovider -s tests/test_x.py > "$LOG" 2>&1; echo "rc=$?"
```
then search `$LOG` with the Read tool or a separate grep on the file (never a pipe from the test command). Look for the test name, the line that triggers the call, and the repeating parameter. Remove the instrumentation before finishing.

## Finding which test pollutes shared state (bisection)
Symptom: a test passes alone but fails in the full run (or the reverse), or state such as a DB row, a Redis key in db 15, a module-level singleton or a file appears unexpectedly. Bisect by hand; do not write or run a script that loops the suite.

1. Confirm the failure and record the exact command and its exit code (redirect to a file, never pipe).
2. Run the failing test alone. If it passes alone, the cause is order or shared state.
3. Split the preceding tests into two halves (by file, then by test id) and run each half followed by the failing test:
   `cd apps/api && uv run pytest -q -p no:cacheprovider <half files> tests/test_failing.py > "$LOG" 2>&1; echo "rc=$?"`
   Web: vitest runs files in parallel in its own order, so argument order does not control it; bisect by running different subsets of files, and if order matters use `--no-file-parallelism`. `corepack pnpm --filter @avp/web exec vitest run <half files> src/path/failing.test.ts > "$LOG" 2>&1; echo "rc=$?"`
4. Keep the half that reproduces; halve again until one file, then one test, remains. That is the polluter.
5. Read what it leaves behind (unreset module state, an un-awaited task, an unclosed connection, a fixture without teardown, a Redis key not cleaned) and fix the leak at its source with a fixture or teardown. Then apply `defense-in-depth.md`.
Note that vitest `beforeEach` teardowns and 'use client' values in server components are common culprits on the web side. Tests use the `avp_test` DB and Redis db 15 only; do not point them elsewhere to "make it pass".

## Key principle
Found the immediate cause -> can you trace one level up? If yes, trace back until you reach the source, fix there, then add validation at each layer. Never fix just where the error appears.
