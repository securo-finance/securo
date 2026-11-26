"""A split expense of a shared workspace counts each viewer's own share.

Ana owns the workspace and pays everything in reais. Bruno (EUR) is a member
and is in the group split of the rent; Carla (BRL) is a member too, outside
the group. The category statement already gave Bruno his share while the
dashboard, Income vs Expenses, the budgets and the list footer counted the
whole row for him.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.group import Group, GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services.budget_service import get_budget_vs_actual
from app.services.category_statement_service import get_category_statement
from app.services.dashboard_service import get_spending_by_category, get_summary
from app.services.report_service import get_income_expenses_report
from app.services.transaction_service import get_transactions


async def _workspace_member(session: AsyncSession, workspace_id, name: str, currency: str) -> User:
    user = User(
        id=uuid.uuid4(), email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x", is_active=True, is_superuser=False, is_verified=True,
        preferences={"currency_display": currency},
    )
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace_id, user_id=user.id, role="editor"
        )
    )
    return user


def _split(tx: Transaction, member: GroupMember, share: str) -> TransactionSplit:
    return TransactionSplit(
        id=uuid.uuid4(), transaction_id=tx.id, workspace_id=tx.workspace_id,
        group_member_id=member.id, share_amount=Decimal(share),
    )


@pytest.fixture
async def home(session: AsyncSession, test_user: User, test_workspace, test_categories):
    """Last month, R$ 5 = 1 EUR: a R$ 1.000 rent split 750 Ana / 250 Bruno, a
    R$ 400 grocery bill nobody splits, a split R$ 200 row kept out of P/L and
    a R$ 300 split expense still pending, dated today."""
    month = (app_today().replace(day=1) - timedelta(days=1)).replace(day=1)
    day = month + timedelta(days=4)
    for when in (day, app_today()):
        session.add_all(
            FxRate(
                id=uuid.uuid4(), base_currency="USD", quote_currency=quote,
                rate=Decimal(rate), date=when, source="test",
            )
            for quote, rate in (("BRL", "5"), ("EUR", "1"))
        )
    bruno = await _workspace_member(session, test_workspace.id, "bruno", "EUR")
    carla = await _workspace_member(session, test_workspace.id, "carla", "BRL")

    group = Group(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Home"
    )
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Joint", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add_all([group, account])
    await session.flush()
    ana_member, bruno_member = (
        GroupMember(
            id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id, **fields
        )
        for fields in (
            {"name": "Ana", "is_self": True, "linked_user_id": test_user.id},
            {"name": "Bruno", "linked_user_id": bruno.id},
        )
    )
    session.add_all([ana_member, bruno_member])

    rent_cat, food_cat = test_categories[0], test_categories[1]

    def tx(amount: str, category_id, when: date, **fields) -> Transaction:
        row = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=account.id, category_id=category_id, description="Household",
            amount=Decimal(amount), currency="BRL", amount_primary=Decimal(amount),
            date=when, type="debit", source="manual", **fields,
        )
        session.add(row)
        return row

    rent = tx("1000", rent_cat.id, day)
    tx("400", food_cat.id, day)
    hidden = tx("200", rent_cat.id, day, exclude_from_pnl=True)
    pending = tx("300", rent_cat.id, app_today(), status="pending")
    await session.flush()
    for row, ana_share, bruno_share in (
        (rent, "750", "250"), (hidden, "100", "100"), (pending, "225", "75"),
    ):
        session.add_all([
            _split(row, ana_member, ana_share), _split(row, bruno_member, bruno_share),
        ])
    await session.commit()
    return {
        "month": month, "bruno": bruno, "carla": carla, "ana": test_user,
        "rent_id": rent_cat.id, "food_id": food_cat.id,
    }


# viewer, currency, rent, groceries, pending rent
VIEWERS = [
    ("ana", "BRL", 750.0, 400.0, 225.0),
    ("bruno", "EUR", 50.0, 80.0, 15.0),
    ("carla", "BRL", 1000.0, 400.0, 300.0),
]


def _last_day(month: date) -> date:
    return (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)


async def _report(session, workspace_id, user_id, month, currency, **kwargs):
    return await get_income_expenses_report(
        session, workspace_id, user_id, currency=currency,
        start_date=month, end_date=_last_day(month), **kwargs,
    )


def _expenses(report) -> float:
    return next(b.value for b in report.summary.breakdowns if b.key == "expenses")


@pytest.mark.asyncio
@pytest.mark.parametrize("name,currency,rent,food,_pending", VIEWERS)
async def test_the_three_sources_count_the_viewers_share(
    session, test_workspace, home, name, currency, rent, food, _pending
):
    user, month, ws = home[name], home["month"], test_workspace.id

    statement = await get_category_statement(
        session, ws, user.id, month, months=1, primary_currency=currency
    )
    assert statement.expenses.totals == pytest.approx([-(rent + food)])

    report = await _report(session, ws, user.id, month, currency)
    assert _expenses(report) == pytest.approx(rent + food)
    slices = {i.key: i.value for i in report.composition if i.group == "expenses"}
    assert slices == pytest.approx({str(home["rent_id"]): rent, str(home["food_id"]): food})

    summary = await get_summary(session, ws, user.id, month)
    assert float(summary.monthly_expenses_primary) == pytest.approx(rent + food)
    spending = await get_spending_by_category(session, ws, user.id, month)
    assert {row.category_id: row.total for row in spending} == pytest.approx(
        {str(home["rent_id"]): rent, str(home["food_id"]): food}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name,_currency,rent,food,_pending", VIEWERS)
async def test_the_list_footer_and_budgets_follow(
    session, test_workspace, home, name, _currency, rent, food, _pending
):
    user, month, ws = home[name], home["month"], test_workspace.id
    for user_pnl_only in (True, False):
        result = await get_transactions(
            session, ws, user.id, from_date=month, to_date=_last_day(month),
            include_summary=True, user_pnl_only=user_pnl_only,
        )
        footer = result[2]
        assert footer is not None
        assert float(footer["expense"]) == pytest.approx(rent + food)

    budgets = await get_budget_vs_actual(session, ws, user.id, month)
    actual = {str(b.category_id): float(b.actual_amount) for b in budgets}
    assert actual[str(home["rent_id"])] == pytest.approx(rent)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,_currency,_rent,_food,pending", VIEWERS)
async def test_a_pending_split_is_projected_at_the_viewers_share(
    session, test_workspace, home, name, _currency, _rent, _food, pending
):
    spending = await get_spending_by_category(
        session, test_workspace.id, home[name].id, app_today().replace(day=1)
    )
    row = next(r for r in spending if r.category_id == str(home["rent_id"]))
    assert row.projected_total == pytest.approx(pending)


@pytest.mark.asyncio
async def test_an_account_filter_keeps_the_full_amounts(session, test_workspace, home):
    from sqlalchemy import select

    accounts = list(
        (await session.execute(
            select(Account.id).where(Account.workspace_id == test_workspace.id)
        )).scalars()
    )
    report = await _report(
        session, test_workspace.id, home["bruno"].id, home["month"], "EUR",
        account_ids=accounts,
    )
    assert _expenses(report) == pytest.approx(280.0)

    # The footer behind the report's drill-down follows, with either way of
    # naming the account.
    for account_id, account_ids in ((None, accounts), (accounts[0], None)):
        for user_pnl_only in (True, False):
            result = await get_transactions(
                session, test_workspace.id, home["bruno"].id,
                from_date=home["month"], to_date=_last_day(home["month"]),
                include_summary=True, user_pnl_only=user_pnl_only,
                account_id=account_id, account_ids=account_ids,
            )
            footer = result[2]
            assert footer is not None
            assert float(footer["expense"]) == pytest.approx(280.0)


async def _add_unstamped_split(session, test_workspace, home) -> None:
    """A JPY 10.000 expense with no rate, split in half."""
    from sqlalchemy import select

    members = (
        await session.execute(
            select(GroupMember).where(GroupMember.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    account_id = await session.scalar(
        select(Account.id).where(Account.workspace_id == test_workspace.id, Account.name == "Joint")
    )
    row = Transaction(
        id=uuid.uuid4(), user_id=home["ana"].id, workspace_id=test_workspace.id,
        account_id=account_id, category_id=home["rent_id"], description="Trip",
        amount=Decimal("10000"), currency="JPY", amount_primary=None,
        date=home["month"] + timedelta(days=4), type="debit", source="manual",
    )
    session.add(row)
    await session.flush()
    session.add_all([_split(row, member, "5000") for member in members])
    await session.commit()


@pytest.mark.asyncio
async def test_a_split_without_a_rate_takes_nothing_off_the_dashboard_card(
    session, test_workspace, home
):
    """The card leaves a row with no stamp out of its sum, so its share can't
    come off it: 10.000 JPY used to turn Bruno's expenses negative."""
    await _add_unstamped_split(session, test_workspace, home)
    summary = await get_summary(session, test_workspace.id, home["bruno"].id, home["month"])
    assert float(summary.monthly_expenses_primary) == pytest.approx(130.0)


@pytest.mark.asyncio
async def test_a_split_without_a_rate_counts_its_share_where_the_row_counts(
    session, test_workspace, home
):
    """These read the row 1:1, so the viewer's share stays on top: 5.000."""
    await _add_unstamped_split(session, test_workspace, home)
    bruno, month, ws = home["bruno"], home["month"], test_workspace.id

    statement = await get_category_statement(
        session, ws, bruno.id, month, months=1, primary_currency="EUR"
    )
    assert statement.expenses.totals == pytest.approx([-5130.0])
    report = await _report(session, ws, bruno.id, month, "EUR")
    assert _expenses(report) == pytest.approx(5130.0)
    spending = await get_spending_by_category(session, ws, bruno.id, month)
    assert sum(row.total for row in spending) == pytest.approx(5130.0)


@pytest.mark.asyncio
async def test_the_card_stays_right_when_no_row_has_a_rate(session, test_workspace, home):
    """With no stamped row at all the card falls back to the native sum,
    which already took the shares off once."""
    from sqlalchemy import update

    await session.execute(
        update(Transaction)
        .where(Transaction.workspace_id == test_workspace.id)
        .values(amount_primary=None)
    )
    await session.commit()
    summary = await get_summary(session, test_workspace.id, home["bruno"].id, home["month"])
    assert float(summary.monthly_expenses_primary) == pytest.approx(650.0)
