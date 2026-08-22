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
from ..schemas.audit import TechnicalAuditOut
from ..services import audit_runner

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


def _ordered(audit) -> TechnicalAuditOut:  # noqa: ANN001
    out = TechnicalAuditOut.model_validate(audit)
    index = {key: i for i, key in enumerate(CHECK_ORDER)}
    out.checks.sort(key=lambda c: (index.get(c.check_key, len(CHECK_ORDER)), c.check_key))
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
    """
    scan = await _load_scan(db, scan_id, principal.agency_id)
    client = (
        await db.execute(select(Client).where(Client.id == scan.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFound(detail="The scan's client no longer exists.")

    audit, _outcome = await audit_runner.run_audit(db, scan, client)
    await db.commit()

    refreshed = await audit_runner.latest_audit(db, scan.id)
    return _ordered(refreshed or audit)


@router.get("/scans/{scanId}/audit", response_model=TechnicalAuditOut)
async def get_audit(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """The scan's technical audit, with every check in display order."""
    scan = await _load_scan(db, scan_id, principal.agency_id)
    audit = await audit_runner.latest_audit(db, scan.id)
    if audit is None:
        raise NotFound(detail="This scan has not been audited yet.")
    return _ordered(audit)
