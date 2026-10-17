import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response

from app.models.account import Account
from app.models.category import Category
from app.models.goal import Goal, GoalAllocation
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.goal import GoalAllocationInput
from app.services.goal_allocation_service import (
    _transaction_allocations_for_update,
    replace_transaction_allocations,
)
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession


def test_goal_allocation_lock_targets_only_allocation_rows():
    statement = _transaction_allocations_for_update(uuid.uuid4(), uuid.uuid4())

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert sql.rstrip().endswith("FOR UPDATE OF goal_allocations")


async def create_pocket(
    client: AsyncClient,
    headers: dict,
    account: Account,
    *,
    name: str = "Monitor",
    initial: str = "0",
) -> dict:
    response = await client.post(
        "/api/goals",
        json={
            "name": name,
            "target_amount": "900.00",
            "currency": "USD",  # Pocket must replace this with account currency.
            "tracking_type": "pocket",
            "account_id": str(account.id),
            "initial_allocation": initial,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def post_transaction(
    client: AsyncClient,
    headers: dict,
    account: Account,
    description: str,
    amount: str,
    transaction_type: str,
    *,
    allocations: list[tuple[str, str]] | None = None,
    status: str = "posted",
    category_id: uuid.UUID | None = None,
) -> Response:
    payload = {
        "account_id": str(account.id),
        "description": description,
        "amount": amount,
        "date": date.today().isoformat(),
        "type": transaction_type,
        "currency": account.currency,
        "status": status,
    }
    if category_id is not None:
        payload["category_id"] = str(category_id)
    if allocations is not None:
        payload["goal_allocations"] = [
            {"goal_id": goal_id, "amount": allocation_amount}
            for goal_id, allocation_amount in allocations
        ]
    return await client.post("/api/transactions", json=payload, headers=headers)


@pytest.mark.asyncio
async def test_pocket_opening_adjustment_and_activity(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account, initial="500.00")

    assert pocket["currency"] == "BRL"
    assert float(pocket["current_amount"]) == 500
    assert float(pocket["account_reserved_total"]) == 500
    assert float(pocket["account_available"]) == 1000
    assert pocket["is_underfunded"] is False

    adjusted = await client.post(
        f"/api/goals/{pocket['id']}/adjustments",
        json={"amount": "-125.00"},
        headers=auth_headers,
    )
    assert adjusted.status_code == 200, adjusted.text
    assert float(adjusted.json()["current_amount"]) == 375

    activity = await client.get(f"/api/goals/{pocket['id']}/activity", headers=auth_headers)
    assert activity.status_code == 200
    assert {row["source"] for row in activity.json()} == {"adjustment", "opening"}


@pytest.mark.asyncio
async def test_pocket_cannot_double_reserve_account_balance(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    first = await create_pocket(client, auth_headers, test_account, initial="1200.00")
    assert float(first["current_amount"]) > float(first["target_amount"])
    response = await client.post(
        "/api/goals",
        json={
            "name": "Desk",
            "target_amount": "500.00",
            "tracking_type": "pocket",
            "account_id": str(test_account.id),
            "initial_allocation": "400.00",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "available balance" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unallocated_debit_marks_account_pockets_underfunded(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    session: AsyncSession,
):
    pocket = await create_pocket(client, auth_headers, test_account, initial="1400.00")

    debit = await post_transaction(
        client, auth_headers, test_account, "Unexpected bill", "200.00", "debit"
    )
    assert debit.status_code == 201, debit.text

    # Connected accounts use the provider balance as their authoritative
    # current value. A later sync brings the external debit into that value.
    test_account.balance = Decimal("1300.00")
    await session.commit()

    refreshed = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["is_underfunded"] is True
    assert float(refreshed.json()["account_available"]) == -100


@pytest.mark.asyncio
async def test_deleting_pocket_releases_reservation_without_deleting_transaction(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account)
    transaction = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Pocket deposit",
        "300.00",
        "credit",
        allocations=[(pocket["id"], "300.00")],
    )
    assert transaction.status_code == 201, transaction.text

    deleted = await client.delete(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    transaction_after = await client.get(
        f"/api/transactions/{transaction.json()['id']}", headers=auth_headers
    )
    assert transaction_after.status_code == 200
    assert transaction_after.json()["goal_allocations"] == []

    replacement = await create_pocket(
        client,
        auth_headers,
        test_account,
        name="Replacement",
        initial="1500.00",
    )
    assert float(replacement["account_available"]) == 0


@pytest.mark.asyncio
async def test_posted_transactions_add_and_remove_pocket_money(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    monitor = await create_pocket(
        client, auth_headers, test_account, name="Monitor", initial="300.00"
    )
    keyboard = await create_pocket(
        client, auth_headers, test_account, name="Keyboard", initial="100.00"
    )

    credit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Savings transfer",
        "200.00",
        "credit",
        allocations=[(monitor["id"], "150.00"), (keyboard["id"], "50.00")],
    )
    assert credit.status_code == 201, credit.text
    assert len(credit.json()["goal_allocations"]) == 2

    debit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Monitor purchase",
        "250.00",
        "debit",
        allocations=[(monitor["id"], "250.00")],
    )
    assert debit.status_code == 201, debit.text
    assert float(debit.json()["goal_allocations"][0]["amount"]) == -250

    monitor_read = await client.get(f"/api/goals/{monitor['id']}", headers=auth_headers)
    keyboard_read = await client.get(f"/api/goals/{keyboard['id']}", headers=auth_headers)
    assert float(monitor_read.json()["current_amount"]) == 200
    assert float(keyboard_read.json()["current_amount"]) == 150


@pytest.mark.asyncio
async def test_pending_transaction_cannot_change_pocket(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account)
    response = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Pending transfer",
        "50.00",
        "credit",
        allocations=[(pocket["id"], "50.00")],
        status="pending",
    )
    assert response.status_code == 400
    assert "posted" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pocket_status_rules_allow_spending_and_direct_release(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account, initial="100.00")
    paused = await client.patch(
        f"/api/goals/{pocket['id']}",
        json={"status": "paused"},
        headers=auth_headers,
    )
    assert paused.status_code == 200, paused.text

    credit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Paused deposit",
        "10.00",
        "credit",
        allocations=[(pocket["id"], "10.00")],
    )
    assert credit.status_code == 400
    assert "active" in credit.json()["detail"]

    debit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Spend paused reserve",
        "20.00",
        "debit",
        allocations=[(pocket["id"], "20.00")],
    )
    assert debit.status_code == 201, debit.text

    archived = await client.patch(
        f"/api/goals/{pocket['id']}",
        json={"status": "archived"},
        headers=auth_headers,
    )
    assert archived.status_code == 200, archived.text

    archived_debit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Archived spend",
        "10.00",
        "debit",
        allocations=[(pocket["id"], "10.00")],
    )
    assert archived_debit.status_code == 400
    assert "Archived" in archived_debit.json()["detail"]

    released = await client.post(
        f"/api/goals/{pocket['id']}/adjustments",
        json={"amount": "-30.00"},
        headers=auth_headers,
    )
    assert released.status_code == 200, released.text
    assert float(released.json()["current_amount"]) == 50


@pytest.mark.asyncio
async def test_transfer_allocates_each_leg_without_creating_extra_money(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    test_user: User,
    session: AsyncSession,
):
    destination = Account(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Savings",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(destination)
    await session.commit()
    await session.refresh(destination)

    source_pocket = await create_pocket(
        client, auth_headers, test_account, name="Source reserve", initial="300.00"
    )
    destination_pocket = await create_pocket(
        client, auth_headers, destination, name="Monitor", initial="0"
    )
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(destination.id),
            "amount": "200.00",
            "date": date.today().isoformat(),
            "description": "Move to savings",
            "from_goal_allocations": [{"goal_id": source_pocket["id"], "amount": "50.00"}],
            "to_goal_allocations": [{"goal_id": destination_pocket["id"], "amount": "150.00"}],
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert float(body["debit"]["goal_allocations"][0]["amount"]) == -50
    assert float(body["credit"]["goal_allocations"][0]["amount"]) == 150

    source_read = await client.get(f"/api/goals/{source_pocket['id']}", headers=auth_headers)
    destination_read = await client.get(
        f"/api/goals/{destination_pocket['id']}", headers=auth_headers
    )
    assert float(source_read.json()["current_amount"]) == 250
    assert float(destination_read.json()["current_amount"]) == 150


@pytest.mark.asyncio
async def test_transaction_amount_change_requires_valid_replacement(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account)
    created = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Deposit",
        "100.00",
        "credit",
        allocations=[(pocket["id"], "100.00")],
    )
    assert created.status_code == 201, created.text
    transaction_id = created.json()["id"]

    rejected = await client.patch(
        f"/api/transactions/{transaction_id}",
        json={"amount": "80.00"},
        headers=auth_headers,
    )
    assert rejected.status_code == 400

    replaced = await client.patch(
        f"/api/transactions/{transaction_id}",
        json={
            "amount": "80.00",
            "goal_allocations": [{"goal_id": pocket["id"], "amount": "80.00"}],
        },
        headers=auth_headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert float(replaced.json()["goal_allocations"][0]["amount"]) == 80

    deleted = await client.delete(f"/api/transactions/{transaction_id}", headers=auth_headers)
    assert deleted.status_code == 204
    goal_after_delete = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert float(goal_after_delete.json()["current_amount"]) == 0


@pytest.mark.asyncio
async def test_transaction_can_clear_allocations_while_becoming_pending(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account)
    created = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Deposit before pending",
        "100.00",
        "credit",
        allocations=[(pocket["id"], "100.00")],
    )
    assert created.status_code == 201, created.text

    updated = await client.patch(
        f"/api/transactions/{created.json()['id']}",
        json={"status": "pending", "goal_allocations": []},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "pending"
    assert updated.json()["goal_allocations"] == []

    pocket_after = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert float(pocket_after.json()["current_amount"]) == 0


@pytest.mark.asyncio
async def test_transfer_amount_change_rejects_allocated_paired_leg(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    test_user: User,
    session: AsyncSession,
    test_categories: list[Category],
):
    destination = Account(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Transfer destination",
        type="savings",
        balance=Decimal("0"),
        currency=test_account.currency,
    )
    session.add(destination)
    await session.commit()
    await session.refresh(destination)

    source_pocket = await create_pocket(
        client, auth_headers, test_account, name="Source", initial="100.00"
    )
    destination_pocket = await create_pocket(client, auth_headers, destination, name="Destination")
    created = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(destination.id),
            "amount": "100.00",
            "date": date.today().isoformat(),
            "description": "Allocated transfer",
            "from_goal_allocations": [{"goal_id": source_pocket["id"], "amount": "50.00"}],
            "to_goal_allocations": [{"goal_id": destination_pocket["id"], "amount": "100.00"}],
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text

    debit = created.json()["debit"]
    credit = created.json()["credit"]
    rejected = await client.patch(
        f"/api/transactions/{debit['id']}",
        json={
            "amount": "80.00",
            "goal_allocations": [{"goal_id": source_pocket["id"], "amount": "50.00"}],
        },
        headers=auth_headers,
    )
    assert rejected.status_code == 400
    assert "paired transfer" in rejected.json()["detail"]

    debit_after = await client.get(f"/api/transactions/{debit['id']}", headers=auth_headers)
    credit_after = await client.get(f"/api/transactions/{credit['id']}", headers=auth_headers)
    assert float(debit_after.json()["amount"]) == 100
    assert float(credit_after.json()["amount"]) == 100

    increased = await client.patch(
        f"/api/transactions/{debit['id']}", json={"amount": "120.00"}, headers=auth_headers
    )
    assert increased.status_code == 200, increased.text
    credit_after = await client.get(f"/api/transactions/{credit['id']}", headers=auth_headers)
    assert float(credit_after.json()["amount"]) == 120
    assert float(credit_after.json()["goal_allocations"][0]["amount"]) == 100
    ignored = test_categories[0]
    ignored.is_ignored = True
    await session.commit()
    recategorized = await client.patch(
        f"/api/transactions/{debit['id']}",
        json={"category_id": str(ignored.id), "apply_to_transfer_pair": True},
        headers=auth_headers,
    )
    assert recategorized.status_code == 400, recategorized.text
    credit_after = await client.get(f"/api/transactions/{credit['id']}", headers=auth_headers)
    assert credit_after.json()["category_id"] is None


@pytest.mark.asyncio
async def test_deleting_account_deletes_its_pockets(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
):
    account_response = await client.post(
        "/api/accounts",
        json={
            "name": "Disposable savings",
            "type": "savings",
            "balance": "500.00",
            "currency": "BRL",
        },
        headers=auth_headers,
    )
    assert account_response.status_code == 201, account_response.text
    account = await session.get(Account, uuid.UUID(account_response.json()["id"]))
    assert account is not None
    pocket = await create_pocket(client, auth_headers, account, initial="100.00")

    deleted = await client.delete(f"/api/accounts/{account.id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text

    goal_after = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert goal_after.status_code == 404


@pytest.mark.asyncio
async def test_allocation_amounts_require_cent_precision(
    client: AsyncClient, auth_headers: dict, test_account: Account
):
    pocket = await create_pocket(client, auth_headers, test_account)

    transaction = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Sub-cent allocation",
        "10.00",
        "credit",
        allocations=[(pocket["id"], "0.001")],
    )
    assert transaction.status_code == 422

    adjustment = await client.post(
        f"/api/goals/{pocket['id']}/adjustments",
        json={"amount": "0.001"},
        headers=auth_headers,
    )
    assert adjustment.status_code == 422


@pytest.mark.asyncio
async def test_viewer_cannot_adjust_pocket(
    client: AsyncClient,
    auth_headers: dict,
    viewer_auth_headers: dict,
    test_account: Account,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    response = await client.post(
        f"/api/goals/{pocket['id']}/adjustments",
        json={"amount": "10.00"},
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "field", "value", "error"),
    [
        (Transaction, "status", "pending", "Only posted"),
        (Transaction, "amount", Decimal("50"), "transaction amount"),
        (Transaction, "is_ignored", True, "Ignored transactions"),
        (Goal, "status", "paused", "Only active"),
        (Account, "currency", "USD", "same currency"),
        (Account, "balance", Decimal("0"), "available balance"),
    ],
)
async def test_allocation_reloads_stale_transaction_goal_and_account(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    test_user: User,
    session: AsyncSession,
    model,
    field,
    value,
    error,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    created = await post_transaction(
        client, auth_headers, test_account, "Unassigned deposit", "100", "credit"
    )
    transaction = await session.get(Transaction, uuid.UUID(created.json()["id"]))
    assert transaction is not None
    row_id = {Transaction: transaction.id, Goal: uuid.UUID(pocket["id"]), Account: test_account.id}[
        model
    ]
    stale_row = await session.get(model, row_id)
    assert stale_row is not None
    previous = getattr(stale_row, field)
    await session.execute(
        update(model)
        .where(model.id == row_id)
        .values({field: value})
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    assert getattr(stale_row, field) == previous

    with pytest.raises(ValueError, match=error):
        await replace_transaction_allocations(
            session,
            test_account.workspace_id,
            test_user.id,
            transaction,
            [GoalAllocationInput(goal_id=uuid.UUID(pocket["id"]), amount=Decimal("100"))],
        )
    assert getattr(stale_row, field) == value
    assert await session.scalar(select(func.count(GoalAllocation.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["create", "single", "bulk"])
async def test_ignored_category_cannot_create_or_retain_allocations(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    test_categories: list[Category],
    session: AsyncSession,
    path: str,
):
    ignored = test_categories[0]
    ignored.is_ignored = True
    await session.commit()
    pocket = await create_pocket(client, auth_headers, test_account)
    if path == "create":
        response = await post_transaction(
            client,
            auth_headers,
            test_account,
            "Ignored deposit",
            "100",
            "credit",
            allocations=[(pocket["id"], "100")],
            category_id=ignored.id,
        )
    else:
        created = await post_transaction(
            client,
            auth_headers,
            test_account,
            "Allocated deposit",
            "100",
            "credit",
            allocations=[(pocket["id"], "100")],
        )
        transaction_id = created.json()["id"]
        url = (
            f"/api/transactions/{transaction_id}"
            if path == "single"
            else "/api/transactions/bulk-categorize"
        )
        payload = {"category_id": str(ignored.id)}
        if path == "bulk":
            payload["transaction_ids"] = [transaction_id]
        response = await client.patch(url, json=payload, headers=auth_headers)
        stored = await client.get(f"/api/transactions/{transaction_id}", headers=auth_headers)
        assert stored.json()["category_id"] is None
        assert float(stored.json()["goal_allocations"][0]["amount"]) == 100
    assert response.status_code == 400, response.text
    assert "Ignored transactions" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "pending"},
        {"is_ignored": True},
        {"type": "debit"},
        {"currency": "USD"},
        {"account_id": "other"},
    ],
)
async def test_incompatible_edit_requires_explicit_cleanup(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    session: AsyncSession,
    changes: dict,
):
    changes = dict(changes)
    if "account_id" in changes:
        other = Account(
            user_id=test_account.user_id,
            workspace_id=test_account.workspace_id,
            name="Other account",
            type="savings",
            balance=Decimal("0"),
            currency=test_account.currency,
        )
        session.add(other)
        await session.commit()
        changes["account_id"] = str(other.id)
    pocket = await create_pocket(client, auth_headers, test_account)
    created = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Allocated deposit",
        "100",
        "credit",
        allocations=[(pocket["id"], "100")],
    )
    transaction_id = created.json()["id"]
    rejected = await client.patch(
        f"/api/transactions/{transaction_id}", json=changes, headers=auth_headers
    )
    assert rejected.status_code == 400, rejected.text
    unchanged = await client.get(f"/api/transactions/{transaction_id}", headers=auth_headers)
    assert float(unchanged.json()["goal_allocations"][0]["amount"]) == 100
    for field in changes:
        assert unchanged.json()[field] == created.json()[field]

    cleared = await client.patch(
        f"/api/transactions/{transaction_id}",
        json={**changes, "goal_allocations": []},
        headers=auth_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["goal_allocations"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["clear", "reduce", "delete", "bulk-delete"])
async def test_spent_deposit_cannot_leave_negative_pocket(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    action: str,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    credit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Savings deposit",
        "100",
        "credit",
        allocations=[(pocket["id"], "100")],
    )
    debit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Pocket purchase",
        "80",
        "debit",
        allocations=[(pocket["id"], "80")],
    )
    credit_id = credit.json()["id"]
    if action == "delete":
        response = await client.delete(f"/api/transactions/{credit_id}", headers=auth_headers)
    elif action == "bulk-delete":
        response = await client.post(
            "/api/transactions/bulk-delete",
            json={"transaction_ids": [credit_id]},
            headers=auth_headers,
        )
    else:
        allocations = [] if action == "clear" else [{"goal_id": pocket["id"], "amount": "50"}]
        response = await client.patch(
            f"/api/transactions/{credit_id}",
            json={"goal_allocations": allocations},
            headers=auth_headers,
        )
    assert response.status_code == 400, response.text
    assert "spending assignments" in response.json()["detail"]
    stored = await client.get(f"/api/transactions/{credit_id}", headers=auth_headers)
    assert float(stored.json()["goal_allocations"][0]["amount"]) == 100
    remaining = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert float(remaining.json()["current_amount"]) == 20

    removed_together = await client.post(
        "/api/transactions/bulk-delete",
        json={"transaction_ids": [credit_id, debit.json()["id"]]},
        headers=auth_headers,
    )
    assert removed_together.status_code == 200, removed_together.text
    assert removed_together.json()["deleted"] == 2
    remaining = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert float(remaining.json()["current_amount"]) == 0


@pytest.mark.asyncio
async def test_valid_correction_and_release_are_allowed_when_account_underfunded(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    session: AsyncSession,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    created = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Allocated deposit",
        "100",
        "credit",
        allocations=[(pocket["id"], "100")],
    )
    test_account.balance = Decimal("20")
    await session.commit()
    transaction_id = created.json()["id"]
    corrected = await client.patch(
        f"/api/transactions/{transaction_id}",
        json={"amount": "110"},
        headers=auth_headers,
    )
    assert corrected.status_code == 200, corrected.text
    released = await client.patch(
        f"/api/transactions/{transaction_id}",
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "50"}]},
        headers=auth_headers,
    )
    assert released.status_code == 200, released.text
    remaining = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert remaining.json()["is_underfunded"] is True
    assert float(remaining.json()["current_amount"]) == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["future", "all"])
async def test_scoped_installment_edit_validates_allocated_siblings(
    client: AsyncClient,
    auth_headers: dict,
    test_account: Account,
    scope: str,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    series = await client.post(
        "/api/transactions/installments",
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Series deposit",
                "amount": "100",
                "date": date.today().isoformat(),
                "type": "credit",
                "currency": test_account.currency,
            },
            "installments": 2,
        },
        headers=auth_headers,
    )
    assert series.status_code == 201, series.text
    first, second = series.json()
    assigned = await client.patch(
        f"/api/transactions/{second['id']}",
        json={"status": "posted", "goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
        headers=auth_headers,
    )
    assert assigned.status_code == 200, assigned.text
    rejected = await client.patch(
        f"/api/transactions/{first['id']}",
        json={"amount": "50", "apply_to": scope},
        headers=auth_headers,
    )
    assert rejected.status_code == 400, rejected.text
    for transaction_id in (first["id"], second["id"]):
        stored = await client.get(f"/api/transactions/{transaction_id}", headers=auth_headers)
        assert float(stored.json()["amount"]) == 100
