import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.transaction import Transaction


class TransactionCategoryLine(Base):
    """One part of a transaction split across several categories.

    The transaction keeps its single row and full amount, exactly as the bank
    reports it; the lines only say how that amount is distributed. A split
    transaction has two or more lines, their amounts add up to the
    transaction amount exactly, and its own `category_id` stays null while
    `has_category_lines` is true. The service layer keeps those invariants.

    Not to be confused with `TransactionSplit`, which shares a transaction
    among the members of a group.
    """

    __tablename__ = "transaction_category_lines"
    __table_args__ = (
        UniqueConstraint("transaction_id", "position", name="uq_transaction_category_line_position"),
        CheckConstraint("amount > 0", name="ck_transaction_category_line_amount_positive"),
        CheckConstraint(
            "share_type IN ('exact', 'percent')", name="ck_transaction_category_line_share_type"
        ),
        CheckConstraint(
            "(share_type = 'percent') = (share_pct IS NOT NULL)",
            name="ck_transaction_category_line_share_pct",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    # Default NO ACTION rather than RESTRICT: both block deleting a category a
    # line still uses, but RESTRICT is checked immediately and can fail while a
    # workspace delete cascades through categories and lines in one statement.
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), index=True
    )
    # Always in the transaction's currency and positive: a line takes the
    # direction (debit or credit) of its transaction.
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    share_type: Mapped[str] = mapped_column(String(10), default="exact", server_default="exact")
    # Kept only for share_type='percent' so an edit shows the percentage the
    # user typed, not one re-derived from rounded amounts.
    share_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=5, scale=2), nullable=True)
    position: Mapped[int] = mapped_column(SmallInteger)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="category_lines")
    # Loaded with the line: loops over loaded transactions read a part's
    # category flags, and a lazy load raises under asyncio.
    category: Mapped["Category"] = relationship(lazy="selectin")


def _install_sqlite_cleanup(target, connection, **kw) -> None:
    """Remove a transaction's lines when the transaction is deleted in SQLite.

    PostgreSQL does this through ON DELETE CASCADE. SQLite test databases do
    not enforce foreign keys, and several paths delete transactions with raw
    SQL (bulk delete, undoing an import, deleting a user), so without this the
    tests would keep orphan lines that production never has.
    """
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS trg_delete_transaction_category_lines
            AFTER DELETE ON transactions
            FOR EACH ROW BEGIN
                DELETE FROM transaction_category_lines WHERE transaction_id = OLD.id;
            END
            """
        )


event.listen(TransactionCategoryLine.__table__, "after_create", _install_sqlite_cleanup)
