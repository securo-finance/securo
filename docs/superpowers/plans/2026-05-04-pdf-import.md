# PDF Import — PicPay & C6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PDF import for PicPay bank statements and C6 credit card bills, with C6 password decryption and per-card tabbed preview.

**Architecture:** New parsers `parse_picpay_pdf` / `parse_c6_pdf` added to `import_service.py`. Preview endpoint extended with optional `password` form field and a new `cards` response field for C6 multi-card grouping. Frontend `import.tsx` gains a password input and tabbed preview; per-card imports fire individual POST requests reusing the existing single-account import endpoint.

**Tech Stack:** pikepdf (C6 decryption), pdfminer.six (text extraction), existing FastAPI/SQLAlchemy/React/react-query stack.

**Spec:** `docs/superpowers/specs/2026-05-04-pdf-import-design.md`

---

## File Map

| File | Role |
|---|---|
| `backend/pyproject.toml` | Add `pikepdf`, `pdfminer.six` deps |
| `backend/app/schemas/transaction.py` | Add `raw_data`, `installment_number`, `total_installments` to `TransactionImport`; add `C6CardPreview`; extend `TransactionImportPreview` |
| `backend/app/services/import_service.py` | Add `_extract_pdf_text`, `_extract_pdf_text_encrypted`, `_parse_picpay_text`, `parse_picpay_pdf`, `_parse_c6_text`, `parse_c6_pdf`, `detect_pdf_institution`; update `import_transactions` to map new fields |
| `backend/app/api/import_transactions.py` | Add `password` form param; route PDF by extension; build C6 grouped response |
| `backend/tests/test_import_service.py` | Tests for PicPay and C6 text parsers |
| `frontend/src/types/index.ts` | Add `ImportPreviewCard`, `ImportPreview` types |
| `frontend/src/lib/api.ts` | Update `previewImport` signature (add `password`); update return type |
| `frontend/src/pages/import.tsx` | Password state + field; PDF detection; C6 tabbed preview; per-card import |

---

## Task 1: Add Python Dependencies

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add deps to pyproject.toml**

In `backend/pyproject.toml`, inside the `dependencies` list, add after `"ofxparse>=0.21"`:

```toml
    "pikepdf>=9.0",
    "pdfminer.six>=20221105",
```

- [ ] **Step 2: Install**

```bash
cd backend && pip install pikepdf "pdfminer.six>=20221105"
```

Expected: both packages install without errors.

- [ ] **Step 3: Verify import**

```bash
python -c "import pikepdf; from pdfminer.high_level import extract_text_to_fp; print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore(deps): add pikepdf and pdfminer.six for PDF import"
```

---

## Task 2: Extend Schemas

**Files:**
- Modify: `backend/app/schemas/transaction.py`

- [ ] **Step 1: Add fields to `TransactionImport` and new preview schemas**

Replace the existing `TransactionImport`, `TransactionImportPreview`, and `TransactionImportRequest` classes (currently at lines ~129–144) with:

```python
class TransactionImport(TransactionBase):
    """TransactionBase extended with import-only fields not exposed in read responses."""
    category_name: Optional[str] = None
    raw_data: Optional[dict] = None
    installment_number: Optional[int] = None
    total_installments: Optional[int] = None


class C6CardPreview(BaseModel):
    card_last4: str
    cardholder: str
    transactions: list[TransactionImport]


class TransactionImportPreview(BaseModel):
    transactions: list[TransactionImport]
    detected_format: str
    institution: Optional[str] = None   # "picpay" | "c6" | None for other formats
    cards: Optional[list[C6CardPreview]] = None  # populated only for C6


class TransactionImportRequest(BaseModel):
    account_id: uuid.UUID
    transactions: list[TransactionImport]
    filename: str = ""
    detected_format: str = ""
    detect_duplicates: bool = True
```

- [ ] **Step 2: Verify schema imports still work**

```bash
cd backend && python -c "from app.schemas.transaction import TransactionImport, TransactionImportPreview, C6CardPreview; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/transaction.py
git commit -m "feat(schemas): add C6CardPreview, extend TransactionImport with raw_data and installment fields"
```

---

## Task 3: Update `import_transactions` Service to Map New Fields

**Files:**
- Modify: `backend/app/services/import_service.py` (around line 521 — Transaction creation block)

- [ ] **Step 1: Pass raw_data and installment fields when creating Transaction**

Locate the `Transaction(...)` constructor call in `import_transactions` (around line 521). Replace it with:

```python
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
            raw_data=getattr(txn_data, 'raw_data', None),
            installment_number=getattr(txn_data, 'installment_number', None),
            total_installments=getattr(txn_data, 'total_installments', None),
        )
```

- [ ] **Step 2: Run existing import tests to verify no regression**

```bash
cd backend && pytest tests/test_import_service.py tests/test_import_api.py -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/import_service.py
git commit -m "feat(import): pass raw_data and installment fields through to Transaction"
```

---

## Task 4: PicPay PDF Parser

**Files:**
- Modify: `backend/app/services/import_service.py` (add new functions before `import_transactions`)
- Modify: `backend/tests/test_import_service.py` (add `TestParsePicpayPdf` class)

Portuguese month names needed by the parser (add near the top of `import_service.py`, after the existing imports):

```python
_PT_MONTH_NAMES = {
    'janeiro': 1, 'fevereiro': 2, 'março': 3, 'abril': 4, 'maio': 5,
    'junho': 6, 'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10,
    'novembro': 11, 'dezembro': 12,
}

_PICPAY_DEBIT_TYPES = {
    'pix enviado', 'compra realizada', 'pagamento realizado', 'dinheiro guardado',
}
_PICPAY_CREDIT_TYPES = {
    'pix recebido', 'transferência recebida', 'dinheiro resgatado',
}
```

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_import_service.py`:

```python
from app.services.import_service import _parse_picpay_text


class TestParsePicpayPdf:
    """Tests for PicPay PDF text parser."""

    SAMPLE_TEXT = """
Francisco Leandro Nascimento De Assis
CPF: 023.648.383-80 Agência: 0001 Conta: 66194351-8
30 de abril 2026 Saldo ao final do dia: R$ 301,07
Hora Tipo Origem / Destino Forma de pagamento Valor
13:29 Pix enviado −R$ 720,00 Francisco Leandro
Nascimento De Assis Com saldo
28 de abril 2026 Saldo ao final do dia: R$ 1.021,07
Hora Tipo Origem / Destino Forma de pagamento Valor
13:28 Compra realizada Rede Tetra Fortaleza Bra Com saldo −R$ 100,00
20 de abril 2026 Saldo ao final do dia: R$ 1.204,07
Hora Tipo Origem / Destino Forma de pagamento Valor
05:31 Transferência recebida Conta Salário +R$ 1.451,61
09:53 Dinheiro guardado −R$ 370,00 No cofrinho Só guardar
dinheiro Com saldo
"""

    def test_parse_transactions_count(self):
        txns = _parse_picpay_text(self.SAMPLE_TEXT)
        assert len(txns) == 4

    def test_debit_pix(self):
        txns = _parse_picpay_text(self.SAMPLE_TEXT)
        pix = next(t for t in txns if 'Pix enviado' in t.description)
        assert pix.amount == Decimal('720.00')
        assert pix.type == 'debit'
        assert pix.date == date(2026, 4, 30)
        assert pix.raw_data == {'institution': 'picpay'}

    def test_debit_purchase(self):
        txns = _parse_picpay_text(self.SAMPLE_TEXT)
        compra = next(t for t in txns if 'Compra realizada' in t.description)
        assert compra.amount == Decimal('100.00')
        assert compra.type == 'debit'
        assert compra.date == date(2026, 4, 28)

    def test_credit_transfer(self):
        txns = _parse_picpay_text(self.SAMPLE_TEXT)
        credit = next(t for t in txns if 'Transferência recebida' in t.description)
        assert credit.amount == Decimal('1451.61')
        assert credit.type == 'credit'
        assert credit.date == date(2026, 4, 20)

    def test_cofrinho_debit(self):
        txns = _parse_picpay_text(self.SAMPLE_TEXT)
        cofrinho = next(t for t in txns if 'Dinheiro guardado' in t.description)
        assert cofrinho.amount == Decimal('370.00')
        assert cofrinho.type == 'debit'
        assert cofrinho.payee_raw == 'Cofrinho PicPay'
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && pytest tests/test_import_service.py::TestParsePicpayPdf -v
```

Expected: `ImportError` or `AttributeError` — `_parse_picpay_text` not found.

- [ ] **Step 3: Implement `_parse_picpay_text` and `parse_picpay_pdf`**

Add to `backend/app/services/import_service.py` (before `import_transactions`):

```python
def _parse_picpay_text(text: str) -> list[TransactionImport]:
    """Parse PicPay statement text (already extracted from PDF) into transactions."""
    transactions = []
    current_date = None
    lines = text.splitlines()

    day_re = re.compile(r'\b(\d{1,2})\s+de\s+(\w+)\s+(\d{4})\b', re.IGNORECASE)
    time_re = re.compile(r'^(\d{2}:\d{2})\s+(.+)')
    amount_re = re.compile(r'([−\-\+]?)R\$\s*([\d.]+,\d{2})')
    cofrinho_keywords = ('cofrinho', 'só guardar', 'aqua terra', 'cashback', 'turbinado')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        day_match = day_re.search(line)
        if day_match and 'Saldo ao final' in line:
            day = int(day_match.group(1))
            month_str = day_match.group(2).lower()
            year = int(day_match.group(3))
            month = _PT_MONTH_NAMES.get(month_str)
            if month:
                current_date = date(year, month, day)
            i += 1
            continue

        time_match = time_re.match(line)
        if time_match and current_date:
            # Collect all lines belonging to this transaction (until next time or day)
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if (time_re.match(next_line) or
                        day_re.search(next_line) or
                        next_line.startswith('Hora') or
                        next_line.startswith('Documento emitido')):
                    break
                if next_line:
                    block_lines.append(next_line)
                j += 1

            block = ' '.join(block_lines)

            # Extract amount and sign
            amt_match = amount_re.search(block)
            if not amt_match:
                i = j
                continue

            sign_char = amt_match.group(1)
            amount_str = amt_match.group(2)
            amount = Decimal(normalize_amount(amount_str))
            is_credit = sign_char == '+'

            # Detect transaction type from known tipo list
            block_lower = block.lower()
            tipo = None
            for known in list(_PICPAY_DEBIT_TYPES) + list(_PICPAY_CREDIT_TYPES):
                if known in block_lower:
                    tipo = known
                    break

            if tipo is None:
                i = j
                continue

            if tipo in _PICPAY_CREDIT_TYPES:
                txn_type = 'credit'
            else:
                txn_type = 'debit'

            if is_credit and txn_type == 'debit':
                txn_type = 'credit'

            # Payee: check for cofrinho
            is_cofrinho = any(k in block_lower for k in cofrinho_keywords)
            payee_raw = 'Cofrinho PicPay' if is_cofrinho else None

            # Description = tipo (title-cased)
            description = tipo.title()

            transactions.append(TransactionImport(
                description=description,
                amount=amount,
                date=current_date,
                type=txn_type,
                currency='BRL',
                payee_raw=payee_raw,
                raw_data={'institution': 'picpay'},
            ))
            i = j
            continue

        i += 1

    return transactions


def parse_picpay_pdf(file_bytes: bytes) -> list[TransactionImport]:
    """Parse PicPay statement PDF. Raises ValueError on parse failure."""
    text = _extract_pdf_text(file_bytes)
    return _parse_picpay_text(text)
```

Also add the PDF text extraction helper before `parse_picpay_pdf`:

```python
def _extract_pdf_text(file_bytes: bytes) -> str:
    """Extract plain text from an unencrypted PDF using pdfminer."""
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    import io as _io
    out = _io.StringIO()
    extract_text_to_fp(_io.BytesIO(file_bytes), out, laparams=LAParams())
    return out.getvalue()


def _extract_pdf_text_encrypted(file_bytes: bytes, password: str) -> str:
    """Decrypt PDF with pikepdf and extract text. Raises ValueError on wrong password."""
    import pikepdf
    import tempfile, os, io as _io
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    try:
        pdf = pikepdf.open(_io.BytesIO(file_bytes), password=password)
    except pikepdf.PasswordError:
        raise ValueError('invalid_password')
    buf = _io.BytesIO()
    pdf.save(buf)
    pdf.close()
    buf.seek(0)
    out = _io.StringIO()
    extract_text_to_fp(buf, out, laparams=LAParams())
    return out.getvalue()
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/test_import_service.py::TestParsePicpayPdf -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/import_service.py backend/tests/test_import_service.py
git commit -m "feat(import): add PicPay PDF parser with tests"
```

---

## Task 5: C6 PDF Parser

**Files:**
- Modify: `backend/app/services/import_service.py`
- Modify: `backend/tests/test_import_service.py`

Add Portuguese month abbreviations near `_PT_MONTH_NAMES`:

```python
_PT_MONTH_ABBR = {
    'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12,
}
```

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_import_service.py`:

```python
from app.services.import_service import _parse_c6_text


class TestParseC6Pdf:
    """Tests for C6 credit card PDF text parser."""

    SAMPLE_TEXT = """
Vencimento: 10 de Maio
Valor da fatura: R$ 4.272,65
Cartão C6 Carbon
Compras e pagamentos feitos até o fechamento desta fatura em 29/04/26.

C6 Carbon Final 3079 - LEANDRO NASCIMENTO

Subtotal deste cartão R$ 1.598,08

         03 abr QUINTAL DA MASSA

         04 abr BOMBOCINE MARANGUAPE

         06 abr

      Inclusao de Pagamento

         11 abr DISTRIBUIDORA DE ALIME

         12 mar CARAJAS CONSTRUCOES - Parcela 2/5

Valores em reais

63,50

75,00

6.330,60

41,87

146,18

C6 Carbon Virtual Final 8084 - LEANDRO

Cartão Virtual

Subtotal deste cartão R$ 200,00

         04 abr

APPLECOMBILL

         24 abr GOOGLE YOUTUBEPREMIUM

Valores em reais

66,90

53,90

"""

    def test_detects_two_cards(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        assert set(cards.keys()) == {'3079', '8084'}

    def test_main_card_transaction_count(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        assert len(cards['3079']) == 5

    def test_regular_debit(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        quintal = next(t for t in cards['3079'] if 'QUINTAL' in t.description)
        assert quintal.amount == Decimal('63.50')
        assert quintal.type == 'debit'
        assert quintal.date == date(2026, 4, 3)
        assert quintal.raw_data == {'institution': 'c6', 'card_last4': '3079', 'cardholder': 'LEANDRO NASCIMENTO'}

    def test_payment_is_credit(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        payment = next(t for t in cards['3079'] if 'Pagamento' in t.description)
        assert payment.amount == Decimal('6330.60')
        assert payment.type == 'credit'

    def test_installment_fields(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        carajas = next(t for t in cards['3079'] if 'CARAJAS' in t.description)
        assert carajas.installment_number == 2
        assert carajas.total_installments == 5
        assert carajas.date == date(2026, 3, 12)  # march(3) <= april(4) closing → same year 2026

    def test_virtual_card_parsed(self):
        cards = _parse_c6_text(self.SAMPLE_TEXT)
        assert len(cards['8084']) == 2
        apple = next(t for t in cards['8084'] if 'APPLE' in t.description)
        assert apple.amount == Decimal('66.90')
        assert apple.type == 'debit'

    def test_year_inference_previous_year(self):
        """Transaction month > closing month means it belongs to the prior year."""
        text = self.SAMPLE_TEXT.replace('12 mar CARAJAS', '12 set CARAJAS')
        cards = _parse_c6_text(text)
        carajas = next(t for t in cards['3079'] if 'CARAJAS' in t.description)
        assert carajas.date == date(2025, 9, 12)  # sept(9) > april(4) closing → prior year 2025
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && pytest tests/test_import_service.py::TestParseC6Pdf -v
```

Expected: `ImportError` — `_parse_c6_text` not found.

- [ ] **Step 3: Implement `_parse_c6_text` and `parse_c6_pdf`**

Add to `backend/app/services/import_service.py` (after `parse_picpay_pdf`):

```python
def _parse_c6_text(text: str) -> dict[str, list[TransactionImport]]:
    """Parse C6 credit card statement text into a dict keyed by card last-4 digits."""
    cards: dict[str, list[TransactionImport]] = {}

    # Extract closing date for year inference
    closing_re = re.compile(r'fechamento desta fatura em (\d{2})/(\d{2})/(\d{2})', re.IGNORECASE)
    closing_match = closing_re.search(text)
    if closing_match:
        close_day, close_month, close_year_2d = int(closing_match.group(1)), int(closing_match.group(2)), int(closing_match.group(3))
        close_year = 2000 + close_year_2d
    else:
        from datetime import date as _date_cls
        today = _date_cls.today()
        close_month, close_year = today.month, today.year

    def infer_year(txn_month: int) -> int:
        return close_year - 1 if txn_month > close_month else close_year

    card_section_re = re.compile(
        r'C6 Carbon (?:Virtual )?Final (\d{4})\s*-\s*(.+?)(?=\n|$)', re.IGNORECASE
    )
    values_header_re = re.compile(r'Valores em reais', re.IGNORECASE)
    desc_re = re.compile(
        r'^\s*(\d{1,2})\s+([a-záéíóúãõç]{3})\s+(.*?)(?:\s+-\s+Parcela\s+(\d+)/(\d+))?\s*$',
        re.IGNORECASE
    )
    amount_re = re.compile(r'^\s*([\d.]+,\d{2})\s*$')
    installment_standalone_re = re.compile(r'Parcela\s+(\d+)/(\d+)', re.IGNORECASE)

    lines = text.splitlines()

    # Find card section start indices
    section_starts = []  # list of (line_index, card_last4, cardholder)
    for idx, line in enumerate(lines):
        m = card_section_re.search(line)
        if m:
            section_starts.append((idx, m.group(1), m.group(2).strip()))

    if not section_starts:
        return cards

    # Process each card section up to the next section start (or end of text)
    for sec_idx, (start, card_last4, cardholder) in enumerate(section_starts):
        end = section_starts[sec_idx + 1][0] if sec_idx + 1 < len(section_starts) else len(lines)
        section_lines = lines[start:end]

        # Split section into descriptions block and amounts block at "Valores em reais"
        values_split = None
        for j, sl in enumerate(section_lines):
            if values_header_re.search(sl):
                values_split = j
                break

        if values_split is None:
            continue  # No amounts block found; skip

        desc_block = section_lines[:values_split]
        amounts_block = section_lines[values_split + 1:]

        # Collect description entries
        desc_entries = []
        pending_date = None

        for sl in desc_block:
            dm = desc_re.match(sl)
            if dm:
                day = int(dm.group(1))
                month_str = dm.group(2).lower()
                month = _PT_MONTH_ABBR.get(month_str)
                if month is None:
                    continue
                txn_year = infer_year(month)
                txn_date = date(txn_year, month, day)
                raw_desc = dm.group(3).strip()
                inst_num = int(dm.group(4)) if dm.group(4) else None
                inst_total = int(dm.group(5)) if dm.group(5) else None

                if raw_desc:
                    desc_entries.append((txn_date, raw_desc, inst_num, inst_total))
                    pending_date = txn_date
                else:
                    pending_date = txn_date  # description on next line
            elif pending_date and sl.strip() and not sl.strip().startswith('Subtotal') and not sl.strip().startswith('C6'):
                raw_desc = sl.strip()
                inst_match = installment_standalone_re.search(raw_desc)
                inst_num = int(inst_match.group(1)) if inst_match else None
                inst_total = int(inst_match.group(2)) if inst_match else None
                if inst_match:
                    raw_desc = installment_standalone_re.sub('', raw_desc).strip(' -')
                desc_entries.append((pending_date, raw_desc, inst_num, inst_total))
                pending_date = None

        # Collect amount lines
        amount_values = []
        for sl in amounts_block:
            am = amount_re.match(sl)
            if am:
                amount_values.append(Decimal(normalize_amount(am.group(1))))

        # Zip descriptions with amounts
        raw_data = {'institution': 'c6', 'card_last4': card_last4, 'cardholder': cardholder}
        card_txns = []
        for (txn_date, desc, inst_num, inst_total), amount in zip(desc_entries, amount_values):
            is_payment = 'inclusao de pagamento' in desc.lower() or 'pagamento' in desc.lower() and amount > Decimal('1000')
            txn_type = 'credit' if is_payment else 'debit'
            payee_raw = desc if txn_type == 'debit' else None
            card_txns.append(TransactionImport(
                description=desc,
                amount=amount,
                date=txn_date,
                type=txn_type,
                currency='BRL',
                payee_raw=payee_raw,
                raw_data=raw_data,
                installment_number=inst_num,
                total_installments=inst_total,
            ))
        cards[card_last4] = card_txns

    return cards


def parse_c6_pdf(file_bytes: bytes, password: str) -> dict[str, list[TransactionImport]]:
    """Parse C6 credit card PDF. Raises ValueError('invalid_password') on wrong password."""
    text = _extract_pdf_text_encrypted(file_bytes, password)
    return _parse_c6_text(text)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/test_import_service.py::TestParseC6Pdf -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd backend && pytest tests/test_import_service.py -v
```

Expected: all tests pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/import_service.py backend/tests/test_import_service.py
git commit -m "feat(import): add C6 PDF parser with installment and year-inference support"
```

---

## Task 6: Add `detect_pdf_institution` + Update Preview API Endpoint

**Files:**
- Modify: `backend/app/services/import_service.py` (add `detect_pdf_institution`)
- Modify: `backend/app/api/import_transactions.py`

- [ ] **Step 1: Add `detect_pdf_institution` to import_service.py**

Add after `_extract_pdf_text` / `_extract_pdf_text_encrypted`:

```python
def detect_pdf_institution(text: str) -> str | None:
    """Return 'picpay', 'c6', or None if institution cannot be identified."""
    if 'PicPay' in text:
        return 'picpay'
    if 'C6 Carbon' in text or 'C6 Bank' in text:
        return 'c6'
    return None
```

- [ ] **Step 2: Write API tests for PDF preview**

Add to `backend/tests/test_import_api.py`:

```python
class TestPdfPreview:
    """Tests for PDF detection and error handling in the preview endpoint."""

    def test_unsupported_pdf_returns_400(self, client, auth_headers):
        fake_pdf = b'%PDF-1.4 Unrecognized bank statement content'
        files = {'file': ('statement.pdf', fake_pdf, 'application/pdf')}
        with patch('app.services.import_service._extract_pdf_text', return_value='unknown content'):
            resp = client.post('/api/transactions/import/preview', files=files, headers=auth_headers)
        assert resp.status_code == 400
        assert 'unsupported_pdf_format' in resp.json()['detail']

    def test_c6_without_password_returns_400(self, client, auth_headers):
        fake_pdf = b'%PDF-1.4'
        files = {'file': ('fatura.pdf', fake_pdf, 'application/pdf')}
        with patch('app.services.import_service._extract_pdf_text', side_effect=Exception('encrypted')):
            with patch('app.services.import_service.detect_pdf_institution', return_value='c6'):
                resp = client.post('/api/transactions/import/preview', files=files, headers=auth_headers)
        assert resp.status_code == 400
        assert 'password_required' in resp.json()['detail']
```

- [ ] **Step 3: Update `preview_import` endpoint in `import_transactions.py`**

Replace the full contents of `backend/app/api/import_transactions.py` with:

```python
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.transaction import (
    C6CardPreview,
    TransactionImportPreview,
    TransactionImportRequest,
)
from app.services import import_service
from app.services import account_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transactions", tags=["import"])


@router.post("/import/preview", response_model=TransactionImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    date_format: Optional[str] = Form(None),
    flip_amount: bool = Form(False),
    inflow_column: Optional[str] = Form(None),
    outflow_column: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    user: User = Depends(current_active_user),
):
    content = await file.read()
    filename = file.filename or ""

    logger.info(
        "Import preview requested: filename=%s, size=%d bytes, content_type=%s",
        filename, len(content), file.content_type,
    )

    is_pdf = filename.lower().endswith('.pdf') or (file.content_type or '').startswith('application/pdf')

    try:
        if is_pdf:
            import pikepdf
            import io as _io

            # Step 1: try unencrypted text extraction (PicPay case)
            institution = None
            plain_text = None
            try:
                plain_text = import_service._extract_pdf_text(content)
                institution = import_service.detect_pdf_institution(plain_text)
            except Exception:
                pass

            if institution == 'picpay':
                transactions = import_service._parse_picpay_text(plain_text)
                return TransactionImportPreview(
                    transactions=transactions,
                    detected_format='pdf',
                    institution='picpay',
                )

            # Step 2: unencrypted extraction gave no known institution.
            # Check if PDF is password-protected (C6 case).
            if institution is None:
                is_encrypted = False
                try:
                    pikepdf.open(_io.BytesIO(content))
                except pikepdf.PasswordError:
                    is_encrypted = True
                except Exception:
                    pass

                if is_encrypted:
                    if not password:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail='password_required',
                        )
                    try:
                        text_enc = import_service._extract_pdf_text_encrypted(content, password)
                    except ValueError as exc:
                        if 'invalid_password' in str(exc):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail='invalid_password',
                            )
                        raise
                    institution = import_service.detect_pdf_institution(text_enc)
                    if institution == 'c6':
                        cards_dict = import_service._parse_c6_text(text_enc)
                        if not cards_dict:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail='no_transactions',
                            )
                        all_txns = [t for txns in cards_dict.values() for t in txns]
                        cards_preview = [
                            C6CardPreview(
                                card_last4=last4,
                                cardholder=(txns[0].raw_data or {}).get('cardholder', last4),
                                transactions=txns,
                            )
                            for last4, txns in cards_dict.items()
                        ]
                        return TransactionImportPreview(
                            transactions=all_txns,
                            detected_format='pdf',
                            institution='c6',
                            cards=cards_preview,
                        )

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='unsupported_pdf_format',
            )

        elif filename.lower().endswith('.ofx') or filename.lower().endswith('.qfx'):
            transactions = import_service.parse_ofx(content)
            detected_format = "ofx"
        elif filename.lower().endswith('.qif'):
            transactions = import_service.parse_qif(content)
            detected_format = "qif"
        elif filename.lower().endswith('.xml') or filename.lower().endswith('.camt'):
            transactions = import_service.parse_camt(content)
            detected_format = "camt"
        elif filename.lower().endswith('.csv'):
            transactions = import_service.parse_csv(
                content,
                date_format=date_format,
                flip_amount=flip_amount,
                inflow_column=inflow_column,
                outflow_column=outflow_column,
            )
            detected_format = "csv"
        else:
            try:
                transactions = import_service.parse_ofx(content)
                detected_format = "ofx"
            except Exception:
                try:
                    transactions = import_service.parse_qif(content)
                    detected_format = "qif"
                except Exception:
                    try:
                        transactions = import_service.parse_camt(content)
                        detected_format = "camt"
                    except Exception:
                        transactions = import_service.parse_csv(content)
                        detected_format = "csv"

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to parse import file: filename=%s, size=%d bytes, "
            "content_type=%s, first_100_bytes=%r, error=%s",
            filename, len(content), file.content_type,
            content[:100], e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {str(e)}",
        )

    logger.info(
        "Import preview parsed: filename=%s, format=%s, transactions=%d",
        filename, detected_format, len(transactions),
    )

    return TransactionImportPreview(transactions=transactions, detected_format=detected_format)


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_transactions(
    data: TransactionImportRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    account = await account_service.get_account(session, data.account_id, user.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    imported, skipped, import_log_id = await import_service.import_transactions(
        session, user.id, data.account_id, data.transactions, data.detected_format or "import",
        filename=data.filename, detected_format=data.detected_format,
        detect_duplicates=data.detect_duplicates,
    )

    return {"imported": imported, "skipped": skipped, "import_log_id": str(import_log_id)}
```

- [ ] **Step 4: Run import API tests**

```bash
cd backend && pytest tests/test_import_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/import_service.py backend/app/api/import_transactions.py backend/tests/test_import_api.py
git commit -m "feat(api): add PDF preview endpoint with PicPay and C6 support"
```

---

## Task 7: Frontend Types + api.ts

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add `ImportPreviewCard` and `ImportPreview` types to `types/index.ts`**

Add after the `ImportLog` interface (search for `export interface ImportLog`):

```typescript
export interface ImportPreviewCard {
  card_last4: string
  cardholder: string
  transactions: Transaction[]
}

export interface ImportPreview {
  transactions: Transaction[]
  detected_format: string
  institution?: string
  cards?: ImportPreviewCard[]
}
```

- [ ] **Step 2: Update `previewImport` in `api.ts`**

Replace the existing `previewImport` function (around line 346) with:

```typescript
  previewImport: async (file: File, options?: {
    date_format?: string
    flip_amount?: boolean
    inflow_column?: string
    outflow_column?: string
    password?: string
  }): Promise<ImportPreview> => {
    const formData = new FormData()
    formData.append('file', file)
    if (options?.date_format) formData.append('date_format', options.date_format)
    if (options?.flip_amount) formData.append('flip_amount', 'true')
    if (options?.inflow_column) formData.append('inflow_column', options.inflow_column)
    if (options?.outflow_column) formData.append('outflow_column', options.outflow_column)
    if (options?.password) formData.append('password', options.password)
    const { data } = await api.post('/transactions/import/preview', formData)
    return data
  },
```

Also update the import at the top of `api.ts` to include `ImportPreview` and `ImportPreviewCard`:

```typescript
import type {
  // ... existing imports ...
  ImportPreview,
  ImportPreviewCard,
} from '@/types'
```

- [ ] **Step 3: Check TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api.ts
git commit -m "feat(types): add ImportPreview and ImportPreviewCard types; update previewImport API"
```

---

## Task 8: Frontend — import.tsx

**Files:**
- Modify: `frontend/src/pages/import.tsx`

This task wires up: PDF password field, C6 tabbed preview, per-card account selector, and multi-request import for C6.

- [ ] **Step 1: Add password and C6 state**

Inside `ImportPage` (after the existing `csvOutflowColumn` state), add:

```typescript
  // PDF options
  const [pdfPassword, setPdfPassword] = useState('')
  const [c6Cards, setC6Cards] = useState<ImportPreviewCard[] | null>(null)
  const [c6AccountMap, setC6AccountMap] = useState<Record<string, string>>({}) // card_last4 → account_id
  const [activeC6Tab, setActiveC6Tab] = useState<string>('')
```

Update the import at the top of the file to include the new types:

```typescript
import type { Transaction, ImportLog, ImportPreview, ImportPreviewCard } from '@/types'
```

- [ ] **Step 2: Update `previewData` state type**

Change line:
```typescript
const [previewData, setPreviewData] = useState<{ transactions: Transaction[]; detected_format: string } | null>(null)
```
to:
```typescript
const [previewData, setPreviewData] = useState<ImportPreview | null>(null)
```

- [ ] **Step 3: Update `previewMutation` to pass password and handle C6 response**

Replace the `previewMutation` definition with:

```typescript
  const previewMutation = useMutation({
    mutationFn: ({ file, options }: {
      file: File
      options?: {
        date_format?: string
        flip_amount?: boolean
        inflow_column?: string
        outflow_column?: string
        password?: string
      }
    }) => transactionsApi.previewImport(file, options),
    onSuccess: (data) => {
      setPreviewData(data)
      if (data.institution === 'c6' && data.cards?.length) {
        setC6Cards(data.cards)
        setActiveC6Tab(data.cards[0].card_last4)
        setC6AccountMap({})
      } else {
        setC6Cards(null)
      }
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      if (detail === 'password_required') {
        toast.error(t('import.passwordRequired', 'Este PDF requer senha.'))
      } else {
        toast.error(detail || t('import.processError'))
      }
    },
  })
```

- [ ] **Step 4: Update `processFile` to pass password for PDFs**

Replace the `processFile` function:

```typescript
  function processFile(file: File, passwordOverride?: string) {
    setFileName(file.name)
    setCurrentFile(file)
    resetCsvOptions()

    const isPdf = file.name.toLowerCase().endsWith('.pdf')

    if (file.name.toLowerCase().endsWith('.csv')) {
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result as string
        const firstLine = text.split('\n')[0]
        if (firstLine) {
          setCsvHeaders(firstLine.split(',').map(h => h.trim()))
        }
      }
      reader.readAsText(file)
    }

    const options: Parameters<typeof transactionsApi.previewImport>[1] = {}
    if (isPdf && (passwordOverride ?? pdfPassword)) {
      options.password = passwordOverride ?? pdfPassword
    }

    previewMutation.mutate({ file, options })
  }
```

- [ ] **Step 5: Add PDF password field to the upload UI**

Locate where the CSV options section renders (search for `isCsvFile` in the JSX). Above or alongside that section, add:

```tsx
{isPdfFile && (
  <div className="space-y-2">
    <Label htmlFor="pdf-password">{t('import.pdfPassword', 'Senha do PDF')}</Label>
    <div className="flex gap-2">
      <input
        id="pdf-password"
        type="password"
        value={pdfPassword}
        onChange={(e) => setPdfPassword(e.target.value)}
        placeholder={t('import.pdfPasswordPlaceholder', 'Deixe vazio se não protegido')}
        className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
      />
      <Button
        variant="outline"
        size="sm"
        onClick={() => currentFile && processFile(currentFile, pdfPassword)}
        disabled={previewMutation.isPending}
      >
        {t('import.retry', 'Tentar novamente')}
      </Button>
    </div>
  </div>
)}
```

Also update `isCsvFile` to add `isPdfFile`:

```typescript
  const isCsvFile = fileName?.toLowerCase().endsWith('.csv') ?? false
  const isPdfFile = fileName?.toLowerCase().endsWith('.pdf') ?? false
```

- [ ] **Step 6: Add C6 tabbed preview**

In the preview section of the JSX, after the existing preview table, add a C6 tab view. Find the section that renders `previewData` and wrap it:

```tsx
{previewData && previewData.institution === 'c6' && c6Cards ? (
  <div className="space-y-4">
    {/* Card tabs */}
    <div className="flex gap-2 border-b">
      {c6Cards.map((card) => (
        <button
          key={card.card_last4}
          onClick={() => setActiveC6Tab(card.card_last4)}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeC6Tab === card.card_last4
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          {card.cardholder.split(' ')[0]} ({card.card_last4})
          <span className="ml-1 text-xs text-muted-foreground">
            {card.transactions.length} {t('import.transactions', 'transações')}
          </span>
        </button>
      ))}
    </div>

    {/* Active card transactions + account selector */}
    {c6Cards.filter(card => card.card_last4 === activeC6Tab).map((card) => (
      <div key={card.card_last4} className="space-y-3">
        <div className="flex items-center gap-3">
          <Label>{t('import.account', 'Conta')}: {card.cardholder}</Label>
          <select
            value={c6AccountMap[card.card_last4] ?? ''}
            onChange={(e) => setC6AccountMap(prev => ({ ...prev, [card.card_last4]: e.target.value }))}
            className="flex h-9 rounded-md border border-input bg-background px-3 py-1 text-sm"
          >
            <option value="">{t('import.selectAccount', 'Selecione uma conta')}</option>
            {accountsList?.map((acc) => (
              <option key={acc.id} value={acc.id}>{acc.name}</option>
            ))}
          </select>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('import.date', 'Data')}</TableHead>
              <TableHead>{t('import.description', 'Descrição')}</TableHead>
              <TableHead>{t('import.type', 'Tipo')}</TableHead>
              <TableHead className="text-right">{t('import.amount', 'Valor')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {card.transactions.map((txn, idx) => (
              <TableRow key={idx}>
                <TableCell>{txn.date}</TableCell>
                <TableCell>
                  {txn.description}
                  {txn.installment_number && txn.total_installments && (
                    <span className="ml-1 text-xs text-muted-foreground">
                      ({txn.installment_number}/{txn.total_installments})
                    </span>
                  )}
                </TableCell>
                <TableCell>{txn.type}</TableCell>
                <TableCell className="text-right">
                  {formatCurrency(Number(txn.amount), 'BRL', 'pt-BR')}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    ))}
  </div>
) : previewData ? (
  /* Keep the existing single-account preview table and account selector JSX here exactly as it is.
     This branch handles OFX / QIF / CAMT / CSV imports — no changes needed. */
  <>{/* existing preview JSX */}</>
) : null}
```

- [ ] **Step 7: Update import handler for C6 multi-card**

Replace the `importMutation` definition with:

```typescript
  const importMutation = useMutation({
    mutationFn: async () => {
      if (previewData?.institution === 'c6' && c6Cards) {
        // Fire one import per card, each with its own account
        const results = await Promise.all(
          c6Cards
            .filter(card => c6AccountMap[card.card_last4])
            .map(card =>
              transactionsApi.import(
                c6AccountMap[card.card_last4],
                card.transactions,
                fileName ?? '',
                'pdf',
              )
            )
        )
        return results.reduce(
          (acc, r) => ({ imported: acc.imported + r.imported, skipped: acc.skipped + r.skipped, import_log_id: r.import_log_id }),
          { imported: 0, skipped: 0, import_log_id: '' }
        )
      }
      return transactionsApi.import(
        selectedAccount,
        previewData!.transactions,
        fileName ?? '',
        previewData!.detected_format,
        isCsvFile ? { detect_duplicates: csvDetectDuplicates } : undefined,
      )
    },
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['import-logs'] })
      const msg = data.skipped > 0
        ? t('import.importedWithSkipped', { imported: data.imported, skipped: data.skipped })
        : `${data.imported} ${t('import.transactionsImported')}`
      toast.success(msg)
      setPreviewData(null)
      setSelectedAccount('')
      setFileName(null)
      setCurrentFile(null)
      setC6Cards(null)
      setC6AccountMap({})
      setPdfPassword('')
      resetCsvOptions()
      if (fileInputRef.current) fileInputRef.current.value = ''
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail || t('import.importError'))
    },
  })
```

- [ ] **Step 8: Update Import button disabled condition**

The Import button should also be disabled if C6 cards are shown and not all have accounts selected:

```typescript
  const isImportDisabled = (() => {
    if (!previewData) return true
    if (previewData.institution === 'c6' && c6Cards) {
      return c6Cards.some(card => !c6AccountMap[card.card_last4])
    }
    return !selectedAccount
  })()
```

Use `isImportDisabled` on the Import button's `disabled` prop.

- [ ] **Step 9: TypeScript check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/import.tsx frontend/src/types/index.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): add PDF import with password field and C6 tabbed preview"
```

---

## Task 9: End-to-End Smoke Test

- [ ] **Step 1: Start dev servers**

```bash
# Terminal 1
cd backend && uvicorn app.main:app --reload

# Terminal 2
cd frontend && npm run dev
```

- [ ] **Step 2: Test PicPay import**

1. Open `http://localhost:5173/import`
2. Drop `import/picpay.pdf` onto the upload area
3. Verify: preview shows transactions grouped from the PDF (Pix enviado, Compra realizada, etc.)
4. Select an account, click Import
5. Verify: success toast, transactions appear in the account

- [ ] **Step 3: Test C6 import**

1. Drop `import/fatura-c6.pdf` onto the upload area
2. Verify: inline error "Este PDF requer senha" appears
3. Enter `023648` in the password field, click "Tentar novamente"
4. Verify: tabbed preview shows 3 tabs (Leandro/3079, Kelly/2579, Virtual/8084)
5. Select or create an account for each card
6. Click Import
7. Verify: success toast, transactions in each account with installment info visible

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: PDF import complete — PicPay and C6 with multi-card support"
```
