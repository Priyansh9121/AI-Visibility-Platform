"""The hostname check on a competitor's domain, on every path that writes one (checklist 3.81).

3.67 checked the domain of a CLIENT at intake. A competitor's domain reached `competitors.domain` by
two paths with no check: typed through `PUT /clients/{clientId}/competitors`, and proposed by the
model in co-citation. Both now use `intake.checked_domain`, which is `parse_domain`'s own check and
its own stored form (lower-case registrable domain, Unicode for an internationalised name).

PUT refuses before anything is written: `422 validation-failed`, with the field and a plain
sentence that quotes nothing typed. Co-citation keeps the brand and drops its domain, and says how
many it dropped in one counts-only line per detection.

Checklist 3.90 adds a second refusal on the typed path: a PLATFORM or publisher (`g2.com`,
`linkedin.com`, `wikipedia.org`), by detection's own list (`serp.NON_COMPETITOR_DOMAINS`) and the
comparison detection makes (`serp.is_plausible_competitor`), so a typed rival and a detected one
obey one rule. A model's domain needs no new check: detection already drops a platform it names.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, update

from avp_api.config import Settings
from avp_api.models import Client, Competitor
from avp_api.services import cocitation
from avp_api.services import competitors as detection
from avp_api.services.cocitation import CoCitationResult
from avp_api.services.intake import checked_domain
from avp_api.services.serp import (
    NON_COMPETITOR_DOMAINS,
    SerpHit,
    SerpResult,
    is_plausible_competitor,
)

BASE = "/api/v1"
SENTINEL = "TYPEDSENTINEL"
# The sentence a person is shown. Pinned, not imported: a change is a change on purpose.
NOT_A_HOSTNAME = (
    "That is not a valid website name. "
    "Names can only contain letters, numbers and hyphens, with dots between the parts. "
    "Enter an address such as example.com."
)

NO_PUBLIC_DOMAIN = (
    "That address does not contain a public domain name. "
    "Enter a website address such as example.com."
)
# For a name that is a hostname but is not the rival's own domain: a page, a path, a subdomain.
OWN_DOMAIN = "Enter just the domain, like rival.com: no https://, page path or subdomain."
SAME_AS_CLIENT = "That is this client's own domain."
# For a platform or publisher (3.90): one on detection's own list, `serp.NON_COMPETITOR_DOMAINS`.
A_PLATFORM = "That is a platform or publisher, so it cannot be added as a rival."

# One of each kind that list holds, spelled out so that trimming the list is a change on purpose.
# An app store is its operator's registrable domain (`apps.apple.com` is `apple.com`).
PLATFORM_KINDS = [
    pytest.param("g2.com", id="review-site"),
    pytest.param("capterra.com", id="software-directory"),
    pytest.param("linkedin.com", id="social-network"),
    pytest.param("reddit.com", id="forum"),
    pytest.param("wikipedia.org", id="wiki"),
    pytest.param("amazon.com", id="marketplace"),
    pytest.param("shiksha.com", id="education-directory"),
    pytest.param("collegedunia.com", id="education-publisher"),
    pytest.param("yelp.com", id="local-directory"),
    pytest.param("forbes.com", id="publisher"),
    pytest.param("indeed.com", id="job-board"),
    pytest.param("crunchbase.com", id="company-database"),
    pytest.param("apple.com", id="app-store"),
    pytest.param("google.com", id="search-engine-and-app-store"),
    pytest.param("github.com", id="code-host"),
]

# Shapes a person pastes that reduce to a DIFFERENT site's domain (a review platform, a social
# network, the client's own site): refused, never reduced.
NOT_THE_OWN_DOMAIN = [
    pytest.param("https://www.g2.com/products/rival/reviews", id="url-with-path"),
    pytest.param("linkedin.com/company/rival", id="path-no-scheme"),
    pytest.param("shop.helpscout.com", id="subdomain-of-the-clients-site"),
    pytest.param("docs.rival.com", id="subdomain"),
    pytest.param("https://rival.com", id="scheme"),
    pytest.param("rival.com:8080", id="port"),
    pytest.param("rival.com?x=1", id="query"),
    pytest.param("www.www.rival.com", id="two-www"),
    pytest.param("g2.com@rival.com", id="userinfo"),
    pytest.param("rival.com#x", id="fragment"),
]

# The classes 3.67's own tests use at the client endpoint.
BAD_DOMAINS = [
    pytest.param("exa mple.com", id="space"),
    pytest.param("exa_mple.com", id="underscore"),
    pytest.param('ex"ample.com', id="quote"),
    pytest.param("a[b].com", id="brackets"),
    pytest.param("ex\x00ample.com", id="control-character"),
    pytest.param("a..b.com", id="empty-label"),
    pytest.param("a" * 64 + ".com", id="label-of-64"),
    pytest.param("-example.com", id="leading-hyphen"),
    pytest.param("example-.com", id="trailing-hyphen"),
    pytest.param("co.uk", id="bare-suffix"),
    pytest.param("http://127.0.0.1:8000", id="ip-address"),
    pytest.param("xn--a.com", id="bad-punycode"),
    *NOT_THE_OWN_DOMAIN,
]


async def _versioned(client: AsyncClient, cid: str, body: dict[str, object]) -> dict[str, object]:
    """`body` as the web sends it: naming the version of the list it has just read.

    None when the client has no list yet, which is what a writer that read none sends.
    """
    read = await client.get(f"{BASE}/clients/{cid}/competitors")
    return {**body, "version": read.json()["version"] if read.status_code == 200 else None}


async def _sign_up(client: AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={
            "agencyName": "Domain Check Agency", "fullName": "Operator",
            "email": "domaincheck@test.example", "password": "correct-horse-battery-staple",
        },
    )
    assert resp.status_code == 201, resp.text


async def _client_with_a_list(client: AsyncClient, stub_discovery) -> str:  # noqa: ANN001
    """A client whose detection has written its next list, so a PUT has a list to replace."""
    await _sign_up(client)
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    stub_discovery(serp_domains=["a.com"], cocit_brands=[("A", "a.com")])
    assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
    return cid


async def _rows(session) -> list[tuple[str, str | None, bool]]:  # noqa: ANN001
    session.expire_all()
    found = (
        await session.execute(
            select(Competitor.name, Competitor.domain, Competitor.is_suppressed).order_by(
                Competitor.name
            )
        )
    ).all()
    return [tuple(r) for r in found]  # type: ignore[misc]


class TestPutRefusesABadDomain:
    @pytest.mark.parametrize("raw", BAD_DOMAINS)
    async def test_refused_before_anything_is_written(
        self, client: AsyncClient, session, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "Good Rival", "domain": "goodrival.com"},
                {"name": "Bad Rival", "domain": raw},
            ]},
        )

        assert resp.status_code == 422, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/validation-failed"
        (error,) = body["errors"]
        assert error["field"] == "competitors.1.domain"
        assert error["message"] in {NOT_A_HOSTNAME, NO_PUBLIC_DOMAIN, OWN_DOMAIN}
        assert await _rows(session) == before, "a refusal writes nothing, the good row included"

    @pytest.mark.parametrize("raw", [p.values[0] for p in NOT_THE_OWN_DOMAIN])
    async def test_a_page_or_subdomain_gets_the_own_domain_sentence(
        self, client: AsyncClient, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Rival", "domain": raw}]},
        )
        assert resp.status_code == 422, resp.text
        (error,) = resp.json()["errors"]
        assert error == {
            "field": "competitors.0.domain", "message": OWN_DOMAIN, "type": "invalid_domain",
        }, "the plain sentence, with no 'Value error, ' in front of it"

    @pytest.mark.parametrize("raw", ["helpscout.com", "HelpScout.com.", "www.helpscout.com"])
    async def test_the_clients_own_domain_is_refused_and_nothing_is_written(
        self, client: AsyncClient, session, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "Good Rival", "domain": "goodrival.com"},
                {"name": "Itself", "domain": raw},
            ]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["type"] == "/problems/validation-failed"
        assert resp.json()["errors"] == [
            {"field": "competitors.1.domain", "message": SAME_AS_CLIENT, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before

    @pytest.mark.parametrize("legacy", ["www.helpscout.com", "shop.helpscout.com", "HELPSCOUT.COM"])
    async def test_a_legacy_client_domain_is_compared_in_its_checked_form(
        self, client: AsyncClient, session, stub_discovery, legacy: str  # noqa: ANN001
    ) -> None:
        """A client stored before 3.67 as `www.acme.com` is still the client `acme.com`."""
        cid = await _client_with_a_list(client, stub_discovery)
        await session.execute(update(Client).where(Client.id == cid).values(domain=legacy))
        await session.commit()
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Itself", "domain": "helpscout.com"}]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"][0]["message"] == SAME_AS_CLIENT

    @pytest.mark.parametrize(
        ("own", "typed"),
        [("bücher.de", "xn--bcher-kva.de"), ("xn--bcher-kva.de", "BÜCHER.de")],
        ids=["unicode-client-punycode-typed", "punycode-client-unicode-typed"],
    )
    async def test_the_clients_own_domain_in_the_other_idn_spelling_is_refused(
        self, client: AsyncClient, session, stub_discovery, own: str, typed: str  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        await session.execute(update(Client).where(Client.id == cid).values(domain=own))
        await session.commit()
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Itself", "domain": typed}]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"][0]["message"] == SAME_AS_CLIENT

    async def test_what_was_typed_is_not_in_the_response(
        self, client: AsyncClient, stub_discovery
    ) -> None:  # noqa: ANN001
        cid = await _client_with_a_list(client, stub_discovery)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "X", "domain": f"user:{SENTINEL}@bad host.com/{SENTINEL}"}
            ]},
        )
        assert resp.status_code == 422, resp.text
        assert SENTINEL not in resp.text
        assert SENTINEL not in str(resp.headers)

    async def test_a_good_international_name_is_stored_in_the_checked_form(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, {"competitors": [
                {"name": "Bücher", "domain": "  BÜCHER.DE "},
                {"name": "With www", "domain": "www.Rival.com"},
                {"name": "With a dot", "domain": "Other.COM."},
                {"name": "Punycode", "domain": "XN--BCHER-KVA.de"},
            ]}),
        )
        assert resp.status_code == 200, resp.text
        by_name = {c["name"]: c["domain"] for c in resp.json()["competitors"]}
        assert by_name == {
            "Bücher": "bücher.de",
            "With www": "rival.com",
            "With a dot": "other.com",
            "Punycode": "xn--bcher-kva.de",
        }
        stored = {name: domain for name, domain, _ in await _rows(session) if domain}
        assert stored["Bücher"] == "bücher.de"

    @pytest.mark.parametrize("domain", [None, "", "   "])
    async def test_an_empty_or_missing_domain_is_still_allowed(
        self, client: AsyncClient, stub_discovery, domain: str | None  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        item: dict[str, Any] = {"name": "No Site"}
        if domain is not None:
            item["domain"] = domain
        resp = await client.put(f"{BASE}/clients/{cid}/competitors", json=await _versioned(
            client, cid, {"competitors": [item]}
        ))
        assert resp.status_code == 200, resp.text
        assert resp.json()["competitors"][0]["domain"] is None


class TestPutRefusesAPlatformDomain:
    """A platform or publisher typed as a rival is refused, by detection's own list (3.90).

    Stored as a rival, `g2.com` types every citation of G2 as the rival's and takes G2 out of
    Tier B for every agency. Detection never proposes such a domain, so a typed one obeys the
    same rule. The refusal is the schema's, so it comes before the client is looked up and before
    anything is written.
    """

    @pytest.mark.parametrize("raw", PLATFORM_KINDS)
    async def test_each_kind_is_refused_with_the_sentence_and_nothing_is_written(
        self, client: AsyncClient, session, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "Good Rival", "domain": "goodrival.com"},
                {"name": "A Platform", "domain": raw},
            ]},
        )

        assert resp.status_code == 422, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["type"] == "/problems/validation-failed"
        assert body["errors"] == [
            {"field": "competitors.1.domain", "message": A_PLATFORM, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before, "a refusal writes nothing, the good row included"

    async def test_every_domain_on_detections_list_is_refused(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The list is detection's own, not a copy: whatever it holds, a typed rival obeys it."""
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        listed = sorted(NON_COMPETITOR_DOMAINS)
        assert len(listed) >= 50, "a sweep of a short list would prove little"

        for start in range(0, len(listed), 10):  # a save takes at most ten rivals
            chunk = listed[start : start + 10]
            resp = await client.put(
                f"{BASE}/clients/{cid}/competitors",
                json={"competitors": [
                    {"name": f"Platform {i}", "domain": domain} for i, domain in enumerate(chunk)
                ]},
            )
            assert resp.status_code == 422, (chunk, resp.text)
            assert resp.json()["errors"] == [
                {"field": f"competitors.{i}.domain", "message": A_PLATFORM,
                 "type": "invalid_domain"}
                for i in range(len(chunk))
            ], chunk
            assert not any(is_plausible_competitor(d, subject_domain="x.com") for d in chunk)
        assert await _rows(session) == before

    async def test_the_spelling_does_not_matter_and_nothing_typed_is_echoed(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        typed = ["G2.COM", "www.g2.com", "g2.com.", "  WWW.LinkedIn.COM. ", "Wikipedia.org"]

        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": f"Platform {i}", "domain": domain} for i, domain in enumerate(typed)
            ]},
        )

        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"] == [
            {"field": f"competitors.{i}.domain", "message": A_PLATFORM, "type": "invalid_domain"}
            for i in range(len(typed))
        ]
        assert "g2.com" not in resp.text.lower()
        assert "linkedin" not in resp.text.lower()
        assert await _rows(session) == before

    @pytest.mark.parametrize(
        "raw",
        ["apps.apple.com", "play.google.com", "en.wikipedia.org", "www.g2.com/products/x/reviews"],
    )
    async def test_a_platforms_own_page_or_subdomain_is_refused_and_never_reduced(
        self, client: AsyncClient, session, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        """An app store's page reduces to its operator's domain: it is refused as a page, not
        stored as `apple.com` (3.81), and not given the platform sentence either."""
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [{"name": "Store", "domain": raw}]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"] == [
            {"field": "competitors.0.domain", "message": OWN_DOMAIN, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before

    async def test_a_typed_rival_and_a_detected_one_obey_one_rule(
        self, client: AsyncClient, stub_discovery  # noqa: ANN001
    ) -> None:
        """Domain by domain, PUT refuses exactly what detection's filter drops."""
        cid = await _client_with_a_list(client, stub_discovery)
        samples = [
            *[p.values[0] for p in PLATFORM_KINDS],
            "zendesk.com", "front.com", "g2labs.com", "linkedin-rival.com", "yelpful.com",
        ]
        refused: list[str] = []
        for domain in samples:
            resp = await client.put(
                f"{BASE}/clients/{cid}/competitors",
                json=await _versioned(
                    client, cid, {"competitors": [{"name": "Rival", "domain": domain}]}
                ),
            )
            assert resp.status_code in {200, 422}, (domain, resp.text)
            if resp.status_code == 422:
                refused.append(domain)
        dropped = [d for d in samples if not is_plausible_competitor(d, subject_domain="x.com")]
        assert refused == dropped
        assert refused, "the sample holds platforms"
        assert len(refused) < len(samples), "and rivals"

    @pytest.mark.parametrize(
        "raw",
        ["g2labs.com", "linkedin-rival.com", "notreddit.com", "yelpful.com", "WWW.Zendesk.com."],
    )
    async def test_a_good_rival_is_stored_as_before(
        self, client: AsyncClient, session, stub_discovery, raw: str  # noqa: ANN001
    ) -> None:
        """A name that only contains a platform's, or sits next to it, is a rival: the rule is
        the list's membership, not a match on part of a name."""
        cid = await _client_with_a_list(client, stub_discovery)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json=await _versioned(client, cid, {"competitors": [{"name": "Rival", "domain": raw}]}),
        )
        assert resp.status_code == 200, resp.text
        (stored,) = [d for name, d, _ in await _rows(session) if name == "Rival"]
        assert stored == raw.lower().removeprefix("www.").removesuffix(".")
        assert resp.json()["competitors"][0]["domain"] == stored

    async def test_the_clients_own_domain_still_gets_its_own_sentence(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        resp = await client.put(
            f"{BASE}/clients/{cid}/competitors",
            json={"competitors": [
                {"name": "Good Rival", "domain": "goodrival.com"},
                {"name": "Itself", "domain": "helpscout.com"},
            ]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"] == [
            {"field": "competitors.1.domain", "message": SAME_AS_CLIENT, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before

    async def test_a_list_with_both_kinds_of_row_is_refused_for_the_platform_row_first(
        self, client: AsyncClient, session, stub_discovery  # noqa: ANN001
    ) -> None:
        """The platform check is the schema's, so it runs before the client's own-domain check,
        which needs the client: the second kind of row is named once the first is fixed."""
        cid = await _client_with_a_list(client, stub_discovery)
        before = await _rows(session)
        both = {"competitors": [
            {"name": "Itself", "domain": "helpscout.com"},
            {"name": "A Platform", "domain": "g2.com"},
        ]}

        first = await client.put(f"{BASE}/clients/{cid}/competitors", json=both)
        assert first.status_code == 422, first.text
        assert first.json()["errors"] == [
            {"field": "competitors.1.domain", "message": A_PLATFORM, "type": "invalid_domain"}
        ]

        both["competitors"].pop()
        second = await client.put(f"{BASE}/clients/{cid}/competitors", json=both)
        assert second.status_code == 422, second.text
        assert second.json()["errors"] == [
            {"field": "competitors.0.domain", "message": SAME_AS_CLIENT, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before

    @pytest.mark.parametrize("fix", ["remove the row", "clear its domain"])
    async def test_a_platform_stored_before_blocks_a_save_until_its_row_is_fixed(
        self, client: AsyncClient, session, stub_discovery, fix: str  # noqa: ANN001
    ) -> None:
        """No migration: what an older version stored is read as it is, and the list that holds
        it is refused when it is saved again, naming the row, until that row is removed (or its
        domain cleared). The editor sends the list back as it read it, so it meets the refusal."""
        cid = await _client_with_a_list(client, stub_discovery)
        await session.execute(
            update(Competitor).where(Competitor.name == "A").values(domain="g2.com")
        )
        await session.commit()

        listed = await client.get(f"{BASE}/clients/{cid}/competitors")
        assert listed.status_code == 200, listed.text
        as_read = [{"name": c["name"], "domain": c["domain"]} for c in listed.json()["competitors"]]
        assert as_read == [{"name": "A", "domain": "g2.com"}], "a read does not recheck a row"
        before = await _rows(session)
        another = {"name": "Another Rival", "domain": "anotherrival.com"}

        blocked = await client.put(
            f"{BASE}/clients/{cid}/competitors", json=await _versioned(
                client, cid, {"competitors": [*as_read, another]}
            )
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["errors"] == [
            {"field": "competitors.0.domain", "message": A_PLATFORM, "type": "invalid_domain"}
        ]
        assert await _rows(session) == before

        fixed = [] if fix == "remove the row" else [{"name": "A", "domain": None}]
        saved = await client.put(
            f"{BASE}/clients/{cid}/competitors", json=await _versioned(
                client, cid, {"competitors": [*fixed, another]}
            )
        )
        assert saved.status_code == 200, saved.text
        assert {c["name"]: c["domain"] for c in saved.json()["competitors"]} == {
            **({"A": None} if fix == "clear its domain" else {}),
            "Another Rival": "anotherrival.com",
        }


class TestCheckedDomain:
    @pytest.mark.parametrize(
        ("raw", "stored"),
        [("Stripe.com", "stripe.com"), ("www.Stripe.com/", "stripe.com"),
         ("https://docs.stripe.com/x", "stripe.com"), ("bücher.de", "bücher.de")],
    )
    def test_the_stored_form_is_intakes(self, raw: str, stored: str) -> None:
        assert checked_domain(raw) == stored

    @pytest.mark.parametrize("raw", ["exa mple.com", "co.uk", "localhost", "a..b.com", "10.0.0.1"])
    def test_a_refusal_is_none(self, raw: str) -> None:
        assert checked_domain(raw) is None


# --- co-citation -------------------------------------------------------------------------------

def _install_anthropic(monkeypatch: pytest.MonkeyPatch, brands: list[dict[str, Any]]) -> None:
    payload = {"brands": brands, "subject_named": False}
    body = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5",
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    real_cls = anthropic.AsyncAnthropic

    def factory(**kwargs: Any) -> anthropic.AsyncAnthropic:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        return real_cls(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr(anthropic, "AsyncAnthropic", factory)


def _keyed() -> Settings:
    return Settings(
        _env_file=None, environment="test",
        database_url="postgresql+asyncpg://unused/unused", redis_url="redis://unused",
        app_secret="test-secret-not-used-in-any-real-environment",
        anthropic_api_key="KEYSENTINEL-anthropic",
    )


@pytest.mark.real_parse
class TestCoCitationDropsABadDomain:
    async def test_the_brand_is_kept_and_its_bad_domain_is_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_anthropic(monkeypatch, [
            {"name": "Good Rival", "domain": "www.GoodRival.com/"},
            {"name": "Odd Rival", "domain": "odd rival.com"},
            {"name": "Quoted Rival", "domain": 'qu"ote.com'},
            {"name": "Bare Rival", "domain": "co.uk"},
            {"name": "Unknown Rival", "domain": None},
            {"name": "Slash Rival", "domain": "https://Rival.com/"},
            {"name": "Path Rival", "domain": "apps.shopify.com/acme"},
            {"name": "Sub Rival", "domain": "shop.acme.com"},
            {"name": "Scheme Path Rival", "domain": "https://www.g2.com/products/rival"},
        ])
        result = await cocitation.run_seed_prompt(
            "alternatives?", subject_brand="Help Scout", settings=_keyed()
        )
        assert result.ok
        assert [(h.name, h.domain) for h in result.hits] == [
            ("Good Rival", "goodrival.com"),
            ("Odd Rival", None),
            ("Quoted Rival", None),
            ("Bare Rival", None),
            ("Unknown Rival", None),
            ("Slash Rival", "rival.com"),
            ("Path Rival", None),
            ("Sub Rival", None),
            ("Scheme Path Rival", None),
        ]
        assert result.domains_dropped == 6, "a missing domain is not a dropped one"


class TestDetectionLogsTheCount:
    async def test_one_line_with_the_count_and_no_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def serp(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [SerpResult(query=q, hits=[SerpHit(domain="a.com", position=1, query=q)])
                    for q in queries[:2]]

        async def seeds(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [CoCitationResult(prompt=p, domains_dropped=n)
                    for p, n in zip(prompts[:2], (2, 1), strict=False)]

        monkeypatch.setattr(detection.serp_service, "search_many", serp)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", seeds)

        with structlog.testing.capture_logs() as events:
            await detection.detect_from_facts(
                brand_name="Help Scout", domain="helpscout.com", industry=None, niche=None,
                name="Help Scout", client_id="cli_x", settings=_keyed(),
            )

        (line,) = [e for e in events if e["event"] == "competitors.cocitation_domains_dropped"]
        assert {k: v for k, v in line.items() if k not in ("event", "log_level")} == {
            "client_id": "cli_x", "dropped": 3,
        }

    async def test_no_line_when_none_was_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def serp(queries, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [SerpResult(query=q) for q in queries[:2]]

        async def seeds(prompts, **kwargs):  # noqa: ANN001, ANN003, ARG001
            return [CoCitationResult(prompt=prompts[0])]

        monkeypatch.setattr(detection.serp_service, "search_many", serp)
        monkeypatch.setattr(detection.cocitation_service, "run_seed_prompts", seeds)
        with structlog.testing.capture_logs() as events:
            await detection.detect_from_facts(
                brand_name="B", domain="helpscout.com", industry=None, niche=None,
                name="B", client_id="cli_x", settings=_keyed(),
            )
        assert not [e for e in events if e["event"] == "competitors.cocitation_domains_dropped"]

