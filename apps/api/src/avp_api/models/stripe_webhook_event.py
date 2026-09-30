"""StripeWebhookEvent — the ledger that makes the billing webhook idempotent.

WHY THIS TABLE EXISTS
---------------------
Stripe delivers at least once. It retries on any non-2xx and on a timeout, and
it does not promise order. Without a record of which events were already
applied, a retried delivery re-applies old state, and an out-of-order one
(`customer.subscription.updated: active` arriving after `...deleted`) turns a
cancelled agency back into a paying one.

The row is inserted with `INSERT ... ON CONFLICT (id) DO NOTHING` in the SAME
transaction as the state change (`services/billing.apply_event`). Two
consequences follow, and both are the point:

* a handler that raises rolls the row back with the change, so Stripe's retry
  is processed rather than mistaken for a duplicate;
* two concurrent deliveries of one event serialise on the primary key: the
  second insert waits for the first's uncommitted row, then conflicts.

FACTS ONLY
----------
Nothing of the payload is kept: an id, a type, Stripe's own timestamp, the
agency it resolved to, and one of two outcome codes. No customer, no email, no
amounts. Only event types the service handles are recorded, not every event the
account is configured to send.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, enum_column, fk_column


class StripeWebhookOutcome(str, enum.Enum):
    """What happened to a recorded event. A duplicate is never recorded."""

    APPLIED = "applied"
    STALE = "stale"


class StripeWebhookEvent(Base, TimestampMixin):
    """One handled Stripe event, keyed by Stripe's own event id."""

    __tablename__ = "stripe_webhook_events"

    # Stripe's `evt_...` id, not one of ours: it is the dedupe key.
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # `event.created`: one-second resolution, Stripe's clock.
    stripe_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # NULL once the agency is gone (SET NULL keeps the ledger row; it holds no
    # tenant data). Unmatched events are never recorded.
    agency_id: Mapped[str | None] = fk_column(
        "agencies.id", nullable=True, ondelete="SET NULL"
    )
    outcome: Mapped[StripeWebhookOutcome] = enum_column(
        StripeWebhookOutcome, name="stripe_webhook_outcome"
    )
