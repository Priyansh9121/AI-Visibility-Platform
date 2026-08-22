"""CORS configuration — the browser's ability to reach this API at all.

Added in Epic 3.6, after the competitor override endpoint turned out to have
been unreachable from a browser since Epic 3: `allow_methods` listed GET, POST,
PATCH, DELETE and OPTIONS, and the only PUT in the API is that override. The
preflight had never been issued because nothing in the frontend called it until
Epic 3.6 built the UI, and the endpoint's own tests all passed throughout.

They passed because the suite drives the app through httpx's `ASGITransport`,
which calls the application directly and **never runs a preflight**. Every
CORS-related failure is invisible to it by construction — the tests and the
browser were exercising two different things, and only one of them was checked.

The guard below is therefore on the configuration rather than on a response:
asserting against a request this suite cannot issue would be asserting against
nothing.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware

from avp_api.config import Settings
from avp_api.main import create_app


def _cors_options(settings: Settings) -> dict:
    """The kwargs the CORS middleware was actually constructed with."""
    app = create_app(settings)
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return dict(middleware.kwargs)
    raise AssertionError("no CORS middleware is installed")


def test_the_allowed_methods_are_exactly_the_registered_ones(settings: Settings) -> None:
    """Exact equality, both directions.

    Epic 3.6 asserted only that registered methods are a SUBSET of allowed
    ones, which catches a route a browser cannot reach. Epic 3.7's config audit
    found the other direction: PATCH and DELETE were allowed while no route
    used either, left over from a hand-written list. `_registered_methods` now
    derives the value, so equality is the honest assertion — anything else
    would leave room for a list to be wrong in a way this test tolerates.

    OPTIONS is expected on the allowed side and never on the registered side:
    the CORS middleware answers preflight itself, so no route declares it.
    """
    app = create_app(settings)
    allowed = {m.upper() for m in _cors_options(settings)["allow_methods"]}

    registered: set[str] = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            if method != "HEAD":  # Starlette adds HEAD alongside every GET
                registered.add(method)

    assert registered, "no routes found — the sweep would pass vacuously"

    blocked = registered - allowed
    assert not blocked, (
        f"{sorted(blocked)} is registered on a route but blocked by CORS — "
        "a browser cannot call it, and no test in this suite would notice"
    )
    surplus = allowed - registered - {"OPTIONS"}
    assert not surplus, (
        f"{sorted(surplus)} is allowed by CORS but no route uses it — "
        "an allow-list ahead of the routes is the same drift that hid the "
        "missing PUT, pointing the other way"
    )


def test_put_specifically_is_allowed(settings: Settings) -> None:
    """Named explicitly because it is the one that was missing.

    The sweep above would catch it, but a named test says what the regression
    was, so a future reader does not have to reconstruct it from the sweep.
    """
    assert "PUT" in {m.upper() for m in _cors_options(settings)["allow_methods"]}


def test_preflight_is_advertised_even_though_no_route_declares_it(
    settings: Settings,
) -> None:
    """OPTIONS is the one method that must be allowed without being registered.

    Deriving the list from the routes would drop it — the middleware handles
    preflight before routing, so no route ever declares OPTIONS — and a browser
    that does not see it advertised refuses the request before sending it.
    Asserted because it is the one hand-added member of a derived list, and
    therefore the one that could be dropped by a tidy-up.
    """
    assert "OPTIONS" in {m.upper() for m in _cors_options(settings)["allow_methods"]}


def test_credentials_are_allowed_and_the_origin_is_not_a_wildcard(
    settings: Settings,
) -> None:
    """Session cookies are httpOnly and cross-origin in development.

    A browser refuses to send them when the origin is `*` and credentials are
    allowed, so a wildcard here would break every authenticated call while
    looking maximally permissive.
    """
    options = _cors_options(settings)
    assert options["allow_credentials"] is True
    assert "*" not in options["allow_origins"]
