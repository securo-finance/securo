import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class ExpectedIncome(Base):
    """One manually entered income figure for one month of a workspace.

    The row keeps the currency the number was typed in; converting to the
    reader's display currency happens on read, never here (ADR 0008).
    """

    __tablename__ = "expected_incomes"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "month", name="uq_expected_income_per_workspace_month"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    month: Mapped[date] = mapped_column(Date)  # First day of month
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
