"""Security headers on every response the API sends — launch checklist 3.1, 2026-10-03.

Six headers go on every HTTP response, unless the response already set that
header itself (a route that sends its own value keeps it):

- `X-Content-Type-Options: nosniff`. A browser never guesses a type for a JSON,
  problem or PDF body.
- `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`.
  Nothing this API serves is meant to be framed. The older header is still the
  only one some old browsers read; the standard one wins where both are read.
  The policy carries that one directive and nothing else, so it cannot stop a
  script, a style or an image from loading.
- `Referrer-Policy: no-referrer`. An API response is data, never a page, and
  where it redirects (Google's sign-in) the next request has no business with a
  Referer from here.
- `Strict-Transport-Security: max-age=63072000; includeSubDomains`. Two years,
  subdomains included, and no `preload`: leaving that list is slow and hard.
  Browsers ignore the header over plain HTTP, so local development is untouched.
  The same value the web app sends. Sent in every environment; it is not made
  conditional on one.
- `Permissions-Policy`: switches off what nothing here uses (camera, microphone,
  location, payment, USB, the Topics API). The SAME value the web app sends.

Both of those values are held equal to the web app's (`apps/web/next.config.mjs`)
by `tests/test_security_headers.py`.

The web app sends its own set on every page but `/api/v1`, which it proxies to
this API and leaves out of its rule: on `/api/v1` a browser gets this set, once.

WHERE IT SITS
-------------
OUTSIDE the CORS middleware, the outermost layer there is (`main.py`). A CORS
preflight is answered by the CORS middleware itself, without calling the app it
wraps, so a layer inside CORS never sees one, and the preflight is a response
that must carry these headers too. The origin check's refusals sit inside CORS
and pass back out through this layer as well. Putting this outside changes
nothing CORS does: it reads no request and answers none, it only appends to the
head of a response on its way out.

A PURE ASGI MIDDLEWARE
----------------------
Not `BaseHTTPMiddleware`, which wraps the body and the exceptions of everything
inside it. This touches `http.response.start` and nothing else: every body
message goes through untouched, so a streamed response still streams, and a
`websocket` or `lifespan` scope is passed straight on.

WHAT IT DOES NOT REACH
----------------------
An exception nothing handles becomes a 500 built by Starlette's
`ServerErrorMiddleware`. That layer sits OUTSIDE every user middleware, this one
included: the exception passes through here as an exception, not as a response,
and the 500 it ends in is sent without these headers (and without CORS's). The
app's own error handlers (`errors.py`: problems, validation, Redis, HTTP errors)
run inside, so every error the API chooses to send does carry them.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The two values the web app sends in the same words (`apps/web/next.config.mjs`
# has them as constants of these names): the test that holds them equal reads
# these constants and that file, so neither side can drift from the other.
STRICT_TRANSPORT_SECURITY = "max-age=63072000; includeSubDomains"
PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()"
)

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("Referrer-Policy", "no-referrer"),
    ("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY),
    ("Permissions-Policy", PERMISSIONS_POLICY),
)

# As ASGI carries them: lower-case names, bytes.
_RAW_HEADERS: tuple[tuple[bytes, bytes], ...] = tuple(
    (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in SECURITY_HEADERS
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                sent = list(message.get("headers") or ())
                # Compared lower-cased: ASGI asks for lower-case names, but an
                # app that sends `X-Frame-Options` has still set that header.
                already = {name.lower() for name, _ in sent}
                message = {
                    **message,
                    "headers": sent + [pair for pair in _RAW_HEADERS if pair[0] not in already],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)
