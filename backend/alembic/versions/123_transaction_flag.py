"""let the user flag a transaction to come back to it (issue #56)

Revision ID: 123
Revises: 122
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "123"
down_revision: Union[str, None] = "122"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "is_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "is_flagged")
