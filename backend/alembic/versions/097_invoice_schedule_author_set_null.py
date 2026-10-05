"""keep recurring agreements when their author is deleted

Revision ID: 097
Revises: 096
"""
from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("invoice_schedules_user_id_fkey", "invoice_schedules", type_="foreignkey")
    op.create_foreign_key(
        "invoice_schedules_user_id_fkey", "invoice_schedules", "users", ["user_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("invoice_schedules_user_id_fkey", "invoice_schedules", type_="foreignkey")
    op.create_foreign_key(
        "invoice_schedules_user_id_fkey", "invoice_schedules", "users", ["user_id"], ["id"],
    )
