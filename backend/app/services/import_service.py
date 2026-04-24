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

from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionBase, TransactionImport
from app.services.credit_card_service import apply_effective_date
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
    """Parse CAMT.053 (ISO 20022) XML file content and return transactions."""
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

    # Navigate: Document > BkToCstmrStmt > Stmt > Ntry
    for stmt in findall(root, 'BkToCstmrStmt/Stmt'):
        for ntry in findall(stmt, 'Ntry'):
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


def parse_csv(
    content: bytes,
    date_format: str | None = None,
    flip_amount: bool = False,
    inflow_column: str | None = None,
    outflow_column: str | None = None,
) -> list[TransactionImport]:
    """Parse CSV file content and return transactions.

    Attempts to detect common column formats:
    - date, description, amount
    - data, descricao, valor (Portuguese)

    Options:
    - date_format: explicit date format (DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD)
    - flip_amount: negate all parsed amounts
    - inflow_column/outflow_column: use split columns instead of single amount
    """
    text = content.decode('utf-8-sig')  # Handle BOM
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel  # fallback to comma
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

    def find_col(candidates):
        for c in candidates:
            if c in fieldnames:
                return c
        return None

    date_col = find_col(date_cols)
    desc_col = find_col(desc_cols)

    # In split mode, we don't require a single amount column
    use_split = inflow_column and outflow_column
    inflow_col = inflow_column.lower().strip() if inflow_column else None
    outflow_col = outflow_column.lower().strip() if outflow_column else None

    if use_split:
        if inflow_col not in fieldnames or outflow_col not in fieldnames:
            raise ValueError(f"Inflow/outflow columns not found in CSV. Available columns: {', '.join(fieldnames)}")
        amount_col = None
    else:
        amount_col = find_col(amount_cols)

    type_col = find_col(type_cols)
    category_col = find_col(category_cols)
    currency_col = find_col(currency_cols)
    fx_rate_col = find_col(fx_rate_cols)

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


async def import_transactions(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    transactions: list[TransactionBase],
    source: str,
    filename: str = "",
    detected_format: str = "",
) -> tuple[int, int, uuid.UUID]:
    """Import transactions into an account. Returns (imported, skipped, import_log_id)."""
    from app.models.import_log import ImportLog

    # Calculate summaries
    total_credit = sum(t.amount for t in transactions if t.type == "credit")
    total_debit = sum(t.amount for t in transactions if t.type == "debit")

    # Create import log first to get its ID
    import_log = ImportLog(
        user_id=user_id,
        account_id=account_id,
        filename=filename,
        format=detected_format,
        transaction_count=len(transactions),
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

    # Build category name → id map for this user (used when CSV provides category names)
    category_result = await session.execute(
        select(Category).where(Category.user_id == user_id)
    )
    category_map = {c.name: c.id for c in category_result.scalars()}

    imported = 0
    skipped = 0
    for txn_data in transactions:
        # Resolve currency: CSV value > account currency
        txn_currency = txn_data.currency or account_currency

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

        category_id = category_map.get(getattr(txn_data, "category_name", None)) if getattr(txn_data, "category_name", None) else None

        transaction = Transaction(
            user_id=user_id,
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
        )
        apply_effective_date(transaction, account)

        # If CSV provided an fx_rate, use it directly
        if txn_data.fx_rate:
            transaction.fx_rate_used = txn_data.fx_rate
            transaction.amount_primary = txn_data.amount * txn_data.fx_rate

        session.add(transaction)
        await session.flush()
        await apply_rules_to_transaction(session, user_id, transaction)

        # Only auto-convert if no fx_rate was provided by the CSV
        if not txn_data.fx_rate:
            await stamp_primary_amount(session, user_id, transaction)

        imported += 1

    # Update import log with actual imported count
    import_log.transaction_count = imported

    await session.commit()
    return imported, skipped, import_log.id

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