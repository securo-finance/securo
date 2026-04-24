import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.user import User
from app.services.import_service import parse_csv, parse_ofx, parse_qif, parse_camt, import_transactions


class TestParseCsv:
    """Tests for the parse_csv function."""

    def test_parse_csv(self):
        """Parse a valid CSV with localized columns (data, descricao, valor)."""
        csv_content = (
            "data,descricao,valor\n"
            "10/02/2026,UBER TRIP,-25.50\n"
            "12/02/2026,IFOOD RESTAURANTE,-45.00\n"
            "05/02/2026,SALARIO FEV,8000.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 3

        # First transaction: UBER TRIP (debit because negative)
        assert transactions[0].description == "UBER TRIP"
        assert transactions[0].amount == Decimal("25.50")
        assert transactions[0].date == date(2026, 2, 10)
        assert transactions[0].type == "debit"

        # Second transaction: IFOOD (debit)
        assert transactions[1].description == "IFOOD RESTAURANTE"
        assert transactions[1].amount == Decimal("45.00")
        assert transactions[1].date == date(2026, 2, 12)
        assert transactions[1].type == "debit"

        # Third transaction: SALARIO (credit because positive)
        assert transactions[2].description == "SALARIO FEV"
        assert transactions[2].amount == Decimal("8000.00")
        assert transactions[2].date == date(2026, 2, 5)
        assert transactions[2].type == "credit"

    def test_parse_csv_english(self):
        """Parse a CSV with English column headers (date, description, amount)."""
        csv_content = (
            "date,description,amount\n"
            "2026-02-10,GROCERY STORE,-120.50\n"
            "2026-02-15,SALARY PAYMENT,5000.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2

        assert transactions[0].description == "GROCERY STORE"
        assert transactions[0].amount == Decimal("120.50")
        assert transactions[0].date == date(2026, 2, 10)
        assert transactions[0].type == "debit"

        assert transactions[1].description == "SALARY PAYMENT"
        assert transactions[1].amount == Decimal("5000.00")
        assert transactions[1].date == date(2026, 2, 15)
        assert transactions[1].type == "credit"

    def test_parse_csv_invalid_columns(self):
        """CSV with unrecognized column names should raise ValueError with found and expected columns."""
        csv_content = (
            "col_a,col_b,col_c\n"
            "foo,bar,baz\n"
        )
        with pytest.raises(ValueError, match="Found: col_a, col_b, col_c") as exc_info:
            parse_csv(csv_content.encode("utf-8"))
        # Should also tell the user what columns are expected
        assert "date" in str(exc_info.value)
        assert "description" in str(exc_info.value)

    def test_parse_csv_brl_amounts(self):
        """CSV with R$ prefix and comma-as-decimal amounts should be parsed correctly.

        When amounts use comma as decimal separator, CSV values must be quoted
        to avoid conflict with the comma column delimiter.
        """
        csv_content = (
            'data,descricao,valor\n'
            '10/02/2026,MERCADO LIVRE,"R$ -150,99"\n'
            '11/02/2026,PIX RECEBIDO,"R$ 200,00"\n'
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2

        # R$ -150,99 -> strip R$ -> -150,99 -> comma becomes dot -> -150.99 -> abs = 150.99
        assert transactions[0].description == "MERCADO LIVRE"
        assert transactions[0].amount == Decimal("150.99")
        assert transactions[0].type == "debit"

        # R$ 200,00 -> 200.00
        assert transactions[1].description == "PIX RECEBIDO"
        assert transactions[1].amount == Decimal("200.00")
        assert transactions[1].type == "credit"

    def test_parse_csv_with_bom(self):
        """CSV encoded with UTF-8 BOM should be parsed correctly."""
        # Encode with utf-8-sig which prepends BOM bytes; parse_csv decodes with utf-8-sig
        csv_content = "date,description,amount\n2026-01-15,TEST TRANSACTION,-50.00\n"
        transactions = parse_csv(csv_content.encode("utf-8-sig"))

        assert len(transactions) == 1
        assert transactions[0].description == "TEST TRANSACTION"
        assert transactions[0].amount == Decimal("50.00")

    def test_parse_csv_skips_invalid_dates(self):
        """Rows with unparseable dates should be silently skipped."""
        csv_content = (
            "date,description,amount\n"
            "not-a-date,BAD ROW,-10.00\n"
            "2026-02-20,GOOD ROW,-30.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].description == "GOOD ROW"

    def test_parse_csv_skips_invalid_amounts(self):
        """Rows with unparseable amounts should be silently skipped."""
        csv_content = (
            "date,description,amount\n"
            "2026-02-20,BAD AMOUNT,abc\n"
            "2026-02-21,GOOD AMOUNT,-75.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].description == "GOOD AMOUNT"

    def test_parse_csv_dd_mm_yyyy_format(self):
        """DD/MM/YYYY date format should be correctly parsed."""
        csv_content = "data,descricao,valor\n25/12/2025,NATAL,-500.00\n"
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].date == date(2025, 12, 25)

    def test_parse_csv_semicolon_delimiter(self):
        """CSV using semicolons as delimiter should be parsed correctly."""
        csv_content = (
            "date;description;amount\n"
            "15/01/2026;Grocery Store;-120.50\n"
            "20/01/2026;Salary Payment;5000.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].description == "Grocery Store"
        assert transactions[0].amount == Decimal("120.50")
        assert transactions[0].type == "debit"
        assert transactions[1].description == "Salary Payment"
        assert transactions[1].amount == Decimal("5000.00")
        assert transactions[1].type == "credit"

    def test_parse_csv_tab_delimiter(self):
        """CSV using tabs as delimiter should be parsed correctly."""
        csv_content = (
            "date\tdescription\tamount\n"
            "2026-01-15\tGrocery Store\t-120.50\n"
            "2026-01-20\tSalary Payment\t5000.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].description == "Grocery Store"
        assert transactions[0].amount == Decimal("120.50")
        assert transactions[0].type == "debit"
        assert transactions[1].description == "Salary Payment"
        assert transactions[1].amount == Decimal("5000.00")
        assert transactions[1].type == "credit"

    def test_parse_csv_empty_file(self):
        """A CSV with only headers and no data rows should return empty list."""
        csv_content = "date,description,amount\n"
        transactions = parse_csv(csv_content.encode("utf-8"))
        assert len(transactions) == 0

    def test_parse_csv_explicit_date_format(self):
        """CSV with explicit date format should use only that format."""
        csv_content = (
            "date,description,amount\n"
            "03/04/2026,PAYMENT,-100.00\n"
        )
        # With MM/DD/YYYY format, 03/04 = March 4
        transactions = parse_csv(csv_content.encode("utf-8"), date_format="MM/DD/YYYY")
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 3, 4)

        # With DD/MM/YYYY format, 03/04 = April 3
        transactions = parse_csv(csv_content.encode("utf-8"), date_format="DD/MM/YYYY")
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 4, 3)

    def test_parse_csv_flip_amount(self):
        """Flip amount should negate amounts, swapping credit/debit."""
        csv_content = (
            "date,description,amount\n"
            "2026-01-10,EXPENSE,100.00\n"
            "2026-01-11,INCOME,-500.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"), flip_amount=True)
        assert len(transactions) == 2
        # 100.00 flipped to -100.00 => debit
        assert transactions[0].type == "debit"
        assert transactions[0].amount == Decimal("100.00")
        # -500.00 flipped to 500.00 => credit
        assert transactions[1].type == "credit"
        assert transactions[1].amount == Decimal("500.00")

    def test_parse_csv_split_columns(self):
        """CSV with inflow/outflow split columns."""
        csv_content = (
            "date,description,inflow,outflow\n"
            "2026-01-10,SALARY,5000.00,\n"
            "2026-01-11,RENT,,1200.00\n"
        )
        transactions = parse_csv(
            csv_content.encode("utf-8"),
            inflow_column="inflow",
            outflow_column="outflow",
        )
        assert len(transactions) == 2
        assert transactions[0].type == "credit"
        assert transactions[0].amount == Decimal("5000.00")
        assert transactions[1].type == "debit"
        assert transactions[1].amount == Decimal("1200.00")

    def test_parse_csv_brazilian_amount(self):
        """CSV using comma as the decimal separator in the amount field."""
        csv_content = (
            "date,description,amount\n"
            '2026-01-10,SALARY,"5,000.00"\n' 
            "2026-01-11,RENT,1200.00\n"
        )

        transactions = parse_csv(csv_content.encode("utf-8"))
        assert len(transactions) == 2
        assert transactions[0].amount == Decimal("5000.00")
        assert transactions[1].amount == Decimal("1200.00")


class TestParseQif:
    """Tests for the parse_qif function."""

    def test_parse_qif_basic(self):
        """Parse a basic QIF file with multiple transactions."""
        qif_content = (
            "!Type:Bank\n"
            "D01/15/2026\n"
            "T-250.00\n"
            "PElectric Company\n"
            "MMonthly bill\n"
            "^\n"
            "D01/20/2026\n"
            "T1500.00\n"
            "PEmployer Inc\n"
            "MSalary\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))

        assert len(transactions) == 2

        assert transactions[0].description == "Electric Company"
        assert transactions[0].amount == Decimal("250.00")
        assert transactions[0].date == date(2026, 1, 15)
        assert transactions[0].type == "debit"

        assert transactions[1].description == "Employer Inc"
        assert transactions[1].amount == Decimal("1500.00")
        assert transactions[1].date == date(2026, 1, 20)
        assert transactions[1].type == "credit"

    def test_parse_qif_memo_as_description(self):
        """When no payee, memo should be used as description."""
        qif_content = (
            "D02/10/2026\n"
            "T-50.00\n"
            "MGrocery purchase\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].description == "Grocery purchase"

    def test_parse_qif_unknown_description(self):
        """When no payee or memo, description should be 'Unknown'."""
        qif_content = (
            "D03/01/2026\n"
            "T-10.00\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].description == "Unknown"

    def test_parse_qif_iso_date(self):
        """QIF with YYYY-MM-DD date format."""
        qif_content = (
            "D2026-03-15\n"
            "T-100.00\n"
            "PTest\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 3, 15)

    def test_parse_qif_skips_invalid_blocks(self):
        """Blocks without date or amount should be skipped."""
        qif_content = (
            "!Type:Bank\n"
            "^\n"
            "POrphan payee\n"
            "^\n"
            "D01/01/2026\n"
            "T-50.00\n"
            "PValid\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].description == "Valid"

    def test_parse_qif_comma_in_amount(self):
        """QIF amounts with comma thousands separator."""
        qif_content = (
            "D01/01/2026\n"
            "T-1,250.00\n"
            "PBig Purchase\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("1250.00")

    def test_parse_qif_latin1_encoding(self):
        """QIF files from legacy software (e.g. Microsoft Money) using Latin-1 encoding."""
        qif_content = (
            "!Type:Bank\n"
            "D01/15/2026\n"
            "T-75.00\n"
            "PCaf\u00e9 Fran\u00e7ais\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("latin-1"))
        assert len(transactions) == 1
        assert transactions[0].description == "Caf\u00e9 Fran\u00e7ais"
        assert transactions[0].amount == Decimal("75.00")
        assert transactions[0].type == "debit"

    def test_parse_qif_two_digit_year(self):
        """QIF with 2-digit year date formats (common in Microsoft Money)."""
        qif_content = (
            "D01/15/26\n"
            "T-50.00\n"
            "PTest\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 1, 15)

    def test_parse_qif_apostrophe_two_digit_year(self):
        """QIF with apostrophe separator and 2-digit year (Microsoft Money format)."""
        qif_content = (
            "D01/15'26\n"
            "T-100.00\n"
            "PTest\n"
            "^\n"
        )
        transactions = parse_qif(qif_content.encode("utf-8"))
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 1, 15)


class TestParseCamt:
    """Tests for the parse_camt function (ISO 20022 XML)."""

    def _make_camt_xml(self, entries_xml: str) -> bytes:
        """Helper to wrap entries in a valid CAMT.053 XML structure."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
            '<BkToCstmrStmt><Stmt>'
            f'{entries_xml}'
            '</Stmt></BkToCstmrStmt>'
            '</Document>'
        ).encode('utf-8')

    def test_parse_camt_basic(self):
        """Parse a basic CAMT file with credit and debit entries."""
        entries = (
            '<Ntry>'
            '<Amt Ccy="BRL">1500.00</Amt>'
            '<CdtDbtInd>CRDT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-15</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Salary Payment</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
            '<Ntry>'
            '<Amt Ccy="BRL">250.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-16</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Electric Bill</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))

        assert len(transactions) == 2

        assert transactions[0].description == "Salary Payment"
        assert transactions[0].amount == Decimal("1500.00")
        assert transactions[0].type == "credit"
        assert transactions[0].date == date(2026, 1, 15)

        assert transactions[1].description == "Electric Bill"
        assert transactions[1].amount == Decimal("250.00")
        assert transactions[1].type == "debit"
        assert transactions[1].date == date(2026, 1, 16)

    def test_parse_camt_valdt_fallback(self):
        """When BookgDt is missing, ValDt should be used."""
        entries = (
            '<Ntry>'
            '<Amt Ccy="BRL">100.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<ValDt><Dt>2026-02-20</Dt></ValDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Test</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))
        assert len(transactions) == 1
        assert transactions[0].date == date(2026, 2, 20)

    def test_parse_camt_description_fallbacks(self):
        """Description should fall back through various paths."""
        # Creditor name fallback
        entries = (
            '<Ntry>'
            '<Amt Ccy="BRL">50.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-01</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Store ABC</Nm></Cdtr></RltdPties></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))
        assert len(transactions) == 1
        assert transactions[0].description == "Store ABC"

    def test_parse_camt_unknown_description(self):
        """When no description paths exist, should default to 'Unknown'."""
        entries = (
            '<Ntry>'
            '<Amt Ccy="BRL">75.00</Amt>'
            '<CdtDbtInd>CRDT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-01</Dt></BookgDt>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))
        assert len(transactions) == 1
        assert transactions[0].description == "Unknown"

    def test_parse_camt_skips_entries_without_date(self):
        """Entries without any date should be skipped."""
        entries = (
            '<Ntry>'
            '<Amt Ccy="BRL">100.00</Amt>'
            '<CdtDbtInd>CRDT</CdtDbtInd>'
            '</Ntry>'
            '<Ntry>'
            '<Amt Ccy="BRL">200.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<BookgDt><Dt>2026-03-01</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Valid</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))
        assert len(transactions) == 1
        assert transactions[0].description == "Valid"

    def test_parse_camt_no_namespace(self):
        """CAMT XML without namespace should still be parsed."""
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Document>'
            '<BkToCstmrStmt><Stmt>'
            '<Ntry>'
            '<Amt Ccy="BRL">300.00</Amt>'
            '<CdtDbtInd>CRDT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-10</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>No NS</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
            '</Stmt></BkToCstmrStmt>'
            '</Document>'
        ).encode('utf-8')
        transactions = parse_camt(xml)
        assert len(transactions) == 1
        assert transactions[0].description == "No NS"
        assert transactions[0].amount == Decimal("300.00")


class TestParseOfx:
    """Tests for the parse_ofx function."""

    def _make_ofx(self, transactions_sgml: str) -> bytes:
        """Helper to wrap transaction SGML in a valid OFX structure."""
        return (
            "OFXHEADER:100\n"
            "DATA:OFXSGML\n"
            "VERSION:102\n"
            "SECURITY:NONE\n"
            "ENCODING:USASCII\n"
            "CHARSET:1252\n"
            "COMPRESSION:NONE\n"
            "OLDFILEUID:NONE\n"
            "NEWFILEUID:NONE\n"
            "\n"
            "<OFX>\n"
            "<SIGNONMSGSRSV1>\n"
            "<SONRS>\n"
            "<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
            "<DTSERVER>20260101\n"
            "<LANGUAGE>POR\n"
            "</SONRS>\n"
            "</SIGNONMSGSRSV1>\n"
            "<BANKMSGSRSV1>\n"
            "<STMTTRNRS>\n"
            "<TRNUID>1001\n"
            "<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
            "<STMTRS>\n"
            "<CURDEF>BRL\n"
            "<BANKACCTFROM>\n"
            "<BANKID>0001\n"
            "<ACCTID>12345\n"
            "<ACCTTYPE>CHECKING\n"
            "</BANKACCTFROM>\n"
            "<BANKTRANLIST>\n"
            "<DTSTART>20260101\n"
            "<DTEND>20260131\n"
            f"{transactions_sgml}\n"
            "</BANKTRANLIST>\n"
            "</STMTRS>\n"
            "</STMTTRNRS>\n"
            "</BANKMSGSRSV1>\n"
            "</OFX>\n"
        ).encode("ascii")

    def test_parse_ofx_extracts_fitid(self):
        """FITID from OFX transactions populates external_id."""
        ofx = self._make_ofx(
            "<STMTTRN>\n"
            "<TRNTYPE>DEBIT\n"
            "<DTPOSTED>20260115\n"
            "<TRNAMT>-985.50\n"
            "<FITID>TXN001ABC\n"
            "<MEMO>PIX ENVIADO - FULANO\n"
            "</STMTTRN>\n"
        )
        transactions = parse_ofx(ofx)

        assert len(transactions) == 1
        assert transactions[0].external_id == "TXN001ABC"
        assert transactions[0].amount == Decimal("985.50")
        assert transactions[0].type == "debit"

    def test_parse_ofx_keeps_duplicate_looking_transactions(self):
        """Transactions with same fields but different FITIDs are both kept."""
        ofx = self._make_ofx(
            "<STMTTRN>\n"
            "<TRNTYPE>DEBIT\n"
            "<DTPOSTED>20260115\n"
            "<TRNAMT>-985.50\n"
            "<FITID>FITID_001\n"
            "<MEMO>PIX ENVIADO - FULANO\n"
            "</STMTTRN>\n"
            "<STMTTRN>\n"
            "<TRNTYPE>DEBIT\n"
            "<DTPOSTED>20260115\n"
            "<TRNAMT>-985.50\n"
            "<FITID>FITID_002\n"
            "<MEMO>PIX ENVIADO - FULANO\n"
            "</STMTTRN>\n"
        )
        transactions = parse_ofx(ofx)

        assert len(transactions) == 2
        assert transactions[0].external_id == "FITID_001"
        assert transactions[1].external_id == "FITID_002"
        assert transactions[0].amount == transactions[1].amount
        assert transactions[0].description == transactions[1].description

    def test_parse_ofx_skips_balance_summary_rows_with_empty_fitid(self):
        """Banco do Brasil emits Saldo Anterior/Saldo do dia as STMTTRN with empty
        FITID. These should be silently skipped instead of aborting the import."""
        ofx = self._make_ofx(
            "<STMTTRN>\n"
            "<TRNTYPE>OTHER\n"
            "<DTPOSTED>20260101\n"
            "<TRNAMT>0.00\n"
            "<FITID>\n"
            "<MEMO>Saldo Anterior\n"
            "</STMTTRN>\n"
            "<STMTTRN>\n"
            "<TRNTYPE>DEBIT\n"
            "<DTPOSTED>20260115\n"
            "<TRNAMT>-985.50\n"
            "<FITID>TXN001ABC\n"
            "<MEMO>PIX ENVIADO - FULANO\n"
            "</STMTTRN>\n"
            "<STMTTRN>\n"
            "<TRNTYPE>OTHER\n"
            "<DTPOSTED>20260131\n"
            "<TRNAMT>0.00\n"
            "<FITID>\n"
            "<MEMO>Saldo do dia\n"
            "</STMTTRN>\n"
        )
        transactions = parse_ofx(ofx)

        assert len(transactions) == 1
        assert transactions[0].external_id == "TXN001ABC"
        assert transactions[0].description == "PIX ENVIADO - FULANO"

    def test_parse_ofx_keeps_real_transactions_with_empty_fitid(self):
        """A real transaction missing a FITID should still be imported (without
        an external_id), not abort the whole file."""
        ofx = self._make_ofx(
            "<STMTTRN>\n"
            "<TRNTYPE>DEBIT\n"
            "<DTPOSTED>20260115\n"
            "<TRNAMT>-100.00\n"
            "<FITID>\n"
            "<MEMO>UBER TRIP\n"
            "</STMTTRN>\n"
        )
        transactions = parse_ofx(ofx)

        assert len(transactions) == 1
        assert transactions[0].external_id is None
        assert transactions[0].description == "UBER TRIP"
        assert transactions[0].amount == Decimal("100.00")


# ═══════════════════════════════════════════════════════════════════════════
# MULTI-CURRENCY PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestCsvCurrencyParsing:
    """Tests for CSV parsing with currency and fx_rate columns."""

    def test_parse_csv_with_currency_column(self):
        """CSV with a 'currency' column should populate the currency field."""
        csv_content = (
            "date,description,amount,currency\n"
            "2026-01-10,Amazon Purchase,-120.50,USD\n"
            "2026-01-11,Local Store,-45.00,BRL\n"
            "2026-01-12,Euro Payment,-80.00,EUR\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 3
        assert transactions[0].currency == "USD"
        assert transactions[1].currency == "BRL"
        assert transactions[2].currency == "EUR"

    def test_parse_csv_with_moeda_column(self):
        """CSV with Portuguese 'moeda' column should detect currency."""
        csv_content = (
            "data,descricao,valor,moeda\n"
            "10/01/2026,AMAZON,-120.50,USD\n"
            "11/01/2026,PIX RECEBIDO,500.00,BRL\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].currency == "USD"
        assert transactions[1].currency == "BRL"

    def test_parse_csv_with_fx_rate_column(self):
        """CSV with 'fx_rate' column should populate the fx_rate field."""
        csv_content = (
            "date,description,amount,currency,fx_rate\n"
            "2026-01-10,Amazon Purchase,-120.50,USD,5.25\n"
            "2026-01-11,Local Store,-45.00,BRL,\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].currency == "USD"
        assert transactions[0].fx_rate == Decimal("5.25")
        assert transactions[1].currency == "BRL"
        assert transactions[1].fx_rate is None

    def test_parse_csv_with_taxa_cambio_column(self):
        """CSV with Portuguese 'taxa_cambio' column should detect fx_rate."""
        csv_content = (
            "data,descricao,valor,moeda,taxa_cambio\n"
            '10/01/2026,COMPRA EXTERIOR,-200.00,USD,"5,30"\n'
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].fx_rate == Decimal("5.30")

    def test_parse_csv_without_currency_column(self):
        """CSV without currency column should leave currency as None."""
        csv_content = (
            "date,description,amount\n"
            "2026-01-10,GROCERY,-50.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].currency is None
        assert transactions[0].fx_rate is None


class TestCamtCurrencyParsing:
    """Tests for CAMT parsing with currency extraction."""

    def _make_camt_xml(self, entries_xml: str) -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
            '<BkToCstmrStmt><Stmt>'
            f'{entries_xml}'
            '</Stmt></BkToCstmrStmt>'
            '</Document>'
        ).encode('utf-8')

    def test_parse_camt_extracts_currency(self):
        """CAMT parser should extract currency from Ccy attribute on Amt element."""
        entries = (
            '<Ntry>'
            '<Amt Ccy="USD">500.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-15</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Wire Transfer</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
            '<Ntry>'
            '<Amt Ccy="EUR">300.00</Amt>'
            '<CdtDbtInd>CRDT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-16</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Euro Payment</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))

        assert len(transactions) == 2
        assert transactions[0].currency == "USD"
        assert transactions[1].currency == "EUR"

    def test_parse_camt_no_ccy_attribute(self):
        """CAMT entries without Ccy attribute should have currency=None."""
        entries = (
            '<Ntry>'
            '<Amt>100.00</Amt>'
            '<CdtDbtInd>DBIT</CdtDbtInd>'
            '<BookgDt><Dt>2026-01-15</Dt></BookgDt>'
            '<NtryDtls><TxDtls><RmtInf><Ustrd>Test</Ustrd></RmtInf></TxDtls></NtryDtls>'
            '</Ntry>'
        )
        transactions = parse_camt(self._make_camt_xml(entries))

        assert len(transactions) == 1
        assert transactions[0].currency is None


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT TRANSACTIONS WITH FX — INTEGRATION TESTS (mocked FX provider)
# ═══════════════════════════════════════════════════════════════════════════


async def _insert_fx_rate(session: AsyncSession, quote_currency: str, rate: Decimal, rate_date: date) -> None:
    """Insert a test FX rate (base=USD)."""
    fx = FxRate(base_currency="USD", quote_currency=quote_currency, date=rate_date, rate=rate, source="test")
    session.add(fx)
    await session.commit()


class TestImportTransactionsFx:
    """Tests for import_transactions with multi-currency and FX rate handling.

    All tests mock the external OER provider to avoid real API calls.
    """

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_import_with_fx_rate_from_csv(self, mock_provider, session: AsyncSession, test_user: User, test_account: Account):
        """When CSV provides fx_rate, it should be used directly without calling FX service."""
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        txns = [
            TransactionBase(
                description="Amazon US",
                amount=Decimal("100.00"),
                date=date(2026, 1, 15),
                type="debit",
                currency="USD",
                fx_rate=Decimal("5.25"),
            ),
        ]

        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "csv",
        )

        assert imported == 1
        assert skipped == 0

        # Verify the transaction was saved with correct FX fields
        result = await session.execute(
            select(Transaction).where(Transaction.description == "Amazon US")
        )
        tx = result.scalar_one()
        assert tx.currency == "USD"
        assert tx.fx_rate_used == Decimal("5.25")
        assert tx.amount_primary == Decimal("525.00")  # 100 * 5.25

        # Provider should NOT have been called since fx_rate was provided
        mock_provider.fetch_latest.assert_not_called()
        mock_provider.fetch_historical.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_import_foreign_currency_without_fx_rate_auto_converts(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """When no fx_rate is provided, stamp_primary_amount should auto-convert using DB rates."""
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        # Insert known FX rates so stamp_primary_amount can convert
        await _insert_fx_rate(session, "BRL", Decimal("5.0000"), date(2026, 1, 15))
        await _insert_fx_rate(session, "EUR", Decimal("0.9200"), date(2026, 1, 15))

        # Mock the provider to prevent real API calls during on-demand sync
        mock_provider.fetch_latest = AsyncMock(return_value={})
        mock_provider.fetch_historical = AsyncMock(return_value={})

        txns = [
            TransactionBase(
                description="Euro Store",
                amount=Decimal("100.00"),
                date=date(2026, 1, 15),
                type="debit",
                currency="EUR",
            ),
        ]

        imported, _, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "csv",
        )

        assert imported == 1

        result = await session.execute(
            select(Transaction).where(Transaction.description == "Euro Store")
        )
        tx = result.scalar_one()
        assert tx.currency == "EUR"
        # stamp_primary_amount should have converted EUR -> BRL
        # Rate: EUR/USD = 0.92, BRL/USD = 5.00 => EUR->BRL = 5.00/0.92 ≈ 5.4348
        assert tx.amount_primary is not None
        assert float(tx.amount_primary) > 500  # 100 EUR * ~5.43 = ~543

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_import_uses_account_currency_as_default(
        self, mock_provider, session: AsyncSession, test_user: User,
    ):
        """When transaction has no currency, the account's currency should be used."""
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        # Create a USD account
        usd_account = Account(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name="USD Checking",
            type="checking",
            balance=Decimal("5000.00"),
            currency="USD",
        )
        session.add(usd_account)
        await session.commit()
        await session.refresh(usd_account)

        # Insert FX rates for conversion
        await _insert_fx_rate(session, "BRL", Decimal("5.0000"), date(2026, 2, 10))

        mock_provider.fetch_latest = AsyncMock(return_value={})
        mock_provider.fetch_historical = AsyncMock(return_value={})

        txns = [
            TransactionBase(
                description="ATM Withdrawal",
                amount=Decimal("200.00"),
                date=date(2026, 2, 10),
                type="debit",
                # No currency set — should inherit from account
            ),
        ]

        imported, _, _ = await import_transactions(
            session, test_user.id, usd_account.id, txns, "csv",
        )

        assert imported == 1

        result = await session.execute(
            select(Transaction).where(Transaction.description == "ATM Withdrawal")
        )
        tx = result.scalar_one()
        # Should have inherited USD from the account
        assert tx.currency == "USD"

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_import_brl_into_brl_account_no_fx(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """Importing BRL transactions into a BRL account should not trigger FX conversion."""
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        txns = [
            TransactionBase(
                description="Supermercado",
                amount=Decimal("150.00"),
                date=date(2026, 3, 1),
                type="debit",
                # No currency — account is BRL, user primary is BRL
            ),
        ]

        imported, _, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "csv",
        )

        assert imported == 1

        result = await session.execute(
            select(Transaction).where(Transaction.description == "Supermercado")
        )
        tx = result.scalar_one()
        assert tx.currency == "BRL"
        # For same-currency (BRL->BRL), amount_primary should equal amount
        # (stamp_primary_amount returns 1:1 for same currency)

        # Provider should NOT have been called for same-currency import
        mock_provider.fetch_latest.assert_not_called()
        mock_provider.fetch_historical.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_import_csv_currency_overrides_account_currency(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """CSV-provided currency should take priority over account currency."""
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        await _insert_fx_rate(session, "BRL", Decimal("5.0000"), date(2026, 3, 5))
        await _insert_fx_rate(session, "GBP", Decimal("0.7900"), date(2026, 3, 5))

        mock_provider.fetch_latest = AsyncMock(return_value={})
        mock_provider.fetch_historical = AsyncMock(return_value={})

        txns = [
            TransactionBase(
                description="London Hotel",
                amount=Decimal("300.00"),
                date=date(2026, 3, 5),
                type="debit",
                currency="GBP",  # Explicit currency from CSV, account is BRL
            ),
        ]

        imported, _, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "csv",
        )

        assert imported == 1

        result = await session.execute(
            select(Transaction).where(Transaction.description == "London Hotel")
        )
        tx = result.scalar_one()
        # Currency from CSV should override account currency
        assert tx.currency == "GBP"
        assert tx.amount_primary is not None


# ═══════════════════════════════════════════════════════════════════════════
# CSV TYPE COLUMN TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestParseCsvTypeColumn:
    """Tests for explicit 'type' column support in parse_csv.

    Before this fix, parse_csv always derived type from the amount sign.
    Now, when a 'type' column is present with 'credit' or 'debit', it is
    used directly — enabling all-positive-amount CSVs (like Securo exports).
    """

    def test_explicit_type_column_overrides_amount_sign(self):
        """Positive amounts with type=debit should produce debit transactions."""
        csv_content = (
            "date,description,amount,type\n"
            "2026-01-05,Salario Dia 5,13311.00,credit\n"
            "2026-01-01,Financiamento Casa,577.00,debit\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].type == "credit"
        assert transactions[0].amount == Decimal("13311.00")
        assert transactions[1].type == "debit"
        assert transactions[1].amount == Decimal("577.00")

    def test_explicit_type_column_all_debit(self):
        """All-positive CSV with type=debit should not become credit."""
        csv_content = (
            "date,description,amount,type\n"
            "2026-01-01,Rent,1200.00,debit\n"
            "2026-01-02,Internet,119.00,debit\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert all(t.type == "debit" for t in transactions)

    def test_without_type_column_derives_from_sign(self):
        """Without a type column, backward-compatible sign-based derivation applies."""
        csv_content = (
            "date,description,amount\n"
            "2026-01-01,Expense,-100.00\n"
            "2026-01-02,Income,500.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert transactions[0].type == "debit"
        assert transactions[1].type == "credit"

    def test_unknown_type_value_falls_back_to_sign(self):
        """Unrecognized type values (not credit/debit) fall back to amount sign."""
        csv_content = (
            "date,description,amount,type\n"
            "2026-01-01,Expense,-100.00,unknown\n"
            "2026-01-02,Income,500.00,invalid\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert transactions[0].type == "debit"
        assert transactions[1].type == "credit"


# ═══════════════════════════════════════════════════════════════════════════
# CSV CATEGORY COLUMN TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestParseCsvCategoryColumn:
    """Tests for 'category' column support in parse_csv.

    The category column value is stored as category_name on TransactionBase
    and later resolved to a UUID by import_transactions.
    """

    def test_category_column_populates_category_name(self):
        """CSV with a category column should set category_name on each transaction."""
        csv_content = (
            "date,description,amount,type,currency,category\n"
            "2026-01-05,Salario Dia 5,13311.00,credit,BRL,Salário & Renda\n"
            "2026-01-01,Financiamento Casa,577.00,debit,BRL,Moradia\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 2
        assert transactions[0].category_name == "Salário & Renda"
        assert transactions[1].category_name == "Moradia"

    def test_without_category_column_leaves_none(self):
        """CSV without category column should leave category_name as None."""
        csv_content = (
            "date,description,amount\n"
            "2026-01-01,Grocery Store,-50.00\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert transactions[0].category_name is None

    def test_empty_category_field_leaves_none(self):
        """An empty category field should result in category_name = None."""
        csv_content = (
            "date,description,amount,type,currency,category\n"
            "2026-01-01,Some Transaction,100.00,credit,BRL,\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert transactions[0].category_name is None

    def test_portuguese_categoria_column_detected(self):
        """Portuguese 'categoria' column should also be detected."""
        csv_content = (
            "data,descricao,valor,categoria\n"
            "01/01/2026,Supermercado,-150.00,Alimentação\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert len(transactions) == 1
        assert transactions[0].category_name == "Alimentação"


# ═══════════════════════════════════════════════════════════════════════════
# IMPORT TRANSACTIONS WITH CATEGORY RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════


class TestImportTransactionsWithCategory:
    """Tests for category_name → category_id resolution in import_transactions."""

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_known_category_name_resolved_to_id(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """When category_name matches a user category, category_id should be set."""
        from app.models.category import Category
        from app.models.transaction import Transaction
        from sqlalchemy import select

        category = Category(
            id=uuid.uuid4(), user_id=test_user.id,
            name="Salário & Renda", icon="banknote", color="#16A34A",
        )
        session.add(category)
        await session.commit()

        from app.schemas.transaction import TransactionImport
        txns = [TransactionImport(
            description="Salario Dia 5",
            amount=Decimal("13311.00"),
            date=date(2026, 1, 5),
            type="credit",
            category_name="Salário & Renda",
        )]

        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "import",
        )

        assert imported == 1
        assert skipped == 0

        tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Salario Dia 5")
        )).scalar_one()
        assert tx.category_id == category.id

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_unknown_category_name_leaves_uncategorized(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """When category_name has no match, category_id should be None."""
        from app.schemas.transaction import TransactionImport
        from app.models.transaction import Transaction
        from sqlalchemy import select

        txns = [TransactionImport(
            description="Unknown Cat Transaction",
            amount=Decimal("100.00"),
            date=date(2026, 1, 10),
            type="debit",
            category_name="Categoria Inexistente",
        )]

        imported, _, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "import",
        )

        assert imported == 1

        tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Unknown Cat Transaction")
        )).scalar_one()
        assert tx.category_id is None

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_no_category_name_leaves_uncategorized(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """When category_name is None, category_id should be None."""
        from app.schemas.transaction import TransactionImport
        from app.models.transaction import Transaction
        from sqlalchemy import select

        txns = [TransactionImport(
            description="No Cat Transaction",
            amount=Decimal("50.00"),
            date=date(2026, 1, 15),
            type="debit",
        )]

        imported, _, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "import",
        )

        assert imported == 1

        tx = (await session.execute(
            select(Transaction).where(Transaction.description == "No Cat Transaction")
        )).scalar_one()
        assert tx.category_id is None

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_multiple_categories_resolved_correctly(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """Multiple transactions with different categories should each resolve correctly."""
        from app.models.category import Category
        from app.models.transaction import Transaction
        from app.schemas.transaction import TransactionImport
        from sqlalchemy import select

        salary_cat = Category(
            id=uuid.uuid4(), user_id=test_user.id,
            name="Salário & Renda", icon="banknote", color="#16A34A",
        )
        housing_cat = Category(
            id=uuid.uuid4(), user_id=test_user.id,
            name="Moradia", icon="house", color="#8B5CF6",
        )
        session.add(salary_cat)
        session.add(housing_cat)
        await session.commit()

        txns = [
            TransactionImport(
                description="Salario",
                amount=Decimal("13311.00"),
                date=date(2026, 1, 5),
                type="credit",
                category_name="Salário & Renda",
            ),
            TransactionImport(
                description="Financiamento",
                amount=Decimal("577.00"),
                date=date(2026, 1, 1),
                type="debit",
                category_name="Moradia",
            ),
            TransactionImport(
                description="Unknown",
                amount=Decimal("100.00"),
                date=date(2026, 1, 2),
                type="debit",
                category_name="Categoria Inexistente",
            ),
        ]

        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, txns, "import",
        )

        assert imported == 3
        assert skipped == 0

        salary_tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Salario")
        )).scalar_one()
        housing_tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Financiamento")
        )).scalar_one()
        unknown_tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Unknown")
        )).scalar_one()

        assert salary_tx.category_id == salary_cat.id
        assert housing_tx.category_id == housing_cat.id
        assert unknown_tx.category_id is None

    @pytest.mark.asyncio
    @patch("app.services.fx_rate_service._provider")
    async def test_end_to_end_parse_and_import_with_type_and_category(
        self, mock_provider, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """Full flow: parse_csv reads type+category columns, import_transactions resolves them."""
        from app.models.category import Category
        from app.models.transaction import Transaction
        from sqlalchemy import select

        salary_cat = Category(
            id=uuid.uuid4(), user_id=test_user.id,
            name="Salário & Renda", icon="banknote", color="#16A34A",
        )
        housing_cat = Category(
            id=uuid.uuid4(), user_id=test_user.id,
            name="Moradia", icon="house", color="#8B5CF6",
        )
        session.add(salary_cat)
        session.add(housing_cat)
        await session.commit()

        csv_content = (
            "date,description,amount,type,currency,category\n"
            "2026-01-05,Salario Dia 5,13311.00,credit,BRL,Salário & Renda\n"
            "2026-01-01,Financiamento Casa,577.00,debit,BRL,Moradia\n"
        )
        transactions = parse_csv(csv_content.encode("utf-8"))

        assert transactions[0].type == "credit"
        assert transactions[1].type == "debit"
        assert transactions[0].category_name == "Salário & Renda"
        assert transactions[1].category_name == "Moradia"

        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, transactions, "import",
        )

        assert imported == 2
        assert skipped == 0

        salary_tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Salario Dia 5")
        )).scalar_one()
        housing_tx = (await session.execute(
            select(Transaction).where(Transaction.description == "Financiamento Casa")
        )).scalar_one()

        assert salary_tx.type == "credit"
        assert salary_tx.category_id == salary_cat.id
        assert housing_tx.type == "debit"
        assert housing_tx.category_id == housing_cat.id


class TestOfxInstallmentDedup:
    """Brazilian credit-card installments share one FITID across all monthly
    statements (issue #98). Deduplication must consider the date so that
    later monthly imports still register the next installment."""

    @pytest.mark.asyncio
    async def test_same_external_id_different_dates_both_imported(
        self, session: AsyncSession, test_user: User, test_account: Account,
    ):
        from app.schemas.transaction import TransactionBase
        from app.models.transaction import Transaction
        from sqlalchemy import select

        first = [
            TransactionBase(
                description="Nimbus Stay - Parcela 1/6",
                amount=Decimal("100.00"),
                date=date(2025, 12, 15),
                type="debit",
                external_id="PURCHASE_ABC123",
            ),
        ]
        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, first, "ofx",
        )
        assert imported == 1
        assert skipped == 0

        second = [
            TransactionBase(
                description="Nimbus Stay - Parcela 2/6",
                amount=Decimal("100.00"),
                date=date(2026, 1, 15),
                type="debit",
                external_id="PURCHASE_ABC123",  # bank reuses purchase FITID
            ),
        ]
        imported2, skipped2, _ = await import_transactions(
            session, test_user.id, test_account.id, second, "ofx",
        )
        assert imported2 == 1
        assert skipped2 == 0

        rows = (await session.execute(
            select(Transaction).where(Transaction.external_id == "PURCHASE_ABC123")
        )).scalars().all()
        assert len(rows) == 2
        assert {tx.date for tx in rows} == {date(2025, 12, 15), date(2026, 1, 15)}

    @pytest.mark.asyncio
    async def test_same_external_id_same_date_dedups(
        self, session: AsyncSession, test_user: User, test_account: Account,
    ):
        """Re-importing the same OFX file must still dedup — same FITID + same
        date is the strict duplicate case."""
        from app.schemas.transaction import TransactionBase

        txn = TransactionBase(
            description="Padaria",
            amount=Decimal("12.50"),
            date=date(2026, 2, 10),
            type="debit",
            external_id="DEDUP_ME",
        )
        await import_transactions(session, test_user.id, test_account.id, [txn], "ofx")
        imported, skipped, _ = await import_transactions(
            session, test_user.id, test_account.id, [txn], "ofx",
        )
        assert imported == 0
        assert skipped == 1
