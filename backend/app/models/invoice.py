"""The invoicing ledger: what a client owes, and what settled it.

Four tables and one rule that shapes all of them:

    Stored status is a decision a human took.
    Derived status is a fact about money and time.

`Invoice.status` therefore holds only decisions — someone issued it,
someone voided it, someone gave up on it. Whether an invoice is paid,
partially paid or overdue is computed from its allocations and its due
date, never written down. Peers that store `overdue` need a job to set
it and compensating SQL to unset it when a due date moves, and every
query downstream has to remember to say `IN ('unpaid', 'overdue')`.

Nothing here is jurisdiction-specific. The schema carries *shapes* — a
document type, a fiscal reference, a competence date — and the
jurisdiction packs (`app/fiscal/`) carry the vocabulary that fills them.
Brazil is the stress test because it is among the most demanding, not
because the model is shaped around it: the same tables serve a French
freelancer issuing a Factur-X invoice and a US contractor issuing
nothing at all.
"""
import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.invoice_attachment import InvoiceAttachment
    from app.models.payee import Payee
    from app.models.transaction import Transaction


#: Decisions a human takes about an invoice. Closed, and closed on
#: decisions only.
#:
#:   draft         — being written. Mutable, deletable, counted nowhere.
#:   open          — issued. Carries a number, appears in receivables.
#:   void          — cancelled. Keeps its number, reads as zero, terminal.
#:   uncollectible — given up on. Leaves aging, never counts as paid.
#:
#: `paid`, `partial` and `overdue` are deliberately absent: they are
#: derived (see `invoice_service.derive_state`).
INVOICE_STATUSES = ("draft", "open", "void", "uncollectible")

#: UNTDID 1001 document types, by their common names. Only `invoice`
#: (380) is accepted today; `credit_note` (381) is reserved so the column
#: exists before the feature does, because adding it later means
#: backfilling every row in every self-hosted install.
INVOICE_DOCUMENT_TYPES = ("invoice", "credit_note")

#: Which side of the ledger a document sits on.
#:
#:   receivable — we issued it; the money comes in
#:   payable    — we received it; the money goes out
#:
#: Kept **separate from `document_type`** rather than folded into a single
#: four-value enum (out_invoice / out_refund / in_invoice / in_refund, as
#: some accounting systems do). They answer different questions — which
#: direction the money moves, and what kind of document this is — and
#: crossing them means every new document type doubles the enum.
#:
#: Everything issued today is `receivable`. `payable` exists now because
#: supplier invoices are a stated direction for this module, and a column
#: added later is a migration over every row in every self-hosted install.
INVOICE_DIRECTIONS = ("receivable", "payable")

#: Who authored the document. `imported` rows are reconstructed from an
#: external system (Stripe, Asaas, a CSV): that system owns the document,
#: and Securo owns the cash that settled it.
INVOICE_ORIGINS = ("local", "imported")

#: How an allocation came to exist.
#:
#: Deliberately **not** a closed set. `manual` is a person pointing at a
#: transaction; everything else is the **id of the matching strategy that
#: produced the row**, and those ids come from the reconciliation policy —
#: a document the user will eventually edit, not an enum this file owns.
#: Closing this tuple would mean a migration every time somebody adds a
#: strategy, which is the opposite of the point.
#:
#: What it buys: the ledger can answer *why* a link exists and not merely
#: that it does. "Matched by: same client, net of withholding" is a
#: sentence; `payee_join` was a shrug.
MANUAL_METHOD = "manual"


class Invoice(Base):
    """One expected payment, with an optional document attached to it."""

    __tablename__ = "invoices"
    __table_args__ = (
        # A number is unique within its series, within a workspace. Drafts
        # carry NULL and are exempt for free: SQL treats NULLs as distinct
        # in a unique index, which is exactly the "unique when present"
        # rule wanted here.
        UniqueConstraint(
            "workspace_id", "series", "number", name="uq_invoices_workspace_series_number"
        ),
        # An imported document is identified by its source's own id, so
        # two syncs of the same Stripe invoice converge on one row.
        UniqueConstraint(
            "workspace_id",
            "external_source",
            "external_id",
            name="uq_invoices_workspace_external",
        ),
        # Leads with direction because every list starts by choosing a
        # side of the ledger; status and due date narrow within it.
        Index(
            "ix_invoices_workspace_direction_status_due",
            "workspace_id",
            "direction",
            "status",
            "due_date",
        ),
        CheckConstraint(
            "status IN ('draft', 'open', 'void', 'uncollectible')",
            name="ck_invoices_status",
        ),
        CheckConstraint(
            "document_type IN ('invoice', 'credit_note')",
            name="ck_invoices_document_type",
        ),
        CheckConstraint(
            "direction IN ('receivable', 'payable')", name="ck_invoices_direction"
        ),
        CheckConstraint("origin IN ('local', 'imported')", name="ck_invoices_origin"),
        # A draft has no number, and anything we issued has one. Written as
        # a constraint because the alternative is trusting every future
        # code path to remember.
        #
        # An imported row never carries one of ours at all: its name lives
        # in `external_number`, and leaving this column null is what keeps
        # our sequence answerable for nothing but our own documents.
        CheckConstraint(
            "(origin = 'imported' AND number IS NULL)"
            " OR (status = 'draft' AND number IS NULL)"
            " OR (status <> 'draft' AND number IS NOT NULL)",
            name="ck_invoices_number_matches_status",
        ),
        CheckConstraint("total >= 0", name="ck_invoices_total_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Who created the row. Not an owner: an invoice belongs to the
    # workspace, and a member leaving does not take it with them.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # The client. Nullable because an imported document may name a
    # counterparty this workspace has never seen, and refusing the import
    # over it would be worse than storing the name in the snapshot.
    #
    # RESTRICT, not CASCADE: deleting a payee must never silently delete
    # the record of money someone owed.
    payee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payees.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    document_type: Mapped[str] = mapped_column(String(20), default="invoice", server_default="invoice")
    direction: Mapped[str] = mapped_column(
        String(20), default="receivable", server_default="receivable"
    )
    # Lineage for a credit note: which document it corrects. Provenance
    # only — applying a credit is an allocation, and this column never
    # takes part in the arithmetic.
    corrects_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )

    origin: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    external_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Assigned at issuance, gapless within a series, never reused — not
    # even by a voided invoice, which keeps the number it was given.
    #
    # Ours alone. An imported document is named in `external_number`
    # instead: an integer plus a series cannot hold `2026/A/0031` without
    # dropping the padding and turning the series into a workaround, and
    # a source's numbering is not a sequence we may reason about — only a
    # name we must reproduce exactly.
    number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    series: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    external_number: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft", index=True)

    issue_date: Mapped[_date] = mapped_column(Date)
    due_date: Mapped[_date] = mapped_column(Date)
    # The accountant's axis, and only it. Defaults to `issue_date`, but is
    # its own field because the two genuinely diverge: work delivered in
    # July and invoiced in August is booked in July under accrual
    # accounting — competência in Brazil, fait générateur in France, and
    # the Leistungsdatum that German law requires on the invoice itself.
    competence_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    # A timestamp, not a status. It distinguishes "sent, awaiting payment"
    # from "recorded but never sent" without inflating the enum.
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    currency: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    discount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    # Tax charged TO the client — part of what they owe. Tax withheld BY
    # the client is not this: that reduces the cash without reducing the
    # debt, and lands in the deductions table T6 introduces.
    tax_total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Everything the document said about itself when it was issued:
    # issuer identity, counterparty details, the labels and logo in force.
    # Frozen on purpose — renaming a client or changing a logo in
    # September must not rewrite August's invoice.
    snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # Values for fields this workspace defined (a PO number, a project
    # reference, a municipal service code). The *definitions* live in
    # settings, so a new field is a settings edit and never a migration.
    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Null until someone asks for a shareable link, and nulled again to
    # revoke one. Unique so the public lookup is a single indexed read.
    share_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    payee: Mapped[Optional["Payee"]] = relationship(lazy="joined")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.position",
        lazy="selectin",
    )
    # `foreign_keys` is not optional here: an allocation points at
    # `invoices` twice — once for the debt it settles, once for a credit
    # note that settles it — and SQLAlchemy cannot guess which edge this
    # collection means.
    allocations: Mapped[list["InvoiceAllocation"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        foreign_keys="InvoiceAllocation.invoice_id",
        lazy="selectin",
    )
    # The paper gathered under this debt. Deleting the invoice takes the
    # rows with it; the stored files are removed by the service, which is
    # the only layer that can talk to the storage provider.
    attachments: Mapped[list["InvoiceAttachment"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceAttachment.created_at",
        lazy="selectin",
    )


class InvoiceLine(Base):
    """One billed item. Optional under the `tracking` preset."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        Index("ix_invoice_lines_invoice", "invoice_id"),
        CheckConstraint("quantity >= 0", name="ck_invoice_lines_quantity_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=4), default=Decimal("1"))
    # What the quantity counts — hours, days, pieces, kilos. Free text
    # rather than a list: a translator bills by word, a photographer by
    # image, a freight company by tonne-kilometre, and an enum here would
    # be a guess about somebody else's trade. Null keeps the line reading
    # exactly as it does today.
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    # A rate, not an amount, and nullable: most workspaces under the
    # `tracking` preset never fill it in.
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=7, scale=4), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0"))
    position: Mapped[int] = mapped_column(Integer, default=0)

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")


class InvoiceAllocation(Base):
    """A slice of money bound to a slice of debt.

    N:N with an amount, deliberately. A gateway payout is one bank credit
    settling many invoices net of fees; a Pix-paid invoice is one debt
    settled by several credits. A foreign key on the transaction could
    express neither, and would put a column on the hottest table in the
    system to do it — so the whole relationship lives here, and
    `transactions` is untouched by this feature.
    """

    __tablename__ = "invoice_allocations"
    __table_args__ = (
        Index("ix_invoice_allocations_invoice", "invoice_id"),
        Index("ix_invoice_allocations_transaction", "transaction_id"),
        CheckConstraint("amount > 0", name="ck_invoice_allocations_amount_positive"),
        # Exactly one source. A row that settles from both a transaction
        # and a credit note is not a richer row, it is an ambiguous one.
        CheckConstraint(
            "(transaction_id IS NOT NULL AND credit_note_id IS NULL)"
            " OR (transaction_id IS NULL AND credit_note_id IS NOT NULL)",
            name="ck_invoice_allocations_one_source",
        ),
        # The same transaction is never applied twice to the same invoice.
        # Two genuine partial payments are two different transactions.
        UniqueConstraint("invoice_id", "transaction_id", name="uq_invoice_allocation_transaction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # CASCADE is right here and nowhere else in this file: if the
    # transaction is gone, the money it represented is gone, and a link
    # pointing at nothing would overstate what was received.
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True
    )
    credit_note_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    # Wide enough for a strategy id, not for an enum value: the policy's
    # own ids already run to 30 characters. See `MANUAL_METHOD`.
    method: Mapped[str] = mapped_column(String(60), default="manual", server_default="manual")
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="allocations", foreign_keys=[invoice_id])
    transaction: Mapped[Optional["Transaction"]] = relationship(lazy="joined")


class InvoiceSettings(Base):
    """How this workspace's invoicing behaves and how it looks.

    One row per workspace. The two presets exist so the Brazilian
    tracking flow and the international document flow are the same
    product configured differently — never two code paths, and never a
    branch on the UI language. A jurisdiction may *suggest* a preset; it
    never imposes one, because a Brazilian MEI serving a foreign client
    has every reason to pick the other.
    """

    __tablename__ = "invoice_settings"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_invoice_settings_workspace"),
        CheckConstraint("preset IN ('tracking', 'document')", name="ck_invoice_settings_preset"),
        CheckConstraint(
            "initial_state IN ('draft', 'open')", name="ck_invoice_settings_initial_state"
        ),
        CheckConstraint(
            "tax_fields IN ('hidden', 'optional', 'required')",
            name="ck_invoice_settings_tax_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )

    # The preset is a starting point, not a mode: it fills the three
    # fields below, each individually overridable afterwards.
    preset: Mapped[str] = mapped_column(String(20), default="tracking", server_default="tracking")
    document_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    initial_state: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    tax_fields: Mapped[str] = mapped_column(String(10), default="hidden", server_default="hidden")
    default_payment_terms_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")

    # Numbering. `next_number` is the counter the issue path consumes;
    # gapless means it is never rolled back, not even when an invoice is
    # voided one second after being issued.
    number_prefix: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    series: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    next_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Presentation.
    #
    # The logo is a file this workspace uploaded, not a URL pointing
    # somewhere else. A remote address would fetch the mark from a third
    # party every time a document is drawn — on a self-hosted install
    # that is a request out of the building for something the user
    # already owns, and it breaks the day that host does.
    #
    # Stored as an id rather than a path: the storage key is derived from
    # it, every upload mints a new one, and the old file is left in place
    # because an invoice issued last month froze *that* id in its
    # snapshot. Replacing a logo must not repaint a document a client
    # already holds.
    logo_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    issuer_display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    footer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Free text, deliberately. A Brazilian writes a Pix key, a German an
    # IBAN and a BIC, an American a routing number — structuring this
    # would bake one country's shape into the schema.
    payment_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The one visual knob worth having: a document that carries the
    # sender's colour reads as theirs rather than as the tool's.
    accent_color: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)

    # Labels and custom-field definitions. One jsonb rather than a column
    # per label: peers ship twenty columns for this and pay a migration
    # every time a label is added.
    template: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
