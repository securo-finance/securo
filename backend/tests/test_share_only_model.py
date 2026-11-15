"""Conformity checks for the share-only P/L model.

Under this model, an owner-side split transaction contributes only the
*owner's share* to user-level P/L (dashboard summary, spending-by-
category, reports, budgets). The non-owner shares are reimbursable, not
real cost. Settlement credits are also dropped from P/L because the
share already captured the cost — counting the credit again would
double-book.

Account-level stats (per-account income/expenses, balance history) keep
full amounts because they track real cash through the account.

The tests here pin those guarantees end-to-end: split layout → service
output. Most run in single-currency USD; the cross-currency ones seed
USD↔EUR rates explicitly so SQLite has something to convert with.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate
from app.schemas.transaction_split import (
    TransactionSplitInput,
    TransactionSplitsInput,
)
from app.services import (
    admin_service,
    balance_service,
    budget_service,
    dashboard_service,
    group_service,
    settlement_service,
    split_service,
)


# ──────────────────────────── helpers ─────────────────────────────


async def _make_account(
    session: AsyncSession, user_id, currency: str = "USD"
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name="W",
        type="checking",
        balance=Decimal("0"),
        currency=currency,
    )
    session.add(account)
    await session.flush()
    return account


async def _make_tx(
    session: AsyncSession,
    user_id,
    account_id,
    amount: str,
    currency: str = "USD",
    *,
    type_: str = "debit",
    category_id=None,
    when: date | None = None,
    description: str = "tx",
    source: str = "manual",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        description=description,
        amount=Decimal(amount),
        currency=currency,
        date=when or date.today(),
        type=type_,
        source=source,
        amount_primary=Decimal(amount) if currency == "USD" else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    return tx


async def _setup_group(
    session: AsyncSession,
    user,
    workspace_id,
    *member_names: str,
    default_currency: str = "USD",
):
    group = await group_service.create_group(
        session,
        workspace_id,
        user.id,
        GroupCreate(name=f"G-{uuid.uuid4().hex[:6]}", default_currency=default_currency),
    )
    members = []
    for i, name in enumerate(member_names):
        m = await group_service.create_member(
            session,
            group.id,
            workspace_id,
            GroupMemberCreate(name=name, is_self=(i == 0)),
        )
        members.append(m)
    return group, members


async def _force_user_currency(session: AsyncSession, user, code: str) -> None:
    """Pin the test_user's primary currency so dashboard math is predictable."""
    prefs = dict(user.preferences or {})
    prefs["currency_display"] = code
    user.preferences = prefs
    await session.commit()
    await session.refresh(user)


def _month_window(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


# ─────────────── share-only model: dashboard summary ────────────────


@pytest.mark.asyncio
async def test_summary_owner_share_caps_expense(
    session: AsyncSession, test_user, test_workspace
):
    """Owner pays $150 split equally with two friends ($50 share). The
    dashboard should report a $50 expense, not $150."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "150.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A", "B"
    )

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == pytest.approx(50.0, abs=0.01)
    assert summary.monthly_income_primary == 0.0


@pytest.mark.asyncio
async def test_summary_owner_with_zero_share(
    session: AsyncSession, test_user, test_workspace
):
    """Owner fronts a $150 expense entirely for two friends. They each
    have a $75 share; owner has no share. Expense should be $0."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "150.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A", "B"
    )
    me, friend_a, friend_b = members

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=friend_a.id, share_amount=Decimal("75.00")
                ),
                TransactionSplitInput(
                    group_member_id=friend_b.id, share_amount=Decimal("75.00")
                ),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == 0.0


@pytest.mark.asyncio
async def test_summary_no_split_full_amount_counts(
    session: AsyncSession, test_user, test_workspace
):
    """A regular non-split transaction must still count its full amount —
    the offset only applies when splits exist."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    await _make_tx(session, test_user.id, account.id, "80.00", when=today)
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == pytest.approx(80.0, abs=0.01)


@pytest.mark.asyncio
async def test_summary_drops_settlement_credits_from_income(
    session: AsyncSession, test_user, test_workspace
):
    """Receiver-side settlement credits no longer count as income — the
    parent share already booked the expense, so the credit would
    double-count."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "60.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[
                TransactionSplitInput(group_member_id=me.id),
                TransactionSplitInput(group_member_id=friend.id),
            ],
        ),
        test_user.id,
    )
    # Friend pays the owner $30 — both the payer-side debit and the
    # receiver-side credit get auto-stamped as source='settlement'.
    await settlement_service.create_settlement(
        session,
        members[0].group_id,
        test_workspace.id,
        test_user.id,
        GroupSettlementCreate(
            from_member_id=friend.id,
            to_member_id=me.id,
            amount=Decimal("30.00"),
            currency="USD",
            date=today,
        ),
    )

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    # Owner's expense = $30 share. No income from the settlement credit.
    assert summary.monthly_expenses_primary == pytest.approx(30.0, abs=0.01)
    assert summary.monthly_income_primary == 0.0


@pytest.mark.asyncio
async def test_summary_settlement_debit_excluded_for_payer(
    session: AsyncSession, test_user, test_workspace
):
    """Linked-member side: paying back a debt is not an expense — the
    share already captured it."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    # Settlement-sourced debit on the user's account, by hand.
    await _make_tx(
        session,
        test_user.id,
        account.id,
        "30.00",
        when=today,
        source="settlement",
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == 0.0


# ─────────────── share-only model: spending by category ─────────────


@pytest.mark.asyncio
async def test_spending_by_category_uses_owner_share(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A categorized split contributes only the owner's share to its
    bucket. No share → category drops out."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    food = test_categories[0]  # Alimentação
    account = await _make_account(session, test_user.id)

    # Brunch $50, owner share $25 → category should show $25
    brunch = await _make_tx(
        session,
        test_user.id,
        account.id,
        "50.00",
        category_id=food.id,
        description="Brunch",
        when=today,
    )
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    await split_service.replace_splits(
        session,
        brunch,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(group_member_id=me.id, share_amount=Decimal("25.00")),
                TransactionSplitInput(group_member_id=friend.id, share_amount=Decimal("25.00")),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    food_row = next(s for s in spending if s.category_name == "Alimentação")
    assert food_row.total == pytest.approx(25.0, abs=0.01)


@pytest.mark.asyncio
async def test_spending_by_category_drops_zero_share_category(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A split where the owner has no share = category contributes $0
    and should not appear in the breakdown at all."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    food = test_categories[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(
        session,
        test_user.id,
        account.id,
        "100.00",
        category_id=food.id,
        when=today,
    )
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A"
    )
    me, friend = members
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=friend.id, share_amount=Decimal("100.00")
                ),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert all(s.category_name != "Alimentação" for s in spending)


# ─────────────── owner self-member detection ─────────────


@pytest.mark.asyncio
async def test_owner_self_member_recognized_without_link(
    session: AsyncSession, test_user, test_workspace
):
    """The owner's GroupMember has is_self=True but linked_user_id=NULL.
    The offset query must still recognize it as the owner's own share —
    otherwise the regression we hit before returns: the owner's share
    would be subtracted as if it were a non-owner share, blowing the
    expense down to $0."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "90.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    # Sanity-check the schema invariant the bug exploited.
    assert me.is_self is True
    assert me.linked_user_id is None

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[
                TransactionSplitInput(group_member_id=me.id),
                TransactionSplitInput(group_member_id=friend.id),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    # Owner's $45 share survives; the previous bug would have returned 0.
    assert summary.monthly_expenses_primary == pytest.approx(45.0, abs=0.01)


# ─────────────── balance lines: cross-currency rollup ─────────────


async def _seed_eur_rate(session: AsyncSession, when: date) -> None:
    """Seed a USD→EUR rate so balance_service can FX-convert. Stored as
    base=USD,quote=EUR per the rate model convention."""
    session.add(
        FxRate(
            id=uuid.uuid4(),
            base_currency="USD",
            quote_currency="EUR",
            rate=Decimal("0.85"),  # 1 USD = 0.85 EUR ⇒ €100 ≈ $117.65
            date=when,
            source="test",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_balance_line_includes_fx_converted_amount(
    session: AsyncSession, test_user, test_workspace
):
    """A EUR-denominated debt line on a USD-default group must surface
    `amount_in_default_currency` so the frontend can sum cross-currency
    balances into a single KPI without doing FX itself."""
    today = date.today()
    await _seed_eur_rate(session, today)
    account = await _make_account(session, test_user.id, currency="EUR")
    tx = await _make_tx(
        session, test_user.id, account.id, "100.00", currency="EUR", when=today
    )
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend", default_currency="USD"
    )
    me, friend = members

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=friend.id, share_amount=Decimal("100.00")
                ),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, members[0].group_id, test_workspace.id, test_user.id
    )
    assert balances is not None
    assert balances["default_currency"] == "USD"
    assert len(balances["lines"]) == 1
    line = balances["lines"][0]
    assert line["currency"] == "EUR"
    assert Decimal(str(line["amount"])) == Decimal("100.00")
    # 100 EUR / 0.85 ≈ 117.65 USD
    assert Decimal(str(line["amount_in_default_currency"])) == pytest.approx(
        Decimal("117.65"), abs=Decimal("0.5")
    )


@pytest.mark.asyncio
async def test_balance_line_default_currency_passthrough(
    session: AsyncSession, test_user, test_workspace
):
    """Same-currency line: amount_in_default_currency should equal amount
    (no FX needed). Guards against accidental rounding."""
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "60.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=friend.id, share_amount=Decimal("60.00")
                ),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    balances = await balance_service.compute_balances(
        session, members[0].group_id, test_workspace.id, test_user.id
    )

    assert balances is not None
    line = balances["lines"][0]
    assert Decimal(str(line["amount"])) == Decimal(str(line["amount_in_default_currency"]))


# ─────────────── share-only model: reports + budgets ─────────────


@pytest.mark.asyncio
async def test_owner_split_offset_pnl_helper(
    session: AsyncSession, test_user, test_workspace
):
    """The income/expenses report uses Postgres `to_char`, so we can't
    exercise it under SQLite. Instead pin its underlying helper — the
    same one the dashboard uses — directly."""
    from app.services._query_filters import owner_split_offset_pnl

    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "120.00", when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A", "B", "C"
    )

    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )
    await session.commit()

    month_start, month_end = _month_window(today)
    income_offset, expense_offset = await owner_split_offset_pnl(
        session, test_user.id, month_start, month_end, primary_currency="USD"
    )
    # Three friends each owe $30 → offset is $90; the owner's $30 share
    # is what survives in the dashboard math.
    assert income_offset == 0.0
    assert expense_offset == pytest.approx(90.0, abs=0.01)


@pytest.mark.asyncio
async def test_budget_actual_uses_share_only(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """Budget vs. actual: a category's "actual" spent should be the
    user's share, not the full amount fronted."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    food = test_categories[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(
        session,
        test_user.id,
        account.id,
        "60.00",
        category_id=food.id,
        when=today,
    )
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A", "B"
    )
    me, *friends = members
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )

    # Create a budget for the food category so it appears in the result.
    from app.models.budget import Budget

    session.add(
        Budget(
            id=uuid.uuid4(),
            user_id=test_user.id,
            category_id=food.id,
            amount=Decimal("100.00"),
            currency="USD",
            month=today.replace(day=1),
            is_recurring=True,
        )
    )
    await session.commit()

    rows = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    food_row = next(r for r in rows if r.category_id == food.id)
    # Owner's share is $20 (60/3); the full $60 would be the bug.
    assert float(food_row.actual_amount) == pytest.approx(20.0, abs=0.01)


# ──────────── share-only model: pending and future-dated rows ─────────────


def _next_month(d: date) -> date:
    return _month_window(d)[1]


async def _split_half_with_friend(session, test_user, test_workspace, tx) -> None:
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    half = (tx.amount / 2).quantize(Decimal("0.01"))
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(group_member_id=m.id, share_amount=half)
                for m in members
            ],
        ),
        test_user.id,
    )


@pytest.mark.asyncio
async def test_summary_projection_uses_owner_share_of_pending_row(
    session: AsyncSession, test_user, test_workspace
):
    """A pending $150 card purchase split three ways projects $50, the same
    as it will count once it posts."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "150.00", when=today)
    tx.status = "pending"
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "A", "B"
    )
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == pytest.approx(0.0, abs=0.01)
    assert summary.projected_expenses == pytest.approx(50.0, abs=0.01)
    assert summary.projected_expenses_primary == pytest.approx(50.0, abs=0.01)


@pytest.mark.asyncio
async def test_summary_projection_drops_pending_row_owed_in_full(
    session: AsyncSession, test_user, test_workspace
):
    """A pending row split entirely to a friend adds nothing to the
    owner's projection."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "80.00", when=today)
    tx.status = "pending"
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(group_member_id=me.id, share_amount=Decimal("0")),
                TransactionSplitInput(group_member_id=friend.id, share_amount=Decimal("80.00")),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.projected_expenses_primary == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_accrual_summary_takes_the_friend_share_off_a_card_purchase_once(
    session: AsyncSession, test_user, test_workspace, monkeypatch
):
    """In accrual mode a card purchase posted today but due later this month
    is still forecast, so the posted offset must not also take the friend's
    share off it: a 10 expense plus a 100 purchase split in half projects 60."""
    today = date(2026, 5, 10)
    monkeypatch.setattr("app.services.dashboard_service.app_today", lambda: today)
    monkeypatch.setattr("app.services._query_filters.app_today", lambda: today)
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    await _force_user_currency(session, test_user, "USD")
    account = await _make_account(session, test_user.id)
    await _make_tx(session, test_user.id, account.id, "10.00", when=today)
    purchase = await _make_tx(session, test_user.id, account.id, "100.00", when=today)
    purchase.effective_date = date(2026, 5, 25)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    await split_service.replace_splits(
        session,
        purchase,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.monthly_expenses_primary == pytest.approx(10.0, abs=0.01)
    assert summary.projected_expenses_primary == pytest.approx(60.0, abs=0.01)
    assert summary.monthly_expenses == pytest.approx(10.0, abs=0.01)
    assert summary.projected_expenses == pytest.approx(60.0, abs=0.01)


@pytest.mark.asyncio
async def test_spending_by_category_projection_uses_owner_share(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A pending categorized split projects only the owner's share into its
    category, and an account filter keeps the full amount, as it does for
    posted rows."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    food = test_categories[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(
        session, test_user.id, account.id, "50.00", category_id=food.id, when=today
    )
    tx.status = "pending"
    await _split_half_with_friend(session, test_user, test_workspace, tx)
    await session.commit()

    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    food_row = next(s for s in spending if s.category_name == "Alimentação")
    assert food_row.projected_total == pytest.approx(25.0, abs=0.01)

    filtered = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, today.replace(day=1),
        account_ids=[account.id],
    )
    food_row = next(s for s in filtered if s.category_name == "Alimentação")
    assert food_row.projected_total == pytest.approx(50.0, abs=0.01)


@pytest.mark.asyncio
async def test_budget_projection_uses_owner_share_of_future_installment(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """Next month's $300 installment, split half with a friend, projects
    $150 against next month's budget."""
    await _force_user_currency(session, test_user, "USD")
    next_month = _next_month(date.today())
    food = test_categories[0]
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(
        session,
        test_user.id,
        account.id,
        "300.00",
        category_id=food.id,
        when=next_month.replace(day=10),
    )
    await _split_half_with_friend(session, test_user, test_workspace, tx)

    from app.models.budget import Budget

    session.add(
        Budget(
            id=uuid.uuid4(),
            user_id=test_user.id,
            category_id=food.id,
            amount=Decimal("200.00"),
            currency="USD",
            month=next_month,
            is_recurring=True,
        )
    )
    await session.commit()

    rows = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, next_month
    )
    food_row = next(r for r in rows if r.category_id == food.id)
    assert float(food_row.actual_amount) == pytest.approx(0.0, abs=0.01)
    # The full $300 would put the category over its $200 budget.
    assert float(food_row.projected_amount) == pytest.approx(150.0, abs=0.01)


@pytest.mark.asyncio
async def test_monthly_trend_uses_owner_share_of_pending_row(
    session: AsyncSession, test_user, test_workspace
):
    """The current month of the trend counts a pending split at the owner's
    share."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "90.00", when=today)
    tx.status = "pending"
    await _split_half_with_friend(session, test_user, test_workspace, tx)
    await session.commit()

    # get_monthly_trend groups with Postgres' to_char; SQLite needs a stand-in.
    def _install_to_char(dbapi_conn):
        dbapi_conn.create_function(
            "to_char", 2, lambda value, _fmt: None if value is None else str(value)[:7]
        )

    raw = await session.connection()
    await raw.run_sync(lambda conn: _install_to_char(conn.connection.dbapi_connection))

    trend = await dashboard_service.get_monthly_trend(
        session, test_workspace.id, test_user.id, months=1
    )
    assert trend[-1].month == today.strftime("%Y-%m")
    assert trend[-1].expenses == pytest.approx(45.0, abs=0.01)


@pytest.mark.parametrize("my_share, friend_share", [("120.00", "-20.00"), ("-20.00", "120.00")])
@pytest.mark.asyncio
async def test_summary_projection_keeps_the_sign_of_exact_shares(
    session: AsyncSession, test_user, test_workspace, my_share, friend_share
):
    """An exact split may give a member a negative share. The projection
    follows the posted offset and counts the owner's share with its sign:
    $120 rather than $80, and -$20 rather than $0 or +$20."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    account = await _make_account(session, test_user.id)
    tx = await _make_tx(session, test_user.id, account.id, "100.00", when=today)
    tx.status = "pending"
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    await split_service.replace_splits(
        session,
        tx,
        TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(group_member_id=me.id, share_amount=Decimal(my_share)),
                TransactionSplitInput(group_member_id=friend.id, share_amount=Decimal(friend_share)),
            ],
        ),
        test_user.id,
    )
    await session.commit()

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    assert summary.projected_expenses_primary == pytest.approx(float(my_share), abs=0.01)


@pytest.mark.asyncio
async def test_negative_owner_forecast_reduces_but_never_inverts_a_category(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """A pending row where the owner's share is -$20 lowers a category with
    $50 posted to $30 projected. A category with only that row projects
    nothing instead of a negative amount, as posted spending would."""
    await _force_user_currency(session, test_user, "USD")
    today = date.today()
    food, transport = test_categories[0], test_categories[1]
    account = await _make_account(session, test_user.id)
    await _make_tx(session, test_user.id, account.id, "50.00", category_id=food.id, when=today)
    _, members = await _setup_group(
        session, test_user, test_workspace.id, "Me", "Friend"
    )
    me, friend = members
    for category in (food, transport):
        tx = await _make_tx(
            session, test_user.id, account.id, "100.00", category_id=category.id, when=today
        )
        tx.status = "pending"
        await split_service.replace_splits(
            session,
            tx,
            TransactionSplitsInput(
                share_type="exact",
                splits=[
                    TransactionSplitInput(group_member_id=me.id, share_amount=Decimal("-20.00")),
                    TransactionSplitInput(group_member_id=friend.id, share_amount=Decimal("120.00")),
                ],
            ),
            test_user.id,
        )

    from app.models.budget import Budget

    for category in (food, transport):
        session.add(
            Budget(
                id=uuid.uuid4(),
                user_id=test_user.id,
                category_id=category.id,
                amount=Decimal("100.00"),
                currency="USD",
                month=today.replace(day=1),
                is_recurring=True,
            )
        )
    await session.commit()

    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    by_name = {s.category_name: s for s in spending}
    assert by_name["Alimentação"].total == pytest.approx(50.0, abs=0.01)
    assert by_name["Alimentação"].projected_total == pytest.approx(30.0, abs=0.01)
    assert "Transporte" not in by_name

    rows = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, today.replace(day=1)
    )
    by_id = {r.category_id: r for r in rows}
    assert float(by_id[food.id].projected_amount) == pytest.approx(30.0, abs=0.01)
    assert float(by_id[transport.id].projected_amount) == pytest.approx(0.0, abs=0.01)
