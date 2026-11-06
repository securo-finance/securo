"""Reports read a transaction split across categories part by part (#425).

The scenario is one month of activity in the user's primary currency. Each
test takes a snapshot of every figure that depends on categories (dashboard,
budgets, reports, calendar, the transaction list footer, the account summary
and the MCP aggregate tool), splits one transaction, and checks the snapshot
again:

- splitting across ordinary categories moves money between categories and
  changes no total (conservation),
- a part in a transfer-like category leaves the totals by exactly its amount,
- balances never move.

`test_category_lines_reports_postgres.py` runs the same tests on PostgreSQL.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient

import mcp_server.tools  # noqa: F401 -- registers the MCP handlers
from app.core.app_clock import app_today
from app.models.category import Category
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

pytestmark = pytest.mark.asyncio

CURRENCY = "BRL"  # the test user's primary currency


async def _install_sqlite_to_char(session) -> None:
    """`to_char(date, 'YYYY-MM')` for the monthly trend; PostgreSQL has it."""
    if session.bind.dialect.name != "sqlite":
        return

    def to_char(value, fmt):
        return None if value is None else str(value)[:7]

    connection = await session.connection()
    await connection.run_sync(
        lambda conn: conn.connection.dbapi_connection.create_function("to_char", 2, to_char)
    )


@pytest_asyncio.fixture
async def scene(client: AsyncClient, auth_headers, session, test_user, test_categories):
    await _install_sqlite_to_char(session)
    food, transport, income = test_categories
    invest = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Investments",
        icon="trending-up", color="#0EA5E9", treat_as_transfer=True,
    )
    session.add(invest)
    await session.commit()

    resp = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Checking", "type": "checking", "balance": 0, "currency": CURRENCY},
    )
    assert resp.status_code in (200, 201), resp.text
    account = resp.json()

    day = app_today().replace(day=1)

    async def create(amount, type_, category=None, **extra):
        payload = {
            "account_id": account["id"], "description": f"{type_} {amount}",
            "amount": amount, "date": day.isoformat(), "type": type_,
            "currency": CURRENCY,
        }
        if category is not None:
            payload["category_id"] = str(category.id)
        payload.update(extra)
        resp = await client.post("/api/transactions", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    await create(1000, "credit", income)
    await create(150, "debit", food)
    await create(100, "debit", transport)
    target = await create(200, "debit")  # uncategorized until split

    return {
        "client": client, "headers": auth_headers, "session": session, "user": test_user,
        "account": account, "day": day, "target": target, "create": create,
        "food": food, "transport": transport, "income": income, "invest": invest,
    }


async def _split(scene, *parts) -> dict:
    resp = await scene["client"].patch(
        f"/api/transactions/{scene['target']['id']}", headers=scene["headers"],
        json={"category_lines": {
            "share_type": "exact",
            "lines": [{"category_id": str(c.id), "amount": a} for c, a in parts],
        }},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get(scene, path, **params):
    resp = await scene["client"].get(path, headers=scene["headers"], params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _r(value) -> float:
    return round(float(value or 0), 2)


async def _snapshot(scene) -> dict:
    """Every category-dependent figure for the scene's month."""
    month = scene["day"].isoformat()
    month_key = scene["day"].strftime("%Y-%m")
    account_id = scene["account"]["id"]

    summary = await _get(scene, "/api/dashboard/summary", month=month)
    spending = await _get(scene, "/api/dashboard/spending-by-category", month=month)
    trend = await _get(scene, "/api/dashboard/monthly-trend", months=1)
    report = await _get(scene, "/api/reports/income-expenses", months=1, interval="monthly")
    budgets = await _get(scene, "/api/budgets/comparison", month=month)
    calendar = await _get(scene, "/api/transactions/calendar", month=month)
    listing = await _get(
        scene, "/api/transactions", **{"from": month, "to": app_today().isoformat()}
    )
    account_summary = await _get(scene, f"/api/accounts/{account_id}/summary")
    account = await _get(scene, f"/api/accounts/{account_id}")
    aggregate = await REGISTRY["aggregate"].handler(
        session=scene["session"],
        ctx=CallContext(user_id=scene["user"].id, conversation_id=uuid.uuid4()),
        metric="sum", group_by="category", tx_type="expense",
        from_date=month,
    )

    breakdowns = {b["key"]: b["value"] for b in report["summary"]["breakdowns"]}
    composition = {
        (item.get("group"), item["label"]): _r(item["value"])
        for item in report.get("composition") or []
    }
    trend_month = next((t for t in trend if t["month"] == month_key), None)
    days = [d for d in calendar["days"] if d["in_month"]]
    return {
        "income": _r(summary["monthly_income"]),
        "expenses": _r(summary["monthly_expenses"]),
        "income_primary": _r(summary["monthly_income_primary"]),
        "expenses_primary": _r(summary["monthly_expenses_primary"]),
        "pending_categorization": summary["pending_categorization"],
        "spending": {s["category_name"]: _r(s["total"]) for s in spending},
        "trend": (_r(trend_month["income"]), _r(trend_month["expenses"])) if trend_month else None,
        "report": (_r(breakdowns["income"]), _r(breakdowns["expenses"])),
        "composition": composition,
        "budgets": {b["category_name"]: _r(b["actual_amount"]) for b in budgets if _r(b["actual_amount"])},
        "calendar": (
            _r(sum(d["income"] for d in days)),
            _r(sum(d["expense"] for d in days)),
            _r(sum(d["transfer_net"] for d in days)),
        ),
        "footer": (
            _r(listing["summary"]["income"]),
            _r(listing["summary"]["expense"]),
            _r(listing["summary"]["excluded"]),
        ),
        "list_count": listing["total"],
        "account_summary": (
            _r(account_summary["monthly_income"]), _r(account_summary["monthly_expenses"]),
        ),
        "balance": _r(account.get("current_balance", account.get("balance"))),
        "aggregate": {
            item["label"]: _r(item["value"]) for item in aggregate["items"]
        },
    }


async def test_the_scene_before_any_split(scene):
    snap = await _snapshot(scene)

    assert snap["income"] == 1000 and snap["expenses"] == 450
    assert snap["spending"] == {"Alimentação": 150, "Transporte": 100, "Sem categoria": 200}
    assert snap["pending_categorization"] == 1
    assert snap["footer"] == (1000, 450, 0)
    assert snap["calendar"] == (1000, 450, 0)
    assert snap["balance"] == 550


async def test_a_split_across_categories_changes_no_total(scene):
    before = await _snapshot(scene)

    await _split(scene, (scene["food"], 120), (scene["transport"], 80))
    after = await _snapshot(scene)

    for key in (
        "income", "expenses", "income_primary", "expenses_primary", "trend", "report",
        "calendar", "footer", "list_count", "account_summary", "balance",
    ):
        assert after[key] == before[key], key
    # The money moved between categories, each part to its own.
    assert after["spending"] == {"Alimentação": 270, "Transporte": 180}
    assert after["budgets"] == {"Alimentação": 270, "Transporte": 180}
    assert after["aggregate"] == {"Alimentação": 270, "Transporte": 180}
    assert after["composition"][("expenses", "Alimentação")] == 270
    assert after["composition"][("expenses", "Transporte")] == 180
    assert ("expenses", "Uncategorized") not in after["composition"]
    # A split row is categorized: it no longer waits for a category.
    assert after["pending_categorization"] == before["pending_categorization"] - 1
    assert sum(after["spending"].values()) == sum(before["spending"].values())


async def test_a_transfer_like_part_leaves_the_totals_by_exactly_its_amount(scene):
    before = await _snapshot(scene)

    await _split(scene, (scene["food"], 140), (scene["invest"], 60))
    after = await _snapshot(scene)

    assert after["income"] == before["income"]
    assert after["expenses"] == before["expenses"] - 60
    assert after["expenses_primary"] == before["expenses_primary"] - 60
    assert after["trend"] == (before["trend"][0], before["trend"][1] - 60)
    assert after["report"] == (before["report"][0], before["report"][1] - 60)
    assert after["account_summary"] == (
        before["account_summary"][0], before["account_summary"][1] - 60,
    )
    assert after["spending"] == {"Alimentação": 290, "Transporte": 100}
    assert after["budgets"] == {"Alimentação": 290, "Transporte": 100}
    # The part shows up where transfers do, and nowhere else.
    assert after["footer"] == (1000, 390, 60)
    assert after["calendar"] == (1000, 390, -60)
    assert after["composition"][("investments", "Investments")] == 60
    # The MCP tool leaves transfers out by default, part by part too.
    assert after["aggregate"] == {"Alimentação": 290, "Transporte": 100}
    # The money still left the account.
    assert after["balance"] == before["balance"]


async def test_undoing_a_split_restores_every_figure(scene):
    before = await _snapshot(scene)
    await _split(scene, (scene["food"], 140), (scene["invest"], 60))

    resp = await scene["client"].patch(
        f"/api/transactions/{scene['target']['id']}", headers=scene["headers"],
        json={"category_lines": {"share_type": "exact", "lines": []}},
    )
    assert resp.status_code == 200, resp.text
    after = await _snapshot(scene)

    # The largest part's category comes back, so the 200 now sits in Food.
    assert after["spending"] == {"Alimentação": 350, "Transporte": 100}
    for key in ("income", "expenses", "report", "calendar", "footer", "balance"):
        assert after[key] == before[key], key


async def test_the_list_filters_and_totals_a_split_row_by_its_parts(scene):
    await _split(scene, (scene["food"], 140), (scene["invest"], 60))
    target_id = scene["target"]["id"]
    month = scene["day"].isoformat()
    today = app_today().isoformat()

    by_invest = await _get(
        scene, "/api/transactions", category_id=str(scene["invest"].id),
        **{"from": month, "to": today},
    )
    assert [row["id"] for row in by_invest["items"]] == [target_id]
    # Money crosses JSON as a decimal string, never a float.
    assert by_invest["items"][0]["matched_amount"] == "60.00"
    assert by_invest["items"][0]["matched_amount_primary"] in (None, "60.00")
    # Only the matching part is in the footer, and it is a transfer.
    assert by_invest["summary"]["expense"] == 0
    assert by_invest["summary"]["excluded"] == 60

    by_food = await _get(
        scene, "/api/transactions", category_ids=[str(scene["food"].id)],
        **{"from": month, "to": today},
    )
    assert {row["id"] for row in by_food["items"]} >= {target_id}
    assert by_food["summary"]["expense"] == 290  # 150 + the 140 part

    # Plain rows carry no matched amount.
    plain = [row for row in by_food["items"] if row["id"] != target_id]
    assert plain and all(row["matched_amount"] is None for row in plain)

    uncategorized = await _get(scene, "/api/transactions", uncategorized=True)
    assert target_id not in {row["id"] for row in uncategorized["items"]}

    transfers = await _get(scene, "/api/transactions", type="transfer")
    assert [row["id"] for row in transfers["items"]] == [target_id]
    # The row qualifies by its transfer part; its food part is not a transfer.
    assert transfers["summary"]["expense"] == 0
    assert transfers["summary"]["excluded"] == 60

    pnl_only = await _get(scene, "/api/transactions", user_pnl_only=True)
    assert target_id in {row["id"] for row in pnl_only["items"]}


async def test_the_calendar_leaves_out_a_part_whose_category_became_ignored(scene):
    """A line cannot be saved in an ignored category, but the category can be
    ignored while the split is saved. The part then drops out, as in every
    other report."""
    await _split(scene, (scene["food"], 140), (scene["transport"], 60))
    scene["transport"].is_ignored = True
    scene["session"].add(scene["transport"])
    await scene["session"].commit()

    snap = await _snapshot(scene)
    assert snap["calendar"][1] == 150 + 140
    assert snap["expenses"] == 150 + 140


async def test_a_split_row_that_is_all_transfer_drops_out_of_user_pnl_lists(scene):
    savings = Category(
        id=uuid.uuid4(), user_id=scene["user"].id, name="Savings",
        icon="piggy-bank", color="#10B981", treat_as_transfer=True,
    )
    scene["session"].add(savings)
    await scene["session"].commit()
    await _split(scene, (scene["invest"], 150), (savings, 50))

    pnl_only = await _get(scene, "/api/transactions", user_pnl_only=True)
    assert scene["target"]["id"] not in {row["id"] for row in pnl_only["items"]}
    snap = await _snapshot(scene)
    assert snap["expenses"] == 250
    assert snap["calendar"][2] == -200


async def test_sorting_by_category_keeps_split_rows_together(scene):
    await _split(scene, (scene["food"], 120), (scene["transport"], 80))
    second = await scene["create"](
        90, "debit",
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(scene["food"].id), "amount": 45},
            {"category_id": str(scene["transport"].id), "amount": 45},
        ]},
    )

    listing = await _get(scene, "/api/transactions", sort_by="category", sort_dir="asc")
    ids = [row["id"] for row in listing["items"]]
    split_positions = sorted(ids.index(i) for i in (scene["target"]["id"], second["id"]))
    assert split_positions[1] - split_positions[0] == 1


async def test_a_foreign_currency_split_sums_to_its_primary_amount(scene):
    resp = await scene["client"].post(
        "/api/accounts", headers=scene["headers"],
        json={"name": "Euro", "type": "checking", "balance": 0, "currency": "EUR"},
    )
    assert resp.status_code in (200, 201), resp.text
    euro = resp.json()
    resp = await scene["client"].post(
        "/api/transactions", headers=scene["headers"],
        json={
            "account_id": euro["id"], "description": "Hotel", "amount": 100,
            "date": scene["day"].isoformat(), "type": "debit", "currency": "EUR",
            "amount_primary": 610, "category_lines": {"share_type": "percent", "lines": [
                {"category_id": str(scene["food"].id), "share_pct": 30},
                {"category_id": str(scene["transport"].id), "share_pct": 70},
            ]},
        },
    )
    assert resp.status_code == 201, resp.text
    hotel = resp.json()
    primary = Decimal(str(hotel["amount_primary"]))

    snap = await _snapshot(scene)
    food_part = Decimal(str(snap["spending"]["Alimentação"])) - Decimal("150")
    transport_part = Decimal(str(snap["spending"]["Transporte"])) - Decimal("100")
    assert food_part + transport_part == primary
    assert abs(food_part - primary * Decimal("0.3")) <= Decimal("0.01")
    assert snap["expenses_primary"] == _r(Decimal("450") + primary)


async def test_a_forecast_split_projects_part_by_part(scene):
    """Future-dated rows feed the forecast fields through Python loops."""
    future = date(scene["day"].year + 1, scene["day"].month, 1)
    await scene["create"](
        300, "debit", date=future.isoformat(),
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(scene["food"].id), "amount": 200},
            {"category_id": str(scene["invest"].id), "amount": 100},
        ]},
    )
    month = future.isoformat()
    summary = await _get(scene, "/api/dashboard/summary", month=month)
    spending = await _get(scene, "/api/dashboard/spending-by-category", month=month)
    budgets = await _get(scene, "/api/budgets/comparison", month=month)

    assert _r(summary["projected_expenses"]) == 200
    assert {s["category_name"]: _r(s["projected_total"]) for s in spending} == {
        "Alimentação": 200,
    }
    projected = {b["category_name"]: _r(b["projected_amount"]) for b in budgets}
    assert projected["Alimentação"] == 200
    assert projected.get("Investments", 0) == 0


async def test_a_part_in_primary_currency_is_exact(scene):
    """The part's primary amount is the row's, scaled by the part, without a
    rounded ratio carried into every total (150 * 100 / 150, not
    150 * 0.666...)."""
    target = await scene["create"](
        150, "debit", description="three ways",
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(scene["food"].id), "amount": 100},
            {"category_id": str(scene["transport"].id), "amount": 50},
        ]},
    )
    assert target["has_category_lines"] is True
    rows = await _get(scene, "/api/budgets/comparison", month=scene["day"].isoformat())
    food = next(row for row in rows if row["category_id"] == str(scene["food"].id))
    assert Decimal(str(food["actual_amount"])) == Decimal("250")
