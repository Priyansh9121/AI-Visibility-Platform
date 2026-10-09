"""Which matching rule made a scan's brand matches — checklist 3.97.

FORMULA_VERSION v7 (2026-10-07) changed BRAND MATCHING (`extraction.find_brand` began to find a
2-4 character short form written in capitals). Answer text is never stored (facts only), so a scan
matched before v7 can never be matched again, and a re-score from its stored matches would write a
row labelled v7 from matches v7 did not make. The founder decided to refuse that re-score
(`POST /scans/{scanId}/score`, pinned in `test_score_endpoints.py`). What the refusal reads is
`Scan.matcher_version`, and this file pins where that column's value comes from:

* a scan the ORM creates is matched by this code, so the column's default is the current
  `extraction.MATCHER_VERSION`; the run stamps it again (`test_scan_endpoints.py`);
* a row written WITHOUT the column (every row that existed when it was added; an older instance
  still serving during a deploy) reads 'pre-v7', "not recorded", and a re-score of it is refused,
  which is the safe direction;
* the revision's backfill marks a scan 'v7' only when it has a score and every one of its scores
  is v7: production can hold scans run after v7 deployed (the founder's brief describes RMIT
  University's re-run of 2026-10-08), and marking them 'pre-v7' would refuse a re-score that
  is fine. A pre-v7 scan that was explicitly re-scored after the deploy has its older row too,
  so it stays 'pre-v7'.

The backfill is one SQL constant in the revision, `BACKFILL_V7_MATCHED`. It is run here over the
schema the models build, and again by the real `alembic upgrade` over a database that held scans
before the column did.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from avp_api import ids
from avp_api.models import Agency, Client, Scan, ScanStatus, Score, ScoreStatus
from avp_api.services import extraction

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_DIR = REPO_ROOT / "infra" / "db"
VERSIONS_DIR = DB_DIR / "migrations" / "versions"
ALEMBIC = REPO_ROOT / "apps" / "api" / ".venv" / "bin" / "alembic"

REVISION = "5863629b56ab"
SCRATCH_PREFIX = "avp_matcher_version_"

# The four shapes a scan's score history can have, and what the backfill must make of each.
# Names are for the failure message only.
HISTORIES: dict[str, tuple[tuple[str, ...], str]] = {
    "scored only by v7": (("v7",), "v7"),
    "scored by v5, then re-scored by v7": (("v5", "v7"), "pre-v7"),
    "scored only by v5": (("v5",), "pre-v7"),
    "never scored": ((), "pre-v7"),
}


def _load_migration() -> ModuleType:
    """The revision, loaded by path: `versions/` is not a package."""
    (path,) = VERSIONS_DIR.glob(f"*_{REVISION}_*.py")
    spec = importlib.util.spec_from_file_location("scan_matcher_version_revision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess[str]:
    """The real `alembic`, against the one database `db_url` names and no other."""
    return subprocess.run(
        [str(ALEMBIC), *args],
        cwd=DB_DIR,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        timeout=180,
    )


async def _a_client(session: AsyncSession) -> Client:
    agency = Agency(id=ids.new_id(ids.AGENCY), name="A", slug=f"a-{ids.new_id(ids.AGENCY)[-6:]}")
    session.add(agency)
    await session.flush()
    client = Client(id=ids.new_id(ids.CLIENT), agency_id=agency.id, name="C", domain="example.test")
    session.add(client)
    await session.flush()
    return client


async def _a_scan_scored_by(
    session: AsyncSession, client: Client, formula_versions: tuple[str, ...]
) -> str:
    """A finished scan with one score row per formula version. Returns its id."""
    scan = Scan(
        id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=client.agency_id,
        status=ScanStatus.SUCCEEDED,
    )
    session.add(scan)
    await session.flush()
    for version in formula_versions:
        session.add(Score(
            id=ids.new_id(ids.SCORE), scan_id=scan.id, status=ScoreStatus.INSUFFICIENT_DATA,
            composite=None, reason_code="INSUFFICIENT_DATA", formula_version=version, weights={},
        ))
        await session.flush()
    await session.commit()
    return scan.id


async def _matcher_versions(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(select(Scan.id, Scan.matcher_version))
    return {scan_id: version for scan_id, version in rows}


class TestWhereTheColumnsValueComesFrom:
    def test_the_matcher_is_named_by_the_code_that_matches(self) -> None:
        # The default and the stamp both read this one constant; it is a string a column holds.
        assert isinstance(extraction.MATCHER_VERSION, str)
        assert 0 < len(extraction.MATCHER_VERSION) <= Scan.__table__.c.matcher_version.type.length
        # "pre-v7" is the database's word for "not recorded", so no matcher may be called that.
        assert extraction.MATCHER_VERSION != "pre-v7"

    async def test_a_scan_the_orm_creates_is_matched_by_this_code(
        self, session: AsyncSession
    ) -> None:
        client = await _a_client(session)
        scan = Scan(id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=client.agency_id)
        session.add(scan)
        await session.commit()

        assert (await _matcher_versions(session))[scan.id] == extraction.MATCHER_VERSION

    async def test_a_row_written_without_the_column_reads_not_recorded(
        self, session: AsyncSession
    ) -> None:
        """An older instance, still serving while a deploy migrates, inserts a scan it has no
        `matcher_version` for. The database's default says it was not recorded, and a re-score
        of it is refused."""
        client = await _a_client(session)
        await session.commit()
        scan_id = ids.new_id(ids.SCAN)
        await session.execute(
            text(
                "INSERT INTO scans (id, client_id, agency_id, status, trigger, formula_version, "
                "prompt_count, engine_result_count) "
                "VALUES (:id, :client_id, :agency_id, 'queued', 'manual', 'v1', 0, 0)"
            ),
            {"id": scan_id, "client_id": client.id, "agency_id": client.agency_id},
        )
        await session.commit()

        assert (await _matcher_versions(session))[scan_id] == "pre-v7"

    def test_the_column_is_required_and_fits_a_version(self) -> None:
        column = Scan.__table__.c.matcher_version
        assert column.nullable is False
        assert column.type.length == 40  # as formula_version, the other version label
        assert str(column.server_default.arg) == "pre-v7"


class TestTheBackfill:
    """`BACKFILL_V7_MATCHED`: 'v7' iff the scan has a score and every score it has is v7."""

    async def test_marks_a_scan_v7_only_when_every_score_it_has_is_v7(
        self, session: AsyncSession
    ) -> None:
        backfill = _load_migration().BACKFILL_V7_MATCHED
        client = await _a_client(session)
        scans = {
            name: await _a_scan_scored_by(session, client, versions)
            for name, (versions, _expected) in HISTORIES.items()
        }
        # The ORM's default makes each of these 'v7'; the column is new, so every row starts as
        # 'pre-v7', which is what the revision's backfill starts from.
        await session.execute(text("UPDATE scans SET matcher_version = 'pre-v7'"))
        await session.commit()

        await session.execute(text(backfill))
        await session.commit()

        stamped = await _matcher_versions(session)
        assert {name: stamped[scan_id] for name, scan_id in scans.items()} == {
            name: expected for name, (_versions, expected) in HISTORIES.items()
        }

    async def test_a_second_run_changes_nothing_and_leaves_a_stamped_row_as_stamped(
        self, session: AsyncSession
    ) -> None:
        """Safe to run again. A scan a run has stamped 'v7' is not read as 'pre-v7' because its
        score history is mixed: the backfill only ever moves a 'pre-v7' row to 'v7'."""
        backfill = _load_migration().BACKFILL_V7_MATCHED
        client = await _a_client(session)
        mixed = await _a_scan_scored_by(session, client, ("v5", "v7"))
        only_v7 = await _a_scan_scored_by(session, client, ("v7",))
        await session.execute(text("UPDATE scans SET matcher_version = 'pre-v7'"))
        await session.execute(
            text("UPDATE scans SET matcher_version = 'v7' WHERE id = :id"), {"id": mixed}
        )
        await session.commit()

        await session.execute(text(backfill))
        await session.commit()
        once = await _matcher_versions(session)
        await session.execute(text(backfill))
        await session.commit()

        assert once == {mixed: "v7", only_v7: "v7"}
        assert await _matcher_versions(session) == once

    async def test_touches_nothing_but_the_matcher_version(self, session: AsyncSession) -> None:
        """No score is rewritten (scoring-spec.md rule 5) and no other scan column moves."""
        backfill = _load_migration().BACKFILL_V7_MATCHED
        client = await _a_client(session)
        sid = await _a_scan_scored_by(session, client, ("v7",))
        await session.execute(text("UPDATE scans SET matcher_version = 'pre-v7'"))
        await session.commit()
        # Every column of every row, as JSON, the one under test left out.
        scans = text("SELECT to_jsonb(s) - 'matcher_version' FROM scans s ORDER BY s.id")
        scores = text("SELECT to_jsonb(s) FROM scores s ORDER BY s.id")
        scans_before = (await session.execute(scans)).all()
        scores_before = (await session.execute(scores)).all()
        assert scans_before and scores_before

        await session.execute(text(backfill))
        await session.commit()

        assert (await session.execute(scans)).all() == scans_before
        assert (await session.execute(scores)).all() == scores_before
        assert (await _matcher_versions(session))[sid] == "v7"


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed")
async def test_the_upgrade_runs_the_backfill_over_scans_that_predate_the_column(
    scratch_databases,  # noqa: ANN001
) -> None:
    """The real revision, not only its constant: a database at the previous head holds scans of
    each history, `alembic upgrade head` adds the column and backfills it, and a scan an older
    instance inserts afterwards reads 'pre-v7'."""
    migration = _load_migration()
    agency, client = ids.new_id(ids.AGENCY), ids.new_id(ids.CLIENT)
    scans = {name: ids.new_id(ids.SCAN) for name in HISTORIES}

    async with scratch_databases.database(SCRATCH_PREFIX) as url:
        before = _alembic(["upgrade", migration.down_revision], url)
        assert before.returncode == 0, f"{before.stdout}\n{before.stderr}"

        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO agencies (id, name, slug, seat_limit) "
                         "VALUES (:id, 'Migration Agency', 'migration-agency', 3)"),
                    {"id": agency},
                )
                await conn.execute(
                    text("INSERT INTO clients (id, agency_id, name, domain, kind, "
                         "classification_status) VALUES (:id, :agency, 'C', 'example.test', "
                         "'prospect', 'pending')"),
                    {"id": client, "agency": agency},
                )
                for name, (versions, _expected) in HISTORIES.items():
                    await conn.execute(
                        text("INSERT INTO scans (id, client_id, agency_id, status, trigger, "
                             "formula_version, prompt_count, engine_result_count) VALUES "
                             "(:id, :client, :agency, 'succeeded', 'manual', 'v1', 0, 0)"),
                        {"id": scans[name], "client": client, "agency": agency},
                    )
                    for version in versions:
                        await conn.execute(
                            text("INSERT INTO scores (id, scan_id, status, formula_version, "
                                 "weights) VALUES (:id, :scan, 'insufficient_data', :version, "
                                 "CAST('{}' AS jsonb))"),
                            {"id": ids.new_id(ids.SCORE), "scan": scans[name],
                             "version": version},
                        )

            upgraded = _alembic(["upgrade", "head"], url)
            assert upgraded.returncode == 0, f"{upgraded.stdout}\n{upgraded.stderr}"

            later = ids.new_id(ids.SCAN)
            async with engine.begin() as conn:
                # What the instance still serving during the deploy writes: no `matcher_version`.
                await conn.execute(
                    text("INSERT INTO scans (id, client_id, agency_id, status, trigger, "
                         "formula_version, prompt_count, engine_result_count) VALUES "
                         "(:id, :client, :agency, 'succeeded', 'manual', 'v1', 0, 0)"),
                    {"id": later, "client": client, "agency": agency},
                )
                rows = await conn.execute(text("SELECT id, matcher_version FROM scans"))
                stamped = {scan_id: version for scan_id, version in rows}
        finally:
            await engine.dispose()

    assert {name: stamped[scan_id] for name, scan_id in scans.items()} == {
        name: expected for name, (_versions, expected) in HISTORIES.items()
    }
    assert stamped[later] == "pre-v7"
