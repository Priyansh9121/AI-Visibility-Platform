"""Credential-bearing links carry the token in the URL fragment, never the path.

A fragment is not sent to the server, so it stays out of Render's request log.
"""

from __future__ import annotations

import pytest

from avp_api.config import Settings
from avp_api.routers.report import _share_url
from avp_api.services import password_reset as reset_service
from avp_api.services.links import invite_link, reset_link, share_link

TOKEN = "Tk-abc_123XYZ"
BASE = "https://app.example.com"


def _assert_fragment_only(link: str, page: str) -> None:
    assert link == f"{BASE}/{page}#{TOKEN}"
    before, _, after = link.partition("#")
    assert after == TOKEN
    assert TOKEN not in before, "the token is in the path or query"


@pytest.mark.parametrize("base", [BASE, BASE + "/"])
def test_every_builder_puts_the_token_after_a_hash(base: str) -> None:
    for build, page in (
        (share_link, "share"),
        (invite_link, "invite"),
        (reset_link, "reset-password"),
        (_share_url, "share"),
    ):
        link = build(base, TOKEN)
        assert "#" in link
        _assert_fragment_only(link, page)


def test_reset_url_uses_the_fragment_form(settings: Settings) -> None:
    link = reset_service.reset_url(settings, TOKEN)
    assert link.endswith(f"/reset-password#{TOKEN}")
    assert TOKEN not in link.partition("#")[0]
