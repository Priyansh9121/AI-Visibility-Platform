"""RFC 9457 problem+json error handling.

api-contracts.md fixes the convention:
    { type, title, status, detail, instance }
served as `application/problem+json`.

Every error the API emits goes through here, so the contract holds by
construction rather than by each route remembering to format its own errors.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import AuthenticationError, AuthorizationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# `type` is a URI reference identifying the error class. Relative URIs are
# permitted by RFC 9457 and keep us from promising to host a docs page we have
# not built yet.
TYPE_BASE = "/problems"


class ProblemError(Exception):
    """Base class for errors that render as an RFC 9457 problem document."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    problem_type: str = "internal-error"
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        self.detail = detail or self.title
        self.headers = headers or {}
        self.extra = extra
        super().__init__(self.detail)

    def to_dict(self, instance: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{TYPE_BASE}/{self.problem_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
        }
        body.update(self.extra)
        return body


class ValidationProblem(ProblemError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    problem_type = "validation-failed"
    title = "Request validation failed"


class AuthenticationRequired(ProblemError):
    status_code = status.HTTP_401_UNAUTHORIZED
    problem_type = "authentication-required"
    title = "Authentication required"


class InvalidCredentials(ProblemError):
    status_code = status.HTTP_401_UNAUTHORIZED
    problem_type = "invalid-credentials"
    title = "Invalid email or password"


class InvalidResetToken(ProblemError):
    """A password-reset token that is unknown, expired, used, or orphaned.

    **400, not 404, and one class for all four reasons.** A 404 would confirm
    which guesses were shaped like real tokens; separate classes would leak the
    same thing through `type`. The reset-confirm endpoint has exactly one
    failure to report.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    problem_type = "invalid-reset-token"
    title = "That reset link is not valid"


class InvalidInvitation(ProblemError):
    """A seat invitation that is unknown, expired, used, revoked, or orphaned.

    **400, not 404, and one class for all five reasons** — the same shape as
    `InvalidResetToken` above, and for the same reason. A 404 would confirm
    which guesses were shaped like real tokens; separate classes would leak the
    same thing through `type`. "The seat this points at has since been removed"
    is folded in here too: distinguishing it would tell a stranger about an
    agency's roster changes.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    problem_type = "invalid-invitation"
    title = "That invitation link is not valid"


class PermissionDenied(ProblemError):
    status_code = status.HTTP_403_FORBIDDEN
    problem_type = "permission-denied"
    title = "You do not have permission to perform this action"


class OriginNotAllowed(ProblemError):
    """A write from a page that is not the web app.

    **403, and a type of its own** rather than `permission-denied`: the caller may
    hold every role there is, and that is not what is refused. What is refused is
    where the request came from — an `Origin` that is `null` or not one of
    `CORS_ALLOW_ORIGINS`, with or without a session, or no `Origin` at all on a
    request that carries the session cookie — so a client can tell "this page may
    not do that" from "this person may not". Rendered by `origin_check.py`, which
    sits outside the exception handlers and so builds the response itself,
    through the same serializer they use.
    """

    status_code = status.HTTP_403_FORBIDDEN
    problem_type = "origin-not-allowed"
    title = "Origin not allowed"


class NotFound(ProblemError):
    status_code = status.HTTP_404_NOT_FOUND
    problem_type = "not-found"
    title = "Resource not found"


class Conflict(ProblemError):
    status_code = status.HTTP_409_CONFLICT
    problem_type = "conflict"
    title = "Conflicting request"


class FailedScanNotShareable(Conflict):
    """Minting a share link for a FAILED scan is refused (409).

    Founder decision, checklist 3.24: a failed scan has no score that is ever
    shown, so there is no report worth sending. PARTIAL scans stay shareable.
    """

    problem_type = "failed-scan-not-shareable"
    title = "Failed scan cannot be shared"


class EmailAlreadyRegistered(Conflict):
    problem_type = "email-already-registered"
    title = "That email address is already registered"


class CheckoutInProgress(Conflict):
    """Another checkout for this agency holds its row lock (409).

    A specific type rather than the generic `conflict`, so a client can say
    "try again in a moment" without parsing prose. Retrying is safe: nothing was
    created at Stripe.
    """

    problem_type = "checkout-in-progress"
    title = "Checkout already in progress"


class InvalidWebhookSignature(ProblemError):
    """A billing webhook whose Stripe signature is missing, malformed or wrong.

    **400, and it says so plainly** — unlike `InvalidResetToken` and
    `InvalidInvitation` above, which blur several causes into one refusal
    because the caller might be an attacker probing for real tokens. There is
    nobody to protect here: the only legitimate caller is Stripe, Stripe is not
    enumerating anything, and a webhook endpoint that is vague about why it
    refused is a webhook endpoint nobody can debug at three in the morning.

    Not 401: there is no session to have, and no credentials to re-present.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    problem_type = "invalid-webhook-signature"
    title = "Webhook signature verification failed"


class BillingNotConfigured(ProblemError):
    """A billing route reached while a required Stripe setting is unset.

    **503, not 500.** Nothing is broken — the server is running correctly and
    has been told to do something it has not been given the credentials for.
    That is a deployment state, and 503 is the status that says "try again once
    somebody fixes the configuration" rather than "this code has a bug".

    The `detail` carries `provider_key`'s own message, which names the exact
    environment variable, so an operator reading a browser network panel is told
    what to set rather than being sent to the logs. That is the same courtesy
    every other integration in this codebase already gets, and it is the reason
    this class exists instead of letting a RuntimeError become a bare 500.

    **This is the webhook's answer when `STRIPE_WEBHOOK_SECRET` is unset.** It
    refuses. It does not fall back to trusting an unverified payload, because a
    payload claiming a subscription is active is exactly what an attacker would
    send, and "we were not configured to check" is not a reason to believe one.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "billing-not-configured"
    title = "Billing is not configured on this server"


class GoogleSignInNotConfigured(ProblemError):
    """Google sign-in was asked for and no OAuth client is configured — Epic 20.

    `503` rather than `404`, for the reason `BillingNotConfigured` gives: the
    route exists and the feature is real; what is missing is a credential the
    operator of this deployment has not supplied. The web app shows the
    button regardless and this is what it gets until the founder creates the
    Google Cloud client — a loud, named answer, not a dead link.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "google-sign-in-not-configured"
    title = "Google sign-in is not configured"


class InvalidGoogleTicket(ProblemError):
    """The sign-up ticket is unknown, used, or expired — Epic 20.

    One response for all three, the way `InvalidInvitation` and
    `InvalidResetToken` answer: the ticket is a bearer credential and which
    way it failed is not the holder's business.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    problem_type = "invalid-google-ticket"
    title = "That Google sign-in can no longer be used"


class SeatLimitReached(ProblemError):
    status_code = status.HTTP_409_CONFLICT
    problem_type = "seat-limit-reached"
    title = "Seat limit reached"


class SubscriptionAlreadyActive(ProblemError):
    """Checkout refused: the agency already has a paid-up subscription.

    **409.** A second Checkout Session would create a second subscription and
    bill the agency twice; Stripe does not stop that. "Paid up" is exactly
    `services.billing.ACTIVE_STATUSES` (`active`, `trialing`). A subscription
    that is live at Stripe but not paid up (`past_due`, `unpaid`, `paused`,
    `incomplete`, or a status nobody has seen) answers `SubscriptionNeedsAttention`
    instead. The detail points at the billing portal, where the existing
    subscription is managed, and carries no Stripe id and not the status itself.
    """

    status_code = status.HTTP_409_CONFLICT
    problem_type = "subscription-already-active"
    title = "Subscription already active"


class SubscriptionNeedsAttention(ProblemError):
    """Checkout refused: the agency's subscription is live at Stripe but not paid up.

    **409.** `past_due`, `unpaid`, `paused` and `incomplete` are not "active" —
    `is_active()` and the scan allowance still count only `active` and
    `trialing` — but Stripe still holds each of them open, and a second Checkout
    Session would create a second subscription beside it (founder decision 4,
    2026-10-01). "Needs attention" is every status that is neither paid up nor
    over: it denies by default, so a status Stripe adds later is refused too
    (`services.billing.is_live_at_stripe`). The detail points at the billing
    portal, where the existing subscription is put right, and carries no Stripe id
    and not the status itself.
    """

    status_code = status.HTTP_409_CONFLICT
    problem_type = "subscription-needs-attention"
    title = "Subscription needs attention"


class ScanLimitReached(ProblemError):
    """The agency's scan allowance is spent — 2026-09-28.

    **402**, because that is what it is: the thing asked for costs money the
    caller has not paid. Carries a stable `code` — `SCAN_LIMIT_FREE_USED` (one
    free scan, lifetime), `SCAN_LIMIT_MONTHLY` (forty a month on the plan) or,
    since 2026-09-29, `SCAN_LIMIT_VERIFY_REQUIRED` (the free scan, asked for by
    a user with no linked Google identity) — plus `scansUsed`, `scanLimit` and,
    for the monthly case, `periodResetsAt`, so a client can say "resets on the
    1st" rather than "try later". The gate itself is
    `services/scan_allowance.py`; its docstring says when it is on.
    """

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    problem_type = "scan-limit-reached"
    title = "Scan limit reached"


class ServiceUnavailable(ProblemError):
    """A dependency this route cannot safely do without is down (503)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "service-unavailable"
    title = "Service unavailable"


class RateLimited(ProblemError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    problem_type = "rate-limited"
    title = "Too many requests"


def _problem_response(request: Request, exc: ProblemError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(instance=request.url.path),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=exc.headers or None,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire every error path to the problem+json representation."""

    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem_response(request, exc)

    @app.exception_handler(RedisConnectionError)
    @app.exception_handler(RedisTimeoutError)
    async def _handle_redis(request: Request, exc: Exception) -> JSONResponse:
        # Redis unreachable or too slow (connect/read timeout). Other RedisError
        # classes (a Lua bug) are NOT mapped, and neither are AuthenticationError
        # and AuthorizationError, which subclass ConnectionError in redis-py: a
        # rotated password is a config fault and stays a 500, not an outage.
        if isinstance(exc, AuthenticationError | AuthorizationError):
            raise exc
        # Fail closed with the same 503 problem the rate limiter gives. Only
        # the exception's class is logged; its text can carry the host.
        logger.warning("redis.unavailable", error=type(exc).__name__)
        return _problem_response(
            request,
            ServiceUnavailable(
                detail="This is temporarily unavailable. Please try again shortly."
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surface field-level detail under an extension member. RFC 9457 allows
        # extension members, and the frontend needs them to mark bad fields.
        problem = ValidationProblem(
            detail="One or more fields were invalid.",
            errors=[
                {
                    "field": ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]),
                    "message": err["msg"],
                    "type": err["type"],
                }
                for err in exc.errors()
            ],
        )
        return _problem_response(request, problem)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = ProblemError(detail=str(exc.detail))
        problem.status_code = exc.status_code
        problem.problem_type = _slug_for_status(exc.status_code)
        problem.title = _title_for_status(exc.status_code)
        return _problem_response(request, problem)


_STATUS_SLUGS = {
    400: ("bad-request", "Bad request"),
    401: ("authentication-required", "Authentication required"),
    403: ("permission-denied", "Permission denied"),
    404: ("not-found", "Resource not found"),
    405: ("method-not-allowed", "Method not allowed"),
    409: ("conflict", "Conflicting request"),
    422: ("validation-failed", "Request validation failed"),
    429: ("rate-limited", "Too many requests"),
}


def _slug_for_status(code: int) -> str:
    return _STATUS_SLUGS.get(code, ("internal-error", ""))[0]


def _title_for_status(code: int) -> str:
    return _STATUS_SLUGS.get(code, ("", "Internal server error"))[1]
