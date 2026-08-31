"""store the masked card number on credit-card transactions

Revision ID: 078
Revises: 077
Create Date: 2026-08-30

Only the provider-normalized final four characters are persisted. A card
account can represent several physical or virtual cards, so Account's own
masked number cannot answer which card made a transaction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("card_masked_number", sa.String(length=4), nullable=True))
    op.create_index(
        "ix_transactions_account_card_masked_number",
        "transactions",
        ["account_id", "card_masked_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_account_card_masked_number", table_name="transactions")
    op.drop_column("transactions", "card_masked_number")
