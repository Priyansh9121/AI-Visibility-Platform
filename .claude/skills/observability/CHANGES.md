# Changes from observability-and-instrumentation

Source = skills/observability-and-instrumentation/SKILL.md.

- Rewrote for structlog and dotted event names (source :54-68 used JS `logger.warn({event:'payment_failed'})`) -> matches `services/billing.py`, `services/rate_limit.py`, `errors.py`.
- Removed the Express request-id middleware and `runLog`/entry-point section (:79-104) -> AVP has no request-id or contextvars binding (checked in `apps/api/src`); replaced with a statement to correlate by ids and a founder question instead of silently adding middleware.
- Removed RED/USE metrics and `prom-client` example (:108-132) -> new runtime dependency and likely outbound host that `providers.test.ts` would flag (audit risk).
- Removed OpenTelemetry tracing and `@opentelemetry/sdk-node` example (:134-150) -> same dependency risk; replaced with section 6 requiring founder decision and the classification steps from CLAUDE.md.
- Removed alerting rules and `docs/runbooks/` convention (:152-188) -> no alerting backend exists; `docs/runbook.md` is the single runbook and is edited by the scribe.
- Replaced "never log secrets" (:106) with AVP's allowed/forbidden field lists, including facts-only for third-party text and the `logs.py` history (`classify.result`, `fixes.reasoning`).
- Added the real guard `test_secrets_stay_out_of_logs.py`, a `capture_logs` test recipe from `test_billing.py`, and a negative control step -> source :190-197 verified telemetry by staging/tracing UI, which does not exist here.
- Shell snippet: `LOG` defined, subshell, redirect to file with own exit code, no pipes; count gate via `infra/ci/assert_count.py`.
- Replaced sibling-skill references (`debugging-and-error-recovery`, `performance-optimization`, `shipping-and-launch`, `security-and-hardening`; :21-23, :106) -> `systematic-debugging` or removed.
- Dropped the Common Rationalizations table and unsourced generic advice (alert thresholds such as 1%/5 min, :156-161) -> no source for the numbers.
- Rewrote the frontmatter description in third person, naming AVP surfaces and exclusions.

## Security review fixes (2026-10-01)

- Added `logger.exception`/`exc_info=True` to forbidden fields and red flags, with the SQLAlchemy `[parameters: ...]` exposure and the existing call sites as an unconfirmed open item -> tracebacks render `str(exc)` (M1).
- Corrected the claim that the guard catches all three sinks: `capture_logs` sees event dicts before rendering, not tracebacks or stdout (M1).
- Restored the source's second half (source :106): no whole bodies, Stripe events, Pydantic models or ORM rows; allowlist fields; added full names and IP addresses to the PII list (L2).
- Snippet: `cd apps/api || exit 1` and an absolute-`LOG` check (L3).
- Test recipe now also asserts a sentinel token is absent, as in `test_secrets_stay_out_of_logs.py:36-37` (L4).
- Corrected the guard note: inside `capture_logs` structlog renders nothing, so no structlog traceback is checked by `test_secrets_stay_out_of_logs.py`; only stdlib tracebacks reach `caplog.text`; to test one, emit outside `capture_logs` and read `capsys` (re-review).
- Moved the absolute-`LOG` check inside the subshell with an error message (re-review).
