import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account


class AccountReservedBalance(Base):
    """Funds the institution holds reserved inside a connected account.

    Pluggy reports these in ``bankData.reservedBalances`` (Mercado Pago
    cofrinhos, "saldo reservado", caixinhas). Their relationship to the account
    balance is not guaranteed, so they are displayed separately and never
    added to totals (ADR 0022).

    Provider-owned: the sync upserts by ``(account_id, external_id, currency)``
    and deletes rows the provider stopped reporting. ``name`` is a label, never
    an identity.
    """

    __tablename__ = "account_reserved_balances"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "external_id",
            "currency",
            name="uq_account_reserved_balances_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    # The provider's identification for the reservation, stable across syncs
    # and unique per account (Pluggy `identification`).
    external_id: Mapped[str] = mapped_column(String(255))
    # User-facing label the provider gives the reservation; never an identity.
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=15, scale=2), default=Decimal("0.00")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    account: Mapped["Account"] = relationship(back_populates="reserved_balances")
