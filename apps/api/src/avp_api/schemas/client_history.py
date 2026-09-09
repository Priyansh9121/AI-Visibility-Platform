"""A client's scan history — Epic 9.20.

Every field is a fact or a count over facts. There is no text-bearing field
here at all, and nothing upstream to fill one with: this reads citation
domains, brand names and numbers, which the facts-only rule permits explicitly, and
the cited page's text is never read, stored or shown.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import ApiModel


class HistoryCitedDomainOut(ApiModel):
    """One domain and how often this scan's answers cited it.

    Not the report's `CitedDomainOut`. That one carries a `sample_url` and is
    ranked and TRUNCATED for display; this is the full per-scan tally a trend
    needs, so borrowing that shape would mean inheriting a display cap into a
    time series.
    """

    domain: str
    citations: int
    cites_subject: bool
    # Set when the domain belongs to a rival in this scan's competitor set.
    competitor_name: str | None = None


class HistoryCompetitorOut(ApiModel):
    """One rival's comparable figures for one scan.

    No composite, for the reason `ReportCompetitorOut` gives at length:
    sentiment is classified toward the subject only and technical foundation is
    the subject's own site, so 25% of the weight has no per-competitor input and
    a rival "composite" would not be comparable to the subject's.
    """

    competitor_id: str
    name: str
    mention_rate: Decimal | None = None
    share_of_voice: Decimal | None = None
    citation_strength: Decimal | None = None


class HistorySentimentOut(ApiModel):
    """How one engine described the subject across one scan's answers — Epic A.

    COUNTS, not a rate, and the four buckets are exhaustive over that engine's
    answers for the scan. A rate would have to pick a denominator, and the only
    honest one here is "answers where the subject was named" — which is exactly
    the number `unclassified` reports separately, so a caller can compute any
    rate it wants and none is baked in.

    `unclassified` is the state the whole product turns on: the subject was not
    named, so tone toward it was never asked. It is NOT a neutral. Folding the
    two together would report a brand nobody mentioned as having been described
    neutrally, which is a measurement nobody took.
    """

    engine: str
    positive: int
    neutral: int
    negative: int
    unclassified: int


class HistoryScanOut(ApiModel):
    """One point on the client's timeline."""

    scan_id: str
    status: str
    scanned_at: datetime
    # The STORED composite, so it matches the report and the dashboard. Null
    # when the scan was never scored or scored INSUFFICIENT_DATA — never zero.
    composite: Decimal | None = None
    # The subject's own share of voice, on the same axis as every rival's: both
    # are appearances over the same total brand mentions.
    share_of_voice: Decimal | None = None
    # Required rather than defaulted: `build_history` always supplies both,
    # and a defaulted list is emitted as OPTIONAL in the OpenAPI schema —
    # which would make every consumer guard a field that is always there.
    cited_domains: list[HistoryCitedDomainOut]
    competitors: list[HistoryCompetitorOut]
    # One row per engine that answered anything in this scan — Epic A. Required
    # rather than defaulted, for the reason the two lists above are.
    sentiment: list[HistorySentimentOut]


class ClientHistoryOut(ApiModel):
    """Every scan of one client that produced a reading, oldest first."""

    client_id: str
    name: str
    domain: str
    scans: list[HistoryScanOut]
    # Scans that exist but carry no reading — queued, running, failed,
    # cancelled. Reported rather than dropped silently, so a client with four
    # scans and one usable one does not look like a client with one scan.
    scans_without_data: int
