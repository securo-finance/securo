"""add stable_id to accounts (Enable Banking reauth duplication)

Revision ID: 097
Revises: 096
Create Date: 2026-10-02

Enable Banking identifies an account by a `uid` that is scoped to a single
session, so reauthorising (which mints a new session) hands back a new uid for
the same real account. The sync matched accounts only on `external_id`, so it
inserted a second row and left the original — with its transactions, rules and
name overrides — behind.

EB publishes an `identification_hash` precisely to match the same account across
sessions. Persist it here so the sync can rebind the existing row to the new uid
instead of duplicating.

Additive and nullable: existing rows are unaffected and backfill themselves on
the next sync, since `stable_id` is provider-owned like `masked_number`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "097"
down_revision: Union[str, None] = "096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("stable_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "stable_id")
