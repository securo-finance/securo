"""Regression tests for TransactionUpdate schema.

Ensures the Pydantic field-name/type-name collision for `date: Optional[date]`
does not resurface. See: Pydantic V2 metaclass resolves the `date` type annotation
to NoneType when the field name is also `date` and the type is Optional.
"""

import uuid
from datetime import date
from decimal import Decimal

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

    def test_accepts_none(self):
        data = TransactionUpdate.model_validate({"date": None})
        assert data.date is None

    def test_unset_date_excluded(self):
        data = TransactionUpdate.model_validate({"description": "test"})
        dumped = data.model_dump(exclude_unset=True)
        assert "date" not in dumped

    def test_set_date_included(self):
        data = TransactionUpdate.model_validate({"date": "2026-03-15"})
        dumped = data.model_dump(exclude_unset=True)
        assert dumped["date"] == date(2026, 3, 15)

    def test_rejects_invalid_date_string(self):
        with pytest.raises(ValidationError):
            TransactionUpdate.model_validate({"date": "not-a-date"})


class TestTransactionUpdateAllFields:
    """Verify TransactionUpdate accepts all editable fields together."""

    def test_all_fields_accepted(self):
        data = TransactionUpdate.model_validate({
            "description": "Updated description",
            "amount": "250.00",
            "date": "2026-06-01",
            "type": "credit",
            "currency": "USD",
            "category_id": "11111111-1111-1111-1111-111111111111",
        })
        assert data.description == "Updated description"
        assert data.amount == Decimal("250.00")
        assert data.date == date(2026, 6, 1)
        assert data.type == "credit"
        assert data.currency == "USD"
        assert str(data.category_id) == "11111111-1111-1111-1111-111111111111"

    def test_partial_update_only_type(self):
        data = TransactionUpdate.model_validate({"type": "credit"})
        dumped = data.model_dump(exclude_unset=True)
        assert dumped == {"type": "credit"}

    def test_partial_update_amount_and_currency(self):
        data = TransactionUpdate.model_validate({"amount": "99.99", "currency": "EUR"})
        dumped = data.model_dump(exclude_unset=True)
        assert dumped == {"amount": Decimal("99.99"), "currency": "EUR"}


class TestTransactionReadInstallmentFields:
    """Verify the installment metadata fields (issue #14 v1) round-trip
    through TransactionRead without loss."""

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

    def test_all_installment_fields_round_trip(self):
        series_id = uuid.uuid4()
        data = TransactionRead.model_validate(self._base(
            installment_number=3,
            total_installments=12,
            installment_total_amount="1446.00",
            installment_purchase_date="2026-02-10",
            installment_series_id=series_id,
        ))
        assert data.installment_number == 3
        assert data.total_installments == 12
        assert data.installment_total_amount == 1446.00
        assert data.installment_purchase_date == date(2026, 2, 10)
        assert data.installment_series_id == series_id

    def test_installment_fields_default_none(self):
        data = TransactionRead.model_validate(self._base())
        assert data.installment_number is None
        assert data.total_installments is None
        assert data.installment_total_amount is None
        assert data.installment_purchase_date is None
        assert data.installment_series_id is None

    def test_installment_fields_serialize_in_api_response(self):
        data = TransactionRead.model_validate(self._base(
            installment_number=1,
            total_installments=6,
            installment_total_amount="300.00",
            installment_purchase_date="2026-03-25",
        ))
        dumped = data.model_dump(mode="json")
        assert dumped["installment_number"] == 1
        assert dumped["total_installments"] == 6
        assert dumped["installment_total_amount"] == 300.00
        assert dumped["installment_purchase_date"] == "2026-03-25"

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

    def test_accepts_all_four_installment_fields(self):
        data = TransactionCreate.model_validate({
            **self.BASE,
            "installment_number": 2,
            "total_installments": 10,
            "installment_total_amount": "10000.00",
            "installment_purchase_date": "2026-01-10",
        })
        assert data.installment_number == 2
        assert data.total_installments == 10
        assert data.installment_total_amount == Decimal("10000.00")
        assert data.installment_purchase_date == date(2026, 1, 10)

    def test_omitting_installment_fields_is_fine(self):
        data = TransactionCreate.model_validate(self.BASE)
        assert data.installment_number is None

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

    def test_accepts_all_scopes(self):
        for scope in ("this", "future", "all"):
            data = TransactionUpdate.model_validate({"apply_to": scope})
            assert data.apply_to == scope

    def test_rejects_unknown_scope(self):
        with pytest.raises(ValidationError):
            TransactionUpdate.model_validate({"apply_to": "yesterday"})


class TestInstallmentSeriesCreate:
    """Validate the series payload for POST /api/transactions/installments."""

    BASE = {
        "description": "Notebook",
        "amount": "500.00",
        "date": "2026-04-01",
        "type": "debit",
        "account_id": "11111111-1111-1111-1111-111111111111",
    }

    def test_accepts_minimal_payload(self):
        data = InstallmentSeriesCreate.model_validate(
            {"base": self.BASE, "installments": 3}
        )
        assert data.installments == 3
        assert data.first_installment_status == "posted"
        assert data.frequency == "monthly"

    def test_accepts_all_options(self):
        data = InstallmentSeriesCreate.model_validate({
            "base": self.BASE,
            "installments": 12,
            "first_installment_status": "pending",
            "frequency": "yearly",
        })
        assert data.first_installment_status == "pending"
        assert data.frequency == "yearly"


    @pytest.mark.parametrize("frequency", ["biweekly", "semiannual"])
    def test_installment_series_accepts_new_frequencies(self, frequency):
        data = InstallmentSeriesCreate.model_validate({
            "base": self.BASE,
            "installments": 3,
            "frequency": frequency,
        })
        assert data.frequency == frequency
        assert data.model_dump(mode="json")["frequency"] == frequency

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
