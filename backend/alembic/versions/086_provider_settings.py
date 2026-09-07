"""Allow encrypted PEM credentials in app settings.

Revision ID: 086
Revises: 085
"""

from alembic import op
import sqlalchemy as sa

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("app_settings", "value", existing_type=sa.String(2000), type_=sa.Text())


def downgrade() -> None:
    # Refuse to truncate a saved credential if it exceeds the old limit.
    op.alter_column("app_settings", "value", existing_type=sa.Text(), type_=sa.String(2000))
