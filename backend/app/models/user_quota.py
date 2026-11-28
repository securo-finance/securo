import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserQuota(Base):
    """The numbers an account may hold on a managed instance (ADR 0049, 0053).

    Neutral on purpose: the operator writes the numbers and the code attaches
    nothing else to them. A null counter is unbounded, and a user with no row
    follows the operator's defaults, or is unbounded without them.
    """

    __tablename__ = "user_quotas"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    max_personal_workspaces: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_business_workspaces: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_bank_connections: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_members_per_workspace: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_assistant_messages_per_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # From this moment an account still above a number feels it (ADR 0055):
    # sync pauses while it holds more bank connections than its number. Null
    # means an excess has no effect, as ADR 0049 first had it.
    excess_effective_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
