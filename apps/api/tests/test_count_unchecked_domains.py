"""`scripts/count_unchecked_domains.py`: counts by kind, and nothing else (launch checklist 3.81).

The script is read-only and prints counts only: a production client's domain must never reach a
terminal, a ticket or a commit. The tests plant recognisable bad domains and read what it prints.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from avp_api import ids
from avp_api.models import Client, Competitor, CompetitorSet, DetectionSource

BASE = "/api/v1"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "count_unchecked_domains.py"


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("count_unchecked_domains", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed(client: AsyncClient, session) -> str:  # noqa: ANN001
    resp = await client.post(
        f"{BASE}/auth/sign-up",
        json={"agencyName": "Count Agency", "fullName": "Operator",
              "email": "count@test.example", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(f"{BASE}/clients", json={"url": "helpscout.com", "classify": False})
    cid = resp.json()["id"]
    await session.execute(
        update(Client).where(Client.id == cid).values(domain="bad SENTINELCLIENT.com")
    )
    cset = CompetitorSet(id=ids.new_id(ids.COMPETITOR_SET), client_id=cid)
    session.add(cset)
    await session.flush()
    rows = [
        ("good.com", "fine"), ("bad SENTINELONE.com", "SENTINELNAME one"), ("co.uk", "bare"),
        ("WWW.Shouty.com", "shouty"), (None, "none"), ("", "blank"),
    ]
    for rank, (domain, name) in enumerate(rows, start=1):
        session.add(Competitor(
            id=ids.new_id(ids.COMPETITOR), competitor_set_id=cset.id, name=name, domain=domain,
            rank=rank, detection_source=DetectionSource.MANUAL,
        ))
    await session.commit()
    return cid


class TestCounts:
    async def test_counts_by_kind_and_prints_no_domain(
        self, client: AsyncClient, session, capsys: pytest.CaptureFixture[str]  # noqa: ANN001
    ) -> None:
        await _seed(client, session)
        script = _load()

        counts = await script.count_unchecked(session)

        assert counts["clients.domain"] == {
            "checked": 1, "empty": 0, "not_a_hostname": 1, "no_public_domain": 0,
            "not_in_checked_form": 0,
        }
        assert counts["competitors.domain"] == {
            "checked": 4, "empty": 2, "not_a_hostname": 1, "no_public_domain": 1,
            "not_in_checked_form": 1,
        }
        for line in script.render(counts):
            print(line)
        out = capsys.readouterr().out
        for forbidden in ("SENTINEL", "good.com", "shouty", "co.uk", "helpscout", "clnt_", "comp_"):
            assert forbidden.lower() not in out.lower(), forbidden
        assert "failing the check: 3; would be rewritten: 1" in out

    async def test_it_writes_nothing(self, client: AsyncClient, session) -> None:  # noqa: ANN001
        await _seed(client, session)
        script = _load()

        async def snapshot() -> list[tuple[str | None, str | None]]:
            session.expire_all()
            return [
                *(await session.execute(select(Client.domain, Client.name))).all(),
                *(await session.execute(select(Competitor.domain, Competitor.name))).all(),
            ]

        before = await snapshot()
        await script.count_unchecked(session)
        assert await snapshot() == before
        total = select(func.count()).select_from(Competitor)
        assert (await session.execute(total)).scalar_one() == 6

    async def test_the_transaction_is_read_only(self, client: AsyncClient, session) -> None:  # noqa: ANN001
        await _seed(client, session)
        await _load().count_unchecked(session)
        with pytest.raises(DBAPIError, match="read-only"):
            await session.execute(update(Client).values(name="changed"))
        await session.rollback()

    @pytest.mark.parametrize(
        ("stored", "kind"),
        [("example.com", None), ("bücher.de", None), ("exa mple.com", "not_a_hostname"),
         ("a..b.com", "not_a_hostname"), ("co.uk", "no_public_domain"),
         ("10.0.0.1", "no_public_domain"), ("www.example.com", "not_in_checked_form"),
         ("Example.com", "not_in_checked_form")],
    )
    def test_the_kinds(self, stored: str, kind: str | None) -> None:
        assert _load().classify(stored) == kind
