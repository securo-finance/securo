"""clear stale Pluggy sandbox.svg connection logos

v0.13.0 stored the Pluggy connector ``imageUrl`` verbatim, which for the demo
/ sandbox connector is the generic ``sandbox.svg`` placeholder. The v0.13.1
logo fix only backfills connections whose ``logo_url`` is NULL, so these rows
keep showing the placeholder. Null them out here so the next sync re-resolves
the real bank logo (via the COMPE-code fallback). Only the placeholder rows
are touched — real logos, Enable Banking and SimpleFIN entries are untouched.

Revision ID: 058
Revises: 057
Create Date: 2026-06-09
"""

from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"UPDATE bank_connections SET logo_url = NULL "
        r"WHERE logo_url LIKE '%/sandbox.svg'"
    )


def downgrade() -> None:
    # One-way data cleanup: the placeholder value carried no information worth
    # restoring, so the downgrade is intentionally a no-op.
    pass
