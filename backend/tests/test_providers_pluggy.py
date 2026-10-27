"""Parser tests for the Pluggy provider, focused on the
`creditCardMetadata` → `TransactionData` mapping introduced with the
installment-metadata v1 feature (issue #14).

These tests exercise `PluggyProvider.get_transactions` with an httpx
client stubbed out, so no network traffic happens.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.pluggy import PluggyProvider


def _mock_httpx_client(results: list[dict]) -> MagicMock:
    """Build a MagicMock that behaves like an `httpx.AsyncClient` context
    manager whose `.get()` returns a single page of `results`."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"results": results, "totalPages": 1})

    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def _fetch(txns: list[dict]):
    provider = PluggyProvider()
    fake_client = _mock_httpx_client(txns)
    with patch.object(
        PluggyProvider, "_ensure_api_key", new=AsyncMock(return_value="fake-key")
    ), patch("app.providers.pluggy.httpx.AsyncClient", return_value=fake_client):
        return await provider.get_transactions({"item_id": "i"}, "acc-ext-1")


@pytest.mark.asyncio
async def test_parser_captures_full_installment_metadata():
    """Happy path: all 4 creditCardMetadata fields flow into TransactionData."""
    result = await _fetch([
        {
            "id": "tx-1",
            "description": "AMAZON PARCELADO",
            "amount": -120.50,
            "date": "2026-04-10",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": 3,
                "totalInstallments": 12,
                "totalAmount": 1446.00,
                "purchaseDate": "2026-02-10",
            },
        }
    ])
    assert len(result) == 1
    tx = result[0]
    assert tx.installment_number == 3
    assert tx.total_installments == 12
    assert tx.installment_total_amount == Decimal("1446.00")
    assert tx.installment_purchase_date == date(2026, 2, 10)


@pytest.mark.asyncio
async def test_parser_no_credit_card_metadata_leaves_fields_none():
    """Non-CC txns (no creditCardMetadata) get null installment fields."""
    result = await _fetch([
        {
            "id": "tx-2",
            "description": "GROCERIES",
            "amount": -30.00,
            "date": "2026-04-11",
            "type": "DEBIT",
        }
    ])
    tx = result[0]
    assert tx.installment_number is None
    assert tx.total_installments is None
    assert tx.installment_total_amount is None
    assert tx.installment_purchase_date is None


@pytest.mark.asyncio
async def test_parser_empty_credit_card_metadata():
    """`creditCardMetadata: {}` should yield all-null installment fields."""
    result = await _fetch([
        {
            "id": "tx-3",
            "description": "SINGLE CHARGE",
            "amount": -50.00,
            "date": "2026-04-11",
            "type": "DEBIT",
            "creditCardMetadata": {},
        }
    ])
    tx = result[0]
    assert tx.installment_number is None
    assert tx.total_installments is None
    assert tx.installment_total_amount is None
    assert tx.installment_purchase_date is None


@pytest.mark.asyncio
async def test_parser_null_credit_card_metadata():
    """`creditCardMetadata: null` should be handled like missing."""
    result = await _fetch([
        {
            "id": "tx-4",
            "description": "NULL META",
            "amount": -10,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": None,
        }
    ])
    tx = result[0]
    assert tx.installment_number is None
    assert tx.installment_total_amount is None


@pytest.mark.asyncio
async def test_parser_invalid_installment_number_types_coerce_to_none():
    """Non-integer installmentNumber/totalInstallments must not break parsing."""
    result = await _fetch([
        {
            "id": "tx-5",
            "description": "BAD TYPES",
            "amount": -1,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": "3",  # string, not int
                "totalInstallments": 12.0,  # float, not int
                "totalAmount": 100,
                "purchaseDate": "2026-04-01",
            },
        }
    ])
    tx = result[0]
    assert tx.installment_number is None
    assert tx.total_installments is None
    assert tx.installment_total_amount == Decimal("100")
    assert tx.installment_purchase_date == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_parser_malformed_purchase_date_falls_back_to_none():
    """Invalid purchaseDate strings should not raise — silently drop."""
    result = await _fetch([
        {
            "id": "tx-6",
            "description": "BAD DATE",
            "amount": -1,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": 1,
                "totalInstallments": 2,
                "totalAmount": 2,
                "purchaseDate": "not-a-date",
            },
        }
    ])
    tx = result[0]
    assert tx.installment_purchase_date is None
    assert tx.installment_number == 1
    assert tx.total_installments == 2


@pytest.mark.asyncio
async def test_parser_purchase_date_with_time_suffix():
    """ISO datetime strings (with time) should be truncated to date cleanly."""
    result = await _fetch([
        {
            "id": "tx-7",
            "description": "WITH TIME",
            "amount": -1,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": 1,
                "totalInstallments": 1,
                "totalAmount": 10,
                "purchaseDate": "2026-01-15T12:34:56.000Z",
            },
        }
    ])
    tx = result[0]
    assert tx.installment_purchase_date == date(2026, 1, 15)


@pytest.mark.asyncio
async def test_parser_negative_total_amount_is_stored_as_absolute():
    """Pluggy may report negative totalAmount for debits; we store absolute."""
    result = await _fetch([
        {
            "id": "tx-8",
            "description": "NEG TOTAL",
            "amount": -10,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": 2,
                "totalInstallments": 6,
                "totalAmount": -600.00,
                "purchaseDate": "2026-01-01",
            },
        }
    ])
    tx = result[0]
    assert tx.installment_total_amount == Decimal("600.00")


@pytest.mark.asyncio
async def test_parser_captures_bill_external_id():
    """`creditCardMetadata.billId` flows into TransactionData.bill_external_id —
    the sync layer resolves it to a credit_card_bills FK (issue #92)."""
    result = await _fetch([
        {
            "id": "tx-bill-1",
            "description": "RESTAURANT",
            "amount": -50.00,
            "date": "2026-04-10",
            "type": "DEBIT",
            "creditCardMetadata": {"billId": "bill-abc-123"},
        }
    ])
    assert result[0].bill_external_id == "bill-abc-123"


@pytest.mark.asyncio
async def test_parser_no_bill_id_leaves_field_none():
    result = await _fetch([
        {
            "id": "tx-no-bill",
            "description": "X",
            "amount": -10,
            "date": "2026-04-10",
            "type": "DEBIT",
            "creditCardMetadata": {"installmentNumber": 1, "totalInstallments": 1},
        }
    ])
    assert result[0].bill_external_id is None


@pytest.mark.asyncio
async def test_parser_bill_id_coerced_to_string():
    """Defensive: providers may emit numeric bill ids; column is String(255)."""
    result = await _fetch([
        {
            "id": "tx-num-bill",
            "description": "X",
            "amount": -10,
            "date": "2026-04-10",
            "type": "DEBIT",
            "creditCardMetadata": {"billId": 999},
        }
    ])
    assert result[0].bill_external_id == "999"


@pytest.mark.asyncio
async def test_parser_missing_purchase_date_only():
    """Some connectors omit purchaseDate even when counts are present."""
    result = await _fetch([
        {
            "id": "tx-9",
            "description": "NO PURCHASE DATE",
            "amount": -25,
            "date": "2026-04-12",
            "type": "DEBIT",
            "creditCardMetadata": {
                "installmentNumber": 4,
                "totalInstallments": 10,
                "totalAmount": 250,
            },
        }
    ])
    tx = result[0]
    assert tx.installment_number == 4
    assert tx.total_installments == 10
    assert tx.installment_total_amount == Decimal("250")
    assert tx.installment_purchase_date is None


# ---------------------------------------------------------------------------
# Future installments reported at the purchase date (Mercado Pago)
# ---------------------------------------------------------------------------


def _installment(number: int, total: int, txn_date: str, purchase_date: str) -> dict:
    return {
        "id": f"tx-{number}",
        "description": "MERCADOLIVRE*MERCADOLIVRE",
        "amount": -56.99,
        "date": txn_date,
        "type": "DEBIT",
        "status": "PENDING",
        "creditCardMetadata": {
            "installmentNumber": number,
            "totalInstallments": total,
            "purchaseDate": purchase_date,
        },
    }


async def test_future_installment_at_purchase_date_moves_to_its_month():
    """Installment 5 of a 6 June purchase belongs in October, not June."""
    result = await _fetch([
        _installment(5, 10, "2026-06-06T13:26:40.000Z", "2026-06-06T13:26:40.000Z"),
    ])
    assert result[0].date == date(2026, 10, 6)
    assert result[0].installment_purchase_date == date(2026, 6, 6)


async def test_future_installment_crosses_into_the_next_year():
    result = await _fetch([
        _installment(18, 18, "2025-11-01T14:49:23.000Z", "2025-11-01T14:49:23.000Z"),
    ])
    assert result[0].date == date(2027, 4, 1)


async def test_future_installment_day_is_clamped_to_a_shorter_month():
    result = await _fetch([
        _installment(2, 3, "2026-01-31", "2026-01-31"),
    ])
    assert result[0].date == date(2026, 2, 28)


async def test_first_installment_keeps_the_purchase_date():
    result = await _fetch([
        _installment(1, 10, "2026-06-06T13:26:40.000Z", "2026-06-06T13:26:40.000Z"),
    ])
    assert result[0].date == date(2026, 6, 6)


async def test_installment_the_connector_already_dated_is_kept():
    """A date of the connector's own is left alone, even when it is not
    exactly the purchase day N-1 months on."""
    result = await _fetch([
        _installment(2, 10, "2026-07-12T03:00:00.000Z", "2026-06-06T13:26:40.000Z"),
    ])
    assert result[0].date == date(2026, 7, 12)


async def test_scheduled_installment_on_its_purchase_day_is_kept():
    """Nubank sometimes reports a scheduled installment with a purchaseDate on
    the installment's own day. Same day, different timestamp: it is already
    in its month, and moving it would push it N-1 months too far."""
    result = await _fetch([
        _installment(3, 5, "2026-11-10T03:00:00.000Z", "2026-11-10T00:00:00.001Z"),
    ])
    assert result[0].date == date(2026, 11, 10)


async def test_posted_installment_at_its_purchase_date_is_kept():
    """Itaú sends billed installments with a purchaseDate equal to their own
    charge date. A posted row is the bank's record of when it was charged."""
    installment = _installment(8, 12, "2026-07-09T03:00:00.000Z", "2026-07-09T03:00:00.000Z")
    installment["status"] = "POSTED"
    result = await _fetch([installment])
    assert result[0].date == date(2026, 7, 9)


# ---------------------------------------------------------------------------
# v2 cursor pagination (GET /v2/transactions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "next_value,expected",
    [
        (None, None),
        ("", None),
        (
            "https://api.pluggy.ai/v2/transactions?accountId=a&after=CURSOR123",
            "CURSOR123",
        ),
        ("/v2/transactions?after=abc%3D%3D&accountId=a", "abc=="),
        # No `after` in the URL → stop (don't loop on a malformed value).
        ("https://api.pluggy.ai/v2/transactions?accountId=a", None),
    ],
)
def test_extract_after(next_value, expected):
    assert PluggyProvider._extract_after(next_value) == expected


def _txn(id_: str) -> dict:
    return {"id": id_, "description": "x", "amount": -1, "date": "2026-01-01", "type": "DEBIT"}


@pytest.mark.asyncio
async def test_get_transactions_follows_cursor_until_next_is_null():
    """Pages via the `after` cursor from `next` until it's null, hitting v2
    and forwarding createdAtFrom."""
    page1 = MagicMock(raise_for_status=MagicMock())
    page1.json = MagicMock(return_value={
        "results": [_txn("t1"), _txn("t2")],
        "next": "https://api.pluggy.ai/v2/transactions?accountId=a&after=CUR2",
    })
    page2 = MagicMock(raise_for_status=MagicMock())
    page2.json = MagicMock(return_value={"results": [_txn("t3")], "next": None})

    client = MagicMock()
    client.get = AsyncMock(side_effect=[page1, page2])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    provider = PluggyProvider()
    with patch.object(
        PluggyProvider, "_ensure_api_key", new=AsyncMock(return_value="k")
    ), patch("app.providers.pluggy.httpx.AsyncClient", return_value=client):
        txns = await provider.get_transactions(
            {"item_id": "i"}, "acc", since=date(2026, 1, 1)
        )

    assert [t.external_id for t in txns] == ["t1", "t2", "t3"]
    assert client.get.await_count == 2
    first = client.get.await_args_list[0]
    assert first.args[0].endswith("/v2/transactions")
    assert first.kwargs["params"]["createdAtFrom"] == "2026-01-01"
    assert "after" not in first.kwargs["params"]
    assert client.get.await_args_list[1].kwargs["params"]["after"] == "CUR2"


# ----- masked account number (issue #408) -----


def test_build_account_data_masks_account_number():
    """Brazil has no IBAN; Pluggy's `number` is the branch/account number."""
    from app.providers.pluggy import _build_account_data

    acc = {
        "id": "acc-1",
        "name": "Conta Corrente",
        "type": "BANK",
        "number": "1234-56789",
        "balance": 100,
        "currencyCode": "BRL",
    }
    out = _build_account_data(acc, PluggyProvider._map_account_type)
    assert out.masked_number == "6789"


def test_build_account_data_without_number_leaves_mask_none():
    from app.providers.pluggy import _build_account_data

    acc = {"id": "acc-2", "name": "Conta", "type": "BANK", "balance": 0}
    out = _build_account_data(acc, PluggyProvider._map_account_type)
    assert out.masked_number is None


def test_build_account_data_groups_consolidated_credit_line():
    from app.providers.pluggy import _build_account_data

    credit_data = {
        "disaggregatedCreditLimits": [{
            "lineName": "CREDITO_A_VISTA",
            "creditLineLimitType": "LIMITE_CREDITO_TOTAL",
            "consolidationType": "CONSOLIDADO",
            "usedAmount": 250.50,
            "customizedLimitAmount": 1000,
        }]
    }
    visa = _build_account_data(
        {"id": "visa", "name": "Visa", "type": "CREDIT", "balance": 250.50,
         "currencyCode": "BRL", "creditData": credit_data},
        PluggyProvider._map_account_type,
    )
    mastercard = _build_account_data(
        {"id": "mc", "name": "Mastercard", "type": "CREDIT", "balance": 250.50,
         "currencyCode": "BRL", "creditData": credit_data},
        PluggyProvider._map_account_type,
    )

    assert visa.shared_balance_group is not None
    assert visa.shared_balance_group == mastercard.shared_balance_group


def test_build_account_data_does_not_group_individual_credit_line():
    from app.providers.pluggy import _build_account_data

    acc = _build_account_data(
        {"id": "visa", "name": "Visa", "type": "CREDIT", "balance": 250,
         "currencyCode": "BRL", "creditData": {"disaggregatedCreditLimits": [{
             "lineName": "CREDITO_A_VISTA",
             "creditLineLimitType": "LIMITE_CREDITO_TOTAL",
             "consolidationType": "INDIVIDUAL",
             "usedAmount": 250,
             "limitAmount": 1000,
         }]}},
        PluggyProvider._map_account_type,
    )

    assert acc.shared_balance_group is None


def test_build_account_data_maps_bank_savings_subtype_to_savings():
    from app.providers.pluggy import _build_account_data

    acc = {
        "id": "acc-savings",
        "name": "Poupança",
        "type": "BANK",
        "subtype": "SAVINGS_ACCOUNT",
        "balance": 0,
        "currencyCode": "BRL",
    }

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.type == "savings"


# ----- issue #843: auto-invested balance must not be double-counted -----
# Pluggy reports the bank's auto-withdrawal investment inside the account
# balance (bankData.automaticallyInvestedBalance) and as an investment
# holding. The mapping keeps the reported balance and exposes the invested
# portion; the sync decides whether to subtract it (see connection tests).


def _bb_account(balance, bank_data):
    return {
        "id": "acc-bb",
        "name": "Conta Corrente",
        "type": "BANK",
        "subtype": "CHECKING_ACCOUNT",
        "number": "1234-5678",
        "currencyCode": "BRL",
        "balance": balance,
        "bankData": bank_data,
    }


def test_build_account_data_exposes_fully_auto_invested_balance():
    """The reported case: the whole available balance is auto-applied to a
    short-term investment, which also appears in /investments."""
    from app.providers.pluggy import _build_account_data

    acc = _bb_account(
        5000.00,
        {
            "transferNumber": "0001/0001/12345678-9",
            "closingBalance": 5000.00,
            "automaticallyInvestedBalance": 5000.00,
        },
    )

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.balance == Decimal("5000.00")
    assert out.auto_invested_balance == Decimal("5000.00")


def test_build_account_data_exposes_partial_auto_invested_balance():
    from app.providers.pluggy import _build_account_data

    acc = _bb_account(
        1000,
        {
            "transferNumber": "0001/0001/12345678-9",
            "closingBalance": 1000,
            "automaticallyInvestedBalance": 250.50,
        },
    )

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.balance == Decimal("1000")
    assert out.auto_invested_balance == Decimal("250.50")


def test_build_account_data_ignores_auto_invested_without_positive_balance():
    """An overdraft cannot have money auto-invested; keep the balance as-is
    instead of making it more negative."""
    from app.providers.pluggy import _build_account_data

    acc = _bb_account(
        -50,
        {
            "transferNumber": "0001/0001/12345678-9",
            "closingBalance": -50,
            "automaticallyInvestedBalance": 20,
        },
    )

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.balance == Decimal("-50")
    assert out.auto_invested_balance == Decimal("0")


def test_build_account_data_caps_auto_invested_at_the_reported_balance():
    """A malformed payload (auto-invested > balance) must never produce a
    negative cash balance."""
    from app.providers.pluggy import _build_account_data

    acc = _bb_account(
        100,
        {
            "transferNumber": "0001/0001/12345678-9",
            "closingBalance": 100,
            "automaticallyInvestedBalance": 500,
        },
    )

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.balance == Decimal("100")
    assert out.auto_invested_balance == Decimal("100")


@pytest.mark.parametrize(
    "bank_data",
    [
        None,  # no bankData at all
        {},  # bankData without the field
        {"transferNumber": "0001/0001/12345678-9", "closingBalance": 100},
        {"automaticallyInvestedBalance": None},
        {"automaticallyInvestedBalance": "not-a-number"},
        {"automaticallyInvestedBalance": True},
        {"automaticallyInvestedBalance": -10},
    ],
    ids=[
        "no-bank-data",
        "empty-bank-data",
        "field-absent",
        "null-field",
        "non-numeric-field",
        "bool-field",
        "negative-field",
    ],
)
def test_build_account_data_keeps_balance_when_field_missing_or_invalid(
    bank_data,
):
    """Missing or invalid automaticallyInvestedBalance keeps the historical
    behaviour (balance as reported)."""
    from app.providers.pluggy import _build_account_data

    acc = _bb_account(100, bank_data)

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.balance == Decimal("100")
    assert out.auto_invested_balance == Decimal("0")


def test_auto_invested_balance_accepts_string_numbers():
    """Pluggy documents the field as a number, but some partners send
    strings; both must parse."""
    from app.providers.pluggy import _auto_invested_balance

    assert _auto_invested_balance({"bankData": {"automaticallyInvestedBalance": "100.00"}}) == Decimal("100.00")
    assert _auto_invested_balance({"bankData": {"automaticallyInvestedBalance": 100.00}}) == Decimal("100.00")
    assert _auto_invested_balance({}) == Decimal("0")
    assert _auto_invested_balance({"bankData": "oops"}) == Decimal("0")


@pytest.mark.parametrize(
    "pluggy_type,pluggy_subtype,expected",
    [
        ("BANK", "CHECKING_ACCOUNT", "checking"),
        ("BANK", "SAVINGS_ACCOUNT", "savings"),
        ("CREDIT", "CREDIT_CARD", "credit_card"),
        # `subtype` is documented as always present, but a payload that omits
        # it must still fall back to the `type` mapping.
        ("BANK", None, "checking"),
        ("CREDIT", None, "credit_card"),
        # Unknown values keep the historical "checking" default.
        ("SOMETHING_NEW", None, "checking"),
    ],
)
def test_map_account_type_covers_pluggy_type_subtype_pairs(
    pluggy_type, pluggy_subtype, expected
):
    """Pluggy's enums are `type` ∈ (BANK, CREDIT) and `subtype` ∈
    (CHECKING_ACCOUNT, SAVINGS_ACCOUNT, CREDIT_CARD). Pin every real pair so
    the savings branch can't swallow the others.
    """
    assert PluggyProvider._map_account_type(pluggy_type, pluggy_subtype) == expected


def test_build_account_data_without_subtype_still_maps_bank_to_checking():
    from app.providers.pluggy import _build_account_data

    acc = {
        "id": "acc-checking",
        "name": "Conta Corrente",
        "type": "BANK",
        "balance": 0,
        "currencyCode": "BRL",
    }

    out = _build_account_data(acc, PluggyProvider._map_account_type)

    assert out.type == "checking"


# ----- per-account institution hints for banking-group brokerage accounts
# (issue #723) -----


def _bank_account(external_id: str, compe: str) -> dict:
    return {
        "id": external_id,
        "name": "XP",
        "type": "BANK",
        "subtype": "CHECKING_ACCOUNT",
        "balance": 0,
        "currencyCode": "BRL",
        "bankData": {"transferNumber": f"{compe}/0001/00437907-0"},
    }


@pytest.mark.asyncio
async def test_annotate_institutions_resolves_colliding_checking_accounts():
    """Two BANK/CHECKING_ACCOUNT rows in the same connection with different
    COMPE codes (bank vs. its brokerage arm, e.g. XP 348/102) are ambiguous —
    both get institution_* hints resolved."""
    from app.providers.pluggy import _annotate_institutions, _build_account_data

    raw = [_bank_account("acc-bank", "348"), _bank_account("acc-broker", "102")]
    accounts = [_build_account_data(a, PluggyProvider._map_account_type) for a in raw]

    async def fake_lookup(compe):
        return {
            "348": {"name": "Banco XP S.A.", "logo_url": "https://x/348.svg"},
            "102": {"name": "XP Investimentos CCTVM S/A", "logo_url": "https://x/102.svg"},
        }[compe]

    with patch("app.providers.pluggy._lookup_bank_info", side_effect=fake_lookup):
        await _annotate_institutions(raw, accounts)

    by_id = {a.external_id: a for a in accounts}
    assert by_id["acc-bank"].institution_external_id == "348"
    assert by_id["acc-bank"].institution_name == "Banco XP S.A."
    assert by_id["acc-bank"].institution_logo_url == "https://x/348.svg"
    assert by_id["acc-broker"].institution_external_id == "102"
    assert by_id["acc-broker"].institution_name == "XP Investimentos CCTVM S/A"


@pytest.mark.asyncio
async def test_annotate_institutions_leaves_non_colliding_accounts_untouched():
    """Checking + savings at the same bank (identical COMPE code) is the
    common case — no ambiguity, no lookup call, no institution_* hint set."""
    from app.providers.pluggy import _annotate_institutions, _build_account_data

    checking = _bank_account("acc-checking", "237")
    savings = dict(checking, id="acc-savings", subtype="SAVINGS_ACCOUNT")
    raw = [checking, savings]
    accounts = [_build_account_data(a, PluggyProvider._map_account_type) for a in raw]

    with patch("app.providers.pluggy._lookup_bank_info") as fake_lookup:
        await _annotate_institutions(raw, accounts)

    fake_lookup.assert_not_called()
    assert all(a.institution_name is None for a in accounts)
    assert all(a.institution_external_id is None for a in accounts)


@pytest.mark.asyncio
async def test_lookup_bank_info_returns_cached_value_without_http_call():
    from app.providers.pluggy import _lookup_bank_info

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(
        return_value='{"name": "Banco XP S.A.", "logo_url": "https://x/348.svg"}'
    )

    with patch("app.providers.pluggy.get_settings") as mock_settings, \
         patch("app.core.redis.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.providers.pluggy.httpx.AsyncClient") as fake_client_cls:
        mock_settings.return_value.brasilapi_institution_lookup_enabled = True
        result = await _lookup_bank_info("348")

    assert result == {"name": "Banco XP S.A.", "logo_url": "https://x/348.svg"}
    fake_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_bank_info_is_none_and_non_fatal_on_http_failure():
    """A BrasilAPI outage must never break sync — it just means no hint."""
    from app.providers.pluggy import _lookup_bank_info

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=RuntimeError("boom"))
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.providers.pluggy.get_settings") as mock_settings, \
         patch("app.core.redis.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.providers.pluggy.httpx.AsyncClient", return_value=fake_client):
        mock_settings.return_value.brasilapi_institution_lookup_enabled = True
        result = await _lookup_bank_info("348")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_bank_info_disabled_by_default_skips_http_call():
    """No BrasilAPI call — not even a cache read — until a deploy opts in."""
    from app.providers.pluggy import _lookup_bank_info

    with patch("app.providers.pluggy.get_settings") as mock_settings, \
         patch("app.core.redis.get_redis") as fake_get_redis, \
         patch("app.providers.pluggy.httpx.AsyncClient") as fake_client_cls:
        mock_settings.return_value.brasilapi_institution_lookup_enabled = False
        result = await _lookup_bank_info("348")

    assert result is None
    fake_get_redis.assert_not_called()
    fake_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# _build_holding_data: TOTAL_WITHDRAWAL vs. positive balance/quantity (#819)
# ---------------------------------------------------------------------------


def _investment(
    *,
    inv_id: str = "inv-1",
    status: str = "ACTIVE",
    balance=None,
    quantity=None,
    value=None,
) -> dict:
    inv = {
        "id": inv_id,
        "name": "PETR4",
        "type": "EQUITY",
        "currencyCode": "BRL",
        "status": status,
    }
    if balance is not None:
        inv["balance"] = balance
    if quantity is not None:
        inv["quantity"] = quantity
    if value is not None:
        inv["value"] = value
    return inv


def test_holding_total_withdrawal_with_positive_balance_is_not_withdrawn():
    """Contradictory payload: status says TOTAL_WITHDRAWAL but the same
    response still reports a positive balance and quantity. The position
    still holds value, so it must not be treated as sold (issue #819)."""
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(
        status="TOTAL_WITHDRAWAL", balance=1234.56, quantity=10, value=123.46,
    ))

    assert holding.is_withdrawn is False
    assert holding.current_value == Decimal("1234.56")
    assert holding.quantity == Decimal("10")


def test_holding_total_withdrawal_with_only_positive_quantity_is_not_withdrawn():
    """No balance in the payload, but the quantity is still positive: the
    position is not fully gone."""
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(
        status="TOTAL_WITHDRAWAL", quantity=10, value=123.46,
    ))

    assert holding.is_withdrawn is False
    assert holding.current_value == Decimal("0")


def test_holding_total_withdrawal_with_negative_balance_is_not_withdrawn():
    """A nonzero balance means the position is not empty, even when it is
    negative; it must not be hidden as sold."""
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(
        status="TOTAL_WITHDRAWAL", balance=-12.5, quantity=0,
    ))

    assert holding.is_withdrawn is False
    assert holding.current_value == Decimal("-12.5")


def test_holding_total_withdrawal_empty_position_is_withdrawn():
    """A genuinely closed position reports zero balance and zero quantity;
    that one must still be marked withdrawn."""
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(
        status="TOTAL_WITHDRAWAL", balance=0, quantity=0, value=0,
    ))

    assert holding.is_withdrawn is True


def test_holding_total_withdrawal_without_balance_or_quantity_is_withdrawn():
    """Some payloads omit both figures entirely; status alone is enough to
    mark the position closed."""
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(status="TOTAL_WITHDRAWAL"))

    assert holding.is_withdrawn is True


def test_holding_active_status_is_never_withdrawn():
    from app.providers.pluggy import _build_holding_data

    holding = _build_holding_data(_investment(
        status="ACTIVE", balance=100, quantity=1,
    ))

    assert holding.is_withdrawn is False
