"""scope user-owned records to workspaces

Revision ID: 099
Revises: 098
"""

import sqlalchemy as sa
from alembic import op

revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep creator ownership in the key: two workspace members may legitimately
    # have matching schedules or group names. Adding workspace_id removes the
    # cross-workspace collisions that blocked restoring a second workspace.
    op.drop_constraint("uq_recurring_tx", "recurring_transactions", type_="unique")
    op.create_unique_constraint(
        "uq_recurring_tx",
        "recurring_transactions",
        ["workspace_id", "user_id", "description", "frequency", "start_date"],
    )

    op.drop_constraint("uq_groups_user_id_name", "groups", type_="unique")
    op.create_unique_constraint(
        "uq_groups_workspace_user_name",
        "groups",
        ["workspace_id", "user_id", "name"],
    )


def downgrade() -> None:
    # A user may have created the same value in multiple workspaces after the
    # upgrade. Preserve every row while making names deterministic enough for
    # the previous user-global constraints.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY user_id, description, frequency, start_date
                           ORDER BY workspace_id, id
                       ) AS duplicate_number
                FROM recurring_transactions
            )
            UPDATE recurring_transactions AS recurring
            SET description = left(recurring.description, 450)
                || ' [workspace '
                || left(recurring.workspace_id::text, 8)
                || '-'
                || left(recurring.id::text, 8)
                || ']'
            FROM ranked
            WHERE recurring.id = ranked.id
              AND ranked.duplicate_number > 1
            """
        )
    )
    op.drop_constraint("uq_recurring_tx", "recurring_transactions", type_="unique")
    op.create_unique_constraint(
        "uq_recurring_tx",
        "recurring_transactions",
        ["user_id", "description", "frequency", "start_date"],
    )

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY user_id, name
                           ORDER BY workspace_id, id
                       ) AS duplicate_number
                FROM groups
            )
            UPDATE groups AS group_row
            SET name = left(group_row.name, 50)
                || ' [workspace '
                || left(group_row.workspace_id::text, 8)
                || '-'
                || left(group_row.id::text, 8)
                || ']'
            FROM ranked
            WHERE group_row.id = ranked.id
              AND ranked.duplicate_number > 1
            """
        )
    )
    op.drop_constraint("uq_groups_workspace_user_name", "groups", type_="unique")
    op.create_unique_constraint(
        "uq_groups_user_id_name",
        "groups",
        ["user_id", "name"],
    )
