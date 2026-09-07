"""White-label branding — Epic 9.22.

**The tests that matter here are the ones about what an agency CANNOT change.**
The report's palette is notation rather than decoration: `--avp-vis-*` encodes
the score on a monotonic lightness ramp, `--avp-competitor-{1..5}` is neutral
so no rival reads as endorsed or attacked, `--avp-beacon-*` marks the subject
being scanned (the prospect, not the agency), and the semantic four say a scan
failed or a quota is low. An agency free to recolour any of those changes what
the document means.

So the happy path is two assertions and the rest of this file is boundaries:
the request model cannot express an override of an encoded token, a hostile
colour cannot be stored, and a `javascript:` logo cannot reach an
unauthenticated page.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

BASE = "/api/v1"


async def _sign_up(client: AsyncClient, agency: str, email: str) -> str:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": agency, "fullName": "Op",
              "email": email, "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    return (await client.get(f"{BASE}/auth/me")).json()["agency"]["id"]


class TestSettingBranding:
    async def test_a_logo_and_an_accent_round_trip(
        self, client: AsyncClient
    ) -> None:
        aid = await _sign_up(client, "Acme Digital", "brand1@test.example")

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding",
            json={"logoUrl": "https://cdn.example/acme.png", "accentColor": "#1f6feb"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["logoUrl"] == "https://cdn.example/acme.png"
        assert resp.json()["accentColor"] == "#1f6feb"

    async def test_an_absent_field_is_left_alone_and_an_explicit_null_clears_it(
        self, client: AsyncClient
    ) -> None:
        """The distinction PATCH exists to make.

        An agency updating only its colour must not silently lose its logo, and
        an agency that set the wrong logo needs a way back to unbranded. Both
        require reading the body field by field rather than assigning it whole.
        """
        aid = await _sign_up(client, "Acme Digital", "brand2@test.example")
        await client.patch(
            f"{BASE}/agencies/{aid}/branding",
            json={"logoUrl": "https://cdn.example/acme.png", "accentColor": "#1f6feb"},
        )

        # Absent logoUrl: untouched.
        only_colour = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"accentColor": "#aa0000"}
        )
        assert only_colour.json()["logoUrl"] == "https://cdn.example/acme.png"
        assert only_colour.json()["accentColor"] == "#aa0000"

        # Explicit null: removed.
        cleared = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"logoUrl": None}
        )
        assert cleared.json()["logoUrl"] is None
        assert cleared.json()["accentColor"] == "#aa0000"

    async def test_unbranded_is_the_default_and_a_valid_state(
        self, client: AsyncClient
    ) -> None:
        aid = await _sign_up(client, "Acme Digital", "brand3@test.example")

        resp = await client.patch(f"{BASE}/agencies/{aid}/branding", json={})

        assert resp.status_code == 200
        assert resp.json()["logoUrl"] is None
        assert resp.json()["accentColor"] is None


class TestTheAgencyCannotReachAnEncodedToken:
    """The written policy, as a test — Epic 9.22.

    The guarantee is the TYPE: `BrandingRequest` has no field capable of
    naming a visibility, beacon, competitor or semantic token. These assert
    that rather than trusting a reviewer to notice when a field is added.
    """

    @pytest.mark.parametrize(
        "field",
        [
            "visColor", "visRamp", "visScale",          # the score itself
            "beaconColor", "beacon",                     # the subject scanned
            "competitor1", "competitorColors",           # the rival series
            "successColor", "warnColor", "dangerColor",  # system state
            "infoColor", "tokens", "css", "theme",
        ],
    )
    async def test_no_encoded_token_can_be_set_through_this_endpoint(
        self, client: AsyncClient, field: str
    ) -> None:
        """An unknown field is ignored by the model, so the read-back proves it.

        Asserting a 422 would test Pydantic's `extra` policy rather than this
        product's rule. Asserting the value did not land tests the rule.
        """
        aid = await _sign_up(client, "Acme Digital", f"brand-{field}@test.example")

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={field: "#ff0000"}
        )

        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert set(body) == {"agencyId", "logoUrl", "accentColor"}
            assert body["accentColor"] is None, f"{field} reached the accent"

    def test_the_request_model_has_exactly_two_brandable_fields(self) -> None:
        """A field added here is a policy change and must be a deliberate one."""
        from avp_api.schemas.agency import BrandingRequest

        assert set(BrandingRequest.model_fields) == {"logo_url", "accent_color"}


class TestHostileValuesAreRefused:
    """Both values land on an UNAUTHENTICATED page. That sets the bar."""

    @pytest.mark.parametrize(
        "logo",
        [
            "javascript:alert(1)",                       # script in an <img src>
            "data:text/html;base64,PHNjcmlwdD4=",        # payload carried inline
            "http://cdn.example/acme.png",               # mixed content, blocks
            "//cdn.example/acme.png",                    # scheme-relative
            "  javascript:alert(1)",                     # leading whitespace
            "https:/cdn.example/x.png",                  # one slash short
        ],
    )
    async def test_a_logo_that_is_not_an_https_url_is_refused(
        self, client: AsyncClient, logo: str
    ) -> None:
        aid = await _sign_up(client, "Acme Digital", f"logo{abs(hash(logo))}@test.example")

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"logoUrl": logo}
        )

        assert resp.status_code == 422, f"{logo!r} was accepted"

    @pytest.mark.parametrize(
        "colour",
        [
            "#abcdef; } body { display: none",   # closes the declaration
            "red",                                # a name, not a hex triple
            "#fff",                               # short form, not the contract
            "#12345",                             # five digits
            "#1234567",                           # seven
            "expression(alert(1))",               # legacy CSS execution
            "var(--avp-vis-high)",                # pointing AT an encoded token
        ],
    )
    async def test_an_accent_that_is_not_a_hex_triple_is_refused(
        self, client: AsyncClient, colour: str
    ) -> None:
        """The last case is the interesting one.

        `var(--avp-vis-high)` is not an injection; it is an agency quietly
        adopting the visibility ramp as its brand colour, which is the exact
        confusion the disjointness rule exists to prevent.
        """
        aid = await _sign_up(client, "Acme Digital", f"col{abs(hash(colour))}@test.example")

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"accentColor": colour}
        )

        assert resp.status_code == 422, f"{colour!r} was accepted"

    async def test_the_database_refuses_what_the_schema_would_have(
        self, client: AsyncClient
    ) -> None:
        """Two guards, checked independently.

        The schema rejects a bad value with a readable message; the CHECK
        constraints mean one cannot be stored however it arrived — a script, a
        fixture, a future endpoint that forgets. Neither is redundant.
        """
        from sqlalchemy import text as sa_text

        from avp_api.db import get_sessionmaker

        aid = await _sign_up(client, "Acme Digital", "brand-db@test.example")

        async with get_sessionmaker()() as s:
            for column, value in (
                ("accent_color", "red"),
                ("logo_url", "javascript:alert(1)"),
            ):
                with pytest.raises(IntegrityError):
                    await s.execute(
                        sa_text(f"UPDATE agencies SET {column} = :v WHERE id = :i"),
                        {"v": value, "i": aid},
                    )
                    await s.commit()
                await s.rollback()


class TestTenantIsolation:
    async def test_another_agency_cannot_rebrand_yours(
        self, client: AsyncClient
    ) -> None:
        """404, not 403 — the same rule every scoped route here uses."""
        victim = await _sign_up(client, "Agency A", "branda@test.example")
        await client.post(f"{BASE}/auth/logout")
        await _sign_up(client, "Agency B", "brandb@test.example")

        resp = await client.patch(
            f"{BASE}/agencies/{victim}/branding", json={"accentColor": "#000000"}
        )

        assert resp.status_code == 404

    async def test_branding_requires_a_session(self, client: AsyncClient) -> None:
        aid = await _sign_up(client, "Acme Digital", "brandc@test.example")
        client.cookies.clear()

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"accentColor": "#000000"}
        )

        assert resp.status_code == 401

    async def test_a_member_seat_cannot_rebrand_the_agency(
        self, client: AsyncClient
    ) -> None:
        """How every report an agency sends looks is an agency-level decision.

        Same bar as changing seats, and for the same reason: it has an external
        audience.
        """
        from sqlalchemy import update as sa_update

        from avp_api.db import get_sessionmaker
        from avp_api.models import User, UserRole

        aid = await _sign_up(client, "Acme Digital", "brandd@test.example")
        async with get_sessionmaker()() as s:
            await s.execute(
                sa_update(User)
                .where(User.email == "brandd@test.example")
                .values(role=UserRole.MEMBER)
            )
            await s.commit()

        resp = await client.patch(
            f"{BASE}/agencies/{aid}/branding", json={"accentColor": "#000000"}
        )

        assert resp.status_code == 403


class TestTheReportCarriesBranding:
    """The projection, end to end — and the PDF gap, asserted rather than assumed."""

    async def test_branding_reaches_the_authenticated_and_public_reports(
        self, client: AsyncClient, monkeypatch
    ) -> None:  # noqa: ANN001
        from decimal import Decimal

        from avp_api.models.engine_result import Engine, Sentiment
        from avp_api.models.prompt import PromptIntent
        from avp_api.services import scan_runner
        from avp_api.services.engines import EngineAnswer
        from avp_api.services.prompts import GeneratedPrompt

        async def fake_generate(**kwargs):  # noqa: ANN003, ARG001
            return [GeneratedPrompt(text="q", intent=PromptIntent.AWARENESS)], "stub"

        async def fake_ask_all(prompt, *, engines, settings):  # noqa: ANN001, ARG001
            return [
                EngineAnswer(engine=e, engine_version="stub", prompt_text=prompt,
                             text="Help Scout is simpler and well liked.", latency_ms=5)
                for e in engines
            ]

        async def fake_sentiment(answer, *, subject_name, settings=None):  # noqa: ANN001, ARG001
            return Sentiment.POSITIVE, Decimal("0.900")

        monkeypatch.setattr(scan_runner.prompt_service, "generate_prompts", fake_generate)
        monkeypatch.setattr(scan_runner.engine_service, "ask_all", fake_ask_all)
        monkeypatch.setattr(
            scan_runner.extraction_service, "classify_sentiment", fake_sentiment
        )
        assert Engine.CLAUDE  # the stub covers whatever DEFAULT_ENGINES holds

        aid = await _sign_up(client, "Acme Digital", "brand-report@test.example")
        await client.patch(
            f"{BASE}/agencies/{aid}/branding",
            json={"logoUrl": "https://cdn.example/acme.png", "accentColor": "#1f6feb"},
        )
        cid = (await client.post(
            f"{BASE}/clients", json={"url": "helpscout.com", "classify": False}
        )).json()["id"]
        sid = (await client.post(f"{BASE}/clients/{cid}/scans", json={})).json()["id"]

        private = (await client.get(f"{BASE}/scans/{sid}/report")).json()
        assert private["agency"]["logoUrl"] == "https://cdn.example/acme.png"
        assert private["agency"]["accentColor"] == "#1f6feb"

        token = (await client.post(f"{BASE}/scans/{sid}/share")).json()["token"]
        client.cookies.clear()
        public = (await client.get(f"{BASE}/reports/{token}")).json()
        assert public["agency"] == private["agency"], "the prospect sees the same branding"

    async def test_the_pdf_does_not_carry_the_logo_and_that_is_deliberate(
        self, client: AsyncClient
    ) -> None:
        """Epic 9.22 shipped the web half only, and named the gap.

        `report_pdf.py` was built at zero dependencies behind an explicit
        licensing survey. A logo is the first image this document has ever
        needed, and adding one would reopen that survey AND make the server
        fetch an agency-supplied URL. This asserts the decision so that
        reversing it is a deliberate act rather than a quiet drift.
        """
        import inspect

        from avp_api.services import report_pdf

        source = inspect.getsource(report_pdf)
        assert "logo_url" not in source
        assert "accent_color" not in source
