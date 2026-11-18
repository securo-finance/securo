"""make spending plan timestamps non-nullable, as the model declares

Revision ID: fork_003
Revises: fork_002
"""

import sqlalchemy as sa

from alembic import op

revision = "fork_003"
down_revision = "fork_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.execute(f"UPDATE spending_plans SET {column} = now() WHERE {column} IS NULL")
        op.alter_column(
            "spending_plans", column, existing_type=sa.DateTime(timezone=True), nullable=False
        )


def downgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column(
            "spending_plans", column, existing_type=sa.DateTime(timezone=True), nullable=True
        )
