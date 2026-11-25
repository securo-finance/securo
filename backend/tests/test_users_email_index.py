"""The database refuses a second account for one address (#568).

The services compare addresses without regard to case, but two requests can
pass those checks together and each write its row. A unique index on
``lower(email)`` is what makes the second one fail at commit, instead of
leaving a pair of accounts that no lookup can answer for: sign-in and recovery
raised, and both accounts were unreachable.
"""
import uuid

import bcrypt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

pytestmark = pytest.mark.asyncio


def _account(email: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(b"somepass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )


async def test_a_second_account_for_one_address_is_refused(session: AsyncSession):
    session.add(_account("Ana@example.com"))
    await session.commit()

    session.add(_account("ana@example.com"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_the_address_keeps_its_own_case(session: AsyncSession):
    """The index compares lowered, it does not rewrite what was stored."""
    session.add(_account("Bia@example.com"))
    await session.commit()

    stored = await session.execute(
        text("SELECT email FROM users WHERE lower(email) = 'bia@example.com'")
    )
    assert stored.scalar_one() == "Bia@example.com"
