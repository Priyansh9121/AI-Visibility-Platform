# ===========================================================================
# apps/api — the API and, in the same process, the scan executor. Epic 18.2.
#
# WHY THIS BASE IMAGE. The technical audit drives a real Chromium through
# Playwright (`services/technical_audit.py`), and the scan executor runs
# in-process via `BackgroundTasks` — so the host must be a persistent process
# (north-star.md §4.1), and the image must carry a browser. Playwright's own
# image ships Chromium and every system library it needs; the pinned tag MUST
# match the `playwright` version in uv.lock, because the Python package looks
# for a browser build of exactly its own version.
#
# Build from the REPO ROOT — the migrations live in infra/db, not apps/api:
#     docker build -f apps/api/Dockerfile .
# ===========================================================================

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# tini, to be PID 1 (see the ENTRYPOINT). Installed as root, so before `USER`; the
# package lists are removed in the same layer, so it adds the one package.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned, from its own image rather than a curl-pipe.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Playwright's image keeps its browsers here; the pip package must be
    # told, or it looks in ~/.cache and finds nothing.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first, so a source change does not reinstall the world.
COPY apps/api/pyproject.toml apps/api/uv.lock /app/apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /app/apps/api && uv sync --frozen --no-dev --no-install-project

# Then the source, and the migrations the entrypoint runs.
COPY apps/api /app/apps/api
COPY infra/db /app/infra/db
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /app/apps/api && uv sync --frozen --no-dev

# Runs as the non-root user the base image provides.
RUN chown -R pwuser:pwuser /app
USER pwuser

ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    PORT=8000

EXPOSE 8000

# Liveness only — readiness (/ready) checks the datastores and is what a
# host's deploy check should poll. See routers/health.py for why they differ.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

# WHY tini IS PID 1. Every browser run leaves orphans: Chromium's helper
# processes outlive the process that started them, and Linux hands an orphan to
# PID 1. That used to be uvicorn (`exec uvicorn` in docker-entrypoint.sh), which
# never reaps, so each one stayed a zombie until the container restarted. They
# cost no memory, but they accumulate and fill the process table. tini reaps them,
# and passes SIGTERM on to the script, which `exec`s uvicorn, so a deploy still
# stops the server cleanly. It is Ubuntu's own package (MIT), installed above.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/apps/api/docker-entrypoint.sh"]
