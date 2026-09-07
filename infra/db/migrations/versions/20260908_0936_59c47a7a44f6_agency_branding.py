"""agency branding

Two nullable columns on `agencies` — `logo_url` and `accent_color` — plus a
format CHECK on each.

WHY TWO AND NOT THE THREE §7 ASKED FOR
--------------------------------------
§7 line 2 says "logo, custom domain, colours". What an agency may actually
change is a logo and ONE colour, used only on chrome, and the shortness of that
list is the decision rather than an omission.

The report's palette is notation, not decoration. `--avp-vis-*` encodes the
score on a monotonic lightness ramp that survives greyscale and colour-vision
deficiency; `--avp-competitor-{1..5}` is neutral so no rival reads as endorsed
or attacked; `--avp-beacon-*` marks the subject being scanned, who is the
prospect and not the agency; the semantic four say a scan failed or a quota is
low. An agency free to recolour any of those changes what the document MEANS.
So there is exactly one agency colour and it is confined to chrome — a
letterhead rule, a footer line, the "prepared by" byline.

NO `custom_domain`. Deferred, not forgotten: a custom domain needs DNS
verification, certificate issuance and request routing before it does anything,
and a nullable column with none of that behind it is a field that looks built
and is not.

VALIDATED TWICE, ON PURPOSE
---------------------------
Both values are agency-supplied and both land on the UNAUTHENTICATED share
page — the logo as an `<img src>`, the accent as a CSS custom property value.
`schemas/agency.py` rejects a bad one with a readable message; these
constraints mean a bad one cannot be stored however it arrived, including from
a script or a future endpoint that forgets. `javascript:` and `data:` in an
`src` are script execution; an unvalidated string in a custom property closes
the declaration and opens another.

The logo is a URL and never an upload: there is no asset store in this product,
and inventing one for a logo would be the larger decision smuggled inside the
smaller. It is never fetched server-side, which is what keeps an
agency-supplied URL from being an SSRF vector.

No backfill: every existing agency has NULL for both, which is the correct and
expected state — an unbranded report is the one every agency has today.

DOWNGRADE drops both columns; the constraints go with them.

Revision ID: 59c47a7a44f6
Revises: a6a32760b418
Created: 2026-09-08 09:36:16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '59c47a7a44f6'
down_revision: str | None = 'a6a32760b418'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEX = "ck_agencies_accent_color_is_hex"
HTTPS = "ck_agencies_logo_url_is_https"


def upgrade() -> None:
    op.add_column("agencies", sa.Column("logo_url", sa.String(length=2048), nullable=True))
    op.add_column("agencies", sa.Column("accent_color", sa.String(length=7), nullable=True))
    op.create_check_constraint(
        op.f(HEX), "agencies", "accent_color IS NULL OR accent_color ~ '^#[0-9a-fA-F]{6}$'"
    )
    op.create_check_constraint(
        op.f(HTTPS), "agencies", "logo_url IS NULL OR logo_url LIKE 'https://%'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(HTTPS), "agencies", type_="check")
    op.drop_constraint(op.f(HEX), "agencies", type_="check")
    op.drop_column("agencies", "accent_color")
    op.drop_column("agencies", "logo_url")
