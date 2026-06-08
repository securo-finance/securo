import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.account import Account


class BankConnection(Base):
    __tablename__ = "bank_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50))  # "pluggy", "belvo", etc.
    external_id: Mapped[str] = mapped_column(String(255))  # Provider's item ID
    institution_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Fully-formed institution logo URL captured from the provider (Pluggy
    # connector.imageUrl, Enable Banking ASPSP logo). Null = no logo; the
    # frontend falls back to the account-type icon. Mirrors assets.logo_url.
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    credentials: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # Encrypted tokens
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="active")  # active, error, expired
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship(back_populates="bank_connections")
    accounts: Mapped[list["Account"]] = relationship(back_populates="connection", cascade="all, delete-orphan")
