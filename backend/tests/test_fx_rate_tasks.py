from datetime import date
from decimal import Decimal
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.tasks import fx_rate_tasks


@pytest.mark.asyncio
async def test_restamp_fallback_fx_stamps_legacy_transaction_currency(
    session, test_user, test_workspace, monkeypatch
):
    """Legacy rows with a valid primary amount still need their missing currency stamp."""
    test_user.preferences = {
        **(test_user.preferences or {}),
        "currency_display": "BRL",
    }
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Legacy FX account",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.flush()

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Legacy transaction",
        amount=Decimal("10.00"),
        currency="USD",
        date=date.today(),
        type="debit",
        source="manual",
        amount_primary=Decimal("50.00"),
        amount_primary_currency=None,
        fx_rate_used=Decimal("5.0"),
    )
    fx_rate = FxRate(
        id=uuid.uuid4(),
        base_currency="USD",
        quote_currency="BRL",
        date=date.today(),
        rate=Decimal("5.0"),
        source="test",
    )
    session.add_all([tx, fx_rate])
    await session.commit()

    class _NoopEngine:
        async def dispose(self):
            return None

    task_session_maker = async_sessionmaker(
        bind=session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    monkeypatch.setattr(
        fx_rate_tasks,
        "_make_session_maker",
        lambda: (_NoopEngine(), task_session_maker),
    )

    restamped = await fx_rate_tasks._restamp_fallback_transactions()

    assert restamped == 1
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("50.00")
    assert tx.amount_primary_currency == "BRL"
    assert tx.fx_rate_used == Decimal("5.0")

    await session.delete(tx)
    await session.delete(fx_rate)
    await session.delete(account)
    await session.commit()
