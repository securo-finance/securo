"""What a workspace changed about matching, and what matching is unsure of.

Two tables, and they answer two different questions.

`reconciliation_rules` holds **only what a workspace changed.** The rules
themselves ship with the image, in `reconciliation_policy`. Copying them
into every workspace at creation would freeze them: a better default six
months from now would never reach anyone who had already opened the
page. So an untouched rule keeps improving with the product, and a row
here exists exactly when somebody decided otherwise.

`reconciliation_suggestions` holds the doubtful space: money that looks
like it answers a promise without the evidence to say so. Its most
important column is `status`, and specifically that `declined` is stored:
without it a suggestion somebody rejected comes back on the next sync,
and asking a person the same question every morning is worse than never
asking.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.transaction import Transaction

#: jsonb on Postgres, plain JSON elsewhere. The overrides are read on
#: every match, so on the deployment everyone actually runs they should
#: be the indexable, queryable type rather than a string of text.
_Json = JSON().with_variant(JSONB(), "postgresql")


class ReconciliationRule(Base):
    """One workspace's departure from a shipped rule, or a rule of its own.

    `config` is a **sparse patch** for an overridden default: it carries
    only the keys somebody touched, so turning a rule off does not also
    freeze its date window at today's value. For a rule the workspace
    wrote itself it is the whole strategy.
    """

    __tablename__ = "reconciliation_rules"
    __table_args__ = (
        # One row per rule per workspace. A second override of the same
        # strategy is not a second opinion, it is a bug.
        UniqueConstraint(
            "workspace_id", "node", "strategy_id", name="uq_reconciliation_rule_strategy"
        ),
        CheckConstraint(
            "origin IN ('default', 'custom')", name="ck_reconciliation_rule_origin"
        ),
        Index("ix_reconciliation_rules_workspace_node", "workspace_id", "node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Who wrote it. Kept for the same reason the categorization rules keep
    #: it: in a workspace with several members, "who changed this" is the
    #: first question asked when matching starts behaving differently.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    #: Which policy document this belongs to: invoices or recurring bills.
    node: Mapped[str] = mapped_column(String(64))
    #: The shipped strategy being overridden, or this rule's own id.
    strategy_id: Mapped[str] = mapped_column(String(64))
    #: `default` patches something we ship; `custom` is the workspace's own.
    origin: Mapped[str] = mapped_column(String(16), default="default")
    #: What the workspace calls it. Null for an overridden default, which
    #: keeps its translated shipped name: a name that would otherwise
    #: freeze in one language the day somebody edited a threshold.
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: Where it sits in the order strategies are tried. Null means "leave
    #: it where the shipped document put it", so inserting a rule above a
    #: default does not require rewriting every other row.
    position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict)
    #: The workspace threw this rule away.
    #:
    #: Only meaningful on an `origin='default'` row: a rule of the
    #: workspace's own is deleted by deleting the row, because it exists
    #: nowhere else. A shipped rule is a document in the image, so the
    #: only way to be rid of it is to say so here. `config` is kept, not
    #: cleared: a deleted rule does not run, and nobody is served by
    #: silently discarding thresholds somebody tuned in case they bring
    #: it back.
    deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: The shape this override was written against, so a future change to
    #: the document can tell which rows it has to migrate.
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReconciliationSuggestion(Base):
    """A match the engine is not confident enough to make on its own.

    The row is keyed on the pair, not on the transaction, because one
    payment can plausibly answer several promises and a person should see
    all of them rather than whichever we happened to score highest.

    `scores` keeps the per-signal breakdown rather than one number. "We
    are 78% sure" tells a person nothing they can act on; "the amount is
    exact, the date is four days out, the name does not match" tells them
    what to look at, and is the difference between a queue somebody
    trusts and one they clear without reading.
    """

    __tablename__ = "reconciliation_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "transaction_id",
            "expectation_kind",
            "expectation_id",
            name="uq_reconciliation_suggestion_pair",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'expired')",
            name="ck_reconciliation_suggestion_status",
        ),
        CheckConstraint(
            "expectation_kind IN ('invoice', 'recurring')",
            name="ck_reconciliation_suggestion_kind",
        ),
        # The queue is always read as "what is still open here", so that is
        # what the index serves.
        Index(
            "ix_reconciliation_suggestions_open",
            "workspace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    #: Which kind of promise this points at. There is no foreign key,
    #: deliberately: an invoice and a recurring bill live in different
    #: tables and a column cannot point at both. Deletion is handled by
    #: the reaper rather than by the database.
    expectation_kind: Mapped[str] = mapped_column(String(16))
    expectation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    #: Ties together the several invoices one payment may cover, so the
    #: queue asks *"does this cover these three?"* rather than three
    #: separate questions somebody could answer inconsistently and end up
    #: with a payment spread across two debts and short on a third. Null
    #: for the ordinary single-invoice case.
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    #: The rule that produced it, so the queue can say *why*; the same id
    #: the rules page shows.
    strategy_id: Mapped[str] = mapped_column(String(64))
    node: Mapped[str] = mapped_column(String(64))
    #: What would be settled if this were accepted.
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    #: Per-signal breakdown, e.g. {"amount": 1.0, "date": 0.6, "name": 0.0}.
    scores: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    #: When a person acted on it, and who.
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    transaction: Mapped["Transaction"] = relationship(lazy="joined")


class ReconciliationEvent(Base):
    """What matching did, in one order.

    Small on purpose. Most of what a person calls "the history" is
    already stored: a link is an allocation with a rule id and a
    timestamp, an answered suggestion is a suggestion row with a status
    and who resolved it. What did not exist was a single stream: reading
    those two tables together means joining shapes that have nothing in
    common and no shared ordering.

    And one event genuinely had nowhere to live. `unallocate` **deletes**
    the allocation, so a match that was made and then undone looked
    exactly like one that never happened. That is the row this table
    exists for first.

    It is **not** a log of everything the engine considered. A sync of
    three hundred transactions where two hundred and ninety match nothing
    would write two hundred and ninety rows saying nothing happened, and
    a history nobody can scan is the same as no history.
    """

    __tablename__ = "reconciliation_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('linked', 'suggested', 'accepted', 'declined',"
            " 'expired', 'unlinked')",
            name="ck_reconciliation_event_action",
        ),
        Index("ix_reconciliation_events_stream", "workspace_id", "at"),
        Index("ix_reconciliation_events_expectation", "expectation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    action: Mapped[str] = mapped_column(String(16))
    #: SET NULL rather than CASCADE: deleting a transaction should not
    #: erase the record that something was once matched to it. The
    #: history is the one place that has to survive the tidying up.
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    expectation_kind: Mapped[str] = mapped_column(String(16))
    expectation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    strategy_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: Null means the system did it on its own: the difference a reader
    #: most often wants: was this me, or was this the rules?
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    detail: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict)
