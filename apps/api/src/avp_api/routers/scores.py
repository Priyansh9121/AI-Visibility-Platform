"""Scoring endpoints — Epic 5.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import Conflict, NotFound
from ..models import Scan, Score
from ..schemas.score import CompetitorScoreOut, ScoreDetailOut, ScoreOut
from ..services import scoring_runner
from ..services.failed_scan import score_is_withheld
from ..services.scoring import ScoreResult

router = APIRouter(tags=["scores"])

# A FAILED scan has no score that is ever shown (checklist 3.24: the dashboard and
# the report; 3.30: these routes). The first sentence is the one the report and
# its PDF already use. A scan whose deadline came after scoring keeps its Score
# rows. They are neither changed nor deleted (scoring-spec.md rule 5), only not
# read here, and nothing is computed for the scan.
FAILED_NO_SCORE = "This scan failed, so it has no score."
FAILED_NOT_SCORABLE = "This scan failed, so it cannot be scored."


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


def _detail(
    row: Score, computed: ScoreResult, previous_versions: list[str] | None = None
) -> ScoreDetailOut:
    """The stored row plus what is derived on read: the comparison and the
    subject's average position, both pure functions of persisted rows."""
    out = ScoreDetailOut.model_validate(row)
    out.previous_formula_versions = previous_versions or []
    out.average_position = computed.average_position
    out.position_count = computed.position_count
    out.competitors = [
        CompetitorScoreOut(
            competitor_id=c.competitor_id, name=c.name,
            mention_rate=c.mention_rate, share_of_voice=c.share_of_voice,
            citation_strength=c.citation_strength,
            average_position=c.average_position, position_count=c.position_count,
        )
        for c in computed.competitors
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

    **A FAILED scan is not scored** (3.30, extending 3.24): `409`
    `/problems/conflict`, and no Score row is written or refreshed. A failed scan
    has no score that is ever shown, and the rows it kept from before it failed
    are neither changed nor deleted. The agency check comes first, so another
    agency's failed scan is the same `404` as an unknown one. The status is read
    under a row lock held until the score is committed, so a scan that fails
    while this runs either fails first (`409`) or is scored first and fails
    after. Every other status is scored as before.

    **Errors:** `401`, `404` (unknown scan, or another agency's), `409` (failed
    scan).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    # The status the check below reads is the one now, not the one `_load_scan` saw: a
    # deadline or the reaper can end the scan between the two, and a Score row written
    # after that is a score for a failed scan. `FOR UPDATE` makes a status write wait
    # for this transaction, and `populate_existing` overwrites the copy `_load_scan`
    # left in the session, which a locked read otherwise returns as it was. Held from
    # here to the commit below: database reads, one insert or update, and no outbound
    # call (scoring is arithmetic over stored rows).
    scan = (
        await db.execute(
            select(Scan)
            .where(Scan.id == scan.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if score_is_withheld(scan):
        # Before score_scan, which would compute and write, even if only to refresh
        # the row the scan already has.
        raise Conflict(detail=FAILED_NOT_SCORABLE)
    row, computed, _competitors = await scoring_runner.score_scan(db, scan)
    await db.commit()
    await db.refresh(row)
    return _detail(
        row, computed,
        await scoring_runner.prior_formula_versions(db, scan.id, row.formula_version),
    )


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

    **A FAILED scan has no score** (3.30, extending 3.24): the same `404` as a scan
    not yet scored, with the detail "This scan failed, so it has no score.",
    whatever rows the scan kept. They are not read, changed or deleted.

    **Errors:** `401`; `404` (unknown scan, another agency's, not yet scored, or
    failed).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    if score_is_withheld(scan):
        raise NotFound(detail=FAILED_NO_SCORE)
    row = await scoring_runner.latest_score(db, scan.id)
    if row is None:
        raise NotFound(detail="This scan has not been scored yet.")
    _, computed, _competitors = await scoring_runner.score_scan(db, scan, persist=False)
    return _detail(
        row, computed,
        await scoring_runner.prior_formula_versions(db, scan.id, row.formula_version),
    )


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

    **A FAILED scan lists none** (3.30, extending 3.24): `[]`, the same `200` as a
    scan not yet scored, whatever rows the scan kept. They are not read, changed
    or deleted.
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    if score_is_withheld(scan):
        return []
    rows = await scoring_runner.score_history(db, scan.id)
    return [ScoreOut.model_validate(r) for r in rows]
