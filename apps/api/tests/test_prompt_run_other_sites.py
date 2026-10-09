"""An ad-hoc prompt run types a CONFIRMED other site as the brand's own (M1, task 1.2 follow-up).

The scan does it (`tests/test_other_sites_attribution.py`); a prompt run stores `cites_subject`
and `source_type` per citation the same way, so it reads the same two things, once per run: the
client's `other_sites` and the other sites on the rows of the competitor list the run already
reads. A proposed site changes nothing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from avp_api.models.engine_result import Sentiment
from avp_api.services import prompt_runs as service
from avp_api.services.engines import CitedSource, EngineAnswer

BASE = "/api/v1"


@pytest.fixture
def cites_siblings(monkeypatch):  # noqa: ANN001, ANN201
    async def ask_all(prompt: str, *, engines: Any = None, settings: Any = None):  # noqa: ANN202, ARG001
        return [
            EngineAnswer(
                engine=engine, engine_version="stub", prompt_text=prompt,
                text="RMIT University and Monash University are both options.",
                citations=[
                    CitedSource(url="https://rmit.edu.vn/x", domain="rmit.edu.vn", position=1),
                    CitedSource(url="https://monash.edu.my/x", domain="monash.edu.my", position=2),
                ],
            )
            for engine in engines or service.engine_service.DEFAULT_ENGINES
        ]

    async def sentiment(answer: Any, *, subject_name: str, settings: Any = None):  # noqa: ANN202, ARG001
        return Sentiment.POSITIVE, Decimal("0.900")

    monkeypatch.setattr(service.engine_service, "ask_all", ask_all)
    monkeypatch.setattr(service.extraction_service, "classify_sentiment", sentiment)


async def _setup(client: AsyncClient, stub_discovery, *, confirm: bool) -> str:  # noqa: ANN001
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Runs", "fullName": "Op", "email": "runs@test.example",
              "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    made = await client.post(
        f"{BASE}/clients",
        json={"url": "rmit.edu.au", "name": "RMIT University", "classify": False},
    )
    cid = made.json()["id"]
    stub_discovery(
        serp_domains=["monash.edu", "deakin.edu.au"],
        cocit_brands=[("Monash University", "monash.edu"), ("Deakin University", "deakin.edu.au")],
    )
    assert (await client.post(f"{BASE}/clients/{cid}/competitors/detect")).status_code == 201
    if confirm:
        put = await client.put(
            f"{BASE}/clients/{cid}/other-sites",
            json={"client": ["rmit.edu.vn"],
                  "rivals": [{"rivalDomain": "monash.edu", "sites": ["monash.edu.my"]}],
                  "dismissed": []},
        )
        assert put.status_code == 200, put.text
    return cid


async def _cites(client: AsyncClient, cid: str) -> dict[str, tuple[bool, str]]:
    resp = await client.post(
        f"{BASE}/clients/{cid}/prompt-runs", json={"prompt": "best university"}
    )
    assert resp.status_code == 201, resp.text
    cites = resp.json()["results"][0]["citations"]
    return {c["domain"]: (c["citesSubject"], c["sourceType"]) for c in cites}


async def test_a_confirmed_site_is_the_clients_own_in_a_prompt_run(
    client: AsyncClient, stub_discovery, cites_siblings  # noqa: ANN001
) -> None:
    cid = await _setup(client, stub_discovery, confirm=True)

    got = await _cites(client, cid)

    assert got["rmit.edu.vn"] == (True, "owned")


async def test_a_proposed_site_is_still_a_stranger_in_a_prompt_run(
    client: AsyncClient, stub_discovery, cites_siblings  # noqa: ANN001
) -> None:
    cid = await _setup(client, stub_discovery, confirm=False)

    got = await _cites(client, cid)

    assert got["rmit.edu.vn"][0] is False
    assert got["monash.edu.my"][0] is False
    assert got["monash.edu.my"][1] != "competitor"


async def test_a_rivals_confirmed_site_is_typed_as_the_rivals_in_a_prompt_run(
    client: AsyncClient, stub_discovery, cites_siblings  # noqa: ANN001
) -> None:
    cid = await _setup(client, stub_discovery, confirm=True)

    got = await _cites(client, cid)

    assert got["monash.edu.my"] == (False, "competitor")
