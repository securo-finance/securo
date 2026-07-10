"""rebucket credit card effective_dates to Brazilian close-day convention

Revision ID: 030
Revises: 029
Create Date: 2026-04-17

Per Brazilian banking convention (Nubank, Itaú, Bradesco, Santander, etc.),
a transaction dated ON the statement close day belongs to the NEXT invoice,
not the invoice closing that day. Securo's original formula bucketed these
transactions into the same-day close. This migration recomputes effective_date
for every transaction on every credit card that has both statement_close_day
and payment_due_day configured, using the corrected formula.

Accounts without both cycle days stay untouched (effective_date already
equals the transaction date for them — there's nothing to rebucket).
"""

import calendar
from datetime import date

from alembic import op
from sqlalchemy import text

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def _clamp_day(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _compute_effective_date(tx_date: date, close_day: int, due_day: int) -> date:
    # Mirror of app.services.credit_card_service.compute_effective_date
    # (post-fix). Inlined so the migration doesn't depend on app code that
    # may drift over time.
    same_month_close = _clamp_day(tx_date.year, tx_date.month, close_day)
    if same_month_close > tx_date:
        cycle_end = same_month_close
    elif tx_date.month == 12:
        cycle_end = _clamp_day(tx_date.year + 1, 1, close_day)
    else:
        cycle_end = _clamp_day(tx_date.year, tx_date.month + 1, close_day)

    same_month_due = _clamp_day(cycle_end.year, cycle_end.month, due_day)
    if same_month_due > cycle_end:
        return same_month_due
    if cycle_end.month == 12:
        return _clamp_day(cycle_end.year + 1, 1, due_day)
    return _clamp_day(cycle_end.year, cycle_end.month + 1, due_day)


def upgrade() -> None:
    conn = op.get_bind()

    accounts = conn.execute(
        text(
            """
            SELECT id, statement_close_day, payment_due_day
            FROM accounts
            WHERE type = 'credit_card'
              AND statement_close_day IS NOT NULL
              AND payment_due_day IS NOT NULL
            """
        )
    ).fetchall()

    for acc_id, close_day, due_day in accounts:
        rows = conn.execute(
            text("SELECT id, date FROM transactions WHERE account_id = :acc_id"),
            {"acc_id": str(acc_id)},
        ).fetchall()

        for tx_id, tx_date in rows:
            new_effective = _compute_effective_date(tx_date, close_day, due_day)
            conn.execute(
                text("UPDATE transactions SET effective_date = :eff WHERE id = :tx_id"),
                {"eff": new_effective, "tx_id": str(tx_id)},
            )


def downgrade() -> None:
    # The old formula used `>=` instead of `>`. Restoring it precisely would
    # require re-running that variant on every row. We skip the back-migration
    # since effective_date is a derived/cached column — any future recompute
    # (account edit or resync) will regenerate it from whichever formula is
    # current. No schema changes to reverse.
    pass
