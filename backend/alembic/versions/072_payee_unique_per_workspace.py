"""scope the payee name uniqueness to the workspace instead of the user

Revision ID: 072
Revises: 071
Create Date: 2026-08-19

`uq_payees_user_id_name` was created in 019, back when a payee belonged to
a user. Migration 052 moved payees onto workspaces and the query layer
followed, but the constraint stayed user-scoped, so the database kept
enforcing a rule the application no longer believes in: one name per user,
across every workspace they touch.

The visible symptom is a bank connection failing with a duplicate key
error. Sync resolves a counterparty with a workspace-scoped lookup, finds
nothing in *this* workspace, inserts, and collides with a row the same
person created in a different one — commonly a counterparty they already
have in their personal workspace and now meet again in a shared one, or a
leftover from a connection that was removed earlier.

Existing rows can violate the new constraint where two members of one
workspace each created the same name (the old constraint allowed it,
because their user ids differ), so duplicates are merged into the oldest
row before the constraint goes on.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The oldest row of each (workspace_id, name) group wins; every other row
# in the group is a duplicate that has to be folded into it.
DUPLICATES = """
    SELECT
        p.id AS dup_id,
        first_value(p.id) OVER (
            PARTITION BY p.workspace_id, p.name
            ORDER BY p.created_at, p.id
        ) AS keeper_id
    FROM payees p
"""


def _constraint_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'payees' AND constraint_name = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    # Transactions and merge mappings follow the keeper.
    op.execute(
        f"""
        UPDATE transactions t
        SET payee_id = d.keeper_id
        FROM ({DUPLICATES}) d
        WHERE t.payee_id = d.dup_id AND d.dup_id <> d.keeper_id
        """
    )
    op.execute(
        f"""
        UPDATE payee_mapping m
        SET target_id = d.keeper_id
        FROM ({DUPLICATES}) d
        WHERE m.target_id = d.dup_id AND d.dup_id <> d.keeper_id
        """
    )
    # Fiscal documents would cascade away with the duplicate row, so move
    # the ones the keeper has no answer for. A kind the keeper already
    # carries is left behind: two CNPJs on one counterparty is not
    # something a migration should pick a winner for.
    op.execute(
        f"""
        UPDATE payee_tax_ids ti
        SET payee_id = d.keeper_id
        FROM ({DUPLICATES}) d
        WHERE ti.payee_id = d.dup_id
          AND d.dup_id <> d.keeper_id
          AND NOT EXISTS (
              SELECT 1 FROM payee_tax_ids keep
              WHERE keep.payee_id = d.keeper_id AND keep.kind = ti.kind
          )
        """
    )
    op.execute(
        f"""
        DELETE FROM payees p
        USING ({DUPLICATES}) d
        WHERE p.id = d.dup_id AND d.dup_id <> d.keeper_id
        """
    )

    if _constraint_exists("uq_payees_user_id_name"):
        op.drop_constraint("uq_payees_user_id_name", "payees", type_="unique")
    op.create_unique_constraint(
        "uq_payees_workspace_id_name", "payees", ["workspace_id", "name"]
    )


def downgrade() -> None:
    # Going back cannot recreate the user-scoped constraint unconditionally:
    # the rows this migration made legal are exactly the ones it forbade.
    op.drop_constraint("uq_payees_workspace_id_name", "payees", type_="unique")
    duplicated_per_user = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM payees GROUP BY user_id, name HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar()
    if not duplicated_per_user:
        op.create_unique_constraint(
            "uq_payees_user_id_name", "payees", ["user_id", "name"]
        )
