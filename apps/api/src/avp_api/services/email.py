"""Outbound email — Epic 9.13, and the first send path in this product.

There has never been email here. Epic 9.8's share link is deliberately manual:
the operator is handed a URL and pastes it into their own client. A password
reset cannot work that way — the whole point is that it reaches an address only
the account holder controls — so this module exists.

CONFIGURED OR NOT, THE CALLER CANNOT TELL
-----------------------------------------
`send_password_reset` **never raises and never reports a different outcome
based on configuration.** That is not defensive tidiness; it is the security
property the endpoint above it depends on. `POST /auth/reset-password/request`
must answer identically whether the address exists, whether the provider is
reachable, and whether a key is set at all — any difference is an oracle for
enumerating accounts.

With no `RESEND_API_KEY` the would-be email is logged at info and the function
returns. That is a real, useful mode: it is how this runs in development today,
and the reset link is recoverable from the log. It is NOT a fake send — nothing
pretends a message went out, and the log line says plainly that it did not.

No `resend` SDK. Its default client is `requests` (synchronous), which is the
exact objection Epic 3.1 raised against the SerpApi SDK inside an async service,
and the send is one authenticated JSON POST. `httpx` is already vetted
(BSD-3-Clause). Zero dependencies added.

ip-safety.md #7 is not at stake here — the only content is our own copy and our
own URL — but the token itself is a credential and is never logged. The log line
carries the recipient and the URL because in the unconfigured mode the URL is
the point; that mode is for development, and the docstring on the endpoint says
so.
"""

from __future__ import annotations

import httpx
import structlog

from ..config import Settings

logger = structlog.get_logger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT = 10.0

# Reused rather than re-typed at each call site so the two modes cannot drift.
_SUBJECT = "Reset your password"


def _body(reset_url: str) -> str:
    """The message. Our own copy, plain text, no tracking pixel and no HTML."""
    return (
        "Someone asked to reset the password for this address.\n\n"
        f"{reset_url}\n\n"
        "The link works once and expires in an hour.\n\n"
        "If this was not you, nothing has changed and you can ignore this "
        "message — the link cannot be used without opening it."
    )


async def send_password_reset(to: str, reset_url: str, *, settings: Settings) -> None:
    """Send a reset link. **Never raises, whatever happens.**

    Returns `None` in every path on purpose. A caller that could distinguish
    "sent" from "not sent" would eventually branch on it, and the endpoint's
    guarantee is that it cannot.
    """
    key = settings.resend_api_key

    if key is None:
        # The development path, and an honest one: nothing is claimed to have
        # been sent. The URL is logged because recovering it from here IS the
        # mechanism when no provider is configured.
        logger.info(
            "email.password_reset.not_configured",
            to=to,
            reset_url=reset_url,
            reason="RESEND_API_KEY is unset; no message was sent",
        )
        return

    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            response = await client.post(
                RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.email_from,
                    "to": [to],
                    "subject": _SUBJECT,
                    "text": _body(reset_url),
                },
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - swallowed on purpose, see docstring
        # Logged, never surfaced. A provider outage must not turn into a
        # different HTTP response, because the difference is the oracle.
        logger.warning(
            "email.password_reset.failed",
            to=to,
            error=type(exc).__name__,
        )
        return

    # Deliberately no reset_url here: once a real message carries the token,
    # the token is a live credential and a log line holding it is a copy of it.
    logger.info("email.password_reset.sent", to=to)
