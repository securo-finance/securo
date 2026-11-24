"""The currency a client sends on a transaction create or edit (#497).

POST and PATCH stored "XXX" and "" as typed, so the row kept no conversion
and a permanent fallback, and a PATCH with an explicit null crashed with a
500. Now a code outside SUPPORTED_CURRENCIES, an empty one and a null on the
edit are a 422 and nothing is written. Null (or no currency) on the create
still means the account's currency, which the receipt dialog relies on, and
a row synced in an unsupported currency still saves when the form sends its
own currency back.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User


def _body(account_id, **extra):
    return {
        "account_id": str(account_id),
        "description": "Currency input",
        "amount": "100.00",
        "date": date.today().isoformat(),
        "type": "debit",
        **extra,
    }


async def _count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Transaction)) or 0


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["XXX", "", "   "])
async def test_create_refuses_an_unsupported_or_empty_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account, currency
):
    before = await _count(session)

    resp = await client.post(
        "/api/transactions", headers=auth_headers, json=_body(test_account.id, currency=currency)
    )

    assert resp.status_code == 422, resp.text
    assert "currency" in resp.text
    assert await _count(session) == before


@pytest.mark.asyncio
async def test_create_with_null_currency_takes_the_account_currency(
    client: AsyncClient, auth_headers, test_account
):
    resp = await client.post(
        "/api/transactions", headers=auth_headers, json=_body(test_account.id, currency=None)
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["currency"] == test_account.currency


@pytest.mark.asyncio
async def test_create_trims_and_upper_cases_a_supported_currency(
    client: AsyncClient, auth_headers, test_account
):
    resp = await client.post(
        "/api/transactions", headers=auth_headers, json=_body(test_account.id, currency=" usd ")
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["currency"] == "USD"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"currency": "XXX", "amount": "1500.00"},
        {"currency": ""},
        {"currency": None},
    ],
)
async def test_update_refuses_an_unsupported_empty_or_null_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account, body
):
    created = await client.post(
        "/api/transactions", headers=auth_headers, json=_body(test_account.id)
    )
    assert created.status_code == 201, created.text
    txn_id = created.json()["id"]

    resp = await client.patch(f"/api/transactions/{txn_id}", headers=auth_headers, json=body)

    assert resp.status_code == 422, resp.text
    assert "currency" in resp.text
    row = await session.get(Transaction, uuid.UUID(txn_id))
    assert row is not None
    await session.refresh(row)
    assert row.currency == test_account.currency
    assert row.amount == 100


@pytest.mark.asyncio
async def test_update_keeps_a_row_already_in_an_unsupported_currency_editable(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User
):
    # A row an import or a bank sync wrote in a currency the instance does
    # not list. The edit form sends its currency back with every save.
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Kuwait",
        type="checking",
        currency="KWD",
    )
    session.add(account)
    await session.commit()
    created = await client.post("/api/transactions", headers=auth_headers, json=_body(account.id))
    assert created.status_code == 201, created.text
    assert created.json()["currency"] == "KWD"

    resp = await client.patch(
        f"/api/transactions/{created.json()['id']}",
        headers=auth_headers,
        json={"currency": "kwd", "description": "Renamed"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["currency"] == "KWD"
    assert resp.json()["description"] == "Renamed"


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["XXX", "KWD", "US D", "", "   "])
async def test_series_create_refuses_an_unsupported_or_empty_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account, currency
):
    before = await _count(session)

    resp = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={"installments": 3, "base": _body(test_account.id, currency=currency)},
    )

    assert resp.status_code == 422, resp.text
    assert "currency" in resp.text
    await session.rollback()
    assert await _count(session) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("currency", "stored"), [(" usd ", "USD"), ("EUR", "EUR"), (None, None)]
)
async def test_series_create_accepts_a_supported_or_null_currency(
    client: AsyncClient, auth_headers, test_account, currency, stored
):
    resp = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={"installments": 3, "base": _body(test_account.id, currency=currency)},
    )

    assert resp.status_code == 201, resp.text
    expected = stored or test_account.currency
    assert [row["currency"] for row in resp.json()] == [expected] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["kwd", None])
async def test_series_create_keeps_an_unsupported_account_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User, currency
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Kuwait",
        type="checking",
        currency="KWD",
    )
    session.add(account)
    await session.commit()

    resp = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={"installments": 2, "base": _body(account.id, currency=currency)},
    )

    assert resp.status_code == 201, resp.text
    assert [row["currency"] for row in resp.json()] == ["KWD", "KWD"]


@pytest.mark.asyncio
async def test_a_refused_series_edit_writes_no_installment(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User, test_account
):
    """The first installment keeps its KWD, the second refuses it. The first
    one's FX lookup commits the session (`sync_rates`), so its amount stayed
    changed behind the 422."""
    series_id = uuid.uuid4()
    purchase = date.today() - timedelta(days=40)
    rows = [
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_account.workspace_id,
            account_id=test_account.id, description="Notebook", amount=Decimal("100.00"),
            currency=currency, date=purchase + timedelta(days=30 * index), type="debit",
            source="manual", installment_series_id=series_id, installment_number=index + 1,
            total_installments=2, installment_purchase_date=purchase,
            installment_total_amount=Decimal("200.00"),
        )
        for index, currency in enumerate(("KWD", "USD"))
    ]
    session.add_all(rows)
    await session.commit()
    ids = [row.id for row in rows]

    resp = await client.patch(
        f"/api/transactions/{ids[0]}",
        headers=auth_headers,
        json={"currency": "KWD", "amount": "150.00", "apply_to": "all"},
    )

    assert resp.status_code == 422, resp.text
    assert "currency" in resp.text
    await session.rollback()
    stored = (
        await session.execute(
            select(Transaction.currency, Transaction.amount)
            .where(Transaction.id.in_(ids))
            .order_by(Transaction.installment_number)
        )
    ).all()
    assert [tuple(row) for row in stored] == [("KWD", 100), ("USD", 100)]
