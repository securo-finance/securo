"""enrich payees with contact + fiscal data, and give workspaces a tax jurisdiction

Revision ID: 070
Revises: 069
Create Date: 2026-08-14

Three additive changes, none of them requiring a backfill:

  - Contact columns on `payees`, all nullable. Sync creates a payee for
    every merchant a card touches, and the overwhelming majority keep every
    one of these null forever. That is the expected end state, not an
    incomplete migration.
  - `payee_tax_ids`, one row per fiscal document. Rows rather than columns
    because a counterparty legitimately has several (CNPJ plus state and
    municipal registrations in Brazil, Partita IVA plus Codice Fiscale plus
    an SDI code in Italy), and keyed by `kind` rather than by position so a
    stored value keeps its meaning regardless of which jurisdiction pack was
    loaded when it was written.
  - `workspaces.tax_jurisdiction`, which selects that pack. Nullable, and
    null is a working configuration: the registry falls back to free-text
    documents, so a country without a pack works on day one.

Plus one change that is not additive, and is here rather than later because
later is too late: `payees.source`, and the retirement of the `merchant`
value from `payees.type`.

`type` now holds a legal nature (`person` or `company`) or null. It used to
also hold `merchant`, which is a *role* — something a counterparty does,
not what it legally is. That value was the only thing distinguishing the
hundreds of rows sync creates from card descriptors, so it is harvested
into `source` before being dropped rather than discarded.

`source` has to be added now: provenance is only knowable at the moment of
insert, so any payee created before the column exists could never be
classified afterwards. The seeding below is the one and only chance to
recover it for existing rows, and it is an inference, not a record — a
payee somebody typed in by hand and left at the old `merchant` default is
indistinguishable from one sync created, and lands in `sync`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payees", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("payees", sa.Column("phone", sa.String(length=50), nullable=True))
    op.add_column("payees", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("payees", sa.Column("website", sa.String(length=255), nullable=True))

    op.add_column(
        "workspaces",
        sa.Column("tax_jurisdiction", sa.String(length=10), nullable=True),
    )

    op.create_table(
        "payee_tax_ids",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("value", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["payee_id"], ["payees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One document of a given kind per payee. A second CNPJ on the same
        # counterparty is a data-entry mistake, not a valid state.
        sa.UniqueConstraint("payee_id", "kind", name="uq_payee_tax_id_kind"),
    )
    op.create_index("ix_payee_tax_ids_payee_id", "payee_tax_ids", ["payee_id"])
    op.create_index("ix_payee_tax_ids_workspace_id", "payee_tax_ids", ["workspace_id"])
    # Matching a payment to a counterparty by document is the lookup this
    # table exists to make cheap.
    op.create_index(
        "ix_payee_tax_ids_workspace_kind_value",
        "payee_tax_ids",
        ["workspace_id", "kind", "value"],
    )

    # Added with a default so existing rows are valid immediately, then the
    # default is dropped: provenance must be stated by whoever inserts, never
    # silently assumed for future writes.
    op.add_column(
        "payees",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
    )
    # Harvest the signal `merchant` was carrying before retiring it. This is
    # the last moment it exists.
    op.execute("UPDATE payees SET source = 'sync' WHERE type = 'merchant'")
    op.alter_column("payees", "source", server_default=None)

    # `merchant` is not a legal nature, and no honest one can be derived from
    # a bank descriptor, so those rows become unknown rather than being
    # guessed into `company`.
    #
    # The server default has to go with it. An insert that omits `type` —
    # which is what the bulk sync path emits, since it never sets one —
    # would otherwise be handed `merchant` by the database and quietly
    # resurrect the value this migration exists to retire. Nothing in the
    # test suite would notice: tests build their schema from the model,
    # which never carried this default, so only the migrated database has
    # it.
    op.alter_column(
        "payees",
        "type",
        existing_type=sa.String(length=20),
        nullable=True,
        server_default=None,
    )
    op.execute("UPDATE payees SET type = NULL WHERE type = 'merchant'")


def downgrade() -> None:
    # Restore `merchant` for the rows it was inferred from. Anything whose
    # legal nature was set after this migration keeps it.
    op.execute("UPDATE payees SET type = 'merchant' WHERE type IS NULL")
    op.alter_column(
        "payees",
        "type",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="merchant",
    )
    op.drop_column("payees", "source")
    op.drop_index("ix_payee_tax_ids_workspace_kind_value", table_name="payee_tax_ids")
    op.drop_index("ix_payee_tax_ids_workspace_id", table_name="payee_tax_ids")
    op.drop_index("ix_payee_tax_ids_payee_id", table_name="payee_tax_ids")
    op.drop_table("payee_tax_ids")
    op.drop_column("workspaces", "tax_jurisdiction")
    op.drop_column("payees", "website")
    op.drop_column("payees", "address")
    op.drop_column("payees", "phone")
    op.drop_column("payees", "email")
