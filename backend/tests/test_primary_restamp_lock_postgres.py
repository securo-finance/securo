"""The restamp holds the owner's row until its caller commits (PostgreSQL)."""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models.user import User
from app.services.primary_restamp_service import restamp_user_primary_amounts

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


async def test_a_currency_switch_waits_for_the_restamp_to_commit(
    session, postgres_sessions, test_user
):
    user_id = test_user.id
    await session.commit()

    await restamp_user_primary_amounts(session, user_id)  # not committed yet

    async with postgres_sessions() as other:
        with pytest.raises(DBAPIError, match="could not obtain lock"):
            await other.execute(
                select(User).where(User.id == user_id).with_for_update(nowait=True)
            )
        await other.rollback()

    await session.commit()
    async with postgres_sessions() as other:
        await other.execute(
            select(User).where(User.id == user_id).with_for_update(nowait=True)
        )
        await other.rollback()
