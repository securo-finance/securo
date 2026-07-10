"""phase2 sync polish

Revision ID: 012
Revises: 011
Create Date: 2026-03-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("accounts", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "bank_connections",
        sa.Column("settings", sa.JSON(), nullable=True, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("bank_connections", "settings")
    op.drop_column("accounts", "closed_at")
    op.drop_column("accounts", "is_closed")
