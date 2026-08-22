"""Scoring endpoints — Epic 5.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import NotFound
from ..models import Scan, Score
from ..schemas.score import CompetitorScoreOut, ScoreDetailOut, ScoreOut
from ..services import scoring_runner

router = APIRouter(tags=["scores"])


async def _load_scan(db: Any, scan_id: str, agency_id: str) -> Scan:
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404, never 403 — confirming an id exists is a cross-tenant leak.
        raise NotFound(detail="No scan with that identifier.")
    return scan


def _detail(row: Score, competitors: list) -> ScoreDetailOut:  # noqa: ANN001
    out = ScoreDetailOut.model_validate(row)
    out.competitors = [
        CompetitorScoreOut(
            competitor_id=c.competitor_id, name=c.name,
            mention_rate=c.mention_rate, share_of_voice=c.share_of_voice,
            citation_strength=c.citation_strength,
        )
        for c in competitors
    ]
    return out


@router.post(
    "/scans/{scanId}/score",
    response_model=ScoreDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def compute_scan_score(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Compute and store the AI Visibility Score for a scan.

    **Makes no provider calls and costs nothing** — scoring is pure arithmetic
    over rows Epic 4 already persisted. Sentiment is read from storage, never
    re-classified (scoring-spec.md rule 4), which is what makes re-scoring
    reproducible.

    **Re-scoring INSERTS a new row** rather than overwriting. A Score is a dated
    claim under a named `formulaVersion`; overwriting would erase the history
    before/after reporting depends on.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    row, _computed, competitors = await scoring_runner.score_scan(db, scan)
    await db.commit()
    await db.refresh(row)
    return _detail(row, competitors)


@router.get("/scans/{scanId}/score", response_model=ScoreDetailOut)
async def get_scan_score(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """The most recent stored Score for a scan, with the competitor comparison.

    The comparison is recomputed from persisted rows on read rather than stored
    — it is a pure function of data already in the database, and a second copy
    could fall out of step with the first.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    row = await scoring_runner.latest_score(db, scan.id)
    if row is None:
        raise NotFound(detail="This scan has not been scored yet.")
    _, _computed, competitors = await scoring_runner.score_scan(db, scan, persist=False)
    return _detail(row, competitors)


@router.get("/scans/{scanId}/scores", response_model=list[ScoreOut])
async def list_scan_scores(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Every Score computed for a scan, newest first.

    The audit trail: which `formulaVersion` produced which composite, from which
    `inputsDigest`. Two rows sharing a digest but differing in composite mean the
    formula changed; differing digests mean the inputs did.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    rows = await scoring_runner.score_history(db, scan.id)
    return [ScoreOut.model_validate(r) for r in rows]
