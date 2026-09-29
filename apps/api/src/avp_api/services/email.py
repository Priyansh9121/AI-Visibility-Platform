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
must answer identically for every address, whether it has an account and
whether the provider is reachable — any difference is an oracle for
enumerating accounts. Whether a key is set at all is the one thing it does
report (2026-09-29), from `Settings.email_configured`: a fact about the
deployment, the same for every address, and never the outcome of a send.

With no `RESEND_API_KEY` nothing is sent, and the log line says so and nothing
more. That is production's state today (2026-09-29: no verified sending domain
yet), and the flows above this module now say so to the person, too:
`Settings.email_configured` is what the reset response and the invite response
report, never the outcome of a send.

No `resend` SDK. Its default client is `requests` (synchronous), which is the
exact objection Epic 3.1 raised against the SerpApi SDK inside an async service,
and the send is one authenticated JSON POST. `httpx` is already vetted
(BSD-3-Clause). Zero dependencies added.

NOTHING THAT CARRIES A TOKEN IS EVER LOGGED, IN ANY CONFIGURATION
-----------------------------------------------------------------
Until 2026-09-29 the unconfigured path logged the recipient and the full
reset or invitation URL — "the link is recoverable from the log", for
development. With Resend unset in production, that put live credentials in
stdout, which Render keeps: anyone who could read the logs could take over any
account that asked for a reset. Every line here now carries the event, the
user id and whether a message went out. Never the address, never the link.
`tests/test_secrets_stay_out_of_logs.py` captures the log output of both
paths, configured and not, and fails if a token, a link or an address appears
in it.
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
    user_id: str,
    subject: str,
    text: str,
    log_event: str,
) -> None:
    """One send. **Never raises, in any path.**

    Shared by both messages rather than copied, so the two cannot end up with
    different failure behaviour — which is the failure mode that matters here,
    because the reset endpoint's security property depends on its send never
    reporting an outcome. A second hand-written copy of this would be one
    stray `raise` away from becoming an account-enumeration oracle.

    Every log line names the account by `user_id` and nothing else about it:
    not `to`, and never `text`, which holds the link.
    """
    key = settings.resend_api_key

    if key is None:
        # Nothing is claimed to have been sent, and the log line says so.
        logger.info(f"{log_event}.not_sent", user_id=user_id, reason="email is not configured")
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
        logger.warning(f"{log_event}.failed", user_id=user_id, error=type(exc).__name__)
        return

    logger.info(f"{log_event}.sent", user_id=user_id)


async def send_password_reset(
    to: str, reset_url: str, *, user_id: str, settings: Settings
) -> None:
    """Send a reset link. **Never raises, whatever happens.**

    Returns `None` in every path on purpose. A caller that could distinguish
    "sent" from "not sent" would eventually branch on it, and the endpoint's
    guarantee is that it cannot.
    """
    await _send(
        settings=settings,
        to=to,
        user_id=user_id,
        subject=_SUBJECT,
        text=_body(reset_url),
        log_event="email.password_reset",
    )


async def send_invitation(
    to: str,
    invite_url: str,
    *,
    user_id: str,
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
        user_id=user_id,
        subject=_invitation_subject(agency_name),
        text=_invitation_body(
            invite_url, agency_name=agency_name, inviter_name=inviter_name
        ),
        log_event="email.invitation",
    )
