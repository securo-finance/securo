import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Decisions a human took. The API accepts none of these directly — a
#: status changes through the action that causes it (`/issue`, `/void`),
#: never by PATCHing a string, because "why is this void" must always
#: have an answer.
InvoiceStatus = Literal["draft", "open", "void", "uncollectible"]

#: What a reader sees. The three decisions above plus the four facts
#: computed from allocations and the due date.
InvoiceState = Literal[
    "draft", "open", "partial", "paid", "overdue", "void", "uncollectible"
]

#: Which side of the ledger. Everything the UI writes today is a
#: receivable; `payable` is accepted so supplier documents have somewhere
#: to land without a migration when that path arrives.
InvoiceDirection = Literal["receivable", "payable"]

InvoicePreset = Literal["tracking", "document"]
TaxFieldsMode = Literal["hidden", "optional", "required"]


class InvoiceLineInput(BaseModel):
    description: str = Field(..., max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    #: What the quantity counts — hours, words, pieces. Free text.
    unit: Optional[str] = Field(default=None, max_length=20)
    unit_price: Decimal = Field(default=Decimal("0"))
    # A percentage, not an amount — and only meaningful when the
    # workspace shows tax fields at all.
    tax_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)


class InvoiceLineRead(BaseModel):
    id: uuid.UUID
    description: str
    quantity: Decimal
    unit: Optional[str] = None
    unit_price: Decimal
    tax_rate: Optional[Decimal]
    total: Decimal
    position: int

    model_config = ConfigDict(from_attributes=True)


class AllocationTransaction(BaseModel):
    """Just enough of the transaction to render the row it came from."""

    id: uuid.UUID
    description: Optional[str] = None
    date: Optional[_Date] = None
    amount: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class InvoiceAllocationRead(BaseModel):
    id: uuid.UUID
    transaction_id: Optional[uuid.UUID]
    credit_note_id: Optional[uuid.UUID]
    amount: Decimal
    method: str
    allocated_at: datetime
    transaction: Optional[AllocationTransaction] = None

    model_config = ConfigDict(from_attributes=True)


#: Where a document came from. `local` is one someone typed here;
#: `imported` is one that arrived from somewhere else and was
#: reconstructed — a gateway sync, a forwarded email, a photographed
#: supplier invoice. The *source* goes in `external_source`.
InvoiceOrigin = Literal["local", "imported"]


class InvoiceCreate(BaseModel):
    direction: Optional[InvoiceDirection] = None
    #: Provenance. Every future intake — gateway sync, email, upload,
    #: OCR — writes through these three fields, which is why they are
    #: accepted here before any of those intakes exist.
    origin: Optional[InvoiceOrigin] = None
    external_source: Optional[str] = Field(default=None, max_length=50)
    external_id: Optional[str] = Field(default=None, max_length=255)
    #: The name the source gave it, exactly as it reads. Text, not an
    #: integer plus a series: `2026/A/0031` is a name, and treating it as
    #: a number loses the padding and invents a sequence we do not own.
    #: Ignored on a document written here, which is numbered from this
    #: workspace's own counter.
    external_number: Optional[str] = Field(default=None, max_length=60)
    #: Keep it a draft even where the workspace would open it. Not a
    #: status field — the caller cannot ask for `void` here — only the
    #: difference between "I am still writing this" and "this is owed".
    as_draft: bool = False
    payee_id: Optional[uuid.UUID] = None
    issue_date: Optional[_Date] = None
    # Optional: falls back to the workspace's default payment terms, so
    # the three-field flow really is three fields.
    due_date: Optional[_Date] = None
    competence_date: Optional[_Date] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    total: Optional[Decimal] = Field(default=None, ge=0)
    discount: Optional[Decimal] = Field(default=None, ge=0)
    subtotal: Optional[Decimal] = Field(default=None, ge=0)
    tax_total: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None
    lines: Optional[list[InvoiceLineInput]] = None


class InvoiceUpdate(BaseModel):
    payee_id: Optional[uuid.UUID] = None
    issue_date: Optional[_Date] = None
    due_date: Optional[_Date] = None
    competence_date: Optional[_Date] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    total: Optional[Decimal] = Field(default=None, ge=0)
    discount: Optional[Decimal] = Field(default=None, ge=0)
    subtotal: Optional[Decimal] = Field(default=None, ge=0)
    tax_total: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    custom_fields: Optional[dict[str, Any]] = None
    lines: Optional[list[InvoiceLineInput]] = None


class InvoicePayee(BaseModel):
    id: uuid.UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class InvoiceRead(BaseModel):
    id: uuid.UUID
    payee_id: Optional[uuid.UUID]
    payee: Optional[InvoicePayee] = None
    document_type: str
    direction: InvoiceDirection
    origin: str
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    number: Optional[int]
    series: Optional[str]
    external_number: Optional[str] = None
    status: InvoiceStatus
    #: The derived answers. Defaults exist only so the ORM row can be
    #: validated before they are attached; `api/invoices.py::_serialize`
    #: is the sole constructor and always fills all four. They are never
    #: stored — see `invoice_service.derive_state`.
    state: InvoiceState = "draft"
    issue_date: _Date
    due_date: _Date
    competence_date: Optional[_Date]
    sent_at: Optional[datetime]
    currency: str
    subtotal: Decimal
    discount: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    days_overdue: int = 0
    notes: Optional[str]
    internal_notes: Optional[str]
    custom_fields: Optional[dict[str, Any]]
    snapshot: Optional[dict[str, Any]]
    #: Present once the invoice has been issued: a link anyone holding it
    #: can open. Null until someone asks for one, and null again once
    #: revoked.
    share_token: Optional[str] = None
    lines: list[InvoiceLineRead] = []
    allocations: list[InvoiceAllocationRead] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AllocationCreate(BaseModel):
    transaction_id: uuid.UUID
    #: Omitted means "as much as fits": the smaller of what remains on
    #: the invoice and what the transaction carries.
    amount: Optional[Decimal] = Field(default=None, gt=0)


class AgingBuckets(BaseModel):
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal


class InvoiceSummary(BaseModel):
    outstanding: Decimal
    overdue_amount: Decimal
    overdue_count: int
    received_this_month: Decimal
    buckets: AgingBuckets
    upcoming: list[InvoiceRead] = []


class IssuerTaxIdInput(BaseModel):
    kind: str
    value: str = Field(..., max_length=120)


class IssuerTaxIdRead(BaseModel):
    kind: str
    value: str

    model_config = ConfigDict(from_attributes=True)


class IssuerProfileRead(BaseModel):
    """The workspace describing itself, as the issuer block on a document."""

    legal_name: Optional[str]
    address: Optional[str]
    tax_jurisdiction: Optional[str]
    tax_ids: list[IssuerTaxIdRead] = []


class IssuerProfileUpdate(BaseModel):
    legal_name: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = Field(default=None, max_length=500)
    #: Omit to leave documents untouched; send a list to replace the set.
    tax_ids: Optional[list[IssuerTaxIdInput]] = None


class ShareLinkRead(BaseModel):
    token: str
    #: Path rather than an absolute URL: the server does not reliably know
    #: the public origin behind a reverse proxy, and the frontend does.
    path: str


class InvoiceFacetCounts(BaseModel):
    all: int
    open: int
    overdue: int
    paid: int
    draft: int


class InvoiceFacets(BaseModel):
    """What the filter bar needs to render itself.

    `years` is every year that has an invoice, newest first, and is never
    narrowed by the `year` parameter — otherwise picking a year would
    remove every other year from the picker.
    """

    years: list[int]
    counts: InvoiceFacetCounts


class InvoiceSettingsRead(BaseModel):
    preset: InvoicePreset
    document_required: bool
    initial_state: Literal["draft", "open"]
    tax_fields: TaxFieldsMode
    default_payment_terms_days: int
    number_prefix: Optional[str]
    series: Optional[str]
    next_number: int
    logo_id: Optional[uuid.UUID] = None
    issuer_display_name: Optional[str]
    footer_note: Optional[str]
    payment_details: Optional[str]
    accent_color: Optional[str]
    template: Optional[dict[str, Any]]

    model_config = ConfigDict(from_attributes=True)


class InvoiceSettingsUpdate(BaseModel):
    preset: Optional[InvoicePreset] = None
    document_required: Optional[bool] = None
    initial_state: Optional[Literal["draft", "open"]] = None
    tax_fields: Optional[TaxFieldsMode] = None
    default_payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    #: These five are blankable on purpose — clearing a logo is a thing
    #: people do, and it must not read as "leave it alone".
    number_prefix: Optional[str] = Field(default=None, max_length=20)
    series: Optional[str] = Field(default=None, max_length=20)
    issuer_display_name: Optional[str] = Field(default=None, max_length=255)
    footer_note: Optional[str] = None
    #: Free text on purpose — a Pix key, an IBAN and a routing number have
    #: nothing structural in common, and picking one shape would pick a
    #: country.
    payment_details: Optional[str] = None
    accent_color: Optional[str] = Field(default=None, max_length=9)
    #: Labels and custom-field definitions. Free-form by design: adding a
    #: field a workspace needs must never be a database migration.
    template: Optional[dict[str, Any]] = None


#: What a filed document proves. Roles, not file types — the vocabulary
#: that names them for a user comes from the jurisdiction pack.
InvoiceAttachmentKind = Literal["bill", "fiscal", "receipt", "contract", "other"]


class InvoiceAttachmentRead(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    #: The system that produced or delivered the file. Null when a person
    #: uploaded it here.
    source: Optional[str] = None
    external_id: Optional[str] = None
    kind: str
    #: True on the one file that *is* the document. What `/pdf` serves and
    #: what the screen shows instead of a page drawn from our own fields.
    is_primary: bool
    document_number: Optional[str] = None
    issued_at: Optional[_Date] = None
    filename: str
    content_type: str
    size: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceAttachmentUpdate(BaseModel):
    kind: Optional[InvoiceAttachmentKind] = None
    source: Optional[str] = Field(default=None, max_length=50)
    document_number: Optional[str] = Field(default=None, max_length=120)
    issued_at: Optional[_Date] = None
    is_primary: Optional[bool] = None
