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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.import_log import ImportLog
from app.providers.market_price import (
    MarketPriceProvider,
    MarketPriceRateLimitedError,
    get_market_price_provider,
)
from app.schemas.asset_import import (
    AssetImportRowError,
    AssetImportWarning,
    AssetOrderImport,
)
from app.services import asset_transaction_service
from app.services.import_service import (
    DATE_FORMAT_MAP,
    _sniff_csv_dialect,
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


def _decode(content: bytes) -> str:
    """Broker exports are not always UTF-8; fall back rather than blow up."""
    for encoding in ('utf-8-sig', 'latin-1'):
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
    folded = _strip_accents(value.strip().lower().replace('_', ' '))
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
    formats = []
    if date_format and date_format in DATE_FORMAT_MAP:
        formats.append(DATE_FORMAT_MAP[date_format])
    formats.extend(['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y'])
    for fmt in formats:
        try:
            return datetime.strptime(raw[:10] if len(raw) > 10 and fmt == '%Y-%m-%d' else raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(raw: str) -> Optional[Decimal]:
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(str(normalize_amount(raw)))
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

    mapping = {k: v for k, v in (column_mapping or {}).items() if v}
    for field, header in _auto_mapping(headers).items():
        mapping.setdefault(field, header)

    missing = [f for f in ASSET_CSV_REQUIRED_FIELDS if f not in mapping]
    if missing:
        raise ValueError(f"Missing required column mapping: {', '.join(missing)}")

    def cell(row: dict, field: str) -> str:
        header = mapping.get(field)
        if not header:
            return ''
        return (row.get(header) or '').strip()

    orders: list[AssetOrderImport] = []
    errors: list[AssetImportRowError] = []

    for index, row in enumerate(reader, start=2):  # row 1 is the header
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

        quantity = _parse_decimal(cell(row, 'quantity'))
        if quantity is None or quantity == 0:
            errors.append(AssetImportRowError(row=index, reason='invalid_quantity', ticker=ticker))
            continue

        price = _parse_decimal(cell(row, 'price'))
        if price is None or price < 0:
            errors.append(AssetImportRowError(row=index, reason='invalid_price', ticker=ticker))
            continue

        # Buy or sell comes from an explicit column when the file has one, and
        # from the sign of the quantity otherwise — the convention brokers use.
        kind_word = _normalize_header(cell(row, 'kind'))
        if kind_word in _SELL_WORDS:
            kind = 'sell'
        elif kind_word in _BUY_WORDS:
            kind = 'buy'
        elif kind_word:
            errors.append(AssetImportRowError(row=index, reason='invalid_kind', ticker=ticker))
            continue
        else:
            kind = 'sell' if quantity < 0 else 'buy'

        orders.append(AssetOrderImport(
            row=index,
            ticker=ticker,
            date=order_date,
            kind=kind,
            quantity=abs(quantity),
            price=price,
            fee=_parse_decimal(cell(row, 'fee')) or Decimal('0'),
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


async def _already_imported(
    session: AsyncSession,
    asset_ids: list[uuid.UUID],
) -> set[tuple]:
    """Fingerprints of the ledger rows these holdings already carry.

    Re-uploading the same file is the normal way people fix a mapping mistake,
    so a repeat run should add nothing rather than double the position.
    """
    if not asset_ids:
        return set()
    result = await session.execute(
        select(AssetTransaction).where(AssetTransaction.asset_id.in_(asset_ids))
    )
    seen = set()
    for tx in result.scalars().all():
        if tx.external_id:
            seen.add(('external', tx.asset_id, tx.external_id))
        seen.add(('row', tx.asset_id, tx.date, tx.kind, tx.quantity, tx.price))
    return seen


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
    ordered = sorted(orders, key=lambda o: (o.date, o.row))
    tickers = [o.ticker for o in ordered]

    holdings = await _existing_holdings(session, workspace_id, group_id, tickers)
    seen = await _already_imported(session, [a.id for a in holdings.values()])

    elsewhere = await _holdings_in_other_wallets(session, workspace_id, group_id, tickers)
    warnings: list[AssetImportWarning] = []
    if elsewhere:
        seen_elsewhere = await _already_imported(session, [a.id for a, _ in elsewhere.values()])
        for ticker, (other, wallet_name) in sorted(elsewhere.items()):
            # Same orders already on the other holding is the strong signal:
            # this is the same position about to be counted twice.
            duplicated = any(
                ('row', other.id, o.date, o.kind, o.quantity, o.price) in seen_elsewhere
                for o in ordered if o.ticker == ticker
            )
            warnings.append(AssetImportWarning(
                ticker=ticker,
                reason='orders_already_in_other_wallet' if duplicated else 'exists_in_other_wallet',
                wallet=wallet_name,
            ))

    missing_tickers = sorted({t for t in tickers if t not in holdings})
    resolvable = await resolve_tickers(missing_tickers, market_provider=market_provider) if missing_tickers else {}

    errors: list[AssetImportRowError] = []
    accepted: list[AssetOrderImport] = []
    skipped = 0

    # Units per ticker as the file is replayed, so a sell that would leave the
    # position negative is caught here rather than by the ledger halfway
    # through the write.
    units: dict[str, Decimal] = {
        ticker: Decimal(str(asset.units or 0)) for ticker, asset in holdings.items()
    }

    for order in ordered:
        if order.ticker not in holdings and not resolvable.get(order.ticker, False):
            errors.append(AssetImportRowError(row=order.row, reason='unknown_ticker', ticker=order.ticker))
            continue

        asset = holdings.get(order.ticker)
        if asset is not None:
            fingerprint = ('row', asset.id, order.date, order.kind, order.quantity, order.price)
            external = ('external', asset.id, order.external_id) if order.external_id else None
            if fingerprint in seen or (external and external in seen):
                skipped += 1
                continue

        held = units.get(order.ticker, Decimal('0'))
        if order.kind == 'sell' and order.quantity > held:
            errors.append(AssetImportRowError(
                row=order.row, reason='oversell', ticker=order.ticker,
                detail=f'selling {order.quantity} with {held} held',
            ))
            continue

        units[order.ticker] = held + (order.quantity if order.kind == 'buy' else -order.quantity)
        accepted.append(order)

    to_create = sorted({o.ticker for o in accepted if o.ticker not in holdings})
    summary = {
        'imported': len(accepted),
        'skipped': skipped,
        'holdings_created': len(to_create),
        'holdings_matched': len({o.ticker for o in accepted if o.ticker in holdings}),
        'errors': errors,
        'warnings': warnings,
    }
    if dry_run or not accepted:
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

    quotes = {}
    if to_create:
        provider = market_provider or get_market_price_provider()
        quotes = await provider.get_quotes(to_create)

    touched: dict[str, Asset] = {}
    written = 0
    for order in accepted:
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

        session.add(AssetTransaction(
            asset_id=asset.id,
            workspace_id=workspace_id,
            kind=order.kind,
            quantity=order.quantity,
            price=order.price,
            fee=order.fee or Decimal('0'),
            date=order.date,
            source='import',
            external_id=order.external_id,
            import_id=log.id,
            notes=order.notes,
        ))
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


def csv_template() -> str:
    """A file someone can fill in, with the required columns marked."""
    return (
        'ticker*,date*,quantity*,price*,fee,kind,currency,notes\n'
        'AAPL,2026-01-15,10,150.00,1.20,buy,USD,\n'
        'AAPL,2026-03-02,-4,178.30,1.20,sell,USD,partial exit\n'
        'PETR4.SA,2026-02-10,100,38.50,2.90,buy,BRL,\n'
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
    asset_ids = {row.asset_id for row in rows}

    for row in rows:
        await session.delete(row)
    await session.flush()

    for asset_id in asset_ids:
        asset = await session.get(Asset, asset_id)
        if asset is None or asset.workspace_id != workspace_id:
            continue
        remaining = await session.execute(
            select(AssetTransaction.id).where(AssetTransaction.asset_id == asset_id).limit(1)
        )
        if remaining.scalar_one_or_none() is None:
            await session.delete(asset)
        else:
            await asset_transaction_service.recompute_and_cache(session, asset)

    await session.delete(log)
    await session.commit()
    return len(rows)
