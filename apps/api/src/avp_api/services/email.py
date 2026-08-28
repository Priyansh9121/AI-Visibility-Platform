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


def _invitation_subject(agency_name: str) -> str:
    return f"{agency_name} invited you to their workspace"


def _invitation_body(invite_url: str, *, agency_name: str, inviter_name: str) -> str:
    """Our own copy again. Plain text, no HTML, no tracking pixel.

    It names who invited and which agency because an unexplained link asking a
    stranger to choose a password is indistinguishable from phishing. Both are
    facts the inviting session already established; nothing here is generated,
    inferred, or guessed.
    """
    return (
        f"{inviter_name} added you to {agency_name} on their AI visibility "
        "workspace.\n\n"
        f"{invite_url}\n\n"
        "Opening the link lets you choose a password and sign in. It works "
        "once and expires in seven days.\n\n"
        "If you were not expecting this, you can ignore it — the seat cannot "
        "be used without opening the link."
    )


async def _send(
    *,
    settings: Settings,
    to: str,
    subject: str,
    text: str,
    log_event: str,
    unconfigured_extra: dict[str, str] | None = None,
) -> None:
    """One send. **Never raises, in any path.**

    Shared by both messages rather than copied, so the two cannot end up with
    different failure behaviour — which is the failure mode that matters here,
    because the reset endpoint's security property depends on its send never
    reporting an outcome. A second hand-written copy of this would be one
    stray `raise` away from becoming an account-enumeration oracle.

    `unconfigured_extra` is what gets logged INSTEAD of sending when no
    provider is configured. It exists because the two messages want different
    things there: each URL is the recoverable credential in development, and
    that log line is how it is recovered.
    """
    key = settings.resend_api_key

    if key is None:
        # The development path, and an honest one: nothing is claimed to have
        # been sent, and the log line says so in as many words.
        logger.info(
            f"{log_event}.not_configured",
            to=to,
            reason="RESEND_API_KEY is unset; no message was sent",
            **(unconfigured_extra or {}),
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
                    "subject": subject,
                    "text": text,
                },
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - swallowed on purpose, see docstring
        # Logged, never surfaced. A provider outage must not turn into a
        # different HTTP response, because the difference is the oracle.
        logger.warning(f"{log_event}.failed", to=to, error=type(exc).__name__)
        return

    # Deliberately no URL here: once a real message carries the token, the
    # token is a live credential and a log line holding it is a copy of it.
    logger.info(f"{log_event}.sent", to=to)


async def send_password_reset(to: str, reset_url: str, *, settings: Settings) -> None:
    """Send a reset link. **Never raises, whatever happens.**

    Returns `None` in every path on purpose. A caller that could distinguish
    "sent" from "not sent" would eventually branch on it, and the endpoint's
    guarantee is that it cannot.
    """
    await _send(
        settings=settings,
        to=to,
        subject=_SUBJECT,
        text=_body(reset_url),
        log_event="email.password_reset",
        unconfigured_extra={"reset_url": reset_url},
    )


async def send_invitation(
    to: str,
    invite_url: str,
    *,
    agency_name: str,
    inviter_name: str,
    settings: Settings,
) -> None:
    """Send a seat invitation. **Never raises** — for a DIFFERENT reason.

    The reset path swallows failures because a distinguishable outcome would be
    an account-enumeration oracle. Nothing is being concealed here: the caller
    is authenticated and named the address themselves. The reason is durability
    instead — the invitation row and the seat it consumes are committed before
    this runs, so a provider outage must not turn a seat grant that SUCCEEDED
    into a 500 that invites the operator to retry it. Re-inviting re-sends the
    link, which is the supported recovery.
    """
    await _send(
        settings=settings,
        to=to,
        subject=_invitation_subject(agency_name),
        text=_invitation_body(
            invite_url, agency_name=agency_name, inviter_name=inviter_name
        ),
        log_event="email.invitation",
        unconfigured_extra={"invite_url": invite_url, "agency": agency_name},
    )
