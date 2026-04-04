from typing import TYPE_CHECKING, Optional

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.bank_connection import BankConnection


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    preferences: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=lambda: {
            "language": "en",
            "date_format": "MM/DD/YYYY",
            "timezone": "UTC",
            "currency_display": "USD",
        },
    )

    categories: Mapped[list["Category"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    bank_connections: Mapped[list["BankConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def primary_currency(self) -> str:
        """Return the user's configured primary currency."""
        from app.core.config import get_settings
        return (self.preferences or {}).get("currency_display", get_settings().default_currency)
