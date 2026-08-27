"""Report endpoints — Epic 7, and the public share path from Epic 9.8.

Recorded in docs/api-contracts.md.

**This module holds the first unauthenticated read surface in the product.**
Everything else behind `/api/v1` resolves a session cookie to a Principal and
scopes every query to that agency. `GET /reports/{token}` does not: it is
reached by a stranger with a link. The rules that keeps safe are stated on the
route itself rather than assumed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..errors import NotFound
from ..models import Scan
from ..schemas.report import ReportOut, ShareLinkOut
from ..services import report as report_service
from ..services import share as share_service

router = APIRouter(tags=["reports"])


def _share_url(base_url: str, token: str) -> str:
    """The public URL for a token. One definition, used by every caller."""
    return f"{base_url.rstrip('/')}/share/{token}"


@router.get("/scans/{scanId}/report", response_model=ReportOut)
async def get_report(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Everything the narrative report needs, in one response.

    **Presents; does not compute.** Score, competitors and audit are read from
    the rows Epics 3-6 wrote. The only new numbers are aggregates — citation
    counts per domain, mention shares, per-engine coverage — and each is a
    count over stored facts.

    Degraded data is reported, never smoothed over. `score` is null when the
    scan was never scored, and a score with `status: "insufficient_data"` is a
    different thing again: an unrunnable scan is never rendered as a low score.
    `competitorSet` is null when detection never ran, and `audit` is null when
    the site was never audited — in each case the report says so rather than
    quietly omitting a beat.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404, never 403 — confirming an id exists is a cross-tenant leak.
        raise NotFound(detail="No scan with that identifier.")

    return await report_service.build_report(db, scan)


@router.post(
    "/scans/{scanId}/share",
    response_model=ShareLinkOut,
    status_code=status.HTTP_200_OK,
)
async def create_share_link(
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    scan_id: str = Path(alias="scanId"),
) -> Any:
    """Mint (or return) the public link for this scan's report — Epic 9.8.

    **An explicit action, not a side effect of running a scan.** A scan is a
    private measurement until an agency decides otherwise; publishing every
    report at creation time and relying on the URL being unknown would make
    that decision for them. This is the moment they make it.

    **`200`, not `201`, and idempotent.** Calling twice returns the same token
    rather than minting a second live link to the same report — there is no
    revocation, so every extra token would be a URL nobody is tracking. The
    second call creates nothing, so it does not claim to.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        raise NotFound(detail="No scan with that identifier.")

    token = await share_service.get_or_create_share_token(db, scan)
    # Commit before returning the URL. The operator's next action is to paste
    # this link somewhere it cannot be un-pasted, so the token must be durable
    # before it is handed out — returning a URL backed by an uncommitted row
    # would hand out a link that 404s.
    await db.commit()
    return ShareLinkOut(
        scan_id=scan.id,
        token=token,
        url=_share_url(settings.public_web_base_url, token),
    )


@router.get("/reports/{token}", response_model=ReportOut)
async def get_public_report(
    db: DbDep,
    token: str = Path(),
) -> Any:
    """A report, by share token. **No session required** — Epic 9.8.

    This is the send path Epic 9's acceptance criterion needs: the prospect a
    report is about has no account, and must not need one to read it.

    **The token is the whole credential**, so the rules are narrow and worth
    stating:

    * **Read-only.** There is no sibling route that mutates anything by token.
      A holder cannot edit the competitor set, re-run the scan, or reach any
      other scan — the token resolves to exactly one row.
    * **`404` for every rejection, with one code path.** A malformed token, an
      unknown token and a well-formed miss are indistinguishable in both status
      and body. There is no shape or length pre-check, deliberately: rejecting
      an implausible token faster than a plausible one is a timing oracle that
      makes enumeration cheaper. Every guess pays for the same index lookup.
    * **No `401`, ever.** A `401` would say "this token is real, authenticate to
      use it", which is precisely the bit an enumerator wants.
    * **Same projection as the authenticated route.** `build_report` is reused
      rather than reimplemented, so the facts-only guarantee `test_ip_safety.py`
      sweeps over this module covers this response too — a second assembly path
      would be a second place for a snippet to slip in.

    **Errors:** `404` only.
    """
    scan = await share_service.scan_for_share_token(db, token)
    if scan is None:
        # Identical to every other miss. Never "malformed", never "expired",
        # never 401 — each of those is a bit of information about a guess.
        raise NotFound(detail="No report with that link.")

    return await report_service.build_report(db, scan)
