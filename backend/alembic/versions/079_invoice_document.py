"""the invoice document: issuer fiscal identity, payment details, share links

Revision ID: 079
Revises: 078
Create Date: 2026-08-26

Three groups of change, all additive.

**The issuer side of the fiscal pack.** `workspace_tax_ids` mirrors
`payee_tax_ids` from `070` exactly — same closed `kind` vocabulary, same
named validators, same normalisation. Two small tables rather than one
with a polymorphic owner: the cardinality differs by orders of magnitude
(one row set per workspace against thousands of payees), and a
discriminator column would buy nothing but a filter on every query.

`workspaces.legal_name` and `workspaces.address` sit on the workspace
rather than in `invoice_settings` because they are the workspace
describing *itself*, not a preference about invoicing — fiscal documents
will want the same two fields, and a personal workspace simply leaves
them null forever, which is the expected end state and not an incomplete
migration.

**What the document needs to render.** `payment_details` is deliberately
free text: a Brazilian writes a Pix key, a German an IBAN and BIC, an
American a routing number. Structuring it would bake one country's shape
into the schema, which is exactly the rule this model refuses.
`accent_color` is the one visual knob worth having — a document that
carries the sender's colour reads as theirs.

**Delivery.** `share_token` is null until someone asks for a link, and
nulling it again revokes one. Unique so the public lookup is a single
indexed read, and long enough that guessing is not a strategy.

Downgrade drops all of it. Nothing outside these columns references them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- T10: the workspace's own fiscal identity -------------------------
    op.create_table(
        "workspace_tax_ids",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        # A value from `fiscal.registry.TaxIdKind`. A string so a new kind is
        # a pull request rather than a migration, kept closed by validation
        # on the way in — the same trade `payee_tax_ids` makes.
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("value", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One document per kind: a workspace has one CNPJ, not several.
        sa.UniqueConstraint("workspace_id", "kind", name="uq_workspace_tax_id_kind"),
    )
    op.create_index("ix_workspace_tax_ids_workspace_id", "workspace_tax_ids", ["workspace_id"])

    op.add_column("workspaces", sa.Column("legal_name", sa.String(length=255), nullable=True))
    op.add_column("workspaces", sa.Column("address", sa.String(length=500), nullable=True))

    # --- T7: what the rendered document carries ---------------------------
    op.add_column("invoice_settings", sa.Column("payment_details", sa.Text(), nullable=True))
    op.add_column(
        "invoice_settings", sa.Column("accent_color", sa.String(length=9), nullable=True)
    )

    op.add_column("invoices", sa.Column("share_token", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_invoices_share_token", "invoices", ["share_token"])


def downgrade() -> None:
    op.drop_constraint("uq_invoices_share_token", "invoices", type_="unique")
    op.drop_column("invoices", "share_token")
    op.drop_column("invoice_settings", "accent_color")
    op.drop_column("invoice_settings", "payment_details")
    op.drop_column("workspaces", "address")
    op.drop_column("workspaces", "legal_name")
    op.drop_index("ix_workspace_tax_ids_workspace_id", table_name="workspace_tax_ids")
    op.drop_table("workspace_tax_ids")
