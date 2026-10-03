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

- a legacy share token: `/reports/{token}` and `/reports/{token}.pdf` (the
  current `POST /reports/shared[/pdf]` routes carry it in the body);
- the Google callback's `code` and `state`: `/auth/google/callback?…`;
- an unfinished Google sign-up's `ticket`: `/auth/google/pending?…`.

`RedactSecretPaths` rewrites those before the line is formatted — the token
segment becomes `…`, and those two query strings are dropped. The access log
itself stays: the client, the method, the status and the rest of the path are
how a request is traced. Invitation, reset and (now) share tokens travel in POST bodies,
which the access log never prints; the links carry them in the URL fragment,
which a browser never sends.

INSTALLED BY `create_app` (`main.py`), so it applies to whatever command
imports `avp_api.main:app` — the uvicorn in `docker-entrypoint.sh`, which is
what the image runs on Render. uvicorn configures its own loggers BEFORE it
imports the app, so a filter added here is not overwritten; the build log's
2026-09-29 entry records that run, with the redacted lines it printed.

THE LOGGERS KEPT QUIET — 2026-10-03 (`stripe` added 2026-10-04)
---------------------------------------------------------------
httpx logs every request it makes through the stdlib, at INFO, with the whole URL:
`HTTP Request: GET https://serpapi.com/search.json?api_key=… "HTTP/1.1 200 OK"`. The
SerpApi key travels in that query string. httpcore's DEBUG trace prints the host, the
port and every response header. The Anthropic SDK's own logger prints each request's
options at DEBUG, the prompt in them: `Request options: {… 'json_data': {'messages':
[{'role': 'user', 'content': …}]}}`. That is client-site content and, for sentiment, the
engine's answer text: the third-party raw text the facts-only rule says not to keep, and
Render keeps stdout.

The Stripe SDK logs through `stripe`: at INFO each request's method and URL and the message of
each error Stripe answers with, at DEBUG the request body (an agency's name goes to Stripe as the
customer's name) and the response body (`stripe/_util.py`, `stripe/_api_requestor.py`).

Nothing prints any of it while no stdlib handler or level is configured, which is the
case here, so all of it is dropped. Two things can change that: a root-level change
(`logging.basicConfig(level=INFO)`), and the Anthropic SDK itself, which raises its own
logger and the `httpx` one to INFO or DEBUG and installs a root handler when
`ANTHROPIC_LOG` is `info` or `debug` (anthropic 0.125.0, `_utils/_logs.py`, run when the
package is imported). So `configure_logging` pins `httpx`, `httpcore`, `anthropic` and `stripe`
to WARNING, on the loggers themselves and not inherited from the root, and `create_app` runs
it once everything is imported, so the pin is applied last. A warning or an error from any
of them is still written. `ANTHROPIC_LOG` therefore changes nothing in this app.
`tests/test_secrets_stay_out_of_logs.py` holds all of it, the import order included.

ONE THING THE PIN CANNOT REACH: `STRIPE_LOG`. With it set to `info` or `debug` the Stripe SDK
does not only log those lines, it PRINTS them to stderr (`print(..., file=sys.stderr)`), past every
logger level, and Render's log collects stderr as it does stdout. The SDK reads the variable once,
as it is imported, so the protection cannot be Python code that runs later: `docker-entrypoint.sh`
unsets it before it starts anything, and `tests/test_secrets_stay_out_of_logs.py` runs that
script to prove it.
"""

from __future__ import annotations

import logging
import re

import structlog

from .config import Settings

ACCESS_LOGGER = "uvicorn.access"
# Loggers whose records can carry what must not be logged. `httpx` writes each request's
# whole URL at INFO, and the SerpApi key is a parameter of that URL; `httpcore` traces the
# host, the port and every response header at DEBUG; `anthropic` writes each request's
# options at DEBUG, the prompt text in them: client-site content and, for sentiment, the
# engine's answer text, which the facts-only rule says not to keep; `stripe` writes each
# request's URL and Stripe's error messages at INFO and the request and response bodies at
# DEBUG (its stderr printing, `STRIPE_LOG`, is switched off in `docker-entrypoint.sh`).
QUIET_LOGGERS = ("httpx", "httpcore", "anthropic", "stripe")
REDACTED = "…"

# Under /reports/ only the two body-token routes, `/reports/shared` and
# `/reports/shared/pdf`, are logged as they are: they carry no secret. Any other
# remainder (a legacy `/reports/<token>` or `<token>.pdf`, `/reports/shared?token=`,
# `/reports/shared/<token>`, `/reports/shared%2F<token>`) is replaced by one
# ellipsis, keeping only a trailing ".pdf", and a query string on a /reports/ path
# is dropped whole. Tokens are URL-safe base64, so never contain a dot.
_REPORTS_PATH = re.compile(r"(/reports/)([^?#\s]*)(?:[?#]\S*)?")
_OPEN_REPORT_ROUTES = frozenset({"shared", "shared/pdf"})


def _redact_reports(match: re.Match[str]) -> str:
    rest = match.group(2)
    if rest in _OPEN_REPORT_ROUTES and match.group(0) == match.group(1) + rest:
        return match.group(0)
    return match.group(1) + REDACTED + (".pdf" if rest.endswith(".pdf") else "")


# Everything after the "?" on the Google routes. The callback carries Google's
# `code` and `state` there. `/pending` no longer carries anything (its ticket is
# in a POST body); it stays listed so a stale client's `?ticket=` is still cut.
_SECRET_QUERY = re.compile(r"(/auth/google/(?:callback|pending))\?\S*")


def redact_path(path: str) -> str:
    """The path with every secret it can carry replaced. Anything else untouched."""
    return _SECRET_QUERY.sub(r"\1", _REPORTS_PATH.sub(_redact_reports, path))


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
    """Apply the level to structlog, put the redaction on the access log, and keep what
    httpx, httpcore and the Anthropic and Stripe SDKs would write about their requests out of
    the log. Idempotent."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(settings.effective_log_level)
    )
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(existing, RedactSecretPaths) for existing in access.filters):
        access.addFilter(RedactSecretPaths())
    # Explicitly, on the loggers themselves: a logger with no level of its own follows the
    # root, so leaving them unset would let a later root-level INFO write the SerpApi key,
    # and DEBUG the prompts. The Anthropic SDK sets its own level and httpx's when
    # `ANTHROPIC_LOG` is set, at import; this runs after, and the last level set is the one.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
