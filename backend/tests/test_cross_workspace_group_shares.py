"""Issue #1305: the cross-workspace share projection must be symmetric.

An invited member already sees both ends of a split expense: their own
parent row and the owner's. The group owner must see the invited member's
split in the default statement scope too, without ever getting their own
transactions re-projected as "shared" after switching workspaces.

Issue #242: the report's per-category breakdowns must count that share as
the summary already does.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.services import (
    dashboard_service,
    group_service,
    report_service,
    split_service,
    workspace_service,
)
from app.services._query_filters import viewer_shared_pnl
from app.services.search_service import search_all
from app.services.transaction_service import get_transactions


async def _make_user_with_workspace(
    session: AsyncSession, email: str
) -> tuple[User, "object"]:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    workspace = await workspace_service.create_personal_workspace_for_user(
        session, user, commit=True
    )
    return user, workspace


async def _make_category(session, user, workspace, name: str, color: str) -> Category:
    category = Category(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        icon="circle-help",
        color=color,
    )
    session.add(category)
    await session.flush()
    return category


async def _record_split_expense(
    session, payer, workspace, members, amount: str, description: str,
    category: Category | None = None, txn_type: str = "debit",
    amount_primary: str | None = None,
    splits: TransactionSplitsInput | None = None,
) -> Transaction:
    """`payer` pays `amount` from an account in `workspace`, split equally
    unless `splits` says otherwise."""
    account = Account(
        id=uuid.uuid4(),
        user_id=payer.id,
        workspace_id=workspace.id,
        name="Wallet",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=payer.id,
        workspace_id=workspace.id,
        account_id=account.id,
        category_id=category.id if category else None,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        amount_primary=Decimal(amount_primary) if amount_primary else None,
        date=date.today(),
        type=txn_type,
        source="manual",
    )
    session.add(tx)
    await session.flush()
    await split_service.replace_splits(
        session,
        tx,
        splits
        or TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
        ),
        payer.id,
    )
    await session.commit()
    return tx


async def _trip_group(session, test_user, test_workspace, *, linked_self: bool):
    """A group owned by `test_user` with their self-member plus an invited
    member linked to a fresh user in another workspace."""
    guest, guest_ws = await _make_user_with_workspace(
        session, f"guest-{uuid.uuid4().hex[:8]}@example.com"
    )
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Trip")
    )
    me = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(
            name="Me",
            is_self=True,
            email=test_user.email if linked_self else None,
        ),
    )
    friend = await group_service.create_member(
        session,
        group.id,
        test_workspace.id,
        GroupMemberCreate(name="Guest", email=guest.email),
    )
    assert me is not None and friend is not None
    assert friend.linked_user_id == guest.id
    assert (me.linked_user_id == test_user.id) is linked_self
    return group, me, friend, guest, guest_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_self", [False, True])
async def test_owner_sees_invited_members_shared_expense(
    session: AsyncSession, test_user, test_workspace, linked_self
):
    """The invited member's expense lands in the owner's default list as a
    shared row carrying the owner's share, the case that was asymmetric."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=linked_self
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    rows, _, _ = await get_transactions(session, test_workspace.id, test_user.id)

    shared = [row for row in rows if row.description == "Groceries"]
    assert len(shared) == 1
    row = shared[0]
    assert row.is_shared is True
    assert row.viewer_share == Decimal("50.00")
    assert row.group_id == group.id
    # The expense's author is the invited member, not the group owner.
    assert row.parent_owner_name == "Guest"

    hits = await search_all(session, test_workspace.id, test_user.id, "Groceries")
    assert any(
        hit["type"] == "transaction" and hit["label"] == "Groceries" for hit in hits
    ), hits


@pytest.mark.asyncio
async def test_owner_own_split_expense_is_not_reprojected_in_another_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """The owner keeps seeing their own expense once, where it lives. The
    dedup guard is the row's author, not the `is_self` flag of their member."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )
    await _record_split_expense(
        session, test_user, test_workspace, [me, friend], "40.00", "Owner dinner"
    )

    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )

    rows, _, _ = await get_transactions(session, other_ws.id, test_user.id)
    descriptions = [row.description for row in rows]
    assert "Owner dinner" not in descriptions
    groceries = [row for row in rows if row.description == "Groceries"]
    assert len(groceries) == 1
    assert groceries[0].is_shared is True


@pytest.mark.asyncio
async def test_owner_does_not_see_a_workspace_row_as_shared(
    session: AsyncSession, test_user, test_workspace
):
    """A linked self-member must not turn a coworker's row in the owner's
    own workspace into a locked "shared" row: only the other workspace's
    share is projected to the owner."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=True
    )
    await _record_split_expense(
        session, guest, test_workspace, [me, friend], "60.00", "Team lunch"
    )

    rows, _, _ = await get_transactions(session, test_workspace.id, test_user.id)
    row = next(r for r in rows if r.description == "Team lunch")
    assert row.is_shared is False


@pytest.mark.asyncio
async def test_owner_group_stays_reachable_from_their_other_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """The projected row links to its group; the owner must be able to open
    the group from any workspace they belong to, like an invited member."""
    group, _me, _friend, _guest, _guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )

    listed = await group_service.list_groups(session, other_ws.id, test_user.id)
    assert group.id in {g.id for g in listed}
    visible = await group_service.get_group_visible(
        session, group.id, other_ws.id, test_user.id
    )
    assert visible is not None


@pytest.mark.asyncio
async def test_owner_cannot_edit_or_delete_the_invited_members_row(
    session: AsyncSession,
    client: AsyncClient,
    auth_headers,
    test_user,
    test_workspace,
):
    """The projected row is read-only for the owner. The API resolves a
    transaction inside the workspace that owns it, so edit and delete from
    a workspace where the row only shows up as a share answer 404."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    guest_tx = await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )
    other_ws = await workspace_service.create_workspace(
        session, name="Second", creator=test_user, self_membership=True
    )
    headers = {**auth_headers, "X-Workspace-Id": str(other_ws.id)}

    rows, _, _ = await get_transactions(session, other_ws.id, test_user.id)
    assert any(row.id == guest_tx.id and row.is_shared for row in rows)

    patched = await client.patch(
        f"/api/transactions/{guest_tx.id}",
        json={"description": "Mine now"},
        headers=headers,
    )
    assert patched.status_code == 404, patched.text

    deleted = await client.delete(
        f"/api/transactions/{guest_tx.id}", headers=headers
    )
    assert deleted.status_code == 404, deleted.text


@pytest.mark.asyncio
async def test_reports_count_the_owners_share_of_an_invited_members_expense(
    session: AsyncSession, test_user, test_workspace
):
    """The dashboard/report reader of the same projection must count the
    owner's share of the invited member's expense, not only the invitee's."""
    group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    income, expense = await viewer_shared_pnl(
        session, test_user.id, month_start, month_end
    )
    assert income == pytest.approx(0.0)
    assert expense == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_a_negative_exact_share_on_an_unstamped_row_lowers_the_total(
    session: AsyncSession, test_user, test_workspace
):
    """An exact split may give the owner a negative share. On a row without
    `amount_primary` it must lower the total read in the owner's currency,
    as it does on a stamped row and in the nominal sum, not add to it."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "200.00", "Hotel",
        amount_primary="200.00",
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "40.00", "Refunded taxi",
        splits=TransactionSplitsInput(
            share_type="exact",
            splits=[
                TransactionSplitInput(
                    group_member_id=me.id, share_amount=Decimal("-20.00")
                ),
                TransactionSplitInput(
                    group_member_id=friend.id, share_amount=Decimal("60.00")
                ),
            ],
        ),
    )

    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    _income, expense = await viewer_shared_pnl(
        session, test_user.id, month_start, month_end, primary_currency="BRL"
    )
    assert expense == pytest.approx(80.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_self", [False, True])
async def test_dashboard_counts_a_third_party_row_in_the_owners_workspace_once(
    session: AsyncSession, test_user, test_workspace, linked_self
):
    """A member's expense in the owner's workspace is already in the
    workspace total, whole. Projecting the owner's self-member share of
    that same row would count it twice, so the projection must leave the
    current workspace out while still adding the invited member's row
    from theirs."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=linked_self
    )
    # 100 paid by the invited member inside the owner's workspace, split
    # with the owner's self-member: someone other than the owner paid, but
    # the row already sits in the dashboard base total, whole.
    await _record_split_expense(
        session, guest, test_workspace, [me, friend], "100.00", "Team lunch"
    )
    # 100 paid by the invited member in their own workspace: the owner's
    # 50 share is not in the base total and must still be projected.
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries"
    )

    today = date.today()
    month = today.replace(day=1)

    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=month
    )
    assert summary.monthly_expenses == pytest.approx(150.0)

    by_category = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, month=month
    )
    assert sum(item.total for item in by_category) == pytest.approx(150.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_self", [False, True])
async def test_report_category_totals_count_the_owners_shared_share(
    session: AsyncSession, test_user, test_workspace, linked_self
):
    """The report summary already counted the owner's share of the invited
    member's expense; the composition and the per-category trend were still
    workspace-only (#242). A row inside the owner's workspace stays whole,
    and the share of a row in the member's workspace is added."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=linked_self
    )
    lunch = await _make_category(session, guest, test_workspace, "Lunch", "#F59E0B")
    groceries = await _make_category(
        session, guest, guest_ws, "Groceries", "#22C55E"
    )
    await _record_split_expense(
        session, guest, test_workspace, [me, friend], "100.00", "Team lunch",
        category=lunch,
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries",
        category=groceries,
    )

    report = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1
    )

    expenses = next(b for b in report.summary.breakdowns if b.key == "expenses")
    assert expenses.value == pytest.approx(150.0)

    composition = {
        item.label: item.value
        for item in report.composition
        if item.group == "expenses"
    }
    assert composition["Lunch"] == pytest.approx(100.0)
    assert composition["Groceries"] == pytest.approx(50.0)

    trend = {
        item.label: item
        for item in report.category_trend
        if item.group == "expenses"
    }
    assert trend["Groceries"].total == pytest.approx(50.0)
    current_point = report.trend[-1]
    current_series = next(
        point for point in trend["Groceries"].series if point.date == current_point.date
    )
    assert current_series.value == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_report_views_skip_a_shared_share_of_unknown_type(
    session: AsyncSession, test_user, test_workspace
):
    """Only debit and credit are spending and income, in every view.

    The summary's shared-splits query sums just those two types, so the
    owner's share of a row saved as "expense" never reached it. The
    composition, the category trend and the trend point read every
    non-credit share as expense, so the same share showed up there alone
    and the views disagreed (#345)."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    groceries = await _make_category(
        session, guest, guest_ws, "Groceries", "#22C55E"
    )
    odd = await _make_category(session, guest, guest_ws, "Odd", "#EF4444")
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries",
        category=groceries,
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "80.00", "Odd purchase",
        category=odd, txn_type="expense",
    )

    report = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1
    )

    expenses = next(b for b in report.summary.breakdowns if b.key == "expenses")
    assert expenses.value == pytest.approx(50.0)

    composition = {
        item.label: item.value
        for item in report.composition
        if item.group == "expenses"
    }
    assert composition == {"Groceries": pytest.approx(50.0)}

    trend = {
        item.label: item
        for item in report.category_trend
        if item.group == "expenses"
    }
    assert set(trend) == {"Groceries"}
    assert trend["Groceries"].total == pytest.approx(50.0)

    current_point = report.trend[-1]
    assert current_point.breakdowns["expenses"] == pytest.approx(50.0)
    point_composition = {
        item.label: item.value
        for item in current_point.composition
        if item.group == "expenses"
    }
    assert point_composition == {"Groceries": pytest.approx(50.0)}


@pytest.mark.asyncio
async def test_report_for_the_invited_member_counts_their_own_share(
    session: AsyncSession, test_user, test_workspace
):
    """From the invited member's workspace their expense counts once: the
    whole row minus the owner's share, with no projection of their own row."""
    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    guest.preferences = {"currency_display": "BRL"}
    await session.commit()
    groceries = await _make_category(
        session, guest, guest_ws, "Groceries", "#22C55E"
    )
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Groceries",
        category=groceries,
    )

    report = await report_service.get_income_expenses_report(
        session, guest_ws.id, guest.id, months=1
    )

    expenses = next(b for b in report.summary.breakdowns if b.key == "expenses")
    assert expenses.value == pytest.approx(50.0)

    composition = {
        item.label: item.value
        for item in report.composition
        if item.group == "expenses"
    }
    assert composition["Groceries"] == pytest.approx(50.0)

    trend = {
        item.label: item
        for item in report.category_trend
        if item.group == "expenses"
    }
    assert trend["Groceries"].total == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_cross_workspace_equivalent_categories_merge_across_views(
    session: AsyncSession, test_user, test_workspace
):
    """Equivalent categories across workspaces (same name or localized default
    variants like 'Alimentação' <-> 'Food & Dining') map onto the viewer's
    workspace category across Dashboard, Reports, Budgets, Category Statement,
    and Transactions filtering (#1309)."""
    from app.models.budget import Budget
    from app.services import budget_service, category_statement_service, transaction_service

    _group, me, friend, guest, guest_ws = await _trip_group(
        session, test_user, test_workspace, linked_self=False
    )
    viewer_food = await _make_category(
        session, test_user, test_workspace, "Food & Dining", "#F97316"
    )
    guest_food_pt = await _make_category(
        session, guest, guest_ws, "Alimentação", "#10B981"
    )
    guest_only = await _make_category(
        session, guest, guest_ws, "Guest-Only Tag", "#6366F1"
    )

    today = date.today()
    month = today.replace(day=1)
    month_end = (month + timedelta(days=32)).replace(day=1)

    session.add(
        Budget(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            category_id=viewer_food.id,
            amount=Decimal("200.00"),
            currency="BRL",
            month=month,
        )
    )
    await session.flush()

    # Viewer's own expense in "Food & Dining": 40
    await _record_split_expense(
        session, test_user, test_workspace, [me, friend], "80.00", "Own dinner",
        category=viewer_food,
    )
    # Guest's expense in "Alimentação" (maps to viewer's "Food & Dining"): viewer share 50
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "100.00", "Shared lunch",
        category=guest_food_pt,
    )
    # Guest's expense in unmapped "Guest-Only Tag": viewer share 30
    await _record_split_expense(
        session, guest, guest_ws, [me, friend], "60.00", "Shared misc",
        category=guest_only,
    )

    # 1. Dashboard spending-by-category merges mapped categories into viewer_food.id
    #    while preserving unmapped foreign categories so drill-downs match Reports.
    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, month=month
    )
    by_id = {item.category_id: item for item in spending}
    assert str(viewer_food.id) in by_id
    assert by_id[str(viewer_food.id)].total == pytest.approx(90.0)
    assert str(guest_food_pt.id) not in by_id
    assert str(guest_only.id) in by_id
    assert by_id[str(guest_only.id)].category_name == "Guest-Only Tag"
    assert by_id[str(guest_only.id)].total == pytest.approx(30.0)

    # 2. Reports merge "Alimentação" into viewer's "Food & Dining" slice & trend
    report = await report_service.get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1
    )
    comp = {item.label: item.value for item in report.composition if item.group == "expenses"}
    assert comp["Food & Dining"] == pytest.approx(90.0)
    assert "Alimentação" not in comp
    assert comp["Guest-Only Tag"] == pytest.approx(30.0)

    # 3. Budgets include the mapped cross-workspace share under viewer_food
    bva = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=month
    )
    food_bva = next(row for row in bva if row.category_id == viewer_food.id)
    assert food_bva.actual_amount == pytest.approx(90.0)

    # 3b. Budget suggestions count months_with_data for mapped shared-only categories
    viewer_transport = await _make_category(
        session, test_user, test_workspace, "Transport", "#0EA5E9"
    )
    guest_transport_pt = await _make_category(
        session, guest, guest_ws, "Transporte", "#38BDF8"
    )
    prev_month = (month - timedelta(days=1)).replace(day=15)
    prev_tx = await _record_split_expense(
        session, guest, guest_ws, [me, friend], "120.00", "Shared taxi",
        category=guest_transport_pt,
    )
    prev_tx.date = prev_month
    await session.flush()

    suggestions = await budget_service.get_budget_suggestions(
        session, test_workspace.id, test_user.id, month=month, months=1
    )
    transport_sugg = next(
        s for s in suggestions.suggestions if s.category_id == viewer_transport.id
    )
    assert transport_sugg.total_spent == Decimal("60.00")
    assert transport_sugg.months_with_data == 1

    # 4. Category statement attributes mapped share to viewer_food, unmapped to uncategorized
    stmt = await category_statement_service.get_category_statement(
        session, test_workspace.id, test_user.id, month=month, months=1
    )
    all_rows = [
        child for r in stmt.expenses.rows for child in ([r] + list(r.children))
    ]
    food_row = next(r for r in all_rows if r.key == str(viewer_food.id))
    assert food_row.values[-1] == pytest.approx(-90.0)
    uncat_row = next(r for r in stmt.expenses.rows if r.kind == "uncategorized")
    assert uncat_row.values[-1] == pytest.approx(-30.0)

    # 5. Transactions filtered by viewer_food.id returns both own and mapped shared row with viewer category
    txs, total, _ = await transaction_service.get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=month,
        to_date=month_end - timedelta(days=1),
        category_id=viewer_food.id,
    )
    assert total == 2
    shared_tx = next(t for t in txs if t.description == "Shared lunch")
    assert shared_tx.category_id == viewer_food.id
    assert shared_tx.category is not None and shared_tx.category.id == viewer_food.id

    # 5b. Drilling down on an unmapped foreign category slice returns the shared row and matching summary
    foreign_txs, foreign_total, foreign_summary = await transaction_service.get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=month,
        to_date=month_end - timedelta(days=1),
        category_id=guest_only.id,
        include_summary=True,
    )
    assert foreign_total == 1
    assert foreign_txs[0].description == "Shared misc"
    assert foreign_txs[0].category_id == guest_only.id
    assert foreign_summary is not None
    assert float(foreign_summary["expense"]) == pytest.approx(30.0)

    # 6. shared_as_uncategorized drill-down returns only the unmapped shared row (not "Shared lunch")
    uncat_txs, uncat_total, _ = await transaction_service.get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=month,
        to_date=month_end - timedelta(days=1),
        uncategorized=True,
        shared_as_uncategorized=True,
    )
    assert uncat_total == 1
    assert uncat_txs[0].description == "Shared misc"
    assert uncat_txs[0].category_id == guest_only.id
