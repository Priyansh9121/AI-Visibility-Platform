"""The breached-password check (hardening A4, the founder's D10; launch checklist 3.130, item 1).

WHAT IT DOES
------------
Where a person SETS a password (sign-up, a password change, a reset, an invitation accept), and
only while `BREACHED_PASSWORD_CHECK_ENABLED` is on, the password is looked up in the Pwned
Passwords range service. The lookup is k-anonymity. This service sends the first FIVE characters
of the password's SHA-1 hash (upper-case hex) and nothing else: not the password, not the rest of
the hash, no address, no cookie. The service answers with the ending of every hash that begins
with those five characters (some hundreds), and the comparison with the rest of the hash is made
HERE, so the service never learns which one was meant. `Add-Padding: true` asks it to pad the
answer with made-up entries, each with a count of 0, so that the size of the answer says nothing
about the prefix either; they are ignored. A listed password is refused with
`BreachedPasswordError`: a `422` on the password's own field, in the shape the web forms already
show (`services/auth.SamePasswordError` has it), carrying the founder's sentence.

OFF BY DEFAULT. The range service is an outside party that `/privacy` does not name yet. The
founder's decision (checklist 3.130, item 1) is that a new outside service ships switched off and
its `/privacy` line goes to the lawyer (checklist 2.4). Off, `refuse_if_breached` returns at once:
no client is built and no request is made. `providers.test.ts` reads the default from `config.py`
and fails if it is turned on before the privacy page names the service.

NEVER AT SIGN-IN. A person presenting a password is not setting it, and an account whose password
is on a list keeps working: D10 forces no rotation, and the rule applies at the next change.

FAILS OPEN. A lookup that raises, times out, is answered with a status that is not a success, with
a body that is not a list of `SUFFIX:COUNT` lines, or with one that is bigger than the cap below,
accepts the password and logs `auth.breach_check_unavailable` with the error's class and nothing
else: never the password, its hash, the prefix, a suffix or the address. A refusal that depended
on an outside service being up would hand that service the power to stop anybody setting a
password. The log line is how an operator learns the check is not checking.

WHERE IT RUNS. On the password alone, before the password is hashed, and in the order that costs
least for the answer that is not a breach:
  * sign-up: first, on every attempt that has passed the per-address limit and the schema, before
    the hash and before any statement. Nothing before it depends on WHETHER THE ADDRESS HAS AN
    ACCOUNT, so the answer to it never does: a new address and a taken one make the same one
    request, and a listed password gets this `422` for both, so the neutral 409 (A1) cannot be told
    from a success by a probe that uses one.
  * a change: after the current password is verified and found to differ from the new one.
  * a reset and an invitation accept: after the first look at the link, so a link that was never
    good is refused before any outside request is made, as it is before a hash, and inside the
    claim on the link (`rate_limit.claim_redemption`), whose lifetime counts this wait.

THE BOUND. One attempt of at most `BREACHED_PASSWORD_TIMEOUT_SECONDS` in all.
`call_bounds.CallBound` is not used: it is the shape of a paid Anthropic call (its `client()`
builds an Anthropic client and its ceiling adds the SDK's retry backoff). This call has no SDK and
no retry (`httpx.AsyncClient` does not retry), so the ceiling IS the setting. httpx's own timeout
bounds each step (connect, write, read) and not the call, so one outer `asyncio.timeout` makes the
setting the whole of it, which is what `CallBound.deadline()` does for the paid calls.

THE SIZE OF THE ANSWER. At most `BREACHED_PASSWORD_MAX_ANSWER_BYTES` (1 MiB by default; a padded
answer is tens of KB). The answer is read as a stream and the read stops, and the connection with
it, once the bytes AFTER decompression pass the cap: `UnexpectedAnswerError`, which fails open.
httpx inflates with no limit of its own, so the cap is counted on what it hands over, one decoded
chunk at a time: the most that is held is the cap plus one decoded chunk. With one encoding, a chunk
of at most 64 KiB off the wire inflates to tens of MB at most; an answer that declares several
encodings (`gzip, gzip`) inflates by their product, and nothing here bounds that. So the cap is not
yet strict: before the check is turned on, the read must refuse an answer that declares any
content encoding and count the raw bytes (A4's security confirmation, N1; the runbook's step 0).

WHAT THE LOGS DO NOT SEE. The prefix is in the request's URL, and httpx logs a request's whole URL
at INFO. `logs.configure_logging` pins `httpx` and `httpcore` to WARNING, so neither writes it, and
`tests/test_breached_passwords_a4.py` reads the captured output for the prefix.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

import httpx
import structlog

from .. import __version__
from ..config import Settings
from ..errors import ValidationProblem

logger = structlog.get_logger(__name__)

# The founder's sentence (D10, 2026-10-22). Plain, and it says what to do next; it does not name
# the service the lookup goes to.
BREACHED_PASSWORD_MESSAGE = "This password has appeared in a data breach. Choose a different one."

# The range endpoint of the Pwned Passwords service; the first five characters of the hash are
# appended. The one literal address of this check in `src`, and the one `providers.test.ts`
# classifies (its host is in the "switched off until /privacy names it" category).
RANGE_URL = "https://api.pwnedpasswords.com/range/"

# Who is asking: this API, by name and version. Deliberately not the crawler's identity
# (`crawl.crawler_user_agent`, AVPScanBot), which says "a bot is reading your site"; this is not
# one.
USER_AGENT = f"AVP-API/{__version__} (breached-password check)"

# How much of the hash is sent. Five is the service's own range size; sending more would narrow the
# set of passwords the answer could be for, and sending all of it would be the hash itself.
PREFIX_LENGTH = 5

# One entry of the answer: the 35 characters that follow the prefix, a colon and a count.
_ANSWER_LINE = re.compile(r"[0-9A-F]{35}:[0-9]{1,12}", re.IGNORECASE)


class UnexpectedAnswerError(Exception):
    """The answer is not a list of `SUFFIX:COUNT` lines, or is bigger than the cap allows."""


class BreachedPasswordError(ValidationProblem):
    """The password is on the breached-password list: a `422` on its field, with the sentence.

    The shape `services/auth.SamePasswordError` has, so the forms that already show a field's
    message show this one with no change: `detail` is the usual one-line summary and `errors`
    names the field the form reads (`password` or `newPassword`, as the request's own field is
    called). Raised only after the check has gone through and the password was found listed.
    """

    def __init__(self, field: str) -> None:
        super().__init__(
            detail="One or more fields were invalid.",
            errors=[
                {"field": field, "message": BREACHED_PASSWORD_MESSAGE, "type": "value_error"}
            ],
        )


def hash_parts(password: str) -> tuple[str, str]:
    """The password's SHA-1 in upper-case hex, as (the five characters that are sent, the rest).

    SHA-1 is the service's lookup key and nothing is protected by it, hence `usedforsecurity=False`.
    The rest never leaves this process.
    """
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    return digest[:PREFIX_LENGTH], digest[PREFIX_LENGTH:]


def is_listed(answer: str, suffix: str) -> bool:
    """Whether `suffix` is in the service's answer with a count above zero.

    Every line must be a `SUFFIX:COUNT` entry, or the whole answer is `UnexpectedAnswerError`: a
    page from a proxy, or an answer in some other shape, must not read as "not listed" and so pass
    every password without a word. An answer with no entries at all is the same, because the
    padding means a real one never is. Entries with a count of 0 are padding and are ignored.
    """
    entries = 0
    listed = False
    for raw in answer.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ANSWER_LINE.fullmatch(line) is None:
            raise UnexpectedAnswerError
        entries += 1
        found, _, count = line.partition(":")
        if found.upper() == suffix and int(count) > 0:
            listed = True
    if entries == 0:
        raise UnexpectedAnswerError
    return listed


async def _ask(prefix: str, timeout_seconds: float, max_bytes: int) -> str:
    """The service's answer for `prefix`: one attempt, at most `timeout_seconds` in all and at most
    `max_bytes` of it, counted after decompression.

    Streamed, so that a status that is not a success is found out before any of the body is read,
    and so that an answer past the cap is dropped, with its connection, as soon as it passes it.
    The one outer deadline covers all of it, the body included. The text is decoded as UTF-8 with
    replacement: the answer is hex and digits, and anything else is refused by `is_listed`.
    """
    async with asyncio.timeout(timeout_seconds):
        async with (
            httpx.AsyncClient(
                timeout=timeout_seconds,
                headers={"User-Agent": USER_AGENT, "Add-Padding": "true"},
            ) as client,
            client.stream("GET", RANGE_URL + prefix) as response,
        ):
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise UnexpectedAnswerError
    return body.decode("utf-8", errors="replace")


async def refuse_if_breached(password: str, settings: Settings, *, field: str) -> None:
    """Refuse a password that is on the breached-password list. Returns None for any other.

    `field` is the request field the refusal names (`password` or `newPassword`). Returns at once
    while the check is off. Any failure of the lookup accepts the password and logs it (see the
    module's notes): only the exception's class is logged, never its text, which can carry the
    address of the service, and never `exc_info`.
    """
    if not settings.breached_password_check_enabled:
        return
    try:
        prefix, suffix = hash_parts(password)
        answer = await _ask(
            prefix,
            settings.breached_password_timeout_seconds,
            settings.breached_password_max_answer_bytes,
        )
        listed = is_listed(answer, suffix)
    except Exception as exc:
        logger.warning("auth.breach_check_unavailable", error=type(exc).__name__)
        return
    if listed:
        raise BreachedPasswordError(field)
