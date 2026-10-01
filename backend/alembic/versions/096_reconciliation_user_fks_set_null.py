"""let a user with reconciliation history be deleted

Three columns record who did something to matching, and 086/088 created
their foreign keys to `users` without an ON DELETE action. Deleting a
user who wrote a rule, answered a suggestion, or appears in the history
then fails on the constraint. The rows belong to the workspace, not to
the person, so they stay and only the reference is cleared:

- reconciliation_rules.user_id           -> ON DELETE SET NULL
- reconciliation_suggestions.resolved_by -> ON DELETE SET NULL
- reconciliation_events.user_id          -> ON DELETE SET NULL

Revision ID: 096
Revises: 095
"""
from alembic import op

revision = "096"
down_revision = "095"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("reconciliation_rules", "user_id"),
    ("reconciliation_suggestions", "resolved_by"),
    ("reconciliation_events", "user_id"),
)


def _recreate(ondelete: str | None) -> None:
    for table, column in _COLUMNS:
        name = f"{table}_{column}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, "users", [column], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    _recreate("SET NULL")


def downgrade() -> None:
    _recreate(None)
