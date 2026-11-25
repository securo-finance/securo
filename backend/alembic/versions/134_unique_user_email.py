"""one account per login address, whatever its case

Revision ID: 134
Revises: 133

An address names one mailbox, but the column's unique index compares it byte
for byte: an instance that ran a release before the case-insensitive checks
can already hold `ana@example.com` and `Ana@example.com` as two accounts. A
lookup that expected one row raised, so both accounts answered 500 from every
screen that names them by address, sign-in included (#568).

The row every lookup answers with is the lowest id
(`account_lookup_service.find_account_by_email`), so that one keeps the
address. The others are moved to an address derived from their own id and
deactivated: they answered a mailbox that already belongs to another account,
which is exactly what the person on the other side never asked for. Their
data stays, and an administrator can still give the account a free address.

Safe to run twice: only rows that still share an address are touched, and the
index is created only when it is missing.
"""

import sqlalchemy as sa
from alembic import op

revision = "134"
down_revision = "133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY lower(email)
                        ORDER BY id
                    ) AS rn
                FROM users
            )
            UPDATE users u
            SET email = (
                    split_part(u.email, '@', 1)
                    || '+duplicate-'
                    || left(replace(u.id::text, '-', ''), 8)
                    || '@'
                    || split_part(u.email, '@', 2)
                ),
                is_active = false
            FROM ranked
            WHERE u.id = ranked.id
              AND ranked.rn > 1
            """
        )
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_lower_email ON users (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_lower_email")
