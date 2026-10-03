"""A write must come from the web app — 2026-10-01.

CORS decides which pages may READ an answer from this API. It does not stop a
page on another site from SENDING a write: the browser attaches the visitor's
cookies to any request aimed at this host, and by the time the page is told it
may not look at the reply, the write has happened.

So a POST, PUT, PATCH or DELETE is refused — `403 /problems/origin-not-allowed` —
in two cases:

- its `Origin` header is PRESENT and is not exactly one of `CORS_ALLOW_ORIGINS`.
  `null` and an empty value are present and unlisted, so they fail. This holds
  with or without a session: a sign-in, sign-up, password reset, invitation or
  Google completion sent by another site is a write too (founder decision,
  2026-10-01; until then only a request carrying the session cookie was checked);
- it has NO `Origin` header and carries the session cookie. A browser sends
  `Origin` on every cross-origin write, and on a same-origin one other than GET
  and HEAD, so a write that carries a session and names no origin is not the web
  app's.

WHAT IS NOT CHECKED
-------------------
- A write with no `Origin` and no session cookie: Stripe, curl, a server-side
  caller. Nothing about it is a browser's, and there is no session to ride on.
- `POST` to the Stripe webhook, whatever it carries. It is verified by its signature.
  Only that method and that exact path: a PUT, PATCH or DELETE to the same path is
  checked like any other write.
- GET, HEAD and OPTIONS. A preflight is the CORS middleware's to answer.
- A `websocket` scope. There is no websocket route; `tests/test_origin_check.py`
  fails if one is ever registered, so this cannot become a way around the check.

The cookie is looked for by NAME, with any value: the session is not looked up,
so a refused request costs no Redis or Postgres call, and a stale or forged
cookie is refused as readily as a live one. The name is `Settings.session_cookie_key`
(`__Host-` prefixed when Secure).
It is read with Starlette's own cookie parser, the one behind `request.cookies`
and so behind `current_principal` — but out of every `Cookie` header rather than
only the first, which can only refuse more, never less.

A PURE ASGI MIDDLEWARE, AND IT RETURNS THE RESPONSE
---------------------------------------------------
Not `BaseHTTPMiddleware`, which wraps the body and the exceptions of everything
inside it for no use here. And the refusal is RETURNED rather than raised: the
app's exception handlers sit inside the middleware stack, so a `ProblemError`
raised out here would be nobody's to catch and would become a bare 500. It is
rendered through `errors._problem_response`, the serializer those handlers use,
so its members, its `instance` and its content type are theirs by construction.

Registered in `main.py` BEFORE the CORS middleware, which keeps CORS outside it. (One
layer sits further out than CORS: the security headers, which read and answer nothing.)
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import quote, urlsplit

import structlog
from starlette.datastructures import Headers
from starlette.requests import Request, cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from .errors import OriginNotAllowed, _problem_response
from .logs import redact_path

logger = structlog.get_logger(__name__)

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

REFUSAL_DETAIL = "Requests that change data must come from the AVP web app."


def _normalise(origin: str) -> str:
    """A configured origin as a browser writes it: no stray space, no trailing slash."""
    return origin.strip().rstrip("/")


def _can_be_listed(origin: str) -> bool:
    """Blank and `null` are not origins, and a list that named one would admit a
    request that is not the web app's: a blank entry equals an empty `Origin`
    header, and `null` is what a browser sends for a sandboxed frame, a `file:`
    page or a request its referrer policy stripped."""
    return origin != "" and origin.lower() != "null"


_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


# A host whose last label is a number is an IPv4 address to a browser, however
# it is spelled (`2130706433`, `0x7f000001`, `127.1`).
_NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")


def _is_dns_name(host: str) -> bool:
    return len(host) <= 253 and all(_DNS_LABEL.fullmatch(label) for label in host.split("."))


def origin_log_fields(origin: str | None) -> dict[str, str]:
    """What of a refused `Origin` header may be logged, and nothing more.

    The header is the requester's to write, so it is never logged as sent. What
    comes out is one of:

    - `{}` for no header (the caller logs `origin_present` on its own);
    - `{"origin_host": <name>}` for an http(s) origin whose host is a DNS name:
      lower-cased, at most 253 characters, labels of 1-63 of a-z, 0-9 and `-`
      that neither start nor end with `-`. A name is not a secret, a token or an
      email. Only a bare origin (scheme, host, optional port) is read: userinfo,
      a backslash, a path, a query or a fragment make it `unparsed`. Never the
      scheme or port. A name that merely contains an address
      (`10-0-0-1.sslip.io`) is still a name and is logged;
    - `{"origin_kind": "ip"}` for an IP-literal host, in any spelling a browser
      reads as one: dotted quad, IPv6, or a host whose last label is a number
      (`2130706433`, `0x7f000001`, `127.1`). The address is never logged: it can
      identify a person or a private network;
    - `{"origin_kind": "opaque"}` for the literal `null`;
    - `{"origin_kind": "unparsed"}` for anything else. No value.
    """
    if origin is None:
        return {}
    if origin == "null":
        return {"origin_kind": "opaque"}
    unparsed = {"origin_kind": "unparsed"}
    # Anything that is not printable ASCII-without-spaces is not an origin, and
    # urlsplit would quietly drop a tab or newline from inside it.
    if not origin.isascii() or not origin.isprintable() or " " in origin:
        return unparsed
    if any(c in origin for c in "@\\?#"):
        return unparsed
    try:
        parts = urlsplit(origin)
        host = parts.hostname
    except ValueError:
        return unparsed
    if parts.scheme.lower() not in ("http", "https") or not host or parts.path:
        return unparsed
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return {"origin_kind": "ip"}
    if _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1]):
        return {"origin_kind": "ip"}
    if "[" in parts.netloc or not _is_dns_name(host):
        return unparsed
    return {"origin_host": host}


class OriginCheckMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str],
        session_cookie_name: str,
        exempt: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.app = app
        # An empty list, or one with nothing usable in it, refuses every write
        # that names an origin.
        self.allowed_origins = frozenset(
            origin for origin in map(_normalise, allowed_origins) if _can_be_listed(origin)
        )
        self.session_cookie_name = session_cookie_name
        # (METHOD, path) pairs, each matched exactly: the webhook is exempt for
        # POST alone.
        self.exempt = frozenset((method.upper(), path) for method, path in exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._is_a_write(scope):
            origin = Headers(scope=scope).get("origin")
            if self._is_refused(scope, origin):
                await self._refuse(scope, receive, send, origin=origin)
                return
        await self.app(scope, receive, send)

    def _is_a_write(self, scope: Scope) -> bool:
        # Upper-cased: HTTP methods are case-sensitive and Starlette's router
        # matches them as written (a lowercase `post` gets a 405), but uvicorn's
        # h11 parser passes one through, and the check must not hinge on that.
        method = scope["method"].upper()
        return method in STATE_CHANGING_METHODS and (method, scope["path"]) not in self.exempt

    def _is_refused(self, scope: Scope, origin: str | None) -> bool:
        if origin is not None:
            # An origin is named, so it must be one of ours — session or not.
            return origin not in self.allowed_origins
        # None is named. A browser would have named one, so what is left is a
        # server-side caller, which is fine, unless it carries the session cookie:
        # then it is a write pretending to be the web app's.
        return self._carries_the_session_cookie(scope)

    def _carries_the_session_cookie(self, scope: Scope) -> bool:
        return any(
            self.session_cookie_name in cookie_parser(line)
            for line in Headers(scope=scope).getlist("cookie")
        )

    async def _refuse(
        self, scope: Scope, receive: Receive, send: Send, *, origin: str | None
    ) -> None:
        # What happened — not the cookie, and not the Origin value as sent: whether
        # there was an Origin, and `origin_log_fields`' bounded reading of it (a
        # DNS host name, or only its kind). The path is percent-encoded the way uvicorn's
        # access log encodes it (`scope["path"]` is DECODED, so an escape, a NUL
        # or a newline sent as `%1B`, `%00` or `%0A` would otherwise reach the log
        # raw) and then goes through that log's own redaction.
        logger.warning(
            "security.origin_refused",
            method=scope["method"].upper(),
            path=redact_path(quote(scope["path"])),
            origin_present=origin is not None,
            **origin_log_fields(origin),
        )
        response = _problem_response(
            Request(scope), OriginNotAllowed(detail=REFUSAL_DETAIL)
        )
        await response(scope, receive, send)
