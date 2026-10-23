"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, EmailStr, Field
from pydantic_core import PydanticCustomError

from ..models import UserRole, UserStatus
from .common import ApiModel, RequestModel

SignInMethod = Literal["email", "google"]

# NIST SP 800-63B: length is what matters; composition rules push users toward
# predictable substitutions and are explicitly discouraged. So: a real minimum,
# a generous maximum, and no character-class theatre.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
# The sentences a person reads when a password they are SETTING breaks these bounds ("Use at least
# 12 characters." and "Use at most 256 characters.") are written in `errors.py`
# (`PASSWORD_TOO_SHORT`, `PASSWORD_TOO_LONG`), which imports nothing from here. A test ties each to
# the number above: change a number and change its sentence with it.

# How few distinct characters make a string not a password. `aaaaaaaaaaaa`
# clears twelve characters and is not one.
MIN_DISTINCT_CHARACTERS = 5

REPETITIVE_PASSWORD_MESSAGE = (
    "Password is too repetitive. Use a longer, more varied passphrase."
)


def reject_repetitive(value: str) -> str:
    """A single repeated character clears a length check but is not a password.

    Cheap to reject; catches the worst real-world input.
    """
    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        raise ValueError(REPETITIVE_PASSWORD_MESSAGE)
    return value


# THE password rule. One definition, FOUR doors: sign-up (`SignUpRequest.password`),
# reset-confirm (`ResetPasswordConfirm.new_password`), the authenticated change
# (`ChangePasswordRequest.new_password`) and the invitation accept
# (`AcceptInvitationRequest.password`). Those are the only places a person SETS a password;
# `tests/test_password_policy_a4.py` lists every function that hashes one, so a fifth cannot
# arrive without being seen.
#
# Sign-up, reset-confirm and the authenticated change each used to restate the
# bounds and re-declare their own `_reject_trivial` validator. Copies of
# one policy are chances for them to drift, and the direction drift takes
# is always the same: a recovery or change path ends up laxer than registration,
# which makes it the way in rather than the way back. Epic 9.13 already said so
# in prose on `ResetPasswordConfirm` ("the SIGN-UP rules, reused rather than
# restated") while still copying the code; this is that sentence made true.
#
# WHAT THE RULE IS (hardening A4, the founder's D10): at least 12 characters and at most 256 (D10
# asks for a maximum of at least 128), counted as Unicode code points after the two ends are
# trimmed; refuse a string of fewer than 5 distinct characters (NIST SP 800-63B allows refusing
# repetitive strings; `reject_repetitive`); no composition rules, so a space, a symbol, a letter
# of any script and an emoji are all allowed. Two things it does NOT do, both on purpose:
#   * It does not normalise Unicode. The string is hashed exactly as the person sent it, so a
#     password with an accent typed as one character and the same typed as two are two passwords.
#     Normalising now would change what every existing hash means for a person who set a password
#     with such a character, and lock them out. (The NFKD in `services/auth.slugify` is the
#     agency's slug and has nothing to do with a password.)
#   * It is not the whole of what happens to a new password. Whether it is on the breached-password
#     list is decided in the services, where the lookup can wait for an outside service and fail
#     open (`services/breached_passwords.py`, off until `/privacy` names the service).
# One thing it inherits and does not choose: `ApiModel` trims whitespace at the two ends of every
# string field (`str_strip_whitespace`), here, at sign-in and in the change's current password
# alike. So a password's leading and trailing whitespace is never part of it, whether it is being
# set or presented, and nobody is locked out by it; a space inside is kept. It was recorded as a
# surprise in the Epic 9.14 notes and is pinned by `tests/test_password_policy_a4.py`.
Password = Annotated[
    str,
    Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH),
    AfterValidator(reject_repetitive),
]


def _local_part_within_limit(value: str) -> str:
    """The one length check on an address that `EmailStr` leaves off (hardening A11, D11).

    email-validator checks the 64 characters RFC 5321 allows before the @-sign only when it is
    told to be strict, and pydantic's `EmailStr` does not tell it, so an address with a longer
    local part passed. (The whole-address limit of 254 and the dotted domain are checked either
    way.) The address is put to the library again here with `strict=True`, and a refusal is the
    library's own sentence in the shape pydantic gives every other refusal of an address (a
    `value_error` reading "value is not a valid email address: ..."): this adds no wording.
    """
    try:
        validate_email(value, check_deliverability=False, strict=True)
    except EmailNotValidError as exc:
        raise PydanticCustomError(
            "value_error",
            "value is not a valid email address: {reason}",
            {"reason": str(exc.args[0])},
        ) from exc
    return value


# The address a person gives to MAKE an account. Only here: sign-in and reset-request take an
# address that may already exist, and an account made before this check is not locked out by it.
SignUpEmail = Annotated[EmailStr, AfterValidator(_local_part_within_limit)]


class SignUpRequest(RequestModel):
    agency_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: SignUpEmail
    password: Password


class ResetPasswordRequest(RequestModel):
    """Ask for a reset link. The response is identical whoever this is."""

    email: EmailStr


class ResetRequestOut(ApiModel):
    """The answer to a reset request — the same for every address.

    `email_configured` is a fact about the DEPLOYMENT, never about the address:
    whether this service can send email at all. It exists so the screen can
    stop saying "check your inbox" when nothing was sent (2026-09-29), and it
    cannot become an enumeration oracle, because no address changes it.
    """

    status: str
    detail: str
    email_configured: bool


class ResetPasswordConfirm(RequestModel):
    """Redeem a link and set a new password.

    The password rules are the SIGN-UP rules, reused rather than restated —
    same length bounds and the same repetitiveness check. A reset path with
    weaker rules than registration is a way in, not a convenience.
    """

    token: str = Field(min_length=1, max_length=512)
    new_password: Password


class LoginRequest(RequestModel):
    email: EmailStr
    # Deliberately NOT `Password`. This is a credential being presented, not
    # one being set: applying the minimum-length and repetitiveness rules here
    # would 422 on a password that predates a rule change, turning "your rules
    # got stricter" into "you cannot sign in", and would leak which submitted
    # strings are even eligible to be somebody's password.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(RequestModel):
    """Change your own password while signed in — Epic 9.14.

    `current_password` is presented, not set, so it carries the login bounds
    for the reason `LoginRequest` gives. `new_password` is set, so it carries
    the full rule — the same `Password` sign-up and reset-confirm use.
    """

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: Password


class AcceptInvitationRequest(RequestModel):
    """Redeem a seat invitation and choose a password — Epic 9.14.

    `full_name` is required here and not on the invitation itself: the person
    who sent the invite knows an email address, not how the recipient writes
    their own name. Until this is submitted the seat list shows the address,
    which is the only fact anybody has.
    """

    token: str = Field(min_length=1, max_length=512)
    full_name: str = Field(min_length=1, max_length=200)
    password: Password


class UserOut(ApiModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    last_login_at: datetime | None = None
    created_at: datetime
    # How the account can be signed into — Epic 20: "email" (a password),
    # "google", or both. An account created through Google that never set a password has
    # only "google"; Settings uses that to say so instead of offering a
    # password change that would refuse. (Not `hasPassword`: the contract
    # test forbids any response field named after a credential.)
    sign_in_methods: list[SignInMethod]


class GooglePendingRequest(RequestModel):
    """The ticket to peek at, in a body so it never lands in a URL."""

    ticket: str = Field(min_length=1, max_length=512)


class GooglePendingOut(ApiModel):
    """What Google told us about a person who has no account yet — Epic 20.

    Read by the sign-up completion page so it can show the address the
    account will be under and pre-fill the name. Nothing here is a credential;
    the ticket is.

    `invited_to` (3.126) is the name of the agency whose invitation is waiting for this address,
    or null for a new address. The page then asks whether to join that agency or start the
    person's own, and not for an agency's name. It is given only to the browser the ticket is
    bound to, for an address Google vouched for, and only while the invitation can still be
    accepted.
    """

    email: str
    suggested_name: str
    invited_to: str | None


class GoogleChoiceRequest(RequestModel):
    """The answer to "Join, or start your own agency?": the ticket the page holds, in a body so it
    never lands in a URL. The same ticket the peek was given; the choice is the route."""

    ticket: str = Field(min_length=1, max_length=512)


class GoogleTicketOut(ApiModel):
    """The ticket for the next step (3.126): "Start your own agency" gives the person a new one.

    The same verified identity and the same browser as the ticket just spent, and no invitation
    in it, so it is read and spent by the new-address step exactly as a new address's is. A bearer
    credential like the one in the redirect the callback sends, and it travels only to the
    browser that holds the flow cookie it is bound to.
    """

    ticket: str


class CompleteGoogleSignUpRequest(RequestModel):
    """Finish creating an agency after Google has verified the person — Epic 20.

    The same two facts `SignUpRequest` asks for that Google cannot supply: what
    the agency is called, and how the person writes their own name (Google's
    `name` is a suggestion, not an answer). No email — it is the ticket's — and
    no password, which is the point.
    """

    ticket: str = Field(min_length=1, max_length=512)
    agency_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)


class AgencyOut(ApiModel):
    id: str
    name: str
    slug: str
    seat_limit: int
    created_at: datetime


class SeatUsageOut(ApiModel):
    used: int
    limit: int

    @property
    def available(self) -> int:
        return max(0, self.limit - self.used)


class ScanEmailsOut(ApiModel):
    """The scan-finished email switch, for the signed-in caller only (F1, 2026-10-06).

    `available` is a fact about the deployment: `EMAIL_SCAN_NOTIFICATIONS` is on AND email is
    configured, the same for every user. `enabled` is this caller's own choice.
    """

    available: bool
    enabled: bool


class ScanEmailsRequest(RequestModel):
    """Set your own choice. Strict (a JSON true or false) and nothing else may be in the body."""

    enabled: bool = Field(strict=True)


class MeOut(ApiModel):
    """The whole authenticated context in one call.

    The web app needs user, agency, and seat usage to render its shell. Serving
    them together avoids three round trips on every page load.
    """

    user: UserOut
    agency: AgencyOut
    seats: SeatUsageOut
    # Where the shell's "Send feedback" link writes to — 2026-10-06: the API's `FEEDBACK_EMAIL`,
    # null when it is unset (the default, and then the web draws no link at all). Not a secret,
    # and on every signed-in user's context by design. It is on this response and on NO other:
    # a context is only ever built for someone who holds a session or has just presented a
    # credential in a call that sets one (`test_feedback_email.py` lists the six routes that
    # return one, the one that redeems an invitation token among them), so a signed-out answer,
    # the public plan and the routes that only look a token up (a share, a reset) cannot carry
    # it. Required, with no default, so a route that builds a context has to decide it.
    feedback_email: str | None
    # F1 (2026-10-06). Required, so every route that builds the context has to decide it.
    scan_emails: ScanEmailsOut
