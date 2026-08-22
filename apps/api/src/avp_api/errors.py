"""RFC 9457 problem+json error handling.

api-contracts.md fixes the convention:
    { type, title, status, detail, instance }
served as `application/problem+json`.

Every error the API emits goes through here, so the contract holds by
construction rather than by each route remembering to format its own errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        self.detail = detail or self.title
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


class PermissionDenied(ProblemError):
    status_code = status.HTTP_403_FORBIDDEN
    problem_type = "permission-denied"
    title = "You do not have permission to perform this action"


class NotFound(ProblemError):
    status_code = status.HTTP_404_NOT_FOUND
    problem_type = "not-found"
    title = "Resource not found"


class Conflict(ProblemError):
    status_code = status.HTTP_409_CONFLICT
    problem_type = "conflict"
    title = "Conflicting request"


class EmailAlreadyRegistered(Conflict):
    problem_type = "email-already-registered"
    title = "That email address is already registered"


class SeatLimitReached(ProblemError):
    status_code = status.HTTP_409_CONFLICT
    problem_type = "seat-limit-reached"
    title = "Seat limit reached"


class RateLimited(ProblemError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    problem_type = "rate-limited"
    title = "Too many requests"


def _problem_response(request: Request, exc: ProblemError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(instance=request.url.path),
        media_type=PROBLEM_CONTENT_TYPE,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire every error path to the problem+json representation."""

    @app.exception_handler(ProblemError)
    async def _handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem_response(request, exc)

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
