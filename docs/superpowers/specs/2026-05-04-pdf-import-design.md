# PDF Import — PicPay & C6

**Date:** 2026-05-04
**Status:** Approved
**Approach:** B — Virtual accounts auto-created per card, full reuse of existing import infrastructure

---

## 1. Scope

Add PDF import support for two Brazilian financial institutions:

- **PicPay** — conta digital, extrato mensal sem senha
- **C6 Bank** — fatura de cartão de crédito, protegida por senha (primeiros 6 dígitos do CPF)

Goal: enable categorization and day-of-month spending analysis from these PDFs inside Securo, using the existing transaction/account/category infrastructure.

---

## 2. Architecture

```
Frontend (import.tsx)
  ├─ format selector: adds "PDF" option
  ├─ password field (optional, shown for all PDFs)
  ├─ preview: single list (PicPay) or tabbed per card (C6)
  └─ account mapping: per card/institution → select existing or auto-create

Backend API (import_transactions.py)
  ├─ detects PDF by MIME/extension
  ├─ extracts text (pdfminer.six)
  ├─ decrypts C6 in-memory (pikepdf), password never stored
  ├─ detects institution from text content
  └─ returns preview grouped by card

Parsers (import_service.py)
  ├─ parse_picpay_pdf(file_bytes) → List[TransactionImport]
  └─ parse_c6_pdf(file_bytes, password) → Dict[card_last4, List[TransactionImport]]

New dependencies (pyproject.toml)
  ├─ pikepdf   — C6 PDF decryption
  └─ pdfminer.six — text extraction
```

**Transaction `source` field:** adds `"pdf"` to the existing enum.
Institution and card metadata stored in `raw_data`:
```json
{ "institution": "picpay" }
{ "institution": "c6", "card_last4": "3079", "cardholder": "LEANDRO NASCIMENTO" }
```

---

## 3. Parsers

### 3.1 PicPay (`parse_picpay_pdf`)

**Input structure (text extracted from PDF):**
```
DD de mês AAAA        Saldo ao final do dia: R$ X
Hora    Tipo    Origem / Destino    Forma de pagamento    Valor
HH:MM   Pix enviado   NOME          Com saldo             −R$ X
```

**Logic:**
1. Detect day headers via regex `(\d{1,2}) de (\w+) (\d{4})`
2. Per transaction line: extract time, tipo, destino, valor
3. Combine day header + time → full `datetime` for `date` field
4. Amount sign: `−R$` = debit, `+R$` or no prefix = credit
5. `description` = tipo, `payee` = origem/destino field

**Type mapping:**

| PicPay tipo | Transaction type |
|---|---|
| Pix enviado | debit |
| Compra realizada | debit |
| Pagamento realizado | debit |
| Dinheiro guardado | debit |
| Pix recebido | credit |
| Transferência recebida | credit |
| Dinheiro resgatado | credit |

**Cofrinho entries:** imported with `payee = "Cofrinho PicPay"`.
Self-transfers (same name as account holder): imported normally, user categorizes.

---

### 3.2 C6 (`parse_c6_pdf`)

**Steps:**
1. `pikepdf.open(buffer, password=password)` → decrypted PDF in memory buffer
2. `pdfminer` extracts text
3. Detect card sections via regex: `C6 Carbon (?:Virtual )?Final (\d{4}) - (.+)`
4. Within each section, extract transactions:
   ```
   DD mmm   ESTABELECIMENTO - Parcela N/M    VALOR
   ```
5. Return `Dict[card_last4, List[TransactionImport]]`

**Year inference:**
- Extract closing date from header (`"Compras feitas até DD/MM/YY"` or similar)
- Transactions with month > closing month belong to the previous year

**Installments:**
- `"Parcela N/M"` → `installment_number=N`, `total_installments=M`
- No parcela info → both fields `null`

**Special lines:**
- `"Inclusao de Pagamento"` → `type=credit`
- `"Anuidade"` → imported as regular debit, `payee = "C6 Bank"`

**Amount parsing:**
- Values in PDF are always positive (e.g., `6.330,60`)
- All are debit except `"Inclusao de Pagamento"` (credit)
- Parse BR format: `.` as thousands separator, `,` as decimal

---

## 4. Institution Detection

Triggered in the preview endpoint after text extraction:

| Text content contains | Action |
|---|---|
| `"PicPay"` | → `parse_picpay_pdf` |
| `"C6 Carbon"` | → `parse_c6_pdf` (requires password) |
| Neither | → error `unsupported_pdf_format` |

If C6 detected but no password provided → return error `password_required` before attempting parse.

---

## 5. Frontend UX

### Step 1 — Upload
- "PDF" added to format selector alongside OFX, QIF, CAMT, CSV
- Password field appears below file input (visible when format = PDF)
  - Placeholder: "Deixe vazio se não protegido"
  - Pre-fill: first 6 digits of user's CPF from profile (if available)

### Step 2 — Preview

**PicPay:** same as current single-account import flow.

**C6:** tabbed preview, one tab per detected card:
```
[ Leandro (3079) ]  [ Kelly (2579) ]  [ Virtual (8084) ]
```
- Each tab: transaction list for that card
- Each tab: independent account selector
- "Criar conta automaticamente" button if no existing account matches card last4
  - Auto-created account: `type=credit_card`, name = `"C6 Carbon - {cardholder} ({last4})"`

### Step 3 — Import
- C6: N parallel import requests (one per card) → N entries in `import_log`
- PicPay: single import request

**"Password required" UX:**
- If backend returns `password_required` during preview request, highlight the password field in Step 1 with the error message and block progression to preview until password is provided

---

## 6. Account Auto-Creation (C6)

For each card section detected in the PDF:
1. Look up existing accounts where `name` contains the card last4 digits
2. If found: pre-select in account dropdown
3. If not found: show "Criar conta" option
   - Creates `Account` with `type=credit_card`, `currency=BRL`, `card_brand=mastercard` (C6 Carbon is Mastercard)

---

## 7. Error Handling

| Error code | Trigger | UI message |
|---|---|---|
| `unsupported_pdf_format` | PDF text matches neither PicPay nor C6 | "Formato não suportado. Verifique se é um extrato PicPay ou fatura C6." |
| `password_required` | C6 detected, no password in request | "Este PDF requer senha. Informe os 6 primeiros dígitos do CPF." |
| `invalid_password` | pikepdf raises `PasswordError` | "Senha incorreta. Tente os 6 primeiros dígitos do CPF." |
| `pdf_parse_error` | Unexpected exception during parse | "Não foi possível ler o PDF. Tente exportar novamente." |
| `no_transactions` | Parser returns empty list | "Nenhuma transação encontrada neste período." |

---

## 8. Security

- Password transmitted via HTTPS in multipart form body (not query string, not header)
- Password never logged (excluded from any debug/access log)
- Decrypted PDF exists only in memory buffer, never written to disk
- `raw_data` stores only card metadata, not password or CPF

---

## 9. Date Precision

| Institution | Date stored |
|---|---|
| PicPay | Full datetime (`YYYY-MM-DD HH:MM`) — hour available per transaction |
| C6 | Date only (`YYYY-MM-DD`) — fatura has no per-transaction time |

Both support day-of-month spending analysis.

---

## 10. Files to Change

| File | Change |
|---|---|
| `backend/pyproject.toml` | Add `pikepdf`, `pdfminer.six` dependencies |
| `backend/app/models/transaction.py` | Add `"pdf"` to `source` enum |
| `backend/app/services/import_service.py` | Add `parse_picpay_pdf()`, `parse_c6_pdf()`, `detect_pdf_institution()` |
| `backend/app/api/import_transactions.py` | Handle PDF MIME, password field, C6 multi-card response shape |
| `backend/app/schemas/transaction.py` | Update `TransactionImportRequest` to include optional `password` field |
| `frontend/src/pages/import.tsx` | PDF format option, password field, C6 tabbed preview, per-card account selector |
| `frontend/src/types/index.ts` | Update import preview types for multi-card response |
