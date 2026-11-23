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

import itertools
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient

import mcp_server.tools  # noqa: F401 -- registers the MCP handlers
from app.core.app_clock import app_today
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.report_service import _without_mirrored_legs
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.conftest import sqlite_to_char

pytestmark = pytest.mark.asyncio

CURRENCY = "BRL"  # the test user's primary currency


async def _install_sqlite_to_char(session) -> None:
    """`to_char(date, fmt)` for the monthly trend; PostgreSQL has it."""
    if session.bind.dialect.name != "sqlite":
        return

    connection = await session.connection()
    await connection.run_sync(
        lambda conn: conn.connection.dbapi_connection.create_function(
            "to_char", 2, sqlite_to_char
        )
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


async def test_an_investment_withdrawal_is_a_source_in_the_money_map(scene):
    """Money taken out of an investment pays for the month without being
    income (#1418): the Money Map gets it as its own group, beside the
    contributions, and the income and expense totals do not move."""
    before = await _snapshot(scene)

    await scene["create"](300, "credit", scene["invest"])
    await scene["create"](60, "debit", scene["invest"])
    after = await _snapshot(scene)

    assert after["composition"][("withdrawals", "Investments")] == 300
    assert after["composition"][("investments", "Investments")] == 60
    assert after["report"] == before["report"]
    assert after["income"] == before["income"]
    assert after["expenses"] == before["expenses"]


async def test_an_unpaired_card_bill_payment_is_neither_a_withdrawal_nor_a_contribution(scene):
    """A bill paid from checking and imported from both statements is a debit
    on the account and a credit on the card, never paired (#1551). Both legs
    are the same money: they leave the Money Map, and a real withdrawal in
    the same month still shows."""
    bill = Category(
        id=uuid.uuid4(), user_id=scene["user"].id, name="Pagamento de fatura",
        icon="credit-card", color="#64748B", treat_as_transfer=True,
    )
    scene["session"].add(bill)
    await scene["session"].commit()
    resp = await scene["client"].post(
        "/api/accounts", headers=scene["headers"],
        json={"name": "Itaú Azul", "type": "credit_card", "balance": 0, "currency": CURRENCY},
    )
    assert resp.status_code in (200, 201), resp.text
    card = resp.json()
    before = await _snapshot(scene)

    await scene["create"](300, "credit", scene["invest"])
    await scene["create"](2500, "debit", bill, description="PAGTO FATURA ITAU AZUL")
    await scene["create"](
        2500, "credit", bill, account_id=card["id"], description="PAGAMENTO RECEBIDO",
    )
    after = await _snapshot(scene)

    assert after["composition"][("withdrawals", "Investments")] == 300
    assert ("withdrawals", "Pagamento de fatura") not in after["composition"]
    assert ("investments", "Pagamento de fatura") not in after["composition"]
    assert after["report"] == before["report"]


async def test_legs_are_matched_by_booking_date_not_reporting_override(scene):
    """A credit booked nine days after the debit is another movement, even
    when its reporting date was moved onto the debit's day."""
    bill = Category(
        id=uuid.uuid4(), user_id=scene["user"].id, name="Pagamento de fatura",
        icon="credit-card", color="#64748B", treat_as_transfer=True,
    )
    scene["session"].add(bill)
    await scene["session"].commit()
    resp = await scene["client"].post(
        "/api/accounts", headers=scene["headers"],
        json={"name": "Itaú Azul", "type": "credit_card", "balance": 0, "currency": CURRENCY},
    )
    assert resp.status_code in (200, 201), resp.text
    day = scene["day"]

    await scene["create"](2500, "credit", bill, account_id=resp.json()["id"])
    debit = await scene["create"](
        2500, "debit", bill, date=(day + timedelta(days=9)).isoformat(),
    )
    # The override is only offered on a synced row of a non-card account.
    row = await scene["session"].get(Transaction, uuid.UUID(debit["id"]))
    row.source = "sync"
    row.reporting_date_override = day
    await scene["session"].commit()
    snap = await _snapshot(scene)

    assert snap["composition"][("investments", "Pagamento de fatura")] == 2500
    assert snap["composition"][("withdrawals", "Pagamento de fatura")] == 2500


@pytest.mark.parametrize("reverse_credits", [False, True])
async def test_mirrored_legs_do_not_depend_on_row_order(reverse_credits):
    """Debits on A on the 2nd and 4th, credits on B on the 1st and 3rd: all
    four pair up whichever credit comes first, so none is left over."""
    cat = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()

    def row(kind, account, day):
        return (cat, "Bill", "#000", kind, Decimal("100.00"), account, date(2026, 11, day))

    debits = [row("debit", a, 2), row("debit", a, 4)]
    credits = [row("credit", b, 3), row("credit", b, 1)]
    if reverse_credits:
        credits.reverse()

    assert _without_mirrored_legs(debits + credits) == []
    assert _without_mirrored_legs(credits + debits) == []


async def test_a_thousand_identical_legs_pair_without_recursion():
    """1,300 debits and 1,300 credits of 77.77 on one day, in two accounts
    (#319): they all pair up instead of hitting the recursion limit."""
    cat, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    day = date(2020, 1, 15)
    debits = [(cat, "Inv", "#000", "debit", Decimal("77.77"), a, day)] * 1300
    credits = [(cat, "Inv", "#000", "credit", Decimal("77.77"), b, day)] * 1300
    extra = (cat, "Inv", "#000", "credit", Decimal("77.77"), b, day)

    assert _without_mirrored_legs(debits + credits) == []
    assert _without_mirrored_legs(credits + [extra] + debits) == [extra[:5]]


async def test_two_thousand_daily_pairs_all_cancel():
    """One debit and one credit a day for 2,000 days: each pairs with its own
    day, and nothing is left over."""
    cat, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    start = date(2015, 1, 1)
    rows = []
    for n in range(2000):
        day = start + timedelta(days=n)
        rows.append((cat, "Inv", "#000", "debit", Decimal("10.00"), a, day))
        rows.append((cat, "Inv", "#000", "credit", Decimal("10.00"), b, day))

    assert _without_mirrored_legs(rows) == []


@pytest.mark.parametrize("seed", range(40))
async def test_mirrored_legs_match_a_brute_force_count(seed):
    """Random small groups over three accounts and a week: as many legs are
    dropped as the largest set of disjoint fitting pairs allows."""
    rng = random.Random(seed)
    cat = uuid.uuid4()
    accounts = [uuid.uuid4() for _ in range(3)]
    rows = [
        (
            cat, "Bill", "#000", rng.choice(["debit", "credit"]), Decimal("50.00"),
            rng.choice(accounts), date(2026, 11, rng.randint(1, 7)),
        )
        for _ in range(rng.randint(1, 9))
    ]
    credits = [r for r in rows if r[3] == "credit"]
    debits = [r for r in rows if r[3] == "debit"]

    def fits(c, d):
        return c[5] != d[5] and abs((c[6] - d[6]).days) <= 2

    best = 0
    for size in range(min(len(credits), len(debits)), 0, -1):
        if any(
            all(fits(credits[i], debits[j]) for i, j in zip(cs, ds))
            for cs in itertools.combinations(range(len(credits)), size)
            for ds in itertools.permutations(range(len(debits)), size)
        ):
            best = size
            break

    left = _without_mirrored_legs(rows)
    assert len(left) == len(rows) - 2 * best
    for kind in ("credit", "debit"):
        assert sum(r[3] == kind for r in left) == sum(r[3] == kind for r in rows) - best


async def test_a_movement_in_one_account_only_still_counts(scene):
    """Same amount and category, but both in the same account: an
    application and its redemption, not two legs of one movement."""
    await scene["create"](500, "debit", scene["invest"])
    await scene["create"](500, "credit", scene["invest"])
    snap = await _snapshot(scene)

    assert snap["composition"][("investments", "Investments")] == 500
    assert snap["composition"][("withdrawals", "Investments")] == 500


async def test_a_month_without_withdrawals_has_no_withdrawal_group(scene):
    snap = await _snapshot(scene)

    assert not [key for key in snap["composition"] if key[0] == "withdrawals"]
