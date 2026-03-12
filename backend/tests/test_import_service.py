from datetime import date
from decimal import Decimal

import pytest

from app.services.import_service import parse_csv, parse_ofx, parse_qif, parse_camt


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
        """CSV with unrecognized column names should raise ValueError."""
        csv_content = (
            "col_a,col_b,col_c\n"
            "foo,bar,baz\n"
        )
        with pytest.raises(ValueError, match="Could not detect CSV columns"):
            parse_csv(csv_content.encode("utf-8"))

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
