"""convert category icons from emojis to Lucide icon names

Revision ID: 008
Revises: 007
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "categories",
        "icon",
        existing_type=sa.String(10),
        type_=sa.String(50),
    )

    op.execute(
        """
        UPDATE categories SET icon = CASE icon
            WHEN '🏠' THEN 'house'
            WHEN '🍔' THEN 'utensils-crossed'
            WHEN '🚗' THEN 'car'
            WHEN '🛒' THEN 'shopping-cart'
            WHEN '💊' THEN 'pill'
            WHEN '🎮' THEN 'gamepad-2'
            WHEN '📱' THEN 'smartphone'
            WHEN '📚' THEN 'book-open'
            WHEN '💸' THEN 'arrow-left-right'
            WHEN '❓' THEN 'circle-help'
            ELSE icon
        END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE categories SET icon = CASE icon
            WHEN 'house' THEN '🏠'
            WHEN 'utensils-crossed' THEN '🍔'
            WHEN 'car' THEN '🚗'
            WHEN 'shopping-cart' THEN '🛒'
            WHEN 'pill' THEN '💊'
            WHEN 'gamepad-2' THEN '🎮'
            WHEN 'smartphone' THEN '📱'
            WHEN 'book-open' THEN '📚'
            WHEN 'arrow-left-right' THEN '💸'
            WHEN 'circle-help' THEN '❓'
            ELSE icon
        END
        """
    )

    op.alter_column(
        "categories",
        "icon",
        existing_type=sa.String(50),
        type_=sa.String(10),
    )
