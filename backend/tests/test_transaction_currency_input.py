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
from datetime import date

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
