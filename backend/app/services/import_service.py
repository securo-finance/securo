import csv
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from ofxparse import OfxParser
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport, FailedRow
from app.services import recurring_match_service
from app.services.credit_card_service import apply_effective_date
from app.services.category_service import get_hidden_category_ids
from app.services.rule_engine import apply_rule_actions, evaluate_conditions, merge_notes
from app.services.rule_service import apply_rules_to_transaction, preview_rules_for_transaction
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


def _decode_ofx_bytes(content: bytes) -> tuple[str, str]:
    """Decode OFX content to text, trying UTF-8 then falling back to Latin-1.

    Returns (text, encoding) so callers can re-encode consistently later.
    """
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return content.decode("latin-1"), "latin-1"


def _patch_empty_fitids(text: str) -> str:
    """Synthesize a FITID for STMTTRN blocks that have an empty/missing one.

    Banco do Brasil (and a few other Brazilian banks) emit balance-summary
    rows as <STMTTRN> blocks with empty <FITID> tags, which makes ofxparse
    abort the entire import with "Empty FIT id (a required field)". We patch
    each affected block with a deterministic synthetic FITID so parsing
    succeeds; balance rows are filtered out later by description.
    """
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

    return re.sub(
        r"<STMTTRN>.*?</STMTTRN>",
        _replace,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _ensure_ofx_sgml_header(text: str, encoding: str) -> str:
    """Prepend a legacy OFX 1.x SGML header for OFX 2.x files that omit it.

    OFX 2.x is plain XML that goes straight into its first tag, with no
    colon-delimited SGML header block (e.g. Erste Bank's "MS Money Sunset
    Deluxe" export). ofxparse only looks for encoding hints in the bytes
    preceding the file's first "<"; when that's empty it silently assumes
    ASCII and crashes on any non-ASCII byte. Prepending a synthetic SGML
    header routes the file through ofxparse's existing, correctly-working
    SGML decode path instead of its broken auto-detection (see
    https://github.com/jseutter/ofxparse/issues/133).

    The trigger is therefore "nothing precedes the first tag", not "starts
    with <?xml": the XML declaration is optional in XML 1.0, so an OFX 2.x
    file may open with just its <?OFX ... ?> instruction, or with <OFX>
    itself, and those hit the same ofxparse bug. Anything else already has a
    legacy header, and a file with no tag at all is left for ofxparse to
    reject on its own terms.

    `encoding` must match whatever the caller will re-encode `text` with,
    so the declared header and the actual bytes stay consistent.
    """
    preamble, first_tag, _ = text.lstrip("\ufeff \t\r\n").partition("<")
    if not first_tag or preamble.strip():
        return text
    if encoding == "latin-1":
        enc_lines = "ENCODING:USASCII\r\nCHARSET:8859-1\r\n"
    else:
        enc_lines = "ENCODING:UTF-8\r\nCHARSET:NONE\r\n"
    header = (
        f"OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\n"
        f"{enc_lines}COMPRESSION:NONE\r\nOLDFILEUID:NONE\r\nNEWFILEUID:NONE\r\n\r\n"
    )
    return header + text


def _preprocess_ofx(content: bytes) -> bytes:
    """Apply text-level fixups ofxparse needs before it can parse the file."""
    text, encoding = _decode_ofx_bytes(content)
    text = _patch_empty_fitids(text)
    text = _ensure_ofx_sgml_header(text, encoding)
    return text.encode(encoding, errors="replace")


def _is_balance_summary_row(description: str | None) -> bool:
    if not description:
        return False
    normalized = description.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _OFX_BALANCE_ROW_DESCRIPTIONS)


def parse_ofx(content: bytes) -> list[TransactionImport]:
    """Parse OFX file content and return transactions."""
    content = _preprocess_ofx(content)
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


# QIF "D" lines carry no format metadata; US-first order is the historical
# default. The Quicken apostrophe variants are always month-first.
_QIF_FALLBACK_DATE_FORMATS = [
    '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d',
    "%m/%d'%Y", "%m/%d'%y",
    '%m/%d/%y', '%d/%m/%y',
]


def _qif_date_formats(date_format: str | None, raw_dates: list[str]) -> list[str]:
    """Decide the strptime formats for a QIF file's dates, once per file.

    An explicit user choice is strict (like parse_csv): only that format and
    its 2-digit-year variant are accepted. Otherwise the order is inferred
    from the whole file — a first component > 12 can only be a day, so the
    file is DD/MM; per-line first-match parsing would silently mix MM/DD and
    DD/MM within a single import for the ambiguous days 1-12.
    """
    if date_format and date_format in DATE_FORMAT_MAP:
        fmt = DATE_FORMAT_MAP[date_format]
        return [fmt, fmt.replace('%Y', '%y')]

    saw_day_first = saw_month_first = False
    for value in raw_dates:
        match = re.match(r"^(\d{1,2})/(\d{1,2})/\d{2,4}$", value)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 >= second:
            saw_day_first = True
        if second > 12 >= first:
            saw_month_first = True

    if saw_day_first and not saw_month_first:
        return ['%d/%m/%Y', '%d/%m/%y'] + _QIF_FALLBACK_DATE_FORMATS
    return list(_QIF_FALLBACK_DATE_FORMATS)


def parse_qif(content: bytes, date_format: str | None = None) -> list[TransactionImport]:
    """Parse QIF file content and return transactions.

    date_format: optional explicit format (see DATE_FORMAT_MAP keys); when
    omitted, the day/month order is inferred from the whole file.
    """
    # Try UTF-8 first, fall back to Latin-1 for legacy software (e.g. Microsoft Money)
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')
    transactions = []

    # Split into transaction blocks by "^"
    blocks = text.split('^')

    raw_dates = [
        stripped[1:].strip()
        for block in blocks
        for stripped in (line.strip() for line in block.strip().splitlines())
        if stripped.startswith('D')
    ]
    date_formats = _qif_date_formats(date_format, raw_dates)

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
                for fmt in date_formats:
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
    'payee', 'external_id', 'notes',
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
    ) -> tuple[list[TransactionImport], list[FailedRow]]:
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
    fieldnames = [f.lower().strip() if f is not None else "" for f in (reader.fieldnames or [])]

    # Map common column names
    date_cols = ['date', 'data', 'dt', 'transaction_date', 'data_transacao']
    desc_cols = ['description', 'descricao', 'desc', 'memo', 'historico', 'lancamento']
    amount_cols = ['amount', 'valor', 'value', 'quantia']
    type_cols = ['type', 'tipo']
    category_cols = ['category', 'categoria']
    currency_cols = ['currency', 'moeda', 'currency_code']
    fx_rate_cols = ['fx_rate', 'fx_rate_used', 'taxa_cambio', 'exchange_rate', 'taxa']
    payee_cols = ['payee', 'merchant', 'beneficiary', 'beneficiario', 'pagador']
    external_id_cols = [] # External ID must be mapped explicitly
    notes_cols = ['notes', 'nota', 'observacao']

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
    payee_col = resolve_col('payee', payee_cols)
    external_id_col = resolve_col('external_id', external_id_cols)
    notes_col = resolve_col('notes', notes_cols)

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
        date_formats = ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d.%m.%Y']

    transactions = []
    failed_rows = []
    for row in reader:
        # Normalize row keys, and missing cells along with them. A row with
        # fewer cells than the header leaves the rest as None, which every
        # .strip() below raises on and which FailedRow.raw_value rejects. That
        # turned a single short row into a 400 for the whole file, which is
        # exactly the case this parser is meant to report row by row. An
        # absent cell reads as an empty one.
        row = {
            k.lower().strip() if k is not None else "": ("" if v is None else v)
            for k, v in row.items()
        }

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
            failed_rows.append(FailedRow(line_number=reader.line_num, description=row.get(desc_col, "").strip(), raw_value=date_str, error_reason="invalid_date"))
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
                raw_val = f"inflow: {row.get(inflow_col, '')}, outflow: {row.get(outflow_col, '')}"
                failed_rows.append(FailedRow(line_number=reader.line_num, description=row.get(desc_col, "").strip(), raw_value=raw_val, error_reason="no_amount"))
                continue  # Skip rows with no amount
        else:
            amount_str = normalize_amount(row[amount_col])

            try:
                amount = Decimal(amount_str)
            except Exception:
                failed_rows.append(FailedRow(line_number=reader.line_num, description=row.get(desc_col, "").strip(), raw_value=row[amount_col], error_reason="invalid_amount"))
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

        txn_payee = row[payee_col].strip() if payee_col and row.get(payee_col) else None
        txn_external_id = row[external_id_col].strip() if external_id_col and row.get(external_id_col) else None
        txn_notes = row[notes_col].strip() if notes_col and row.get(notes_col) else None

        transactions.append(TransactionImport(
            description=row[desc_col].strip(),
            amount=abs(amount),
            date=txn_date,
            type=txn_type,
            currency=txn_currency,
            fx_rate=txn_fx_rate,
            category_name=category_name,
            payee_raw=txn_payee,
            external_id=txn_external_id,
            notes=txn_notes,
        ))

    return transactions, failed_rows


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
    categories = category_result.scalars().all()
    hidden_categories = await get_hidden_category_ids(session, workspace_id)
    category_name_map = {str(c.id): c.name for c in categories}
    category_name_to_id = {
        c.name.strip().lower(): c.id 
        for c in categories 
        if c.id not in hidden_categories
    }

    if not rules and not category_name_to_id:
        return transactions

    for txn in transactions:
        proxy: Transaction = Transaction(
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
                category_set = apply_rule_actions(
                    actions,
                    proxy,
                    category_set,
                    hidden_category_ids=hidden_categories,
                )
        
        # If rules did not set a category, apply the CSV category if found
        if not category_set and txn.category_name:
            csv_cat_id = category_name_to_id.get(txn.category_name.strip().lower())
            if csv_cat_id:
                proxy.category_id = csv_cat_id
                category_set = True
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

    # Build category name → id map scoped to the workspace, on the same terms
    # the preview used: hidden categories excluded, names matched
    # case-insensitively. Diverging here meant a CSV category the preview
    # deliberately withheld was still persisted on the imported row, and that
    # "Food" matched in the preview but not on import.
    category_result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    hidden_categories = await get_hidden_category_ids(session, workspace_id)
    category_map = {
        c.name.strip().lower(): c.id
        for c in category_result.scalars()
        if c.id not in hidden_categories
    }

    imported = 0
    skipped = 0
    effective_format = (detected_format or source or "").lower()
    should_detect_duplicates = detect_duplicates if effective_format == "csv" else True

    for txn_data in included:
        # Resolve currency: CSV value > account currency
        txn_currency = txn_data.currency or account_currency

        if should_detect_duplicates:
            # Prefer an external ID (OFX FITID), with date retained because some
            # Brazilian cards reuse one purchase FITID across monthly installments.
            # Formats without unique IDs fall back to transaction fields; compare
            # both descriptions because rules may have changed the displayed one.
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
                        or_(
                            Transaction.description == txn_data.description,
                            Transaction.original_description == txn_data.description,
                        ),
                    )
                )
            # `.first()` is intentional: duplicate keys can legitimately match
            # multiple rows after an import/sync race or reused bank identifier,
            # and duplicate detection only needs to establish that any row exists.
            if existing.scalars().first() is not None:
                skipped += 1
                continue

        import_payee_id = None
        import_payee_raw = getattr(txn_data, "payee_raw", None)
        if import_payee_raw:
            import_payee_entity = await get_or_create_payee(
                session, user_id, import_payee_raw, workspace_id=workspace_id,
                source="import",
            )
            import_payee_id = import_payee_entity.id

        user_category_id = txn_data.category_id
        suggested_cat_id = txn_data.suggested_category_id
        csv_category_id = (
            category_map.get(txn_data.category_name.strip().lower())
            if txn_data.category_name
            else None
        )
        category_id = (
            None
            if txn_data.force_uncategorized
            else user_category_id or suggested_cat_id or csv_category_id
        )

        incoming = Transaction(
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account_id,
            description=txn_data.description,
            original_description=txn_data.description,
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
            notes=getattr(txn_data, "notes", None),
        )
        apply_effective_date(incoming, account)
        preview = await preview_rules_for_transaction(
            session,
            user_id,
            incoming,
            skip_category_rules=txn_data.force_uncategorized,
        )

        # Normalize a detached candidate before either recurring match. If a
        # generated placeholder already represents this occurrence, upgrade it
        # in place; otherwise link the new row to an active recurring definition.
        placeholder = await recurring_match_service.find_placeholder_for_incoming(
            session,
            account_id,
            txn_data.amount,
            txn_currency,
            txn_data.type,
            txn_data.date,
            preview.description,
        )
        if placeholder and not placeholder.is_ignored:
            placeholder.source = source
            placeholder.external_id = txn_data.external_id
            placeholder.import_id = import_log.id
            placeholder.status = "posted"
            # The rules already ran, against the incoming charge, to build
            # `preview`. Fold that result in rather than re-running them
            # against the placeholder: its description is the recurring
            # definition's own wording, so conditions written for the bank's
            # text would no longer match. Everything the user can already see
            # wins, the charge only fills what is still empty, and only its
            # provenance is recorded outright.
            placeholder.original_description = txn_data.description
            if placeholder.category_id is None:
                placeholder.category_id = preview.category_id
            if import_payee_raw and not placeholder.payee:
                placeholder.payee = import_payee_raw
            if placeholder.payee_id is None:
                placeholder.payee_id = preview.payee_id
            placeholder.notes = merge_notes(placeholder.notes, preview.notes)
            if preview.is_ignored:
                placeholder.is_ignored = True
            imported += 1
            continue

        recurring_link = await recurring_match_service.find_bill_for_incoming(
            session,
            user_id,
            account_id,
            txn_data.amount,
            txn_currency,
            txn_data.type,
            txn_data.date,
            preview.description,
        )
        incoming.recurring_transaction_id = (
            recurring_link.id if recurring_link else None
        )
        if txn_data.fx_rate:
            incoming.fx_rate_used = txn_data.fx_rate
            incoming.amount_primary = txn_data.amount * txn_data.fx_rate

        session.add(incoming)
        await session.flush()
        if recurring_link is not None:
            recurring_match_service.advance_past(recurring_link, txn_data.date)

        await apply_rules_to_transaction(
            session,
            user_id,
            incoming,
            skip_category_rules=txn_data.force_uncategorized,
        )

        if not txn_data.fx_rate:
            await stamp_primary_amount(session, user_id, incoming)

        imported += 1

    # Update import log with actual imported count
    import_log.transaction_count = imported

    await session.commit()
    return imported, skipped, excluded_count, import_log.id

def normalize_amount(amount_str: str | None) -> str:
    """
    Normalize monetary string into a standard decimal format compatible with Decimal.

    Example:
        1.442,20 -> 1442.20
        1,442.20 -> 1442.20
    """
    if not amount_str:
        return ""

    # Strip currency prefix and Swiss thousands separators (single quote)
    amount_str = str(amount_str).replace('R$', '').replace("'", "").strip()

    if ',' in amount_str and '.' in amount_str:
        if amount_str.rfind(',') > amount_str.rfind('.'):
            amount_str = amount_str.replace('.', '').replace(',', '.')
        else:
            amount_str = amount_str.replace(',', '')
    elif ',' in amount_str:
        amount_str = amount_str.replace(',', '.')

    return amount_str
