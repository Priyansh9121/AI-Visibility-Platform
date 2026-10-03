"""Dashboard schemas.

Epic 1's acceptance criterion ends at "see an empty dashboard", so this carries
only what an empty state needs: identity, seat usage, counts, and a scan list
that is legitimately empty. The real scan UI is Epic 2+.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..models import ScanStatus
from .auth import AgencyOut, SeatUsageOut
from .common import ApiModel


class ScanSummaryOut(ApiModel):
    id: str
    client_id: str
    client_name: str
    client_domain: str
    status: ScanStatus
    # Why a partial or failed scan ended as it did: `scans.error_code`, OUR OWN
    # constant (`scan_executor`, `scan_runner`), never a vendor message. Null when
    # there is nothing to say. The web turns (status, code) into a sentence and
    # never prints the code; `error_detail` is deliberately not projected.
    error_code: str | None = None
    # Null until Epic 5 scores the scan — and null also means INSUFFICIENT_DATA,
    # which the UI must render as "—", never as zero.
    composite_score: Decimal | None = None
    created_at: datetime
    finished_at: datetime | None = None


class DashboardOut(ApiModel):
    agency: AgencyOut
    seats: SeatUsageOut
    client_count: int
    scan_count: int
    recent_scans: list[ScanSummaryOut]
    # True when the agency has never run a scan. Lets the frontend show the
    # onboarding path rather than an empty table.
    is_empty: bool

    # --- the getting-started checklist (Epic 19) ---------------------------
    #
    # Three more facts about the account, in the same shape as `client_count`
    # and `scan_count`, so the screen can DERIVE whether each step is done
    # rather than the server storing per-step flags. `recent_scans` is a
    # bounded page and cannot answer "has any scan ever been scored" once the
    # scored one falls off it, so the two counts are queried over the whole
    # agency. `getting_started_dismissed` is the one fact that is stored, and
    # the only one that is a decision rather than a state.
    scored_scan_count: int
    # Scans whose share link would open right now: the public lookup's own test, so a link
    # past its end and a failed scan's are not in it (checklist 3.42). Not scans that ever
    # had a link. A cancelled scan's link opens, so it is in it.
    shared_scan_count: int
    getting_started_dismissed: bool
