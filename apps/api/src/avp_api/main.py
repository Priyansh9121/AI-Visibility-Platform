"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, price_watch, redis_watch
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
from .security_headers import SecurityHeadersMiddleware
from .services import billing as billing_service  # `billing` is the router module above
from .services import memory_stats

logger = logging.getLogger(__name__)
# The stdlib logger above is the start-up line's; structured events go through this one.
structured_logger = structlog.get_logger(__name__)


def _log_memory_at_start() -> None:
    """`app.memory_at_start`: what the process holds before it serves anything. Numbers only.

    The baseline the per-phase and per-browser lines are read against (runbook:
    "Reading the API's memory"). It must never keep the app from starting, so it
    catches everything, whatever `snapshot` does.
    """
    try:
        structured_logger.info("app.memory_at_start", **memory_stats.snapshot())
    except Exception:  # noqa: BLE001 - a memory reading is never worth a start-up
        return


def _log_billing_half_configured(settings: Settings) -> None:
    """`billing.half_configured`: a deployed API with billing only part set up.

    Either one of the key and the Price id is missing, and billing's gates are off for every
    agency, or both are set and the webhook secret is not, and no payment would be recorded.
    Nothing else says so, so the start does, once, naming the missing variable and no value
    (`log_half_configured` in `services/billing.py`). It must never keep the app from starting, so
    it catches everything, whatever that does.
    """
    try:
        billing_service.log_half_configured(settings)
    except Exception:  # noqa: BLE001 - a configuration warning is never worth a start-up
        return


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("api starting environment=%s", settings.environment)
    _log_memory_at_start()
    _log_billing_half_configured(settings)
    # `redis.eviction_policy` now, and `redis.data_lost` whenever the sentinel goes missing, for as
    # long as the app runs (`redis_watch.py`). Neither can fail the start.
    redis_watcher = await redis_watch.start(settings)
    # `billing.price_check` now and then every hour (`price_watch.py`): is the Stripe Price the
    # one this API sells? A task, started only on a DEPLOYED API with Stripe configured (a local
    # start never calls Stripe); it cannot fail or delay the start, because the first check runs
    # in the task and never raises.
    price_watcher = price_watch.start(settings)
    try:
        yield
    finally:
        # Before the pools below are closed, so neither watcher is ever mid-call on a closed pool.
        await price_watch.stop(price_watcher)
        await redis_watch.stop(redis_watcher)
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
    # the outermost layer, and CORS has to stay outside every layer that reads or
    # answers a request. It answers a preflight (an OPTIONS, which this never
    # checks) before this sees it, and whatever this returns, the refusal
    # included, passes back out through it. (The one layer outside CORS answers
    # nothing: the security headers, added last, below.)
    app.add_middleware(
        OriginCheckMiddleware,
        allowed_origins=settings.cors_allow_origins,
        session_cookie_name=settings.session_cookie_key,
        # The one write that is exempt from both rules: Stripe's webhook, which is
        # verified by its signature instead. Exactly this method and this path.
        exempt=[("POST", f"{base}/billing/webhook")],
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

    # Security headers on every response — launch checklist 3.1, see
    # `security_headers.py`. Added LAST, so this is the OUTERMOST layer, outside
    # CORS. That is deliberate and the only way to reach everything: the CORS
    # middleware answers a preflight itself, without calling what it wraps, so a
    # layer inside it never sees a preflight, and a preflight needs these headers
    # as much as any response. The origin check's refusals pass back out through
    # here too. CORS itself behaves exactly as before: this layer reads no request,
    # answers none, and only appends to a response's head on its way out.
    #
    # What it cannot reach: an unhandled exception's 500. Starlette builds that in
    # `ServerErrorMiddleware`, which sits outside every user middleware, so that
    # response leaves without these headers (and without CORS's, as ever). Every
    # error the API chooses to send (`errors.py`) is raised or returned inside the
    # stack, and carries them.
    app.add_middleware(SecurityHeadersMiddleware)

    return app


app = create_app()
