"""add app_settings table

Revision ID: 020
Revises: 019
Create Date: 2026-04-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.String(2000), nullable=False),
    )
    # Seed default registration setting
    op.execute("INSERT INTO app_settings (key, value) VALUES ('registration_enabled', 'true')")


def downgrade() -> None:
    op.drop_table("app_settings")
