"""Importing broker orders: reading the file, and applying it to holdings."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.user import User
from app.models.workspace import Workspace
from app.providers.market_price import MarketSymbolQuote
from app.services import asset_import_service


class FakeProvider:
    """Knows two tickers, and counts calls so the batching can be asserted."""

    def __init__(self, known=("AAPL", "PETR4.SA")):
        self.known = {t.upper() for t in known}
        self.latest_price_calls = 0
        self.quote_calls = 0

    async def get_latest_prices(self, symbols):
        self.latest_price_calls += 1
        return {s.upper(): (Decimal("100") if s.upper() in self.known else None) for s in symbols}

    async def get_quote(self, symbol):
        self.quote_calls += 1
        if symbol.upper() not in self.known:
            return None
        return MarketSymbolQuote(
            symbol=symbol.upper(), name=f"{symbol.upper()} Inc", price=100.0,
            currency="USD", exchange="XNAS", quote_type="EQUITY", logo_url=None,
        )

    async def get_quotes(self, symbols):
        return {s.upper(): await self.get_quote(s) for s in symbols}


def _csv(*rows: str) -> bytes:
    return "\n".join(rows).encode("utf-8")


async def _only_asset(session: AsyncSession, workspace: Workspace) -> Asset:
    """The single holding the import created, asserted rather than assumed."""
    result = await session.execute(select(Asset).where(Asset.workspace_id == workspace.id))
    assets = result.scalars().all()
    assert len(assets) == 1, f"expected one holding, found {len(assets)}"
    return assets[0]


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


def test_headers_are_recognised_without_a_mapping():
    orders, errors, columns = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,10,150.00,1.20",
    ))
    assert errors == []
    assert columns == ["ticker", "date", "quantity", "price", "fee"]
    assert (orders[0].ticker, orders[0].kind, orders[0].quantity) == ("AAPL", "buy", Decimal("10"))
    assert orders[0].fee == Decimal("1.20")


def test_portuguese_broker_headers_are_recognised():
    """A Brazilian export names its columns in Portuguese and prices with commas."""
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "Ativo;Data;Quantidade;Preço;Corretagem",
        "PETR4.SA;10/02/2026;100;38,50;2,90",
    ))
    assert errors == []
    assert orders[0].ticker == "PETR4.SA"
    assert orders[0].price == Decimal("38.50")
    assert orders[0].fee == Decimal("2.90")
    assert str(orders[0].date) == "2026-02-10"


def test_negative_quantity_reads_as_a_sale():
    """The convention most broker exports use for a sale."""
    orders, _, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price",
        "AAPL,2026-03-02,-4,178.30",
    ))
    assert orders[0].kind == "sell"
    assert orders[0].quantity == Decimal("4")  # stored unsigned


def test_explicit_kind_column_wins_over_the_sign():
    orders, _, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,tipo",
        "AAPL,2026-03-02,4,178.30,venda",
    ))
    assert orders[0].kind == "sell"


def test_unreadable_rows_are_reported_not_silently_dropped():
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,150.00",
        ",2026-01-15,10,150.00",
        "MSFT,not-a-date,10,150.00",
        "MSFT,2026-01-15,zero,150.00",
    ))
    assert len(orders) == 1
    assert [(e.row, e.reason) for e in errors] == [
        (3, "missing_ticker"), (4, "invalid_date"), (5, "invalid_quantity"),
    ]


def test_latin1_file_does_not_blow_up():
    """Broker exports are not always UTF-8; the transaction importer raises here."""
    content = "ticker,date,quantity,price,name\nAAPL,2026-01-15,10,150.00,Ação\n".encode("latin-1")
    orders, errors, _ = asset_import_service.parse_orders_csv(content)
    assert errors == []
    assert orders[0].ticker == "AAPL"


def test_missing_required_column_is_a_parse_error():
    with pytest.raises(ValueError, match="quantity"):
        asset_import_service.parse_orders_csv(_csv("ticker,date,price", "AAPL,2026-01-15,150.00"))


def test_explicit_mapping_overrides_the_guess():
    orders, errors, _ = asset_import_service.parse_orders_csv(
        _csv("col_a,col_b,col_c,col_d", "AAPL,2026-01-15,10,150.00"),
        column_mapping={"ticker": "col_a", "date": "col_b", "quantity": "col_c", "price": "col_d"},
    )
    assert errors == []
    assert orders[0].ticker == "AAPL"


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    return FakeProvider()


async def _import(session, workspace, user, csv_bytes, provider, **kwargs):
    orders, _, _ = asset_import_service.parse_orders_csv(csv_bytes)
    return await asset_import_service.import_orders(
        session, workspace.id, user.id, orders, market_provider=provider, **kwargs
    )


@pytest.mark.asyncio
async def test_import_creates_the_holding_and_the_position(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,10,100.00,0",
        "AAPL,2026-02-15,10,200.00,0",
    ), provider)

    assert summary["imported"] == 2
    assert summary["holdings_created"] == 1

    stored = await _only_asset(session, test_workspace)
    assert stored.ticker == "AAPL"
    assert stored.units == Decimal("20")
    assert stored.average_price == Decimal("150")  # the math the ledger already had


@pytest.mark.asyncio
async def test_distinct_tickers_are_resolved_in_one_call(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """200 rows over 2 tickers must not mean 200 provider calls."""
    rows = ["ticker,date,quantity,price"]
    for i in range(1, 51):
        rows.append(f"AAPL,2026-01-{i % 28 + 1:02d},1,100.00")
        rows.append(f"PETR4.SA,2026-01-{i % 28 + 1:02d},1,30.00")
    await _import(session, test_workspace, test_user, _csv(*rows), provider)
    assert provider.latest_price_calls == 1


@pytest.mark.asyncio
async def test_unknown_ticker_is_refused_with_its_row(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
        "NOSUCH,2026-01-16,5,10.00",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason, e.ticker) for e in summary["errors"]] == [(3, "unknown_ticker", "NOSUCH")]


@pytest.mark.asyncio
async def test_a_sell_beyond_the_position_is_caught_before_anything_is_written(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A file that starts mid-history would otherwise fail halfway through."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,5,100.00",
        "AAPL,2026-02-15,-9,120.00",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(3, "oversell")]
    assert (await _only_asset(session, test_workspace)).units == Decimal("5")


@pytest.mark.asyncio
async def test_rows_are_replayed_in_date_order_not_file_order(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Brokers export newest-first; a sell listed above its buy is still valid."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-02-15,-4,120.00",
        "AAPL,2026-01-15,10,100.00",
    ), provider)

    assert summary["imported"] == 2
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_reimporting_the_same_file_adds_nothing(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Fixing a mapping and re-uploading must not double the position."""
    content = _csv("ticker,date,quantity,price", "AAPL,2026-01-15,10,100.00")
    await _import(session, test_workspace, test_user, content, provider)
    summary = await _import(session, test_workspace, test_user, content, provider)

    assert summary["imported"] == 0
    assert summary["skipped"] == 1
    rows = (await session.execute(AssetTransaction.__table__.select())).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_orders_land_on_an_existing_holding(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    existing = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", units=Decimal("0"),
    )
    session.add(existing)
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)

    assert summary["holdings_created"] == 0
    assert summary["holdings_matched"] == 1
    reloaded = await session.get(Asset, existing.id)
    assert reloaded is not None and reloaded.units == Decimal("10")


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider, dry_run=True)

    assert summary["imported"] == 1
    assert summary["holdings_created"] == 1
    assert (await session.execute(select(Asset))).scalars().first() is None


@pytest.mark.parametrize(
    "header,row,expected_kind",
    [
        # One export per language Securo is translated into.
        ("Symbol,Date,Quantity,Price,Fee,Side", "AAPL,2026-01-15,10,150.00,1.20,buy", "buy"),
        ("Ativo;Data;Quantidade;Preço;Corretagem;Operação", "AAPL;15/01/2026;10;150,00;1,20;compra", "buy"),
        ("Activo,Fecha,Cantidad,Precio,Comisión,Operación", "AAPL,15/01/2026,10,150.00,1.20,venta", "sell"),
        ("Symbole,Date,Quantité,Cours,Frais,Sens", "AAPL,15/01/2026,10,150.00,1.20,achat", "buy"),
        ("Wertpapier;Datum;Stück;Kurs;Gebühr;Art", "AAPL;15.01.2026;10;150,00;1,20;verkauf", "sell"),
        ("Titolo,Data,Quantità,Prezzo,Commissioni,Operazione", "AAPL,15/01/2026,10,150.00,1.20,acquisto", "buy"),
        ("Walor;Data;Ilość;Cena;Prowizja;Rodzaj", "AAPL;15/01/2026;10;150,00;1,20;kupno", "buy"),
        ("Тикер,Дата,Количество,Цена,Комиссия,Операция", "AAPL,15/01/2026,10,150.00,1.20,продажа", "sell"),
        ("Тікер,Дата,Кількість,Ціна,Комісія,Операція", "AAPL,15/01/2026,10,150.00,1.20,купівля", "buy"),
    ],
)
def test_headers_are_recognised_in_every_language_the_app_ships(header, row, expected_kind):
    """A broker export is written in the language of whoever downloaded it."""
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(header, row))
    assert errors == [], header
    assert len(orders) == 1, header
    assert orders[0].ticker == "AAPL"
    assert orders[0].quantity == Decimal("10")
    assert orders[0].price == Decimal("150.00")
    assert orders[0].fee == Decimal("1.20")
    assert orders[0].kind == expected_kind
    assert str(orders[0].date) == "2026-01-15"


@pytest.mark.asyncio
async def test_created_holding_takes_the_quote_currency_not_the_file(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A file reporting a US stock in BRL must not label the holding BRL while
    its price feed keeps returning USD."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,10,150.00,BRL",
    ), provider)

    assert summary["imported"] == 1
    stored = await _only_asset(session, test_workspace)
    assert stored.currency == "USD"  # what the provider quotes it in


@pytest.mark.asyncio
async def test_warns_when_the_ticker_already_sits_in_another_wallet(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Two brokers, two positions is legitimate; a mis-picked wallet looks the
    same, so the preview says it rather than leaving it to be noticed later."""
    from app.models.asset_group import AssetGroup

    wallet = AssetGroup(id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=test_user.id, name="Corretora B")
    session.add(wallet)
    await session.flush()
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", group_id=wallet.id, units=Decimal("5"),
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider, dry_run=True)

    assert [(w.ticker, w.reason, w.wallet) for w in summary["warnings"]] == [
        ("AAPL", "exists_in_other_wallet", "Corretora B"),
    ]


@pytest.mark.asyncio
async def test_warns_harder_when_the_same_orders_are_already_in_another_wallet(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Importing the same file into a second wallet counts the shares twice,
    and the wallet-scoped dedup cannot see it."""
    from app.models.asset_group import AssetGroup

    wallet = AssetGroup(id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=test_user.id, name="Corretora B")
    session.add(wallet)
    await session.flush()
    await session.commit()

    content = _csv("ticker,date,quantity,price", "AAPL,2026-01-15,10,100.00")
    await _import(session, test_workspace, test_user, content, provider, group_id=wallet.id)

    summary = await _import(session, test_workspace, test_user, content, provider, dry_run=True)

    assert [(w.ticker, w.reason, w.wallet) for w in summary["warnings"]] == [
        ("AAPL", "orders_already_in_other_wallet", "Corretora B"),
    ]


# ---------------------------------------------------------------------------
# History and undo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_records_a_history_entry(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    from app.models.import_log import ImportLog

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
        "AAPL,2026-02-15,5,120.00",
    ), provider, filename="corretora.csv")

    log = (await session.execute(select(ImportLog))).scalars().one()
    assert (log.entity, log.filename, log.transaction_count) == ("asset_orders", "corretora.csv", 2)
    assert log.account_id is None  # an order import has no account
    assert summary["import_log_id"] == str(log.id)


@pytest.mark.asyncio
async def test_an_import_that_writes_nothing_leaves_no_history(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    from app.models.import_log import ImportLog

    content = _csv("ticker,date,quantity,price", "AAPL,2026-01-15,10,100.00")
    await _import(session, test_workspace, test_user, content, provider)
    summary = await _import(session, test_workspace, test_user, content, provider)  # all duplicates

    assert summary["imported"] == 0
    assert len((await session.execute(select(ImportLog))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_undo_removes_the_orders_and_the_holding_it_created(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    from app.models.import_log import ImportLog

    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)
    log = (await session.execute(select(ImportLog))).scalars().one()

    removed = await asset_import_service.undo_import(session, test_workspace.id, log)

    assert removed == 1
    assert (await session.execute(select(Asset))).scalars().first() is None
    assert (await session.execute(select(AssetTransaction))).scalars().first() is None
    assert (await session.execute(select(ImportLog))).scalars().first() is None


@pytest.mark.asyncio
async def test_undo_keeps_a_holding_that_has_other_orders_and_recomputes_it(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A holding the user also fed by hand survives the undo, with the position
    it would have had if the import had never run."""
    from app.models.import_log import ImportLog

    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)
    asset = await _only_asset(session, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("4"), price=Decimal("200.00"), fee=Decimal("0"),
        date=date(2026, 3, 1), source="manual",
    ))
    await session.commit()

    log = (await session.execute(select(ImportLog))).scalars().one()
    await asset_import_service.undo_import(session, test_workspace.id, log)

    survivor = await _only_asset(session, test_workspace)
    assert survivor.units == Decimal("4")          # only the manual buy is left
    assert survivor.average_price == Decimal("200")


@pytest.mark.asyncio
async def test_undo_leaves_a_pre_existing_holding_alone(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    from app.models.import_log import ImportLog

    existing = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", units=Decimal("0"),
    )
    session.add(existing)
    session.add(AssetTransaction(
        asset_id=existing.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("2"), price=Decimal("50.00"), fee=Decimal("0"),
        date=date(2025, 1, 1), source="manual",
    ))
    await session.commit()

    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)
    log = (await session.execute(select(ImportLog))).scalars().one()
    await asset_import_service.undo_import(session, test_workspace.id, log)

    survivor = await session.get(Asset, existing.id)
    assert survivor is not None
    assert survivor.units == Decimal("2")


class FlakyBatchProvider(FakeProvider):
    """The bulk endpoint answers empty even for tickers it knows.

    Not hypothetical: yfinance's bulk download returned a price for AAPL and
    then nothing for the same ticker seconds later, which used to reject the
    whole file as unknown tickers.
    """

    async def get_latest_prices(self, symbols):
        self.latest_price_calls += 1
        return {s.upper(): None for s in symbols}


@pytest.mark.asyncio
async def test_a_bulk_miss_is_confirmed_against_the_quote_endpoint(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    provider = FlakyBatchProvider()
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)

    assert summary["errors"] == []
    assert summary["imported"] == 1
    assert provider.quote_calls >= 1  # the bulk miss was double-checked


@pytest.mark.asyncio
async def test_a_ticker_neither_call_knows_is_still_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    provider = FlakyBatchProvider()
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "NOSUCH,2026-01-15,10,100.00",
    ), provider)

    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "unknown_ticker")]
    assert summary["imported"] == 0
