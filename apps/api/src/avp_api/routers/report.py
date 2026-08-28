"""Report endpoints — Epic 7, and the public share path from Epic 9.8.

Recorded in docs/api-contracts.md.

**This module holds the first unauthenticated read surface in the product.**
Everything else behind `/api/v1` resolves a session cookie to a Principal and
scopes every query to that agency. `GET /reports/{token}` does not: it is
reached by a stranger with a link. The rules that keeps safe are stated on the
route itself rather than assumed.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..errors import NotFound
from ..models import Scan
from ..schemas.report import ReportOut, ShareLinkOut
from ..services import report as report_service
from ..services import report_pdf as pdf_service
from ..services import share as share_service

router = APIRouter(tags=["reports"])


def _share_url(base_url: str, token: str) -> str:
    """The public URL for a token. One definition, used by every caller."""
    return f"{base_url.rstrip('/')}/share/{token}"


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _pdf_response(report: ReportOut) -> Response:
    """One place where a report becomes a downloadable file.

    Shared by the authenticated route and the share-token route so the two
    cannot drift into producing different documents from the same scan — which
    is the whole point of there being one `render_report_pdf`.

    The filename is derived from the subject's own name and reduced to ASCII
    word characters. A `Content-Disposition` header is a header: a name
    carrying a quote, a semicolon or a newline is a response-splitting
    opportunity, and a client name is caller-supplied data. There is no
    `filename*` / RFC 5987 form because there is nothing to encode once the
    name is ASCII, and offering both is a second thing to get wrong.
    """
    stem = _UNSAFE_FILENAME.sub(
        "-", report.subject.brand_name or report.subject.name
    ).strip("-.")[:60]
    filename = f"{stem or 'report'}-ai-visibility.pdf"
    return Response(
        content=pdf_service.render_report_pdf(report),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A report is a snapshot of one scan and its content can change when
            # the competitor set is corrected or the scan is re-scored, so it is
            # deliberately not cacheable by a shared proxy.
            "Cache-Control": "private, no-store",
        },
    )


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


@router.get(
    "/scans/{scanId}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_report_pdf(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Response:
    """The same report, as a PDF — Epic 9.14 (Epic 9 send path, slice 3).

    **A rendering target, not a second report.** It calls the same
    `build_report` the JSON route calls and renders what comes back; there is no
    second query, no second aggregation and no second set of numbers. A PDF
    assembled independently would eventually disagree with the page it was
    downloaded from, and the disagreement would reach a client.

    It degrades exactly as the web report does, because it is derived from the
    same payload: a null composite prints "Not scored" and never a zero,
    `insufficient_data` gets its own sentence, an excluded dimension prints its
    reason rather than a sub-score, a null competitor set says detection did not
    run instead of quietly dropping the beat, and an empty `actionItems` renders
    the deterministic fix derivation — the case api-contracts.md's note on that
    field describes.

    **No dependency was added to build this.** WeasyPrint's required `Pyphen` is
    GPL/LGPL/MPL, which ip-safety.md #6 blocks; React-PDF is a Node library and
    this is a Python process. See `services/pdf.py` for the full assessment.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        raise NotFound(detail="No scan with that identifier.")

    return _pdf_response(await report_service.build_report(db, scan))


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


# The `.pdf` route MUST be registered before `/reports/{token}`.
#
# Starlette's default path converter matches everything except `/`, so
# `/reports/abc.pdf` is a perfectly good match for `/reports/{token}` with a
# token of "abc.pdf" — and routes are tried in registration order, so whichever
# is declared first wins. Declared the other way round, every PDF request
# silently became a JSON request for a token nobody minted, and answered 404.
# `/scans/{scanId}/report.pdf` has no such problem because its final segment is
# a literal. A test asserts this ordering rather than trusting the file to stay
# in this shape.
@router.get(
    "/reports/{token}.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_public_report_pdf(
    db: DbDep,
    token: str = Path(),
) -> Response:
    """A report PDF, by share token. **No session required** — Epic 9.14.

    THE DECISION, STATED RATHER THAN DEFAULTED
    ------------------------------------------
    A stranger holding a share token CAN download the PDF, and the share page
    offers it. Three reasons, in order of weight:

    1. **It exposes nothing new.** The PDF carries strictly the facts
       `GET /reports/{token}` already serves to the same holder, rendered
       differently. Withholding it would not protect a single datum.
    2. **The send path is the point.** Epic 9.8 built this token because "the
       prospect a report is about has no account, and must not need one to read
       it". Granting the harder-to-forward form (a live URL) while withholding
       the easy one (a file they can keep) is backwards for a mechanism whose
       whole job is *send*.
    3. **A prospect wants a file.** The realistic next step for someone sent a
       report is forwarding it to a colleague or putting it in front of a
       budget holder, and a link that dies when they change jobs is worse for
       the agency than a PDF that does not.

    **What it costs, said plainly:** a downloaded PDF outlives any future
    revocation of the link. That cost is currently zero, because share links
    have no expiry and no revocation at all — recorded as known debt in
    api-contracts.md and explicitly out of scope for this epic. The PDF
    therefore takes away nothing the link does not already give away
    permanently. **When revocation ships, this is the route to revisit**, and
    that is the moment to decide whether a revoked link should stop serving
    files — not now, by pre-emptively refusing something that costs nothing yet.

    Every rule the JSON share route states holds here unchanged, because it is
    the same lookup: read-only, one code path, `404` for every rejection with no
    shape pre-check, and never a `401` — which would say "this token is real,
    authenticate to use it".

    **Errors:** `404` only.
    """
    scan = await share_service.scan_for_share_token(db, token)
    if scan is None:
        raise NotFound(detail="No report with that link.")

    return _pdf_response(await report_service.build_report(db, scan))


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
