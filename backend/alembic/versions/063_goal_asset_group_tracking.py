"""add asset group tracking to goals

Revision ID: 063
Revises: 062
Create Date: 2026-06-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("goals") as batch_op:
        batch_op.add_column(
            sa.Column("asset_group_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_goals_asset_group_id",
            "asset_groups",
            ["asset_group_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("goals") as batch_op:
        batch_op.drop_constraint("fk_goals_asset_group_id", type_="foreignkey")
        batch_op.drop_column("asset_group_id")
