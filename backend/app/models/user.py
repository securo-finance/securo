from typing import TYPE_CHECKING, Optional

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.category_group import CategoryGroup
    from app.models.bank_connection import BankConnection


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    preferences: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=lambda: {
            "language": "pt-BR",
            "date_format": "DD/MM/YYYY",
            "timezone": "America/Sao_Paulo",
            "currency_display": "BRL",
        },
    )

    categories: Mapped[list["Category"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    category_groups: Mapped[list["CategoryGroup"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    bank_connections: Mapped[list["BankConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
   # savings: Mapped[list["Savings"]] = relationship(back_populates="user", cascade="all, delete-orphan")     
