"""add category_groups table and group_id to categories

Revision ID: 009
Revises: 008
Create Date: 2026-02-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default groups: (name, icon, color, position)
DEFAULT_GROUPS = [
    ("Moradia", "house", "#8B5CF6", 0),
    ("Alimentação", "utensils-crossed", "#F59E0B", 1),
    ("Transporte", "car", "#3B82F6", 2),
    ("Estilo de Vida", "sparkles", "#EC4899", 3),
    ("Outros", "circle-help", "#64748B", 4),
]

# Map category name -> group name
CATEGORY_TO_GROUP = {
    "Moradia": "Moradia",
    "Alimentação": "Alimentação",
    "Mercado": "Alimentação",
    "Transporte": "Transporte",
    "Saúde": "Estilo de Vida",
    "Lazer": "Estilo de Vida",
    "Educação": "Estilo de Vida",
    "Assinaturas": "Outros",
    "Transferências": "Outros",
    "Outros": "Outros",
}


def upgrade() -> None:
    # 1. Create category_groups table
    op.create_table(
        "category_groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("icon", sa.String(50), nullable=False, server_default="folder"),
        sa.Column("color", sa.String(7), nullable=False, server_default="#6B7280"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_category_groups_user_id", "category_groups", ["user_id"])

    # 2. Add group_id column to categories
    op.add_column(
        "categories",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category_groups.id"),
            nullable=True,
        ),
    )

    # 3. Seed default groups for each existing user and assign categories
    conn = op.get_bind()

    users = conn.execute(sa.text("SELECT id FROM users")).fetchall()

    for (user_id,) in users:
        # Create groups for this user
        group_ids = {}
        for name, icon, color, position in DEFAULT_GROUPS:
            result = conn.execute(
                sa.text(
                    "INSERT INTO category_groups (id, user_id, name, icon, color, position, is_system) "
                    "VALUES (gen_random_uuid(), :user_id, :name, :icon, :color, :position, true) "
                    "RETURNING id"
                ),
                {
                    "user_id": user_id,
                    "name": name,
                    "icon": icon,
                    "color": color,
                    "position": position,
                },
            )
            group_ids[name] = result.scalar()

        # Assign categories to groups
        for cat_name, group_name in CATEGORY_TO_GROUP.items():
            group_id = group_ids.get(group_name)
            if group_id:
                conn.execute(
                    sa.text(
                        "UPDATE categories SET group_id = :group_id "
                        "WHERE user_id = :user_id AND name = :cat_name"
                    ),
                    {"group_id": group_id, "user_id": user_id, "cat_name": cat_name},
                )


def downgrade() -> None:
    op.drop_column("categories", "group_id")
    op.drop_index("ix_category_groups_user_id", "category_groups")
    op.drop_table("category_groups")
