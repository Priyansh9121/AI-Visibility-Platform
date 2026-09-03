"""Alert — something changed between two scans that an operator should see.

WHY AN ALERT IS A ROW AND NOT A QUERY
--------------------------------------
Every figure an alert is derived from is already persisted, so this could have
been computed on read. It is not, for the reason `client_history.py` gives about
the composite: a number that recomputes on every request can change between two
page loads of the same data. An alert is worse than a number in that respect —
it can be ACKNOWLEDGED, and an acknowledgement has nowhere to live if the thing
acknowledged is re-derived each time it is asked for.

So alerts are generated once, at the end of a scan, and are a record of what was
true then.

=============================================================================
THE BASELINE IS NOT "THE PREVIOUS SCAN"  — the correction this table encodes
=============================================================================
The obvious rule is to compare a scan with the one before it. Measured against
the real `avp_dev` rows, that rule is wrong: of the three consecutive-scan pairs
in the whole database, **two are re-runs 43 minutes and 2 hours 11 minutes
apart**, and only one spans a real interval (1 day 21 hours).

Comparing a scan with a re-run of itself does not measure the market changing,
it measures the engines answering nondeterministically. On those re-runs the
composite moved -0.93 and +0.68, net tone moved as much as 6 points, and
competitor citation counts swung by 26 — all with nothing whatsoever having
happened.

So the baseline is the most recent scan at least `MIN_BASELINE_HOURS` older,
and a scan with no such predecessor produces NO alerts rather than alerts
against whatever happened to run before it. `services/alerts.py` holds the
number and the argument for it.
=============================================================================
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, enum_column, fk_column, id_column


class AlertKind(str, enum.Enum):
    """What was detected. Each name says what it MEASURES, not what it implies.

    Two of these are deliberately not what the brief for Epic E asked for, and
    the names record that:

    * `SENTIMENT_DECLINE`, not `sentiment_negative`. "Net tone crossing to
      negative" fires zero times on every scan in the database — the minimum
      net tone ever recorded is **+4**. What does happen is a steep decline
      while still positive: Notion's net fell 13->6, 12->5 and 10->4 across all
      three engines in one interval. A rule watching for a sign change would
      have reported nothing about the clearest tone event on record.

    * `OWNED_CITATION_LOST`, not `citation_lost` in the sense of "a rival took
      the citation". A domain cannot change hands: `classify_citation` returns
      `cites_subject` as `domain == subject_domain`, a pure function of the
      domain string, so a source the client owned in one scan cannot be a
      rival's in the next. Verified in the data too — no domain in any client's
      history has ever carried two different `cites_subject` values. What IS
      measurable is the client's own domain going from cited to uncited.
    """

    VISIBILITY_DROP = "visibility_drop"
    SENTIMENT_DECLINE = "sentiment_decline"
    OWNED_CITATION_LOST = "owned_citation_lost"


class Alert(Base, TimestampMixin):
    """One detected change, between one scan and its baseline."""

    __tablename__ = "alerts"

    id: Mapped[str] = id_column()
    client_id: Mapped[str] = fk_column("clients.id")
    # Denormalised so tenancy is one predicate rather than a join — the rule
    # `PromptRun` states and every agency-scoped read in this codebase follows.
    agency_id: Mapped[str] = fk_column("agencies.id")

    kind: Mapped[AlertKind] = enum_column(AlertKind, name="alert_kind")

    # The scan that triggered it, and the scan it was compared against. Both
    # required: an alert with no baseline is not a change, and being able to
    # name both is what lets the screen say "since the scan of the 29th".
    scan_id: Mapped[str] = fk_column("scans.id")
    baseline_scan_id: Mapped[str] = fk_column("scans.id")

    # Only sentiment alerts carry one — tone is measured per engine, and the
    # others are whole-scan figures. NULL means "not an engine-scoped finding",
    # never "unknown engine".
    engine: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # OUR OWN sentence, assembled from our own numbers (ip-safety.md #7). There
    # is no engine text anywhere in this row and no column that could hold any.
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # NO relationship to Scan, deliberately. There are TWO foreign keys to
    # `scans` on this table, so a relationship needs an explicit `foreign_keys`
    # on each side and buys nothing here: the feed reads scan timestamps in the
    # same grouped query it reads alerts, rather than lazy-loading two scans per
    # row. An unused relationship that has to be disambiguated is a footgun
    # waiting for whoever adds the third foreign key.

    __table_args__ = (
        # The feed query: this client's alerts, newest first. IDs are ULIDs, so
        # id DESC is creation order and this index serves the ordering too.
        Index("ix_alerts_client_id_id", "client_id", "id"),
        # "Which alerts are still outstanding" — the figure the tab badges.
        Index("ix_alerts_client_acknowledged", "client_id", "acknowledged_at"),
    )
