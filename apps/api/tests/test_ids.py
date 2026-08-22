"""Prefixed ULID identifier tests."""

from __future__ import annotations

import pytest

from avp_api import ids
from avp_api.ids import InvalidIdError


def test_new_id_has_prefix_and_ulid() -> None:
    value = ids.new_id(ids.SCAN)
    assert value.startswith("scan_")
    prefix, body = ids.parse(value)
    assert prefix == "scan"
    assert len(body) == 26


def test_every_entity_prefix_round_trips() -> None:
    for prefix in ids.ALL_PREFIXES:
        value = ids.new_id(prefix)
        assert ids.parse(value)[0] == prefix
        assert ids.is_valid(value, prefix)


def test_ids_are_time_sortable() -> None:
    """ULIDs sort by creation time, which is what makes them usable as cursors."""
    generated = [ids.new_id(ids.SCAN) for _ in range(50)]
    assert generated == sorted(generated)


def test_ids_are_unique() -> None:
    assert len({ids.new_id(ids.SCAN) for _ in range(2000)}) == 2000


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(InvalidIdError):
        ids.new_id("nope")


def test_wrong_entity_type_is_caught() -> None:
    """Passing a client id where a scan id belongs must fail at the edge."""
    client_id = ids.new_id(ids.CLIENT)
    with pytest.raises(InvalidIdError, match="expected a 'scan' identifier"):
        ids.parse(client_id, ids.SCAN)
    assert not ids.is_valid(client_id, ids.SCAN)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "noprefix",
        "scan_",
        "scan_tooshort",
        "scan_01J" + "X" * 40,
        "unknown_01JBCD8F9GHJKMNPQRSTVWXYZ",
        "scan_!!!!!!!!!!!!!!!!!!!!!!!!!!",
    ],
)
def test_malformed_ids_are_rejected(bad: str) -> None:
    assert not ids.is_valid(bad)
    with pytest.raises(InvalidIdError):
        ids.parse(bad)
