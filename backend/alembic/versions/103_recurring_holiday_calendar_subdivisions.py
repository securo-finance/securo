"""recurring holiday calendars: any country, with subdivisions

Revision ID: 103
Revises: 102

``holiday_calendar`` held only ``BR``. It now takes any country the
``holidays`` package supports, optionally with a subdivision (``BR-SP``,
``US-NY``, ``CH-Stadt Zurich``), so the column widens and the CHECK
constraint goes: the list is validated by the API, where it can follow the
package, rather than pinned in the database.
"""
import sqlalchemy as sa

from alembic import op

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Already dropped where this ran as 100 before the renumbering.
    op.execute(
        "ALTER TABLE recurring_transactions "
        "DROP CONSTRAINT IF EXISTS ck_recurring_transactions_holiday_calendar"
    )
    op.alter_column(
        "recurring_transactions",
        "holiday_calendar",
        type_=sa.String(length=32),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Calendars other than plain BR cannot be represented before this
    # revision; their schedules fall back to weekdays only.
    op.execute(
        "UPDATE recurring_transactions SET holiday_calendar = 'BR' "
        "WHERE holiday_calendar LIKE 'BR-%'"
    )
    op.execute(
        "UPDATE recurring_transactions SET holiday_calendar = NULL "
        "WHERE holiday_calendar IS NOT NULL AND holiday_calendar <> 'BR'"
    )
    op.alter_column(
        "recurring_transactions",
        "holiday_calendar",
        type_=sa.String(length=10),
        existing_type=sa.String(length=32),
        existing_nullable=True,
    )
    op.create_check_constraint(
        "ck_recurring_transactions_holiday_calendar",
        "recurring_transactions",
        "holiday_calendar IS NULL OR holiday_calendar IN ('BR')",
    )
