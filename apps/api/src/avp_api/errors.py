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

# What a request body that names a field its model does not declare is told (A8). Pydantic's own
# words are "Extra inputs are not permitted", which says nothing of which key; `field` already
# names the key, so this is the sentence beside it. The `type` stays pydantic's `extra_forbidden`.
FIELD_NOT_ACCEPTED = "This field is not accepted."

# What one validation problem carries at most (A8 review): the first errors, in the order pydantic
# found them, and the first characters of each `field`. A body can make pydantic build an error per
# bad item at every depth, and `field` echoes a key the sender chose; the answer to a request that
# is wrong in many places is the first of them, not all of them at any length. When the list is cut
# the problem says so, with a top-level `errorsTruncated: true` (A8b); it is absent otherwise.
MAX_VALIDATION_ERRORS = 20
MAX_FIELD_CHARS = 200

# What a request body that passes its size limit is told (A8b, `body_limit.py`): one plain sentence,
# the same for every route and for both ways the limit is found out (a declared length, or the
# count of the bytes as they arrive). It names no number: the limit is the operator's to know.
PAYLOAD_TOO_LARGE_DETAIL = "This request is larger than this service accepts."

# What a request that declares its length in a way no length can be written is told (A8b).
MALFORMED_CONTENT_LENGTH_DETAIL = "The Content-Length header of this request is not a valid length."

# What a request whose body did not arrive in time is told (A8b, `body_limit.py`): one sentence, the
# same for every route. It names no number: the deadline is the operator's to know.
REQUEST_TIMEOUT_DETAIL = "This request took too long to arrive."

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
    # The founder's sentence (D9): the one refusal for an unknown email, a wrong password, an
    # account with no password and an account that is not active. It is the default `detail` too,
    # which is what the sign-in screen shows.
    title = "Email or password is incorrect."
    # Set by `authenticate` to the live user the address matched (None when none did). For the
    # server's own log line only: never part of the problem document.
    user_id: str | None = None


class InvalidResetToken(ProblemError):
    """A password-reset token that is unknown, expired, used, or orphaned.

    **400, not 404, and one class, one `type` and one `title` for all four reasons.** A 404
    would confirm which guesses were shaped like real tokens; separate classes would leak the
    same thing through `type`.

    **What differs, since 2026-10-15 (A5 part 2, the founder's decision): the `detail`.** A link
    that was already used (redeemed, or retired by a newer request, a password change, a Google
    link or a seat removal) or that another request is redeeming right now says so, in a sentence
    that also tells a person who just set a password to sign in with it. Unknown, expired and
    orphaned links keep the older sentence.

    **What it reveals, and to whom.** A link is 256 random bits, so no guess is "shaped like a
    real token". The used answer is not tied to holding a real link, though: two requests at once
    with the same never-issued string both pass the claim, so the second hears "used". That
    blurs the answer and reveals nothing. What a holder of a link that was really issued learns,
    within the hour or after it, is that it was used or retired (not which of the two events),
    and nothing more.

    **The race's answer is identical to a used link's in every byte, not in timing.** It is
    refused at the claim in Redis and skips the database read, so only a holder of that link,
    during that redemption, could see the difference. The invitation accept
    (`InvalidInvitation`) is not changed.
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


class BadRequest(ProblemError):
    """A request that cannot be read as one (400), refused by a layer before the routes.

    The type is the one `_STATUS_SLUGS` gives every other bare 400, so a client sees one
    `bad-request` whichever layer said it. Rendered by `body_limit.py` for a `Content-Length` that
    is not a length, which sits outside the exception handlers and so builds the response itself,
    through the same serializer they use.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    problem_type = "bad-request"
    title = "Bad request"


class PayloadTooLarge(ProblemError):
    """A request body larger than its limit (413) — A8b.

    Rendered by `body_limit.py` when the request DECLARES a length over the limit, and by the
    exception handlers (`RequestBodyTooLarge` below) when the bytes counted as they arrive pass it.
    Both documents are this one: the sentence is `PAYLOAD_TOO_LARGE_DETAIL` and the title is
    here, and `_STATUS_SLUGS` reads its type and title from this class.
    """

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    problem_type = "payload-too-large"
    title = "Request too large"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail or PAYLOAD_TOO_LARGE_DETAIL, headers=headers, **extra)


class RequestBodyTooLarge(StarletteHTTPException):
    """Raised out of the `receive` that `body_limit.py` hands the app, when the count passes.

    **A Starlette `HTTPException` and not a `ProblemError`, and that is the point.** FastAPI reads
    a JSON body inside a `try`: it turns every exception it does not know into a `400` ("There was
    an error parsing the body"), and re-raises an `HTTPException` as it is ("If a middleware raises
    an HTTPException, it should be raised again"). A `ProblemError` raised from `receive` would be
    answered 400 and the refusal would read as a malformed body. This one reaches the exception
    handlers (`_handle_http` below), which render `PayloadTooLarge`'s document for 413.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=PayloadTooLarge.status_code, detail=PAYLOAD_TOO_LARGE_DETAIL
        )


class RequestTimeout(ProblemError):
    """A request body that did not arrive within the deadline (408) — A8b.

    Rendered by the exception handlers (`RequestBodyTimeout` below) when `body_limit.py` cuts off a
    body that has stopped, and by `body_limit.py` itself for an app that has no handler. The
    connection is closed after it (RFC 9110 15.5.9: a 408 says the server has stopped waiting), so
    it carries `Connection: close`, which `body_limit.py` adds to the response in either case.
    """

    status_code = status.HTTP_408_REQUEST_TIMEOUT
    problem_type = "request-timeout"
    title = "Request timeout"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail or REQUEST_TIMEOUT_DETAIL, headers=headers, **extra)


class RequestBodyTimeout(StarletteHTTPException):
    """Raised out of the `receive` that `body_limit.py` hands the app, when the deadline passes.

    A Starlette `HTTPException` for the reason `RequestBodyTooLarge` is: FastAPI re-raises it from
    its body read instead of turning it into a `400`, and `_handle_http` renders `RequestTimeout`'s
    document for 408.
    """

    def __init__(self) -> None:
        super().__init__(status_code=RequestTimeout.status_code, detail=REQUEST_TIMEOUT_DETAIL)


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


class CompetitorListChanged(Conflict):
    """A save of a client's competitor list named a list that is no longer the client's (409).

    `PUT /clients/{clientId}/competitors` replaces the whole list, so a writer that read the list
    before somebody else changed it would strike what it never saw (launch checklist 3.113). The
    save names the `version` of the list it read; this is the answer when that is not the version
    of the list now, or when it named none for a client that has a list (an old tab open across the
    deploy). Nothing is written. `current` is the list as `GET` shows it now, its version
    included, so the writer can say what changed and load it; it is null only if the client has no
    list at all.

    A type of its own, so the web can tell this from the other 409 of the route (a scan is running).
    """

    problem_type = "competitor-list-changed"
    title = "The competitor list changed"


class ScanNotExportable(Conflict):
    """The CSV of a report's figures was asked for a scan that has none to give (409).

    A scan that FAILED (its score is withheld, as for sharing), one still QUEUED or RUNNING
    (its figures are not final) or any other status that is not SUCCEEDED or PARTIAL. A type
    of its own, so the web can show the `detail`, which is one plain sentence for the person
    at the screen and names no code, no figure and nothing about the client.
    """

    problem_type = "scan-not-exportable"
    title = "This scan's figures cannot be exported"


class EmailAlreadyRegistered(Conflict):
    problem_type = "email-already-registered"
    title = "That email address is already registered"


# The founder's sentence (D9, 2026-10-11) for a sign-up the service cannot complete.
SIGN_UP_NOT_POSSIBLE = (
    "We couldn't create an account with that email. If you already have one, "
    "sign in or reset your password."
)


class SignUpNotPossible(Conflict):
    """A sign-up that cannot create an account, whatever the reason (409).

    **A type of its own that does not name registration.** The one refusal for an address that
    already has an account, whether the pre-existing row was seen first or the unique index
    refused the insert, so the answer is the same and so is the work behind it. Title and
    `detail` are the same sentence. `EmailAlreadyRegistered` stays for the places where the
    caller has already proved something (an invitation, the Google complete step).
    """

    problem_type = "sign-up-not-possible"
    title = SIGN_UP_NOT_POSSIBLE


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


class PlanPriceMismatch(ProblemError):
    """Checkout refused: the latest price check CONFIRMED the Price is not the plan.

    **503**, like `BillingNotConfigured`: nothing is wrong with the request, and
    the deployment's Stripe Price (amount, currency, interval or archived state)
    has to be fixed by whoever runs it before anyone can subscribe. The `detail`
    is a plain sentence for the person at the screen; it names no amount, no
    Price id and no setting, and promises nothing about who has been told. A
    failed or missing price read never raises this: only a confirmed mismatch.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "plan-price-mismatch"
    title = "The plan's price is not set up correctly"


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


class SubscriptionRequired(ProblemError):
    """What was asked for is for paying agencies, and this agency is not one — 2026-10-04.

    **402**, as `ScanLimitReached` is and for the same reason: the thing asked for costs
    money the caller has not paid for. A type of its own, and no `code` or counts: there is
    no allowance here to say how much of, only a plan to have. Raised by
    `POST /clients/{clientId}/competitors/detect` (launch checklist 3.61, through
    `services/scan_allowance.assert_may_run_detection`). The sentence is the caller's, one
    for every case, and names no status: an agency that never subscribed and one whose
    subscription lapsed read the same words.
    """

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    problem_type = "subscription-required"
    title = "Subscription required"


class ServiceUnavailable(ProblemError):
    """A dependency this route cannot safely do without is down (503)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "service-unavailable"
    title = "Service unavailable"


class PlatformCapReached(ProblemError):
    """AVP as a whole has started its daily limit of scans — D2, 2026-10-10.

    **503**, not 429: no caller did anything wrong and no per-caller limit tripped; the
    platform's own spend limit (`services/spend_caps`) is spent for the UTC day. Carries
    `Retry-After`, the whole seconds until the next UTC midnight, when the count starts over.
    The sentence names no number and no one. `cap` is the one extra (`scans` or
    `free_scans`) so a client can tell the two apart; the platform's limit is never sent, it is
    only in the log line.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    problem_type = "platform-cap-reached"
    title = "Daily scan limit reached"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        title: str | None = None,
        **extra: Any,
    ) -> None:
        """`title` is the scan one unless the caller names another: F3 added the paid AI calls
        outside a scan (`prompt_runs`, `fix_runs`, `classifications`), which are not scans, and
        say "Daily limit reached". `cap` (in `extra`) names the kind either way."""
        super().__init__(detail, headers=headers, **extra)
        if title is not None:
            self.title = title


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


def _validation_field(err: Any) -> str:
    """Where one validation error is, as a dotted path from the body, cut to `MAX_FIELD_CHARS`."""
    loc = err["loc"]
    return (".".join(str(part) for part in loc[1:]) or str(loc[0]))[:MAX_FIELD_CHARS]


def _validation_message(err: Any) -> str:
    """The message of one validation error: pydantic's own, except for an unknown field."""
    if err["type"] == "extra_forbidden":
        return FIELD_NOT_ACCEPTED
    return str(err["msg"])


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
        found = exc.errors()
        # `errorsTruncated` (A8b) says the list was cut at `MAX_VALIDATION_ERRORS`: present and
        # true when it was, absent when every error is there, so a client that finds it absent
        # knows it has the whole list. Exactly the cap is not a cut.
        truncated: dict[str, Any] = (
            {"errorsTruncated": True} if len(found) > MAX_VALIDATION_ERRORS else {}
        )
        problem = ValidationProblem(
            detail="One or more fields were invalid.",
            errors=[
                {
                    "field": _validation_field(err),
                    "message": _validation_message(err),
                    "type": err["type"],
                }
                for err in found[:MAX_VALIDATION_ERRORS]
            ],
            **truncated,
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
    # Read from the classes, so a 408 or 413 raised out of `body_limit.py`'s `receive` is the
    # document of `RequestTimeout` or `PayloadTooLarge`.
    408: (RequestTimeout.problem_type, RequestTimeout.title),
    413: (PayloadTooLarge.problem_type, PayloadTooLarge.title),
    422: ("validation-failed", "Request validation failed"),
    429: ("rate-limited", "Too many requests"),
}


def _slug_for_status(code: int) -> str:
    return _STATUS_SLUGS.get(code, ("internal-error", ""))[0]


def _title_for_status(code: int) -> str:
    return _STATUS_SLUGS.get(code, ("", "Internal server error"))[1]
