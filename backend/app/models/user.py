import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.category_group import CategoryGroup
    from app.models.bank_connection import BankConnection
    from app.models.passkey import UserPasskey


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    if TYPE_CHECKING:
        # fastapi-users declares its inherited columns as plain types (e.g.
        # `email: str`) under its own TYPE_CHECKING branch, so `ty` can't see
        # them as SQLAlchemy `Mapped` attributes (`.ilike()`, `.in_()`, ...).
        # Re-annotate them here so type checkers treat them as real mapped
        # columns everywhere.
        id: Mapped[uuid.UUID]
        email: Mapped[str]
        hashed_password: Mapped[str]
        is_active: Mapped[bool]
        is_superuser: Mapped[bool]
        is_verified: Mapped[bool]

    # A change of login address an administrator asked for, waiting for the
    # person whose account it is to accept it (ADR 0042, #556). The address is
    # a recovery credential, so this request alone never lets anyone in: the
    # account keeps its email, and the password recovery that goes with it,
    # until the owner confirms from their own session.
    pending_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, default=None)
    pending_email_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    preferences: Mapped[Optional[dict]] = mapped_column(
        JSON,
        default=lambda: {
            "language": "en",
            "date_format": "MM/DD/YYYY",
            "currency_display": "USD",
        },
    )

    # The appearance this person chose (a theme id from the library). None is
    # Securo's own look, with the instance's accent colors if an admin set them.
    theme: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, default=None)

    totp_secret: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    oidc_issuer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    oidc_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    categories: Mapped[list["Category"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    category_groups: Mapped[list["CategoryGroup"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    bank_connections: Mapped[list["BankConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    passkeys: Mapped[list["UserPasskey"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def display_name(self) -> Optional[str]:
        """The name this person chose, when they set one.

        Callers that show a person anywhere the email is the only other
        identifier fall back to the email themselves; anything offered by
        an API that names people should carry this.
        """
        prefs = self.preferences or {}
        # Preferences is a JSON column, so the value is only a string because
        # the writers put one there; anything else is not a name.
        name = prefs.get("display_name")
        if not isinstance(name, str):
            return None
        return name.strip() or None

    @property
    def primary_currency(self) -> str:
        """Return the user's configured primary currency."""
        from app.core.config import get_settings
        return (self.preferences or {}).get("currency_display", get_settings().default_currency)
