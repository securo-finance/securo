"""Ranking of the ticker autocomplete behind /api/assets/market/search (#621).

Yahoo answers a coin name with one row per currency pair and with unrelated
assets whose names resemble the query, in no useful order. These tests pin the
ordering the Add-Asset form relies on: exact matches first, a coin's pairs
ahead of the rest when the query names a coin, and inside a coin the pair in
the user's own currency first. Nothing is filtered out.
"""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.schemas.asset import MarketSymbolMatch
from app.services.asset_service import rank_symbol_matches


def _crypto(symbol: str, name: str) -> MarketSymbolMatch:
    return MarketSymbolMatch(
        symbol=symbol, name=name, exchange="CCC", quote_type="CRYPTOCURRENCY"
    )


def _equity(symbol: str, name: str) -> MarketSymbolMatch:
    return MarketSymbolMatch(symbol=symbol, name=name, exchange="NMS", quote_type="EQUITY")


def _symbols(matches: list[MarketSymbolMatch]) -> list[str]:
    return [match.symbol for match in matches]


# ---------------------------------------------------------------------------
# rank_symbol_matches
# ---------------------------------------------------------------------------
def test_exact_name_beats_a_similar_one():
    matches = [
        _equity("CRON", "Cronos Group Inc."),
        _crypto("CRO-USD", "Cronos"),
        _equity("CRONX", "Cronos Similar Holdings"),
    ]

    assert _symbols(rank_symbol_matches(matches, "cronos", "USD"))[0] == "CRO-USD"


def test_a_pair_is_matched_by_its_base_symbol():
    # "BTC-USD" is how Yahoo lists "BTC", so the pair ties with the ticker that
    # carries the name literally and the crypto row wins the tie.
    matches = [
        _equity("BTC", "Bitcoin Depot"),
        _crypto("BTC-USD", "Bitcoin USD"),
    ]

    assert _symbols(rank_symbol_matches(matches, "BTC", "USD")) == ["BTC-USD", "BTC"]


def test_crypto_pairs_move_ahead_of_equities_named_after_the_coin():
    matches = [
        _equity("CRON", "Cronos Group Inc."),
        _crypto("CRO-USD", "Cronos"),
        _crypto("CRO-EUR", "Cronos"),
    ]

    ranked = _symbols(rank_symbol_matches(matches, "cronos", "USD"))

    assert ranked[:2] == ["CRO-USD", "CRO-EUR"]
    assert ranked[2] == "CRON"


def test_pairs_in_the_preferred_currency_come_first():
    matches = [
        _crypto("CRO-USD", "Cronos"),
        _crypto("CRO-EUR", "Cronos"),
        _crypto("CRO-BTC", "Cronos"),
    ]

    assert _symbols(rank_symbol_matches(matches, "cronos", "EUR")) == [
        "CRO-EUR",
        "CRO-USD",
        "CRO-BTC",
    ]


def test_usd_comes_after_the_preferred_currency_and_before_the_rest():
    matches = [
        _crypto("CRO-GBP", "Cronos"),
        _crypto("CRO-USD", "Cronos"),
        _crypto("CRO-BRL", "Cronos"),
    ]

    assert _symbols(rank_symbol_matches(matches, "cronos", "BRL")) == [
        "CRO-BRL",
        "CRO-USD",
        "CRO-GBP",
    ]


def test_a_query_that_names_no_coin_keeps_equities_first():
    # "gold" is a word in the token's name, not the coin itself, so the gold
    # ETFs keep their place above the PAX Gold token.
    matches = [
        _crypto("PAXG-USD", "PAX Gold"),
        _equity("GLD", "SPDR Gold Shares"),
        _equity("IAU", "iShares Gold Trust"),
    ]

    assert _symbols(rank_symbol_matches(matches, "gold", "USD")) == [
        "GLD",
        "IAU",
        "PAXG-USD",
    ]


def test_provider_order_is_kept_between_equally_good_matches():
    matches = [_equity("AAPL", "Apple Inc."), _equity("APLE", "Apple Hospitality")]

    assert _symbols(rank_symbol_matches(matches, "apple", "USD")) == ["AAPL", "APLE"]


def test_a_single_result_is_returned_unchanged():
    matches = [_equity("AAPL", "Apple Inc.")]

    assert rank_symbol_matches(matches, "apple", "USD") == matches


def test_an_empty_query_does_not_reorder_anything():
    matches = [_crypto("CRO-USD", "Cronos"), _equity("CRON", "Cronos Group Inc.")]

    assert rank_symbol_matches(matches, "   ", "USD") == matches


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_market_search_ranks_pairs_by_the_requested_currency(
    client: AsyncClient, auth_headers
):
    fake = AsyncMock()
    fake.search = AsyncMock(
        return_value=[
            _crypto("CRO-USD", "Cronos"),
            _crypto("CRO-BRL", "Cronos"),
            _crypto("CRO-EUR", "Cronos"),
        ]
    )
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get(
            "/api/assets/market/search?q=cronos&currency=EUR", headers=auth_headers
        )

    assert resp.status_code == 200, resp.text
    assert [row["symbol"] for row in resp.json()] == ["CRO-EUR", "CRO-USD", "CRO-BRL"]


@pytest.mark.asyncio
async def test_market_search_falls_back_to_the_workspace_currency(
    client: AsyncClient, auth_headers
):
    # The test workspace is in BRL.
    fake = AsyncMock()
    fake.search = AsyncMock(
        return_value=[
            _crypto("CRO-USD", "Cronos"),
            _crypto("CRO-BRL", "Cronos"),
            _crypto("CRO-EUR", "Cronos"),
        ]
    )
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/search?q=cronos", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert [row["symbol"] for row in resp.json()] == ["CRO-BRL", "CRO-USD", "CRO-EUR"]


@pytest.mark.asyncio
async def test_market_search_keeps_every_result(client: AsyncClient, auth_headers):
    fake = AsyncMock()
    fake.search = AsyncMock(
        return_value=[_equity("CRON", "Cronos Group Inc."), _crypto("CRO-USD", "Cronos")]
    )
    with patch("app.api.assets.get_market_price_provider", return_value=fake):
        resp = await client.get("/api/assets/market/search?q=cronos", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert [row["symbol"] for row in resp.json()] == ["CRO-USD", "CRON"]
