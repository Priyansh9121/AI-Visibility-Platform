"""What the intake card is given when a site's robots.txt does not answer — 2026-10-03.

The card that tells a person what happened to a site the crawler could not read is
tested on the web side, and the code it is handed is tested here
(`test_robots_obeyed.py`: a robots.txt that times out makes the client
`unclassifiable` with `ROBOTS_UNREACHABLE`). Nothing tied the two. Change the shape
of the response or the code, and both suites stayed green while the card showed
something else; launch checklist 3.41 ended by looking at the card by hand.

Both suites now read ONE checked-in file,
`packages/shared-types/fixtures/intake-robots-unreachable.json`: the body
`POST /api/v1/clients` returns for a site whose robots.txt does not answer, with
the ids and timestamps, which differ on every run, replaced by fixed placeholders.
THIS side is the source of truth and writes the file when asked:

    AVP_WRITE_FIXTURES=1 uv run pytest tests/test_intake_robots_unreachable.py

Otherwise it asserts the file is what the API returns now. The web suite
(`apps/web/src/app/intakeRobotsUnreachable.test.tsx`) mounts the intake page, answers
its request with that file, and reads the card the page shows. Change what the API
returns and this goes red on the field that moved; regenerate, and the web suite says
whether the card still tells the person what happened.

No network and no browser: `robots_web` (conftest) answers the crawler's HTTP from
memory, and the browser is a stand-in that fails the test if it is started.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from httpx import AsyncClient

from avp_api import ids
from avp_api.services import crawl

BASE = "/api/v1"
# Typed as a person types it, and as the web test types it (the fixture's own `domain`).
DOMAIN = "prospect-site.com"
ROBOTS = f"https://{DOMAIN}/robots.txt"

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages" / "shared-types" / "fixtures" / "intake-robots-unreachable.json"
)

# What differs on every run, and the fixed value it is replaced by. An id keeps its own
# prefix and a ULID of zeros (the smallest one, still a valid id); an instant is one
# instant. Nothing else in the body is replaced, so a field added to the response, or one
# that starts to vary, fails the comparison instead of being smoothed over.
IDS = {"id": ids.CLIENT, "agencyId": ids.AGENCY}
INSTANTS = ("classifiedAt", "createdAt", "updatedAt")
PLACEHOLDER_ULID = "0" * 26
PLACEHOLDER_INSTANT = "2026-01-01T00:00:00Z"


def _is_utc_instant(value: object) -> bool:
    """A real date and time, written the way the API writes one: in UTC, ending in `Z`."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def normalised(body: dict[str, Any]) -> dict[str, Any]:
    """The body with its volatile fields replaced, each checked to be what it should be first.

    A placeholder over a field that is not an id, or not an instant, would hide the very
    change this fixture exists to show: `id: null` must not become a tidy placeholder.
    """
    out = dict(body)
    for field, prefix in IDS.items():
        value = out.get(field)
        assert isinstance(value, str) and ids.is_valid(value, prefix), (
            f"{field} is not a {prefix} id: {value!r}"
        )
        out[field] = f"{prefix}_{PLACEHOLDER_ULID}"
        assert ids.is_valid(out[field], prefix)
    for field in INSTANTS:
        assert _is_utc_instant(out.get(field)), f"{field} is not a UTC instant: {out.get(field)!r}"
        out[field] = PLACEHOLDER_INSTANT
    return out


def _no_browser() -> NoReturn:
    raise AssertionError("a browser was started for a site whose robots.txt did not answer")


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Intake Card Agency", "fullName": "Op", "email": "card@test.example",
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text


class TestTheBodyTheCardIsGiven:
    async def test_it_is_the_shared_fixture(
        self, client: AsyncClient, robots_web: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The host takes the connection and never answers on /robots.txt.
        robots_web.fail(ROBOTS, httpx.ReadTimeout("no answer"))
        monkeypatch.setattr(crawl, "async_playwright", _no_browser)
        await _sign_up(client)

        resp = await client.post(f"{BASE}/clients", json={"url": DOMAIN, "classify": True})

        assert resp.status_code == 201, resp.text
        body = normalised(resp.json())
        # What the fixture is FOR, said here and not only in the file: regenerating it
        # cannot turn it into the body of some other outcome and still pass. A file that
        # forbids the page is `ROBOTS_DISALLOWED`, and the card words that differently.
        assert body["classificationStatus"] == "unclassifiable"
        assert body["classificationReasonCode"] == "ROBOTS_UNREACHABLE"
        assert body["industry"] is None
        # Nothing but robots.txt was asked for: no page, no HEAD, no sitemap.
        assert set(robots_web.fetched()) == {ROBOTS}

        if os.environ.get("AVP_WRITE_FIXTURES"):
            FIXTURE.write_text(
                f"{json.dumps(body, indent=2, ensure_ascii=False)}\n", encoding="utf-8"
            )
        assert FIXTURE.exists(), (
            f"{FIXTURE.name} is missing: AVP_WRITE_FIXTURES=1 uv run pytest "
            "tests/test_intake_robots_unreachable.py writes it"
        )
        assert body == json.loads(FIXTURE.read_text(encoding="utf-8")), (
            "the response is not what the web suite renders the card from; if the change is "
            "meant, regenerate with AVP_WRITE_FIXTURES=1 and read what "
            "intakeRobotsUnreachable.test.tsx says of the card"
        )


class TestTheNormalisationHidesNothing:
    GOOD = {
        "id": f"clnt_{PLACEHOLDER_ULID}",
        "agencyId": f"agcy_{PLACEHOLDER_ULID}",
        "classifiedAt": "2026-10-03T12:34:56.789012Z",
        "createdAt": "2026-10-03T12:34:55Z",
        "updatedAt": "2026-10-03T12:34:56.789012Z",
        "domain": "prospect-site.com",
    }

    def test_a_well_formed_body_is_replaced_field_by_field_and_nothing_else(self) -> None:
        # The placeholders are themselves what they stand in for.
        assert _is_utc_instant(PLACEHOLDER_INSTANT)
        assert ids.is_valid(f"clnt_{PLACEHOLDER_ULID}", ids.CLIENT)
        out = normalised(self.GOOD)
        assert out["id"] == f"clnt_{PLACEHOLDER_ULID}"
        assert out["agencyId"] == f"agcy_{PLACEHOLDER_ULID}"
        assert {out[field] for field in INSTANTS} == {PLACEHOLDER_INSTANT}
        assert out["domain"] == "prospect-site.com"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", None),
            ("id", "scan_" + "0" * 26),
            ("id", "clnt_not-a-ulid"),
            ("agencyId", ""),
            ("classifiedAt", None),
            ("createdAt", "yesterday"),
            ("updatedAt", "2026-13-45T00:00:00Z"),
            ("updatedAt", "2026-10-03T12:34:56+02:00"),
        ],
    )
    def test_a_volatile_field_that_is_not_what_it_should_be_is_refused(
        self, field: str, value: Any
    ) -> None:
        with pytest.raises(AssertionError):
            normalised({**self.GOOD, field: value})
