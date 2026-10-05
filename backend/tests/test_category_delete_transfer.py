"""Deleting a category and handing what it held over to another one.

Any category can be deleted now, including the ones Securo seeds. What made
that safe to allow is this: nothing a category holds is thrown away, and
nothing is left pointing at an id that stopped existing.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TypeVar

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.account import Account
from app.models.user import User
from app.services import budget_service, category_service


Row = TypeVar("Row")


async def _reload(session: AsyncSession, model: type[Row], row_id: uuid.UUID) -> Row:
    """Read a row back after the request that changed it, and say so if it is gone."""
    row = await session.get(model, row_id)
    assert row is not None, f"{model.__name__} {row_id} no longer exists"
    return row


def _recurring(user: User, workspace, category_id: uuid.UUID) -> RecurringTransaction:
    return RecurringTransaction(
        user_id=user.id,
        workspace_id=workspace.id,
        category_id=category_id,
        description="Assinatura",
        amount=Decimal("39.90"),
        type="debit",
        frequency="monthly",
        start_date=date.today(),
        next_occurrence=date.today(),
    )


async def _make_rule(
    session: AsyncSession,
    user: User,
    workspace,
    category_id: uuid.UUID,
    *,
    name: str = "Rule",
    is_active: bool = True,
) -> Rule:
    rule = Rule(
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        conditions=[{"field": "description", "op": "contains", "value": "X"}],
        actions=[
            {"op": "set_category", "value": str(category_id)},
            {"op": "append_notes", "value": "kept"},
        ],
        is_active=is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def _make_budget(
    session: AsyncSession,
    user: User,
    workspace,
    category_id: uuid.UUID,
    *,
    amount: str,
    month: date,
    is_recurring: bool = False,
) -> Budget:
    budget = Budget(
        user_id=user.id,
        workspace_id=workspace.id,
        category_id=category_id,
        amount=Decimal(amount),
        month=month,
        is_recurring=is_recurring,
    )
    session.add(budget)
    await session.commit()
    await session.refresh(budget)
    return budget


@pytest.mark.asyncio
async def test_usage_lists_every_kind_of_reference(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
    test_transactions: list[Transaction],
):
    category = test_categories[0]
    await _make_budget(
        session, test_user, test_workspace, category.id,
        amount="300.00", month=date.today().replace(day=1),
    )
    session.add(_recurring(test_user, test_workspace, category.id))
    await session.commit()
    await _make_rule(session, test_user, test_workspace, category.id, name="Files food")

    response = await client.get(
        f"/api/categories/{category.id}/usage", headers=auth_headers
    )

    assert response.status_code == 200
    usage = response.json()
    assert usage["transactions"] == 1
    assert usage["budgets"] == 1
    assert usage["recurring_transactions"] == 1
    assert [rule["name"] for rule in usage["rules"]] == ["Files food"]


@pytest.mark.asyncio
async def test_usage_of_an_untouched_category_is_all_zeros(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    response = await client.get(
        f"/api/categories/{test_categories[2].id}/usage", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "transactions": 0,
        "budgets": 0,
        "recurring_transactions": 0,
        "rules": [],
    }


@pytest.mark.asyncio
async def test_delete_without_a_destination_is_refused_while_in_use(
    client: AsyncClient,
    auth_headers,
    test_categories: list[Category],
    test_transactions: list[Transaction],
):
    """The refusal comes from the count, not from a foreign key blowing up.

    SQLite does not enforce foreign keys, so a check that relied on the
    database would pass here and only fail in production on Postgres.
    """
    response = await client.delete(
        f"/api/categories/{test_categories[0].id}", headers=auth_headers
    )

    assert response.status_code == 409
    assert "Choose another category" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_without_a_destination_is_refused_for_a_rule_alone(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
):
    """A rule is the reference no foreign key protects.

    It keeps the category id inside a JSON action, so deleting the category
    used to succeed and leave the rule naming an id that was gone.
    """
    category = test_categories[0]
    await _make_rule(session, test_user, test_workspace, category.id)

    response = await client.delete(
        f"/api/categories/{category.id}", headers=auth_headers
    )

    assert response.status_code == 409
    remaining = await session.get(Category, category.id)
    assert remaining is not None


@pytest.mark.asyncio
async def test_delete_with_a_destination_moves_everything(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
    test_transactions: list[Transaction],
):
    source, destination = test_categories[0], test_categories[1]
    budget = await _make_budget(
        session, test_user, test_workspace, source.id,
        amount="300.00", month=date.today().replace(day=1),
    )
    recurring = _recurring(test_user, test_workspace, source.id)
    session.add(recurring)
    await session.commit()
    rule = await _make_rule(session, test_user, test_workspace, source.id)
    source_id, destination_id = source.id, destination.id
    budget_id, recurring_id, rule_id = budget.id, recurring.id, rule.id

    response = await client.delete(
        f"/api/categories/{source_id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(destination_id)},
    )

    assert response.status_code == 204
    session.expire_all()
    assert await session.get(Category, source_id) is None

    moved = (
        await session.execute(
            select(Transaction).where(Transaction.category_id == destination_id)
        )
    ).scalars().all()
    assert len(moved) == 2  # its own transaction plus the one moved over

    assert (await _reload(session, Budget, budget_id)).category_id == destination_id
    moved_recurring = await _reload(session, RecurringTransaction, recurring_id)
    assert moved_recurring.category_id == destination_id

    reloaded = await _reload(session, Rule, rule_id)
    assert reloaded.actions[0] == {"op": "set_category", "value": str(destination_id)}
    # The rule keeps doing everything else it did.
    assert reloaded.actions[1] == {"op": "append_notes", "value": "kept"}


@pytest.mark.asyncio
async def test_delete_repoints_rules_that_are_switched_off(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
):
    """An inactive rule has to move too, or turning it back on breaks it."""
    source, destination = test_categories[0], test_categories[1]
    rule = await _make_rule(
        session, test_user, test_workspace, source.id, is_active=False
    )
    destination_id, rule_id = destination.id, rule.id

    response = await client.delete(
        f"/api/categories/{source.id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(destination_id)},
    )

    assert response.status_code == 204
    session.expire_all()
    reloaded = await _reload(session, Rule, rule_id)
    assert reloaded.actions[0]["value"] == str(destination_id)
    assert reloaded.is_active is False


@pytest.mark.asyncio
async def test_budgets_for_the_same_month_are_added_up(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
):
    """Two budgets cannot share a month and a category, so they become one.

    Keeping only one of the two would quietly lower the ceiling the user set.
    """
    source, destination = test_categories[0], test_categories[1]
    month = date.today().replace(day=1)
    source_budget = await _make_budget(
        session, test_user, test_workspace, source.id, amount="300.00", month=month
    )
    destination_budget = await _make_budget(
        session, test_user, test_workspace, destination.id, amount="500.00", month=month
    )
    source_budget_id, destination_budget_id = source_budget.id, destination_budget.id

    response = await client.delete(
        f"/api/categories/{source.id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(destination.id)},
    )

    assert response.status_code == 204
    session.expire_all()
    assert await session.get(Budget, source_budget_id) is None
    assert (await _reload(session, Budget, destination_budget_id)).amount == Decimal("800.00")


@pytest.mark.asyncio
async def test_a_recurring_budget_does_not_absorb_a_one_off_one(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
):
    """The one-off includes both limits without changing the recurring default."""
    source, destination = test_categories[0], test_categories[1]
    month = date.today().replace(day=1)
    moving = await _make_budget(
        session, test_user, test_workspace, source.id,
        amount="300.00", month=month, is_recurring=True,
    )
    standing = await _make_budget(
        session, test_user, test_workspace, destination.id,
        amount="500.00", month=month, is_recurring=False,
    )
    destination_id, moving_id, standing_id = destination.id, moving.id, standing.id

    response = await client.delete(
        f"/api/categories/{source.id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(destination_id)},
    )

    assert response.status_code == 204
    session.expire_all()
    assert (await _reload(session, Budget, moving_id)).category_id == destination_id
    assert (await _reload(session, Budget, moving_id)).amount == Decimal("300.00")
    assert (await _reload(session, Budget, standing_id)).amount == Decimal("800.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("different_authors", [False, True], ids=["one-author", "two-authors"])
@pytest.mark.parametrize(
    ("source_rows", "destination_rows", "expected"),
    [
        pytest.param(
            [(1, "100", True)], [(3, "200", True)],
            [(1, "100", True), (2, "100", True), (3, "300", True), (4, "300", True)],
            id="source-starts-first",
        ),
        pytest.param(
            [(3, "100", True)], [(1, "200", True)],
            [(1, "200", True), (2, "200", True), (3, "300", True), (4, "300", True)],
            id="destination-starts-first",
        ),
        pytest.param(
            [(1, "100", True)], [(1, "200", True)],
            [(1, "300", True), (2, "300", True)],
            id="same-start-month",
        ),
        pytest.param(
            [(1, "100", True), (2, "50", False)], [(1, "200", True)],
            [(1, "300", True), (2, "250", False), (3, "300", True)],
            id="source-monthly-override",
        ),
        pytest.param(
            [(1, "100", True)], [(1, "200", True), (2, "50", False)],
            [(1, "300", True), (2, "150", False), (3, "300", True)],
            id="destination-monthly-override",
        ),
        pytest.param(
            [(1, "100", True), (2, "50", False)],
            [(1, "200", True), (2, "80", False)],
            [(1, "300", True), (2, "130", False), (3, "300", True)],
            id="both-monthly-overrides",
        ),
        pytest.param(
            [(1, "100", True), (7, "150", True)],
            [(3, "200", True), (9, "300", True)],
            [
                (1, "100", True), (3, "300", True), (6, "300", True),
                (7, "350", True), (9, "450", True), (10, "450", True),
            ],
            id="future-changes-on-both-sides",
        ),
    ],
)
async def test_category_transfer_preserves_effective_budget_schedule(
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_categories: list[Category],
    source_rows: list[tuple[int, str, bool]],
    destination_rows: list[tuple[int, str, bool]],
    expected: list[tuple[int, str, bool]],
    different_authors: bool,
):
    source, destination = test_categories[:2]
    destination_author = test_user
    if different_authors:
        destination_author = User(
            id=uuid.uuid4(), email="budget-author@example.com", hashed_password="synthetic",
        )
        session.add(destination_author)
        await session.commit()
    destination_budget_ids: list[uuid.UUID] = []
    for category, rows in ((source, source_rows), (destination, destination_rows)):
        for month, amount, recurring in rows:
            budget = await _make_budget(
                session, destination_author if category is destination else test_user,
                test_workspace, category.id,
                amount=amount, month=date(2025, month, 1), is_recurring=recurring,
            )
            if category is destination:
                destination_budget_ids.append(budget.id)

    assert await category_service.delete_category(
        session, source.id, test_workspace.id, transfer_to_id=destination.id
    )

    for month, amount, recurring in expected:
        budgets = await budget_service.get_budgets(
            session, test_workspace.id, month=date(2025, month, 1)
        )
        assert [(b.category_id, b.amount, b.is_recurring) for b in budgets] == [
            (destination.id, Decimal(amount), recurring)
        ]
    for budget_id in destination_budget_ids:
        assert (await _reload(session, Budget, budget_id)).user_id == destination_author.id


@pytest.mark.asyncio
async def test_a_category_cannot_be_transferred_into_itself(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    category = test_categories[0]
    response = await client.delete(
        f"/api/categories/{category.id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(category.id)},
    )

    assert response.status_code == 400
    assert "into itself" in response.json()["detail"]


@pytest.mark.asyncio
async def test_destination_has_to_exist(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    response = await client.delete(
        f"/api/categories/{test_categories[0].id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(uuid.uuid4())},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Destination category not found."


@pytest.mark.asyncio
async def test_a_destination_in_another_workspace_is_not_accepted(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_categories: list[Category],
):
    """The lookup is scoped, so a real id from elsewhere reads as not found."""
    from app.models.workspace import Workspace

    other = Workspace(name="Other", kind="personal", created_by_user_id=test_user.id)
    session.add(other)
    await session.commit()
    outsider = Category(
        user_id=test_user.id, workspace_id=other.id, name="Elsewhere"
    )
    session.add(outsider)
    await session.commit()

    response = await client.delete(
        f"/api/categories/{test_categories[0].id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(outsider.id)},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_unused_category_still_goes_without_a_destination(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    response = await client.delete(
        f"/api/categories/{test_categories[0].id}", headers=auth_headers
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_transfer_is_one_transaction(
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_account: Account,
    test_categories: list[Category],
    monkeypatch,
):
    """A delete that fails leaves the references where they were.

    The transactions are repointed before the category goes, so a failure
    between the two would otherwise move the history into a category the user
    never chose and keep the old one around.
    """
    source, destination = test_categories[0], test_categories[1]
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        category_id=source.id,
        description="UBER TRIP",
        amount=Decimal("25.50"),
        date=date.today(),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    source_id, destination_id = source.id, destination.id
    transaction_id = transaction.id

    async def fail_commit():
        raise RuntimeError("commit blew up")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        await category_service.delete_category(
            session, source_id, test_workspace.id, transfer_to_id=destination_id
        )
    monkeypatch.undo()
    await session.rollback()

    assert (await _reload(session, Transaction, transaction_id)).category_id == source_id
    assert await session.get(Category, source_id) is not None
