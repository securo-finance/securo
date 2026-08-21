import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
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
    # Doubles as the connection_id lookup index, and keeps two syncs racing
    # on the same payload from double-inserting an institution.
    __table_args__ = (
        UniqueConstraint("connection_id", "name", name="uq_institutions_connection_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_connections.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    connection: Mapped["BankConnection"] = relationship(back_populates="institutions")
    accounts: Mapped[list["Account"]] = relationship(back_populates="institution")
