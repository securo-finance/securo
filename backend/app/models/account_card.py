import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account


class AccountCard(Base):
    """A physical or virtual card identified by a provider-supplied final four.

    A credit-card account can have several cards sharing the same statement and
    limit. The final four remains provider-owned; ``label`` is an optional
    user-owned alias such as "Pessoal" or "Cartão adicional".
    """

    __tablename__ = "account_cards"
    __table_args__ = (
        UniqueConstraint("account_id", "masked_number", name="uq_account_cards_account_masked_number"),
        Index("ix_account_cards_workspace_account", "workspace_id", "account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # Only a normalized final four is ever stored — never a full PAN.
    masked_number: Mapped[str] = mapped_column(String(4), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    account: Mapped["Account"] = relationship(back_populates="linked_cards")
