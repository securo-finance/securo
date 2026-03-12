import csv
import io
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from ofxparse import OfxParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionBase
from app.services.rule_service import apply_rules_to_transaction


def parse_ofx(content: bytes) -> list[TransactionBase]:
    """Parse OFX file content and return transactions."""
    ofx = OfxParser.parse(io.BytesIO(content))
    transactions = []

    for account in ofx.accounts:
        for txn in account.statement.transactions:
            transactions.append(TransactionBase(
                description=txn.memo or txn.payee or "Unknown",
                amount=abs(Decimal(str(txn.amount))),
                date=txn.date.date() if hasattr(txn.date, 'date') else txn.date,
                type="credit" if txn.amount > 0 else "debit",
                external_id=getattr(txn, 'id', None),
            ))

    return transactions


def parse_qif(content: bytes) -> list[TransactionBase]:
    """Parse QIF file content and return transactions."""
    text = content.decode('utf-8-sig')
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
                # Try common date formats
                for fmt in ['%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d', "%m/%d'%Y"]:
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
        transactions.append(TransactionBase(
            description=description,
            amount=abs(amount),
            date=txn_date,
            type="credit" if amount > 0 else "debit",
        ))

    return transactions


def parse_camt(content: bytes) -> list[TransactionBase]:
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

            transactions.append(TransactionBase(
                description=description,
                amount=abs(amount),
                date=txn_date,
                type=txn_type,
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
) -> list[TransactionBase]:
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
    reader = csv.DictReader(io.StringIO(text))

    # Normalize field names
    fieldnames = [f.lower().strip() for f in (reader.fieldnames or [])]

    # Map common column names
    date_cols = ['date', 'data', 'dt', 'transaction_date', 'data_transacao']
    desc_cols = ['description', 'descricao', 'desc', 'memo', 'historico', 'lancamento']
    amount_cols = ['amount', 'valor', 'value', 'quantia']

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
            raise ValueError(f"Inflow/outflow columns not found in CSV. Available: {fieldnames}")
        amount_col = None
    else:
        amount_col = find_col(amount_cols)

    if not date_col or not desc_col:
        raise ValueError("Could not detect CSV columns. Expected: date, description, amount")
    if not use_split and not amount_col:
        raise ValueError("Could not detect CSV columns. Expected: date, description, amount")

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
            inflow_str = row.get(inflow_col, '').strip().replace(',', '.').replace('R$', '').strip()
            outflow_str = row.get(outflow_col, '').strip().replace(',', '.').replace('R$', '').strip()
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
            amount_str = row[amount_col].strip().replace(',', '.').replace('R$', '').strip()
            try:
                amount = Decimal(amount_str)
            except Exception:
                continue  # Skip invalid amounts

            if flip_amount:
                amount = -amount

            txn_type = "credit" if amount > 0 else "debit"
            amount = abs(amount)

        transactions.append(TransactionBase(
            description=row[desc_col].strip(),
            amount=abs(amount),
            date=txn_date,
            type=txn_type,
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

    imported = 0
    skipped = 0
    for txn_data in transactions:
        # Duplicate detection: use external_id when available (OFX FITID),
        # fall back to field-based matching for formats without unique IDs
        if txn_data.external_id:
            existing = await session.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.external_id == txn_data.external_id,
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
        )
        session.add(transaction)
        await session.flush()
        await apply_rules_to_transaction(session, user_id, transaction)
        imported += 1

    # Update import log with actual imported count
    import_log.transaction_count = imported

    await session.commit()
    return imported, skipped, import_log.id
