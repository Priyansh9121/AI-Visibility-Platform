"""agency subscription columns

The four columns behind `POST /agencies/{id}/billing/checkout`, `GET
/agencies/{id}/billing` and `POST /billing/webhook` — Epic 9.15, the first
commercial state this schema has ever carried.

EVERY COLUMN IS NULLABLE, AND NULL IS THE RIGHT ANSWER FOR EVERY EXISTING ROW.
No agency in this database has a Stripe customer, a subscription, or a status,
because none of those things existed until this migration ran. A backfill would
be inventing facts; a NOT NULL with a default would be asserting that every
agency is in some billing state, which is exactly the claim the product must
not make. An agency with all four NULL is an agency that has not subscribed,
and it keeps every bit of access it had yesterday — nothing in the application
gates on these.

`stripe_customer_id` and `stripe_subscription_id` are UNIQUE. Postgres permits
unlimited NULLs under a unique index, which is what makes the constraint usable
on columns almost every row leaves empty, and the constraint is not decoration:
the webhook resolves an agency BY these ids, and a lookup key that can match two
rows is a cancellation applied to the wrong agency. `services/billing.py` also
reads `stripe_customer_id` before creating a customer, so the column is what
stops a second visit to checkout minting a duplicate customer — the index is the
backstop under that.

`subscription_status` is a plain VARCHAR(32), NOT an `enum_column`, and that is
the one deliberate departure from the convention every other status in this
schema follows. The values are Stripe's to define and to extend. A CHECK
constraint listing today's eight would mean that the day Stripe adds a ninth,
the webhook raises on write, Stripe retries and then gives up, and the row holds
a status that stopped being true days ago — a wrong answer that looks like a
right one, which is the failure mode this codebase spends the most effort
avoiding. Storing the string records an unrecognised status accurately instead.

`subscription_current_period_end` exists so that Settings can say "renews
<date>" without calling Stripe on page load. Those two requirements are only
compatible if the webhook writes the date next to the status, so it does.

See build-log Epic 9.15.

Revision ID: e5a71c9d3b84
Revises: d8f1b2a45c93
Created: 2026-08-28 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e5a71c9d3b84'
down_revision: str | None = 'd8f1b2a45c93'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agencies",
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agencies",
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agencies",
        sa.Column("subscription_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agencies",
        sa.Column(
            "subscription_current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        op.f("uq_agencies_stripe_customer_id"), "agencies", ["stripe_customer_id"]
    )
    op.create_unique_constraint(
        op.f("uq_agencies_stripe_subscription_id"),
        "agencies",
        ["stripe_subscription_id"],
    )


def downgrade() -> None:
    # Dropping these forgets which Stripe customer an agency is, while the
    # subscription itself carries on billing at Stripe. That is worth stating
    # plainly rather than discovering: a downgrade here does not cancel
    # anything, and re-upgrading will not re-link the rows — the webhook has to
    # deliver another event, or the customer has to be matched by hand.
    op.drop_constraint(
        op.f("uq_agencies_stripe_subscription_id"), "agencies", type_="unique"
    )
    op.drop_constraint(
        op.f("uq_agencies_stripe_customer_id"), "agencies", type_="unique"
    )
    op.drop_column("agencies", "subscription_current_period_end")
    op.drop_column("agencies", "subscription_status")
    op.drop_column("agencies", "stripe_subscription_id")
    op.drop_column("agencies", "stripe_customer_id")
