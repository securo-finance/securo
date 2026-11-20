"""Trades of exchange-traded Pluggy positions, read from
/investments/{id}/transactions so the ledger can derive average price and
return (#811). The /investments payload itself carries no cost for equities.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers.pluggy import PluggyProvider, _build_trade_data, _sum_expenses

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _tx(**overrides) -> dict:
    tx = {
        "id": "tx-1",
        "type": "BUY",
        "movementType": "DEBIT",
        "date": "2026-10-22T03:00:00.000Z",
        "tradeDate": "2026-10-22T03:00:00.000Z",
        "quantity": 2,
        "value": 115.58,
        "amount": 231.16,
        "expenses": {"settlementFee": 0.06, "stockExchangeFee": 0.16, "brokerageFee": 0, "iof": None},
    }
    tx.update(overrides)
    return tx


def test_buy_maps_price_quantity_date_and_fees():
    trade = _build_trade_data(_tx())
    assert trade is not None
    assert (trade.external_id, trade.kind, trade.date) == ("tx-1", "buy", date(2026, 10, 22))
    assert trade.quantity == Decimal("2")
    assert trade.price == Decimal("115.58")
    assert trade.fee == Decimal("0.22")


def test_sell_maps_to_sell():
    trade = _build_trade_data(_tx(type="SELL", movementType="CREDIT"))
    assert trade is not None and trade.kind == "sell"


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "INTEREST", "quantity": None, "value": None, "expenses": None},
        {"type": "TAX"},
        {"type": "TRANSFER"},
        {"type": None},
        {"id": None},
        {"quantity": None},
        {"quantity": 0},
        {"quantity": -2},
        {"value": None, "amount": None},
        {"value": -1},
        {"tradeDate": None, "date": None},
        {"tradeDate": "not a date", "date": None},
    ],
)
def test_rows_that_are_not_trades_are_dropped(overrides):
    assert _build_trade_data(_tx(**overrides)) is None


def test_price_falls_back_to_amount_over_quantity():
    trade = _build_trade_data(_tx(value=None, amount=231.16))
    assert trade is not None and trade.price == Decimal("115.58")


def test_trade_date_is_preferred_over_settlement_date():
    trade = _build_trade_data(_tx(tradeDate="2026-10-22T03:00:00.000Z", date="2026-10-24T03:00:00.000Z"))
    assert trade is not None and trade.date == date(2026, 10, 22)


def test_expenses_sum_ignores_null_negative_and_non_numeric_entries():
    assert _sum_expenses({"a": 0.1, "b": None, "c": -5, "d": "x", "e": True, "f": "0.2"}) == Decimal("0.3")
    assert _sum_expenses(None) == Decimal("0")
    assert _sum_expenses([]) == Decimal("0")


def _routes(investments: list[dict], transactions: dict[str, object]):
    """MockTransport handler: /investments pages and per-investment trades.

    A transactions value that is an int is answered as that HTTP status.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/investments":
            return httpx.Response(200, json={"results": investments, "totalPages": 1})
        investment_id = request.url.path.split("/")[2]
        answer = transactions.get(investment_id, [])
        if isinstance(answer, int):
            return httpx.Response(answer, json={"message": "nope"})
        return httpx.Response(200, json={"results": answer, "totalPages": 1})

    return handler, calls


async def _holdings(investments: list[dict], transactions: dict[str, object]):
    handler, calls = _routes(investments, transactions)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs["base_url"] = "https://api.pluggy.ai"
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    with patch.object(PluggyProvider, "_headers", new=AsyncMock(return_value={"X-API-KEY": "k"})), \
         patch("app.providers.pluggy.PLUGGY_API_BASE", ""), \
         patch("app.providers.pluggy.httpx.AsyncClient", side_effect=client_factory):
        holdings = await PluggyProvider().get_holdings({"item_id": "item"})
    return holdings, calls


@pytest.mark.asyncio
async def test_exchange_traded_holdings_carry_their_trades():
    investments = [
        {"id": "eq", "type": "EQUITY", "code": "ITUB4", "balance": 280.49, "quantity": 7},
        {"id": "cdb", "type": "FIXED_INCOME", "code": "0001-02", "balance": 1000, "quantity": 1},
    ]
    holdings, calls = await _holdings(
        investments,
        {"eq": [_tx(id="b1", quantity=5, value=39.85), _tx(id="b2", quantity=2, value=48.59),
                {"id": "i1", "type": "INTEREST", "date": "2026-11-01", "quantity": None, "value": None}]},
    )

    by_id = {h.external_id: h for h in holdings}
    assert [t.external_id for t in by_id["eq"].trades] == ["b1", "b2"]
    assert sum(t.quantity for t in by_id["eq"].trades) == Decimal("7")
    # Fixed income is not asked: its quantity is not a share count.
    assert by_id["cdb"].trades is None
    assert "/investments/cdb/transactions" not in calls


@pytest.mark.asyncio
async def test_a_failing_transactions_endpoint_leaves_trades_unknown():
    holdings, _ = await _holdings(
        [{"id": "eq", "type": "ETF", "code": "WRLD11", "balance": 797.6, "quantity": 5}],
        {"eq": 500},
    )
    # None, not []: the sync then keeps the ledger it has.
    assert holdings[0].trades is None


@pytest.mark.asyncio
async def test_a_position_without_trades_reports_an_empty_list():
    holdings, _ = await _holdings(
        [{"id": "eq", "type": "EQUITY", "code": "PSSA3", "balance": 312.62, "quantity": 7}],
        {"eq": []},
    )
    assert holdings[0].trades == []
