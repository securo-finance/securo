"""store a transaction's amount in its account's currency

Revision ID: 123
Revises: 122

`amount_primary` was read for two things: a row's value in its owner's
display currency (reports) and, for a row in another currency than its
account, its value in the account's currency (balances, bills). The two only
agree while the account is in the owner's display currency (ADR 0027).

The new column carries the second meaning. It is filled from `amount_primary`
for every row whose currency differs from its account's, which is exactly the
figure the balances read today, so no balance moves on upgrade.

`amount_account_source` says where the value came from, so a stamp that only
heals the reporting value keeps a bank charge or a typed amount. Old rows
do not record it, so it is read from what they still carry: a stored payload
with the bank's account-currency amount (Pluggy's `amountInAccountCurrency`)
is the bank's figure whatever its rate; otherwise a row stamped 1:1 (the
fallback #353 stopped writing) is derived, so the FX healer can still fix it,
a synced row is the bank's and any other the user's.
"""

import sqlalchemy as sa
from alembic import op

revision = "123"
down_revision = "122"
branch_labels = None
depends_on = None

# Exactly the rows the balances read `amount_primary` for: a currency other
# than the account's, compared without case like `amount_in_account_currency`.
BACKFILL_SQL = """
UPDATE transactions
SET amount_account = transactions.amount_primary,
    amount_account_source = CASE
        WHEN transactions.raw_data ->> 'amountInAccountCurrency' IS NOT NULL THEN 'bank'
        WHEN transactions.fx_rate_used = 1 THEN NULL
        WHEN transactions.source = 'sync' THEN 'bank'
        ELSE 'user'
    END
FROM accounts
WHERE accounts.id = transactions.account_id
  AND upper(transactions.currency) <> upper(accounts.currency)
  AND transactions.amount_primary IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("amount_account", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("amount_account_source", sa.String(length=8), nullable=True),
    )
    op.execute(sa.text(BACKFILL_SQL))


def downgrade() -> None:
    op.drop_column("transactions", "amount_account_source")
    op.drop_column("transactions", "amount_account")
