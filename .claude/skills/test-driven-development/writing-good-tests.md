# Writing Good Tests (AVP)

Load this when writing or changing tests, adding mocks, or adding test helpers.

```
1. Every test names the break it catches
2. Every test exercises the real thing
```

A test written first and watched failing against real code has already proven it can fail.

## Principle 1: name the break

Before the test body, answer: what production change should make this fail, and is that change a bug or a decision?

**Derive expectations independently.** Use literals and hand-checked fixtures; table-driven tests with literal expected values are preferred. An expectation computed by the code under test passes whatever that code does. This matters most for scoring: expected scores come from `docs/scoring-spec.md` by hand.

```python
# Bad: same function computes both sides, always true
expected = visibility_score(inputs)
assert visibility_score(inputs) == expected

# Good: hand-derived literal
assert visibility_score(inputs) == 62.5
```

**No change detectors.** (Deliberate pins are exempt: see below.) If only an intentional decision can fail the test (a constant's value, exact message wording, private structure), it fires on redesign and sleeps through bugs. Test the behaviour that depends on the decision: not `MAX_RETRIES == 5`, but "a failing call is retried 5 times and a 6th attempt never happens".

**Behaviour, not text.** Asserting that a script or config contains an exact line proves only that the source is the source. Run code against controlled inputs and assert outputs, side effects or exit codes. This has one deliberate exception, below.

**Your code, not the framework.** Test the contract your code makes at its boundaries: the route you register, the query you emit, the payload you produce. Upstream mechanics are their maintainers' tests. Constructors, getters, constants and trivial forwarding earn tests only when they validate, normalise, default, derive, enforce or cause side effects.

### Exemption: deliberate pins

`apps/api/tests/test_scoring.py` `test_formula_version_is_recorded` asserts `FORMULA_VERSION == "v5"` on purpose, so a formula change cannot ship without a deliberate bump. It is not a change detector to remove. When you bump the version it fails; the person making that decision updates it visibly and reports it. Deleting, skipping or loosening it (or any existing test) needs the founder's OK and a line in the report; the count gate is only a floor and would not notice.

### Exemption: AVP's guard tests grep source on purpose

These policy tests read source text or built output because their job is to enforce a rule across the codebase. Do not "fix" them into behaviour tests, do not delete them, and do not treat their source-grepping as the warning sign below:

- `apps/web/src/components/legal/providers.test.ts`: every outbound host or runtime dependency is classified and named on `/privacy`.
- `packages/design-system/src/tokens/reportIsolation.test.ts`: the report never uses the Working-screen accent layer and stays paper in both themes.
- `apps/web/src/app/referrerPolicy.test.ts`: token routes send `Referrer-Policy: no-referrer`.
- `apps/api/tests/test_facts_only.py`: no third-party raw text is stored (schema side).
- `apps/api/tests/test_secrets_stay_out_of_logs.py`: no secret, token or email in a log.

When a change trips one of them, the change is what needs adjusting (classify the host, name it on `/privacy`, redact the log), not the guard. Adding a new rule of the same kind is legitimate; say so in your report.

### Gate

```
BEFORE writing the test body:
  Name the production change that would make this test fail.
  Cannot name one            -> redesign around an observable behaviour
  "The source text changed"  -> run the artifact and assert its effects
                                (unless it is a guard test above)
  Only intentional decisions -> change detector; test the behaviour behind it
  Confirm the expected value is derived without the code under test.
```

## Principle 2: exercise the real thing

**The mock earns no assertions.** A mock assertion passes when the mock is present and fails when it is absent. Assert the real component's behaviour; if the mock is what you are checking, unmock it or delete the assertion.

```tsx
// Good: real behaviour
expect(screen.getByRole('navigation')).toBeInTheDocument();
// Bad: mock existence
expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();
```

**Mock at the right level.** Learn the real method's side effects before replacing it; mock the slow or external operation and keep what the test depends on real. In `apps/api`, prefer the real `avp_test` Postgres and Redis db 15 over a mocked session; mock only outbound providers (LLM engines, Stripe, HTTP crawl targets). In `apps/web`, use `vi.mock` sparingly and never for the component under test.

**Make doubles specific.** When arguments, call counts or ordering are the contract, assert them. Give each branch (success, error, malformed) its own fixture, so the wrong branch cannot satisfy the expectation.

**Mirror real data completely.** Mock the whole structure as it exists (all documented fields), not just what the test reads. Partial mocks pass while integration breaks. Fixtures stay facts-only: booleans, counts, positions, URLs, names; never realistic third-party answer text or page HTML.

**Production classes carry production methods only.** Cleanup only tests need lives in test utilities or fixtures (`conftest.py`, test helpers). Vitest teardown belongs in `beforeEach`/`afterEach`, not in production modules.

**Prefer real components over complex mocks** when mock setup outgrows the test logic.

### Gate

```
BEFORE adding a mock or test helper:
  List the real method's side effects; keep the ones the test depends on real.
  Mock responses mirror the complete real structure.
  A method only tests call lives in test utilities, not production.
  About to assert on the mock itself? Unmock it or delete the assertion.
```

## Tests ship with the implementation

Ship the tests the behaviour needs and only those. Trivial code and human prose earn none. Bump the matching floor in `infra/ci/floors.json` in the same change (see SKILL.md, count gates).

## The mutation check

Before finishing, mentally mutate the production code; at least one test should fail for each realistic mutation: wrong constant or argument, wrong branch, missing side effect, empty or default return, missing validation for zero, empty, unauthorised or malformed input. Where a test proves something important, do it for real: break the code, see the test fail, restore the file, and report the result. Restore by `cp file "$LOG/file.bak"` before, `cp` back after, and `cmp` to confirm; never `git checkout` or `git restore`, which wipe uncommitted work.

## Quick reference

| When you | Do |
|---|---|
| Write any test | Name the break it catches |
| Build an expected value | Derive it by hand, never with the code under test |
| Test a script or config | Run it and assert effects; do not grep text (guard tests excepted) |
| Want to assert on a mocked element | Test the real component, or unmock |
| Are about to mock a method | Learn its side effects; mock the slow or external level |
| Need cleanup only tests use | Put it in test utilities |
| Finish a test file | Run the mutation check |

## Warning signs

- Setup and assertion share the same object
- The test can fail only through a crash or missing selector
- It fails on every intentional change, never on accidental breakage (except a deliberate pin such as `test_formula_version_is_recorded`)
- Expected values hidden behind loops, builders or helpers
- The test greps source text, or asserts a removed symbol stays removed (except the five guard tests above)
- It would still matter if only the framework remained
- It exists for coverage rather than checking an outcome (AVP uses count gates, not coverage targets)
- An assertion checks a `*-mock` test ID
- A method is called only from test files
- Mock setup is more than half the test
- Mocking "just to be safe"
