"""A request body has a size limit and a deadline — A8b, 2026-10-11.

Until now the API set neither. No middleware, no check of `Content-Length`, and uvicorn has no flag
for a size or a time (`docker-entrypoint.sh` runs one worker). A route that takes a JSON body has
FastAPI read the whole of it, parse it, and only then look at the session, the role or any rate
limit, so a stranger could make the one worker read and parse a body of any size on any route, the
unauthenticated ones too, and hold it as long as the connection stayed open. A8 bounded the errors
such a body can produce; this bounds the body, in size and in time.

SIZE
----
A request whose body is larger than its limit is refused with `413 /problems/payload-too-large`
(title "Request too large", one plain sentence for `detail`), and nothing else of it is read. How
the limit is found out depends on what the request says about itself:

- **It declares a length over the limit** (`Content-Length`): refused at once. Nothing is read, and
  the route is never reached.
- **It declares none (a chunked body) or declares less than it sends:** the bytes are counted as
  `receive` delivers them, and the moment the count passes the limit that `receive` raises
  `RequestBodyTooLarge` instead of handing the app the chunk that did it. See below for where that
  goes. The app is never handed more than the limit.
- **The length is not a length** (not digits, several different values, over 19 digits): `400
  /problems/bad-request`, with `Connection: close` (RFC 9112 6.3: a request whose framing cannot be
  trusted is not read on from). uvicorn rejects such a request itself, with a plain-text 400, before
  the app is called, so this answers only where a server passes one on; Starlette, which never
  reads the header, would have taken the request as it came. Equal copies of one valid length are
  one length.

A body of exactly the limit passes. Every method is treated alike: a request that carries no body
counts none, and one that carries a body where this API takes none is refused for it like any
other. A preflight never gets here (CORS answers it), and a `websocket` or `lifespan` scope is
passed on. The 413 does NOT close the connection: uvicorn drops the unread rest of a body once the
response is complete, and a close can erase the answer for a client that is still sending.

TIME
----
A body is also given a deadline (`REQUEST_BODY_TIMEOUT_SECONDS`, 30 by default), counted from the
first `receive` the app makes for it. A body not whole by then is refused with `408
/problems/request-timeout` ("This request took too long to arrive."), and `Connection: close` ends
the connection (RFC 9110 15.5.9). Without it a client that sends a little and stops holds what it
sent for as long as it likes: the size limit bounds what each upload holds, and this bounds for how
long.

The wait is CUT OFF at the deadline: each `receive` is awaited with the time that is left
(`asyncio.timeout`), so a client that goes silent in the middle of a chunk is answered at the
deadline and not whenever it next sends. Checking the clock only between chunks would never answer
one that never sends another. Once the body is whole (`more_body` false) the clock stops: what the
app asks for after that is a wait to hear the client has gone, and it is not timed. A request with
no body is whole on its first `receive`. A slow app does not use up the time: the clock starts at
the first `receive`.

The 408 goes the way the 413 does, below, and carries the same security and CORS headers.

WHERE A MID-STREAM REFUSAL ANSWERS
----------------------------------
FastAPI reads a JSON body in full, before the handler runs, inside a `try` that turns any
exception it does not know into a `400` ("There was an error parsing the body"). It re-raises a
Starlette `HTTPException` as it is (its own comment: "If a middleware raises an HTTPException, it
should be raised again"), so `RequestBodyTooLarge` and `RequestBodyTimeout` are ones, and the
exception handlers then render them as the same 413 or 408 the middleware itself would send
(`errors.py`). Nothing has been sent when either is raised, since the body is read first, so the
answer is a whole, ordinary response and carries the security and CORS headers like any other. A
raw-body route (the Stripe webhook, `await request.body()`) takes the same path. If the exception
reaches this middleware unhandled (an app that is not FastAPI) and no response has started, this
answers it itself; if one has started, it can only be re-raised.

THE LIMITS
----------
Three sizes, all settings (`config.py` explains the values and gives each its own range, outside
which the API does not start): every route's, the one for `PUT /clients/{clientId}/other-sites`,
whose schema allows about 200 KB, and the one for the Stripe webhook, which reads a raw body. A
route is matched here, by method and path template, before routing; a path that matches no override
has the default, so anything unexpected gets the smaller limit. `tests/test_request_body_limit.py`
derives the largest body each route's schema allows and fails when a limit is below it, and when an
override names no route. The deadline is one setting for every route.

**Where the three disagree, the API raises the lower, it does not refuse to start.** A setting
that is in its own range but under what its route needs would be an outage that a refused start
turns into a locked deploy (a hotfix included) for an ordinary mistake: the default raised to its
top while the other-sites limit was left where it was. So `limits_from_settings` applies, for the
webhook, `max(webhook, default)`, and for other-sites, `max(other_sites, default, the largest body
its schema allows)`, which is computed from the schema's bounds (`other_sites_bounds`) and so holds
when they are raised and the setting is not. Each raised value is logged once when the app is built
(`config.body_limit_raised`: the setting, what was configured, what is applied, and `below_default`
or `below_schema` for the floor that applied). `configured_limits` is the table as the operator set
it, which is what the test of the shipped defaults reads: CI fails when a schema outgrows them,
though the running API would have raised the limit and gone on.

WHERE IT SITS
-------------
`main.py` adds it after the origin check and before CORS, so it is INSIDE the security headers and
CORS and OUTSIDE the origin check. Inside the first two, so that its answers carry the security
headers and the CORS headers an allowed origin gets (a page on that origin can read the refusal).
Outside the origin check, so that it is the first layer that could read a body or, were one ever
added that does, the one that bounds what it reads. It reads nothing itself: it only counts, and
times.

A PURE ASGI MIDDLEWARE
----------------------
Not `BaseHTTPMiddleware`, which wraps the body and the exceptions of everything inside it. It
touches `receive` to count `http.request` messages and to time them, and `send` only to know
whether a response has started (and to add `Connection: close` to a 408). Every message goes
through unchanged. The refusals are RETURNED, not raised: the exception handlers sit inside the
stack, so a `ProblemError` raised out here would be nobody's to catch. They are rendered through
`errors._problem_response`, the serializer those handlers use.

THE LOG LINES
-------------
One warning per refused request, with the method and `route` (the path TEMPLATE the request would
reach, `/api/v1/clients/{clientId}/other-sites`, not the path as sent, and null for a path no route
has). `security.request_too_large` adds `limit` and `declared_length` when the request declared
one. `security.request_body_timeout` adds `deadline_seconds`, `bytes_received` (an integer, what
had arrived when the deadline passed) and `declared_length` when there was one. No body, no header,
no address, no id. The 400 for a length that is not one is not logged: the header is the sender's
to write, and uvicorn answers first.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import structlog
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .errors import (
    MALFORMED_CONTENT_LENGTH_DETAIL,
    BadRequest,
    PayloadTooLarge,
    ProblemError,
    RequestBodyTimeout,
    RequestBodyTooLarge,
    RequestTimeout,
    _problem_response,
)
from .other_sites_bounds import largest_plain_body_bytes

logger = structlog.get_logger(__name__)

# A length of more than this many digits is not one: it does not fit 63 bits, and a header the
# sender chose must not make the integer parser (or a log line) as long as the sender likes.
MAX_LENGTH_DIGITS = 19


class MalformedContentLengthError(ValueError):
    """The `Content-Length` header is not a body length."""


def declared_length(headers: Headers) -> int | None:
    """The body length the request declares, `None` if it declares none.

    Every `Content-Length` header is read, and they must agree: several different values are the
    framing ambiguity request smuggling lives on, so they are refused like a bad value. Digits
    only (no sign, no space inside, no other script's digits), 1 to `MAX_LENGTH_DIGITS` of them.
    Space or tab around a value is the header's own padding and is dropped.
    """
    values = {value.strip(" \t") for value in headers.getlist("content-length")}
    if not values:
        return None
    if len(values) > 1:
        raise MalformedContentLengthError("Content-Length is given more than once, differently")
    (value,) = values
    if not (value.isascii() and value.isdigit() and len(value) <= MAX_LENGTH_DIGITS):
        raise MalformedContentLengthError("Content-Length is not a length")
    return int(value)


@dataclass(frozen=True)
class RouteLimit:
    """A route's own limit: its method, its path TEMPLATE (`{name}` is one parameter), in bytes."""

    method: str
    path: str
    max_bytes: int


def _template_pattern(template: str) -> re.Pattern[str]:
    """`/a/{id}/b` as a regex: the literal parts as they are, each `{name}` one non-empty segment.

    The segment pattern is Starlette's own for a plain parameter (`[^/]+`), so a template matches
    the paths its route does, and no more.
    """
    parts = re.split(r"\{[^{}/]+\}", template)
    return re.compile("[^/]+".join(re.escape(part) for part in parts))


class BodyLimits:
    """The limit that applies to a request: its route's own if it has one, else the default."""

    def __init__(self, default: int, routes: Iterable[RouteLimit] = ()) -> None:
        self.default = default
        self.routes = tuple(routes)
        self._patterns = tuple(
            (route.method.upper(), _template_pattern(route.path), route.max_bytes)
            for route in self.routes
        )

    def for_request(self, method: str, path: str) -> int:
        """The most body bytes `method path` may carry. `path` is the concrete path, as routed."""
        method = method.upper()
        for route_method, pattern, max_bytes in self._patterns:
            if method == route_method and pattern.fullmatch(path):
                return max_bytes
        return self.default


OTHER_SITES_SETTING = "MAX_OTHER_SITES_BODY_BYTES"
WEBHOOK_SETTING = "MAX_STRIPE_WEBHOOK_BODY_BYTES"


@dataclass(frozen=True)
class RaisedLimit:
    """A configured limit that the API applies at a higher value, and why."""

    setting: str
    configured: int
    effective: int
    # The floor that set `effective`: the default limit, or the largest body the schema allows.
    reason: Literal["below_default", "below_schema"]


def _table(settings: Settings, *, default: int, other_sites: int, webhook: int) -> BodyLimits:
    base = settings.api_base_path
    return BodyLimits(
        default=default,
        routes=(
            RouteLimit("PUT", f"{base}/clients/{{clientId}}/other-sites", other_sites),
            RouteLimit("POST", f"{base}/billing/webhook", webhook),
        ),
    )


def configured_limits(settings: Settings) -> BodyLimits:
    """The limits exactly as the settings give them, before any is raised."""
    return _table(
        settings,
        default=settings.max_request_body_bytes,
        other_sites=settings.max_other_sites_body_bytes,
        webhook=settings.max_stripe_webhook_body_bytes,
    )


def raised_limits(settings: Settings) -> list[RaisedLimit]:
    """The limits that are below their floor, each with the value it is raised to.

    The webhook's is not below the default. The other-sites one is not below the default or below
    the largest body its schema allows (`other_sites_bounds.largest_plain_body_bytes`, read now, so
    a schema whose bounds have been raised raises the limit with it). The default has no floor:
    it has a range of its own, and `Settings` refuses the start outside it.
    """
    default = settings.max_request_body_bytes
    raised: list[RaisedLimit] = []
    webhook = settings.max_stripe_webhook_body_bytes
    if webhook < default:
        raised.append(RaisedLimit(WEBHOOK_SETTING, webhook, default, "below_default"))
    schema = largest_plain_body_bytes()
    other_sites = settings.max_other_sites_body_bytes
    if other_sites < max(default, schema):
        reason: Literal["below_default", "below_schema"] = (
            "below_default" if default >= schema else "below_schema"
        )
        raised.append(RaisedLimit(OTHER_SITES_SETTING, other_sites, max(default, schema), reason))
    return raised


def limits_from_settings(settings: Settings) -> BodyLimits:
    """The API's limits: the default, and the two routes that need their own (see `config.py`).

    A limit under its floor is applied at the floor (`raised_limits`); nothing here refuses.
    """
    applied = {raised.setting: raised.effective for raised in raised_limits(settings)}
    return _table(
        settings,
        default=settings.max_request_body_bytes,
        other_sites=applied.get(OTHER_SITES_SETTING, settings.max_other_sites_body_bytes),
        webhook=applied.get(WEBHOOK_SETTING, settings.max_stripe_webhook_body_bytes),
    )


def log_raised_limits(settings: Settings) -> None:
    """`config.body_limit_raised`, once for each limit the API will apply above what was set.

    Called when the app is built, so a deploy says so in its log once, and a request says nothing.
    Numbers and the setting's name only.
    """
    for raised in raised_limits(settings):
        logger.warning(
            "config.body_limit_raised",
            setting=raised.setting,
            configured=raised.configured,
            effective=raised.effective,
            reason=raised.reason,
        )


def _route_template(scope: Scope) -> str | None:
    """The path template of the route this request would reach, or None if no route matches.

    Looked up only when a request is refused, from the app the request came in through, so the
    log names the route and not the path as sent (which carries an id, or whatever was typed).
    """
    for route in getattr(scope.get("app"), "routes", ()):
        match, _ = route.matches(scope)
        if match is not Match.NONE:
            path: str | None = getattr(route, "path", None)
            return path
    return None


# `Connection: close`: as a ProblemError header (a fresh dict each time, as the response keeps it)
# and as the raw pair ASGI carries, for a response the exception handlers have already rendered.
def _close() -> dict[str, str]:
    return {"Connection": "close"}


_CLOSE_RAW: tuple[tuple[bytes, bytes], ...] = ((b"connection", b"close"),)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limits: BodyLimits, body_timeout_seconds: float) -> None:
        self.app = app
        self.limits = limits
        self.body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limits.for_request(scope["method"], scope["path"])
        try:
            declared = declared_length(Headers(scope=scope))
        except MalformedContentLengthError:
            problem = BadRequest(MALFORMED_CONTENT_LENGTH_DETAIL, headers=_close())
            await self._answer(scope, receive, send, problem)
            return
        if declared is not None and declared > limit:
            self._log_too_large(scope, limit=limit, declared=declared)
            await self._answer(scope, receive, send, PayloadTooLarge())
            return

        seen = 0
        # What the body was refused with, once it has been: the exception raised out of `receive`.
        refusal: RequestBodyTooLarge | RequestBodyTimeout | None = None
        response_started = False
        body_complete = False
        deadline: float | None = None

        async def counting_receive() -> Message:
            nonlocal seen, refusal, body_complete, deadline
            if refusal is not None:
                # Whatever asks again after the refusal is told the body is gone, not made to
                # catch the same exception twice or to log the same refusal twice.
                return {"type": "http.disconnect"}
            if body_complete:
                # What is asked for now is a wait to hear that the client has gone. There is no
                # body left to count and no time to keep.
                return await receive()
            if deadline is None:
                deadline = asyncio.get_running_loop().time() + self.body_timeout_seconds
            message = await self._next(receive, until=deadline)
            if message is None:
                refusal = RequestBodyTimeout()
                self._log_timeout(scope, bytes_received=seen, declared=declared)
                raise refusal
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    refusal = RequestBodyTooLarge()
                    self._log_too_large(scope, limit=limit, declared=declared)
                    raise refusal
                if not message.get("more_body", False):
                    body_complete = True
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                if isinstance(refusal, RequestBodyTimeout):
                    # The exception handlers render the 408 and know nothing of the connection.
                    message = {**message, "headers": [*message.get("headers", ()), *_CLOSE_RAW]}
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except (RequestBodyTooLarge, RequestBodyTimeout) as refused:
            # The app's own handlers answer these in FastAPI (see the module docstring). Reaching
            # here means nobody did: answer it, unless a response is already on its way.
            if response_started:
                raise
            if isinstance(refused, RequestBodyTimeout):
                await self._answer(scope, receive, send, RequestTimeout(headers=_close()))
            else:
                await self._answer(scope, receive, send, PayloadTooLarge())

    @staticmethod
    async def _next(receive: Receive, *, until: float) -> Message | None:
        """The next message, or None if `until` (a time on the event loop's clock) comes first.

        The wait is cut off at `until`, whatever `receive` is doing: a client that has gone quiet
        is answered at the deadline, not when it next speaks.
        """
        remaining = until - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        waiting = asyncio.timeout(remaining)
        try:
            async with waiting:
                return await receive()
        except TimeoutError:
            if waiting.expired():
                return None
            raise

    @staticmethod
    async def _answer(scope: Scope, receive: Receive, send: Send, problem: ProblemError) -> None:
        await _problem_response(Request(scope), problem)(scope, receive, send)

    @staticmethod
    def _log_too_large(scope: Scope, *, limit: int, declared: int | None) -> None:
        fields: dict[str, str | int | None] = {
            "method": scope["method"].upper(),
            "route": _route_template(scope),
            "limit": limit,
        }
        if declared is not None:
            fields["declared_length"] = declared
        logger.warning("security.request_too_large", **fields)

    def _log_timeout(self, scope: Scope, *, bytes_received: int, declared: int | None) -> None:
        fields: dict[str, str | int | float | None] = {
            "method": scope["method"].upper(),
            "route": _route_template(scope),
            "deadline_seconds": self.body_timeout_seconds,
            "bytes_received": bytes_received,
        }
        if declared is not None:
            fields["declared_length"] = declared
        logger.warning("security.request_body_timeout", **fields)
