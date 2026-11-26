"""An import names its categories by id, and those ids must be the workspace's.

`category_id` (the person's pick in the review table) and
`suggested_category_id` (what the preview suggested) arrive in the request
body. A category from another workspace, or one that does not exist, refuses
the whole import with the same answer, so the response does not tell which
ids exist elsewhere, and nothing of the batch is written.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.import_log import ImportLog
from app.models.transaction import Transaction
from app.models.workspace import Workspace

CATEGORY_FIELDS = ["category_id", "suggested_category_id"]


async def _foreign_category(session: AsyncSession, test_user) -> Category:
    other_ws = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal",
        created_by_user_id=test_user.id, default_currency="USD",
    )
    session.add(other_ws)
    await session.flush()
    foreign = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_ws.id, name="Foreign"
    )
    session.add(foreign)
    await session.commit()
    return foreign


async def _import(client: AsyncClient, auth_headers, account: Account, row: dict):
    return await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(account.id),
            "transactions": [
                {
                    "description": "MERCADO",
                    "amount": "42.00",
                    "date": "2026-02-10",
                    "type": "debit",
                },
                {
                    "description": "UBER TRIP",
                    "amount": "25.50",
                    "date": "2026-02-11",
                    "type": "debit",
                    **row,
                },
            ],
        },
    )


async def _nothing_written(session: AsyncSession, account: Account) -> None:
    rows = await session.execute(
        select(func.count()).select_from(Transaction).where(Transaction.account_id == account.id)
    )
    assert rows.scalar_one() == 0
    logs = await session.execute(
        select(func.count()).select_from(ImportLog).where(ImportLog.account_id == account.id)
    )
    assert logs.scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", CATEGORY_FIELDS)
async def test_a_category_of_another_workspace_refuses_the_import(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_account, field
):
    foreign = await _foreign_category(session, test_user)

    response = await _import(client, auth_headers, test_account, {field: str(foreign.id)})

    assert response.status_code == 400
    assert response.json()["detail"] == "Category not found"
    await _nothing_written(session, test_account)
    listed = await client.get("/api/transactions", headers=auth_headers)
    assert "Foreign" not in listed.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", CATEGORY_FIELDS)
async def test_a_foreign_category_answers_like_one_that_does_not_exist(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_account, field
):
    foreign = await _foreign_category(session, test_user)

    foreign_response = await _import(
        client, auth_headers, test_account, {field: str(foreign.id)}
    )
    missing_response = await _import(
        client, auth_headers, test_account, {field: str(uuid.uuid4())}
    )

    assert foreign_response.status_code == missing_response.status_code == 400
    assert foreign_response.json() == missing_response.json()
    await _nothing_written(session, test_account)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", CATEGORY_FIELDS)
async def test_a_category_of_the_workspace_is_still_imported(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account, test_categories, field
):
    own = test_categories[0]

    response = await _import(client, auth_headers, test_account, {field: str(own.id)})

    assert response.status_code == 201
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == test_account.id,
            Transaction.description == "UBER TRIP",
        )
    )
    assert result.scalar_one().category_id == own.id


@pytest.mark.asyncio
async def test_an_excluded_row_does_not_need_a_valid_category(
    client: AsyncClient, auth_headers, test_account
):
    # The row is left out of the import, so its category is never written.
    response = await _import(
        client,
        auth_headers,
        test_account,
        {"category_id": str(uuid.uuid4()), "excluded": True},
    )

    assert response.status_code == 201
    assert response.json()["imported"] == 1
