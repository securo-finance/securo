import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
    from app.models.recurring_transaction import RecurringTransaction


class RecurringTransactionCategoryLine(Base):
    """One part of a recurring transaction split across several categories.

    The template for `TransactionCategoryLine`: every occurrence the schedule
    generates starts with these parts, sized to its own amount. Same rules as
    a transaction's lines (two or more, adding up to the template amount,
    `category_id` null on the template while split), kept by
    `category_line_service`. A table of its own rather than one table with
    two optional owners, so each owner keeps a plain foreign key.
    """

    __tablename__ = "recurring_transaction_category_lines"
    __table_args__ = (
        UniqueConstraint(
            "recurring_transaction_id", "position",
            name="uq_recurring_category_line_position",
        ),
        CheckConstraint("amount > 0", name="ck_recurring_category_line_amount_positive"),
        CheckConstraint(
            "share_type IN ('exact', 'percent')", name="ck_recurring_category_line_share_type"
        ),
        CheckConstraint(
            "(share_type = 'percent') = (share_pct IS NOT NULL)",
            name="ck_recurring_category_line_share_pct",
        ),
        # Named explicitly: the default names pass PostgreSQL's 63-character
        # identifier limit.
        Index("ix_recurring_category_lines_workspace_id", "workspace_id"),
        Index("ix_recurring_category_lines_recurring_id", "recurring_transaction_id"),
        Index("ix_recurring_category_lines_category_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    recurring_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_transactions.id", ondelete="CASCADE")
    )
    # NO ACTION, as on transaction lines: see TransactionCategoryLine.
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    share_type: Mapped[str] = mapped_column(String(10), default="exact", server_default="exact")
    share_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=5, scale=2), nullable=True)
    position: Mapped[int] = mapped_column(SmallInteger)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    recurring_transaction: Mapped["RecurringTransaction"] = relationship(
        back_populates="category_lines"
    )
    category: Mapped["Category"] = relationship(lazy="selectin")


def _install_sqlite_cleanup(target, connection, **kw) -> None:
    """SQLite test databases do not enforce ON DELETE CASCADE."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS trg_delete_recurring_category_lines
            AFTER DELETE ON recurring_transactions
            FOR EACH ROW BEGIN
                DELETE FROM recurring_transaction_category_lines
                WHERE recurring_transaction_id = OLD.id;
            END
            """
        )


event.listen(RecurringTransactionCategoryLine.__table__, "after_create", _install_sqlite_cleanup)
