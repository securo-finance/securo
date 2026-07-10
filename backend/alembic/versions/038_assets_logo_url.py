"""add logo_url to assets (cached favicon URL for market-priced assets)

Revision ID: 038
Revises: 037
Create Date: 2026-04-21

Stores a fully-formed logo URL so the frontend can render an <img> without
knowing about the upstream service. Populated at creation time for
market-priced assets when the quote provider returns a company website;
left null for manual assets and tickers without a website (most crypto).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("logo_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "logo_url")
