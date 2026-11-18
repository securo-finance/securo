"""recurring bills: scope the duplicate guard to the workspace

A recurring bill is financial data, so it belongs to one workspace (ADR 0002),
but its duplicate guard was `(user_id, description, frequency, start_date)`,
which spans every workspace the user has. Two books of the same person can
legitimately hold the same rent bill on the same schedule — and a restore from
a backup, which lands in a new workspace, could not recreate one at all: every
row collided with the copy it was restoring from.

The guard keeps the user: a workspace can hold two members' identical bills,
and the per-user constraint is the one existing data already satisfies, so this
migration cannot fail on a live database that has them. Adding `workspace_id`
narrows the guard to one book without weakening what it enforced.

Revision ID: 120
Revises: 119
"""

from alembic import op

revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None

_COLUMNS = ["workspace_id", "user_id", "description", "frequency", "start_date"]


def upgrade() -> None:
    op.drop_constraint("uq_recurring_tx", "recurring_transactions", type_="unique")
    op.create_unique_constraint("uq_recurring_tx", "recurring_transactions", _COLUMNS)


def downgrade() -> None:
    op.drop_constraint("uq_recurring_tx", "recurring_transactions", type_="unique")
    op.create_unique_constraint(
        "uq_recurring_tx",
        "recurring_transactions",
        ["user_id", "description", "frequency", "start_date"],
    )
