"""store a transaction's amount in its account's currency

Revision ID: 123
Revises: 122

`amount_primary` was read for two things: a row's value in its owner's
display currency (reports) and, for a row in another currency than its
account, its value in the account's currency (balances, bills). The two only
agree while the account is in the owner's display currency (ADR 0027).

The new column carries the second meaning. It is filled from `amount_primary`
for every row whose currency differs from its account's, which is exactly the
figure the balances read today. The one exception is a row whose stored
payload still says what the bank charged the account (Pluggy's
`amountInAccountCurrency`): it takes that figure, which is the balance it
should always have had.

`amount_account_source` says where the value came from, so a stamp that only
heals the reporting value keeps a bank charge or a typed amount. Old rows do
not record it: a synced row is taken as the bank's figure, a manual row as the
user's, except a manual row stamped 1:1 (the fallback #353 stopped writing),
which is derived so the FX healer can still fix it.
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
SET amount_account = CASE
        WHEN json_typeof(transactions.raw_data::json -> 'amountInAccountCurrency') = 'number'
        THEN round(abs((transactions.raw_data::json ->> 'amountInAccountCurrency')::numeric), 2)
        ELSE transactions.amount_primary
    END,
    amount_account_source = CASE
        WHEN json_typeof(transactions.raw_data::json -> 'amountInAccountCurrency') = 'number'
            THEN 'bank'
        WHEN transactions.amount_primary IS NULL THEN NULL
        WHEN transactions.source = 'sync' THEN 'bank'
        WHEN transactions.fx_rate_used = 1 THEN NULL
        ELSE 'user'
    END
FROM accounts
WHERE accounts.id = transactions.account_id
  AND upper(transactions.currency) <> upper(accounts.currency)
  AND (
    transactions.amount_primary IS NOT NULL
    OR json_typeof(transactions.raw_data::json -> 'amountInAccountCurrency') = 'number'
  )
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
