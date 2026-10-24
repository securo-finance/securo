"""let a user whose agreements have billed be deleted

Three columns record who created invoicing rows, and 078/080/092 created
their foreign keys to `users` without an ON DELETE action. A recurring
agreement stamps its author on every invoice it issues and on the PDF it
files for each one, so deleting that author fails on the constraint. The
rows belong to the workspace, not to the person, so they stay and only
the reference is cleared:

- invoice_schedules.user_id   -> ON DELETE SET NULL
- invoices.user_id            -> ON DELETE SET NULL
- invoice_attachments.user_id -> ON DELETE SET NULL

Revision ID: 097
Revises: 096
"""
from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None

_TABLES = ("invoice_schedules", "invoices", "invoice_attachments")


def _recreate(ondelete: str | None) -> None:
    for table in _TABLES:
        name = f"{table}_user_id_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, "users", ["user_id"], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    _recreate("SET NULL")


def downgrade() -> None:
    _recreate(None)
