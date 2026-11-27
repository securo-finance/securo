"""Regression tests for TransactionUpdate schema.

Ensures the Pydantic field-name/type-name collision for `date: Optional[date]`
does not resurface. See: Pydantic V2 metaclass resolves the `date` type annotation
to NoneType when the field name is also `date` and the type is Optional.
"""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.transaction import (
    InstallmentSeriesCreate,
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)


class TestTransactionUpdateDateField:
    """Verify the `date` field on TransactionUpdate accepts valid date strings."""

    def test_accepts_iso_date_string(self):
        data = TransactionUpdate.model_validate({"date": "2026-02-25"})
        assert data.date == date(2026, 2, 25)


class TestTransactionReadProviderData:
    """TransactionRead exposes the bank description, never the raw provider payload."""

    def _base(self, **overrides):
        data = {
            "id": "11111111-1111-1111-1111-111111111111",
            "user_id": "22222222-2222-2222-2222-222222222222",
            "description": "AMAZON PARCELADO",
            "amount": "120.50",
            "date": "2026-04-10",
            "type": "debit",
            "source": "pluggy",
        }
        data.update(overrides)
        return data

    def test_exposes_original_description_without_raw_provider_data(self):
        data = TransactionRead.model_validate(
            self._base(
                description="iFood",
                original_description="|fd*f|ood Club",
                raw_data={"provider_secret": "not-safe"},
                description_is_rule_managed=True,
            )
        )
        dumped = data.model_dump(mode="json")

        assert dumped["original_description"] == "|fd*f|ood Club"
        assert "raw_data" not in dumped
        assert "description_is_rule_managed" not in dumped


class TestTransactionCreateDateField:
    """Ensure TransactionCreate also handles the date field correctly."""

    def test_accepts_iso_date_string(self):
        data = TransactionCreate.model_validate({
            "description": "Test",
            "amount": "10.00",
            "date": "2026-02-25",
            "type": "debit",
            "account_id": "11111111-1111-1111-1111-111111111111",
        })
        assert data.date == date(2026, 2, 25)


class TestTransactionCreateInstallmentFields:
    """Verify installment metadata on TransactionCreate."""

    BASE = {
        "description": "TV 55",
        "amount": "1000.00",
        "date": "2026-03-10",
        "type": "debit",
        "account_id": "11111111-1111-1111-1111-111111111111",
    }

    def test_rejects_partial_installment_set(self):
        with pytest.raises(ValidationError, match="must be provided together"):
            TransactionCreate.model_validate({
                **self.BASE,
                "installment_number": 1,
                "total_installments": 10,
            })

    def test_rejects_installment_number_out_of_range(self):
        with pytest.raises(ValidationError, match="between 1 and total_installments"):
            TransactionCreate.model_validate({
                **self.BASE,
                "installment_number": 11,
                "total_installments": 10,
                "installment_total_amount": "10000.00",
                "installment_purchase_date": "2026-01-10",
            })

    def test_rejects_negative_installment_total(self):
        with pytest.raises(ValidationError, match="must be positive"):
            TransactionCreate.model_validate({
                **self.BASE,
                "installment_number": 1,
                "total_installments": 2,
                "installment_total_amount": "-1.00",
                "installment_purchase_date": "2026-03-10",
            })

    def test_rejects_purchase_date_after_transaction_date(self):
        with pytest.raises(ValidationError, match="cannot be after"):
            TransactionCreate.model_validate({
                **self.BASE,
                "installment_number": 1,
                "total_installments": 2,
                "installment_total_amount": "2000.00",
                "installment_purchase_date": "2026-03-11",
            })


class TestTransactionUpdateApplyTo:
    """Verify the installment-series edit scope field."""

    def test_defaults_to_this(self):
        data = TransactionUpdate.model_validate({"description": "x"})
        assert data.apply_to == "this"


class TestInstallmentSeriesCreate:
    """Validate the series payload for POST /api/transactions/installments."""

    BASE = {
        "description": "Notebook",
        "amount": "500.00",
        "date": "2026-04-01",
        "type": "debit",
        "account_id": "11111111-1111-1111-1111-111111111111",
    }

    def test_rejects_single_installment(self):
        with pytest.raises(ValidationError):
            InstallmentSeriesCreate.model_validate(
                {"base": self.BASE, "installments": 1}
            )

    def test_rejects_too_many_installments(self):
        with pytest.raises(ValidationError):
            InstallmentSeriesCreate.model_validate(
                {"base": self.BASE, "installments": 361}
            )

    def test_rejects_negative_base_amount(self):
        with pytest.raises(ValidationError, match="amount must be positive"):
            InstallmentSeriesCreate.model_validate({
                "base": {**self.BASE, "amount": "-5.00"},
                "installments": 2,
            })


class TestCurrencyNormalization:
    """A lower-case code is upper-cased on the way in.

    Every account-currency aggregation compares the row's currency to the
    account's, case-sensitively, so a `brl` row in a BRL account would land in
    the unconverted-foreign branch and count zero (#1263).
    """

    BASE = {
        "description": "Coffee",
        "amount": "10.00",
        "date": "2026-02-25",
        "type": "debit",
        "account_id": str(uuid.uuid4()),
    }

    def test_create_uppercases_currency(self):
        data = TransactionCreate.model_validate({**self.BASE, "currency": "brl"})
        assert data.currency == "BRL"

    def test_create_keeps_an_upper_case_currency(self):
        data = TransactionCreate.model_validate({**self.BASE, "currency": "CLP"})
        assert data.currency == "CLP"

    def test_update_uppercases_currency(self):
        data = TransactionUpdate.model_validate({"currency": "clp"})
        assert data.currency == "CLP"

