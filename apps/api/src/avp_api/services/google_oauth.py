"""Google sign-in — Epic 20.

"Sign in with Google" beside the password form, not instead of it. A person
can create an agency or reach an existing account through Google, sessions
afterwards are the same httpOnly cookie `routers/auth.py` has always set, and
seat and tenancy rules apply exactly as they do to a password sign-in.

THE SHAPE: AUTHORIZATION CODE + PKCE, VERIFIED SERVER-SIDE
----------------------------------------------------------
The browser is sent to Google with a random `state`, a random `nonce` and a
PKCE challenge; Google sends it back to `/auth/google/callback` with a code;
this process exchanges the code for an ID token over TLS with the client
secret, and then VERIFIES THAT TOKEN ANYWAY — signature against Google's
published keys, audience, issuer, expiry, and the nonce it minted. Google's
own documentation says the signature check is redundant when the token came
straight from the token endpoint over HTTPS. It is done regardless: the
brief asked for it, the cost is one cached JWKS fetch, and a verification
that exists only when it is strictly necessary is one that gets dropped the
day the code is rearranged.

Nothing in this module trusts anything the browser sent. The email comes
from the verified token, never from a form; the `state` is looked up
server-side, and a short-lived flow cookie (whose digest is parked with the
state) ties it to the browser that pressed the button, so a flow started in
one browser cannot be finished in another.

THE ACCOUNT-LINKING POLICY, DECIDED AND WRITTEN DOWN
----------------------------------------------------
Google returns a verified email. What happens when that email already has a
password account here?

**It links automatically**, and the Google `sub` is stored on the row. The
alternatives were considered and refused:

* *Require the password first.* This blocks the person Google sign-in is
  mostly for — someone who has forgotten it — and proves nothing the
  reset flow does not already accept: a reset link proves control of the
  inbox, and Google's `email_verified` claim on a Google-issued token proves
  the same thing about the same inbox, with the same trust in the same
  provider. A confirmation step would be theatre.
* *Refuse, and tell them to sign in the original way.* Safe, and useless: it
  makes the button a decoy for every existing customer.

Automatic linking is gated on FOUR things, any one of which refuses:
`email_verified` must be true (an unverified Google address is not proof of
anything); the account must be active and not deleted; the row's stored
`sub`, if any, must be THIS `sub` (an email reassigned inside a Workspace to
a different Google account does not inherit the old account — that person
uses the reset flow, which the prior holder's password would also have
gated); and an invited seat must have a live invitation for that address.

**Match by `sub` first, then by email.** A `sub` is Google's stable id for
an account; an email is not. Once linked, a person whose Google address
changes still reaches their account; a person whose OLD address was handed
to somebody else does not lose it to them.

**A link takes the old credentials with it.** There is no email verification
on password sign-up, so whoever registered an address first may not hold the
inbox. When Google vouches for the address and the row has no `sub` yet, the
link sets `password_hash` to NULL and the caller revokes every session the
row already holds (before the new one is minted; fail closed if Redis cannot).
It also stamps the row's unused password-reset tokens used and revokes
the invitations that user had sent and nobody had accepted. It does NOT remove
anything else the earlier holder created: seats they already added, accepted
invitations and the agency's data stay. A password reset does not give the
row a password back (reset refuses a row with none). A row already linked to
this same `sub` is untouched.

**An invited seat is accepted by signing in with Google.** The invitation
link proves control of an inbox by delivering a token to it; Google proves
control of the same inbox by vouching for it. Same proof, so the seat is
activated and the live invitation stamped `accepted_at`, exactly as the
emailed link would have. The invite page itself carries no Google button
(see build-log Epic 20 for why that was deferred).

**A new email creates nothing on its own.** Google cannot tell us what the
agency is called, and an agency created with a placeholder name would need a
rename endpoint this product does not have. So the callback parks the
verified identity under a ten-minute ticket and sends the browser to a page
that asks the two things `SignUpRequest` asks for that Google cannot answer;
`/auth/google/complete` then creates the agency and its owner with no
password. The ticket is a bearer credential with the entropy of a session
token and the lifetime of a reset link, single-use.

THE PROVIDER IS A SEAM
----------------------
`GoogleProvider` is what tests replace. Everything else — the state store,
the callback, the linking policy, the cookie — runs for real in the suite
against a provider that hands back whatever identity the test decided on.
`LiveGoogleProvider` is the one that talks to Google, and it is the one part
of this epic that only a real Google Cloud client can verify end to end.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..errors import EmailAlreadyRegistered
from ..models import Agency, Invitation, PasswordResetToken, User, UserStatus
from ..security import new_session_token, token_digest
from . import auth as auth_service

logger = structlog.get_logger(__name__)

# From https://accounts.google.com/.well-known/openid-configuration, read on
# 2026-09-10 rather than recalled. `accounts.google.com` without the scheme is
# the legacy issuer form Google's docs say to accept as well.
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
SCOPES = "openid email profile"

STATE_PREFIX = "google-state:"
TICKET_PREFIX = "google-ticket:"


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """What a verified ID token says about a person. Nothing else is read."""

    sub: str
    email: str
    email_verified: bool
    name: str
    # The nonce carried inside the token, compared to the one minted at
    # /start. None here means the token carried none, which fails the check.
    nonce: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "sub": self.sub,
                "email": self.email,
                "email_verified": self.email_verified,
                "name": self.name,
                "nonce": self.nonce,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> GoogleIdentity:
        d = json.loads(raw)
        return cls(
            sub=d["sub"],
            email=d["email"],
            email_verified=bool(d["email_verified"]),
            name=d.get("name") or "",
            nonce=d.get("nonce"),
        )


class GoogleExchangeError(Exception):
    """The code could not be turned into a verified identity.

    One exception for every cause — Google refused the code, the network
    failed, the token did not verify — because the browser gets one redirect
    with one reason either way, and the detail belongs in the log.
    """


class GoogleProvider(Protocol):
    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str: ...

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity: ...


class LiveGoogleProvider:
    """The provider that actually talks to Google.

    Constructing it never raises, even unconfigured: `routers/auth.py` checks
    `settings.google_sign_in_configured` and answers 503 before either method
    is reached, so a deployment without a Google client boots and serves
    everything else.
    """

    def __init__(self, settings: Settings | None = None, http: httpx.AsyncClient | None = None):
        self._settings = settings or get_settings()
        self._http = http

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        s = self._settings
        params = {
            "client_id": s.google_oauth_client_id,
            "redirect_uri": s.google_oauth_redirect_url,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Always show the chooser: an operator with two Google accounts
            # should pick, not be signed into whichever was last used.
            "prompt": "select_account",
        }
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        s = self._settings
        assert s.google_oauth_client_secret is not None  # guarded by the route
        data = {
            "code": code,
            "client_id": s.google_oauth_client_id,
            "client_secret": s.google_oauth_client_secret.get_secret_value(),
            "redirect_uri": s.google_oauth_redirect_url,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        try:
            if self._http is None:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    response = await http.post(TOKEN_ENDPOINT, data=data)
            else:
                response = await self._http.post(TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError as exc:
            logger.warning("google.exchange.transport_failed", error=type(exc).__name__)
            raise GoogleExchangeError from exc
        if response.status_code != 200:
            # Google's error body names the cause (invalid_grant, etc.). Logged,
            # not surfaced: the browser gets one reason either way.
            logger.warning("google.exchange.refused", status=response.status_code)
            raise GoogleExchangeError
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise GoogleExchangeError
        # `verify_id_token` is synchronous: PyJWT fetches the JWKS with urllib
        # on a cold cache (a 30s default timeout) and the RSA verify is CPU
        # work. Off the event loop, so a slow first fetch stalls this sign-in
        # and not every other request in the process — found by the
        # 2026-09-21 audit.
        return await asyncio.to_thread(self.verify_id_token, id_token)

    def verify_id_token(self, id_token: str) -> GoogleIdentity:
        """Signature, audience, issuer, expiry. The nonce is checked by the caller.

        The signing key comes from Google's JWKS, fetched over TLS and cached
        by PyJWT for the process. `algorithms` is pinned to RS256 — the only
        one Google's discovery document lists — so a token claiming `none` or
        an HMAC over the public key is refused before any claim is read. `iat`
        and `exp` are checked with `google_id_token_leeway_seconds` of allowance
        for the difference between this host's clock and Google's.
        """
        try:
            key = _jwks().get_signing_key_from_jwt(id_token).key
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._settings.google_oauth_client_id,
                # `iat` is Google's second, floored: with none, a clock a second behind refuses a
                # token issued just now, and a Google-only account has no other way in.
                leeway=self._settings.google_id_token_leeway_seconds,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            logger.warning("google.id_token.rejected", reason=type(exc).__name__)
            raise GoogleExchangeError from exc
        if claims.get("iss") not in GOOGLE_ISSUERS:
            logger.warning("google.id_token.rejected", reason="issuer")
            raise GoogleExchangeError
        # Google sends a JSON boolean (its docs also show the string "true"); nothing else counts.
        raw = claims.get("email_verified")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(claims.get("email") or ""),
            email_verified=raw is True or raw == "true",
            name=str(claims.get("name") or ""),
            nonce=claims.get("nonce"),
        )


_JWKS: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _JWKS  # noqa: PLW0603 - one cached key set per process
    if _JWKS is None:
        _JWKS = jwt.PyJWKClient(JWKS_URI, cache_keys=True)
    return _JWKS


@dataclass(frozen=True, slots=True)
class FlowState:
    """What /start minted and /callback must find.

    The nonce, the PKCE verifier, and the digest of the flow cookie that ties
    the flow to the browser that started it.
    """

    nonce: str
    code_verifier: str
    # The digest of the cookie value set on the browser that pressed the
    # button. None for a record parked before the binding existed.
    binding_digest: str | None = None


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _ticket_from_json(raw: str) -> tuple[GoogleIdentity, str | None]:
    return GoogleIdentity.from_json(raw), json.loads(raw).get("binding_digest")


class GoogleFlowStore:
    """The two short-lived records a sign-in passes through, in Redis.

    Both keyed by the DIGEST of a 256-bit token, as sessions are: a dump of
    the store yields neither a usable state nor a usable ticket. Both single
    use — read and delete in one transaction — and both expire on their own.
    """

    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self._redis = redis
        self._settings = settings or get_settings()

    async def begin(self) -> tuple[str, str, str, str]:
        """Mint a flow. Returns (state, nonce, code_challenge, binding).

        `binding` goes to the browser that pressed the button, in a cookie;
        only its digest is parked, and the callback compares the two.
        """
        state = new_session_token()
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        binding = secrets.token_urlsafe(32)
        record = json.dumps(
            {
                "nonce": nonce,
                "code_verifier": verifier,
                "binding_digest": token_digest(binding),
            }
        )
        await self._redis.set(
            f"{STATE_PREFIX}{token_digest(state)}",
            record,
            ex=self._settings.google_sign_in_ttl_seconds,
        )
        return state, nonce, _pkce_challenge(verifier), binding

    async def consume_state(self, state: str) -> FlowState | None:
        raw = await self._take(f"{STATE_PREFIX}{token_digest(state)}")
        if raw is None:
            return None
        d = json.loads(raw)
        return FlowState(
            nonce=d["nonce"],
            code_verifier=d["code_verifier"],
            binding_digest=d.get("binding_digest"),
        )

    async def issue_ticket(self, identity: GoogleIdentity, binding_digest: str | None) -> str:
        """Park a verified new address. The ticket is bound to the browser whose
        flow cookie hashes to `binding_digest`: the callback's carry-over."""
        ticket = new_session_token()
        record = json.loads(identity.to_json())
        record["binding_digest"] = binding_digest
        await self._redis.set(
            f"{TICKET_PREFIX}{token_digest(ticket)}",
            json.dumps(record),
            ex=self._settings.google_sign_in_ttl_seconds,
        )
        return ticket

    async def peek_ticket(self, ticket: str) -> tuple[GoogleIdentity, str | None] | None:
        """Read without consuming — the completion page shows the address first.
        Returns the identity and the digest of the flow cookie it is bound to."""
        raw = await self._redis.get(f"{TICKET_PREFIX}{token_digest(ticket)}")
        return None if raw is None else _ticket_from_json(raw)

    async def consume_ticket(self, ticket: str) -> tuple[GoogleIdentity, str | None] | None:
        raw = await self._take(f"{TICKET_PREFIX}{token_digest(ticket)}")
        return None if raw is None else _ticket_from_json(raw)

    async def _take(self, key: str) -> str | None:
        # GET and DELETE in one MULTI/EXEC, so two callbacks racing on one
        # state cannot both succeed.
        pipe = self._redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()
        return raw


class GoogleSignInRefusedError(Exception):
    """The identity verified, and this product will not sign it in.

    `reason` is one of a small closed set the web app maps to a sentence:
    `email-unverified`, `account-suspended`, `email-claimed`,
    `invitation-expired`, `account-unavailable`.

    Distinct since 2026-09-21; coarse (one `account-unavailable` for all
    four) from Epic 20 until then, on the argument that which one applied
    was not something to say to whoever stood at the front door. Reversed
    on the brief's instruction and on a better reading of who is standing
    there: Google has just vouched that this person holds the very email
    address in question, so "your account is suspended", "this address is
    linked to a different Google account" and "your invitation has expired"
    each tell the address's own holder about their own seat and nobody
    else anything. A soft-deleted account stays `account-unavailable`: a
    removed seat is the one case where the honest sentence is the same.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ResolvedIdentity:
    """The user to sign in, and whether this sign-in LINKED the row to Google.

    `linked` is True when the row's `google_sub` was NULL and this call set it
    (the INVITED activation included). The caller must then revoke every
    session the user already holds, before and after its commit, and before it
    mints the new one: see "A link takes the old credentials with it" in the
    module docstring. `password_cleared` is True when a password hash was
    removed by the link.
    """

    user: User
    linked: bool = False
    password_cleared: bool = False
    invitations_revoked: int = 0


async def resolve_identity(
    session: AsyncSession, identity: GoogleIdentity, *, settings: Settings | None = None
) -> ResolvedIdentity | None:
    """Apply the linking policy. Returns the user to sign in, or None for a new one.

    Raises `GoogleSignInRefusedError` for every refusal. Everything the module
    docstring says about the policy is implemented here and nowhere else.
    Linking also removes the row's password (see `ResolvedIdentity`).
    """
    if not identity.email_verified:
        raise GoogleSignInRefusedError("email-unverified")
    email = auth_service.normalise_email(identity.email)
    if not email:
        raise GoogleSignInRefusedError("email-unverified")

    by_sub = (
        await session.execute(select(User).where(User.google_sub == identity.sub))
    ).scalar_one_or_none()
    by_email = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    user = by_sub or by_email
    if user is None:
        return None

    if user.deleted_at is not None:
        raise GoogleSignInRefusedError("account-unavailable")
    if user.status is UserStatus.SUSPENDED:
        raise GoogleSignInRefusedError("account-suspended")
    if user.google_sub is not None and user.google_sub != identity.sub:
        # The address now belongs to a different Google account. Not theirs.
        raise GoogleSignInRefusedError("email-claimed")

    now = datetime.now(UTC)
    if user.status is UserStatus.INVITED:
        invitation = (
            (
                await session.execute(
                    select(Invitation)
                    .where(
                        Invitation.agency_id == user.agency_id,
                        Invitation.email == user.email,
                        Invitation.accepted_at.is_(None),
                        Invitation.revoked_at.is_(None),
                        Invitation.expires_at > now,
                    )
                    .order_by(Invitation.id.desc())
                    .limit(1)
                    # Locks in the order a password accept takes them: invitation, then user.
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        user = (
            await session.execute(
                select(User)
                .where(User.id == user.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        # The row may have changed while this waited for the locks: decide again.
        if user.deleted_at is not None:
            raise GoogleSignInRefusedError("account-unavailable")
        if user.status is UserStatus.SUSPENDED:
            raise GoogleSignInRefusedError("account-suspended")
        if user.google_sub is not None and user.google_sub != identity.sub:
            raise GoogleSignInRefusedError("email-claimed")
        if user.status is UserStatus.INVITED:
            if invitation is None:
                raise GoogleSignInRefusedError("invitation-expired")
            invitation.accepted_at = now
            user.status = UserStatus.ACTIVE
            # The roster showed the address until now; Google's name is the first
            # thing anybody has been told, so it takes over the placeholder only.
            if user.full_name == user.email and identity.name.strip():
                user.full_name = identity.name.strip()
            logger.info(
                "invitation.accepted", agency_id=user.agency_id, user_id=user.id, via="google"
            )
        # Otherwise a password accept won the race: the link path below clears its password.

    linked = user.google_sub is None
    password_cleared = False
    invitations_revoked = 0
    if linked:
        # `google_sub` first: the `active_user_has_credential` CHECK wants one of
        # the two set whenever the row is active. Both land in the same flush.
        user.google_sub = identity.sub
        password_cleared = user.password_hash is not None
        user.password_hash = None
        if password_cleared:
            # What the earlier holder sent out is not the real holder's, and links issued before
            # the link must not set a password afterwards. The invitations first, then the links:
            # the order a seat's removal takes them in (`invitations.release_seat`), so the two
            # cannot each wait for a row the other holds.
            invitations_revoked = await revoke_pending_invitations_sent_by(session, user.id)
            await session.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                    # Live links only: an expired one stays "not valid", not "used".
                    PasswordResetToken.expires_at > now,
                )
                .values(used_at=now)
            )
    user.last_login_at = now
    await session.flush()
    return ResolvedIdentity(
        user=user,
        linked=linked,
        password_cleared=password_cleared,
        invitations_revoked=invitations_revoked,
    )


async def revoke_pending_invitations_sent_by(session: AsyncSession, user_id: str) -> int:
    """Revoke every invitation `user_id` sent that is neither accepted nor revoked.

    Only that user's; other users' invitations and active seats are untouched.
    """
    result = await session.execute(
        update(Invitation)
        .where(
            Invitation.invited_by_user_id == user_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
        .returning(Invitation.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


async def complete_sign_up(
    session: AsyncSession,
    identity: GoogleIdentity,
    *,
    agency_name: str,
    full_name: str,
    settings: Settings | None = None,
) -> tuple[Agency, User]:
    """Create an agency and its owner from a verified Google identity. No password.

    Mirrors `auth.sign_up_agency` line for line except the credential: the
    first user is the owner, the seat check is skipped for the same reason,
    and the email is Google's, not a form's.
    """
    settings = settings or get_settings()
    email = auth_service.normalise_email(identity.email)
    if await auth_service.email_exists(session, email):
        # The ticket was minted for a new address and somebody registered it
        # in the ten minutes since.
        raise EmailAlreadyRegistered(
            detail="An account with that email already exists. Try signing in instead."
        )

    # The same insert as a password sign-up (a savepoint, the email's unique index answering a
    # race, a slug retried). Somebody registering this address between the check above and the
    # insert gets the same 409 as the check, and nothing of the driver's error, which names the
    # address, travels with it.
    try:
        agency, user = await auth_service.create_agency_with_owner(
            session,
            settings=settings,
            agency_name=agency_name,
            user_fields={
                "email": email,
                "password_hash": None,
                "google_sub": identity.sub,
                "full_name": full_name.strip(),
                "last_login_at": datetime.now(UTC),
            },
        )
    except auth_service.EmailTakenError:
        pass
    else:
        logger.info("google.signed_up", agency_id=agency.id, user_id=user.id)
        return agency, user
    # Raised outside the `except`: no `__context__`.
    raise EmailAlreadyRegistered(
        detail="An account with that email already exists. Try signing in instead."
    )
