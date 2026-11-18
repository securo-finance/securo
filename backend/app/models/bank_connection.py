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
    from app.models.institution import Institution


class BankConnection(Base):
    __tablename__ = "bank_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(128))  # "pluggy", "belvo", "plugin:<plugin id>", etc.
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
    # Institutions reached through this link (issue #345). Eager (selectin) so
    # the connections API can summarize them without async lazy loads.
    institutions: Mapped[list["Institution"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def last_sync_failure(self) -> Optional[dict]:
        """Why the last sync failed, until a sync succeeds (issue #1405).

        Kept in `settings` by connection_service; read here so the API can
        return it as a field of its own."""
        value = (self.settings or {}).get(LAST_SYNC_FAILURE_KEY)
        if not isinstance(value, dict) or value.get("code") not in SYNC_FAILURE_CODES:
            return None
        try:
            datetime.fromisoformat(value["at"])
        except (KeyError, TypeError, ValueError):
            return None
        detail = value.get("detail")
        if detail is not None and not isinstance(detail, str):
            # A malformed detail must not take the whole connections list down.
            return {**value, "detail": None}
        return value


# Key in BankConnection.settings holding the last sync failure, and its codes.
LAST_SYNC_FAILURE_KEY = "last_sync_failure"
SYNC_FAILURE_CODES = (
    "psu_present_required",
    "consent_expired",
    # A bank linked through an aggregator the user manages (the SimpleFIN
    # Bridge) needs reauthorizing there; the connection itself stays valid.
    "reauthorize_at_provider",
    "rate_limited",
    "provider_error",
)
