"""Notifications inside the app (ADR 0041): what is told, to whom, once, and until when."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import required_scope
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.bank_connection import BankConnection
from app.models.budget import Budget
from app.models.group_settlement import GroupSettlement
from app.models.invoice import Invoice
from app.models.notification import Notification
from app.models.plugin_connection import PluginConnection
from app.models.reconciliation import ReconciliationSuggestion
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.base import SessionExpiredError
from app.schemas.group_settlement import GroupSettlementCreate
from app.schemas.transaction import TransactionCreate
from app.schemas.transaction_split import TransactionSplitsInput
from app.services import (
    backup_service,
    budget_service,
    connection_service,
    group_service,
    notification_service,
    reconciliation_rule_service,
    settlement_service,
    transaction_service,
)
from app.services.module_service import resolve_modules

pytestmark = pytest.mark.asyncio


def _failure(code: str) -> dict:
    return {"last_sync_failure": {"code": code, "at": datetime.now(timezone.utc).isoformat(), "detail": None}}


async def _open(session: AsyncSession, kind: str | None = None) -> list[Notification]:
    query = select(Notification).where(Notification.resolved_at.is_(None))
    if kind:
        query = query.where(Notification.kind == kind)
    return list((await session.execute(query)).scalars())


async def _evaluate(session: AsyncSession, workspace: Workspace) -> None:
    await notification_service.evaluate_workspace(session, workspace.id)


async def _viewer_id(session: AsyncSession) -> uuid.UUID:
    return (await session.execute(select(User.id).where(User.email == "viewer-role@example.com"))).scalar_one()


# --- Connections ------------------------------------------------------------


async def test_a_connection_that_needs_someone_is_told_once_and_resolved(
    session: AsyncSession, test_workspace: Workspace, test_connection: BankConnection
):
    test_connection.settings = _failure("consent_expired")
    await session.commit()
    await _evaluate(session, test_workspace)
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "connection_error")
    assert row.data == {
        "name": test_connection.display_name or test_connection.institution_name,
        "connection_id": str(test_connection.id),
        "code": "consent_expired",
    }

    test_connection.settings = _failure("reauthorize_at_provider")
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "connection_error")
    assert row.data["code"] == "reauthorize_at_provider"

    test_connection.settings = {}
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []


async def test_a_passing_failure_waits_for_three_in_a_row(
    session: AsyncSession, test_workspace: Workspace, test_connection: BankConnection
):
    for failures, told in ((1, 0), (2, 0), (3, 1)):
        test_connection.status = "error"
        test_connection.settings = {**_failure("provider_error"), "sync_backoff": {"failures": failures}}
        await session.commit()
        await _evaluate(session, test_workspace)
        assert len(await _open(session, "connection_error")) == told


async def test_a_condition_that_flaps_stays_read_and_asks_again_after_a_week(
    session: AsyncSession, test_workspace: Workspace, test_connection: BankConnection
):
    test_connection.settings = _failure("psu_present_required")
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session)
    row.read_at = datetime.now(timezone.utc)
    for settings in ({}, _failure("psu_present_required")):
        test_connection.settings = settings
        await session.commit()
        await _evaluate(session, test_workspace)
    [row] = await _open(session)
    assert row.read_at is not None

    row.notified_at = datetime.now(timezone.utc) - timedelta(days=8)
    await session.commit()
    await _evaluate(session, test_workspace)
    await session.refresh(row)
    assert row.read_at is None


async def test_a_sync_that_fails_tells_at_once_and_disconnecting_ends_it(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection
):
    connection_id, workspace_id, user_id = test_connection.id, test_workspace.id, test_user.id
    provider = AsyncMock()
    provider.refresh_credentials.side_effect = SessionExpiredError("gone")
    with patch("app.services.connection_service.get_provider", return_value=provider):
        with pytest.raises(SessionExpiredError):
            await connection_service.sync_connection(session, connection_id, workspace_id, user_id)
    [row] = await _open(session, "connection_error")
    assert row.data["code"] == "consent_expired"

    await connection_service.delete_connection(session, connection_id, workspace_id, keep_accounts=True)
    assert await _open(session) == []


async def test_notifications_never_fail_a_sync(
    session: AsyncSession, test_connection: BankConnection, monkeypatch
):
    monkeypatch.setattr(notification_service, "_reconcile", AsyncMock(side_effect=RuntimeError("boom")))
    await notification_service.connection_changed(session, test_connection)
    test_connection.status = "error"
    await session.commit()


async def test_a_plugin_connection_is_told_only_after_it_once_worked(
    session: AsyncSession, test_workspace: Workspace, monkeypatch
):
    monkeypatch.setattr(notification_service, "feature_flag", lambda _: True)
    connection = PluginConnection(
        workspace_id=test_workspace.id, plugin_id="me/bank", integration="bank", name="My bank", status="needs_auth"
    )
    session.add(connection)
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []

    connection.last_error = "NeedsAuth: expired"
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session)
    assert row.data["plugin_id"] == "me/bank" and row.data["name"] == "My bank"


# --- Recurring --------------------------------------------------------------


def _bill(user: User, workspace: Workspace, account: Account, days_late: int, **extra) -> RecurringTransaction:
    due = app_today() - timedelta(days=days_late)
    values = {
        "user_id": user.id, "workspace_id": workspace.id, "account_id": account.id, "description": "Rent",
        "amount": Decimal("10"), "currency": "BRL", "type": "debit", "frequency": "monthly",
        "start_date": due, "next_occurrence": due, "is_active": True, "auto_generate": True,
        "created_at": datetime.now(timezone.utc) - timedelta(days=30),
    }
    return RecurringTransaction(**{**values, **extra})


async def test_a_bill_left_behind_is_told_until_it_generates(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account
):
    bill = _bill(test_user, test_workspace, test_account, 4)
    session.add(bill)
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "recurring_failed")
    assert row.data["name"] == "Rent" and row.data["date"] == bill.next_occurrence.isoformat()

    bill.next_occurrence = app_today() + timedelta(days=20)
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []


@pytest.mark.parametrize(
    "days_late,extra",
    [
        (1, {}),
        (4, {"auto_generate": False}),
        (4, {"is_active": False}),
        (4, {"created_at": datetime.now(timezone.utc)}),
        (4, {"end_date": app_today() - timedelta(days=10)}),
    ],
)
async def test_bills_that_are_not_late_are_not_told(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account, days_late, extra
):
    session.add(_bill(test_user, test_workspace, test_account, days_late, **extra))
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []


# --- Budgets ----------------------------------------------------------------


async def test_budget_thresholds_follow_what_was_spent_not_the_projection(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account, test_categories,
    viewer_auth_headers,
):
    category = test_categories[0]
    currency = test_user.primary_currency
    month = app_today().replace(day=1)
    session.add(
        Budget(
            user_id=test_user.id, workspace_id=test_workspace.id, category_id=category.id,
            amount=Decimal("100"), month=month, currency=currency,
        )
    )
    # Fills the budget, but only as a projection.
    session.add(
        _bill(
            test_user, test_workspace, test_account, 0, amount=Decimal("500"), currency=currency,
            category_id=category.id, next_occurrence=month + timedelta(days=27), auto_generate=False,
        )
    )
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session, "budget_threshold") == []

    async def spend(amount: str) -> None:
        session.add(
            Transaction(
                user_id=test_user.id, workspace_id=test_workspace.id, account_id=test_account.id,
                category_id=category.id, description="Shop", amount=Decimal(amount), amount_primary=Decimal(amount),
                currency=currency, date=month, effective_date=month, type="debit", source="manual", status="posted",
            )
        )
        await session.commit()
        await _evaluate(session, test_workspace)

    await spend("80")
    rows = await _open(session, "budget_threshold")
    # A viewer is told too: it is about the money, not about fixing something.
    assert {row.user_id for row in rows} == {test_user.id, await _viewer_id(session)}
    mine = next(row for row in rows if row.user_id == test_user.id)
    assert mine.data["threshold"] == 80
    assert mine.data["actual"] == "80.00" and mine.data["budget"] == "100.00"

    await spend("20")
    rows = [row for row in await _open(session, "budget_threshold") if row.user_id == test_user.id]
    assert [row.data["threshold"] for row in rows] == [100]


async def test_evaluating_never_fetches_exchange_rates(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_categories
):
    month = app_today().replace(day=1)
    foreign = "JPY" if test_user.primary_currency != "JPY" else "EUR"
    session.add(
        Budget(
            user_id=test_user.id, workspace_id=test_workspace.id, category_id=test_categories[0].id,
            amount=Decimal("100"), month=month, currency=foreign,
        )
    )
    await session.commit()
    with patch("app.services.fx_rate_service.sync_rates", AsyncMock(return_value=0)) as sync:
        await _evaluate(session, test_workspace)
        sync.assert_not_awaited()
        # The same read, asked for by someone, does fetch.
        await budget_service.get_budget_vs_actual(session, test_workspace.id, test_user.id, month)
        sync.assert_awaited()


# --- Reminders, matches and invoices ----------------------------------------


def _row(user: User, workspace: Workspace, account: Account, **extra) -> Transaction:
    values = {
        "id": uuid.uuid4(), "user_id": user.id, "workspace_id": workspace.id, "account_id": account.id,
        "description": "Row", "amount": Decimal("10"), "currency": "BRL", "date": app_today(),
        "effective_date": app_today(), "type": "credit", "source": "manual", "status": "posted",
    }
    return Transaction(**{**values, **extra})


async def test_overdue_reminders_are_one_notification_told_again_as_dates_go_by(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account
):
    today = app_today()
    flagged = [
        _row(test_user, test_workspace, test_account, is_flagged=True, flag_due_date=today - timedelta(days=days))
        for days in (5, 3)
    ]
    # Due today, and one without a date: nothing is late.
    others = [
        _row(test_user, test_workspace, test_account, is_flagged=True, flag_due_date=today),
        _row(test_user, test_workspace, test_account, is_flagged=True),
    ]
    session.add_all([*flagged, *others])
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "flag_overdue")
    assert row.data["count"] == 2
    row.read_at = datetime.now(timezone.utc)

    # Fewer of the same is no news, whichever went; another date going by is.
    flagged[1].is_flagged = False
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "flag_overdue")
    assert row.data["count"] == 1 and row.read_at is not None
    others[0].flag_due_date = today - timedelta(days=1)
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "flag_overdue")
    assert row.data["count"] == 2 and row.read_at is None
    row.read_at = datetime.now(timezone.utc)

    for tx in (*flagged, *others):
        tx.is_flagged = False
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []

    # All dealt with, one late again is news, whatever its date.
    flagged[0].is_flagged = True
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "flag_overdue")
    assert row.read_at is None


async def test_matches_waiting_count_questions_and_leave_out_the_stale(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account
):
    payments = [_row(test_user, test_workspace, test_account) for _ in range(3)]
    session.add_all(payments)
    await session.flush()
    now = datetime.now(timezone.utc)
    group = uuid.uuid4()

    def ask(tx: Transaction, **extra) -> ReconciliationSuggestion:
        values = {
            "workspace_id": test_workspace.id, "transaction_id": tx.id, "expectation_kind": "invoice",
            "expectation_id": uuid.uuid4(), "strategy_id": "s", "node": "n", "amount": Decimal("10"),
            "scores": {}, "status": "pending", "created_at": now,
        }
        return ReconciliationSuggestion(**{**values, **extra})

    asked = [
        ask(payments[0], group_id=group),
        ask(payments[0], group_id=group),
        ask(payments[1]),
        ask(payments[1], status="declined"),
        ask(payments[2], created_at=now - timedelta(days=61)),
    ]
    session.add_all(asked)
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "reconciliation_pending")
    assert row.data["count"] == 2
    row.read_at = now

    # Answering the newest leaves an older one waiting: no news.
    asked[2].created_at = now - timedelta(days=3)
    asked[0].status = asked[1].status = "accepted"
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "reconciliation_pending")
    assert row.data["count"] == 1 and row.read_at is not None

    for suggestion in asked:
        suggestion.status = "accepted"
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []


async def test_a_late_invoice_is_told_until_it_is_paid(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    today = app_today()

    def invoice(**extra) -> Invoice:
        values = {
            "id": uuid.uuid4(), "workspace_id": test_workspace.id, "user_id": test_user.id, "number": 7,
            "status": "open", "issue_date": today - timedelta(days=30), "due_date": today - timedelta(days=2),
            "currency": "EUR", "total": Decimal("1000.00"),
        }
        return Invoice(**{**values, **extra})

    late = invoice()
    session.add_all(
        [
            late,
            invoice(number=8, due_date=today),
            invoice(number=None, status="draft"),
            invoice(number=10, status="void"),
            # A bill to pay is not a client's debt.
            invoice(number=11, direction="payable"),
        ]
    )
    await session.commit()

    # Invoices are a business workspace's module.
    await _evaluate(session, test_workspace)
    assert await _open(session) == []
    test_workspace.kind = "business"
    await session.commit()
    await _evaluate(session, test_workspace)
    [row] = await _open(session, "invoice_overdue")
    assert row.data == {
        "invoice_id": str(late.id), "name": "#7", "date": (today - timedelta(days=2)).isoformat(),
        "amount": "1000.00", "currency": "EUR",
    }

    late.status = "uncollectible"
    await session.commit()
    await _evaluate(session, test_workspace)
    assert await _open(session) == []


# --- Kinds by workspace -----------------------------------------------------


async def test_each_kind_of_workspace_gets_the_kinds_it_has_something_for():
    """A kind follows its module, so nothing is offered where its screen is missing."""
    personal = Workspace(id=uuid.uuid4(), name="P", kind="personal")
    business = Workspace(id=uuid.uuid4(), name="B", kind="business")
    everywhere = {
        "connection_error", "budget_threshold", "recurring_failed", "flag_overdue", "reconciliation_pending",
        "group_expense", "group_settlement", "backup_failed",
    }
    assert set(notification_service.kinds_for(personal, can_write=True)) == everywhere
    assert set(notification_service.kinds_for(business, can_write=True)) == everywhere | {"invoice_overdue"}
    # Matching runs in both: transfers and recurring bills need no invoices.
    for workspace in (personal, business):
        assert reconciliation_rule_service.nodes_for(resolve_modules(workspace))


# --- Groups -----------------------------------------------------------------


async def _person(session: AsyncSession, email: str) -> tuple[User, Workspace]:
    user = User(
        id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_superuser=False, is_verified=True
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(id=uuid.uuid4(), name=email, kind="personal", created_by_user_id=user.id)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="owner"))
    await session.commit()
    return user, workspace


class _Trip:
    """A group of the test user with Ana, whose workspace is her own, and Bob, who has no account."""

    def __init__(self, client: AsyncClient, headers: dict, session: AsyncSession):
        self.client, self.headers, self.session = client, headers, session

    async def _post(self, path: str, body: dict) -> dict:
        response = await self.client.post(path, json=body, headers=self.headers)
        assert response.status_code in (200, 201), response.text
        return response.json()

    async def setup(self) -> "_Trip":
        self.ana, self.ana_workspace = await _person(self.session, "ana@example.com")
        self.account = await self._post(
            "/api/accounts", {"name": "Wallet", "type": "checking", "balance": 0, "currency": "EUR"}
        )
        self.group = await self._post("/api/groups", {"name": "Trip", "default_currency": "EUR"})
        self.members = {}
        for name, extra in (
            ("Me", {"is_self": True}), ("Ana", {"email": "ana@example.com"}), ("Bob", {}),
        ):
            self.members[name] = await self._post(
                f"/api/groups/{self.group['id']}/members", {"name": name, **extra}
            )
        return self

    def split(self, *names: str, **exact: str) -> dict:
        if exact:
            return {
                "share_type": "exact",
                "splits": [
                    {"group_member_id": self.members[n]["id"], "share_amount": amount} for n, amount in exact.items()
                ],
            }
        return {"share_type": "equal", "splits": [{"group_member_id": self.members[n]["id"]} for n in names]}

    async def expense(self, splits: dict | None, description: str = "Dinner", **extra) -> dict:
        body = {
            "account_id": self.account["id"], "description": description, "amount": "90.00", "currency": "EUR",
            "date": app_today().isoformat(), "type": "debit", "splits": splits, **extra,
        }
        return await self._post("/api/transactions", {k: v for k, v in body.items() if v is not None})

    async def told(self, kind: str = "group_expense") -> list[Notification]:
        query = select(Notification).where(Notification.resolved_at.is_(None), Notification.kind == kind)
        rows = await self.session.execute(query.execution_options(populate_existing=True))
        return list(rows.scalars())


async def test_a_shared_expense_is_told_to_who_shares_it(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    expense = await trip.expense(trip.split("Me", "Ana", "Bob"))
    # Not the author, and Bob has no account. Ana hears of it in her own workspace.
    [row] = await trip.told()
    assert row.user_id == trip.ana.id and row.workspace_id == trip.ana_workspace.id
    assert row.data == {
        "group_id": trip.group["id"], "group": "Trip", "actor": "Me", "transaction_id": expense["id"],
        "name": "Dinner", "share": "30.00", "currency": "EUR", "changed": False,
    }
    # One she has no part in, and one nobody shares, say nothing.
    await trip.expense(trip.split("Me", "Bob"))
    await trip.expense(None)
    assert len(await trip.told()) == 1


async def test_an_edit_tells_only_whose_share_it_changed(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    expense = await trip.expense(trip.split("Me", "Ana", "Bob"))
    [row] = await trip.told()
    row.read_at = datetime.now(timezone.utc)
    await session.commit()

    async def edit(body: dict) -> None:
        response = await client.patch(f"/api/transactions/{expense['id']}", json=body, headers=auth_headers)
        assert response.status_code == 200, response.text

    await edit({"notes": "x", "splits": trip.split("Me", "Ana", "Bob")})
    [row] = await trip.told()
    assert row.read_at is not None

    await edit({"splits": trip.split(Me="50.00", Ana="40.00")})
    [row] = await trip.told()
    assert row.read_at is None and row.data["share"] == "40.00" and row.data["changed"] is True

    # Taken out of it, there is nothing left to say.
    await edit({"splits": trip.split("Me", "Bob")})
    await notification_service.evaluate_workspace(session, trip.ana_workspace.id)
    assert await trip.told() == []


async def test_told_where_they_are_now_ends_where_they_were_told_before(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    """Someone told in their own workspace joins the group's, and then it is edited."""
    trip = await _Trip(client, auth_headers, session).setup()
    expense = await trip.expense(trip.split("Me", "Ana"))
    settlement = await trip._post(
        f"/api/groups/{trip.group['id']}/settlements",
        {
            "from_member_id": trip.members["Me"]["id"], "to_member_id": trip.members["Ana"]["id"],
            "amount": "40.00", "currency": "EUR", "date": app_today().isoformat(),
        },
    )
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=trip.ana.id, role="editor")
    )
    await session.commit()
    for path, body in (
        (f"/api/transactions/{expense['id']}", {"splits": trip.split(Me="50.00", Ana="40.00")}),
        (f"/api/groups/{trip.group['id']}/settlements/{settlement['id']}", {"amount": "55.00"}),
    ):
        response = await client.patch(path, json=body, headers=auth_headers)
        assert response.status_code == 200, response.text
    [share], [payment] = await trip.told(), await trip.told("group_settlement")
    assert share.workspace_id == payment.workspace_id == test_workspace.id
    assert (share.data["share"], payment.data["amount"]) == ("40.00", "55.00")


async def test_a_share_brought_to_zero_is_over(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    expense = await trip.expense(trip.split("Me", "Ana"))
    assert len(await trip.told()) == 1
    response = await client.patch(
        f"/api/transactions/{expense['id']}", json={"splits": trip.split(Me="90.00", Ana="0.00")}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    await notification_service.evaluate_workspace(session, trip.ana_workspace.id)
    assert await trip.told() == []


async def test_several_expenses_at_once_are_one_notification(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    ids = [(await trip.expense(None, f"Row {i}"))["id"] for i in range(3)]
    response = await client.patch(
        "/api/transactions/bulk-add-to-group",
        json={"transaction_ids": ids, "group_id": trip.group["id"]},
        headers=auth_headers,
    )
    assert response.status_code == 200 and response.json()["updated"] == 3, response.text
    [row] = await trip.told()
    assert row.data == {
        "group_id": trip.group["id"], "group": "Trip", "actor": "Me", "count": 3, "transaction_ids": ANY,
    }
    assert set(row.data["transaction_ids"]) == set(ids)

    # There while any of them is still shared with her, over once none is.
    for left, shown in ((ids[:2], 1), (ids[2:], 0)):
        for tx_id in left:
            response = await client.patch(
                f"/api/transactions/{tx_id}", json={"splits": trip.split("Me", "Bob")}, headers=auth_headers
            )
            assert response.status_code == 200, response.text
        await notification_service.evaluate_workspace(session, trip.ana_workspace.id)
        assert len(await trip.told()) == shown


async def test_a_member_of_the_workspace_hears_there(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=trip.ana.id, role="viewer")
    )
    await session.commit()
    await trip.expense(trip.split("Me", "Ana"))
    [row] = await trip.told()
    assert row.workspace_id == test_workspace.id


async def test_the_owner_hears_of_an_expense_a_member_adds_after_looking_at_the_group(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace
):
    """Looking at a group marks, in memory, the viewer's entry as the self one."""
    trip = await _Trip(client, auth_headers, session).setup()
    hers = Account(
        user_id=trip.ana.id, workspace_id=trip.ana_workspace.id, name="Hers", type="checking", currency="EUR"
    )
    session.add(hers)
    await session.commit()
    seen = await group_service.get_group_visible(
        session, uuid.UUID(trip.group["id"]), trip.ana_workspace.id, trip.ana.id
    )
    assert seen is not None and [m.name for m in seen.members if m.is_self] == ["Ana"]
    await transaction_service.create_transaction(
        session, trip.ana_workspace.id, trip.ana.id,
        TransactionCreate(
            account_id=hers.id, description="Taxi", amount=Decimal("20.00"), currency="EUR", date=app_today(),
            type="debit", splits=TransactionSplitsInput(**trip.split("Me", "Ana")),
        ),
    )
    [row] = await trip.told()
    assert row.user_id == test_user.id and row.data["actor"] == "Ana" and row.data["share"] == "10.00"


async def test_the_other_side_hears_of_a_payment(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    settlement = await trip._post(
        f"/api/groups/{trip.group['id']}/settlements",
        {
            "from_member_id": trip.members["Me"]["id"], "to_member_id": trip.members["Ana"]["id"],
            "amount": "40.00", "currency": "EUR", "date": app_today().isoformat(),
        },
    )
    [row] = await trip.told("group_settlement")
    assert row.user_id == trip.ana.id
    assert row.data == {
        "settlement_id": settlement["id"], "group_id": trip.group["id"], "group": "Trip", "actor": "Me",
        "payer": "Me", "receiver": "Ana", "amount": "40.00", "currency": "EUR",
    }
    deleted = await client.delete(
        f"/api/groups/{trip.group['id']}/settlements/{settlement['id']}", headers=auth_headers
    )
    assert deleted.status_code == 204
    await notification_service.evaluate_workspace(session, trip.ana_workspace.id)
    assert await trip.told("group_settlement") == []


async def test_the_group_owner_hears_of_a_payment_from_a_member(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace
):
    """Their own entry isn't linked, and the money lands on an account of theirs."""
    trip = await _Trip(client, auth_headers, session).setup()
    settlement = await settlement_service.create_settlement(
        session, uuid.UUID(trip.group["id"]), trip.ana_workspace.id, trip.ana.id,
        GroupSettlementCreate(
            from_member_id=uuid.UUID(trip.members["Ana"]["id"]), to_member_id=uuid.UUID(trip.members["Me"]["id"]),
            amount=Decimal("110.00"), currency="EUR", date=app_today(),
        ),
    )
    assert settlement is not None and settlement.receiver_transaction_id is not None
    [row] = await trip.told("group_settlement")
    assert row.user_id == test_user.id and row.workspace_id == test_workspace.id
    assert row.data["actor"] == "Ana" and row.data["amount"] == "110.00"
    await _evaluate(session, test_workspace)
    assert len(await trip.told("group_settlement")) == 1


async def test_groups_turned_off_tell_nothing_and_end_what_was_told(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace, monkeypatch
):
    """By the workspace of who hears of it, not the group's."""
    trip = await _Trip(client, auth_headers, session).setup()
    await trip.expense(trip.split("Me", "Ana"))
    assert len(await trip.told()) == 1
    hers = trip.ana_workspace.id

    def without_groups(workspace: Workspace) -> list[str]:
        return [m for m in resolve_modules(workspace) if workspace.id != hers or m != "split_groups"]

    monkeypatch.setattr(notification_service, "resolve_modules", without_groups)
    await notification_service.evaluate_workspace(session, hers)
    assert await trip.told() == []
    await trip.expense(trip.split("Me", "Ana"), "Taxi")
    await trip._post(
        f"/api/groups/{trip.group['id']}/settlements",
        {
            "from_member_id": trip.members["Me"]["id"], "to_member_id": trip.members["Ana"]["id"],
            "amount": "40.00", "currency": "EUR", "date": app_today().isoformat(),
        },
    )
    assert await trip.told() == [] and await trip.told("group_settlement") == []


@pytest.mark.parametrize("name,actor", [(None, "Trip"), ("Tess", "Tess")])
async def test_who_has_no_entry_in_the_group_is_never_named_by_email(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace,
    name: str | None, actor: str,
):
    """It would reach people of other workspaces."""
    trip = await _Trip(client, auth_headers, session).setup()
    gone = await client.delete(
        f"/api/groups/{trip.group['id']}/members/{trip.members['Me']['id']}", headers=auth_headers
    )
    assert gone.status_code == 204, gone.text
    test_user.preferences = {**(test_user.preferences or {}), "display_name": name}
    await session.commit()
    await trip.expense(trip.split("Ana", "Bob"))
    [row] = await trip.told()
    assert row.user_id == trip.ana.id and row.data["actor"] == actor


async def test_an_edited_payment_says_what_it_is_now(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    trip = await _Trip(client, auth_headers, session).setup()
    cleo, _ = await _person(session, "cleo@example.com")
    trip.members["Cleo"] = await trip._post(
        f"/api/groups/{trip.group['id']}/members", {"name": "Cleo", "email": "cleo@example.com"}
    )
    path = f"/api/groups/{trip.group['id']}/settlements"
    settlement = await trip._post(
        path,
        {
            "from_member_id": trip.members["Me"]["id"], "to_member_id": trip.members["Ana"]["id"],
            "amount": "40.00", "currency": "EUR", "date": app_today().isoformat(),
        },
    )
    [row] = await trip.told("group_settlement")
    row.read_at = datetime.now(timezone.utc)
    await session.commit()

    async def edit(body: dict) -> None:
        response = await client.patch(f"{path}/{settlement['id']}", json=body, headers=auth_headers)
        assert response.status_code == 200, response.text

    await edit({"notes": "x", "amount": "40.00"})
    [row] = await trip.told("group_settlement")
    assert row.read_at is not None

    await edit({"amount": "55.00"})
    [row] = await trip.told("group_settlement")
    assert row.user_id == trip.ana.id and row.read_at is None and row.data["amount"] == "55.00"

    # Who is no longer part of it has nothing left to read; who is now hears of it.
    await edit({"to_member_id": trip.members["Cleo"]["id"]})
    [row] = await trip.told("group_settlement")
    assert row.user_id == cleo.id and row.data["receiver"] == "Cleo"


@pytest.mark.parametrize("hook", ["connection", "expense", "settlement"])
async def test_what_the_caller_wrote_fails_as_its_own_error(
    session: AsyncSession, test_user: User, test_workspace: Workspace, hook: str
):
    connection = BankConnection(id=uuid.uuid4(), workspace_id=test_workspace.id)
    session.add(
        Notification(workspace_id=test_workspace.id, user_id=test_user.id, kind=None, dedup_key="x", data={})
    )
    with pytest.raises(IntegrityError):
        if hook == "connection":
            await notification_service.connection_changed(session, connection, removed=True)
        elif hook == "expense":
            await notification_service.expenses_shared(session, test_user.id, [])
        else:
            await notification_service.settlement_recorded(session, test_user.id, GroupSettlement())


async def test_a_payback_saved_again_unchanged_is_no_news(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace
):
    """Saving the credit rewrites its settlements, under new ids."""
    trip = await _Trip(client, auth_headers, session).setup()

    def body(amount: str) -> dict:
        return {
            "payback": {
                "group_id": trip.group["id"], "to_member_id": trip.members["Me"]["id"],
                "payers": [{"member_id": trip.members["Ana"]["id"], "amount": amount}],
            }
        }

    credit = await trip.expense(None, "Pix", type="credit")

    async def save(amount: str) -> None:
        response = await client.patch(f"/api/transactions/{credit['id']}", json=body(amount), headers=auth_headers)
        assert response.status_code == 200, response.text

    await save("40.00")
    [row] = await trip.told("group_settlement")
    assert row.user_id == trip.ana.id
    row.read_at = datetime.now(timezone.utc)
    await session.commit()

    await save("40.00")
    [row] = await trip.told("group_settlement")
    assert row.read_at is not None
    # It still points at the payment that exists.
    await notification_service.evaluate_workspace(session, trip.ana_workspace.id)
    assert len(await trip.told("group_settlement")) == 1

    await save("50.00")
    [row] = await trip.told("group_settlement")
    assert row.read_at is None and row.data["amount"] == "50.00"


async def test_an_event_leaves_the_bell_once_read_and_ages_out(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace
):
    row = Notification(
        workspace_id=test_workspace.id, user_id=test_user.id, kind="group_expense", dedup_key="e", data={}
    )
    session.add(row)
    await session.commit()
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    async def total(status: str) -> int:
        return (await client.get(f"/api/notifications?status={status}", headers=headers)).json()["total"]

    assert [await total(s) for s in ("open", "unread", "all")] == [1, 1, 1]
    await client.post("/api/notifications/read-all", headers=headers)
    assert [await total(s) for s in ("open", "unread", "all")] == [0, 0, 1]
    await session.refresh(row)
    row.notified_at = datetime.now(timezone.utc) - timedelta(days=91)
    await session.commit()
    await notification_service.prune(session)
    assert await total("all") == 0


async def test_telling_never_fails_the_expense(
    client: AsyncClient, session: AsyncSession, auth_headers, test_workspace: Workspace, monkeypatch
):
    trip = await _Trip(client, auth_headers, session).setup()
    monkeypatch.setattr(notification_service, "_inform", AsyncMock(side_effect=RuntimeError("boom")))
    assert len((await trip.expense(trip.split("Me", "Ana")))["splits"]) == 2


# --- Backups ----------------------------------------------------------------


async def test_a_failed_scheduled_backup_is_told_until_one_works(
    session: AsyncSession, test_user: User, test_workspace: Workspace, tmp_path, monkeypatch
):
    workspace_id = test_workspace.id
    monkeypatch.setattr(backup_service, "_backup_storage_dir", lambda: tmp_path)
    await backup_service.save_backup_config(session, workspace_id, {"scheduled_enabled": True})
    with patch.object(backup_service, "build_backup_zip", AsyncMock(side_effect=OSError("disk full"))):
        result = await backup_service.run_scheduled_backups(session)
    assert result["errors"] == ["disk full"]
    await notification_service.evaluate_workspace(session, workspace_id)
    [row] = await _open(session, "backup_failed")
    assert set(row.data) == {"failed_at"}

    # Changing the settings does not hide it.
    await backup_service.save_backup_config(session, workspace_id, {"retention_count": 3})
    assert (await backup_service.get_backup_config(session, workspace_id)).last_failure_at is not None

    assert (await backup_service.run_scheduled_backups(session))["created"] == 1
    await notification_service.evaluate_workspace(session, workspace_id)
    assert await _open(session) == []
    setting = await session.get(AppSetting, backup_service._config_key(workspace_id))
    assert setting is not None and '"last_failure_at":null' in setting.value


async def test_a_backup_outcome_keeps_what_was_saved_meanwhile(
    session: AsyncSession, test_workspace: Workspace, tmp_path, monkeypatch
):
    """Both outcomes write the settings whole, so they start from the last ones."""
    monkeypatch.setattr(backup_service, "_backup_storage_dir", lambda: tmp_path)
    workspace_id = test_workspace.id
    key = backup_service._config_key(workspace_id)
    await backup_service.save_backup_config(session, workspace_id, {"scheduled_enabled": True, "retention_count": 5})

    async def meanwhile(**values) -> None:
        # Another process writing, behind this session's back.
        config = await backup_service.get_backup_config(session, workspace_id)
        value = config.model_copy(update=values).model_dump_json(exclude={"next_run_at", "storage_shared"})
        await session.execute(
            update(AppSetting).where(AppSetting.key == key).values(value=value)
            .execution_options(synchronize_session=False)
        )

    async def changed_while_building(*_args, **_kwargs) -> bytes:
        await meanwhile(retention_count=9)
        return b"zip"

    with patch.object(backup_service, "build_backup_zip", changed_while_building), \
            patch.object(backup_service, "_item_from_path", lambda _path: object()), \
            patch.object(backup_service, "_apply_retention", AsyncMock()):
        await backup_service.create_stored_backup(session, test_workspace)
    saved = await backup_service.get_backup_config(session, workspace_id)
    assert saved.retention_count == 9 and saved.last_run_at is not None

    # A failure that started before another backup worked is not recorded.
    started = datetime.now(timezone.utc)
    await meanwhile(last_run_at=started + timedelta(seconds=1))
    await backup_service._record_scheduled_failure(session, workspace_id, started)
    session.expire_all()
    assert (await backup_service.get_backup_config(session, workspace_id)).last_failure_at is None


# --- Who and where ----------------------------------------------------------


async def test_only_who_can_act_is_told_of_a_connection(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_connection: BankConnection,
    viewer_auth_headers,
):
    manager = User(
        id=uuid.uuid4(), email="manager@example.com", hashed_password="x", is_active=True,
        is_superuser=False, is_verified=True,
    )
    session.add(manager)
    await session.flush()
    test_workspace.managed_by_user_id = manager.id
    test_connection.settings = _failure("consent_expired")
    await session.commit()
    await _evaluate(session, test_workspace)
    assert {row.user_id for row in await _open(session)} == {test_user.id, manager.id}

    # Leaving ends it for the one who left.
    test_workspace.managed_by_user_id = None
    await session.commit()
    await _evaluate(session, test_workspace)
    assert {row.user_id for row in await _open(session)} == {test_user.id}


async def test_one_failing_check_leaves_the_others_and_writes_nothing(
    session: AsyncSession, test_user: User, test_workspace: Workspace, monkeypatch
):
    user_id, workspace_id = test_user.id, test_workspace.id

    def row(key: str) -> Notification:
        return Notification(
            workspace_id=workspace_id, user_id=user_id, kind="backup_failed", dedup_key=key, data={}
        )

    async def broken(session: AsyncSession, _workspace: Workspace) -> None:
        session.add(row("half"))
        await session.flush()
        raise RuntimeError("boom")

    async def after(session: AsyncSession, _workspace: Workspace) -> None:
        session.add(row("whole"))

    monkeypatch.setattr(notification_service, "_CHECKS", (broken, after))
    await notification_service.evaluate_workspace(session, workspace_id)
    assert [found.dedup_key for found in await _open(session)] == ["whole"]


async def test_resolved_notifications_are_pruned(session: AsyncSession, test_user: User, test_workspace: Workspace):
    now = datetime.now(timezone.utc)
    for key, resolved in (("old", now - timedelta(days=91)), ("recent", now), ("open", None)):
        session.add(
            Notification(
                workspace_id=test_workspace.id, user_id=test_user.id, kind="backup_failed", dedup_key=key,
                data={}, resolved_at=resolved,
            )
        )
    await session.commit()
    await notification_service.prune(session)
    keys = set((await session.execute(select(Notification.dedup_key))).scalars())
    assert keys == {"recent", "open"}


# --- API --------------------------------------------------------------------


async def _add(session: AsyncSession, workspace_id, user_id, key: str, **extra) -> Notification:
    row = Notification(
        workspace_id=workspace_id, user_id=user_id, kind="backup_failed", dedup_key=key, data={}, **extra
    )
    session.add(row)
    await session.commit()
    return row


async def test_each_person_reads_their_own_in_each_workspace(
    client: AsyncClient, session: AsyncSession, auth_headers, viewer_auth_headers, test_user: User,
    test_workspace: Workspace,
):
    viewer_id = await _viewer_id(session)
    other = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()
    mine = await _add(session, test_workspace.id, test_user.id, "a")
    theirs = await _add(session, test_workspace.id, viewer_id, "a")
    await _add(session, other.id, test_user.id, "a")
    await _add(session, test_workspace.id, test_user.id, "gone", resolved_at=datetime.now(timezone.utc))

    here = {"X-Workspace-Id": str(test_workspace.id)}
    owner, viewer = {**auth_headers, **here}, {**viewer_auth_headers, **here}
    listed = (await client.get("/api/notifications", headers=owner)).json()
    assert [item["id"] for item in listed["items"]] == [str(mine.id)]
    assert listed["unread_count"] == 1

    # Nobody else's, and not through another workspace.
    assert (await client.post(f"/api/notifications/{theirs.id}/read", headers=owner)).status_code == 404
    elsewhere = {**auth_headers, "X-Workspace-Id": str(other.id)}
    assert (await client.post(f"/api/notifications/{mine.id}/read", headers=elsewhere)).status_code == 404

    # A viewer dismisses their own, without touching the owner's.
    assert (await client.post(f"/api/notifications/{theirs.id}/read", headers=viewer)).status_code == 204
    assert (await client.get("/api/notifications", headers=viewer)).json()["unread_count"] == 0
    assert (await client.get("/api/notifications", headers=owner)).json()["unread_count"] == 1

    assert (await client.post("/api/notifications/read-all", headers=owner)).status_code == 204
    after = (await client.get("/api/notifications", headers=owner)).json()
    assert after["unread_count"] == 0 and len(after["items"]) == 1
    assert (await client.get("/api/notifications?status=unread", headers=owner)).json()["items"] == []
    assert (await client.get("/api/notifications", headers=elsewhere)).json()["unread_count"] == 1


async def test_the_list_pages_and_filters(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, test_workspace: Workspace
):
    now = datetime.now(timezone.utc)
    for i in range(5):
        await _add(session, test_workspace.id, test_user.id, f"open{i}", notified_at=now - timedelta(minutes=i))
    await _add(session, test_workspace.id, test_user.id, "read", notified_at=now - timedelta(days=1), read_at=now)
    await _add(session, test_workspace.id, test_user.id, "done", notified_at=now - timedelta(days=2), resolved_at=now)
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    async def get(query: str) -> dict:
        response = await client.get(f"/api/notifications?{query}", headers=headers)
        assert response.status_code == 200
        return response.json()

    totals = {status: (await get(f"status={status}"))["total"] for status in ("open", "unread", "all")}
    assert totals == {"open": 6, "unread": 5, "all": 7}
    # The bell's number is the same whatever is being listed.
    everything = await get("status=all")
    assert everything["unread_count"] == 5
    assert [item["resolved_at"] is not None for item in everything["items"]] == [False] * 6 + [True]

    pages = [(await get(f"status=all&limit=3&offset={offset}"))["items"] for offset in (0, 3, 6)]
    assert [len(page) for page in pages] == [3, 3, 1]
    listed = [item["notified_at"] for page in pages for item in page]
    assert listed == sorted(listed, reverse=True) and len({i["id"] for p in pages for i in p}) == 7
    for bad in ("status=resolved", "limit=0", "offset=-1"):
        assert (await client.get(f"/api/notifications?{bad}", headers=headers)).status_code == 422


async def test_a_kind_turned_off_is_not_shown_and_back_on_shows_at_once(
    client: AsyncClient, session: AsyncSession, auth_headers, viewer_auth_headers, test_user: User,
    test_workspace: Workspace,
):
    here = {"X-Workspace-Id": str(test_workspace.id)}
    owner, viewer = {**auth_headers, **here}, {**viewer_auth_headers, **here}
    await _add(session, test_workspace.id, test_user.id, "backup")
    session.add(
        Notification(
            workspace_id=test_workspace.id, user_id=test_user.id, kind="flag_overdue", dedup_key="flag", data={}
        )
    )
    await session.commit()

    async def toggle(kind: str, enabled: bool, headers: dict) -> None:
        response = await client.put(
            f"/api/notifications/preferences/{kind}/in_app", json={"enabled": enabled}, headers=headers
        )
        assert response.status_code == 204

    async def shown() -> set[str]:
        body = (await client.get("/api/notifications?status=all", headers=owner)).json()
        assert body["unread_count"] == body["total"] == len(body["items"])
        return {item["kind"] for item in body["items"]}

    async def preferences(headers: dict) -> dict[str, bool]:
        body = (await client.get("/api/notifications/preferences", headers=headers)).json()
        assert all(set(item["channels"]) == {"in_app"} for item in body)
        return {item["kind"]: item["channels"]["in_app"] for item in body}

    assert await preferences(owner) == {
        "connection_error": True, "budget_threshold": True, "recurring_failed": True, "flag_overdue": True,
        "reconciliation_pending": True, "group_expense": True, "group_settlement": True, "backup_failed": True,
    }
    # A viewer is offered what reaches a viewer, and may turn it off.
    for_viewers = {"budget_threshold": True, "group_expense": True, "group_settlement": True}
    assert await preferences(viewer) == for_viewers
    await toggle("budget_threshold", False, viewer)
    assert await preferences(viewer) == {**for_viewers, "budget_threshold": False}
    assert (await preferences(owner))["budget_threshold"] is True

    await toggle("backup_failed", False, owner)
    await toggle("backup_failed", False, owner)
    assert await shown() == {"flag_overdue"}
    # Reading all leaves alone what isn't shown.
    await client.post("/api/notifications/read-all", headers=owner)
    await toggle("backup_failed", True, owner)
    body = (await client.get("/api/notifications?status=unread", headers=owner)).json()
    assert [item["kind"] for item in body["items"]] == ["backup_failed"]

    for path, refused in (("nope/in_app", 422), ("backup_failed/pigeon", 404)):
        bad = await client.put(f"/api/notifications/preferences/{path}", json={"enabled": True}, headers=owner)
        assert bad.status_code == refused
    test_workspace.kind = "business"
    await session.commit()
    assert "invoice_overdue" in await preferences(owner)


async def test_access_tokens_cannot_reach_notifications():
    assert required_scope("GET", "/api/notifications") is None
    assert required_scope("POST", "/api/notifications/read-all") is None
