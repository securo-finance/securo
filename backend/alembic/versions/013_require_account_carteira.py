"""require account — create Carteira default accounts and make account_id NOT NULL

Revision ID: 013
Revises: 012
Create Date: 2026-03-02
"""

from typing import Sequence, Union

import uuid as _uuid

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Find all users who have orphan transactions (account_id IS NULL)
    orphan_users = conn.execute(
        sa.text("SELECT DISTINCT user_id FROM transactions WHERE account_id IS NULL")
    ).fetchall()

    for (user_id,) in orphan_users:
        # Check if user already has a "Carteira" account
        existing = conn.execute(
            sa.text(
                "SELECT id FROM accounts WHERE user_id = :uid AND name = 'Carteira' AND connection_id IS NULL"
            ),
            {"uid": user_id},
        ).fetchone()

        if existing:
            carteira_id = existing[0]
        else:
            carteira_id = str(_uuid.uuid4())
            conn.execute(
                sa.text(
                    "INSERT INTO accounts (id, user_id, name, type, balance, currency, is_closed) "
                    "VALUES (:id, :uid, 'Carteira', 'checking', 0, 'BRL', false)"
                ),
                {"id": carteira_id, "uid": user_id},
            )

        # Reassign orphan transactions to the Carteira account
        conn.execute(
            sa.text(
                "UPDATE transactions SET account_id = :aid WHERE user_id = :uid AND account_id IS NULL"
            ),
            {"aid": carteira_id, "uid": user_id},
        )

    # 2. Create Carteira for users who don't have any account yet
    users_without_accounts = conn.execute(
        sa.text(
            "SELECT id FROM users WHERE id NOT IN (SELECT DISTINCT user_id FROM accounts WHERE user_id IS NOT NULL)"
        )
    ).fetchall()

    for (user_id,) in users_without_accounts:
        carteira_id = str(_uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO accounts (id, user_id, name, type, balance, currency, is_closed) "
                "VALUES (:id, :uid, 'Carteira', 'checking', 0, 'BRL', false)"
            ),
            {"id": carteira_id, "uid": user_id},
        )

    # 3. Make account_id NOT NULL
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.alter_column("account_id", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.alter_column("account_id", existing_type=sa.Uuid(), nullable=True)
