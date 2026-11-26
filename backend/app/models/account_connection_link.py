import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.bank_connection import BankConnection


class AccountConnectionLink(Base):
    """Links an Account to one or more BankConnection feeds (issue #1341).

    Each connection that sees the same underlying bank account holds a link
    row. Only one active feed is `is_primary_feed=True` (its `connection_id`
    and `external_id` mirror `Account.connection_id`/`external_id` and import
    transactions); secondary active feeds sync balance/failover metadata
    without duplicating transactions. Lower-confidence matches start as
    `status="pending"` until the user confirms (`"active"`) or rejects
    (`"rejected"`).
    """

    __tablename__ = "account_connection_links"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "connection_id",
            name="uq_account_connection_links_account_conn",
        ),
        Index(
            "uq_account_connection_links_conn_ext_active",
            "connection_id",
            "external_id",
            unique=True,
            postgresql_where=text("status != 'rejected'"),
            sqlite_where=text("status != 'rejected'"),
        ),
        Index(
            "uq_account_connection_links_one_active_primary",
            "account_id",
            unique=True,
            postgresql_where=text("is_primary_feed IS TRUE AND status = 'active'"),
            sqlite_where=text("is_primary_feed IS TRUE AND status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stable_external_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    account_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    is_primary_feed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default="active", nullable=False
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    account: Mapped["Account"] = relationship(back_populates="connection_links")
    connection: Mapped["BankConnection"] = relationship(
        back_populates="account_links"
    )
