"""Password hashing and session-token minting.

Two decisions worth stating, both recorded in docs/build-log.md:

**Argon2id, not bcrypt.** Argon2id won the Password Hashing Competition and is
memory-hard, which is what actually resists the GPU/ASIC cracking rigs that
make bcrypt's pure-CPU cost function look cheap. bcrypt additionally truncates
at 72 bytes, which is a silent correctness trap for passphrase users.

**Opaque random session tokens, not JWTs.** See sessions.py for why.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import Settings, get_settings

# 256 bits of entropy. Long enough that online guessing is irrelevant and
# offline guessing is impossible; short enough for a cookie.
_TOKEN_BYTES = 32


def _hasher(settings: Settings | None = None) -> PasswordHasher:
    s = settings or get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost_kib,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str, settings: Settings | None = None) -> str:
    """Hash a password with Argon2id. The salt is generated per-call."""
    return _hasher(settings).hash(password)


def verify_password(
    password: str, password_hash: str, settings: Settings | None = None
) -> bool:
    """Verify a password. Returns False rather than raising on any mismatch."""
    try:
        return _hasher(settings).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    """True when a stored hash predates the current Argon2 parameters.

    Called on successful login so that raising the cost factor migrates the
    user base transparently on next sign-in, rather than requiring a reset.
    """
    try:
        return _hasher(settings).check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_session_token() -> str:
    """Mint an opaque, URL-safe session token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def new_share_token() -> str:
    """Mint an opaque, URL-safe token for a public report link — Epic 9.8.

    Same generator and same 256 bits as `new_session_token`, deliberately. The
    link is unauthenticated: the token IS the credential, so it must be as hard
    to guess as the session cookie it stands in for. `secrets.token_urlsafe`
    draws from the OS CSPRNG — never `random`, and never anything derived from
    the scan id, the clock or a counter, all of which would be enumerable.

    Stored in the clear, unlike a session token, which is stored as a digest.
    That is a real difference and it is deliberate: a session token can be
    hashed because the client presents it for comparison, whereas this one has
    to be handed back to an operator who wants to copy a URL. A digest would
    make the link unrecoverable after minting. The trade-off is that a database
    dump exposes live share links — acceptable while the same dump would also
    expose every report those links lead to, which it would.
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(token: str) -> str:
    """SHA-256 of a session token, hex encoded.

    Only the digest is stored. A dump of the session store therefore does not
    hand an attacker usable session cookies — the same reason password hashes
    are stored instead of passwords. SHA-256 without a work factor is correct
    here: the token already has 256 bits of entropy, so there is nothing to
    brute-force and a slow KDF would only add latency to every request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def dummy_verify(settings: Settings | None = None) -> None:
    """Burn one Argon2 verification against a throwaway hash.

    Called on the login path when no user matches the submitted email, so that
    "unknown email" and "wrong password" cost the same wall-clock time. Without
    it, response latency is a user-enumeration oracle.
    """
    hasher = _hasher(settings)
    with suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        hasher.verify(_dummy_hash(settings), "not-the-password")


@lru_cache(maxsize=4)
def _dummy_hash_for(time_cost: int, memory_cost: int, parallelism: int) -> str:
    return PasswordHasher(
        time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism
    ).hash("dummy-password-for-timing-equalisation")


def _dummy_hash(settings: Settings | None = None) -> str:
    """A real hash at the configured cost.

    Generated rather than hard-coded: a literal that does not parse would raise
    InvalidHashError immediately and return in microseconds, which is exactly
    the timing signal this function exists to suppress. Cached per parameter
    set so the cost is paid once per process, not once per failed login.
    """
    s = settings or get_settings()
    return _dummy_hash_for(s.argon2_time_cost, s.argon2_memory_cost_kib, s.argon2_parallelism)
