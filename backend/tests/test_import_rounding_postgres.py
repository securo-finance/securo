"""A file amount with three decimals, against the column's scale (#616).

`Numeric(15, 2)` rounds 10.005 to 10.01 on write. The duplicate check used to
run in the database, which rounded the bound amount to the column's scale as
well, so re-importing the same statement found the row it had written. Read
once for the file and compared in Python, both sides have to be rounded the
same way or the row lands twice.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions

START = date(2026, 4, 1)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def _account(session, user_id, workspace_id) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name="Statement",
        type="checking", balance=Decimal("0.00"), currency="BRL",
    )
    session.add(account)
    await session.commit()
    return account


async def _import(session, workspace_id, user_id, account_id, rows):
    return await import_transactions(
        session, workspace_id, user_id, account_id, rows, "import",
        filename="statement.csv", detected_format="csv",
    )


@pytest.mark.asyncio
async def test_a_reimport_finds_the_row_the_column_rounded(
    postgres_sessions, test_user, test_workspace,
):
    async with postgres_sessions() as session:
        account = await _account(session, test_user.id, test_workspace.id)
        file_row = TransactionImport(
            description="ROUND TRIP", amount=Decimal("10.005"), date=START, type="debit",
        )
        imported, skipped, _, _ = await _import(
            session, test_workspace.id, test_user.id, account.id, [file_row]
        )
        assert (imported, skipped) == (1, 0)
    account_id = account.id

    # The second import is another request: it reads the row back from the column.
    async with postgres_sessions() as session:
        stored = (await session.execute(
            select(Transaction.amount).where(Transaction.account_id == account_id)
        )).scalars().all()
        assert stored == [Decimal("10.01")]

        imported, skipped, _, _ = await _import(
            session, test_workspace.id, test_user.id, account_id,
            [file_row.model_copy()],
        )
        assert (imported, skipped) == (0, 1)
        amounts = (await session.execute(
            select(Transaction.amount).where(Transaction.account_id == account_id)
        )).scalars().all()
        assert amounts == [Decimal("10.01")]


@pytest.mark.asyncio
async def test_a_synced_row_is_found_at_the_amount_the_column_rounded(
    postgres_sessions, test_user, test_workspace,
):
    async with postgres_sessions() as session:
        account = await _account(session, test_user.id, test_workspace.id)
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, description="GYM", original_description="GYM",
            amount=Decimal("29.994"), date=START + timedelta(days=2), type="debit",
            status="posted", source="sync", currency="BRL",
        ))
        await session.commit()
        stored = (await session.execute(
            select(Transaction.amount).where(Transaction.account_id == account.id)
        )).scalars().all()
        assert stored == [Decimal("29.99")]

        imported, skipped, _, _ = await _import(
            session, test_workspace.id, test_user.id, account.id,
            [TransactionImport(
                description="GYM", amount=Decimal("29.994"), date=START, type="debit",
            )],
        )
        assert (imported, skipped) == (0, 1)
