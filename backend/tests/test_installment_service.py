import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.installment_service import (
    detect_installment,
    _add_months,
    _build_purchase,
    _is_manual_installment,
    _make_purchase_id,
    _purchase_key,
    _resolve_merchant_name,
)


class TestDetectInstallment:
    def test_detect_valid_installment(self):
        assert detect_installment("Compra 1/12") == (1, 12)
        assert detect_installment("Parcela 3/6") == (3, 6)
        assert detect_installment("10/24") == (10, 24)

    def test_detect_with_spaces(self):
        assert detect_installment("Compra 1 / 12") == (1, 12)
        assert detect_installment("Parcela 3 / 6") == (3, 6)

    def test_detect_no_installment(self):
        assert detect_installment("Compra normal") is None
        assert detect_installment("12345") is None
        assert detect_installment("") is None

    def test_detect_invalid_range(self):
        assert detect_installment("0/12") is None
        assert detect_installment("13/12") is None
        assert detect_installment("1/61") is None

    def test_detect_edge_cases(self):
        assert detect_installment("1/1") == (1, 1)
        assert detect_installment("60/60") == (60, 60)
        assert detect_installment("1/60") == (1, 60)


class TestAddMonths:
    def test_add_months_basic(self):
        assert _add_months(date(2024, 1, 15), 1) == date(2024, 2, 15)
        assert _add_months(date(2024, 1, 15), 3) == date(2024, 4, 15)

    def test_add_months_year_boundary(self):
        assert _add_months(date(2024, 12, 15), 1) == date(2025, 1, 15)
        assert _add_months(date(2024, 11, 15), 3) == date(2025, 2, 15)

    def test_add_months_negative(self):
        assert _add_months(date(2024, 3, 15), -1) == date(2024, 2, 15)
        assert _add_months(date(2024, 1, 15), -1) == date(2023, 12, 15)

    def test_add_months_day_overflow(self):
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
        assert _add_months(date(2024, 1, 31), 2) == date(2024, 3, 31)

    def test_add_months_leap_year(self):
        assert _add_months(date(2024, 1, 29), 1) == date(2024, 2, 29)
        assert _add_months(date(2023, 1, 29), 1) == date(2023, 2, 28)


class TestPurchaseKeyAndId:
    def test_purchase_key(self, test_account: Account):
        tx = Transaction(
            id=uuid.uuid4(),
            account_id=test_account.id,
            installment_purchase_date=date(2024, 1, 1),
            amount=Decimal("100"),
        )
        key = _purchase_key(tx)
        assert key == (test_account.id, date(2024, 1, 1))

    def test_make_purchase_id(self):
        key = (uuid.uuid4(), date(2024, 1, 1))
        pid = _make_purchase_id(key)
        assert isinstance(pid, uuid.UUID)
        # Same key should produce same id
        assert _make_purchase_id(key) == pid

    def test_make_purchase_id_different_keys(self):
        key1 = (uuid.uuid4(), date(2024, 1, 1))
        key2 = (uuid.uuid4(), date(2024, 1, 1))
        assert _make_purchase_id(key1) != _make_purchase_id(key2)


class TestResolveMerchantName:
    def test_resolve_from_payees(self):
        tx1 = Transaction(id=uuid.uuid4(), payee="Store A", description="Desc1", amount=Decimal("100"))
        tx2 = Transaction(id=uuid.uuid4(), payee="Store A", description="Desc2", amount=Decimal("100"))
        assert _resolve_merchant_name([tx1, tx2]) == "Store A"

    def test_resolve_most_common_payee(self):
        tx1 = Transaction(id=uuid.uuid4(), payee="Store A", description="Desc1", amount=Decimal("100"))
        tx2 = Transaction(id=uuid.uuid4(), payee="Store B", description="Desc2", amount=Decimal("100"))
        tx3 = Transaction(id=uuid.uuid4(), payee="Store A", description="Desc3", amount=Decimal("100"))
        assert _resolve_merchant_name([tx1, tx2, tx3]) == "Store A"

    def test_resolve_from_description(self):
        tx1 = Transaction(id=uuid.uuid4(), payee=None, description="Purchase at Store", amount=Decimal("100"))
        assert _resolve_merchant_name([tx1]) == "Purchase at Store"

    def test_resolve_unknown(self):
        tx1 = Transaction(id=uuid.uuid4(), payee=None, description=None, amount=Decimal("100"))
        assert _resolve_merchant_name([tx1]) == "Unknown"


class TestIsManualInstallment:
    def test_manual_installment(self):
        tx = Transaction(id=uuid.uuid4(), notes=json.dumps({"manual_installment": True}))
        assert _is_manual_installment(tx) is True

    def test_not_manual(self):
        tx = Transaction(id=uuid.uuid4(), notes=json.dumps({"manual_installment": False}))
        assert _is_manual_installment(tx) is False

    def test_no_notes(self):
        tx = Transaction(id=uuid.uuid4(), notes=None)
        assert _is_manual_installment(tx) is False

    def test_invalid_json(self):
        tx = Transaction(id=uuid.uuid4(), notes="not json")
        assert _is_manual_installment(tx) is False

    def test_no_manual_key(self):
        tx = Transaction(id=uuid.uuid4(), notes=json.dumps({"other": "data"}))
        assert _is_manual_installment(tx) is False
