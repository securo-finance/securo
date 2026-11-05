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

Clients referenced by these surviving records must also survive. Their
creator becomes nullable rather than assigning a different creator:

- payees.user_id -> nullable, ON DELETE SET NULL

Revision ID: 103
Revises: 102
"""
from alembic import op
import sqlalchemy as sa

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None

_TABLES = ("invoice_schedules", "invoices", "invoice_attachments", "payees")


def _recreate(ondelete: str | None) -> None:
    for table in _TABLES:
        name = f"{table}_user_id_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, "users", ["user_id"], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    op.alter_column("payees", "user_id", nullable=True)
    _recreate("SET NULL")


def downgrade() -> None:
    payees = sa.Table("payees", sa.MetaData(), sa.Column("user_id", sa.Uuid()))
    if op.get_bind().execute(
        sa.select(payees.c.user_id).where(payees.c.user_id.is_(None)).limit(1)
    ).first() is not None:
        raise RuntimeError(
            "Reassign payees with no creator to valid users before downgrading "
            "migration 103; the old schema requires payees.user_id. "
            "No client data or constraints were changed."
        )
    _recreate(None)
    op.alter_column("payees", "user_id", nullable=False)
