from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset_transaction import AssetTransaction
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.services.connection_service import _sync_trading212_orders


class _OrdersProvider:
    async def get_historical_orders(self, credentials: dict):
        return [
            {
                "order": {
                    "ticker": "AAPL_US_EQ",
                    "side": "BUY",
                    "currency": "USD",
                    "instrument": {"name": "Apple Inc.", "isin": "US0378331005"},
                },
                "fill": {
                    "id": "fill-1",
                    "type": "TRADE",
                    "quantity": "2",
                    "price": "100",
                    "filledAt": "2026-08-01T10:00:00Z",
                    "walletImpact": {"netValue": "-201", "currency": "USD", "taxes": [{"quantity": "1"}]},
                },
            }
        ]


@pytest.mark.asyncio
async def test_order_sync_upserts_asset_ledger_and_an_ignored_cash_settlement(
    session: AsyncSession, test_user: User
):
    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="trading212",
        kind="brokerage",
        external_id="123",
        institution_name="Trading 212",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    cash = Account(
        user_id=test_user.id,
        connection_id=connection.id,
        external_id="trading212:123:cash",
        name="Trading 212 Cash",
        type="investment",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(cash)
    await session.commit()

    await _sync_trading212_orders(session, test_user.id, connection, _OrdersProvider(), {})
    await session.commit()

    ledger = (await session.execute(select(AssetTransaction))).scalars().all()
    settlements = (await session.execute(select(Transaction))).scalars().all()
    assert [(row.external_id, row.kind, row.quantity, row.price, row.fee) for row in ledger] == [
        ("t212:fill:fill-1", "buy", Decimal("2"), Decimal("100"), Decimal("1"))
    ]
    assert [(row.external_id, row.is_ignored, row.type, row.amount) for row in settlements] == [
        ("t212:settlement:fill-1", True, "debit", Decimal("201"))
    ]
