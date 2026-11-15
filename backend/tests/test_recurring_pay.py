"""Paying a recurring bill before it is due."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.transaction import TransactionCreate
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)
from app.services.dashboard_service import get_projected_transactions
from app.services.transaction_service import create_transaction


@pytest_asyncio.fixture
async def account(session: AsyncSession, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Carteira",
        type="checking",
        balance=Decimal("10000"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _make_bill(session, test_workspace, test_user, account, **overrides):
    data = RecurringTransactionCreate(
        description=overrides.pop("description", "Internet Fibra"),
        amount=overrides.pop("amount", Decimal("120.00")),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=overrides.pop("start_date", date(2025, 1, 10)),
        account_id=account.id,
        **overrides,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _rows_for(session, bill_id):
    result = await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == bill_id)
    )
    return list(result.scalars())


@pytest.mark.asyncio
async def test_manual_early_payment_settles_the_occurrence(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account)

    tx = await create_transaction(
        session,
        test_workspace.id,
        test_user.id,
        TransactionCreate(
            description="Internet Fibra",
            amount=Decimal("120.00"),
            date=date(2025, 1, 7),
            type="debit",
            account_id=account.id,
        ),
    )

    assert tx.recurring_transaction_id == bill.id
    await session.refresh(bill)
    assert bill.next_occurrence == date(2025, 2, 10)

    generated = await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))
    assert generated == 0
    assert [row.id for row in await _rows_for(session, bill.id)] == [tx.id]


@pytest.mark.asyncio
async def test_manual_installment_is_never_linked_to_a_bill(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account)

    tx = await create_transaction(
        session,
        test_workspace.id,
        test_user.id,
        TransactionCreate(
            description="Internet Fibra",
            amount=Decimal("120.00"),
            date=date(2025, 1, 9),
            type="debit",
            account_id=account.id,
            installment_number=1,
            total_installments=3,
            installment_total_amount=Decimal("360.00"),
            installment_purchase_date=date(2025, 1, 9),
        ),
    )

    assert tx.recurring_transaction_id is None
    await session.refresh(bill)
    assert bill.next_occurrence == date(2025, 1, 10)


@pytest.mark.asyncio
async def test_pay_now_from_another_account_settles_one_occurrence(
    client, auth_headers, session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account)
    other = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Poupança",
        type="savings",
        balance=Decimal("500"),
        currency="BRL",
    )
    session.add(other)
    await session.commit()
    bill_id, other_id = bill.id, other.id

    response = await client.post(
        f"/api/recurring-transactions/{bill_id}/pay",
        json={
            "date": "2025-01-02",
            "account_id": str(other_id),
            "amount": "135.50",
            "description": "Internet com multa",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transaction"]["account_id"] == str(other_id)
    assert body["transaction"]["amount"] == "135.50"
    assert body["transaction"]["description"] == "Internet com multa"
    assert body["transaction"]["date"] == "2025-01-02"
    assert body["transaction"]["source"] == "manual"
    assert body["transaction"]["recurring_transaction_id"] == str(bill_id)
    assert body["recurring"]["next_occurrence"] == "2025-02-10"

    user_id = test_user.id
    session.expire_all()
    assert await generate_pending(session, user_id, up_to=date(2025, 1, 31)) == 0
    rows = await _rows_for(session, bill_id)
    assert [str(row.id) for row in rows] == [body["transaction"]["id"]]


@pytest.mark.asyncio
async def test_pay_now_upgrades_the_pending_placeholder(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    bill = await _make_bill(session, test_workspace, test_user, test_account)
    bill_id = bill.id
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1
    [placeholder] = await _rows_for(session, bill_id)
    assert placeholder.status == "pending"
    placeholder_id = placeholder.id

    response = await client.post(
        f"/api/recurring-transactions/{bill_id}/pay",
        json={"date": "2025-01-11"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transaction"]["id"] == str(placeholder_id)
    assert body["transaction"]["status"] == "posted"
    assert body["transaction"]["source"] == "manual"
    assert body["transaction"]["date"] == "2025-01-11"
    assert body["recurring"]["next_occurrence"] == "2025-02-10"

    user_id = test_user.id
    session.expire_all()
    assert await generate_pending(session, user_id, up_to=date(2025, 1, 31)) == 0
    assert len(await _rows_for(session, bill_id)) == 1


@pytest.mark.asyncio
async def test_pay_now_rejects_an_inactive_bill(
    client, auth_headers, session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id
    bill.is_active = False
    await session.commit()

    response = await client.post(
        f"/api/recurring-transactions/{bill_id}/pay",
        json={"date": "2025-01-05"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert await _rows_for(session, bill_id) == []


@pytest.mark.asyncio
async def test_pay_now_removes_the_occurrence_from_the_projection(
    client, auth_headers, session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, start_date=date(2030, 3, 15)
    )
    bill_id = bill.id

    response = await client.post(
        f"/api/recurring-transactions/{bill_id}/pay",
        json={"date": "2030-03-01"},
        headers=auth_headers,
    )
    assert response.status_code == 200

    user_id, workspace_id = test_user.id, test_workspace.id
    session.expire_all()
    march = await get_projected_transactions(
        session, workspace_id, user_id, month=date(2030, 3, 1)
    )
    april = await get_projected_transactions(
        session, workspace_id, user_id, month=date(2030, 4, 1)
    )
    assert [p.date for p in march] == []
    assert [str(p.date) for p in april] == ["2030-04-15"]


def _split_70_30(test_categories) -> dict:
    food, home, _ = test_categories
    return {
        "share_type": "percent",
        "lines": [
            {"category_id": str(home.id), "share_pct": 70},
            {"category_id": str(food.id), "share_pct": 30},
        ],
    }


def _line_amounts(body) -> list[tuple[str, str]]:
    return [(line["category_id"], str(line["amount"])) for line in body["category_lines"]]


@pytest.mark.asyncio
async def test_pay_now_hands_a_split_bill_its_parts(
    client, auth_headers, session, test_user, test_workspace, account, test_categories
):
    food, home, _ = test_categories
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        category_lines=_split_70_30(test_categories),
    )

    response = await client.post(
        f"/api/recurring-transactions/{bill.id}/pay",
        json={"date": "2025-01-05", "amount": "200.00"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()["transaction"]
    assert body["category_id"] is None
    assert body["has_category_lines"] is True
    assert _line_amounts(body) == [(str(home.id), "140.00"), (str(food.id), "60.00")]


@pytest.mark.asyncio
async def test_pay_now_resizes_a_split_placeholder_to_the_paid_amount(
    client, auth_headers, session, test_user, test_workspace, test_account, test_categories
):
    food, home, _ = test_categories
    bill = await _make_bill(
        session, test_workspace, test_user, test_account,
        category_lines=_split_70_30(test_categories),
    )
    bill_id = bill.id
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    response = await client.post(
        f"/api/recurring-transactions/{bill_id}/pay",
        json={"date": "2025-01-11", "amount": "200.00"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()["transaction"]
    assert body["status"] == "posted"
    assert _line_amounts(body) == [(str(home.id), "140.00"), (str(food.id), "60.00")]
