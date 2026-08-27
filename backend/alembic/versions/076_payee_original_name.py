"""remember what the provider called a payee, so renaming one survives sync

Revision ID: 076
Revises: 075
Create Date: 2026-08-25

Sync identified a counterparty by its display name alone, which is the one
field a person is invited to change. Correcting a payee the bank had named
after a document therefore made the next sync fail to recognise it and
insert a duplicate.

The backfill is the point of this migration: setting `original_name` to the
current `name` for every existing row makes every payee already in the
database rename-safe immediately, with no heuristics and nothing to detect.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payees", sa.Column("original_name", sa.String(255), nullable=True))

    # Every row that exists now was named by whoever created it, and for the
    # sync-created majority that is exactly the string the provider will send
    # again on the next run. Seeding it is what lets an existing install
    # rename a payee without waiting for it to be re-synced first.
    op.execute("UPDATE payees SET original_name = name")

    # Deliberately not unique. Two payees can legitimately end up sharing an
    # original_name — after a merge, or alongside a hand-created row — and a
    # constraint would turn that into a sync-time IntegrityError, which is
    # the failure this whole change exists to stop causing (issue #678).
    op.create_index(
        "ix_payees_workspace_original_name",
        "payees",
        ["workspace_id", "original_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_payees_workspace_original_name", table_name="payees")
    op.drop_column("payees", "original_name")
