"""disambiguate existing synced wallets with duplicate names

Revision ID: 035
Revises: 034
Create Date: 2026-04-20

Migration 034 auto-created one wallet per bank connection named after
the institution. Sandbox users hit the case where several connections
come back with the same institution_name (e.g. "MeuPluggy"), leaving
multiple identically-named wallets that are hard to tell apart.

This migration appends " 2", " 3", ... to the second+ occurrence of any
duplicated wallet name per user. Oldest wallet keeps the bare name (by
created-like order: the `id` UUIDv4 isn't time-ordered, so we fall back
to sort by `position` which was set by creation order in 034).

Runs once and is safe to re-run — it only touches rows whose name is
still exactly the duplicate base; wallets the user already renamed are
left alone.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # For each (user_id, name) with multiple rows, rename the 2nd, 3rd, ...
    # by appending a numeric suffix. Uses window functions for a single pass.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    name,
                    ROW_NUMBER() OVER (
                        PARTITION BY user_id, name
                        ORDER BY position, id
                    ) AS rn,
                    COUNT(*) OVER (PARTITION BY user_id, name) AS total
                FROM asset_groups
            )
            UPDATE asset_groups ag
            SET name = ranked.name || ' ' || ranked.rn
            FROM ranked
            WHERE ag.id = ranked.id
              AND ranked.total > 1
              AND ranked.rn > 1
            """
        )
    )


def downgrade() -> None:
    # No reliable inverse — we can't tell which wallets were renamed by
    # this migration vs the user. Leaving as a no-op is safer than
    # stripping suffixes and potentially clobbering user edits.
    pass
