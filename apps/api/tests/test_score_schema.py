"""Schema-level enforcement of docs/scoring-spec.md determinism rules.

The scoring ENGINE is Epic 5. These assert the storage guarantees it will
depend on, now, while the schema is still cheap to change.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from avp_api import ids
from avp_api.models import (
    DEFAULT_WEIGHTS,
    DIMENSION_KEYS,
    Agency,
    Client,
    Scan,
    Score,
    ScoreStatus,
)

SCORE_COLUMNS = ("composite", *DIMENSION_KEYS)


def test_weights_match_the_spec_and_sum_to_100() -> None:
    """product-spec.md §6."""
    assert DEFAULT_WEIGHTS == {
        "mention_rate": 30,
        "share_of_voice": 25,
        "citation_strength": 20,
        "sentiment": 15,
        "technical_foundation": 10,
    }
    assert sum(DEFAULT_WEIGHTS.values()) == 100
    assert tuple(DEFAULT_WEIGHTS) == DIMENSION_KEYS


@pytest.mark.parametrize("column_name", SCORE_COLUMNS)
def test_scores_are_numeric_not_float(column_name: str) -> None:
    """scoring-spec.md rule 3: Decimal, not float.

    A `double precision` column would reintroduce binary-float error at the
    storage boundary even if the calculation itself used Decimal.
    """
    column = Score.__table__.c[column_name]
    assert isinstance(column.type, Numeric), f"{column_name} is {column.type!r}, not NUMERIC"
    assert column.type.precision == 5
    assert column.type.scale == 2
    assert column.type.asdecimal is True


def test_formula_version_is_mandatory() -> None:
    """scoring-spec.md rule 5: version the formula."""
    assert Score.__table__.c.formula_version.nullable is False


def test_rescoring_inserts_rather_than_overwrites() -> None:
    """Uniqueness on (scan_id, formula_version), not scan_id alone.

    Epic 11's before/after ROI reporting is meaningless if changing weights
    silently rewrites historical scores.
    """
    uniques = {
        tuple(sorted(c.name for c in con.columns))
        for con in Score.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("formula_version", "scan_id") in uniques
    assert ("scan_id",) not in uniques


async def _make_scan(session: AsyncSession) -> str:
    agency = Agency(id=ids.new_id(ids.AGENCY), name="A", slug=f"a-{ids.new_id(ids.AGENCY)[-6:]}")
    session.add(agency)
    await session.flush()
    client = Client(
        id=ids.new_id(ids.CLIENT), agency_id=agency.id, name="C", domain="example.test"
    )
    session.add(client)
    await session.flush()
    scan = Scan(id=ids.new_id(ids.SCAN), client_id=client.id, agency_id=agency.id)
    session.add(scan)
    await session.flush()
    return scan.id


async def test_score_round_trips_as_decimal(session: AsyncSession) -> None:
    """The value read back is a Decimal, not a float."""
    scan_id = await _make_scan(session)
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=scan_id,
            status=ScoreStatus.SCORED,
            composite=Decimal("38.35"),
            mention_rate=Decimal("41.00"),
            share_of_voice=Decimal("22.00"),
            citation_strength=Decimal("15.00"),
            sentiment=Decimal("78.00"),
            technical_foundation=Decimal("64.00"),
            formula_version="v1",
            weights=dict(DEFAULT_WEIGHTS),
        )
    )
    await session.commit()

    stored = (await session.execute(select(Score).where(Score.scan_id == scan_id))).scalar_one()
    assert isinstance(stored.composite, Decimal)
    assert stored.composite == Decimal("38.35")
    # The exact decimal survives; a float would give 38.34999999999999857891...
    assert str(stored.composite) == "38.35"


async def test_insufficient_data_stores_null_not_zero(session: AsyncSession) -> None:
    """An unrunnable scan must never render to a client as a score of 0."""
    scan_id = await _make_scan(session)
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=scan_id,
            status=ScoreStatus.INSUFFICIENT_DATA,
            composite=None,
            reason_code="NO_PROMPTS",
            formula_version="v1",
            weights=dict(DEFAULT_WEIGHTS),
        )
    )
    await session.commit()

    stored = (await session.execute(select(Score).where(Score.scan_id == scan_id))).scalar_one()
    assert stored.composite is None
    assert stored.status is ScoreStatus.INSUFFICIENT_DATA


async def test_scored_row_without_a_composite_is_rejected(session: AsyncSession) -> None:
    """The CHECK constraint keeps status and composite consistent."""
    scan_id = await _make_scan(session)
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=scan_id,
            status=ScoreStatus.SCORED,
            composite=None,  # contradicts status
            formula_version="v1",
            weights=dict(DEFAULT_WEIGHTS),
        )
    )
    with pytest.raises(IntegrityError, match="composite_matches_status"):
        await session.commit()
    await session.rollback()


async def test_out_of_range_score_is_rejected(session: AsyncSession) -> None:
    """scoring-spec.md: clamp AND raise — never silently store a bad score."""
    scan_id = await _make_scan(session)
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=scan_id,
            status=ScoreStatus.SCORED,
            composite=Decimal("140.00"),
            formula_version="v1",
            weights=dict(DEFAULT_WEIGHTS),
        )
    )
    with pytest.raises(IntegrityError, match="composite_range"):
        await session.commit()
    await session.rollback()


async def test_same_scan_two_formula_versions_coexist(session: AsyncSession) -> None:
    scan_id = await _make_scan(session)
    for version, composite in (("v1", "38.35"), ("v2", "42.10")):
        session.add(
            Score(
                id=ids.new_id(ids.SCORE),
                scan_id=scan_id,
                status=ScoreStatus.SCORED,
                composite=Decimal(composite),
                formula_version=version,
                weights=dict(DEFAULT_WEIGHTS),
            )
        )
    await session.commit()

    rows = (await session.execute(select(Score).where(Score.scan_id == scan_id))).scalars().all()
    assert {r.formula_version for r in rows} == {"v1", "v2"}


async def test_weights_are_stored_per_score_row(session: AsyncSession) -> None:
    """A stored score stays self-describing after DEFAULT_WEIGHTS changes."""
    scan_id = await _make_scan(session)
    custom = {**DEFAULT_WEIGHTS, "mention_rate": 40, "technical_foundation": 0}
    session.add(
        Score(
            id=ids.new_id(ids.SCORE),
            scan_id=scan_id,
            status=ScoreStatus.SCORED,
            composite=Decimal("50.00"),
            formula_version="v1-industry-dental",
            weights=custom,
            excluded_dimensions=["sentiment"],
            degradation_flags=["citation_authority_unavailable"],
        )
    )
    await session.commit()

    stored = (await session.execute(select(Score).where(Score.scan_id == scan_id))).scalar_one()
    assert stored.weights["mention_rate"] == 40
    assert stored.excluded_dimensions == ["sentiment"]
    assert stored.degradation_flags == ["citation_authority_unavailable"]
