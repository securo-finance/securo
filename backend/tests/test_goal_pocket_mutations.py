"""Pocket invariants across financial mutation paths outside the allocator."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.app_clock import use_resolved_timezone
from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.goal import GoalAdjustmentCreate, GoalAllocationInput, GoalCreate
from app.schemas.transaction import TransactionImport
from app.services import goal_service, import_service, rule_service
from app.services.goal_allocation_service import replace_transaction_allocations
from tests.test_goal_pockets import create_pocket, post_transaction

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("balance", ["100.00", "-500.00"])
async def test_opening_balance_edit_keeps_assignments_valid(client, auth_headers, session, balance):
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Allocated opening",
            "type": "savings",
            "balance": "500",
            "currency": "BRL",
        },
    )
    assert response.status_code == 201
    account = await session.get(Account, uuid.UUID(response.json()["id"]))
    assert account is not None
    pocket = await create_pocket(client, auth_headers, account)
    opening = await session.scalar(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    assert opening is not None
    response = await client.patch(
        f"/api/transactions/{opening.id}",
        headers=auth_headers,
        json={
            "goal_allocations": [{"goal_id": pocket["id"], "amount": "400"}],
        },
    )
    assert response.status_code == 200
    response = await client.patch(
        f"/api/accounts/{account.id}", headers=auth_headers, json={"balance": balance}
    )
    assert response.status_code == 400
    response = await client.get(f"/api/transactions/{opening.id}", headers=auth_headers)
    assert Decimal(response.json()["amount"]) == 500
    assert response.json()["type"] == "credit"
    assert Decimal(response.json()["goal_allocations"][0]["amount"]) == 400


async def test_ignored_category_change_requires_clearing_assignments(
    client,
    auth_headers,
    test_account,
    test_user,
    session,
):
    category = Category(user_id=test_user.id, workspace_id=test_account.workspace_id, name="Normal")
    session.add(category)
    await session.commit()
    pocket = await create_pocket(client, auth_headers, test_account)
    response = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Deposit",
        "100",
        "credit",
        allocations=[(pocket["id"], "100")],
    )
    tx_id = response.json()["id"]
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"category_id": str(category.id)}
    )
    assert response.status_code == 200
    response = await client.patch(
        f"/api/categories/{category.id}", headers=auth_headers, json={"is_ignored": True}
    )
    assert response.status_code == 400
    assert "pocket assignments" in response.json()["detail"]
    response = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"goal_allocations": []}
    )
    assert response.status_code == 200
    response = await client.patch(
        f"/api/categories/{category.id}", headers=auth_headers, json={"is_ignored": True}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("ignored_destination", [False, True])
async def test_category_deletion_preserves_pocket_assignments(
    client, auth_headers, test_account, test_user, session, ignored_destination
):
    source = Category(
        user_id=test_user.id, workspace_id=test_account.workspace_id, name="Source"
    )
    destination = Category(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Destination",
        is_ignored=ignored_destination,
    )
    session.add_all([source, destination])
    await session.commit()
    source_id, destination_id = source.id, destination.id
    pocket = await create_pocket(client, auth_headers, test_account)
    response = await post_transaction(
        client, auth_headers, test_account, "Deposit", "100", "credit",
        category_id=source_id, allocations=[(pocket["id"], "100")],
    )
    assert response.status_code == 201
    transaction_id = response.json()["id"]

    response = await client.delete(
        f"/api/categories/{source_id}", headers=auth_headers,
        params={"transfer_to_category_id": str(destination_id)},
    )
    assert response.status_code == (409 if ignored_destination else 204)
    if ignored_destination:
        assert "Ignored transactions" in response.json()["detail"]
    session.expire_all()
    assert (await session.get(Category, source_id) is not None) == ignored_destination
    response = await client.get(f"/api/transactions/{transaction_id}", headers=auth_headers)
    assert response.json()["category_id"] == str(
        source_id if ignored_destination else destination_id
    )
    assert Decimal(response.json()["goal_allocations"][0]["amount"]) == 100

    if ignored_destination:
        response = await client.patch(
            f"/api/transactions/{transaction_id}", headers=auth_headers,
            json={"goal_allocations": []},
        )
        assert response.status_code == 200
        response = await client.delete(
            f"/api/categories/{source_id}", headers=auth_headers,
            params={"transfer_to_category_id": str(destination_id)},
        )
        assert response.status_code == 204


@pytest.mark.parametrize("apply_all", [False, True])
async def test_rules_remove_assignments_when_ignoring_a_transaction(
    client,
    auth_headers,
    test_account,
    test_user,
    session,
    apply_all,
):
    pocket = await create_pocket(client, auth_headers, test_account, initial="100")
    response = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Ignore this debit",
        "40",
        "debit",
        allocations=[(pocket["id"], "40")],
    )
    tx_id = uuid.UUID(response.json()["id"])
    rule = Rule(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Ignore debit",
        conditions_op="AND",
        conditions=[{"field": "description", "op": "contains", "value": "Ignore this"}],
        actions=[{"op": "ignore"}],
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    if apply_all:
        await rule_service.apply_all_rules(session, test_account.workspace_id)
    else:
        await rule_service.apply_single_rule(session, test_account.workspace_id, rule)
    response = await client.get(f"/api/transactions/{tx_id}", headers=auth_headers)
    assert response.json()["is_ignored"] is True
    assert response.json()["goal_allocations"] == []
    response = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert Decimal(response.json()["current_amount"]) == 100


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_rejected_rule_application_preserves_rule_and_pocket(
    client,
    auth_headers,
    test_account,
    operation,
):
    pocket = await create_pocket(client, auth_headers, test_account)
    credit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Protected deposit",
        "400",
        "credit",
        allocations=[(pocket["id"], "400")],
    )
    assert credit.status_code == 201
    debit = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Spent deposit",
        "100",
        "debit",
        allocations=[(pocket["id"], "100")],
    )
    assert debit.status_code == 201
    payload = {
        "name": "Protected rule",
        "conditions": [{"field": "description", "op": "equals", "value": "Protected deposit"}],
        "actions": [{"op": "ignore", "value": True}],
    }
    if operation == "create":
        response = await client.post("/api/rules", headers=auth_headers, json=payload)
        rule_id = None
    else:
        original_actions = [{"op": "append_notes", "value": "Original action"}]
        original = await client.post(
            "/api/rules",
            headers=auth_headers,
            json={**payload, "actions": original_actions, "apply_to_existing": False},
        )
        assert original.status_code == 201
        rule_id = original.json()["id"]
        response = await client.patch(
            f"/api/rules/{rule_id}",
            headers=auth_headers,
            json={"name": "Rejected edit", "actions": payload["actions"]},
        )
    assert response.status_code == 400
    assert "spending assignments" in response.json()["detail"]
    stored = (await client.get("/api/rules", headers=auth_headers)).json()
    if rule_id is None:
        assert all(rule["name"] != payload["name"] for rule in stored)
    else:
        saved = next(rule for rule in stored if rule["id"] == rule_id)
        assert saved["name"] == payload["name"]
        assert saved["actions"] == original_actions
    response = await client.get(f"/api/transactions/{credit.json()['id']}", headers=auth_headers)
    assert response.json()["is_ignored"] is False
    assert Decimal(response.json()["goal_allocations"][0]["amount"]) == 400
    response = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert Decimal(response.json()["current_amount"]) == 300


async def test_imported_ignored_placeholder_releases_its_assignment(
    client,
    auth_headers,
    test_account,
    test_user,
    session,
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from app.services import recurring_match_service

    pocket = await create_pocket(client, auth_headers, test_account, initial="100")
    response = await post_transaction(
        client,
        auth_headers,
        test_account,
        "Bank charge",
        "40",
        "debit",
        allocations=[(pocket["id"], "40")],
    )
    placeholder = await session.get(Transaction, uuid.UUID(response.json()["id"]))
    assert placeholder is not None
    placeholder.source = "recurring"
    rule = Rule(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Ignore bank charge",
        conditions_op="AND",
        conditions=[{"field": "description", "op": "contains", "value": "Bank charge"}],
        actions=[{"op": "ignore"}],
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    monkeypatch.setattr(
        recurring_match_service,
        "find_placeholder_for_incoming",
        AsyncMock(return_value=placeholder),
    )
    imported, skipped, _, _ = await import_service.import_transactions(
        session,
        test_account.workspace_id,
        test_user.id,
        test_account.id,
        [
            TransactionImport(
                description="Bank charge",
                amount=Decimal("40"),
                date=date.today(),
                type="debit",
                currency=test_account.currency,
            )
        ],
        "csv",
        detect_duplicates=False,
    )
    assert (imported, skipped) == (1, 0)
    response = await client.get(f"/api/transactions/{placeholder.id}", headers=auth_headers)
    assert response.json()["is_ignored"] is True
    assert response.json()["goal_allocations"] == []
    response = await client.get(f"/api/goals/{pocket['id']}", headers=auth_headers)
    assert Decimal(response.json()["current_amount"]) == 100


async def test_pocket_reservations_follow_the_workspace_day(session, test_user, test_workspace):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 5, 18, 23, 30, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    class HostDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 18)

    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Tokyo",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    for day, amount in [(18, "30"), (19, "40")]:
        session.add(
            Transaction(
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                account_id=account.id,
                description="Local day",
                amount=Decimal(amount),
                currency="BRL",
                date=date(2026, 5, day),
                type="credit",
                source="manual",
                status="posted",
            )
        )
    await session.commit()
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch.object(goal_service, "date", HostDate),
    ):
        with use_resolved_timezone(ZoneInfo("Asia/Tokyo")):
            goal = await goal_service.create_goal(
                session,
                test_workspace.id,
                test_user.id,
                GoalCreate(
                    name="Local pocket",
                    target_amount=100,
                    tracking_type="pocket",
                    account_id=account.id,
                    initial_allocation=60,
                ),
            )
            assert goal.account_balance == 70
            await goal_service.adjust_pocket(
                session, goal.id, test_workspace.id, test_user.id, GoalAdjustmentCreate(amount=5)
            )
            tx = await session.scalar(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.date == date(2026, 5, 19),
                )
            )
            assert tx is not None
            await replace_transaction_allocations(
                session,
                test_workspace.id,
                test_user.id,
                tx,
                [GoalAllocationInput(goal_id=goal.id, amount=5)],
            )
            await session.commit()
            goal = await goal_service.get_goal(session, goal.id, test_workspace.id, test_user.id)
            assert goal is not None
            assert goal.current_amount == 70
            assert goal.account_available == 0
        with use_resolved_timezone(ZoneInfo("UTC")):
            goal = await goal_service.get_goal(session, goal.id, test_workspace.id, test_user.id)
            assert goal is not None
            assert goal.account_balance == 30
            assert goal.is_underfunded is True


async def _import_credit(session, account, user, amount: str = "200") -> tuple[uuid.UUID, Transaction]:
    *_, import_log_id = await import_service.import_transactions(
        session,
        account.workspace_id,
        user.id,
        account.id,
        [
            TransactionImport(
                description="Salary",
                amount=Decimal(amount),
                date=date.today(),
                type="credit",
                currency=account.currency,
            )
        ],
        "import",
        detect_duplicates=False,
    )
    imported = await session.scalar(
        select(Transaction).where(Transaction.import_id == import_log_id)
    )
    assert imported is not None
    return import_log_id, imported


async def _pocket_amount(client, headers, pocket) -> Decimal:
    response = await client.get(f"/api/goals/{pocket['id']}", headers=headers)
    return Decimal(response.json()["current_amount"])


async def test_deleting_an_import_refuses_to_orphan_pocket_spending(
    client, auth_headers, test_account, test_user, session
):
    pocket = await create_pocket(client, auth_headers, test_account)
    import_log_id, credit = await _import_credit(session, test_account, test_user)
    response = await client.patch(
        f"/api/transactions/{credit.id}",
        headers=auth_headers,
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
    )
    assert response.status_code == 200, response.text
    response = await post_transaction(
        client, auth_headers, test_account, "Laptop", "60", "debit",
        allocations=[(pocket["id"], "60")],
    )
    assert response.status_code == 201, response.text

    response = await client.delete(f"/api/import-logs/{import_log_id}", headers=auth_headers)

    assert response.status_code == 400
    assert (await client.get(f"/api/transactions/{credit.id}", headers=auth_headers)).status_code == 200
    assert await _pocket_amount(client, auth_headers, pocket) == 40


async def test_deleting_an_import_releases_its_pocket_assignments(
    client, auth_headers, test_account, test_user, session
):
    pocket = await create_pocket(client, auth_headers, test_account)
    import_log_id, credit = await _import_credit(session, test_account, test_user)
    response = await client.patch(
        f"/api/transactions/{credit.id}",
        headers=auth_headers,
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
    )
    assert response.status_code == 200, response.text

    response = await client.delete(f"/api/import-logs/{import_log_id}", headers=auth_headers)

    assert response.status_code == 204
    assert await _pocket_amount(client, auth_headers, pocket) == 0


async def test_pockets_are_rejected_on_credit_card_accounts(
    client, auth_headers, test_account, test_user, session
):
    card = Account(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Card",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(card)
    await session.commit()

    response = await client.post(
        "/api/goals",
        headers=auth_headers,
        json={
            "name": "Card pocket",
            "target_amount": "100",
            "tracking_type": "pocket",
            "account_id": str(card.id),
        },
    )

    assert response.status_code == 400
    assert "credit card" in response.json()["detail"]


async def test_synced_opening_balance_cannot_back_a_pocket(
    client, auth_headers, test_account, test_user, session
):
    pocket = await create_pocket(client, auth_headers, test_account)
    opening = Transaction(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Saldo inicial",
        amount=Decimal("500"),
        currency=test_account.currency,
        date=date.today(),
        type="credit",
        source="opening_balance",
    )
    session.add(opening)
    await session.commit()

    response = await client.patch(
        f"/api/transactions/{opening.id}",
        headers=auth_headers,
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
    )

    assert response.status_code == 400
    assert "opening" in response.json()["detail"]


async def test_background_release_never_raises_when_spending_depends_on_the_row(
    client, auth_headers, test_account, test_user, session
):
    from app.services.goal_allocation_service import release_for_background_delete

    pocket = await create_pocket(client, auth_headers, test_account)
    _, credit = await _import_credit(session, test_account, test_user)
    await client.patch(
        f"/api/transactions/{credit.id}",
        headers=auth_headers,
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
    )
    response = await post_transaction(
        client, auth_headers, test_account, "Laptop", "60", "debit",
        allocations=[(pocket["id"], "60")],
    )
    debit = await session.get(Transaction, uuid.UUID(response.json()["id"]))
    assert debit is not None

    workspace_id = test_account.workspace_id
    assert await release_for_background_delete(session, workspace_id, credit) is False
    assert await _pocket_amount(client, auth_headers, pocket) == 40
    assert await release_for_background_delete(session, workspace_id, debit) is True
    await session.commit()
    assert await _pocket_amount(client, auth_headers, pocket) == 100
