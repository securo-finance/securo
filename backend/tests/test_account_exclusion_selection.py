"""Explicit selection overrides a totals preference, never workspace scope."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

import mcp_server.tools  # noqa: F401 -- register the real tool handlers
from app.core import app_clock
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.group import Group, GroupMember
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.transaction_split import TransactionSplit
from app.models.workspace import Workspace
from app.services.dashboard_service import get_balance_history, get_balance_history_months, get_summary
from app.services.transaction_calendar_service import get_transaction_calendar
from app.services import dashboard_service, report_service, transaction_service
from app.services.fx_rate_service import convert
from mcp_server.auth import CallContext
from mcp_server.registry import call_tool


# The unselected third account and the inaccessible workspace carry distinct
# amounts, so a widened query cannot accidentally satisfy the money assertions.
SCOPES = [
    pytest.param("default", 8130, 160, 30, 8044, id="absent"),
    pytest.param("included", 1100, 120, 20, 1064, id="included-control"),
    pytest.param("flagged", 3280, 350, 70, 3234, id="flagged-only"),
    pytest.param("mixed", 4380, 470, 90, 4298, id="mixed-duplicate"),
    pytest.param("empty", 0, 0, 0, 0, id="empty"),
    pytest.param("foreign", 0, 0, 0, 0, id="foreign-only"),
    pytest.param("unknown", 0, 0, 0, 0, id="unknown-only"),
    pytest.param("flagged-and-foreign", 3280, 350, 70, 3234, id="flagged-and-foreign"),
]


@pytest_asyncio.fixture
async def selection_scene(session, test_user, test_workspace, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 7, 15, 12, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(app_clock, "datetime", FrozenDateTime)
    foreign_ws = Workspace(id=uuid.uuid4(), name="Inaccessible synthetic workspace")
    session.add(foreign_ws)
    await session.flush()
    food = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Food", icon="tag", color="#000000",
    )
    other = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Other", icon="tag", color="#111111",
    )
    session.add_all([food, other])
    await session.flush()
    accounts = {}
    for name, opening, income, expense, pending, virtual in [
        ("included", 1000, 120, 20, 13, 23),
        ("flagged", 3000, 350, 70, 17, 29),
        ("third", 7000, 40, 10, 19, 31),
        ("foreign", 9000, 900, 90, 97, 99),
    ]:
        ws = foreign_ws.id if name == "foreign" else test_workspace.id
        account = Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws,
            name=name, type="checking", balance=Decimal("0"), currency="BRL",
            exclude_from_reports=name == "flagged",
        )
        session.add(account)
        await session.flush()
        accounts[name] = account
        for description, amount, direction, when, source, status in [
            ("opening", opening, "credit", date(2026, 6, 20), "opening_balance", "posted"),
            ("income", income, "credit", date(2026, 7, 8), "manual", "posted"),
            ("expense", expense, "debit", date(2026, 7, 8), "manual", "posted"),
            ("pending", pending, "debit", date(2026, 7, 20), "manual", "pending"),
        ]:
            split = name == "flagged" and description == "expense"
            tx = Transaction(
                id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws,
                account_id=account.id, description=f"{name} {description}",
                amount=Decimal(amount), amount_primary=Decimal(amount), currency="BRL",
                date=when, effective_date=when, type=direction, source=source, status=status,
                category_id=food.id if ws == test_workspace.id and not split else None,
                has_category_lines=split,
            )
            if split:
                tx.category_lines = [
                    TransactionCategoryLine(
                        id=uuid.uuid4(), workspace_id=ws, category_id=food.id,
                        amount=Decimal("50"), share_type="exact", position=0,
                    ),
                    TransactionCategoryLine(
                        id=uuid.uuid4(), workspace_id=ws, category_id=other.id,
                        amount=Decimal("20"), share_type="exact", position=1,
                    ),
                ]
            session.add(tx)
        session.add(RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws,
            account_id=account.id, description=f"{name} virtual", amount=Decimal(virtual),
            currency="BRL", type="debit", frequency="monthly",
            start_date=date(2026, 7, 22), next_occurrence=date(2026, 7, 22),
        ))
    await session.commit()
    return {
        "accounts": accounts, "workspace": test_workspace.id, "user": test_user.id,
        "food": food.id, "other": other.id,
    }


def selected_ids(scene, scope):
    accounts = scene["accounts"]
    if scope == "default":
        return None
    if scope == "empty":
        return []
    if scope == "unknown":
        return [uuid.UUID("00000000-0000-0000-0000-000000000001")]
    if scope == "mixed":
        return [accounts["included"].id, accounts["flagged"].id, accounts["flagged"].id]
    if scope == "flagged-and-foreign":
        return [accounts["flagged"].id, accounts["foreign"].id]
    return [accounts[scope].id]


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_summary_explicit_selection_keeps_flagged_money(
    session, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    summary = await get_summary(
        session, scene["workspace"], scene["user"], month=date(2026, 7, 1),
        account_ids=selected_ids(scene, scope),
    )
    assert (
        summary.total_balance.get("BRL", 0), summary.monthly_income,
        summary.monthly_expenses, summary.projected_balance.get("BRL", 0),
    ) == (balance, income, expense, projected)


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_calendar_money_obeys_original_selection(
    session, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    calendar = await get_transaction_calendar(
        session, scene["workspace"], scene["user"], month=date(2026, 7, 1),
        account_ids=selected_ids(scene, scope),
    )
    actual = next(day for day in calendar.days if day.date == date(2026, 7, 8))
    final = next(day for day in calendar.days if day.date == date(2026, 7, 22))
    assert (actual.actual_income, actual.actual_expense, actual.ending_balance,
            final.ending_balance) == (income, expense, balance, projected)
    # The displayed rows are evidence, independent of aggregate participation.
    if scope == "default":
        assert {item.description for item in actual.items} == {
            f"{name} {kind}" for name in ("included", "flagged", "third")
            for kind in ("income", "expense")
        }


@pytest.mark.parametrize("day_number,kind,expected", [
    pytest.param(8, "actual", {
        "included income": 120, "included expense": 20,
        "flagged income": 350, "flagged expense": 70,
        "third income": 40, "third expense": 10,
    }, id="posted"),
    pytest.param(20, "projected", {
        "included pending": 13, "flagged pending": 17, "third pending": 19,
    }, id="pending"),
    pytest.param(22, "projected", {
        "included virtual": 23, "flagged virtual": 29, "third virtual": 31,
    }, id="virtual"),
])
async def test_default_calendar_keeps_visible_history(
    session, selection_scene, day_number, kind, expected
):
    scene = selection_scene
    calendar = await get_transaction_calendar(
        session, scene["workspace"], scene["user"], month=date(2026, 7, 1),
    )
    day = next(day for day in calendar.days if day.date == date(2026, 7, day_number))
    assert {item.description: item.amount_primary for item in day.items} == expected
    assert all(item.kind == kind for item in day.items)
    assert (day.actual_count if kind == "actual" else day.projected_count) == len(expected)


@pytest.mark.parametrize("day_number,expense", [(20, 32), (22, 54)])
async def test_default_calendar_projected_totals_omit_flagged_money(
    session, selection_scene, day_number, expense
):
    scene = selection_scene
    calendar = await get_transaction_calendar(
        session, scene["workspace"], scene["user"], month=date(2026, 7, 1),
    )
    day = next(day for day in calendar.days if day.date == date(2026, 7, day_number))
    assert (day.projected_income, day.projected_expense, day.projected_transfer_net) == (
        0, expense, 0,
    )


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_registered_aggregate_selection_sum(
    session, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    ids = selected_ids(scene, scope)
    arguments: dict[str, object] = {
        "metric": "sum", "group_by": "category", "tx_type": "expense",
        "from_date": "2026-07-01", "to_date": "2026-07-31",
    }
    if ids is not None:
        arguments["account_ids"] = [str(value) for value in ids]
    result = await call_tool(
        session, CallContext(user_id=scene["user"], workspace_id=scene["workspace"]),
        "aggregate", arguments,
    )
    # Split flagged expenses must not reappear through a non-account grouping.
    assert sum(item["value"] for item in result["items"]) == expense
    assert result["total"] == len(result["items"])
    if scope in {"empty", "foreign", "unknown"}:
        assert result["items"] == []
    elif scope in {"flagged", "flagged-and-foreign"}:
        assert {item["bucket"]: (item["value"], item["count"]) for item in result["items"]} == {
            str(scene["food"]): (50, 1), str(scene["other"]): (20, 1),
        }


@pytest.mark.parametrize("metric,expected", [("sum", 70), ("count", 1), ("avg", 70)])
async def test_registered_aggregate_flagged_split_counts_transactions_once(
    session, selection_scene, metric, expected
):
    scene = selection_scene
    account = scene["accounts"]["flagged"]
    result = await call_tool(
        session, CallContext(user_id=scene["user"], workspace_id=scene["workspace"]),
        "aggregate", {
            "metric": metric, "group_by": "account", "tx_type": "expense",
            "from_date": "2026-07-01", "to_date": "2026-07-31",
            "account_ids": [str(account.id), str(account.id)],
        },
    )
    assert [(item["bucket"], item["value"], item["count"]) for item in result["items"]] == [
        (str(account.id), expected, 1),
    ]


async def test_registered_aggregate_rejects_malformed_selection(session, selection_scene):
    scene = selection_scene
    with pytest.raises(ValueError):
        await call_tool(
            session, CallContext(user_id=scene["user"], workspace_id=scene["workspace"]),
            "aggregate", {"account_ids": ["not-a-uuid"]},
        )
    # Failed selection parsing is a read failure, not a mutation.
    assert dict((await session.execute(select(Account.id, Account.exclude_from_reports))).all()) == {
        account.id: name == "flagged" for name, account in scene["accounts"].items()
    }


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_balance_history_derived_nonloan_scope_keeps_original_intent(
    session, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    session.add(Account(
        id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
        name="Loan forces internal non-loan selection", type="loan", currency="BRL",
        balance=Decimal("0"),
    ))
    await session.commit()
    history = await get_balance_history(
        session, scene["workspace"], scene["user"], month=date(2026, 7, 1),
        account_ids=selected_ids(scene, scope), comparison_months=[date(2026, 6, 1)],
    )
    assert next(day.balance for day in history.current if day.day == 8) == balance
    assert next(day.balance for day in history.current if day.day == 7) == (
        balance - income + expense
    )


async def test_balance_month_availability_omits_implicit_flagged_history(session, selection_scene):
    scene = selection_scene
    session.add(Transaction(
        id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
        account_id=scene["accounts"]["flagged"].id, description="Earlier flagged activity",
        amount=Decimal("43"), currency="BRL", date=date(2026, 5, 10),
        type="debit", source="manual", status="posted",
    ))
    await session.commit()
    assert await get_balance_history_months(session, scene["workspace"]) == ["2026-07"]
    assert await get_balance_history_months(
        session, scene["workspace"], [scene["accounts"]["flagged"].id]
    ) == ["2026-05", "2026-07"]


@pytest.mark.parametrize("explicit,expense", [(False, 0), (True, 46)])
async def test_calendar_flagged_only_day_keeps_counts_not_money_indicators(
    session, selection_scene, explicit, expense
):
    scene = selection_scene
    flagged = scene["accounts"]["flagged"].id
    pending = await session.scalar(select(Transaction).where(Transaction.description == "flagged pending"))
    pending.date = date(2026, 7, 24)
    recurring = await session.scalar(select(RecurringTransaction).where(
        RecurringTransaction.description == "flagged virtual"
    ))
    recurring.start_date = recurring.next_occurrence = date(2026, 7, 24)
    await session.commit()
    calendar = await get_transaction_calendar(
        session, scene["workspace"], scene["user"], date(2026, 7, 1),
        [flagged] if explicit else None,
    )
    day = next(day for day in calendar.days if day.date == date(2026, 7, 24))
    assert (day.projected_count, day.projected_expense, day.has_expense) == (2, expense, explicit)
    assert {item.description: item.amount_primary for item in day.items} == {
        "flagged pending": 17, "flagged virtual": 29,
    }


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
@pytest.mark.parametrize("consumer", ["income-report", "net-worth", "cash-flow", "baseline", "categories", "drilldown"])
async def test_report_consumers_preserve_selection_money(
    session, selection_scene, scope, balance, income, expense, projected, consumer
):
    from tests.test_category_lines_reports import _install_sqlite_to_char

    scene = selection_scene
    await _install_sqlite_to_char(session)
    ids = selected_ids(scene, scope)
    ws, user = scene["workspace"], scene["user"]
    if consumer == "income-report":
        result = await report_service.get_income_expenses_report(
            session, ws, user, account_ids=ids, currency="BRL",
            start_date=date(2026, 7, 1), end_date=date(2026, 7, 15),
        )
        amounts = {item.key: item.value for item in result.summary.breakdowns}
        assert (amounts["income"], amounts["expenses"]) == (income, expense)
        assert sum(item.value for item in result.composition if item.group == "expenses") == expense
    elif consumer == "net-worth":
        result = await report_service._net_worth_at(session, ws, date(2026, 7, 15), "BRL", ids)
        assert result.value == balance
    elif consumer == "cash-flow":
        result = await report_service.get_cash_flow_report(
            session, ws, user, months=1, currency="BRL", account_ids=ids,
        )
        assert next(point.value for point in result.trend if point.date == "2026-07-22") == projected
    elif consumer == "baseline":
        async def to_primary(amount, currency):
            converted, _ = await convert(session, amount, currency, "BRL")
            return float(converted)
        rows, days = await report_service._get_baseline_projection(
            session, ws, date(2026, 7, 15), date(2026, 7, 16), "BRL", to_primary, ids,
        )
        assert days == (7 if income or expense else 0)
        assert sum(row["amount"] for row in rows if row["type"] == "credit") == pytest.approx(income / 7)
        assert sum(row["amount"] for row in rows if row["type"] == "debit") == pytest.approx(expense / 7)
    elif consumer == "categories":
        categories = await dashboard_service.get_spending_by_category(session, ws, user, date(2026, 7, 1), ids)
        assert sum(category.total for category in categories) == expense
        trend = await dashboard_service.get_monthly_trend(session, ws, user, months=1, account_ids=ids)
        # This existing endpoint has one series: actuals plus real forecasts,
        # not the summary's posted-only figures or virtual recurrences.
        pending = {"default": 32, "included": 13, "flagged": 17, "mixed": 30,
                   "flagged-and-foreign": 17}.get(scope, 0)
        assert [(point.income, point.expenses) for point in trend] == (
            [(income, expense + pending)] if income or expense else []
        )
    else:
        rows, count, footer = await transaction_service.get_transactions(
            session, ws, user, account_ids=ids, from_date=date(2026, 7, 1),
            to_date=date(2026, 7, 15), include_summary=True, user_pnl_only=True,
        )
        assert count == len(rows)
        assert footer is not None
        assert (footer["income"], footer["expense"]) == (income, expense)
        assert {row.account_id for row in rows} <= {
            scene["accounts"][name].id for name in ("included", "flagged", "third")
        }


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_future_calendar_carried_money_agrees_across_overlapping_grids(
    session, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    calendars = [await get_transaction_calendar(
        session, scene["workspace"], scene["user"], date(2026, month, 1), selected_ids(scene, scope)
    ) for month in (7, 8, 9)]
    july_end = [next(day for day in calendar.days if day.date == date(2026, 7, 31))
                for calendar in calendars[:2]]
    assert [day.ending_balance for day in july_end] == [projected, projected]
    virtual = {"default": 54, "included": 23, "flagged": 29, "mixed": 52,
               "flagged-and-foreign": 29}.get(scope, 0)
    august_end = [next(day for day in calendar.days if day.date == date(2026, 8, 31))
                  for calendar in calendars[1:]]
    assert [day.ending_balance for day in august_end] == [projected - virtual] * 2


@pytest.mark.parametrize("metric", ["sum", "count", "avg"])
@pytest.mark.parametrize("status,total,count", [("posted", 70, 1), ("pending", 17, 1), ("all", 87, 2)])
@pytest.mark.parametrize("exclude_transfers", [False, True])
async def test_registered_aggregate_status_metrics_and_transfer_parts(
    session, selection_scene, metric, status, total, count, exclude_transfers
):
    scene = selection_scene
    transfer_category = await session.get(Category, scene["other"])
    transfer_category.treat_as_transfer = True
    await session.commit()
    if exclude_transfers and status != "pending":
        total -= 20
    result = await call_tool(session, CallContext(user_id=scene["user"], workspace_id=scene["workspace"]),
        "aggregate", {"metric": metric, "group_by": "account", "tx_type": "expense",
            "from_date": "2026-07-01", "to_date": "2026-07-31", "status": status,
            "account_ids": [str(scene["accounts"]["flagged"].id)], "exclude_transfers": exclude_transfers})
    expected = count if metric == "count" else total / count if metric == "avg" else total
    assert [(item["value"], item["count"]) for item in result["items"]] == [(expected, count)]


@pytest.mark.parametrize("scope,balance,income,expense,projected", SCOPES)
async def test_calendar_http_scope_and_malformed_selection(
    client, auth_headers, selection_scene, scope, balance, income, expense, projected
):
    scene = selection_scene
    ids = selected_ids(scene, scope)
    # HTTP has no representation for an empty UUID list: UI consumers must
    # disable that request; the service's explicit [] case is tested above.
    if ids == []:
        rejected = await client.get('/api/transactions/calendar', headers=auth_headers,
                                    params={"account_ids": "", "month": "2026-07-01"})
        assert rejected.status_code == 422
        return
    params = [("month", "2026-07-01")] + ([] if ids is None else [("account_ids", str(i)) for i in ids])
    response = await client.get('/api/transactions/calendar', headers=auth_headers, params=params)
    assert response.status_code == 200, response.text
    day = next(day for day in response.json()["days"] if day["date"] == "2026-07-08")
    assert (day["actual_income"], day["actual_expense"], day["ending_balance"]) == (income, expense, balance)
    invalid = await client.get('/api/transactions/calendar', headers=auth_headers,
                               params={"account_ids": "malformed", "month": "2026-07-01"})
    assert invalid.status_code == 422


async def test_selected_flagged_fx_split_preserves_parts_balance_and_raw_rows(session, selection_scene):
    scene = selection_scene
    flagged = scene["accounts"]["flagged"]
    transfer = await session.get(Category, scene["other"])
    transfer.treat_as_transfer = True
    ignored = Category(id=uuid.uuid4(), user_id=scene["user"], workspace_id=scene["workspace"],
                       name="Ignored part", icon="tag", color="#222222", is_ignored=True)
    session.add_all([ignored, FxRate(base_currency="USD", quote_currency="BRL",
        date=date(2026, 6, 1), rate=Decimal("5"), source="test")])
    tx = await session.scalar(select(Transaction).where(Transaction.description == "flagged expense"))
    tx.currency, tx.amount_primary = "USD", Decimal("350")
    tx.category_lines[0].amount = Decimal("40")
    tx.category_lines.append(TransactionCategoryLine(
        id=uuid.uuid4(), workspace_id=scene["workspace"], category=ignored,
        amount=Decimal("10"), share_type="exact", position=2,
    ))
    await session.commit()
    before = (await session.execute(select(Transaction.__table__).order_by(Transaction.id))).all()
    for ids, expected_expense, expected_balance in [
        (None, 30, 8130), ([flagged.id], 200, 3000),
    ]:
        summary = await get_summary(session, scene["workspace"], scene["user"], date(2026, 7, 1), account_ids=ids)
        assert summary.monthly_expenses_primary == expected_expense
        assert summary.total_balance_primary == expected_balance
        calendar = await get_transaction_calendar(session, scene["workspace"], scene["user"], date(2026, 7, 1), ids)
        day = next(day for day in calendar.days if day.date == date(2026, 7, 8))
        assert (day.actual_expense, day.actual_transfer_net, day.ending_balance) == (
            expected_expense, -100 if ids is not None else 0, expected_balance,
        )
        assert next(item.amount_primary for item in day.items if item.description == "flagged expense") == 350
    aggregate = await call_tool(session, CallContext(user_id=scene["user"], workspace_id=scene["workspace"]),
        "aggregate", {"metric": "avg", "group_by": "account", "tx_type": "expense",
            "from_date": "2026-07-08", "to_date": "2026-07-08", "account_ids": [str(flagged.id)]})
    # Aggregate is not P&L: it retains the ignored part, just as current main
    # does, while transfer parts are omitted by its own existing option.
    assert [(item["value"], item["count"]) for item in aggregate["items"]] == [(250, 1)]
    assert (await session.execute(select(Transaction.__table__).order_by(Transaction.id))).all() == before


@pytest.mark.parametrize("excluded,explicit,expected", [
    (False, False, 77), (True, False, 0), (True, True, 77),
])
async def test_closed_investment_history_retains_report_and_calendar_semantics(
    session, selection_scene, excluded, explicit, expected
):
    from tests.test_category_lines_reports import _install_sqlite_to_char

    scene = selection_scene
    account = scene["accounts"]["flagged"]
    account.is_closed, account.exclude_from_reports = True, excluded
    transfer = Category(id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
                        name="Closed investments", icon="tag", color="#333333", treat_as_transfer=True)
    session.add(transfer)
    await session.flush()
    session.add(Transaction(
        id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
        account_id=account.id, category_id=transfer.id, description="Closed investment",
        amount=Decimal("77"), amount_primary=Decimal("77"), currency="BRL",
        type="debit", date=date(2026, 7, 10), source="manual", status="posted",
    ))
    await session.commit()
    await _install_sqlite_to_char(session)
    ids = [account.id] if explicit else None
    report = await report_service.get_income_expenses_report(
        session, scene["workspace"], scene["user"], account_ids=ids,
        start_date=date(2026, 7, 1), end_date=date(2026, 7, 15), currency="BRL",
    )
    assert sum(item.value for item in report.composition if item.group == "investments") == expected
    calendar = await get_transaction_calendar(session, scene["workspace"], scene["user"], date(2026, 7, 1), ids)
    day = next(day for day in calendar.days if day.date == date(2026, 7, 10))
    assert day.actual_transfer_net == -expected
    assert {item.description for item in day.items} == {"Closed investment"}
    assert day.ending_balance == (0 if explicit else 8130)
    assert not any(item.description == "flagged virtual" for day in calendar.days for item in day.items)


async def test_wallet_only_http_selection_does_not_broaden_to_accounts_or_other_wallets(
    client, auth_headers, session, selection_scene
):
    scene = selection_scene
    wallet = AssetGroup(id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"], name="Selected wallet")
    session.add(wallet)
    await session.flush()
    for name, group, value in [("Selected holding", wallet.id, "500"), ("Outside holding", None, "900")]:
        session.add(Asset(id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
            name=name, group_id=group, type="investment", currency="BRL", purchase_price=Decimal(value),
            purchase_date=date(2026, 7, 1), valuation_method="manual"))
    await session.commit()
    for path in ["/api/dashboard/summary", "/api/reports/net-worth"]:
        response = await client.get(path, headers=auth_headers,
            params={"asset_group_ids": str(wallet.id), "month": "2026-07-01", "months": 1})
        assert response.status_code == 200, response.text
        data = response.json()
        if path.endswith("summary"):
            # The dashboard's net-worth headline includes the wallet itself.
            assert (data["total_balance_primary"], data["monthly_income"], data["assets_value_primary"], data["accounts_count"]) == (500, 0, 500, 0)
        else:
            assert data["summary"]["primary_value"] == 500
            assert {item["label"] for item in data["composition"]} == {"Selected holding"}


@pytest.mark.parametrize("explicit,income,expense", [(False, 160, 22), (True, 350, 70)])
async def test_owner_share_offsets_and_settlements_preserve_selection_contract(
    session, selection_scene, explicit, income, expense
):
    scene = selection_scene
    group = Group(id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
                  name="Synthetic shared spending", default_currency="BRL")
    session.add(group)
    await session.flush()
    owner = GroupMember(id=uuid.uuid4(), workspace_id=scene["workspace"], group_id=group.id,
                        name="Owner", linked_user_id=scene["user"], is_self=True)
    other = GroupMember(id=uuid.uuid4(), workspace_id=scene["workspace"], group_id=group.id,
                        name="Other", is_self=False)
    session.add_all([owner, other])
    await session.flush()
    for name, owner_share, other_share in [("included", "12", "8"), ("flagged", "42", "28")]:
        tx = await session.scalar(select(Transaction).where(Transaction.description == f"{name} expense"))
        # Group sharing and category splitting are mutually exclusive in the
        # product; use an ordinary parent for this owner-share fixture.
        if tx.has_category_lines:
            tx.category_lines.clear()
            tx.has_category_lines, tx.category_id = False, scene["food"]
        for member, share in [(owner, owner_share), (other, other_share)]:
            session.add(TransactionSplit(id=uuid.uuid4(), workspace_id=scene["workspace"],
                transaction_id=tx.id, group_member_id=member.id, share_amount=Decimal(share), share_type="exact"))
        for direction, amount in [("credit", "11"), ("debit", "13")]:
            session.add(Transaction(id=uuid.uuid4(), workspace_id=scene["workspace"], user_id=scene["user"],
                account_id=scene["accounts"][name].id, description=f"{name} settlement {direction}",
                type=direction, amount=Decimal(amount), amount_primary=Decimal(amount), currency="BRL",
                date=date(2026, 7, 10), source="settlement", status="posted"))
    await session.commit()
    result = await get_summary(session, scene["workspace"], scene["user"], date(2026, 7, 1),
        account_ids=[scene["accounts"]["flagged"].id] if explicit else None)
    assert (result.monthly_income, result.monthly_expenses) == (income, expense)
