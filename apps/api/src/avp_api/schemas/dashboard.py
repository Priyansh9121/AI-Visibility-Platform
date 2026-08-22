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
