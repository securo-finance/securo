"""Allow encrypted provider credentials in app settings.

Revision ID: 113
Revises: 112
"""

from alembic import op
import sqlalchemy as sa

revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("app_settings", "value", existing_type=sa.String(2000), type_=sa.Text())


def downgrade() -> None:
    # Operators must remove long saved credentials before rolling back.
    op.alter_column("app_settings", "value", existing_type=sa.Text(), type_=sa.String(2000))
