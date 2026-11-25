"""A recurring bill only ever carries a category of its own workspace."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select

from app.models.account import Account
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)


@pytest_asyncio.fixture
async def foreign_category(session) -> Category:
    """A category in a workspace that belongs to someone else."""
    owner = User(
        id=uuid.uuid4(),
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={},
    )
    session.add(owner)
    await session.flush()
    workspace = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=owner.id)
    session.add(workspace)
    await session.flush()
    category = Category(
        id=uuid.uuid4(), user_id=owner.id, workspace_id=workspace.id, name="Private"
    )
    session.add(category)
    await session.commit()
    return category


def _payload(account: Account, **overrides) -> dict:
    return {
        "description": "Gym",
        "amount": 100,
        "currency": "BRL",
        "type": "debit",
        "frequency": "monthly",
        "start_date": "2026-03-01",
        "account_id": str(account.id),
        **overrides,
    }


def _lines(*category_ids) -> dict:
    return {
        "share_type": "exact",
        "lines": [{"category_id": str(c), "amount": 50} for c in category_ids],
    }


async def _bill(session, user, workspace, account, category_id) -> RecurringTransaction:
    bill = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        category_id=category_id,
        description="Gym",
        amount=Decimal("100.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2025, 1, 10),
        next_occurrence=date(2025, 1, 10),
    )
    session.add(bill)
    await session.commit()
    return bill


@pytest.mark.asyncio
async def test_create_answers_a_foreign_category_like_an_unknown_one(
    client, session, auth_headers, test_account, foreign_category
):
    answers = []
    for category_id in (foreign_category.id, uuid.uuid4()):
        response = await client.post(
            "/api/recurring-transactions",
            json=_payload(test_account, category_id=str(category_id)),
            headers=auth_headers,
        )
        answers.append((response.status_code, response.json()))

    assert answers[0] == (400, {"detail": "Category not found"})
    assert answers[1] == answers[0]
    assert (await session.execute(select(RecurringTransaction.id))).all() == []


@pytest.mark.asyncio
async def test_update_answers_a_foreign_category_like_an_unknown_one(
    client, session, auth_headers, test_user, test_workspace, test_account,
    test_categories, foreign_category,
):
    own = test_categories[0].id
    bill = await _bill(session, test_user, test_workspace, test_account, own)
    answers = []
    for category_id in (foreign_category.id, uuid.uuid4()):
        response = await client.patch(
            f"/api/recurring-transactions/{bill.id}",
            json={"category_id": str(category_id), "description": "Renamed"},
            headers=auth_headers,
        )
        answers.append((response.status_code, response.json()))

    assert answers[0] == (400, {"detail": "Category not found"})
    assert answers[1] == answers[0]
    await session.refresh(bill)
    assert (bill.category_id, bill.description) == (own, "Gym")


@pytest.mark.asyncio
async def test_undoing_a_split_into_a_foreign_category_is_refused(
    client, session, auth_headers, test_account, test_categories, foreign_category
):
    created = await client.post(
        "/api/recurring-transactions",
        json=_payload(
            test_account, category_lines=_lines(test_categories[0].id, test_categories[1].id)
        ),
        headers=auth_headers,
    )
    assert created.status_code == 201
    url = f"/api/recurring-transactions/{created.json()['id']}"
    response = await client.patch(
        url,
        json={
            "category_id": str(foreign_category.id),
            "category_lines": {"share_type": "exact", "lines": []},
        },
        headers=auth_headers,
    )

    assert (response.status_code, response.json()) == (400, {"detail": "Category not found"})
    stored = await session.get(RecurringTransaction, uuid.UUID(created.json()["id"]))
    await session.refresh(stored, ["category_lines", "category_id", "has_category_lines"])
    assert stored.category_id is None
    assert stored.has_category_lines
    assert len(stored.category_lines) == 2


@pytest.mark.asyncio
async def test_lines_answer_a_foreign_category_like_an_unknown_one(
    client, session, auth_headers, test_user, test_workspace, test_account,
    test_categories, foreign_category,
):
    own = test_categories[0].id
    bill = await _bill(session, test_user, test_workspace, test_account, own)
    for category_id in (foreign_category.id, uuid.uuid4()):
        lines = _lines(own, category_id)
        created = await client.post(
            "/api/recurring-transactions",
            json=_payload(test_account, description="Split", category_lines=lines),
            headers=auth_headers,
        )
        updated = await client.patch(
            f"/api/recurring-transactions/{bill.id}",
            json={"category_lines": lines},
            headers=auth_headers,
        )
        for response in (created, updated):
            assert (response.status_code, response.json()) == (
                400, {"detail": "Category not found"},
            )


@pytest.mark.asyncio
async def test_own_category_and_no_category_are_still_accepted(
    client, auth_headers, test_account, test_categories
):
    first, second = str(test_categories[0].id), str(test_categories[1].id)
    created = await client.post(
        "/api/recurring-transactions",
        json=_payload(test_account, category_id=first),
        headers=auth_headers,
    )
    assert created.status_code == 201
    url = f"/api/recurring-transactions/{created.json()['id']}"
    for category_id in (second, None):
        response = await client.patch(url, json={"category_id": category_id}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["category_id"] == category_id
    uncategorized = await client.post(
        "/api/recurring-transactions",
        json=_payload(test_account, description="Rent"),
        headers=auth_headers,
    )
    assert uncategorized.status_code == 201


@pytest.mark.asyncio
async def test_generation_leaves_a_stored_foreign_category_behind(
    session, test_user, test_workspace, test_account, foreign_category
):
    """A bill saved before the check still generates, without that category."""
    bill = await _bill(session, test_user, test_workspace, test_account, foreign_category.id)

    assert await generate_pending(session, test_user.id, up_to=date(2025, 2, 10)) == 2

    rows = (
        await session.execute(
            select(Transaction.category_id).where(Transaction.recurring_transaction_id == bill.id)
        )
    ).scalars().all()
    assert rows == [None, None]


@pytest.mark.asyncio
async def test_generation_keeps_an_own_category(
    session, test_user, test_workspace, test_account, test_categories
):
    own = test_categories[0].id
    bill = await create_recurring_transaction(
        session, test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            **_payload(test_account, start_date="2025-01-10", category_id=str(own))
        ),
    )

    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    generated = await session.scalar(
        select(Transaction.category_id).where(Transaction.recurring_transaction_id == bill.id)
    )
    assert generated == own


@pytest.mark.asyncio
async def test_generation_tells_apart_the_workspaces_of_one_person(
    session, test_user, test_workspace, test_account
):
    """Both bills run together; the category is own for only one of them."""
    second = Workspace(id=uuid.uuid4(), name="Second", created_by_user_id=test_user.id)
    session.add(second)
    await session.flush()
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=second.id,
        name="Second account",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    category = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=second.id, name="Second only"
    )
    session.add_all([account, category])
    await session.commit()
    foreign = await _bill(session, test_user, test_workspace, test_account, category.id)
    own = await _bill(session, test_user, second, account, category.id)

    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 2

    rows = await session.execute(
        select(Transaction.recurring_transaction_id, Transaction.category_id)
    )
    generated = {bill_id: category_id for bill_id, category_id in rows if bill_id}
    assert generated == {foreign.id: None, own.id: category.id}


@pytest.mark.asyncio
async def test_generation_looks_categories_up_in_the_bills_workspaces_only(
    session, test_user, test_workspace, test_account, foreign_category
):
    """The lookup asks for the bills' workspaces, not for the id wherever it is.

    A bill saved before the check still carries another workspace's category.
    The generation reads its categories in one query, and that query must not
    bring a row of a workspace no bill here belongs to: the row is dropped by
    the pair, but reading it first is what the check is meant to prevent.
    """
    bill = await _bill(session, test_user, test_workspace, test_account, foreign_category.id)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FROM categories" in statement:
            statements.append(statement)

    bind = session.sync_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        assert await generate_pending(session, test_user.id, up_to=date(2025, 2, 10)) == 2
    finally:
        event.remove(bind, "before_cursor_execute", record)

    lookups = [statement for statement in statements if "categories.id IN" in statement]
    assert lookups, statements
    _, where = lookups[0].split("WHERE", 1)
    assert "categories.workspace_id IN" in where

    rows = (
        await session.execute(
            select(Transaction.category_id).where(Transaction.recurring_transaction_id == bill.id)
        )
    ).scalars().all()
    assert rows == [None, None]
