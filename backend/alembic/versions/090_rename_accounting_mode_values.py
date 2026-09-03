"""rename credit_card_accounting_mode values to purchase_date/invoice_due_date

The 'cash'/'accrual' values had the accounting terminology backwards
relative to standard usage (issue #821): 'accrual' bucketed by the bill's
due date (that's cash-basis behavior), 'cash' bucketed by the purchase date
(that's accrual-basis behavior). Renaming to self-describing values and
fixing the UI labels separately, so the accounting terms are only ever used
correctly. This migration preserves each deployment's existing behavior —
only the stored value's name changes, not what it does.

Revision ID: 090
Revises: 089
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "090"
down_revision: Union[str, None] = "089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = sa.table(
    "app_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
)


def upgrade() -> None:
    op.execute(
        _TABLE.update()
        .where(_TABLE.c.key == "credit_card_accounting_mode", _TABLE.c.value == "cash")
        .values(value="purchase_date")
    )
    op.execute(
        _TABLE.update()
        .where(_TABLE.c.key == "credit_card_accounting_mode", _TABLE.c.value == "accrual")
        .values(value="invoice_due_date")
    )


def downgrade() -> None:
    op.execute(
        _TABLE.update()
        .where(_TABLE.c.key == "credit_card_accounting_mode", _TABLE.c.value == "purchase_date")
        .values(value="cash")
    )
    op.execute(
        _TABLE.update()
        .where(_TABLE.c.key == "credit_card_accounting_mode", _TABLE.c.value == "invoice_due_date")
        .values(value="accrual")
    )
