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

import unicodedata
from collections.abc import Mapping

import httpx
import structlog

from ..config import Settings
from ..models.alert import AlertKind

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
    scan_id: str | None = None,
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
    # Ids only. `scan_id` is there for the scan-finished emails (F1), so a line names the scan.
    ids: dict[str, str] = {"user_id": user_id}
    if scan_id is not None:
        ids["scan_id"] = scan_id

    if key is None:
        # Nothing is claimed to have been sent, and the log line says so.
        logger.info(f"{log_event}.not_sent", **ids, reason="email is not configured")
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
        logger.warning(f"{log_event}.failed", **ids, error=type(exc).__name__)
        return

    logger.info(f"{log_event}.sent", **ids)


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


# --- scan-finished emails (F1, 2026-10-06) --------------------------------------------------
# Our own copy, plain text. The client's name and counts are all that is said of the scan: never
# a score, never an alert's own sentence (it quotes the score), never a share link.

# The words the alerts screen uses for each kind (`ClientAlertsView`). Every kind has an entry.
ALERT_KIND_WORDS: dict[AlertKind, str] = {
    AlertKind.VISIBILITY_DROP: "Visibility fell",
    AlertKind.SENTIMENT_DECLINE: "Tone declined",
    AlertKind.OWNED_CITATION_LOST: "Own citation lost",
}

_NO_NAME = "a client"


# The longest a client's name is in an email's subject or body.
NAME_MAX = 60
# Characters that draw nothing or draw as blanks (Hangul fillers, the Braille blank, the combining
# grapheme joiner) and that no category test catches, plus the variation selectors.
_BLANKS = frozenset("\u3164\u115f\u1160\uffa0\u2800\u034f")


def _drawn(ch: str) -> bool:
    if ch.isspace():
        return True
    code = ord(ch)
    if ch in _BLANKS or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
        return False
    return not unicodedata.category(ch).startswith("C")


def plain_name(name: str, *, fallback: str = _NO_NAME) -> str:
    """A name as one short plain line, or `fallback` when nothing is left of it.

    Control, invisible, filler and bidi characters are dropped (a newline in a subject is how a
    header is injected), whitespace is collapsed, `=?` is broken up so a mail client cannot decode
    an RFC 2047 encoded word back into hidden characters, and a name longer than `NAME_MAX` is
    cut at a word boundary and ends in an ellipsis.
    """
    kept = "".join(" " if ch.isspace() else ch for ch in name if _drawn(ch))
    said = " ".join(kept.split()).replace("=?", "= ?")
    if len(said) > NAME_MAX:
        # Cut at a word boundary where there is one, and say it was cut.
        room = said[: NAME_MAX - 1]
        said = (room.rsplit(" ", 1)[0] if " " in room else room).rstrip() + "…"
    return said or fallback


def _footer(settings_url: str) -> str:
    return (
        "You are getting this because you started this scan or turned on its monthly rescan. "
        f"To stop these emails, turn them off in Settings:\n{settings_url}"
    )


def _plural(n: int) -> str:
    return f"{n} alert" if n == 1 else f"{n} alerts"


async def send_scan_ready(
    to: str,
    *,
    user_id: str,
    scan_id: str,
    client_name: str,
    report_url: str,
    settings_url: str,
    partial: bool,
    settings: Settings,
) -> None:
    """The report is ready (SUCCEEDED or PARTIAL). **Never raises**, like every send here."""
    name = plain_name(client_name)
    missing = (
        "Some answers are missing from this scan; the report shows how many each engine "
        "returned.\n\n"
        if partial
        else ""
    )
    text = (
        f"Your report for {name} is ready.\n\n{missing}"
        f"Open it here:\n{report_url}\n\n{_footer(settings_url)}"
    )
    await _send(
        settings=settings, to=to, user_id=user_id, scan_id=scan_id,
        subject=f"Your report for {name} is ready", text=text, log_event="email.scan_ready",
    )


async def send_scan_alerts(
    to: str,
    *,
    user_id: str,
    scan_id: str,
    client_name: str,
    counts: Mapping[AlertKind, int],
    alerts_url: str,
    settings_url: str,
    settings: Settings,
) -> None:
    """How many alerts the scan raised, of each kind, and where to read them."""
    name = plain_name(client_name)
    total = sum(counts.values())
    lines = "\n".join(
        f"- {ALERT_KIND_WORDS[kind]}: {counts[kind]}" for kind in AlertKind if counts.get(kind)
    )
    text = (
        f"Your scan for {name} raised {_plural(total)}:\n\n{lines}\n\n"
        f"See them here:\n{alerts_url}\n\n{_footer(settings_url)}"
    )
    await _send(
        settings=settings, to=to, user_id=user_id, scan_id=scan_id,
        subject=f"Your scan for {name} raised {_plural(total)}", text=text,
        log_event="email.scan_alerts",
    )


async def send_scan_failed(
    to: str,
    *,
    user_id: str,
    scan_id: str,
    client_name: str,
    client_url: str,
    settings_url: str,
    settings: Settings,
) -> None:
    """The scan did not finish. Nothing of it is said but the client's name."""
    name = plain_name(client_name)
    text = (
        f"Your scan for {name} did not finish.\n\n"
        f"You can run it again from the client's page:\n{client_url}\n\n{_footer(settings_url)}"
    )
    await _send(
        settings=settings, to=to, user_id=user_id, scan_id=scan_id,
        subject=f"Your scan for {name} did not finish", text=text, log_event="email.scan_failed",
    )
