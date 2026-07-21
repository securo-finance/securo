import csv
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from ofxparse import OfxParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from types import SimpleNamespace

from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services import recurring_match_service
from app.services.credit_card_service import apply_effective_date
from app.services.rule_engine import apply_rule_actions, evaluate_conditions
from app.services.rule_service import apply_rules_to_transaction
from app.services.fx_rate_service import stamp_primary_amount
from app.services.payee_service import get_or_create_payee


# Descriptions used by some Brazilian banks (e.g. Banco do Brasil) for
# balance-summary rows that arrive as <STMTTRN> blocks but are not real
# transactions. Matched case-insensitively against MEMO/NAME.
_OFX_BALANCE_ROW_DESCRIPTIONS = (
    "saldo anterior",
    "saldo do dia",
    "saldo final",
    "s a l d o",
)


def _preprocess_ofx_for_empty_fitid(content: bytes) -> bytes:
    """Synthesize a FITID for STMTTRN blocks that have an empty/missing one.

    Banco do Brasil (and a few other Brazilian banks) emit balance-summary
    rows as <STMTTRN> blocks with empty <FITID> tags, which makes ofxparse
    abort the entire import with "Empty FIT id (a required field)". We patch
    each affected block with a deterministic synthetic FITID so parsing
    succeeds; balance rows are filtered out later by description.
    """
    try:
        text = content.decode("utf-8")
        original_encoding = "utf-8"
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        original_encoding = "latin-1"

    def _replace(match: re.Match) -> str:
        block = match.group(0)
        fitid_match = re.search(r"<FITID>([^<\r\n]*)", block, re.IGNORECASE)
        has_value = fitid_match and fitid_match.group(1).strip()
        if has_value:
            return block

        seed = hashlib.sha1(block.encode("utf-8", errors="replace")).hexdigest()[:16].upper()
        synthetic = f"SYNTH-{seed}"
        if fitid_match:
            return block[: fitid_match.start(1)] + synthetic + block[fitid_match.end(1):]
        # No FITID tag at all — inject one right after the opening <STMTTRN>
        return re.sub(
            r"(<STMTTRN>)",
            rf"\1\n<FITID>{synthetic}",
            block,
            count=1,
            flags=re.IGNORECASE,
        )

    patched = re.sub(
        r"<STMTTRN>.*?</STMTTRN>",
        _replace,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return patched.encode(original_encoding, errors="replace")


def _is_balance_summary_row(description: str | None) -> bool:
    if not description:
        return False
    normalized = description.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _OFX_BALANCE_ROW_DESCRIPTIONS)


def parse_ofx(content: bytes) -> list[TransactionImport]:
    """Parse OFX file content and return transactions."""
    content = _preprocess_ofx_for_empty_fitid(content)
    ofx = OfxParser.parse(io.BytesIO(content))
    transactions = []

    for account in ofx.accounts:
        for txn in account.statement.transactions:
            raw_payee = getattr(txn, 'payee', None) or None
            description = txn.memo or txn.payee or "Unknown"
            if _is_balance_summary_row(description):
                continue
            external_id = getattr(txn, 'id', None)
            # Synthetic IDs are added only to make ofxparse happy; do not
            # persist them as external_id since they are not stable bank
            # identifiers.
            if external_id and external_id.startswith("SYNTH-"):
                external_id = None
            transactions.append(TransactionImport(
                description=description,
                amount=abs(Decimal(str(txn.amount))),
                date=txn.date.date() if hasattr(txn.date, 'date') else txn.date,
                type="credit" if txn.amount > 0 else "debit",
                external_id=external_id,
                payee_raw=raw_payee,
            ))

    return transactions


def parse_qif(content: bytes) -> list[TransactionImport]:
    """Parse QIF file content and return transactions."""
    # Try UTF-8 first, fall back to Latin-1 for legacy software (e.g. Microsoft Money)
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')
    transactions = []

    # Split into transaction blocks by "^"
    blocks = text.split('^')
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        txn_date = None
        amount = None
        payee = None
        memo = None

        for line in lines:
            line = line.strip()
            if not line:
                continue
            tag, value = line[0], line[1:]
            if tag == 'D':
                # Try common date formats (including 2-digit year variants)
                for fmt in [
                    '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d',
                    "%m/%d'%Y", "%m/%d'%y",
                    '%m/%d/%y', '%d/%m/%y',
                ]:
                    try:
                        txn_date = datetime.strptime(value.strip(), fmt).date()
                        break
                    except ValueError:
                        continue
            elif tag == 'T' or tag == 'U':
                try:
                    amount = Decimal(value.strip().replace(',', ''))
                except Exception:
                    pass
            elif tag == 'P':
                payee = value.strip()
            elif tag == 'M':
                memo = value.strip()

        if txn_date is None or amount is None:
            continue

        description = payee or memo or "Unknown"
        transactions.append(TransactionImport(
            description=description,
            amount=abs(amount),
            date=txn_date,
            type="credit" if amount > 0 else "debit",
            payee_raw=payee,
        ))

    return transactions


def parse_camt(content: bytes) -> list[TransactionImport]:
    """Parse CAMT.052/CAMT.053 (ISO 20022) XML file content and return transactions."""
    root = ET.fromstring(content)

    # Detect namespace dynamically
    ns_match = re.match(r'\{(.+?)\}', root.tag)
    ns = ns_match.group(1) if ns_match else ''
    nsmap = {'ns': ns} if ns else {}

    def find(element, path):
        """Find element with or without namespace."""
        if nsmap:
            parts = path.split('/')
            ns_path = '/'.join(f'ns:{p}' for p in parts)
            return element.find(ns_path, nsmap)
        return element.find(path)

    def findall(element, path):
        if nsmap:
            parts = path.split('/')
            ns_path = '/'.join(f'ns:{p}' for p in parts)
            return element.findall(ns_path, nsmap)
        return element.findall(path)

    def find_text(element, path):
        el = find(element, path)
        return el.text if el is not None else None

    transactions = []

    # Navigate: Document > BkToCstmrStmt > Stmt > Ntry (CAMT.053, end-of-day statement)
    # Fallback: Document > BkToCstmrAcctRpt > Rpt > Ntry (CAMT.052, intraday report —
    # same Ntry sub-schema, different root/container element). Several European banks,
    # including German Volksbanken/Raiffeisenbanken, only offer CAMT.052 exports.
    stmts = findall(root, 'BkToCstmrStmt/Stmt') or findall(root, 'BkToCstmrAcctRpt/Rpt')
    for stmt in stmts:
        for ntry in findall(stmt, 'Ntry'):
            # Entry status: BOOK (final) vs. PDNG/INFO (pending/informational).
            # CAMT.052 intraday reports commonly include PDNG entries for
            # transactions that haven't settled yet; the same transaction is
            # reported again as BOOK once it settles. Skip anything that
            # isn't BOOK to avoid importing it twice. Status is either a
            # plain code (<Sts>BOOK</Sts>) or wrapped (<Sts><Cd>BOOK</Cd></Sts>)
            # depending on the schema version. Check the wrapped form first:
            # in pretty-printed XML the <Sts> element's own text is the
            # whitespace before <Cd>, which would otherwise mask the real code.
            status = find_text(ntry, 'Sts/Cd') or find_text(ntry, 'Sts')
            if status and status.strip().upper() != 'BOOK':
                continue

            # Amount
            amt_el = find(ntry, 'Amt')
            if amt_el is None:
                continue
            try:
                amount = Decimal(amt_el.text)
            except Exception:
                continue

            # Credit/Debit indicator
            cdt_dbt = find_text(ntry, 'CdtDbtInd')
            txn_type = "credit" if cdt_dbt == "CRDT" else "debit"

            # Date: try BookgDt/Dt then ValDt/Dt
            date_str = find_text(ntry, 'BookgDt/Dt') or find_text(ntry, 'ValDt/Dt')
            if not date_str:
                continue
            try:
                txn_date = datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
            except ValueError:
                continue

            # Description from various paths
            description = (
                find_text(ntry, 'NtryDtls/TxDtls/RmtInf/Ustrd')
                or find_text(ntry, 'NtryDtls/TxDtls/RltdPties/Cdtr/Nm')
                or find_text(ntry, 'NtryDtls/TxDtls/RltdPties/Dbtr/Nm')
                or find_text(ntry, 'AddtlNtryInf')
                or "Unknown"
            )

            # Extract currency from Ccy attribute on Amt element
            txn_currency = amt_el.get('Ccy') or None

            transactions.append(TransactionImport(
                description=description,
                amount=abs(amount),
                date=txn_date,
                type=txn_type,
                currency=txn_currency,
            ))

    return transactions


DATE_FORMAT_MAP = {
    'DD/MM/YYYY': '%d/%m/%Y',
    'MM/DD/YYYY': '%m/%d/%Y',
    'YYYY-MM-DD': '%Y-%m-%d',
}

# Securo fields a CSV column can be mapped to. Used to validate the
# user-supplied column_mapping and to drive the import-UI dropdowns.
CSV_MAPPABLE_FIELDS = (
    'date', 'description', 'amount', 'type',
    'category', 'currency', 'fx_rate', 'inflow', 'outflow',
)


def _sniff_csv_dialect(text: str):
    """Detect the CSV dialect (delimiter/quoting), falling back to comma."""
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
    except csv.Error:
        return csv.excel


def detect_csv_columns(content: bytes) -> list[str]:
    """Return the CSV header column names exactly as they appear in the file.

    Used by the import preview so the UI can offer accurate column-mapping
    dropdowns instead of guessing headers client-side.
    """
    text = content.decode('utf-8-sig')  # Handle BOM
    dialect = _sniff_csv_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [f.strip() for f in (reader.fieldnames or []) if f and f.strip()]


def parse_csv(
    content: bytes,
    date_format: str | None = None,
    flip_amount: bool = False,
    inflow_column: str | None = None,
    outflow_column: str | None = None,
    column_mapping: dict[str, str] | None = None,
) -> list[TransactionImport]:
    """Parse CSV file content and return transactions.

    Attempts to detect common column formats:
    - date, description, amount
    - data, descricao, valor (Portuguese)

    Options:
    - date_format: explicit date format (DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD)
    - flip_amount: negate all parsed amounts
    - inflow_column/outflow_column: use split columns instead of single amount
    - column_mapping: explicit Securo-field -> CSV-header map. Any field
      present here overrides auto-detection; unmapped fields still auto-detect.
    """
    text = content.decode('utf-8-sig')  # Handle BOM
    dialect = _sniff_csv_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    # Normalize field names
    fieldnames = [f.lower().strip() for f in (reader.fieldnames or [])]

    # Map common column names
    date_cols = ['date', 'data', 'dt', 'transaction_date', 'data_transacao']
    desc_cols = ['description', 'descricao', 'desc', 'memo', 'historico', 'lancamento']
    amount_cols = ['amount', 'valor', 'value', 'quantia']
    type_cols = ['type', 'tipo']
    category_cols = ['category', 'categoria']
    currency_cols = ['currency', 'moeda', 'currency_code']
    fx_rate_cols = ['fx_rate', 'fx_rate_used', 'taxa_cambio', 'exchange_rate', 'taxa']

    # Normalize the user-supplied column mapping (Securo field -> CSV header).
    mapping = {
        field: value.lower().strip()
        for field, value in (column_mapping or {}).items()
        if field in CSV_MAPPABLE_FIELDS and value and value.strip()
    }

    def find_col(candidates):
        for c in candidates:
            if c in fieldnames:
                return c
        return None

    def resolve_col(field, candidates):
        """Resolve a CSV column for a Securo field.

        An explicit user mapping always wins; otherwise fall back to
        auto-detection against the known column-name candidates.
        """
        mapped = mapping.get(field)
        if mapped:
            if mapped not in fieldnames:
                raise ValueError(
                    f"Mapped column '{mapped}' for field '{field}' not found in CSV. "
                    f"Available columns: {', '.join(fieldnames)}"
                )
            return mapped
        return find_col(candidates)

    date_col = resolve_col('date', date_cols)
    desc_col = resolve_col('description', desc_cols)

    # In split mode, we don't require a single amount column. The inflow/outflow
    # columns may come from the explicit args or from the column mapping.
    inflow_col = (inflow_column or mapping.get('inflow') or '').lower().strip() or None
    outflow_col = (outflow_column or mapping.get('outflow') or '').lower().strip() or None
    use_split = bool(inflow_col and outflow_col)

    if use_split:
        if inflow_col not in fieldnames or outflow_col not in fieldnames:
            raise ValueError(f"Inflow/outflow columns not found in CSV. Available columns: {', '.join(fieldnames)}")
        amount_col = None
    else:
        amount_col = resolve_col('amount', amount_cols)

    type_col = resolve_col('type', type_cols)
    category_col = resolve_col('category', category_cols)
    currency_col = resolve_col('currency', currency_cols)
    fx_rate_col = resolve_col('fx_rate', fx_rate_cols)

    if not date_col or not desc_col:
        raise ValueError(
            f"Could not detect CSV columns. Found: {', '.join(fieldnames)}. "
            f"Expected columns like: date, description, amount (or Portuguese equivalents: data, descricao, valor)"
        )
    if not use_split and not amount_col:
        raise ValueError(
            f"Could not detect amount column. Found: {', '.join(fieldnames)}. "
            f"Expected a column named: {', '.join(amount_cols)}"
        )

    # Determine date formats to try
    if date_format and date_format in DATE_FORMAT_MAP:
        date_formats = [DATE_FORMAT_MAP[date_format]]
    else:
        date_formats = ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y']

    transactions = []
    for row in reader:
        # Normalize row keys
        row = {k.lower().strip(): v for k, v in row.items()}

        # Parse date
        date_str = row[date_col].strip()
        txn_date = None
        for fmt in date_formats:
            try:
                txn_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if not txn_date:
            continue  # Skip invalid dates

        # Parse amount
        if use_split:
            inflow_str = normalize_amount(row.get(inflow_col, ""))
            outflow_str = normalize_amount(row.get(outflow_col, ""))

            try:
                inflow = Decimal(inflow_str) if inflow_str else Decimal('0')
            except Exception:
                inflow = Decimal('0')
            try:
                outflow = Decimal(outflow_str) if outflow_str else Decimal('0')
            except Exception:
                outflow = Decimal('0')

            if inflow > 0:
                amount = inflow
                txn_type = "credit"
            elif outflow > 0:
                amount = outflow
                txn_type = "debit"
            else:
                continue  # Skip rows with no amount
        else:
            amount_str = normalize_amount(row[amount_col])

            try:
                amount = Decimal(amount_str)
            except Exception:
                continue  # Skip invalid amounts

            if flip_amount:
                amount = -amount

            if type_col and row.get(type_col, '').strip() in ('credit', 'debit'):
                txn_type = row[type_col].strip()
            else:
                txn_type = "credit" if amount > 0 else "debit"
            amount = abs(amount)

        # Extract optional category, currency and fx_rate from CSV columns
        category_name = row[category_col].strip() if category_col and row.get(category_col) else None
        txn_currency = None
        txn_fx_rate = None
        if currency_col and row.get(currency_col):
            txn_currency = row[currency_col].strip().upper() or None
        if fx_rate_col and row.get(fx_rate_col):
            fx_str = normalize_amount(row[fx_rate_col].strip())
            if fx_str:
                try:
                    txn_fx_rate = Decimal(fx_str)
                except Exception:
                    pass

        transactions.append(TransactionImport(
            description=row[desc_col].strip(),
            amount=abs(amount),
            date=txn_date,
            type=txn_type,
            currency=txn_currency,
            fx_rate=txn_fx_rate,
            category_name=category_name,
        ))

    return transactions


async def enrich_with_category_suggestions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[TransactionImport],
) -> list[TransactionImport]:
    result = await session.execute(
        select(Rule)
        .where(Rule.workspace_id == workspace_id, Rule.is_active == True)
        .order_by(Rule.priority, Rule.id)
    )
    rules = result.scalars().all()

    category_result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    category_name_map = {str(c.id): c.name for c in category_result.scalars()}

    if not rules:
        return transactions

    for txn in transactions:
        proxy = SimpleNamespace(
            description=txn.description,
            amount=txn.amount,
            date=txn.date,
            type=txn.type,
            account_id=None,
            payee_id=None,
            notes=None,
            category_id=None,
        )
        category_set = False
        for rule in rules:
            conditions = rule.conditions or []
            actions = rule.actions or []
            if evaluate_conditions(rule.conditions_op, conditions, proxy):
                category_set = apply_rule_actions(actions, proxy, category_set)
        if proxy.category_id:
            txn.suggested_category_id = proxy.category_id
            txn.suggested_category_name = category_name_map.get(str(proxy.category_id))

    return transactions


async def import_transactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    transactions: list[TransactionImport],
    source: str,
    filename: str = "",
    detected_format: str = "",
    detect_duplicates: bool = True,
) -> tuple[int, int, int, uuid.UUID]:
    """Import transactions into an account in the given workspace.

    `workspace_id` scopes tenant filters + stamps new rows. `user_id`
    is the creator/author recorded on Transaction + ImportLog.
    Returns (imported, skipped, excluded, import_log_id)."""
    from app.models.import_log import ImportLog

    included = [t for t in transactions if not t.excluded]
    excluded_count = len(transactions) - len(included)

    # Calculate summaries from included transactions only
    total_credit = sum(t.amount for t in included if t.type == "credit")
    total_debit = sum(t.amount for t in included if t.type == "debit")

    # Create import log first to get its ID
    import_log = ImportLog(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        filename=filename,
        format=detected_format,
        transaction_count=len(included),
        total_credit=total_credit,
        total_debit=total_debit,
    )
    session.add(import_log)
    await session.flush()  # Get the import_log.id

    # Look up account currency for fallback
    account_result = await session.execute(
        select(Account).where(Account.id == account_id)
    )
    account = account_result.scalar_one_or_none()
    account_currency = account.currency if account else get_settings().default_currency

    # Build category name → id map scoped to the workspace.
    category_result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    category_map = {c.name: c.id for c in category_result.scalars()}

    imported = 0
    skipped = 0
    effective_format = (detected_format or source or "").lower()
    should_detect_duplicates = detect_duplicates if effective_format == "csv" else True

    for txn_data in included:
        # Resolve currency: CSV value > account currency
        txn_currency = txn_data.currency or account_currency

        if should_detect_duplicates:
            # Duplicate detection: use external_id when available (OFX FITID),
            # fall back to field-based matching for formats without unique IDs.
            # When matching by external_id, also require the same `date` so that
            # Brazilian credit-card installments — where some banks reuse one
            # purchase FITID across every monthly statement — don't get skipped
            # as duplicates from later monthly imports (issue #98).
            if txn_data.external_id:
                existing = await session.execute(
                    select(Transaction).where(
                        Transaction.account_id == account_id,
                        Transaction.external_id == txn_data.external_id,
                        Transaction.date == txn_data.date,
                    )
                )
            else:
                existing = await session.execute(
                    select(Transaction).where(
                        Transaction.account_id == account_id,
                        Transaction.date == txn_data.date,
                        Transaction.amount == txn_data.amount,
                        Transaction.type == txn_data.type,
                        Transaction.description == txn_data.description,
                    )
                )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

        # Resolve payee entity from raw payee text (OFX/QIF)
        import_payee_id = None
        import_payee_raw = getattr(txn_data, "payee_raw", None)
        if import_payee_raw:
            import_payee_entity = await get_or_create_payee(session, user_id, import_payee_raw)
            import_payee_id = import_payee_entity.id

        # Recurring bill reconciliation (issue #116): if this imported charge
        # fulfills a generated placeholder, merge into it instead of creating a
        # duplicate; the recurring link is preserved.
        placeholder = await recurring_match_service.find_placeholder_for_incoming(
            session, account_id, txn_data.amount, txn_currency, txn_data.type,
            txn_data.date, txn_data.description,
        )
        if placeholder and not placeholder.is_ignored:
            placeholder.source = source
            placeholder.external_id = txn_data.external_id
            placeholder.import_id = import_log.id
            if import_payee_raw and not placeholder.payee:
                placeholder.payee = import_payee_raw
                placeholder.payee_id = import_payee_id
            imported += 1
            continue

        # Otherwise, link to an active bill's next occurrence if this fulfills it.
        recurring_link = await recurring_match_service.find_bill_for_incoming(
            session, user_id, account_id, txn_data.amount, txn_currency, txn_data.type,
            txn_data.date, txn_data.description,
        )

        user_category_id = txn_data.category_id
        suggested_cat_id = txn_data.suggested_category_id
        csv_category_id = category_map.get(txn_data.category_name) if txn_data.category_name else None
        if txn_data.force_uncategorized:
            category_id = None
        else:
            category_id = user_category_id or suggested_cat_id or csv_category_id

        transaction = Transaction(
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account_id,
            description=txn_data.description,
            amount=txn_data.amount,
            date=txn_data.date,
            type=txn_data.type,
            source=source,
            import_id=import_log.id,
            external_id=txn_data.external_id,
            currency=txn_currency,
            payee=import_payee_raw,
            payee_id=import_payee_id,
            category_id=category_id,
            recurring_transaction_id=recurring_link.id if recurring_link else None,
        )
        apply_effective_date(transaction, account)

        if txn_data.fx_rate:
            transaction.fx_rate_used = txn_data.fx_rate
            transaction.amount_primary = txn_data.amount * txn_data.fx_rate

        session.add(transaction)
        await session.flush()
        if recurring_link is not None:
            recurring_match_service.advance_past(recurring_link, txn_data.date)

        await apply_rules_to_transaction(session, user_id, transaction, skip_category_rules=txn_data.force_uncategorized)

        # Only auto-convert if no fx_rate was provided by the CSV
        if not txn_data.fx_rate:
            await stamp_primary_amount(session, user_id, transaction)

        imported += 1

    # Update import log with actual imported count
    import_log.transaction_count = imported

    await session.commit()
    return imported, skipped, excluded_count, import_log.id

def normalize_amount(amount_str: str) -> str:
    """
    Normalize monetary string into a standard decimal format compatible with Decimal.

    Example:
        1.442,20 -> 1442.20
        1,442.20 -> 1442.20
    """

    amount_str = amount_str.replace('R$', '').strip()

    if ',' in amount_str and '.' in amount_str:
        if amount_str.rfind(',') > amount_str.rfind('.'):
            amount_str = amount_str.replace('.', '').replace(',', '.')
        else:
            amount_str = amount_str.replace(',', '')
    elif ',' in amount_str:
        amount_str = amount_str.replace(',', '.')

    return amount_str