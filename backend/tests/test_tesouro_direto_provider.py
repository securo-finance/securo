from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.providers.tesouro_direto import (
    TesouroDiretoProvider,
    parse_brl_decimal,
    tesouro_symbol_for,
)


CSV = """Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha
Tesouro Selic;01/03/2029;17/06/2026;0,01;0,02;15120,00;15129,00;15.123,45
Tesouro Selic;01/03/2029;18/06/2026;0,01;0,02;15125,00;15135,00;15.130,10
Tesouro IPCA+;15/05/2035;18/06/2026;6,10;6,20;4320,00;4322,00;4.321,99
"""


def test_parse_brl_decimal_accepts_brazilian_and_plain_formats():
    assert parse_brl_decimal("15.130,10") == Decimal("15130.10")
    assert parse_brl_decimal("15130,10") == Decimal("15130.10")
    assert parse_brl_decimal("15130.10") == Decimal("15130.10")


def test_find_price_matches_title_type_and_maturity_with_latest_price_date():
    provider = TesouroDiretoProvider(csv_text=CSV)

    quote = provider.find_price(" tesouro selic ", date(2029, 3, 1))

    assert quote is not None
    assert quote.title_type == "Tesouro Selic"
    assert quote.maturity_date == date(2029, 3, 1)
    assert quote.price_date == date(2026, 6, 18)
    assert quote.pu_base == Decimal("15130.10")


def test_find_price_returns_none_for_missing_bond():
    provider = TesouroDiretoProvider(csv_text=CSV)

    assert provider.find_price("Tesouro Prefixado", date(2031, 1, 1)) is None


def test_list_latest_quotes_deduplicates_by_title_and_maturity():
    provider = TesouroDiretoProvider(csv_text=CSV)

    quotes = provider.list_latest_quotes()

    assert [(q.title_type, q.maturity_date, q.price_date, q.pu_base) for q in quotes] == [
        ("Tesouro IPCA+", date(2035, 5, 15), date(2026, 6, 18), Decimal("4321.99")),
        ("Tesouro Selic", date(2029, 3, 1), date(2026, 6, 18), Decimal("15130.10")),
    ]


def test_find_price_by_symbol_resolves_compact_market_price_symbol():
    provider = TesouroDiretoProvider(csv_text=CSV)
    symbol = tesouro_symbol_for("Tesouro Selic", date(2029, 3, 1))

    quote = provider.find_price_by_symbol(symbol)

    assert symbol.startswith("TD:")
    assert len(symbol) <= 32
    assert quote is not None
    assert quote.title_type == "Tesouro Selic"
    assert quote.pu_base == Decimal("15130.10")
