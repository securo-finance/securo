from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("shared_balance_group", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "shared_balance_group")
