# Changes from the source (ECC-main/skills/error-handling/SKILL.md, 377 lines)

- Dropped the Go section (source :~255-295) and all Express/Node/Next route-handler code (:113-165) -> not AVP's stack; AVP's API is FastAPI.
- Replaced the `{ error: { code, message } }` envelope (source :241/:250 handler bodies, :371 checklist) with AVP's existing RFC 9457 `ProblemError` hierarchy in `apps/api/src/avp_api/errors.py` -> a second envelope would churn OpenAPI and shared-types.
- Removed `console.log(result.value.email)` (source :110) and the generic `logger.error(... error: result.error)` style; added rules: ids not emails, exception class names not `str(exc)`, no tokens/secrets/credentialed URLs, structlog with `logs.py` redaction -> source logged an email; AVP bars emails in logs (`tests/test_secrets_stay_out_of_logs.py`).
- Replaced `retryIf = () => true` (source :319) and the generic TS `withRetry` with an allow-list rule (idempotent ops; transient causes only), `Retry-After` inside a ceiling, jitter, explicit timeouts, with AVP examples (engines.py, celery_app.py, Stripe client) -> source retried everything by default.
- Replaced "React components are wrapped in `ErrorBoundary`" and the class ErrorBoundary (source :377, :~170-205) with Next 15 `error.tsx`/`global-error.tsx`/`not-found.tsx` guidance, noting none exist today and adding them is a founder-visible change -> pre-App-Router pattern.
- Replaced the `USER_ERROR_MESSAGES` code map (source :351-363) with AVP's `ApiProblem` + `ErrorState` flow -> AVP already has one client error vocabulary.
- Added fail-closed vs fail-open (rate limiter, sessions, reset-request) and the blurred-failure rule for reset/invite/ticket -> AVP security decisions absent from source.
- Added the facts-only rule for error text and logs -> house rule.
- Dropped the source's frontmatter `metadata.origin`, the broad "Python/TS/Go" description, and the generic Result pattern, typed-error boilerplate and circuit-breaker mention -> not needed; circuit breakers would need a dependency decision.
- Added a "no new dependency or outbound host" line (Sentry etc. = founder decision + `providers.test.ts` + `/privacy` + licence audit).
- Tests section uses count gates and non-piped output, per house rules; no coverage percentages.
- Dropped the source's catch-all FastAPI `Exception` handler that called `logger.exception(..., exc_info=exc)` (source :244-251) -> it logs `str(exc)` plus a traceback (SQLAlchemy parameters can carry emails or tokens), and a catch-all is a behaviour change for the founder; SKILL.md section 3 now warns against `logger.exception`/`exc_info`.
- Added review notes: rate_limit.enforce catches all RedisError/OSError (rate_limit.py:212), unhandled 500s likely lack CORS headers (labelled inferred), never stop the shared dev Redis, `cd apps/api || exit 1` in the snippet, description in third person.
- Quoted the SKILL.md description value -> it contained an unquoted ": ", which is invalid YAML and would stop the skill loading.
