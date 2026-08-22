import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.bank_connection import BankConnection


class Institution(Base):
    """One financial institution reached through a bank connection.

    Most providers are one institution per connection, but a single SimpleFIN
    Setup Token can span several (bank + brokerages — issue #345), so org
    identity lives here rather than on the connection.
    """

    __tablename__ = "institutions"
    # Identity is the org id when the provider sends one, the name otherwise.
    # Two partial unique indexes so two same-named orgs (two logins at one
    # bank) stay distinct rows, while both paths keep two racing syncs from
    # double-inserting. Partial indexes can't serve a bare connection_id
    # lookup, so the eager-loaded relationship indexes the FK itself.
    __table_args__ = (
        Index(
            "uq_institutions_connection_external_id",
            "connection_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_institutions_connection_name",
            "connection_id",
            "name",
            unique=True,
            postgresql_where=text("external_id IS NULL"),
            sqlite_where=text("external_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_connections.id", ondelete="CASCADE"), index=True
    )
    # The provider's stable id for the org (SimpleFIN conn_id). Renames update
    # the matched row in place instead of minting a new one (review on #654).
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    connection: Mapped["BankConnection"] = relationship(back_populates="institutions")
    accounts: Mapped[list["Account"]] = relationship(back_populates="institution")
