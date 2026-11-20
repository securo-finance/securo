"""Importing broker orders: reading the file, and applying it to holdings."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.fx_rate import FxRate
from app.models.user import User
from app.models.workspace import Workspace
from app.providers.market_price import MarketSymbolQuote
from app.schemas.asset_import import AssetOrderImport
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


def test_template_headers_are_auto_detected():
    """The downloaded template uses ticker* markers; they must not break detection."""
    content = b"ticker*,date*,quantity*,price*,fee,kind,currency,notes\nAAPL,2026-01-15,10,150.00,1.20,buy,USD,\n"
    orders, errors, _ = asset_import_service.parse_orders_csv(content)
    assert errors == []
    assert orders[0].ticker == "AAPL"


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


def test_windows_1252_minus_and_euro_keep_their_meaning():
    """0x96 is the en dash a broker writes for the minus, and 0x80 the euro.

    Read as Latin-1 they are C1 controls: the sign is stripped, so a sale is
    imported as a buy, and the ticker's name comes out as garbage.
    """
    content = b"ticker,date,quantity,price,name\nAAPL,2026-01-15,\x9610,150.00,A\x80O\n"
    orders, errors, _ = asset_import_service.parse_orders_csv(content)
    assert errors == []
    assert orders[0].kind == "sell"
    assert orders[0].quantity == Decimal("10")
    assert orders[0].name == "A€O"


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
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=date(2026, 1, 15),
        rate=Decimal("5"), source="test",
    ))
    await session.commit()
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,10,150.00,BRL",
    ), provider)

    assert summary["imported"] == 1
    stored = await _only_asset(session, test_workspace)
    assert stored.currency == "USD"  # what the provider quotes it in
    assert stored.average_price == Decimal("30")  # 150 BRL at 5 BRL per USD


async def _eur_rate(session: AsyncSession, on: date, usd_to_eur: str) -> None:
    session.add(FxRate(
        base_currency="USD", quote_currency="EUR", date=on,
        rate=Decimal(usd_to_eur), source="test",
    ))
    await session.commit()


async def _ledger(session: AsyncSession) -> list[AssetTransaction]:
    return list((await session.execute(
        select(AssetTransaction).order_by(AssetTransaction.date)
    )).scalars())


@pytest.mark.asyncio
async def test_orders_in_another_currency_are_converted_at_their_date(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A broker that settles US stocks in EUR: 400 EUR is not 400 USD (#1358)."""
    await _eur_rate(session, date(2026, 1, 15), "0.8")
    await _eur_rate(session, date(2026, 2, 16), "0.9")

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee,kind,currency",
        "AAPL,2026-01-15,1,400,2,buy,EUR",
        "AAPL,2026-02-16,1,450,0,buy,eur",
    ), provider)

    assert summary["imported"] == 2
    assert summary["errors"] == []
    stored = await _only_asset(session, test_workspace)
    assert stored.currency == "USD"
    ledger = await _ledger(session)
    assert [(tx.price, tx.fee) for tx in ledger] == [
        (Decimal("500.000000"), Decimal("2.50")),  # 400 / 0.8, 2 / 0.8
        (Decimal("500.000000"), Decimal("0.00")),  # 450 / 0.9
    ]

    # The same file again adds nothing: the converted rows are what it matches.
    again = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee,kind,currency",
        "AAPL,2026-01-15,1,400,2,buy,EUR",
        "AAPL,2026-02-16,1,450,0,buy,eur",
    ), provider)
    assert again["imported"] == 0
    assert again["skipped"] == 2


@pytest.mark.asyncio
async def test_a_converted_row_keeps_its_file_price_for_the_table(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """The preview shows both numbers: what the file paid, and what the row
    becomes at its own date's rate."""
    await _eur_rate(session, date(2026, 1, 15), "0.8")

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee,kind,currency",
        "AAPL,2026-01-15,1,400,2,buy,EUR",
    ), provider, dry_run=True)

    [row] = summary["orders"]
    assert row.currency == "EUR"
    assert row.price == Decimal("400")
    assert row.converted_currency == "USD"
    assert row.converted_price == Decimal("500.000000")
    assert row.converted_fee == Decimal("2.50")


@pytest.mark.asyncio
async def test_orders_onto_an_existing_holding_take_its_currency(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    await _eur_rate(session, date(2026, 1, 15), "0.8")
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", units=Decimal("0"),
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,2,80,EUR",
    ), provider)

    assert summary["imported"] == 1
    assert [tx.price for tx in await _ledger(session)] == [Decimal("100.000000")]


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_an_order_with_no_rate_for_its_currency_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, dry_run
):
    """No rate means no conversion, and never 400 EUR stored as 400 USD."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,1,400,EUR",
        "AAPL,2026-01-16,1,100,USD",
    ), provider, dry_run=dry_run)

    assert summary["imported"] == 1
    assert [(e.row, e.reason, e.detail) for e in summary["errors"]] == [
        (2, "no_fx_rate", "EUR → USD"),
    ]
    if not dry_run:
        assert [tx.price for tx in await _ledger(session)] == [Decimal("100.000000")]


async def _apple_in_usd(session: AsyncSession, user: User, workspace: Workspace) -> Asset:
    asset = Asset(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", units=Decimal("0"),
    )
    session.add(asset)
    await session.commit()
    return asset


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_a_rate_the_table_lacks_is_fetched_but_never_stored(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, dry_run, monkeypatch
):
    """The preview is read-gated and promises to write nothing, and the import
    reaches the same answer as its preview."""
    asked: list[date] = []

    async def fetch(_session, on):
        asked.append(on)
        return {"EUR": Decimal("0.8")}

    monkeypatch.setattr(asset_import_service.fx_rate_service, "fetch_rates", fetch)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,1,400,EUR",
    ), provider, dry_run=dry_run)

    assert summary["imported"] == 1
    assert summary["errors"] == []
    assert asked == [date(2026, 1, 15)]
    assert (await session.execute(select(FxRate))).scalars().first() is None
    if not dry_run:
        assert [tx.price for tx in await _ledger(session)] == [Decimal("500.000000")]


@pytest.mark.asyncio
async def test_a_failing_rate_provider_is_asked_once_per_import(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, monkeypatch
):
    """Every request can wait 30 s; 40 dates must not mean 40 of them."""
    calls = 0

    async def fetch(_session, on):
        nonlocal calls
        calls += 1
        raise ValueError("openexchangerates_app_id not configured")

    monkeypatch.setattr(asset_import_service.fx_rate_service, "fetch_rates", fetch)
    rows = ["ticker,date,quantity,price,currency"]
    rows += [f"AAPL,2026-{m:02d}-{d:02d},1,400,EUR" for m in (1, 2) for d in range(1, 21)]
    summary = await _import(session, test_workspace, test_user, _csv(*rows), provider, dry_run=True)

    assert calls == 1
    assert {e.reason for e in summary["errors"]} == {"no_fx_rate"}
    assert len(summary["errors"]) == 40


@pytest.mark.asyncio
async def test_rate_requests_per_import_are_capped(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, monkeypatch
):
    calls = 0

    async def fetch(_session, on):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(asset_import_service.fx_rate_service, "fetch_rates", fetch)
    rows = ["ticker,date,quantity,price,currency"]
    rows += [f"AAPL,2026-{m:02d}-{d:02d},1,400,EUR" for m in (1, 2, 3) for d in range(1, 21)]
    await _import(session, test_workspace, test_user, _csv(*rows), provider, dry_run=True)

    assert calls == asset_import_service._FX_FETCH_BUDGET


@pytest.mark.asyncio
async def test_both_legs_of_a_cross_rate_come_from_the_same_day(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """EUR on the order's day and BRL only the day before must not be mixed
    into a rate no day ever had."""
    session.add_all([
        FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 1, 15), rate=Decimal("0.8"), source="t"),
        FxRate(base_currency="USD", quote_currency="EUR", date=date(2026, 1, 14), rate=Decimal("1.0"), source="t"),
        FxRate(base_currency="USD", quote_currency="BRL", date=date(2026, 1, 14), rate=Decimal("5.0"), source="t"),
    ])
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Petrobras", type="stock", currency="BRL", valuation_method="market_price",
        ticker="PETR4.SA", units=Decimal("0"),
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "PETR4.SA,2026-01-15,1,100,EUR",
    ), provider)

    assert summary["imported"] == 1
    assert [tx.price for tx in await _ledger(session)] == [Decimal("500.000000")]  # not 625


@pytest.mark.asyncio
async def test_a_converted_order_is_recognised_after_the_rate_changes(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """The duplicate check reads what the file said, not today's conversion."""
    await _eur_rate(session, date(2026, 1, 14), "0.8")
    content = _csv("ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,400,EUR")
    await _import(session, test_workspace, test_user, content, provider)
    # The exact day's rate arrives later and differs from the one used.
    await _eur_rate(session, date(2026, 1, 15), "0.75")

    again = await _import(session, test_workspace, test_user, content, provider)

    assert (again["imported"], again["skipped"]) == (0, 1)
    ledger = await _ledger(session)
    assert [(tx.price, tx.source_currency, tx.source_price) for tx in ledger] == [
        (Decimal("500.000000"), "EUR", Decimal("400.000000")),
    ]


@pytest.mark.asyncio
async def test_an_order_imported_unconverted_before_is_held_back_and_named(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Before #1358 a 400 EUR order was stored as 400 USD. Uploading the file
    again must not add the same purchase a second time at 500; that stored row
    could also be a different USD order, so the row is named, not hidden."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("1"), price=Decimal("400"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="import",
    ))
    await _eur_rate(session, date(2026, 1, 15), "0.8")

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,1,400,EUR",
    ), provider)

    assert (summary["imported"], summary["skipped"]) == (0, 0)
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "possible_duplicate")]
    assert len(await _ledger(session)) == 1


@pytest.mark.asyncio
async def test_a_distinct_order_at_a_converted_rows_price_is_imported(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """400 EUR stored as 500 USD is not the same order as a new 500 EUR buy."""
    await _eur_rate(session, date(2026, 1, 15), "0.8")
    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,400,EUR",
    ), provider)

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,500,EUR",
    ), provider)

    assert (summary["imported"], summary["skipped"], summary["errors"]) == (1, 0, [])
    assert [tx.price for tx in await _ledger(session)] == [Decimal("500"), Decimal("625")]


@pytest.mark.asyncio
async def test_orders_from_two_currencies_at_one_converted_price_are_both_kept(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """400 EUR and 500 GBP both convert to 500 USD that day; neither file says
    the same thing, so the second order is not taken for the first."""
    await _eur_rate(session, date(2026, 1, 15), "0.8")
    session.add(FxRate(
        base_currency="USD", quote_currency="GBP", date=date(2026, 1, 15),
        rate=Decimal("1"), source="test",
    ))
    await session.commit()
    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,400,EUR",
    ), provider)

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,500,GBP",
    ), provider)

    assert (summary["imported"], summary["skipped"], summary["errors"]) == (1, 0, [])
    assert sorted((tx.source_currency, tx.price) for tx in await _ledger(session)) == [
        ("EUR", Decimal("500")), ("GBP", Decimal("500")),
    ]


@pytest.mark.asyncio
async def test_a_row_without_origin_at_the_converted_price_is_named_not_skipped(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A 500 USD row with no recorded origin may be this 400 EUR order or
    another one: the import holds it back and says so."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("1"), price=Decimal("500"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="import",
    ))
    await _eur_rate(session, date(2026, 1, 15), "0.8")

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,400,EUR",
    ), provider)

    assert (summary["imported"], summary["skipped"]) == (0, 0)
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "possible_duplicate")]
    assert len(await _ledger(session)) == 1


@pytest.mark.asyncio
async def test_a_converted_match_with_another_broker_id_is_a_new_order(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """ORD-2 at 400 EUR converts to the 500 USD that ORD-1 was stored at; the
    ids say they are two orders, so the shares are not lost."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("1"), price=Decimal("500"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="import", external_id="ORD-1",
    ))
    await _eur_rate(session, date(2026, 1, 15), "0.8")

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency,order id",
        "AAPL,2026-01-15,1,400,EUR,ORD-2",
    ), provider)

    assert (summary["imported"], summary["skipped"]) == (1, 0)
    assert sorted(tx.external_id or "" for tx in await _ledger(session)) == ["ORD-1", "ORD-2"]


@pytest.mark.asyncio
async def test_a_same_currency_buy_with_another_broker_id_is_a_new_order(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """ORD-2 shares every number with ORD-1 but is another order: taking it for
    the stored one would drop its shares."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("1"), price=Decimal("500"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="import", external_id="ORD-1",
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,order id",
        "AAPL,2026-01-15,1,500,ORD-2",
    ), provider)

    assert (summary["imported"], summary["skipped"]) == (1, 0)
    assert sorted(tx.external_id or "" for tx in await _ledger(session)) == ["ORD-1", "ORD-2"]


class AnotherOrderWhileTheQuotesArrive(FakeProvider):
    """Writes a different broker order with the same numbers as the file's."""

    def __init__(self, session: AsyncSession, asset: Asset, workspace: Workspace):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._workspace = workspace

    async def get_quotes(self, symbols):
        self._session.add(AssetTransaction(
            asset_id=self._asset.id, workspace_id=self._workspace.id, kind="buy",
            quantity=Decimal("1"), price=Decimal("500"), fee=Decimal("0"),
            date=date(2026, 1, 15), source="manual", external_id="ORD-2",
        ))
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_an_order_landing_with_another_broker_id_is_not_taken_for_this_one(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The recheck under the lock sees ORD-2 appear; ORD-1 is still this file's
    own order, not a repeat of it."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    provider = AnotherOrderWhileTheQuotesArrive(session, asset, test_workspace)

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,order id",
        "AAPL,2026-01-15,1,500,ORD-1",
        "MSFT,2026-01-10,1,100,",
    ), provider)

    assert (summary["imported"], summary["skipped"]) == (2, 0)
    assert sorted(
        tx.external_id or ""
        for tx in await _ledger(session) if tx.asset_id == asset.id
    ) == ["ORD-1", "ORD-2"]


@pytest.mark.asyncio
async def test_the_files_price_is_kept_unrounded_for_the_next_upload(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A seven-decimal price must still match itself after the rate changes."""
    await _eur_rate(session, date(2026, 1, 14), "0.8")
    content = _csv("ticker,date,quantity,price,currency", "AAPL,2026-01-15,1,400.1234567,EUR")
    await _import(session, test_workspace, test_user, content, provider)
    await _eur_rate(session, date(2026, 1, 15), "0.75")

    again = await _import(session, test_workspace, test_user, content, provider)

    assert (again["imported"], again["skipped"]) == (0, 1)
    [tx] = await _ledger(session)
    assert tx.source_price == Decimal("400.1234567")


@pytest.mark.asyncio
async def test_a_converted_order_on_the_first_day_gets_no_rate_not_a_crash(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,0001-01-01,1,400,EUR",
        "AAPL,2026-01-16,1,100,USD",
    ), provider, dry_run=True)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "no_fx_rate")]


@pytest.mark.asyncio
async def test_a_known_order_is_skipped_before_any_rate_is_looked_for(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """A duplicate is a duplicate, not a row refused for a missing rate."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("1"), price=Decimal("500"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="import", external_id="ORD-1",
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency,order id",
        "AAPL,2026-01-15,1,400,EUR,ORD-1",
    ), provider, dry_run=True)

    assert summary["errors"] == []
    assert summary["skipped"] == 1


class NoCurrencyQuoteProvider(FakeProvider):
    """The price feed knows the ticker but its quote names no currency."""

    async def get_quote(self, symbol):
        self.quote_calls += 1
        return None


@pytest.mark.asyncio
async def test_a_ticker_without_a_usable_quote_is_refused_in_the_preview(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The import would refuse it, so the preview must not promise it."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-01-15,1,400,EUR",
    ), NoCurrencyQuoteProvider(), dry_run=True)

    assert summary["imported"] == 0
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "unknown_ticker")]


class ResolvesButCannotQuote(FakeProvider):
    """The batch check finds the ticker; the quote needed to create the
    holding never comes."""

    async def get_latest_prices(self, symbols):
        self.latest_price_calls += 1
        return {s.upper(): Decimal("100") for s in symbols}

    async def get_quotes(self, symbols):
        self.quote_calls += 1
        return {}


@pytest.mark.asyncio
async def test_a_new_holding_without_a_quote_is_refused_in_the_preview(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The confirmation cannot create a holding without a quote, so the
    preview must not count those rows as importable."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), ResolvesButCannotQuote(), dry_run=True)

    assert summary["imported"] == 0
    assert summary["holdings_created"] == 0
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "unknown_ticker")]


@pytest.mark.asyncio
@pytest.mark.parametrize("written", ["GBp", "GBX"])
async def test_a_price_in_pence_is_stored_in_pounds(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, written
):
    """The feed normalises LSE quotes to GBP, so 150 GBp is a 1.50 holding price."""
    session.add(Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="iShares", type="etf", currency="GBP", valuation_method="market_price",
        ticker="IITU.L", units=Decimal("0"),
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee,currency",
        f"IITU.L,2026-01-15,10,150,200,{written}",
    ), provider)

    assert summary["imported"] == 1
    assert [(tx.price, tx.fee, tx.source_currency) for tx in await _ledger(session)] == [
        (Decimal("1.500000"), Decimal("2.00"), "GBp"),
    ]


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


@pytest.mark.asyncio
async def test_undoing_an_import_a_later_sell_depends_on_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Deleting the imported buy would leave the later sell without the units
    it sold: a position the ledger itself would have refused. The undo is
    refused before anything is taken back."""
    from fastapi import HTTPException

    from app.models.import_log import ImportLog
    from app.schemas.asset import AssetTransactionCreate
    from app.services import asset_transaction_service

    await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,10,100.00",
    ), provider)
    asset = await _only_asset(session, test_workspace)
    await asset_transaction_service.add_transaction(
        session, asset.id, test_workspace.id,
        AssetTransactionCreate(
            kind="sell", quantity=Decimal("8"), price=Decimal("120"),
            date=date(2026, 3, 1),
        ),
    )

    log = (await session.execute(select(ImportLog))).scalars().one()
    with pytest.raises(HTTPException) as exc:
        await asset_import_service.undo_import(session, test_workspace.id, log)

    assert exc.value.status_code == 409
    assert (await session.execute(select(ImportLog))).scalars().first() is not None
    remaining = (await session.execute(select(AssetTransaction))).scalars().all()
    assert sorted(tx.kind for tx in remaining) == ["buy", "sell"]


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


# ---------------------------------------------------------------------------
# Fee and explicit date-format validation (issue #850)
# ---------------------------------------------------------------------------


def test_malformed_fee_is_reported():
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,10,150.00,abc",
    ))
    assert orders == []
    assert [(e.row, e.reason) for e in errors] == [(2, "invalid_fee")]


def test_negative_fee_is_reported():
    """A negative fee would push the cost basis down, so it fails the row."""
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,10,150.00,-1.50",
    ))
    assert orders == []
    assert [(e.row, e.reason) for e in errors] == [(2, "invalid_fee")]


def test_empty_fee_stays_zero():
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,10,150.00,",
    ))
    assert errors == []
    assert orders[0].fee == Decimal("0")


def test_explicit_date_format_is_strict():
    """A format the user chose is the only format tried: 13/02 is not a
    valid month/day, so the row fails instead of silently becoming
    February 13."""
    orders, errors, _ = asset_import_service.parse_orders_csv(
        _csv(
            "ticker,date,quantity,price",
            "AAPL,13/02/2026,10,150.00",
        ),
        date_format="MM/DD/YYYY",
    )
    assert orders == []
    assert [(e.row, e.reason) for e in errors] == [(2, "invalid_date")]


# ---------------------------------------------------------------------------
# Splits (issue #1356)
# ---------------------------------------------------------------------------


async def _holding_with_ledger(session, user, workspace, *entries: tuple) -> Asset:
    """An AAPL holding whose ledger holds `entries`, cached like the app does."""
    from app.services import asset_transaction_service

    asset = Asset(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name="Apple", type="stock", currency="USD", valuation_method="market_price",
        ticker="AAPL", units=Decimal("0"),
    )
    session.add(asset)
    for kind, on, quantity, price, ratio in entries:
        old_units, new_units = ratio or (None, None)
        session.add(AssetTransaction(
            asset_id=asset.id, workspace_id=workspace.id, kind=kind, date=on,
            quantity=Decimal(quantity), price=Decimal(price), fee=Decimal("0"),
            old_units=old_units, new_units=new_units, source="manual",
        ))
    await session.flush()
    await asset_transaction_service.recompute_and_cache(session, asset)
    await session.commit()
    return asset


def test_a_split_row_is_read_with_its_ratio():
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-03-01,,,split,1,4",
        "PETR4.SA,2026-03-01,0,0,grupamento,10,1",
    ))
    assert errors == []
    assert [(o.kind, o.quantity, o.price, o.old_units, o.new_units) for o in orders] == [
        ("split", Decimal("0"), Decimal("0"), 1, 4),
        ("split", Decimal("0"), Decimal("0"), 10, 1),
    ]


@pytest.mark.parametrize(
    "row",
    [
        "AAPL,2026-03-01,,,split,1,",          # half a ratio
        "AAPL,2026-03-01,,,split,2,2",         # changes nothing
        "AAPL,2026-03-01,,,split,0,4",         # zero side
        "AAPL,2026-03-01,,,split,1.5,4",       # not whole
        "AAPL,2026-03-01,,,split,1,1000001",   # past the manual limit
        "AAPL,2026-03-01,5,10,split,1,4",      # a split moves no money
        "AAPL,2026-03-01,5,10,buy,1,4",        # a ratio on a buy
    ],
)
def test_a_split_row_is_validated_like_a_manual_split(row):
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,kind,old_units,new_units", row,
    ))
    assert orders == []
    assert [(e.row, e.reason) for e in errors] == [(2, "invalid_split")]


@pytest.mark.parametrize("cell", ["unknown", '"1,a00"', "-"])
def test_a_split_row_with_an_unreadable_money_cell_is_refused(cell):
    """A blank cell and an unreadable one both read as None, and only the
    blank one is allowed: a file that states a fee on a split is wrong even
    when the fee it states cannot be read."""
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee,kind,old_units,new_units",
        f"AAPL,2026-03-01,,,{cell},split,1,4",
    ))
    assert orders == []
    assert [(e.row, e.reason) for e in errors] == [(2, "invalid_split")]


@pytest.mark.parametrize("row", [
    "AAPL,2026-03-01,,,,split,1,4",          # blank cells
    "AAPL,2026-03-01,0,0,0,split,1,4",       # the zeros a broker writes
    "AAPL,2026-03-01,0,0.00,0.00,split,1,4",
])
def test_a_split_row_still_accepts_blank_and_zero_money_cells(row):
    orders, errors, _ = asset_import_service.parse_orders_csv(_csv(
        "ticker,date,quantity,price,fee,kind,old_units,new_units", row,
    ))
    assert errors == []
    assert (orders[0].kind, orders[0].old_units, orders[0].new_units) == ("split", 1, 4)


@pytest.mark.asyncio
async def test_a_sell_after_an_imported_split_counts_the_split_units(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-01-15,10,100.00,buy,,",
        "AAPL,2026-03-01,,,split,1,4",
        "AAPL,2026-04-01,30,30.00,sell,,",
    ), provider)

    assert summary["errors"] == []
    assert summary["imported"] == 3
    stored = await _only_asset(session, test_workspace)
    assert stored.units == Decimal("10")
    assert stored.average_price == Decimal("25")  # 1000 of cost over 40 units
    split = (await session.execute(
        select(AssetTransaction).where(AssetTransaction.kind == "split")
    )).scalar_one()
    assert (split.old_units, split.new_units, split.source) == (1, 4, "import")


@pytest.mark.asyncio
async def test_a_split_replays_before_a_trade_of_its_own_date_in_the_file(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """The split is the day's first row of the ledger even when the file lists
    it after the sell, as entering one by hand does: 10 units become 20 before
    the same day's 20 are sold, instead of the sell being short."""
    await _holding_with_ledger(
        session, test_user, test_workspace,
        ("buy", date(2026, 1, 10), "10", "100", None),
    )
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-03-01,-20,50.00,sell,,",
        "AAPL,2026-03-01,,,split,1,2",
    ), provider)

    assert summary["errors"] == []
    assert summary["imported"] == 2
    stored = await _only_asset(session, test_workspace)
    assert stored.units == Decimal("0")


@pytest.mark.asyncio
async def test_a_sell_after_a_split_in_the_ledger_is_accepted(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """The buy comes from the file, the split is already in the ledger."""
    asset = await _holding_with_ledger(
        session, test_user, test_workspace,
        ("buy", date(2026, 1, 10), "1", "100", None),
        ("split", date(2026, 3, 1), "0", "0", (1, 10)),
    )
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-02-01,5,100.00",
        "AAPL,2026-04-01,-60,15.00",
    ), provider)

    assert summary["errors"] == []
    reloaded = await session.get(Asset, asset.id)
    assert reloaded is not None and reloaded.units == Decimal("0")


@pytest.mark.asyncio
async def test_a_sell_before_a_split_is_measured_against_the_units_held_then(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Today's 100 units are 10 before the split: selling 50 then is short."""
    asset = await _holding_with_ledger(
        session, test_user, test_workspace,
        ("buy", date(2026, 1, 10), "10", "100", None),
        ("split", date(2026, 3, 1), "0", "0", (1, 10)),
    )
    assert asset.units == Decimal("100")
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-02-01,-50,120.00",
    ), provider)

    assert summary["imported"] == 0
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "oversell")]


@pytest.mark.asyncio
async def test_a_sell_that_leaves_a_later_ledger_sell_short_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    await _holding_with_ledger(
        session, test_user, test_workspace,
        ("buy", date(2026, 1, 10), "10", "100", None),
        ("sell", date(2026, 3, 1), "8", "120", None),
    )
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-02-01,-5,110.00",
    ), provider)

    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "oversell")]


@pytest.mark.asyncio
async def test_a_split_with_nothing_held_before_it_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-01-01,,,split,1,4",
        "AAPL,2026-01-15,10,100.00,buy,,",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "nothing_held_before_split")]


@pytest.mark.asyncio
async def test_a_split_dated_in_the_future_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    from datetime import timedelta

    from app.core.app_clock import app_today

    tomorrow = (app_today() + timedelta(days=1)).isoformat()
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-01-15,10,100.00,buy,,",
        f"AAPL,{tomorrow},,,split,1,4",
    ), provider)

    assert [(e.row, e.reason) for e in summary["errors"]] == [(3, "split_in_future")]


@pytest.mark.asyncio
async def test_a_split_in_another_currency_needs_no_exchange_rate(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, monkeypatch
):
    """A split moves no money, so a currency on its row asks for no rate."""

    async def fetch(_session, on):
        raise AssertionError("a split must not ask for an exchange rate")

    monkeypatch.setattr(asset_import_service.fx_rate_service, "fetch_rates", fetch)
    asset = await _holding_with_ledger(
        session, test_user, test_workspace,
        ("buy", date(2026, 1, 10), "10", "100", None),
    )
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,currency,old_units,new_units",
        "AAPL,2026-03-01,,,split,EUR,1,4",
    ), provider)

    assert summary["errors"] == []
    assert summary["imported"] == 1
    reloaded = await session.get(Asset, asset.id)
    assert reloaded is not None and reloaded.units == Decimal("40")
    split = (await session.execute(
        select(AssetTransaction).where(AssetTransaction.kind == "split")
    )).scalar_one()
    assert (split.source_currency, split.source_price) == (None, None)


@pytest.mark.asyncio
async def test_a_reimported_split_is_skipped(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    content = _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-01-15,10,100.00,buy,,",
        "AAPL,2026-03-01,,,split,1,4",
    )
    await _import(session, test_workspace, test_user, content, provider)
    summary = await _import(session, test_workspace, test_user, content, provider)

    assert (summary["imported"], summary["skipped"]) == (0, 2)
    assert (await _only_asset(session, test_workspace)).units == Decimal("40")


class TinyPriceProvider(FakeProvider):
    """Quotes a fraction of a unit, so the units are what overflows, not the value."""

    async def get_quote(self, symbol):
        quote = await super().get_quote(symbol)
        return None if quote is None else quote.model_copy(update={"price": 0.000001})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        # More units than assets.units holds.
        ("AAPL,2026-01-15,999999999,1.00,buy,,", "AAPL,2026-01-16,1,1.00,buy,,"),
        # A split that multiplies the position past it.
        ("AAPL,2026-01-15,1000,1.00,buy,,", "AAPL,2026-03-01,,,split,1,1000000"),
    ],
)
async def test_a_row_that_would_overflow_the_holding_is_refused(
    session: AsyncSession, test_user: User, test_workspace: Workspace, rows
):
    """The size check manual entries get, instead of failing in the database."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units", *rows,
    ), TinyPriceProvider())

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(3, "too_large")]


def test_explicit_date_format_parses_when_it_fits():
    orders, errors, _ = asset_import_service.parse_orders_csv(
        _csv(
            "ticker,date,quantity,price",
            "AAPL,13/02/2026,10,150.00",
        ),
        date_format="DD/MM/YYYY",
    )
    assert errors == []
    assert orders[0].date == date(2026, 2, 13)


def test_an_ignored_column_is_not_mapped_back_by_auto_detection():
    """A position-balance column named like a ratio stays ignored when asked.

    The mapping sends an empty header for Ignore; auto-detection must not
    fill it again, or the same file fails as if it carried a split ratio.
    """
    orders, errors, _ = asset_import_service.parse_orders_csv(
        _csv(
            "ticker,date,quantity,price,old units,new units",
            "AAPL,2026-01-15,10,100.00,0,10",
        ),
        column_mapping={"old_units": "", "new_units": ""},
    )

    assert errors == []
    assert (orders[0].kind, orders[0].old_units, orders[0].new_units) == ("buy", None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ratio",
    [
        (1, None),      # no ratio at all
        (None, 4),
        (1, 1),         # a ratio that changes nothing
        (0, 4),         # out of range
        (1, 2000000),
    ],
)
async def test_a_split_sent_without_a_usable_ratio_is_reported_not_raised(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, ratio
):
    """The confirmation endpoint takes rows from the body, not from the file.

    A split without a ratio used to reach the replay and trip an assertion;
    it is reported against its line, like the read-time check does.
    """
    old_units, new_units = ratio
    orders = [
        AssetOrderImport(
            row=1, ticker="AAPL", date=date(2026, 1, 15), kind="buy",
            quantity=Decimal("10"), price=Decimal("100"),
        ),
        AssetOrderImport(
            row=2, ticker="AAPL", date=date(2026, 3, 1), kind="split",
            quantity=Decimal("0"), price=Decimal("0"),
            old_units=old_units, new_units=new_units,
        ),
    ]

    summary = await asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, orders,
        market_provider=provider, dry_run=True,
    )

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "invalid_split")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["buy", "sell"])
async def test_a_ratio_on_a_trade_row_from_the_body_is_reported_not_raised(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, kind
):
    """Only a split may carry a ratio, and the table says so.

    The confirmation payload has no validator for the ratio, so a trade with
    one used to reach the flush: the constraint failed, and the error took
    the other rows of the batch with it.
    """
    quantity = Decimal("10") if kind == "buy" else Decimal("4")
    orders = [
        AssetOrderImport(
            row=1, ticker="AAPL", date=date(2026, 1, 15), kind="buy",
            quantity=Decimal("10"), price=Decimal("100"),
        ),
        AssetOrderImport(
            row=2, ticker="AAPL", date=date(2026, 3, 1), kind=kind,
            quantity=quantity, price=Decimal("50"), old_units=1, new_units=4,
        ),
    ]

    summary = await asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, orders, market_provider=provider,
    )

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "invalid_split")]
    assert [tx.kind for tx in (await session.execute(
        select(AssetTransaction)
    )).scalars().all()] == ["buy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_a_new_holding_is_measured_against_the_price_it_is_created_with(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider, dry_run
):
    """The size check has to see the quote, not only the units — and the
    preview has to say the same thing the confirmation does.

    1000 units at $100 through a 1 → 100000 split is a chart value the
    columns don't hold; it is refused against its line instead of failing in
    the database and taking the whole file with it.
    """
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-01-15,1000,100.00,buy,,",
        "AAPL,2026-03-01,,,split,1,100000",
    ), provider, dry_run=dry_run)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(3, "too_large")]
    if not dry_run:
        assert (await _only_asset(session, test_workspace)).units == Decimal("1000")


class SellingWhileTheQuotesArrive(FakeProvider):
    """Records a sale on the holding while the import fetches another quote."""

    def __init__(self, session: AsyncSession, asset: Asset, workspace: Workspace):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._workspace = workspace

    async def get_quotes(self, symbols):
        self._session.add(AssetTransaction(
            asset_id=self._asset.id, workspace_id=self._workspace.id, kind="sell",
            quantity=Decimal("5"), price=Decimal("120"), fee=Decimal("0"),
            date=date(2026, 2, 1), source="manual",
        ))
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_a_sale_that_lands_while_the_quotes_are_fetched_is_seen_before_the_write(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The replay read the ledger before the quote fetch; the write re-reads it.

    Without the second check the import commits a sale of 8 units on a
    position a concurrent sale has already taken down to 5.
    """
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    provider = SellingWhileTheQuotesArrive(session, asset, test_workspace)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-03-01,8,120.00,sell,,",
        "MSFT,2026-01-10,1,100.00,buy,,",
    ), provider)

    assert summary["holdings_created"] == 1
    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "oversell")]
    written = (await session.execute(
        select(AssetTransaction).where(
            AssetTransaction.asset_id == asset.id, AssetTransaction.kind == "sell",
        )
    )).scalars().all()
    assert [(tx.quantity, tx.date) for tx in written] == [(Decimal("5"), date(2026, 2, 1))]


class EditingTheBuyWhileTheQuotesArrive(FakeProvider):
    """Takes the stored buy down to 5 units behind the session's identity map."""

    def __init__(self, session: AsyncSession, asset: Asset, workspace: Workspace):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._workspace = workspace

    async def get_quotes(self, symbols):
        await self._session.execute(
            update(AssetTransaction)
            .where(AssetTransaction.asset_id == self._asset.id)
            .values(quantity=Decimal("5"))
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_a_holding_edited_while_the_quotes_are_fetched_is_reread_under_the_lock(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The row was already loaded, so the locked reread must refresh it.

    The buy was 10 when the replay read it and 5 by the time of the write.
    Without `populate_existing` the reread hands back the instance the session
    already holds, and the sale of 8 is written on units that are no longer
    stored.
    """
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    provider = EditingTheBuyWhileTheQuotesArrive(session, asset, test_workspace)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-03-01,8,120.00,sell,,",
        "MSFT,2026-01-10,1,100.00,buy,,",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "oversell")]


class ConfirmingTheSameSplitWhileTheQuotesArrive(FakeProvider):
    """Writes the file's split, as a second confirmation of it would."""

    def __init__(self, session: AsyncSession, asset: Asset, workspace: Workspace):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._workspace = workspace

    async def get_quotes(self, symbols):
        self._session.add(AssetTransaction(
            asset_id=self._asset.id, workspace_id=self._workspace.id, kind="split",
            quantity=Decimal("0"), price=Decimal("0"), fee=Decimal("0"),
            date=date(2026, 3, 1), old_units=1, new_units=10, source="import",
        ))
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_a_split_another_confirmation_wrote_meanwhile_is_not_written_twice(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """Two confirmations of the same file both accepted the split.

    The second one finds it in the refreshed ledger, and duplicate detection —
    which ran before the quote fetch — has to run again on what appeared since,
    or both splits land and 100 units become 1 instead of 10.
    """
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    provider = ConfirmingTheSameSplitWhileTheQuotesArrive(session, asset, test_workspace)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-03-01,,,split,1,10",
        "MSFT,2026-01-10,1,100.00,buy,,",
    ), provider)

    assert summary["skipped"] == 1
    assert summary["imported"] == 1
    splits = (await session.execute(
        select(AssetTransaction).where(AssetTransaction.kind == "split")
    )).scalars().all()
    assert len(splits) == 1


class AnotherOrderAtTheConvertedPriceWhileTheQuotesArrive(FakeProvider):
    """Records an unrelated buy that lands exactly on the converted price."""

    def __init__(self, session: AsyncSession, asset: Asset, workspace: Workspace):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._workspace = workspace

    async def get_quotes(self, symbols):
        self._session.add(AssetTransaction(
            asset_id=self._asset.id, workspace_id=self._workspace.id, kind="buy",
            quantity=Decimal("5"), price=Decimal("200"), fee=Decimal("0"),
            date=date(2026, 3, 1), source="manual",
        ))
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_a_row_at_the_converted_price_is_named_not_taken_for_the_order(
    session: AsyncSession, test_user: User, test_workspace: Workspace, monkeypatch
):
    """A converted order is only known by what its file said.

    Another buy of the same date, kind and units landed on the converted price
    (200 USD) while the quotes were fetched. Matching the order on that price
    would drop it as a repeat of that unrelated row; it is named instead, the
    way the check before the lock already does.
    """

    async def fetch(_session, on):
        return {"EUR": Decimal("0.5")}

    monkeypatch.setattr(asset_import_service.fx_rate_service, "fetch_rates", fetch)
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    provider = AnotherOrderAtTheConvertedPriceWhileTheQuotesArrive(session, asset, test_workspace)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,currency",
        "AAPL,2026-03-01,5,100,EUR",
        "MSFT,2026-01-10,1,100,",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "possible_duplicate")]
    on_that_date = (await session.execute(
        select(AssetTransaction).where(
            AssetTransaction.asset_id == asset.id,
            AssetTransaction.date == date(2026, 3, 1),
        )
    )).scalars().all()
    assert [(tx.price, tx.source) for tx in on_that_date] == [(Decimal("200"), "manual")]


class MovingTheHoldingWhileTheQuotesArrive(FakeProvider):
    """Moves the holding out from under the import while it waits for quotes.

    Either into another wallet or to another ticker: both leave the row the
    file matched pointing at a position it is no longer.
    """

    def __init__(self, session: AsyncSession, asset: Asset, **change):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset
        self._change = change

    async def get_quotes(self, symbols):
        await self._session.execute(
            update(Asset)
            .where(Asset.id == self._asset.id)
            .values(**self._change)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["group_id", "ticker"])
async def test_a_row_whose_holding_moved_while_the_quotes_arrived_is_left_out(
    session: AsyncSession, test_user: User, test_workspace: Workspace, field: str
):
    """The lock reads the holding again, and what it reads is what the row
    has to match: a holding moved to another wallet (or renamed) is no longer
    the position this file's rows were measured against, so they are reported
    and left out instead of written onto somebody else's holding."""
    asset = await _apple_in_usd(session, test_user, test_workspace)
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    wallet = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Elsewhere",
    )
    session.add(wallet)
    await session.commit()

    change = {"group_id": wallet.id} if field == "group_id" else {"ticker": "AAPL2"}
    provider = MovingTheHoldingWhileTheQuotesArrive(session, asset, **change)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-02-01,5,110.00",
        "MSFT,2026-01-15,1,100.00",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "holding_moved")]
    # The manual buy, and the buy the import wrote on the ticker it created.
    rows = (await session.execute(select(AssetTransaction))).scalars().all()
    assert len(rows) == 2
    assert not any(row.asset_id == asset.id and row.source == "import" for row in rows)


class RepricingTheHoldingWhileTheQuotesArrive(FakeProvider):
    """Moves the holding's stored price far above what its columns hold."""

    def __init__(self, session: AsyncSession, asset: Asset):
        super().__init__(known=("AAPL", "MSFT"))
        self._session = session
        self._asset = asset

    async def get_quotes(self, symbols):
        await self._session.execute(
            update(Asset)
            .where(Asset.id == self._asset.id)
            .values(last_price=Decimal("10000000000"))
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return await super().get_quotes(symbols)


@pytest.mark.asyncio
async def test_the_size_check_reads_the_price_stored_under_the_lock(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """The holding is already in this session's identity map, so the locked
    read has to refresh it.

    Its price was 100 when the replay measured the row and 1e10 by the write:
    on the second one the position does not fit the columns, and the row is
    refused instead of failing in the database and taking the file with it.
    """
    asset = await _apple_in_usd(session, test_user, test_workspace)
    asset.last_price = Decimal("100")
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("10"), price=Decimal("100"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    provider = RepricingTheHoldingWhileTheQuotesArrive(session, asset)
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-03-01,1000,100.00",
        "MSFT,2026-01-10,1,100.00",
    ), provider)

    assert summary["imported"] == 1
    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "too_large")]


@pytest.mark.asyncio
async def test_a_quantity_with_more_decimals_than_the_column_is_stored_rounded(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """`asset_transactions.quantity` keeps six decimals, so the ledger the
    import checks is the one the database stores: a seventh is rounded here,
    not by whichever database the code happens to run on."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,0.1234567,100.00",
    ), provider)

    assert summary["imported"] == 1
    stored = await _only_asset(session, test_workspace)
    assert stored.units == Decimal("0.123457")
    row = (await session.execute(select(AssetTransaction))).scalar_one()
    assert row.quantity == Decimal("0.123457")


@pytest.mark.asyncio
async def test_reuploading_an_order_with_more_decimals_than_the_column_keeps_is_skipped(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """Duplicate matching reads the quantity the ledger stores, so the same
    order looks the same on the second upload instead of being added twice."""
    content = _csv("ticker,date,quantity,price", "AAPL,2026-01-15,0.1234567,100.00")
    await _import(session, test_workspace, test_user, content, provider)
    summary = await _import(session, test_workspace, test_user, content, provider)

    assert summary["imported"] == 0
    assert summary["skipped"] == 1
    assert (await _only_asset(session, test_workspace)).units == Decimal("0.123457")


@pytest.mark.asyncio
async def test_a_quantity_too_large_for_the_column_is_reported_against_its_row(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """1e23 has more digits than the column: rounding it raises, so the row is
    named as too large and the rest of the file still imports."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price",
        "AAPL,2026-01-15,1e23,100.00",
        "AAPL,2026-02-15,5,100.00",
    ), provider)

    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "too_large")]
    assert summary["imported"] == 1
    assert (await _only_asset(session, test_workspace)).units == Decimal("5")


@pytest.mark.asyncio
async def test_a_trade_that_rounds_to_no_units_is_reported_against_its_row(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """0.0000004 units round to none in the column: booking the row would take
    its fee and no units, so it is named like any other bad quantity."""
    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,fee",
        "AAPL,2026-01-15,0.0000004,100.00,1",
        "AAPL,2026-02-15,5,100.00,",
    ), provider)

    assert [(e.row, e.reason) for e in summary["errors"]] == [(2, "invalid_quantity")]
    assert summary["imported"] == 1
    assert (await _only_asset(session, test_workspace)).units == Decimal("5")


@pytest.mark.asyncio
async def test_undo_is_refused_when_the_position_left_would_be_too_large(
    session: AsyncSession, test_user: User, test_workspace: Workspace, provider
):
    """No sale stands in the way, but the rows the undo leaves would not fit the
    holding's columns: the reverse split in the import is what keeps them down,
    and refusing it is better than a failed recompute."""
    from fastapi import HTTPException

    from app.models.import_log import ImportLog

    asset = await _apple_in_usd(session, test_user, test_workspace)
    asset.last_price = Decimal("0")
    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("100000000"), price=Decimal("1"), fee=Decimal("0"),
        date=date(2026, 1, 15), source="manual",
    ))
    await session.commit()

    summary = await _import(session, test_workspace, test_user, _csv(
        "ticker,date,quantity,price,kind,old_units,new_units",
        "AAPL,2026-02-01,,,split,100,1",
    ), provider)
    assert summary["imported"] == 1

    session.add(AssetTransaction(
        asset_id=asset.id, workspace_id=test_workspace.id, kind="buy",
        quantity=Decimal("950000000"), price=Decimal("1"), fee=Decimal("0"),
        date=date(2026, 3, 1), source="manual",
    ))
    await session.commit()

    log = (await session.execute(select(ImportLog))).scalars().one()
    with pytest.raises(HTTPException) as exc:
        await asset_import_service.undo_import(session, test_workspace.id, log)

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "import_undo_too_large"
    # Nothing was taken back.
    assert (await session.execute(select(ImportLog))).scalars().first() is not None
