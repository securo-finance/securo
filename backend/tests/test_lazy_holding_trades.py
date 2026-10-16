"""Provider trades are only fetched where they can change the ledger.

Fetching trades costs one provider request per holding, so a bank sync skips
them until the workspace has opened Performance, and afterwards only fetches
holdings whose stored trades no longer add up to the share count the provider
reports.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.user import User
from app.providers import register_provider
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    HoldingData,
    HoldingTradeData,
    TransactionData,
)
from app.services.connection_service import _sync_holdings, enable_holding_trades


def _adds_up(holding: HoldingData) -> list[HoldingTradeData]:
    """Trades that add up to the holding's position: one buy, and a sell once closed."""
    quantity = holding.quantity or Decimal("1")
    trades = [
        HoldingTradeData(
            external_id=f"buy-{holding.external_id}-{quantity}",
            kind="buy",
            date=date(2026, 1, 2),
            quantity=quantity,
            price=Decimal("10"),
        )
    ]
    if holding.is_withdrawn:
        trades.append(
            HoldingTradeData(
                external_id=f"sell-{holding.external_id}",
                kind="sell",
                date=date(2026, 2, 2),
                quantity=quantity,
                price=Decimal("11"),
            )
        )
    return trades


def _partial(holding: HoldingData) -> list[HoldingTradeData]:
    """Trades that miss most of the position: a single one-share buy."""
    return [
        HoldingTradeData(
            external_id=f"partial-{holding.external_id}",
            kind="buy",
            date=date(2026, 1, 2),
            quantity=Decimal("1"),
            price=Decimal("10"),
        )
    ]


class _TradesProvider(BankProvider):
    holdings: list[HoldingData] = []
    requests: list[list[str]] = []
    short: set[str] = set()  # holdings whose trades leave out a purchase
    failing: set[str] = set()  # holdings whose request fails

    @property
    def name(self) -> str:
        return "trades-mock"

    async def get_oauth_url(self, redirect_uri, state, flow_params=None):  # pragma: no cover
        return "http://mock"

    async def handle_oauth_callback(self, code) -> ConnectionData:  # pragma: no cover
        raise NotImplementedError

    async def get_accounts(self, credentials) -> list[AccountData]:  # pragma: no cover
        return []

    async def get_transactions(
        self, credentials, account_external_id, since=None, payee_source="auto"
    ) -> list[TransactionData]:  # pragma: no cover
        return []

    async def refresh_credentials(self, credentials):  # pragma: no cover
        return credentials

    async def get_holdings(self, credentials) -> list[HoldingData]:
        return list(_TradesProvider.holdings)

    async def get_holding_trades(self, credentials, holdings):
        _TradesProvider.requests.append(sorted(h.external_id for h in holdings))
        return {
            holding.external_id: (
                _partial(holding)
                if holding.external_id in _TradesProvider.short
                else _adds_up(holding)
            )
            for holding in holdings
            if holding.external_id not in _TradesProvider.failing
        }


@pytest.fixture(autouse=True)
def _register_provider():
    register_provider("trades-mock", _TradesProvider)
    _TradesProvider.holdings = []
    _TradesProvider.requests = []
    _TradesProvider.short = set()
    _TradesProvider.failing = set()


@pytest_asyncio.fixture
async def connection(session: AsyncSession, test_user: User, test_workspace) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="trades-mock",
        external_id="item-trades",
        institution_name="Mock Broker",
        credentials={"item_id": "item-trades"},
        settings={"import_pending": False},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    return conn


def _fund(
    external_id: str,
    quantity: Optional[str],
    value: str = "100",
    *,
    withdrawn: bool = False,
) -> HoldingData:
    return HoldingData(
        external_id=external_id,
        name=f"Fund {external_id}",
        currency="BRL",
        current_value=Decimal(value),
        quantity=Decimal(quantity) if quantity is not None else None,
        is_withdrawn=withdrawn,
        metadata={"status": "TOTAL_WITHDRAWAL" if withdrawn else "ACTIVE"},
    )


async def _sync(session: AsyncSession, user: User, connection: BankConnection, *holdings):
    _TradesProvider.holdings = list(holdings)
    await _sync_holdings(session, user.id, connection, {"item_id": "item-trades"})
    await session.commit()


@pytest.mark.asyncio
async def test_no_trade_requests_until_performance_is_opened(
    session: AsyncSession, test_user: User, connection: BankConnection
):
    await _sync(session, test_user, connection, _fund("a", "10"), _fund("b", "5"))
    await _sync(session, test_user, connection, _fund("a", "12"), _fund("b", "5"))

    assert _TradesProvider.requests == []


@pytest.mark.asyncio
async def test_trades_are_fetched_only_when_a_share_count_moves(
    session: AsyncSession, test_user: User, test_workspace, connection: BankConnection
):
    await enable_holding_trades(session, test_workspace.id)

    # First sync: nothing is stored yet, so every holding is fetched.
    await _sync(session, test_user, connection, _fund("a", "10"), _fund("b", "5"))
    assert _TradesProvider.requests == [["a", "b"]]

    # Only prices moved: the stored trades still add up, no requests.
    await _sync(session, test_user, connection, _fund("a", "10", "104"), _fund("b", "5", "52"))
    assert _TradesProvider.requests == [["a", "b"]]

    # More shares of "a" were bought, so only "a" is fetched.
    await _sync(session, test_user, connection, _fund("a", "12", "130"), _fund("b", "5", "53"))
    assert _TradesProvider.requests == [["a", "b"], ["a"]]


@pytest.mark.asyncio
async def test_a_closed_holding_is_fetched_once(
    session: AsyncSession, test_user: User, test_workspace, connection: BankConnection
):
    await enable_holding_trades(session, test_workspace.id)
    await _sync(session, test_user, connection, _fund("a", "10"))
    closed = _fund("a", "10", "0", withdrawn=True)

    # The closure is fetched to record the redemption, and never again.
    await _sync(session, test_user, connection, closed)
    await _sync(session, test_user, connection, closed)
    assert _TradesProvider.requests == [["a"], ["a"]]


@pytest.mark.asyncio
async def test_trades_that_do_not_add_up_wait_for_the_share_count_to_change(
    session: AsyncSession, test_user: User, test_workspace, connection: BankConnection
):
    await enable_holding_trades(session, test_workspace.id)
    _TradesProvider.short = {"a"}

    await _sync(session, test_user, connection, _fund("a", "10"))
    await _sync(session, test_user, connection, _fund("a", "10", "105"))
    assert _TradesProvider.requests == [["a"]]
    assert (connection.settings or {})["holding_trades"] == {"a": "10"}

    await _sync(session, test_user, connection, _fund("a", "11", "116"))
    assert _TradesProvider.requests == [["a"], ["a"]]

    # Once they add up, the holding is no longer remembered.
    _TradesProvider.short = set()
    await _sync(session, test_user, connection, _fund("a", "12", "127"))
    assert (connection.settings or {})["holding_trades"] == {}


@pytest.mark.asyncio
async def test_a_failed_request_is_retried_on_the_next_sync(
    session: AsyncSession, test_user: User, test_workspace, connection: BankConnection
):
    await enable_holding_trades(session, test_workspace.id)
    _TradesProvider.failing = {"b"}

    await _sync(session, test_user, connection, _fund("a", "10"), _fund("b", "5"))
    _TradesProvider.failing = set()
    await _sync(session, test_user, connection, _fund("a", "10"), _fund("b", "5"))
    await _sync(session, test_user, connection, _fund("a", "10"), _fund("b", "5"))

    assert _TradesProvider.requests == [["a", "b"], ["b"]]


@pytest.mark.asyncio
async def test_holdings_without_a_share_count_are_never_fetched(
    session: AsyncSession, test_user: User, test_workspace, connection: BankConnection
):
    """Their trades could never be checked against a position."""
    await enable_holding_trades(session, test_workspace.id)

    await _sync(session, test_user, connection, _fund("cdb", None, "1000"))
    await _sync(session, test_user, connection, _fund("cdb", None, "1500"))

    assert _TradesProvider.requests == []


@pytest.mark.asyncio
async def test_enabling_keeps_other_settings_and_what_was_learned(
    session: AsyncSession, test_workspace, connection: BankConnection
):
    await enable_holding_trades(session, test_workspace.id)
    assert connection.settings == {"import_pending": False, "holding_trades": {}}

    connection.settings = {**(connection.settings or {}), "holding_trades": {"a": "10"}}
    await session.commit()
    await enable_holding_trades(session, test_workspace.id)
    assert connection.settings == {"import_pending": False, "holding_trades": {"a": "10"}}


@pytest.mark.asyncio
async def test_opening_performance_enables_holding_trades(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, connection: BankConnection
):
    assert "holding_trades" not in (connection.settings or {})
    response = await client.get(
        "/api/assets/performance", params={"period": "3m"}, headers=auth_headers
    )
    assert response.status_code == 200
    await session.refresh(connection)
    assert (connection.settings or {})["holding_trades"] == {}
