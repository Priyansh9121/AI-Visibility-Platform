"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .db import dispose_engine
from .errors import register_error_handlers
from .logs import configure_logging
from .origin_check import OriginCheckMiddleware
from .redis_client import close_redis
from .routers import (
    action_items,
    agencies,
    alerts,
    audits,
    auth,
    billing,
    clients,
    competitors,
    dashboard,
    health,
    prompt_runs,
    report,
    scans,
    scores,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("api starting environment=%s", settings.environment)
    yield
    # Return pooled connections deliberately, so a redeploy does not leave
    # Postgres holding sockets until they time out.
    await dispose_engine()
    await close_redis()


def _registered_methods(app: FastAPI) -> list[str]:
    """The HTTP methods CORS must allow, derived from the routes themselves.

    This list used to be written by hand, and that is how PUT went missing for
    five epics: it was correct when the API had no PUT, adding one later
    reminded nobody to revisit it, and the only PUT in the system was
    unreachable from a browser the whole time. Epic 3.6 fixed the value and
    added a test that the registered methods are a SUBSET of the allowed ones.

    Epic 3.7 removes the hand-maintained list instead. The subset test could
    only ever catch drift in one direction, and an audit found the other:
    PATCH and DELETE were allowed while no route used either. Deriving the
    list makes both directions impossible rather than merely tested, and means
    the next epic to add a DELETE endpoint does not have to remember anything.

    HEAD is dropped — Starlette registers it alongside every GET, and it is a
    CORS-safelisted method that needs no preflight. OPTIONS is added: the
    CORS middleware answers preflight itself, so no route ever registers it,
    but a browser will not proceed without seeing it advertised.
    """
    methods = {
        method
        for route in app.routes
        for method in getattr(route, "methods", None) or ()
        if method != "HEAD"
    }
    return sorted(methods | {"OPTIONS"})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # First, before anything can log: the level, and the access-log redaction
    # that keeps share tokens and Google codes out of stdout (`logs.py`).
    configure_logging(settings)

    # The API's own documentation — Swagger UI, and the schema it reads —
    # lists every route to anyone who asks, so staging and production serve
    # neither (2026-09-29); local and test keep both. Nothing deployed reads
    # them: packages/shared-types generates from the COMMITTED export, which
    # `scripts/export_openapi.py` writes from `app.openapi()` under local
    # settings, with no server and no URL. ReDoc was never served anywhere.
    docs_served = not settings.is_deployed
    app = FastAPI(
        title="AI Visibility & Competitive Intelligence Platform API",
        version=__version__,
        openapi_url=f"{settings.api_base_path}/openapi.json" if docs_served else None,
        docs_url=f"{settings.api_base_path}/docs" if docs_served else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    register_error_handlers(app)

    # Routers are registered BEFORE the CORS middleware, because the allowed
    # method list is derived from them — see `_registered_methods`.
    base = settings.api_base_path
    app.include_router(health.router, prefix=base)
    app.include_router(auth.router, prefix=base)
    app.include_router(agencies.router, prefix=base)
    app.include_router(billing.router, prefix=base)
    app.include_router(clients.router, prefix=base)
    app.include_router(competitors.router, prefix=base)
    app.include_router(scans.router, prefix=base)
    app.include_router(scores.router, prefix=base)
    app.include_router(audits.router, prefix=base)
    app.include_router(action_items.router, prefix=base)
    app.include_router(report.router, prefix=base)
    app.include_router(dashboard.router, prefix=base)
    app.include_router(prompt_runs.router, prefix=base)
    app.include_router(alerts.router, prefix=base)

    # A write must come from the web app — see `origin_check.py`. One that names
    # an origin which is not listed is refused, session or not; one that names
    # none is refused only if it carries the session cookie. Added BEFORE the
    # CORS middleware, on purpose: Starlette makes the last `add_middleware` call
    # the outermost layer, and CORS has to stay outermost. It answers a preflight
    # (an OPTIONS, which this never checks) before anything else sees it, and
    # whatever this returns, the refusal included, passes back out through it.
    app.add_middleware(
        OriginCheckMiddleware,
        allowed_origins=settings.cors_allow_origins,
        session_cookie_name=settings.session_cookie_key,
        # The one write that is exempt from both rules: Stripe's webhook, which is
        # verified by its signature instead. Exactly this path and no other.
        exempt_paths=[f"{base}/billing/webhook"],
    )

    # CORS is unchanged. The web app's calls are same-origin through its own
    # `/api/v1` proxy and no longer rely on it; the list stays explicit because
    # credentials plus a wildcard is rejected by browsers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=_registered_methods(app),
        allow_headers=["Content-Type", "Accept", "X-Requested-With"],
    )

    return app


app = create_app()
