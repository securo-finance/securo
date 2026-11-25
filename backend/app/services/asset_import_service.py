"""Import investment orders (buys and sells) from a broker CSV.

A portfolio arrives as a list of orders, not as positions: a hundred rows of
"ticker, date, quantity, price, fee". Securo already knows how to turn orders
into a position — `asset_transaction_service._recompute` does the weighted
average, the fees and the realized gain — so this module's whole job is to get
the rows out of the file and onto the right holdings.

Two things shape the design:

- **Tickers are resolved once, not per row.** Creating a market-priced holding
  needs a live quote, and a broker file with 200 rows usually covers 20 or 30
  tickers. Resolving per row would make 200 provider calls and get rate-limited
  halfway through, so the distinct tickers are looked up in one batch before
  anything is written, and the preview reports the ones that came back empty.
- **The whole file is checked before a single row lands.** A sell of more units
  than the ledger holds is refused by the ledger itself, and a file that starts
  mid-history will do exactly that. Failing on row 140 after writing 139 leaves
  a portfolio that is neither the old one nor the new one, so the run either
  applies completely or not at all.
"""
import csv
import io
import logging
import uuid
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.fx_rate import FxRate
from app.models.import_log import ImportLog
from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
    get_market_price_provider,
)
from app.schemas.asset import MarketSymbolQuote
from app.schemas.asset_import import (
    AssetImportRowError,
    AssetImportWarning,
    AssetOrderImport,
)
from app.services import asset_transaction_service, fx_rate_service
from app.services.asset_group_service import ensure_group_in_workspace
from app.services.import_service import (
    DATE_FORMAT_MAP,
    _sniff_csv_dialect,
    infer_decimal_separator,
    normalize_amount,
)
from app.services.rule_engine import _strip_accents

logger = logging.getLogger(__name__)

#: How many tickers the bulk lookup may miss before we stop double-checking
#: them one by one. Past this the provider is having a bad day, not the file.
_QUOTE_FALLBACK_LIMIT = 25

#: Securo fields a CSV column can be mapped to, and which of them a file cannot
#: do without. Mirrors the transaction importer's `CSV_MAPPABLE_FIELDS`, and
#: drives both the mapping dropdowns and the downloadable template.
ASSET_CSV_MAPPABLE_FIELDS = (
    'ticker', 'date', 'quantity', 'price', 'fee', 'kind', 'currency', 'name', 'notes', 'external_id',
    'old_units', 'new_units',
)
ASSET_CSV_REQUIRED_FIELDS = ('ticker', 'date', 'quantity', 'price')

#: Header names brokers actually use, matched case- and accent-insensitively
#: after normalization. A file whose headers are recognised needs no mapping
#: step at all; anything else falls through to the dropdowns.
_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    # One entry per language Securo is translated into, because a broker
    # export is written in the language of the person who downloaded it.
    # Diacritics are folded before matching, so `Preço` finds `preco`; the
    # Cyrillic and Polish entries are spelled as they actually appear.
    'ticker': (
        'ticker', 'symbol', 'code', 'isin',
        'simbolo', 'ativo', 'papel', 'codigo',                    # pt
        'activo', 'valor',                                        # es
        'symbole', 'titre', 'actif',                              # fr
        'wertpapier', 'kuerzel', 'kurzel', 'wkn',                 # de
        'titolo', 'strumento',                                    # it
        'walor', 'instrument',                                    # pl
        'тикер', 'символ', 'бумага',                              # ru
        'тікер', 'папір',                                         # uk
    ),
    'date': (
        'date', 'trade date', 'settlement date',
        'data', 'data do negocio', 'data negocio',                # pt
        'fecha', 'fecha operacion',                               # es
        'date de transaction', 'date operation',                  # fr
        'datum', 'handelstag', 'buchungstag',                     # de
        'data operazione',                                        # it
        'data transakcji',                                        # pl
        'дата', 'дата сделки',                                    # ru
        'дата операції',                                          # uk
    ),
    'quantity': (
        'quantity', 'qty', 'shares', 'units', 'amount of shares',
        'quantidade',                                             # pt
        'cantidad', 'titulos',                                    # es
        'quantite', 'nombre', 'titres',                           # fr
        'menge', 'stueck', 'stuck', 'anzahl', 'stueckzahl',       # de
        'quantita', 'numero',                                     # it
        'ilosc', 'ilość', 'liczba', 'wolumen',                    # pl
        'количество', 'кол-во', 'объем',                          # ru
        'кількість', 'обсяг',                                     # uk
    ),
    'price': (
        'price', 'unit price', 'price per share',
        'preco', 'preco unitario', 'valor unitario',              # pt
        'precio', 'precio unitario', 'cotizacion',                # es
        'prix', 'cours', 'prix unitaire',                         # fr
        'preis', 'kurs', 'stueckpreis',                           # de
        'prezzo', 'prezzo unitario', 'quotazione',                # it
        'cena', 'cena jednostkowa',                               # pl
        'цена', 'курс',                                           # ru
        'ціна',                                                   # uk
    ),
    'fee': (
        'fee', 'fees', 'commission', 'costs',
        'taxa', 'taxas', 'corretagem', 'custos',                  # pt
        'comision', 'comisiones', 'gastos',                       # es
        'frais', 'courtage',                                      # fr
        'gebuehr', 'gebuhr', 'gebuehren', 'provision', 'kosten',  # de
        'commissione', 'commissioni', 'spese',                    # it
        'prowizja', 'oplata', 'opłata', 'koszty',                 # pl
        'комиссия', 'сбор',                                       # ru
        'комісія', 'збір',                                        # uk
    ),
    'kind': (
        'kind', 'type', 'side', 'operation', 'buy/sell',
        'tipo', 'operacao', 'c/v',                                # pt
        'operacion', 'compra/venta', 'sentido',                   # es
        'sens', 'achat/vente',                                    # fr
        'art', 'richtung', 'kauf/verkauf', 'transaktionsart',     # de
        'operazione', 'segno', 'acquisto/vendita',                # it
        'rodzaj', 'operacja', 'kupno/sprzedaz', 'strona',         # pl
        'операция', 'направление', 'покупка/продажа',             # ru
        'операція', 'купівля/продаж',                             # uk
    ),
    'currency': (
        'currency', 'ccy', 'moeda', 'moneda', 'divisa', 'devise', 'monnaie',
        'waehrung', 'wahrung', 'valuta', 'waluta', 'валюта',
    ),
    'name': (
        'name', 'description', 'security',
        'nome', 'descricao', 'nombre', 'descripcion', 'nom', 'libelle',
        'bezeichnung', 'beschreibung', 'descrizione', 'nazwa', 'opis',
        'название', 'наименование', 'назва',
    ),
    'notes': (
        'notes', 'note', 'observacao', 'observacoes', 'obs', 'observaciones',
        'remarques', 'notizen', 'bemerkung', 'notatki', 'uwagi',
        'заметки', 'примечание', 'нотатки', 'примітки',
    ),
    'external_id': ('external_id', 'id', 'order id', 'trade id', 'reference'),
    # A split row's ratio: `old_units` held before it became `new_units`.
    'old_units': ('old units', 'split from', 'ratio from'),
    'new_units': ('new units', 'split to', 'ratio to'),
}

#: Values that mean "this row is a sale". Everything else is read as a buy,
#: except a negative quantity, which is the convention most brokers export.
_SELL_WORDS = {
    'sell', 'sale', 'sold', 's',
    'venda', 'vender', 'saida', 'v',                              # pt
    'venta',                                                      # es
    'vendre', 'vente',                                            # fr
    'verkauf', 'verkaufen', 'vk',                                 # de
    'vendita', 'vendere',                                         # it
    'sprzedaz', 'sprzedaż', 'sprzedac',                           # pl
    'продажа', 'продать', 'продаж', 'продати',                    # ru/uk
}
_BUY_WORDS = {
    'buy', 'purchase', 'bought', 'b',
    'compra', 'comprar', 'entrada', 'c',                          # pt/es
    'achat', 'acheter',                                           # fr
    'kauf', 'kaufen', 'kf',                                       # de
    'acquisto', 'acquistare',                                     # it
    'kupno', 'zakup', 'kupic', 'kupić',                           # pl
    'покупка', 'купить', 'купівля', 'купити',                     # ru/uk
}
#: Values that mean "this row is a split", forward or reverse. A split row
#: carries its ratio in `old_units`/`new_units` instead of a quantity and price.
_SPLIT_WORDS = {
    'split', 'stock split', 'reverse split',
    'desdobramento', 'desdobro', 'grupamento', 'inplit',          # pt
    'desdoblamiento', 'contrasplit',                              # es
    'division', 'regroupement',                                   # fr
    'aktiensplit', 'zusammenlegung',                              # de
    'frazionamento', 'raggruppamento',                            # it
    'podzial', 'scalenie',                                        # pl
    'сплит', 'дробление', 'консолидация',                         # ru
    'спліт', 'дроблення', 'консолідація',                         # uk
}


def _parse_split_units(raw: str) -> Optional[int]:
    """One side of a split ratio: a whole number, or None when it isn't one."""
    value = _parse_decimal(raw)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


def _invalid_split(
    old_units: Optional[int],
    new_units: Optional[int],
    quantity: Optional[Decimal],
    price: Optional[Decimal],
    fee: Optional[Decimal],
) -> bool:
    """Whether a split row's ratio and money fields are unusable.

    Checked the way a split entered by hand is: a ratio that is in range and
    changes something, and no money on the row. The confirmation endpoint
    takes its rows from the request body, so this is checked there too and not
    only while reading the file — a split without a ratio would otherwise
    abort the replay instead of being reported against its line.
    """
    return (
        not asset_transaction_service.split_ratio_in_range(old_units, new_units)
        or old_units == new_units
        or any(value is not None and value != 0 for value in (quantity, price, fee))
    )


def _decode(content: bytes) -> str:
    """Broker exports are not always UTF-8; fall back rather than blow up.

    Windows-1252 comes before Latin-1 because 0x80-0x9F are typographic
    characters (euro sign, en dash) there, while Latin-1 maps them to C1
    controls, which read as a sale with no sign. cp1252 leaves 0x81, 0x8D,
    0x8F, 0x90 and 0x9D undefined, so Latin-1 is the last resort.
    """
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode('utf-8', errors='replace')


def detect_columns(content: bytes) -> list[str]:
    """The file's header names, as written, for the mapping dropdowns."""
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text), dialect=_sniff_csv_dialect(text))
    return [f.strip() for f in (reader.fieldnames or []) if f and f.strip()]


def _normalize_header(value: str) -> str:
    """Fold a header (or a buy/sell word) to its comparable form.

    Accents come off because a Brazilian export writes `Preço` and `Operação`,
    and a header that only differs by a diacritic is the same header.
    """
    folded = _strip_accents(value.strip().lower().replace('_', ' ').replace('*', '').replace('#', ''))
    return ' '.join(folded.split())


def _auto_mapping(headers: list[str]) -> dict[str, str]:
    """Guess which column is which, so a recognisable file needs no mapping."""
    normalized = {_normalize_header(h): h for h in headers}
    mapping: dict[str, str] = {}
    for field, candidates in _COLUMN_CANDIDATES.items():
        for candidate in candidates:
            if candidate in normalized:
                mapping[field] = normalized[candidate]
                break
    return mapping


def _parse_date(raw: str, date_format: Optional[str]) -> Optional[date_type]:
    raw = raw.strip()
    if not raw:
        return None

    def parse(fmt: str) -> Optional[date_type]:
        # Broker exports sometimes append a time to the date, in which case the
        # first 10 characters are the date itself.
        value = raw[:10] if len(raw) > 10 and fmt == '%Y-%m-%d' else raw
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            return None

    if date_format and date_format in DATE_FORMAT_MAP:
        # The user chose a format, so the file is parsed with it strictly,
        # like parse_csv does: "13/02/2026" must not become February 13
        # under MM/DD/YYYY.
        return parse(DATE_FORMAT_MAP[date_format])

    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y']:
        parsed = parse(fmt)
        if parsed is not None:
            return parsed
    return None


def _parse_decimal(raw: str, decimal_separator: Optional[str] = None) -> Optional[Decimal]:
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(str(normalize_amount(raw, decimal_separator)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_orders_csv(
    content: bytes,
    column_mapping: Optional[dict[str, str]] = None,
    date_format: Optional[str] = None,
) -> tuple[list[AssetOrderImport], list[AssetImportRowError], list[str]]:
    """Read a broker CSV into orders, plus one error per row that could not be read.

    Bad rows are reported rather than skipped in silence: a file where a third
    of the rows had an unreadable date should say so before anything is
    imported, not quietly bring in the other two thirds.
    """
    text = _decode(content)
    dialect = _sniff_csv_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [f.strip() for f in (reader.fieldnames or []) if f and f.strip()]
    if not headers:
        raise ValueError('CSV has no header row')

    chosen = column_mapping or {}
    # A field the user set to no column is an explicit Ignore, not a gap for
    # the auto-detection to fill: a broker that also exports position
    # balances must not have them read back as a split ratio after Ignore.
    ignored = {field for field, header in chosen.items() if not header}
    mapping = {field: header for field, header in chosen.items() if header}
    for field, header in _auto_mapping(headers).items():
        if field in ignored:
            continue
        mapping.setdefault(field, header)

    missing = [f for f in ASSET_CSV_REQUIRED_FIELDS if f not in mapping]
    if missing:
        raise ValueError(f"Missing required column mapping: {', '.join(missing)}")

    def cell(row: dict, field: str) -> str:
        header = mapping.get(field)
        if not header:
            return ''
        return (row.get(header) or '').strip()

    rows = list(reader)
    # A cell alone cannot tell "1,500" (fifteen hundred) from "1,5" (one and a
    # half), so each money column's separator is inferred from the whole
    # column, as parse_csv does for transaction amounts.
    separators = {
        field: infer_decimal_separator(cell(row, field) for row in rows)
        for field in ('quantity', 'price', 'fee')
    }

    def number(row: dict, field: str) -> Optional[Decimal]:
        return _parse_decimal(cell(row, field), separators[field])

    orders: list[AssetOrderImport] = []
    errors: list[AssetImportRowError] = []

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        if not any((v or '').strip() for v in row.values()):
            continue

        ticker = cell(row, 'ticker').upper()
        if not ticker:
            errors.append(AssetImportRowError(row=index, reason='missing_ticker'))
            continue

        order_date = _parse_date(cell(row, 'date'), date_format)
        if order_date is None:
            errors.append(AssetImportRowError(row=index, reason='invalid_date', ticker=ticker))
            continue

        kind_word = _normalize_header(cell(row, 'kind'))
        old_units = _parse_split_units(cell(row, 'old_units'))
        new_units = _parse_split_units(cell(row, 'new_units'))
        if kind_word in _SPLIT_WORDS:
            # A split carries no money. A cell that says something the file
            # cannot read is a bad row, not an empty one: `_parse_decimal`
            # answers None for both, so the text is checked as well.
            money = [
                (cell(row, field), number(row, field))
                for field in ('quantity', 'price', 'fee')
            ]
            if (
                any(raw and value is None for raw, value in money)
                or _invalid_split(
                    old_units,
                    new_units,
                    *(value for _, value in money),
                )
            ):
                errors.append(AssetImportRowError(row=index, reason='invalid_split', ticker=ticker))
                continue
            orders.append(AssetOrderImport(
                row=index,
                ticker=ticker,
                date=order_date,
                kind='split',
                quantity=Decimal('0'),
                price=Decimal('0'),
                currency=(cell(row, 'currency') or None),
                name=(cell(row, 'name') or None),
                notes=(cell(row, 'notes') or None),
                external_id=(cell(row, 'external_id') or None),
                old_units=old_units,
                new_units=new_units,
            ))
            continue
        if cell(row, 'old_units') or cell(row, 'new_units'):
            # A ratio only means something on a split.
            errors.append(AssetImportRowError(row=index, reason='invalid_split', ticker=ticker))
            continue

        quantity = number(row, 'quantity')
        if quantity is None or quantity == 0:
            errors.append(AssetImportRowError(row=index, reason='invalid_quantity', ticker=ticker))
            continue

        price = number(row, 'price')
        if price is None or price < 0:
            errors.append(AssetImportRowError(row=index, reason='invalid_price', ticker=ticker))
            continue

        # Buy or sell comes from an explicit column when the file has one, and
        # from the sign of the quantity otherwise — the convention brokers use.
        if kind_word in _SELL_WORDS:
            kind = 'sell'
        elif kind_word in _BUY_WORDS:
            kind = 'buy'
        elif kind_word:
            errors.append(AssetImportRowError(row=index, reason='invalid_kind', ticker=ticker))
            continue
        else:
            kind = 'sell' if quantity < 0 else 'buy'

        # Fees are costs: a negative one would push the cost basis down, and
        # a malformed fee is reported like the other bad cells instead of
        # being read as zero.
        fee_raw = cell(row, 'fee')
        fee = number(row, 'fee')
        if fee_raw and (fee is None or fee < 0):
            errors.append(AssetImportRowError(row=index, reason='invalid_fee', ticker=ticker))
            continue
        fee = fee or Decimal('0')

        orders.append(AssetOrderImport(
            row=index,
            ticker=ticker,
            date=order_date,
            kind=kind,
            quantity=abs(quantity),
            price=price,
            fee=fee,
            currency=(cell(row, 'currency') or None),
            name=(cell(row, 'name') or None),
            notes=(cell(row, 'notes') or None),
            external_id=(cell(row, 'external_id') or None),
        ))

    return orders, errors, headers


async def resolve_tickers(
    tickers: list[str],
    *,
    market_provider: Optional[MarketPriceProvider] = None,
) -> dict[str, bool]:
    """Which of these tickers the price provider recognises.

    One batch call answers for the whole file, which is what keeps a 200-row
    import from making 200 requests. The bulk endpoint is not authoritative
    though: it answers with an empty result often enough — the same ticker can
    come back priced and then empty seconds later — that treating a miss as
    proof would reject real holdings. So the few it did not answer for are
    confirmed one by one against the quote endpoint, which is.

    A ticker nobody recognises can still be imported onto a holding that
    already exists in the workspace; it only blocks a holding that would have
    to be created.
    """
    provider = market_provider or get_market_price_provider()
    unique = sorted({t.upper() for t in tickers if t})
    if not unique:
        return {}

    resolved: dict[str, bool] = {}
    try:
        prices = await provider.get_latest_prices(unique)
        resolved = {t: prices.get(t) is not None for t in unique}
    except MarketPriceRateLimitedError:
        raise  # the endpoint turns this into a 429 the user can act on
    except Exception:
        # Swallowing this silently would report every ticker as unknown and
        # blame the file for the provider's outage.
        logger.warning("Bulk price lookup failed; falling back to quotes", exc_info=True)
        resolved = {t: False for t in unique}

    unconfirmed = [t for t in unique if not resolved.get(t)]
    for ticker in unconfirmed[:_QUOTE_FALLBACK_LIMIT]:
        try:
            resolved[ticker] = await provider.get_quote(ticker) is not None
        except MarketPriceRateLimitedError:
            raise
        except Exception:
            logger.warning("Quote lookup failed for %s", ticker, exc_info=True)
            resolved[ticker] = False
    return resolved


async def _lock_imported_tickers(
    session: AsyncSession, workspace_id: uuid.UUID, tickers: list[str]
) -> None:
    """Make two imports of the same holding take turns, before either reads.

    A holding the file creates does not exist yet, so the holdings' own rows
    cannot order this: two imports of a new ticker would both find it missing
    and both create it, leaving two holdings of one position. The lock is taken
    on the ticker, in sorted order so two imports covering several tickers
    cannot take each other's, and lasts to the end of the transaction — which
    includes the decisions made from that first read.

    PostgreSQL only, in the same shape as the price-cache lock in
    `plugins/logos.py`: SQLite, which the tests use, serialises writers itself.
    """
    if session.bind is None or session.bind.dialect.name != 'postgresql':
        return
    for ticker in tickers:
        await session.execute(
            select(func.pg_advisory_xact_lock(
                func.hashtext(f'asset-import:{workspace_id}:{ticker}')
            ))
        )


async def _existing_holdings(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    group_id: Optional[uuid.UUID],
    tickers: list[str],
) -> dict[str, Asset]:
    if not tickers:
        return {}
    result = await session.execute(
        select(Asset).where(
            Asset.workspace_id == workspace_id,
            Asset.valuation_method == 'market_price',
            Asset.group_id == group_id,
            Asset.ticker.in_(sorted({t.upper() for t in tickers})),
        )
    )
    return {a.ticker.upper(): a for a in result.scalars().all() if a.ticker}


async def _holdings_in_other_wallets(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    group_id: Optional[uuid.UUID],
    tickers: list[str],
) -> dict[str, tuple[Asset, Optional[str]]]:
    """The same tickers held under a *different* wallet, with that wallet's name.

    Holdings are scoped per wallet, so importing AAPL into one wallet while
    AAPL already sits in another is a legitimate thing to do — two brokers,
    two positions. It is also exactly what a mis-picked wallet looks like, and
    the portfolio then counts the same shares twice, so the preview says it out
    loud instead of leaving it to be noticed later.
    """
    if not tickers:
        return {}
    result = await session.execute(
        select(Asset, AssetGroup.name)
        .outerjoin(AssetGroup, AssetGroup.id == Asset.group_id)
        .where(
            Asset.workspace_id == workspace_id,
            Asset.valuation_method == 'market_price',
            Asset.group_id.is_not(None) if group_id is None else Asset.group_id != group_id,
            Asset.ticker.in_(sorted({t.upper() for t in tickers})),
        )
    )
    return {asset.ticker.upper(): (asset, name) for asset, name in result.all() if asset.ticker}


async def _ledger_rows(
    session: AsyncSession,
    asset_ids: list[uuid.UUID],
    *,
    refresh: bool = False,
) -> list[AssetTransaction]:
    """The ledger of these holdings as it stands in the database.

    ``refresh`` is for a read that follows a lock: a row this session had
    already loaded would otherwise come back with the values it had then,
    and the checks would run on a ledger somebody has since edited.
    """
    if not asset_ids:
        return []
    statement = select(AssetTransaction).where(AssetTransaction.asset_id.in_(asset_ids))
    if refresh:
        statement = statement.execution_options(populate_existing=True)
    result = await session.execute(statement)
    return list(result.scalars().all())


def _order_key(asset_id: uuid.UUID, entry: AssetTransaction | AssetOrderImport) -> tuple:
    """What a ledger row and a file row must share to be the same order, on
    one holding, before any price: the date, the kind, the units and, for a
    split, its ratio."""
    return (asset_id, entry.date, entry.kind, entry.quantity, entry.old_units, entry.new_units)


def _row_key(asset_id: uuid.UUID, entry: AssetTransaction | AssetOrderImport) -> tuple:
    """What makes a ledger row the same as a file row, on one holding."""
    return ('row', *_order_key(asset_id, entry), entry.price)


def _fingerprints(rows: list[AssetTransaction]) -> dict[tuple, set[Optional[str]]]:
    """Fingerprints of the ledger rows these holdings already carry, each with
    the broker ids of the rows behind it (None for a row without one).

    Re-uploading the same file is the normal way people fix a mapping mistake,
    so a repeat run should add nothing rather than double the position.

    ``row`` is the stored ledger price, for every row. ``plain`` is the same
    for rows stored at the file's own price (no conversion recorded: same
    currency, or imported before #1358). ``source`` is what a converted row's
    file said.
    """
    seen: dict[tuple, set[Optional[str]]] = {}

    def add(key: tuple, external_id: Optional[str]) -> None:
        seen.setdefault(key, set()).add(external_id)

    for tx in rows:
        ext = tx.external_id or None
        base = _order_key(tx.asset_id, tx)
        if ext:
            add(('external', tx.asset_id, ext), ext)
        add(_row_key(tx.asset_id, tx), ext)
        if tx.source_currency and tx.source_price is not None:
            add(('source', *base, tx.source_currency, tx.source_price), ext)
        else:
            add(('plain', *base, tx.price), ext)
    return seen


def _same_order(seen: dict[tuple, set[Optional[str]]], key: tuple, external_id: Optional[str]) -> bool:
    """Whether a stored row with this fingerprint can be this order.

    A row with another broker id is another order that happens to look the
    same, so only a row without an id, or with this one, counts.
    """
    ids = seen.get(key)
    if not ids:
        return False
    return external_id is None or None in ids or external_id in ids


def _appearance_keys(
    asset_id: uuid.UUID,
    order: AssetOrderImport,
    entry: AssetTransaction,
    stated: Optional[tuple[str, Decimal]],
) -> list[tuple]:
    """The fingerprints a stored row carrying this order would be known by.

    The mirror of `_fingerprints`, for one order: whatever the ledger gained
    under these keys since the replay read it is this order, written by
    somebody else. ``stated`` is what the file said for a converted row, and
    then it is the only fingerprint that counts: another order of the ledger
    may well sit at the converted price, and taking that one for this would drop
    a real order instead of naming the coincidence.
    """
    keys: list[tuple] = []
    external_id = order.external_id or None
    if external_id:
        keys.append(('external', asset_id, external_id))
    base = _order_key(asset_id, entry)
    if stated is not None:
        keys.append(('source', *base, stated[0], stated[1]))
    else:
        keys.append(('plain', *base, entry.price))
    return keys


def _stored_order(order: AssetOrderImport) -> AssetOrderImport:
    """The order with the quantity the ledger will store.

    Duplicate matching compares a file row with the stored one by quantity, so
    both have to hold the same number: the column keeps six decimals and a
    file may state more, and a re-upload would otherwise look like a new order.
    A split's zero is left alone, as is a quantity too large to round, which
    the replay reports as a row of its own.
    """
    if order.kind == 'split' or asset_transaction_service.quantity_too_large(order.quantity):
        return order
    return order.model_copy(update={
        'quantity': asset_transaction_service.stored_quantity(order.quantity),
    })


# Minor units a file may state prices in, named as the market feed names them:
# 150 GBp is 1.50 GBP. Holdings are kept in the major unit, which is what the
# feed's quotes are normalised to. Case matters only here: GBp is pence.
_PENCE = ('GBp', 'GBP', Decimal('0.01'))
_SA_CENTS = ('ZAc', 'ZAR', Decimal('0.01'))
_MINOR_UNITS: dict[str, tuple[str, str, Decimal]] = {
    'GBp': _PENCE, 'GBX': _PENCE, 'gbx': _PENCE,
    'ZAc': _SA_CENTS, 'ZAC': _SA_CENTS,
}


def _order_currency(raw: Optional[str]) -> Optional[tuple[str, str, Decimal]]:
    """The currency a file row states, as (code as kept on the ledger row, ISO
    code, units of the ISO currency per unit of the stated one), or None when
    the row states none."""
    text = (raw or '').strip()
    if not text:
        return None
    if text in _MINOR_UNITS:
        return _MINOR_UNITS[text]
    code = text.upper()
    return code, code, Decimal('1')


# How far before an order a stored rate may come from: a weekend or a bank
# holiday has no snapshot of its own.
_FX_LOOKBACK = timedelta(days=7)
# Live rate requests per import. Each can wait 30 s on the provider, so a file
# that spans hundreds of dates must not become hundreds of requests.
_FX_FETCH_BUDGET = 20


def _cross(usd_rates: dict[str, Decimal], source: str, target: str) -> Optional[Decimal]:
    legs = {**usd_rates, 'USD': Decimal('1')}
    usd_to_source, usd_to_target = legs.get(source), legs.get(target)
    if usd_to_source is None or usd_to_target is None or usd_to_source == 0:
        return None
    return usd_to_target / usd_to_source


class _OrderRates:
    """Exchange rates for the orders of one import, read the same way in the
    preview and in the import itself so both reach the same answer.

    Both legs of a cross rate always come from the same day's snapshot. Rates
    are read from the stored table first and otherwise asked of the provider,
    a bounded number of times, without storing them: the preview writes
    nothing, and neither does this.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.fetched: dict[date_type, dict[str, Decimal]] = {}
        self.fetches_left = _FX_FETCH_BUDGET
        self.known: dict[tuple[str, str, date_type], Optional[Decimal]] = {}

    async def rate(self, source: str, target: str, on: date_type) -> Optional[Decimal]:
        """Units of ``target`` per unit of ``source`` on the order's date, or None."""
        on = min(on, app_today())
        key = (source, target, on)
        if key not in self.known:
            rate = await self._stored(source, target, on)
            if rate is None:
                rate = _cross(await self._fetched(on), source, target)
            self.known[key] = rate
        return self.known[key]

    async def _stored(self, source: str, target: str, on: date_type) -> Optional[Decimal]:
        codes = [c for c in (source, target) if c != 'USD']
        result = await self.session.execute(
            select(FxRate.date, FxRate.quote_currency, FxRate.rate)
            .where(
                FxRate.base_currency == 'USD',
                FxRate.quote_currency.in_(codes),
                # Bounded so a row dated near 0001-01-01 gets no_fx_rate
                # instead of an OverflowError that sinks the whole preview.
                FxRate.date >= max(on, date_type.min + _FX_LOOKBACK) - _FX_LOOKBACK,
                FxRate.date <= on,
            )
            .order_by(desc(FxRate.date))
        )
        days: dict[date_type, dict[str, Decimal]] = {}
        for day, code, value in result.all():
            days.setdefault(day, {})[code] = value
        for day in sorted(days, reverse=True):
            rate = _cross(days[day], source, target)
            if rate is not None:
                return rate
        return None

    async def _fetched(self, on: date_type) -> dict[str, Decimal]:
        if on not in self.fetched:
            if self.fetches_left <= 0:
                return {}
            self.fetches_left -= 1
            try:
                self.fetched[on] = await fx_rate_service.fetch_rates(self.session, on)
            except Exception:
                # Not configured, or down: asking again for the next date
                # would only wait out the same failure.
                logger.warning('FX rate fetch for an order import failed for %s', on, exc_info=True)
                self.fetches_left = 0
                self.fetched[on] = {}
        return self.fetched[on]


def _ledger_problem(
    ledger: list[AssetTransaction],
    entry: AssetTransaction,
    asset: Asset,
) -> Optional[tuple[str, Optional[str]]]:
    """Why `entry` can't join this holding's ledger, as (reason, detail), or None.

    The same checks a buy, sell or split entered by hand goes through, run on
    the ledger as it stands on the entry's date: a sell or a reverse split
    must not leave any later sell short, a split needs units to split, and
    the result has to fit the asset's columns.
    """
    if entry.kind == 'split' and asset_transaction_service.nothing_held_before(ledger, entry.date):
        return 'nothing_held_before_split', None
    prospective = ledger + [entry]
    if entry.kind in ('sell', 'split'):
        over = asset_transaction_service._detect_oversell(prospective)
        if over is not None:
            attempted, available = over
            return 'oversell', f'selling {attempted} with {available} held'
    if asset_transaction_service.too_large_to_store(prospective, asset):
        return 'too_large', None
    return None


def _created_holding(quote: Optional[MarketSymbolQuote]) -> Asset:
    """The holding the file is about to create, as far as the size check reads
    it: the units and the price they are worth at.

    Unsaved on purpose: it is measured against, never added to the session.
    """
    return Asset(
        valuation_method='market_price',
        last_price=Decimal(str(quote.price)) if quote is not None else None,
        last_price_at=datetime.now(timezone.utc) if quote is not None else None,
        # Only so a message naming the converted price names its currency.
        currency=quote.currency if quote is not None else None,
    )


def _measure_created_holdings(
    accepted: list[tuple[AssetOrderImport, AssetTransaction]],
    holdings: dict[str, Asset],
    quotes: dict[str, Optional[MarketSymbolQuote]],
    errors: list[AssetImportRowError],
) -> list[tuple[AssetOrderImport, AssetTransaction]]:
    """Measure the rows of a holding the file creates against its quote.

    The replay checks those rows on units alone, because the quote is not
    fetched yet, so a split that does not fit the columns at the real price
    would be listed as importable and then dropped at confirmation. Measured
    here, in the preview too, against the same quote the write uses.

    A ticker whose quote came back empty is reported the way the write reports
    it, so the preview does not promise rows the confirmation cannot create.
    """
    ledgers: dict[str, list[AssetTransaction]] = {}
    kept: list[tuple[AssetOrderImport, AssetTransaction]] = []
    for order, entry in accepted:
        if order.ticker in holdings:
            kept.append((order, entry))
            continue
        quote = quotes.get(order.ticker)
        if quote is None:
            errors.append(AssetImportRowError(
                row=order.row, reason='unknown_ticker', ticker=order.ticker,
            ))
            continue
        ledger = ledgers.setdefault(order.ticker, [])
        problem = _ledger_problem(ledger, entry, _created_holding(quote))
        if problem is not None:
            reason, detail = problem
            errors.append(AssetImportRowError(
                row=order.row, reason=reason, ticker=order.ticker, detail=detail,
            ))
            continue
        ledger.append(entry)
        kept.append((order, entry))
    return kept


async def _recheck_before_write(
    session: AsyncSession,
    accepted: list[tuple[AssetOrderImport, AssetTransaction]],
    holdings: dict[str, Asset],
    quotes: dict[str, Optional[MarketSymbolQuote]],
    errors: list[AssetImportRowError],
    group_id: Optional[uuid.UUID],
    *,
    seen_before_lock: dict[tuple, set[Optional[str]]],
    origin: dict[int, tuple[str, Decimal]],
) -> tuple[list[tuple[AssetOrderImport, AssetTransaction]], list[AssetImportRowError], list[int]]:
    """Measure every accepted row again against the ledger as it is now.

    The replay read the stored ledger before the quotes were fetched, and a
    sale entered by hand while they were on their way would not be measured by
    it: the file could commit a backdated split that leaves that sale short.
    A holding the file creates was checked on units alone, because its price
    was not known yet. Both are settled here, under a lock on the holdings
    being written to — the manual entry paths take the same lock — and with
    the price the holding is created at, so what is written has been checked
    against what is stored and what is charged.

    Duplicate detection ran before the same window, so it is run again on the
    rows that appeared since: a second confirmation of the same file, or the
    same order entered by hand, must not be written a second time. Those rows
    are counted as skipped, the way the replay already treats a repeat. A
    converted order is only known by what its file said — a row that appeared
    at the converted price is another order that happens to land on the same
    number, and is named as a possible duplicate rather than skipped.

    A row that no longer holds is reported against its line and left out,
    rather than aborting an import whose other rows are fine.
    """
    tickers = {order.ticker for order, _ in accepted}
    asset_ids = sorted(asset.id for ticker, asset in holdings.items() if ticker in tickers)
    ledgers: dict[str, list[AssetTransaction]] = {ticker: [] for ticker in tickers if ticker in holdings}
    settled_rows: list[int] = []
    appeared: dict[tuple, set[Optional[str]]] = {}
    moved: set[str] = set()
    if asset_ids:
        by_id = {asset.id: ticker for ticker, asset in holdings.items() if ticker in tickers}
        # The refreshed row is what the size check below has to see: a refresh
        # of the price may have landed since this session loaded the holding.
        locked = await session.execute(
            select(Asset)
            .where(Asset.id.in_(asset_ids))
            .order_by(Asset.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        for asset in locked.scalars().all():
            ticker = by_id[asset.id]
            if (asset.ticker or '').upper() != ticker or asset.group_id != group_id:
                # Renamed, or moved out of this wallet, while the quotes were
                # on their way: the position this file matched is no longer
                # the one its rows would land on.
                moved.add(ticker)
                continue
            holdings[ticker] = asset
        stored: list[AssetTransaction] = []
        for row in await _ledger_rows(session, asset_ids, refresh=True):
            ledgers[by_id[row.asset_id]].append(row)
            stored.append(row)
        # What the ledger gained since the replay read it, each key with the
        # broker ids behind it now: only the ids that were not there before are
        # new, and an order with another id is another order, not this one.
        for key, ids in _fingerprints(stored).items():
            new_ids = ids - seen_before_lock.get(key, set())
            if new_ids:
                appeared[key] = new_ids

    created = {ticker: _created_holding(quotes.get(ticker)) for ticker in tickers - set(holdings)}

    kept: list[tuple[AssetOrderImport, AssetTransaction]] = []
    for order, entry in accepted:
        asset = holdings.get(order.ticker) or created[order.ticker]
        if order.ticker in moved:
            errors.append(AssetImportRowError(
                row=order.row, reason='holding_moved', ticker=order.ticker,
            ))
            continue
        if appeared:
            ext = order.external_id or None
            if any(
                _same_order(appeared, key, ext)
                for key in _appearance_keys(asset.id, order, entry, origin.get(order.row))
            ):
                # Somebody wrote this very order between the replay and the lock.
                settled_rows.append(order.row)
                continue
            if origin.get(order.row) is not None and _same_order(
                appeared, ('plain', *_order_key(asset.id, entry), entry.price), ext,
            ):
                # The same coincidence the replay names when it meets it before
                # the lock: a row stored at this order's converted price, which
                # may be another order. Adding it could count the shares twice.
                errors.append(AssetImportRowError(
                    row=order.row, reason='possible_duplicate', ticker=order.ticker,
                    detail=f'{entry.price} {asset.currency}',
                ))
                continue
        ledger = ledgers.setdefault(order.ticker, [])
        problem = _ledger_problem(ledger, entry, asset)
        if problem is not None:
            reason, detail = problem
            errors.append(AssetImportRowError(
                row=order.row, reason=reason, ticker=order.ticker, detail=detail,
            ))
            continue
        ledger.append(entry)
        kept.append((order, entry))
    return kept, errors, settled_rows


async def import_orders(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    orders: list[AssetOrderImport],
    *,
    group_id: Optional[uuid.UUID] = None,
    dry_run: bool = False,
    filename: Optional[str] = None,
    market_provider: Optional[MarketPriceProvider] = None,
) -> dict:
    """Apply a file of orders to the workspace's holdings.

    Returns the counts the UI reports, and — on a dry run — the same numbers
    without writing anything, so the preview can promise what the import will
    do instead of guessing.
    """
    await ensure_group_in_workspace(session, group_id, workspace_id)
    # The order the ledger replays in (`_replay_order`): by date, a split
    # before that day's trades, then the file's own order. Checking the rows in
    # any other order would measure a same-day sell against a split the file
    # states on a later line, and the stored ledger would replay it otherwise.
    # The quantity the ledger stores, before anything is matched against the
    # ledger: the same order uploaded again has to look the same both times.
    ordered = [
        _stored_order(o)
        for o in sorted(orders, key=lambda o: (o.date, 0 if o.kind == 'split' else 1, o.row))
    ]
    tickers = [o.ticker for o in ordered]
    if not dry_run:
        # Before anything is read: a second import of the same ticker has to
        # see what this one writes, not the holdings as they were.
        await _lock_imported_tickers(session, workspace_id, sorted({t.upper() for t in tickers}))

    holdings = await _existing_holdings(session, workspace_id, group_id, tickers)
    existing_rows = await _ledger_rows(session, [a.id for a in holdings.values()])
    seen = _fingerprints(existing_rows)

    elsewhere = await _holdings_in_other_wallets(session, workspace_id, group_id, tickers)
    warnings: list[AssetImportWarning] = []
    if elsewhere:
        seen_elsewhere = _fingerprints(await _ledger_rows(session, [a.id for a, _ in elsewhere.values()]))
        for ticker, (other, wallet_name) in sorted(elsewhere.items()):
            # Same orders already on the other holding is the strong signal:
            # this is the same position about to be counted twice.
            duplicated = any(
                _row_key(other.id, o) in seen_elsewhere
                for o in ordered if o.ticker == ticker
            )
            warnings.append(AssetImportWarning(
                ticker=ticker,
                reason='orders_already_in_other_wallet' if duplicated else 'exists_in_other_wallet',
                wallet=wallet_name,
            ))

    missing_tickers = sorted({t for t in tickers if t not in holdings})
    resolvable = await resolve_tickers(missing_tickers, market_provider=market_provider) if missing_tickers else {}

    # The currency each holding is priced in. A holding to be created takes
    # its quote's, so a file that states a currency needs that quote now, in
    # the preview too, to know whether its prices must be converted (#1358).
    # Only a quote that names its currency counts: without one the import
    # could not create the holding either.
    priced_in = {ticker: asset.currency.upper() for ticker, asset in holdings.items() if asset.currency}
    quotes: dict[str, Optional[MarketSymbolQuote]] = {}
    quote_now = sorted({
        o.ticker for o in ordered
        if _order_currency(o.currency) and o.ticker not in holdings and resolvable.get(o.ticker)
    })
    if quote_now:
        fetched = await (market_provider or get_market_price_provider()).get_quotes(quote_now)
        usable = {t: q for t, q in fetched.items() if q and q.currency}
        quotes.update(usable)
        priced_in.update({t: q.currency.upper() for t, q in usable.items()})
    rates = _OrderRates(session)
    # What the file said for each converted row, kept on its ledger entry.
    origin: dict[int, tuple[str, Decimal]] = {}

    errors: list[AssetImportRowError] = []
    accepted: list[tuple[AssetOrderImport, AssetTransaction]] = []
    skipped_rows: list[int] = []

    # Each holding's ledger as the file is replayed onto it: what it already
    # holds, splits included, plus the rows accepted so far. Every row is
    # checked against the ledger on its own date, the way an entry made by
    # hand is, so a sell is measured against the units held then and not
    # against today's position, and nothing the ledger or the columns would
    # refuse is left to fail halfway through the write.
    ledgers: dict[str, list[AssetTransaction]] = {ticker: [] for ticker in holdings}
    by_id = {asset.id: ticker for ticker, asset in holdings.items()}
    for tx in existing_rows:
        ledgers[by_id[tx.asset_id]].append(tx)
    # A holding the file creates has no quote yet, so only its units and
    # average price are measured.
    unquoted = Asset(valuation_method='market_price')
    # Same-date rows go in the order the ledger replays them (`ordered`): a
    # split first, then the file's own order, after whatever the ledger
    # already held. The entry time keeps that order for the rows written here.
    entered_at = datetime.now(timezone.utc)
    today = app_today()

    for order in ordered:
        if order.ticker not in holdings and not resolvable.get(order.ticker, False):
            errors.append(AssetImportRowError(row=order.row, reason='unknown_ticker', ticker=order.ticker))
            continue

        if (
            order.kind != 'split'
            and asset_transaction_service.quantity_too_large(order.quantity)
        ):
            # More digits than the column holds: reporting it against its line
            # keeps the rest of the file importable, but the row would only
            # fail while saving.
            errors.append(AssetImportRowError(row=order.row, reason='too_large', ticker=order.ticker))
            continue

        if order.kind != 'split' and order.quantity == 0:
            # Fewer units than the column keeps (0.0000004) round to none: the
            # row would book its fee and move no units, and its fingerprint
            # would match any other trade that rounds away.
            errors.append(AssetImportRowError(row=order.row, reason='invalid_quantity', ticker=order.ticker))
            continue

        stated = _order_currency(order.currency)
        asset = holdings.get(order.ticker)
        # A ledger row is in the holding's currency. An order paid in another
        # one is converted at its own date's rate, and refused when there is
        # no rate rather than read 1:1 (#1358).
        target = priced_in.get(order.ticker)
        # A split moves no money, so its currency is never converted.
        converts = bool(
            order.kind != 'split' and stated and target and (stated[1] != target or stated[2] != 1)
        )
        if asset is not None:
            # Matched on what the file says, before any conversion: the same
            # file again is skipped whatever the rates say by now.
            ext = order.external_id or None
            fingerprint = _order_key(asset.id, order)
            if (
                (ext and ('external', asset.id, ext) in seen)
                or (stated and _same_order(seen, ('source', *fingerprint, stated[0], order.price), ext))
                or (not converts and _same_order(seen, _row_key(asset.id, order), ext))
            ):
                skipped_rows.append(order.row)
                continue
            if converts and _same_order(seen, ('plain', *fingerprint, order.price), ext):
                # A row stored at this very number in the holding's currency:
                # either this order from an import made before conversion
                # existed, or a different order at that price. Adding it could
                # count the shares twice, so it is left out and named.
                errors.append(AssetImportRowError(
                    row=order.row, reason='possible_duplicate', ticker=order.ticker,
                    detail=f'{order.price} {target}',
                ))
                continue

        if stated and target is None and asset is None:
            errors.append(AssetImportRowError(row=order.row, reason='unknown_ticker', ticker=order.ticker))
            continue
        if converts and stated is not None and target is not None:
            written, source, unit = stated
            rate = Decimal('1') if source == target else await rates.rate(source, target, order.date)
            if rate is None:
                errors.append(AssetImportRowError(
                    row=order.row, reason='no_fx_rate', ticker=order.ticker,
                    detail=f'{source} → {target}',
                ))
                continue
            origin[order.row] = (written, order.price)
            order = order.model_copy(update={
                'price': (order.price * rate * unit).quantize(Decimal('0.000001')),
                'fee': (order.fee * rate * unit).quantize(Decimal('0.01')),
                'currency': target,
            })
            # A converted row that matches was already found above through
            # what its file said; one from another currency or price is
            # another order, even when both convert to the same number. Only
            # a row stored at the converted price without a recorded origin
            # could be this order, or another one: it is named, not dropped.
            if asset is not None and _same_order(
                seen, ('plain', *_order_key(asset.id, order), order.price), order.external_id or None,
            ):
                errors.append(AssetImportRowError(
                    row=order.row, reason='possible_duplicate', ticker=order.ticker,
                    detail=f'{order.price} {target}',
                ))
                continue

        if order.kind != 'split' and (
            order.old_units is not None or order.new_units is not None
        ):
            # The rows of a confirmed import come from the request body, so a
            # ratio on a buy or a sell gets here without the file reader. The
            # table refuses it (the columns must be null off a split), and the
            # flush error would abort the other rows of the batch.
            errors.append(AssetImportRowError(row=order.row, reason='invalid_split', ticker=order.ticker))
            continue

        if order.kind == 'split' and _invalid_split(
            order.old_units, order.new_units, order.quantity, order.price, order.fee,
        ):
            errors.append(AssetImportRowError(row=order.row, reason='invalid_split', ticker=order.ticker))
            continue

        if order.kind == 'split':
            if asset is not None and asset_transaction_service.split_not_allowed(asset):
                errors.append(AssetImportRowError(row=order.row, reason='split_not_allowed', ticker=order.ticker))
                continue
            if order.date > today:
                errors.append(AssetImportRowError(row=order.row, reason='split_in_future', ticker=order.ticker))
                continue

        entry = AssetTransaction(
            kind=order.kind,
            # The quantity the column stores, so the replay below checks the
            # same number the row will hold (a file may state more decimals).
            quantity=asset_transaction_service.stored_quantity(order.quantity),
            price=order.price,
            fee=order.fee or Decimal('0'),
            date=order.date,
            old_units=order.old_units,
            new_units=order.new_units,
            created_at=entered_at + timedelta(microseconds=len(accepted)),
        )
        ledger = ledgers.setdefault(order.ticker, [])
        problem = _ledger_problem(ledger, entry, asset or unquoted)
        if problem is not None:
            reason, detail = problem
            errors.append(AssetImportRowError(row=order.row, reason=reason, ticker=order.ticker, detail=detail))
            continue

        ledger.append(entry)
        accepted.append((order, entry))

    # A holding the file creates gets its quote here, before anything is
    # written, so both the preview and the rows checked below are measured
    # against the price the holding will actually carry.
    to_create = sorted({o.ticker for o, _ in accepted if o.ticker not in holdings})
    unquoted = [t for t in to_create if t not in quotes]
    if unquoted:
        provider = market_provider or get_market_price_provider()
        quotes.update(await provider.get_quotes(unquoted))
    accepted = _measure_created_holdings(accepted, holdings, quotes, errors)

    to_create = sorted({o.ticker for o, _ in accepted if o.ticker not in holdings})
    summary = {
        'imported': len(accepted),
        'skipped': len(skipped_rows),
        'skipped_rows': skipped_rows,
        'orders': _preview_rows(orders, accepted, skipped_rows, errors, origin, priced_in, quotes),
        'holdings_created': len(to_create),
        'holdings_matched': len({o.ticker for o, _ in accepted if o.ticker in holdings}),
        'errors': errors,
        'warnings': warnings,
    }
    if dry_run or not accepted:
        return summary

    accepted, errors, settled_rows = await _recheck_before_write(
        session, accepted, holdings, quotes, errors, group_id,
        seen_before_lock=seen, origin=origin,
    )
    skipped_rows.extend(settled_rows)
    to_create = sorted({o.ticker for o, _ in accepted if o.ticker not in holdings})
    summary.update({
        'imported': len(accepted),
        'skipped': len(skipped_rows),
        'skipped_rows': skipped_rows,
        'orders': _preview_rows(orders, accepted, skipped_rows, errors, origin, priced_in, quotes),
        'holdings_created': len(to_create),
        'holdings_matched': len({o.ticker for o, _ in accepted if o.ticker in holdings}),
        'errors': errors,
    })
    if not accepted:
        return summary

    # The log exists before the rows so they can point at it, and is removed
    # again if the run ends up writing nothing.
    log = ImportLog(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=None,
        entity='asset_orders',
        filename=filename or 'orders.csv',
        format='csv',
        transaction_count=0,
    )
    session.add(log)
    await session.flush()

    touched: dict[str, Asset] = {}
    written = 0
    for order, entry in accepted:
        asset = holdings.get(order.ticker)
        if asset is None:
            quote = quotes.get(order.ticker)
            if quote is None:
                # Resolvable a moment ago in the batch check, gone now. Report
                # it rather than inventing a holding with no price.
                errors.append(AssetImportRowError(row=order.row, reason='unknown_ticker', ticker=order.ticker))
                continue
            asset = Asset(
                user_id=user_id,
                workspace_id=workspace_id,
                name=order.name or quote.name or order.ticker,
                type=asset_transaction_service._type_from_quote(quote.quote_type),
                # The quote's currency wins, exactly as when a holding is
                # created by hand: a file that reports an American stock in
                # BRL would otherwise label the holding BRL while its price
                # feed keeps returning USD, and the portfolio total drifts.
                currency=quote.currency,
                valuation_method='market_price',
                group_id=group_id,
                ticker=order.ticker,
                ticker_exchange=quote.exchange,
                last_price=Decimal(str(quote.price)),
                last_price_at=datetime.now(timezone.utc),
                logo_url=quote.logo_url,
                source='yfinance',
            )
            session.add(asset)
            await session.flush()
            holdings[order.ticker] = asset

        # The entry the checks ran on is the one written, entry time included.
        entry.asset_id = asset.id
        entry.workspace_id = workspace_id
        entry.source = 'import'
        entry.external_id = order.external_id
        entry.import_id = log.id
        entry.notes = order.notes
        if order.row in origin:
            entry.source_currency, entry.source_price = origin[order.row]
        session.add(entry)
        touched[order.ticker] = asset
        written += 1

    await session.flush()
    # Once per holding, not once per row: the recompute walks the whole ledger.
    for asset in touched.values():
        await asset_transaction_service.recompute_and_cache(session, asset)

    if written:
        log.transaction_count = written
        session.add(log)
    else:
        await session.delete(log)
    await session.commit()

    summary['errors'] = errors
    summary['imported'] = written
    summary['import_log_id'] = str(log.id) if written else None
    summary['holdings_created'] = len([t for t in to_create if t in touched])
    return summary


def _preview_rows(
    orders: list[AssetOrderImport],
    accepted: list[tuple[AssetOrderImport, AssetTransaction]],
    skipped_rows: list[int],
    errors: list[AssetImportRowError],
    origin: dict[int, tuple[str, Decimal]],
    priced_in: dict[str, str],
    quotes: dict[str, Optional[MarketSymbolQuote]],
) -> list[AssetOrderImport]:
    """The table's rows: what this run would import, and in which currency.

    A row the dry run already finds in the ledger, or refuses, is left out, so
    the table, the count and the button describe the same import. A row the
    file paid in another currency keeps the price and currency it was read
    with, and carries what it becomes at its own date's rate, so both numbers
    can be shown before the person confirms.
    """
    converted = {order.row: order for order, _ in accepted if order.row in origin}
    left_out = set(skipped_rows) | {error.row for error in errors}
    # A holding the file creates takes the currency of the quote it is
    # created at, fetched after the holdings were read.
    display_currency = dict(priced_in)
    for ticker, quote in quotes.items():
        if quote is not None and quote.currency:
            display_currency.setdefault(ticker, quote.currency.upper())

    rows: list[AssetOrderImport] = []
    for order in orders:
        if order.row in left_out:
            continue
        shown = converted.get(order.row)
        if shown is not None:
            rows.append(order.model_copy(update={
                'converted_price': shown.price,
                'converted_fee': shown.fee,
                'converted_currency': shown.currency,
            }))
            continue
        currency = order.currency or display_currency.get(order.ticker)
        rows.append(order.model_copy(update={'currency': currency}) if currency else order)
    return rows


def csv_template() -> str:
    """A file someone can fill in, with the required columns marked."""
    return (
        'ticker*,date*,quantity*,price*,fee,kind,currency,notes,old_units,new_units\n'
        'AAPL,2026-01-15,10,150.00,1.20,buy,USD,,,\n'
        'AAPL,2026-03-02,-4,178.30,1.20,sell,USD,partial exit,,\n'
        'PETR4.SA,2026-02-10,100,38.50,2.90,buy,BRL,,,\n'
        'PETR4.SA,2026-04-01,,,,split,BRL,2-for-1,1,2\n'
    )


async def undo_import(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    log: ImportLog,
) -> int:
    """Take back every order an import wrote, and leave the portfolio as it was.

    Deleting the rows is only half of it. A position is derived from its
    ledger, so each touched holding has to be recomputed, and a holding this
    import created from nothing has to go with it — otherwise undoing leaves
    an empty ticker sitting in the wallet. A holding that still has orders
    after the delete is one the user also fed by hand or by an earlier import,
    so it stays.
    """
    result = await session.execute(
        select(AssetTransaction).where(AssetTransaction.import_id == log.id)
    )
    rows = list(result.scalars().all())
    asset_ids = sorted({row.asset_id for row in rows})

    # The holdings are locked before the ledger is read and checked, in ID
    # order as the import and the manual entries lock them: an entry that sells
    # the units this import supplied must not land between the check and the
    # delete, or the undo would leave it short. Waiting here makes the ledger
    # read below the one the delete acts on.
    holdings: dict[uuid.UUID, Asset] = {}
    ledger_rows: list[AssetTransaction] = []
    if asset_ids:
        locked = await session.execute(
            select(Asset)
            .where(Asset.id.in_(asset_ids), Asset.workspace_id == workspace_id)
            .order_by(Asset.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        holdings = {asset.id: asset for asset in locked.scalars().all()}
        # Read again once the lock is held: the rows this session loaded
        # before it may have been edited while it waited.
        ledger_rows = await _ledger_rows(session, asset_ids, refresh=True)
        rows = [row for row in ledger_rows if row.import_id == log.id]

    # A later entry may sell the units this import supplied, and taking those
    # rows back would leave a position the ledger itself would have refused —
    # the recompute can only clamp it. Checked before anything is deleted, so
    # a refused undo leaves the ledger as it was.
    remaining: dict[uuid.UUID, list[AssetTransaction]] = {asset_id: [] for asset_id in asset_ids}
    for tx in ledger_rows:
        if tx.import_id != log.id:
            remaining[tx.asset_id].append(tx)
    for asset_id, ledger in remaining.items():
        over = asset_transaction_service._detect_oversell(ledger)
        if over is not None:
            attempted, available = over
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    # A code and the counts, so the panel can say it in the
                    # reader's language: the sentence itself cannot be matched.
                    'code': 'import_undo_oversell',
                    'message': (
                        "This import can't be undone: an entry that stays in the "
                        f"ledger sells {asset_transaction_service.fmt_units(attempted)} "
                        f"units with {asset_transaction_service.fmt_units(available)} "
                        "held. Edit or delete that entry first."
                    ),
                    'attempted': asset_transaction_service.fmt_units(attempted),
                    'available': asset_transaction_service.fmt_units(available),
                },
            )
        asset = holdings.get(asset_id)
        if asset is not None and asset_transaction_service.too_large_to_store(ledger, asset):
            # No sale in the way, but the units left — or their value at the
            # current quote — would not fit the holding's columns.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'code': 'import_undo_too_large',
                    'message': (
                        "This import can't be undone: the holding left without it "
                        "is too large to store."
                    ),
                },
            )

    for row in rows:
        await session.delete(row)
    await session.flush()

    for asset_id in asset_ids:
        asset = await session.get(Asset, asset_id)
        if asset is None or asset.workspace_id != workspace_id:
            continue
        left = await session.execute(
            select(AssetTransaction.id).where(AssetTransaction.asset_id == asset_id).limit(1)
        )
        # A synced holding is its provider's, whatever this import added to it.
        if left.scalar_one_or_none() is None and asset.connection_id is None:
            await session.delete(asset)
        else:
            await asset_transaction_service.recompute_and_cache(session, asset)

    await session.delete(log)
    await session.commit()
    return len(rows)
