"""Logging, configured once at startup — 2026-09-29.

Two things, both missing until this date.

THE LEVEL
---------
structlog was never configured, so its built-in default applied — no level
filter at all — and every DEBUG line printed in production, including two that
carry model-written reasoning about client sites (`classify.result`,
`fixes.reasoning`). `configure_logging` applies `Settings.effective_log_level`:
`LOG_LEVEL` when set, otherwise INFO in staging and production and DEBUG in
local and test. The output format is structlog's own default, unchanged.

SECRETS IN ACCESS-LOG PATHS
---------------------------
uvicorn's access log prints each request's full path and query string, and
three kinds of request carry a secret there:

- a share link's token: `/reports/{token}` and `/reports/{token}.pdf`;
- the Google callback's `code` and `state`: `/auth/google/callback?…`;
- an unfinished Google sign-up's `ticket`: `/auth/google/pending?…`.

`RedactSecretPaths` rewrites those before the line is formatted — the token
segment becomes `…`, and those two query strings are dropped. The access log
itself stays: the client, the method, the status and the rest of the path are
how a request is traced. Invitation and reset tokens travel in POST bodies,
which the access log never prints.

INSTALLED BY `create_app` (`main.py`), so it applies to whatever command
imports `avp_api.main:app` — the uvicorn in `docker-entrypoint.sh`, which is
what the image runs on Render. uvicorn configures its own loggers BEFORE it
imports the app, so a filter added here is not overwritten; the build log's
2026-09-29 entry records that run, with the redacted lines it printed.
"""

from __future__ import annotations

import logging
import re

import structlog

from .config import Settings

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "…"

# A share token is the path segment after /reports/, up to a slash, a query or
# the ".pdf" suffix. Tokens are URL-safe base64 — never a dot.
_SHARE_TOKEN = re.compile(r"(/reports/)[^/?#.]+")
# Everything after the "?" on the two Google routes that carry a secret there.
_SECRET_QUERY = re.compile(r"(/auth/google/(?:callback|pending))\?\S*")


def redact_path(path: str) -> str:
    """The path with every secret it can carry replaced. Anything else untouched."""
    return _SECRET_QUERY.sub(r"\1", _SHARE_TOKEN.sub(rf"\g<1>{REDACTED}", path))


class RedactSecretPaths(logging.Filter):
    """Redact the path in a uvicorn access-log record before it is formatted.

    uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with the arguments (client,
    method, path-with-query, http version, status), so the path is `args[2]`.
    Any other record passes through untouched: a filter that dropped or broke
    a record it did not recognise would lose a log line to protect nothing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], redact_path(args[2]), *args[3:])
        return True


def configure_logging(settings: Settings) -> None:
    """Apply the level to structlog, and put the redaction on the access log. Idempotent."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(settings.effective_log_level)
    )
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(existing, RedactSecretPaths) for existing in access.filters):
        access.addFilter(RedactSecretPaths())
