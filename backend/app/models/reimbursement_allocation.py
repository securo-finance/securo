import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class ReimbursementAllocation(Base):
    """A part of one transaction that offsets another (ADR 0031).

    The anchor is the original row (a dinner paid for three, a purchase later
    partly returned); the counterpart is a row in the other direction that
    pays part of it back. Both rows stay exactly as the bank reports them.
    User-level P&L counts the anchor net of its allocations, in the anchor's
    category and period, and leaves the allocated part of the counterpart out.

    `amount` is the allocated part of the counterpart, positive and in the
    counterpart's currency. A counterpart can be allocated to several anchors,
    and the allocations out of it never add up to more than its amount. The
    service layer (`reimbursement_service`) keeps those invariants.
    """

    __tablename__ = "reimbursement_allocations"
    __table_args__ = (
        UniqueConstraint("anchor_id", "counterpart_id", name="uq_reimbursement_allocation_pair"),
        CheckConstraint("amount > 0", name="ck_reimbursement_allocation_amount_positive"),
        CheckConstraint("anchor_id <> counterpart_id", name="ck_reimbursement_allocation_distinct"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    anchor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    counterpart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    anchor: Mapped["Transaction"] = relationship(foreign_keys=[anchor_id])
    counterpart: Mapped["Transaction"] = relationship(foreign_keys=[counterpart_id])


def _install_sqlite_cleanup(target, connection, **kw) -> None:
    """Remove a transaction's allocations when it is deleted in SQLite.

    PostgreSQL does this through ON DELETE CASCADE; see the same trigger on
    `transaction_category_lines` for why the tests need it.
    """
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS trg_delete_reimbursement_allocations
            AFTER DELETE ON transactions
            FOR EACH ROW BEGIN
                DELETE FROM reimbursement_allocations
                WHERE anchor_id = OLD.id OR counterpart_id = OLD.id;
            END
            """
        )


event.listen(ReimbursementAllocation.__table__, "after_create", _install_sqlite_cleanup)
