"""Prefixed ULID identifiers.

api-contracts.md fixes the convention: `scan_01J...` — a prefixed ULID, "so an
ID is self-describing in logs". This module is the only place IDs are minted.

Why prefixed ULIDs rather than bare UUIDs:
  * self-describing — `clnt_01J...` in a log line needs no lookup to interpret
  * lexicographically sortable by creation time, so `ORDER BY id` is a valid
    cheap proxy for `ORDER BY created_at` and cursor pagination is trivial
  * no cross-type confusion — passing a scan id where a client id belongs is
    caught by `parse`, not by a foreign-key violation three layers down

Cost is ~30 bytes per id versus 16 for a native uuid. That is the right trade
for an operational-clarity win in a product whose row counts are measured in
millions, not billions.
"""

from __future__ import annotations

from typing import Final

from ulid import ULID

# One prefix per entity in product-spec.md §5.3. Adding an entity means adding
# its prefix here first.
AGENCY: Final = "agcy"
USER: Final = "user"
CLIENT: Final = "clnt"
SCAN: Final = "scan"
COMPETITOR_SET: Final = "cset"
COMPETITOR: Final = "comp"
PROMPT_SET: Final = "pset"
PROMPT: Final = "prmt"
ENGINE_RESULT: Final = "eres"
CITATION: Final = "cite"
BRAND_MENTION: Final = "bmen"
TECHNICAL_AUDIT: Final = "taud"
AUDIT_CHECK: Final = "tchk"
SCORE: Final = "scor"
ACTION_ITEM: Final = "acti"
INVITATION: Final = "invt"

ALL_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        AGENCY, USER, CLIENT, SCAN, COMPETITOR_SET, COMPETITOR, PROMPT_SET,
        PROMPT, ENGINE_RESULT, CITATION, BRAND_MENTION, TECHNICAL_AUDIT,
        AUDIT_CHECK, SCORE, ACTION_ITEM, INVITATION,
    }
)

# A ULID is 26 characters of Crockford base32.
_ULID_LEN: Final = 26


class InvalidIdError(ValueError):
    """Raised when an identifier is malformed or of the wrong entity type."""


def new_id(prefix: str) -> str:
    """Mint a new prefixed ULID, e.g. `new_id(ids.SCAN)` -> 'scan_01J...'."""
    if prefix not in ALL_PREFIXES:
        raise InvalidIdError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{ULID()}"


def parse(value: str, expected_prefix: str | None = None) -> tuple[str, str]:
    """Split an id into (prefix, ulid), validating shape and optionally type.

    Passing `expected_prefix` turns a whole class of bug — using a client id
    where a scan id belongs — into a 400 at the edge instead of a confusing
    empty result set deeper in.
    """
    prefix, sep, body = value.partition("_")
    if not sep:
        raise InvalidIdError(f"identifier {value!r} is missing its type prefix")
    if prefix not in ALL_PREFIXES:
        raise InvalidIdError(f"identifier {value!r} has unknown prefix {prefix!r}")
    if len(body) != _ULID_LEN:
        raise InvalidIdError(f"identifier {value!r} does not contain a 26-character ULID")
    try:
        ULID.from_str(body)
    except ValueError as exc:
        raise InvalidIdError(f"identifier {value!r} contains a malformed ULID") from exc
    if expected_prefix is not None and prefix != expected_prefix:
        raise InvalidIdError(
            f"expected a {expected_prefix!r} identifier but got {prefix!r} ({value!r})"
        )
    return prefix, body


def is_valid(value: str, expected_prefix: str | None = None) -> bool:
    """Non-raising form of :func:`parse`."""
    try:
        parse(value, expected_prefix)
    except InvalidIdError:
        return False
    return True
