"""Report endpoints — Epic 7, and the public share path from Epic 9.8.

Recorded in docs/api-contracts.md.

**This module holds the first unauthenticated read surface in the product.**
Everything else behind `/api/v1` resolves a session cookie to a Principal and
scopes every query to that agency. `POST /reports/shared` does not: it is
reached by a stranger with a link. The rules that keeps safe are stated on the
route itself rather than assumed.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select

from ..deps import DbDep, PrincipalDep, SettingsDep
from ..errors import FailedScanNotShareable, NotFound, ScanNotExportable
from ..models import Scan, ScanStatus
from ..schemas.report import ReportOut, SharedReportRequest, ShareLinkOut
from ..services import report as report_service
from ..services import report_csv as csv_service
from ..services import report_pdf as pdf_service
from ..services import share as share_service
from ..services.links import share_link

router = APIRouter(tags=["reports"])


def _share_url(base_url: str, token: str) -> str:
    """The public URL for a token. One definition, used by every caller."""
    return share_link(base_url, token)


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
            # the scan is re-scored (a competitor list saved since moves nothing in it:
            # a scan's own set never changes once the scan has started), so it is
            # deliberately not cacheable by a shared proxy.
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/scans/{scanId}/report", response_model=ReportOut)
async def get_report(
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
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

    **`csvExportAvailable`** says whether this report may offer "Download figures (CSV)"
    (`GET /scans/{scanId}/report.csv`): the `REPORT_CSV_ENABLED` setting is on and this scan's
    status is one the file is made for. It is set here and nowhere else, so it is false on the
    PDF and on every public route; the web draws the link from it alone.

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

    report = await report_service.build_report(db, scan)
    report.csv_export_available = csv_service.is_available(settings.report_csv_enabled, scan.status)
    return report


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
    GPL/LGPL/MPL, which the dependency-licensing rule blocks; React-PDF is a Node library and
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


def _csv_filename(report: ReportOut) -> str:
    """`<client>-ai-visibility-<date>.csv`, with `ai-visibility-<date>.csv` when no client is left.

    The client's name is reduced exactly as `_pdf_response` reduces it, by the same
    `_UNSAFE_FILENAME`: a client's name is caller-supplied data and a `Content-Disposition` header
    is a header, so a run of characters other than `A-Za-z0-9._-` (a quote, a semicolon, a line
    break, a space) becomes a hyphen, a hyphen or a dot at either end is dropped, and the stem is at
    most 60 characters. What follows it is the fixed words and the scan's date
    (`report_csv.scan_date`, the one the file's own `Scan date` row gives): nothing else an
    operator typed is in it. A name with nothing left after the reduction leaves no stem, and the
    file is then named for the fixed words and the date alone: the date stays, so two scans of two
    such clients are two names in a download list.
    """
    stem = _UNSAFE_FILENAME.sub(
        "-", report.subject.brand_name or report.subject.name
    ).strip("-.")[:60]
    if not stem:
        return f"ai-visibility-{csv_service.scan_date(report)}.csv"
    return f"{stem}-ai-visibility-{csv_service.scan_date(report)}.csv"


def _csv_response(report: ReportOut) -> Response:
    """One place where a report's figures become a downloadable file.

    The name is `_csv_filename`'s. `Cache-Control: private, no-store`, as the PDF and the public
    report send: a signed-in response is for the person who asked, and the figures change when a
    scan is re-scored.
    """
    return Response(
        content=csv_service.render_report_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_csv_filename(report)}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/scans/{scanId}/report.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def get_report_csv(
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    scan_id: str = Path(alias="scanId"),
) -> Response:
    """The report's figures as one CSV — F3, off unless `REPORT_CSV_ENABLED` is on.

    **A rendering target, not a second report.** It calls the same `build_report` the JSON route
    calls and writes what comes back (`services/report_csv.py`): no second query, no second
    aggregation, and nothing the signed-in report does not already show. One long table, one
    header row (`Section, Engine, Prompt, Item, Measure, Value`) and plain labels with their
    units; the module says what each of its six sections holds (the first says whose scan it is,
    its date, whether it is complete and its overall score) and what it leaves out.

    **There is no token route, and there will not be one here.** A share link opens the report and
    its PDF; it does not open this. The route is the agency's, scoped to the agency as the report
    is, and `csvExportAvailable` is false on every public response.

    **Off, it is not there.** With the setting off a signed-in caller gets a `404` with the body
    an unknown path gets, whatever the scan, so it says nothing about the scan either. A signed-out
    caller gets the `401` every signed-in route here gives.

    **A scan with no figures to give is `409`** `/problems/scan-not-exportable`, with one plain
    sentence: a FAILED scan (its score is withheld, as for sharing), a QUEUED or RUNNING one (its
    figures are not final). The agency check runs first, so another agency's scan is a `404`
    in every status and never a `409` that says how it ended. SUCCEEDED and PARTIAL are exported.

    **Excel-safe:** a UTF-8 byte-order mark, and a leading apostrophe on any cell that begins
    with `=`, `+`, `-`, `@`, a tab, a carriage return or a full-width `＝＋－＠` (white space in
    front does not hide it), and straight after a `;`, a tab or a line break inside a cell (each
    starts a new cell or a new record in a spreadsheet), so a name somebody typed cannot become a
    formula. The file is named `<client>-ai-visibility-<date>.csv`, the client reduced as the
    PDF's name is.

    **Errors:** `401`, `404` (off, or unknown scan, or another agency's), `409` (no figures yet).
    """
    if not settings.report_csv_enabled:
        # The body an unknown path gets (Starlette's own detail), so off is not a tell.
        raise NotFound(detail="Not Found")

    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404, never 403, and before any word about the scan's status.
        raise NotFound(detail="No scan with that identifier.")

    refusal = csv_service.refusal_detail(scan.status)
    if refusal is not None:
        raise ScanNotExportable(detail=refusal)

    return _csv_response(await report_service.build_report(db, scan))


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

    **`200`, not `201`, and idempotent.** While the scan has a live link,
    calling again returns that link exactly as it is: the same token and the
    same `expiresAt`, with nothing written. Every extra token would be a URL
    nobody is tracking, so the second call mints nothing and does not claim
    to.

    **A press on a live link does not move its end** — Epic 9.21, as changed by
    checklist 3.28 (the founder's decision). The share bar says links last 30
    days from when they are created, and "The link works until ..." names the
    end the link was given, so both stay true of the link already sent. Only
    when no link is live does a press make one: a scan never shared, a
    withdrawn link, or one that has ended. That is a NEW token with a fresh 30
    days. An ended token is replaced, not revived, so the address that stopped
    working stays dead on both public routes. `services/share.py` carries the
    argument in full.

    **A FAILED scan cannot be shared** (3.24): `409`
    `/problems/failed-scan-not-shareable`. Its score is never shown, so there
    is no report to send. PARTIAL and every other status are unchanged. The
    status is read again under the row lock, so a scan that fails while this
    request is in flight is refused the same way and nothing is written. A link
    minted before the scan failed stays on the row, so it can still be
    withdrawn, but it no longer opens the report (3.29): the public routes
    refuse it like a withdrawn one.

    **Errors:** `401`, `404` (unknown scan, or another agency's), `409`
    (failed scan).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        raise NotFound(detail="No scan with that identifier.")
    if scan.status is ScanStatus.FAILED:
        raise FailedScanNotShareable(detail=share_service.FAILED_SCAN_NOT_SHAREABLE_DETAIL)

    token, expires_at = await share_service.get_or_create_share_token(db, scan)
    # Commit before returning the URL. The operator's next action is to paste
    # this link somewhere it cannot be un-pasted, so the token must be durable
    # before it is handed out — returning a URL backed by an uncommitted row
    # would hand out a link that 404s.
    await db.commit()
    return ShareLinkOut(
        scan_id=scan.id,
        token=token,
        url=_share_url(settings.public_web_base_url, token),
        expires_at=expires_at,
    )


@router.delete(
    "/scans/{scanId}/share",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_share_link(
    principal: PrincipalDep,
    db: DbDep,
    scan_id: str = Path(alias="scanId"),
) -> Response:
    """Take the public link down — Epic 9.21. The inverse of the POST above.

    **`DELETE` on the same path the mint uses**, because the thing being
    removed is the resource that path names. The alternative shapes — a
    `POST .../share/revoke`, or a `PATCH` with a null token — either invent a
    second noun for one capability or make "unshare" reachable from an endpoint
    whose job is to describe a scan.

    **`204`, and idempotent.** Revoking a scan that was never shared is not an
    error: the caller asked for it not to be readable, and it is not. Answering
    `404` there would report on something they did not ask about, and `409`
    would invite a retry loop over a state that is already correct. Nothing is
    returned because there is nothing left to describe.

    **After this, the token is gone for both read routes**, JSON and PDF, on
    the same code path as a token that never existed — `scan_for_share_token`
    is where the rule lives, so neither route can drift into treating a revoked
    link as its own kind of wrong. A stranger holding the old URL gets exactly
    the 404 an enumerator gets.

    **What it does not reach: a PDF already downloaded.** Nothing can. The link
    stops serving; a file on someone's disk is theirs. Said here and on the PDF
    route rather than left for a reader to wonder about.

    **Errors:** `401`, `404` (unknown scan, or another agency's).
    """
    scan = (
        await db.execute(
            select(Scan).where(Scan.id == scan_id, Scan.agency_id == principal.agency_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        # 404, never 403 — the same cross-tenant rule as every sibling route.
        raise NotFound(detail="No scan with that identifier.")

    await share_service.revoke_share_token(db, scan)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# The share token is in the POST body, never the path, so it cannot reach a
# request log. `GET /reports/{token}` and `GET /reports/{token}.pdf` are gone
# on purpose: keeping them would keep a token-in-path surface.
@router.post(
    "/reports/shared/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_public_report_pdf(
    db: DbDep,
    payload: SharedReportRequest,
) -> Response:
    """A report PDF, by share token. **No session required** — Epic 9.14.

    THE DECISION, STATED RATHER THAN DEFAULTED
    ------------------------------------------
    A stranger holding a share token CAN download the PDF, and the share page
    offers it. Three reasons, in order of weight:

    1. **It exposes nothing new.** The PDF carries strictly the facts
       `POST /reports/shared` already serves to the same holder, rendered
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

    **What it costs, said plainly: a downloaded PDF outlives revocation.**
    Epic 9.14 accepted that cost when it was zero — links had no expiry and no
    revocation, so the file took away nothing the link did not already give
    away permanently — and marked this "the route to revisit when revocation
    ships". Epic 9.21 shipped it, so this is that revisit, and the decision is
    recorded rather than inherited.

    **A revoked or expired link stops serving files immediately**, on the same
    lookup as the JSON route: `scan_for_share_token` returns nothing and this
    route 404s like any other miss. So does a link whose scan FAILED (3.29): the
    token is kept for the agency to withdraw, but it opens nothing. What
    revocation cannot do is recall a copy
    already on someone's disk, and nothing can — the alternative would be
    serving a watermarked or phone-home document, which is a different product
    with different privacy properties. So the residual gap is real, bounded,
    and named: revocation governs the LINK from the moment it is invoked, never
    a file already downloaded.

    Every rule the JSON share route states holds here unchanged, because it is
    the same lookup: read-only, one code path, `404` for every rejection (the body's
    token is bounded 1..512 by the schema, a `422` alike for real and fake
    tokens, and no other shape check runs), and never a `401` — which would say "this token is real,
    authenticate to use it".

    **Errors:** `404` only.
    """
    scan = await share_service.scan_for_share_token(db, payload.token)
    if scan is None:
        raise NotFound(detail="No report with that link.")

    return _pdf_response(await report_service.build_public_report(db, scan))


@router.post("/reports/shared", response_model=ReportOut)
async def get_public_report(
    db: DbDep,
    payload: SharedReportRequest,
    response: Response,
) -> Any:
    """A report, by share token. **No session required** — Epic 9.8.

    This is the send path Epic 9's acceptance criterion needs: the prospect a
    report is about has no account, and must not need one to read it.

    **The token is the whole credential**, so the rules are narrow and worth
    stating:

    * **Read-only.** There is no sibling route that mutates anything by token.
      A holder cannot edit the competitor set, re-run the scan, or reach any
      other scan — the token resolves to exactly one row.
    * **`404` for every rejection, with one code path.** An unknown, revoked or
      expired token, and one whose scan FAILED (3.29), is indistinguishable in
      both status and body. The only pre-check is the body's length bound (`422`
      for an empty or over-512 token); it says nothing about whether any token
      is real. Every plausible guess pays for the same index lookup.
    * **The token is in the POST body, not the path**, so it stays out of
      request logs.
    * **No `401`, ever.** A `401` would say "this token is real, authenticate to
      use it", which is precisely the bit an enumerator wants.
    * **Same projection as the authenticated route.** `build_report` is reused
      rather than reimplemented, so the facts-only guarantee `test_facts_only.py`
      sweeps over this module covers this response too — a second assembly path
      would be a second place for a snippet to slip in. One field is blanked:
      `scanErrorCode`, which says why a scan is partial or failed and is for the
      agency alone (`build_public_report`).

    **Errors:** `404` only.
    """
    response.headers["Cache-Control"] = "private, no-store"
    scan = await share_service.scan_for_share_token(db, payload.token)
    if scan is None:
        # Identical to every other miss. Never "malformed", never "expired",
        # never 401 — each of those is a bit of information about a guess.
        raise NotFound(detail="No report with that link.")

    return await report_service.build_public_report(db, scan)
