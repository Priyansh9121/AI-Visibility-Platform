"""Alert schemas — Epic E.

Facts only (ip-safety.md #7): an enum, two scan ids, an engine key, timestamps,
and a sentence THIS CODEBASE wrote from its own numbers. No field here can hold
an engine's answer, and none is derived from one.
"""

from __future__ import annotations

from datetime import datetime

from .common import ApiModel


class AlertOut(ApiModel):
    """One detected change, with both scans it sits between."""

    id: str
    kind: str
    detail: str

    scan_id: str
    baseline_scan_id: str
    # When each of those scans ran. Carried so the feed can say "since the scan
    # of the 29th" without a second request per row — a feed of 50 alerts would
    # otherwise be 100 scan lookups.
    scanned_at: datetime
    baseline_scanned_at: datetime

    # Only sentiment alerts carry one. NULL is "not an engine-scoped finding",
    # never "unknown engine".
    engine: str | None = None

    created_at: datetime
    acknowledged_at: datetime | None = None


class AlertFeedOut(ApiModel):
    """A client's alerts, newest first."""

    client_id: str
    alerts: list[AlertOut]
    # Outstanding count, so the tab can badge without the caller filtering.
    unacknowledged: int

    # --- why the feed may be empty, which is not the same as "all clear" ----
    #
    # An alert needs a baseline scan at least `MIN_BASELINE_HOURS` older. Most
    # clients have exactly one scan and can therefore never have an alert, and
    # a client whose only other scan is a re-run from an hour ago is in the same
    # position. An empty feed that does not distinguish "nothing changed" from
    # "nothing could be compared" would read as reassurance the data cannot
    # support, so both figures are reported and the screen says which it is.
    scans_total: int
    # Scans that had a usable baseline. Zero means no comparison has ever been
    # possible for this client.
    scans_compared: int
    min_baseline_hours: int


class AcknowledgeAlertOut(ApiModel):
    """The alert after acknowledgement."""

    id: str
    acknowledged_at: datetime | None = None
