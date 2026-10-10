"""Technical audit endpoints — Epic 6.

Every endpoint here is recorded in docs/api-contracts.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep
from ..errors import NotFound
from ..models import Client, Scan
from ..models.technical_audit import AuditStatus
from ..schemas.audit import TechnicalAuditOut
from ..services import audit_runner, technical_audit
from ..services.browser_slot import one_run_per_agency
from ..services.failed_scan import score_is_withheld, withhold_technical_foundation

router = APIRouter(tags=["audits"])

# Stable display order: crawlability first (it gates everything), then markup,
# then content, then the lab measurements. Sorting by key alphabetically would
# interleave them and make a report harder to read than it needs to be.
CHECK_ORDER = [
    "site_reachable", "indexable", "robots_txt_present", "sitemap_present",
    "canonical_present", "schema_present", "schema_business_entity", "schema_faq",
    "schema_product_or_service", "meta_title", "meta_description", "open_graph_tags",
    "single_h1", "content_freshness", "cwv_lcp", "cwv_cls", "cwv_inp",
]


async def _load_scan(db: Any, scan_id: str, agency_id: str) -> Scan:
    scan = (
        await db.execute(select(Scan).where(Scan.id == scan_id, Scan.agency_id == agency_id))
    ).scalar_one_or_none()
    if scan is None:
        # 404 not 403 — confirming an id exists leaks across tenants.
        raise NotFound(detail="No scan with that identifier.")
    return scan


def _ordered(audit, *, scored: bool) -> TechnicalAuditOut:  # noqa: ANN001
    out = TechnicalAuditOut.model_validate(audit)
    index = {key: i for i, key in enumerate(CHECK_ORDER)}
    out.checks.sort(key=lambda c: (index.get(c.check_key, len(CHECK_ORDER)), c.check_key))

    # The four weighted parts of the sub-score, recomputed from the stored row —
    # Epic 9.22. Same function the score itself was built from, so the two
    # cannot disagree; nothing is re-crawled and nothing is written. Computed for
    # OK and PARTIAL alike: a partial audit was read and scored with a component
    # excluded (its reason is in `excluded_components`), so it has measured
    # components and redistributed weights to show. Skipped only for FAILED,
    # which was not read, not scored and has nothing to show.
    if audit.status is not AuditStatus.FAILED:
        components, _excluded = technical_audit.compute_components(
            technical_audit.signals_from_row(audit)
        )
        out.components = components
        out.component_weights = technical_audit.effective_weights(components)
    # The Technical Foundation sub-score is a score input, and a FAILED scan shows no
    # score (3.30, extending 3.24; the report does the same through the same helper). The
    # component scores that add up to it go with it, after the weights above were worked
    # out, so `component_weights` stays. Every other field is a fact about the site and
    # stays. The stored row is not touched.
    if not scored:
        withhold_technical_foundation(out)
    return out


@router.post(
    "/scans/{scanId}/audit",
    response_model=TechnicalAuditOut,
    status_code=status.HTTP_201_CREATED,
)
async def run_audit(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Crawl the client's site and record structural signals plus per-check verdicts.

    Makes **no model or search calls** — this is a page load plus two side
    fetches (robots.txt, sitemap.xml). Cost is bandwidth and a few seconds.

    Once an audit exists, re-scoring the scan includes Technical Foundation
    instead of excluding it as `NOT_YET_MEASURED`.

    One audit per scan, refreshed on re-run: a scan is a point-in-time claim
    about a site, so a second audit of the same scan is a correction rather than
    a new observation.

    **A FAILED scan's audit carries no `technicalFoundation` and no `components`**
    (3.30, extending 3.24): the sub-score is a score input and a failed scan shows no
    score, and the component scores add up to it, so `technicalFoundation` is `null` and
    `components` is `{}`. `componentWeights` (this audit's own weighting, with an
    unmeasured component's share redistributed) and every fact about the site (the
    checks, the signals) are returned as before, and the stored row keeps its values.
    The agency check comes first, so another agency's failed scan is the same `404` as an
    unknown one. The scan's status is read again after the crawl, so a scan that fails
    while the audit runs is blanked too. Every other status is unchanged.

    **One site read per agency at a time, and four at once** (2026-10-02), shared with
    `POST /clients` and `POST /clients/{clientId}/reclassify`: while the agency's other
    read is queued or running, or four agencies' reads are, this is a `429`. Checked
    after the scan is found, so another agency's scan is still a `404`. A read that
    waits more than a minute for the browser is refused with a `429` too, and the audit
    already stored for the scan is kept. A scan's own audit, part of the scan, is not
    one of these reads and is never refused: it waits up to two minutes for the
    browser, and if the browser is not free by then the audit is recorded as `failed`
    with `errorCode: BROWSER_BUSY` and the scan goes on.

    **Errors:** `401`; `404` (unknown scan, or another agency's); `429 rate-limited`
    (with `Retry-After: 60`).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    # The scan's client in the scan's own agency: a scan row that names another agency's client
    # finds none, and nothing is crawled.
    client = (
        await db.execute(
            select(Client).where(Client.id == scan.client_id, Client.agency_id == scan.agency_id)
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="The scan's client no longer exists.")

    # `refuse_when_busy`: a browser that stays taken for the whole wait is a 429 and
    # nothing is written, so the audit already stored for this scan is kept.
    async with one_run_per_agency(principal.agency_id):
        audit, _outcome = await audit_runner.run_audit(
            db, scan, client, refuse_when_busy=True
        )
    await db.commit()

    # The crawl took seconds, and the scan can have failed in that time (its deadline, or
    # the reaper). `scan` was loaded before it and the commit does not expire it
    # (`expire_on_commit=False`), so its status is the one from before the crawl. Read it
    # again before deciding what to blank.
    await db.refresh(scan)

    refreshed = await audit_runner.latest_audit(db, scan.id)
    return _ordered(refreshed or audit, scored=not score_is_withheld(scan))


@router.get("/scans/{scanId}/audit", response_model=TechnicalAuditOut)
async def get_audit(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """The scan's technical audit, with every check in display order.

    **A FAILED scan's audit carries no `technicalFoundation` and no `components`**
    (3.30, extending 3.24), as on `POST`: `null` and `{}`, with `componentWeights` (this
    audit's own weighting, with an unmeasured component's share redistributed) and every
    fact about the site returned as before, the stored row not changed, and every other
    status unchanged.

    **Errors:** `401`, `404` (unknown scan, another agency's, or not yet audited).
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    audit = await audit_runner.latest_audit(db, scan.id)
    if audit is None:
        raise NotFound(detail="This scan has not been audited yet.")
    return _ordered(audit, scored=not score_is_withheld(scan))
