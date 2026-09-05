"""backfill opening_balance for connected accounts

Revision ID: 029
Revises: 028
Create Date: 2026-04-16

Providers only return ~1 year of history but report the true current balance,
so for every existing Pluggy-linked account the sum of imported transactions
doesn't equal account.balance. The difference is the account's real balance
at the start of the import window, which was never recorded anywhere. This
migration inserts a synthetic `source='opening_balance'` transaction on each
connected account so that SUM(txs) = account.balance going forward — making
balance_history and the running-balance column in the UI line up with the
card balance.

Idempotent: skips accounts that already have an opening_balance transaction.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from alembic import op
from sqlalchemy import text

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Connected accounts that don't already have an opening_balance tx
    accounts = conn.execute(
        text(
            """
            SELECT a.id, a.user_id, a.balance, a.currency, a.type
            FROM accounts a
            WHERE a.connection_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM transactions t
                  WHERE t.account_id = a.id AND t.source = 'opening_balance'
              )
            """
        )
    ).fetchall()

    for acc in accounts:
        acc_id, user_id, balance, currency, acc_type = acc
        is_cc = acc_type == "credit_card"
        # CC balance is stored as positive debt and displayed negated, so the
        # target that SUM(signed txs) must hit is -balance. Other accounts
        # target the stored balance directly.
        target = (-Decimal(balance)) if is_cc else Decimal(balance)

        row = conn.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(
                        CASE
                            WHEN t.type = 'credit' THEN
                                CASE
                                    WHEN t.currency = :acc_currency THEN t.amount
                                    ELSE COALESCE(t.amount_primary, t.amount)
                                END
                            ELSE
                                -CASE
                                    WHEN t.currency = :acc_currency THEN t.amount
                                    ELSE COALESCE(t.amount_primary, t.amount)
                                END
                        END
                    ), 0) AS tx_sum,
                    MIN(t.date) AS oldest_date
                FROM transactions t
                WHERE t.account_id = :acc_id
                  AND t.source <> 'opening_balance'
                """
            ),
            {"acc_id": acc_id, "acc_currency": currency},
        ).fetchone()
        if row is None:
            continue

        tx_sum = Decimal(str(row[0] or 0))
        oldest_date = row[1]
        offset = (target - tx_sum).quantize(Decimal("0.01"))

        # Sub-cent offsets are rounding noise; no backfill needed.
        if abs(offset) < Decimal("0.01"):
            continue

        opening_type = "credit" if offset > 0 else "debit"
        amount = abs(offset)
        opening_date = (oldest_date - timedelta(days=1)) if oldest_date else None

        if opening_date is None:
            # No transactions yet — skip; first sync will create the opening tx.
            continue

        conn.execute(
            text(
                """
                INSERT INTO transactions (
                    id, user_id, account_id, description, amount, currency,
                    date, effective_date, type, source, status, created_at
                ) VALUES (
                    :id, :user_id, :account_id, :description, :amount, :currency,
                    :date, :effective_date, :type, 'opening_balance', 'posted', NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "account_id": str(acc_id),
                "description": "Saldo inicial",
                "amount": amount,
                "currency": currency,
                "date": opening_date,
                "effective_date": opening_date,
                "type": opening_type,
            },
        )


def downgrade() -> None:
    # Reverse: drop every synthetic opening_balance on connected accounts that
    # this migration could have created. Can't distinguish rows created here
    # from ones created later by the sync service, so we match on description
    # + source + connected-ness, which is the signature this migration writes.
    conn = op.get_bind()
    conn.execute(
        text(
            """
            DELETE FROM transactions
            WHERE source = 'opening_balance'
              AND description = 'Saldo inicial'
              AND account_id IN (
                  SELECT id FROM accounts WHERE connection_id IS NOT NULL
              )
            """
        )
    )
