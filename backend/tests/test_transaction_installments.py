"""Tests for manual installment transactions.

Covers the series creation helper, the single-installment create path, and
the this/future/all scoped update + delete behavior.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.transaction import (
    InstallmentSeriesCreate,
    TransactionCreate,
    TransactionUpdate,
)
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.services import group_service
from app.services.transaction_service import (
    create_installment_series,
    create_transaction,
    delete_transaction,
    get_transaction,
    update_transaction,
)


@pytest_asyncio.fixture
async def installment_account(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Installment Acc",
        type="checking",
        balance=Decimal("10000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


def _series_payload(account: Account, **overrides) -> InstallmentSeriesCreate:
    base = {
        "description": "Notebook",
        "amount": Decimal("100.00"),
        "date": date(2026, 1, 15),
        "type": "debit",
        "account_id": account.id,
    }
    base.update(overrides.pop("base", {}))
    return InstallmentSeriesCreate(base=base, **overrides)


def _transaction(account: Account, **overrides) -> Transaction:
    data = dict(
        id=uuid.uuid4(),
        user_id=account.user_id,
        account_id=account.id,
        description="Parcela",
        amount=Decimal("100.00"),
        date=date(2026, 1, 15),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    data.update(overrides)
    return Transaction(**data)


# ---------------------------------------------------------------------------
# create_installment_series — helper behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_installment_series_monthly_defaults(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(installment_account, installments=3),
    )

    assert len(created) == 3
    numbers = [t.installment_number for t in created]
    assert numbers == [1, 2, 3]
    assert all(t.installment_series_id is not None for t in created)
    assert len({t.installment_series_id for t in created}) == 1
    for tx in created:
        assert tx.total_installments == 3
        assert tx.installment_total_amount == Decimal("300.00")
        assert tx.installment_purchase_date == date(2026, 1, 15)
        assert tx.account_id == installment_account.id
        assert tx.description == "Notebook"
        assert tx.source == "manual"

    # Default total = amount * n, so equal split keeps per-parcel == typed amount.
    assert [t.amount for t in created] == [
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    ]
    # First installment posted, later ones pending.
    assert created[0].status == "posted"
    assert created[1].status == "pending"
    assert created[2].status == "pending"
    # Dates advance monthly.
    assert [t.date for t in created] == [
        date(2026, 1, 15),
        date(2026, 2, 15),
        date(2026, 3, 15),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_repeats_amount_for_every_parcel(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    # "Repeat as installments": no total is split — every parcel stores the
    # base amount as-is, even for a large count where rounding a split total
    # could otherwise produce a negative or zero last parcel.
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account,
            installments=360,
            base={"amount": Decimal("100.00")},
        ),
    )

    assert len(created) == 360
    assert all(t.amount == Decimal("100.00") for t in created)
    assert sum(t.amount for t in created) == Decimal("36000.00")
    assert all(t.installment_total_amount == Decimal("36000.00") for t in created)


@pytest.mark.asyncio
async def test_create_installment_series_repeats_amount_primary_per_parcel(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    # FX override rides along on the series base: every parcel stores the
    # base amount_primary as-is and derives the same rate, matching the
    # single-transaction create path.
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account,
            installments=3,
            base={"amount_primary": Decimal("200.00")},
        ),
    )

    assert len(created) == 3
    for tx in created:
        assert tx.amount == Decimal("100.00")
        assert tx.amount_primary == Decimal("200.00")
        assert tx.fx_rate_used == Decimal("2")
    # Identical across parcels, not re-derived proportionally per parcel.
    assert len({t.fx_rate_used for t in created}) == 1


@pytest.mark.asyncio
async def test_create_installment_series_weekly(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(installment_account, installments=3, frequency="weekly"),
    )
    assert [t.date for t in created] == [
        date(2026, 1, 15),
        date(2026, 1, 22),
        date(2026, 1, 29),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_biweekly(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(installment_account, installments=3, frequency="biweekly"),
    )
    assert [transaction.date for transaction in created] == [
        date(2026, 1, 15),
        date(2026, 1, 29),
        date(2026, 2, 12),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_yearly(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(installment_account, installments=2, frequency="yearly"),
    )
    assert [t.date for t in created] == [date(2026, 1, 15), date(2027, 1, 15)]


@pytest.mark.asyncio
async def test_create_installment_series_quarterly(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(installment_account, installments=4, frequency="quarterly"),
    )
    # Matches the recurring-transaction cadence so "repeat as installments"
    # offers the same frequencies as a recurring bill.
    assert [t.date for t in created] == [
        date(2026, 1, 15),
        date(2026, 4, 15),
        date(2026, 7, 15),
        date(2026, 10, 15),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_month_end_clamping(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account,
            base={"date": date(2026, 1, 31)},
            installments=3,
        ),
    )
    # Jan 31 -> Feb 28 (clamped) -> Mar 31 (recovers to intended day).
    assert [t.date for t in created] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_semiannual_month_end(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account,
            base={"date": date(2024, 8, 31)},
            installments=3,
            frequency="semiannual",
        ),
    )
    assert [transaction.date for transaction in created] == [
        date(2024, 8, 31),
        date(2025, 2, 28),
        date(2025, 8, 31),
    ]


@pytest.mark.asyncio
async def test_create_installment_series_first_status_pending(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account, installments=2, first_installment_status="pending"
        ),
    )
    assert created[0].status == "pending"
    assert created[1].status == "pending"


@pytest.mark.asyncio
async def test_create_installment_series_applies_splits_per_parcel(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    # Split-with-group rides along on the series base; every parcel must be
    # split the same way the single-transaction path does.
    group = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Raid")
    )
    member_a = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="A")
    )
    member_b = await group_service.create_member(
        session, group.id, test_workspace.id, GroupMemberCreate(name="B")
    )
    assert member_a is not None and member_b is not None
    members = [member_a, member_b]

    created = await create_installment_series(
        session,
        test_workspace.id,
        test_user.id,
        _series_payload(
            installment_account,
            installments=2,
            base={
                "splits": TransactionSplitsInput(
                    share_type="equal",
                    splits=[TransactionSplitInput(group_member_id=m.id) for m in members],
                )
            },
        ),
    )
    assert len(created) == 2
    for tx in created:
        rows = (
            await session.execute(
                select(TransactionSplit).where(TransactionSplit.transaction_id == tx.id)
            )
        ).scalars().all()
        assert len(rows) == 2
        by_member = {r.group_member_id: r.share_amount for r in rows}
        # base.amount is the per-parcel amount (100.00); split equally into
        # two members = 50.00 each, matching the single-transaction path.
        assert by_member[members[0].id] == Decimal("50.00")
        assert by_member[members[1].id] == Decimal("50.00")


@pytest.mark.asyncio
async def test_create_installment_series_invalid_account(
    session: AsyncSession, test_user, test_workspace
):
    with pytest.raises(ValueError, match="Account not found"):
        await create_installment_series(
            session,
            test_workspace.id,
            test_user.id,
            InstallmentSeriesCreate(
                base=TransactionCreate(
                    description="Orphan",
                    amount=Decimal("10"),
                    date=date(2026, 1, 15),
                    type="debit",
                    account_id=uuid.uuid4(),
                ),
                installments=2,
            ),
        )


# ---------------------------------------------------------------------------
# Single installment create via the regular endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_single_installment_persists_fingerprint(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    txn = await create_transaction(
        session,
        test_workspace.id,
        test_user.id,
        TransactionCreate(
            description="Parcela avulsa",
            amount=Decimal("50.00"),
            date=date(2026, 2, 10),
            type="debit",
            account_id=installment_account.id,
            installment_number=3,
            total_installments=6,
            installment_total_amount=Decimal("300.00"),
            installment_purchase_date=date(2025, 12, 10),
        ),
    )
    assert txn.installment_number == 3
    assert txn.total_installments == 6
    assert txn.installment_total_amount == Decimal("300.00")
    assert txn.installment_purchase_date == date(2025, 12, 10)


# ---------------------------------------------------------------------------
# update_transaction — installment scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_installment_this_only(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    await update_transaction(
        session, series[1].id, test_workspace.id, test_user.id,
        TransactionUpdate(description="Only this one", apply_to="this"),
    )
    untouched = await get_transaction(session, series[0].id, test_workspace.id)
    edited = await get_transaction(session, series[1].id, test_workspace.id)
    later = await get_transaction(session, series[2].id, test_workspace.id)
    assert untouched is not None and edited is not None and later is not None
    assert untouched.description == "Notebook"
    assert edited.description == "Only this one"
    assert later.description == "Notebook"


@pytest.mark.asyncio
async def test_update_installment_future(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    await update_transaction(
        session, series[1].id, test_workspace.id, test_user.id,
        TransactionUpdate(description="Future ones", apply_to="future"),
    )
    untouched = await get_transaction(session, series[0].id, test_workspace.id)
    edited = await get_transaction(session, series[1].id, test_workspace.id)
    later = await get_transaction(session, series[2].id, test_workspace.id)
    assert untouched is not None and edited is not None and later is not None
    assert untouched.description == "Notebook"
    assert edited.description == "Future ones"
    assert later.description == "Future ones"


@pytest.mark.asyncio
async def test_update_installment_all(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    await update_transaction(
        session, series[2].id, test_workspace.id, test_user.id,
        TransactionUpdate(description="All parcels", apply_to="all"),
    )
    for tx in series:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.description == "All parcels"


@pytest.mark.asyncio
async def test_update_installment_amount_all_resyncs_series_total(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Repricing the whole series updates the stored total on every parcel."""
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    assert all(t.installment_total_amount == Decimal("300.00") for t in series)

    await update_transaction(
        session, series[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(amount=Decimal("150.00"), apply_to="all"),
    )

    for tx in series:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.amount == Decimal("150.00")
        assert reloaded.installment_total_amount == Decimal("450.00")


@pytest.mark.asyncio
async def test_update_installment_amount_this_only_resyncs_series_total(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Repricing a single parcel still changes what the series is worth."""
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )

    await update_transaction(
        session, series[1].id, test_workspace.id, test_user.id,
        TransactionUpdate(amount=Decimal("200.00")),
    )

    amounts = []
    for tx in series:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        amounts.append(reloaded.amount)
        # 100 + 200 + 100
        assert reloaded.installment_total_amount == Decimal("400.00")
    assert amounts == [Decimal("100.00"), Decimal("200.00"), Decimal("100.00")]


@pytest.mark.asyncio
async def test_update_installment_amount_leaves_synced_total_alone(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Provider-synced rows have no series id: their total is the bank's
    figure and must not be recomputed from our side."""
    rows = [
        _transaction(
            installment_account,
            workspace_id=test_workspace.id,
            source="sync",
            installment_number=i,
            total_installments=2,
            installment_total_amount=Decimal("200.00"),
            installment_purchase_date=date(2026, 1, 15),
        )
        for i in (1, 2)
    ]
    session.add_all(rows)
    await session.commit()

    await update_transaction(
        session, rows[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(amount=Decimal("175.00")),
    )

    for tx in rows:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.installment_total_amount == Decimal("200.00")


@pytest.mark.asyncio
async def test_update_installment_scope_repeats_only_whitelist_fields(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Scoped edits only repeat the whitelisted fields (description, amount,
    currency, category, type, payee, account, notes) to sibling installments.
    Date and status changes land on the edited row only; siblings keep their
    own dates and statuses."""
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    # Parcel 2 starts as: Feb 15, 100.00, pending (i > 1).
    await update_transaction(
        session, series[1].id, test_workspace.id, test_user.id,
        TransactionUpdate(
            description="Renamed",
            amount=Decimal("250.00"),
            date=date(2026, 4, 10),
            status="posted",
            apply_to="future",
        ),
    )
    # The edited row reflects the full payload, including its own date/status.
    anchor = await get_transaction(session, series[1].id, test_workspace.id)
    assert anchor is not None
    assert anchor.description == "Renamed"
    assert anchor.amount == Decimal("250.00")
    assert anchor.date == date(2026, 4, 10)
    assert anchor.status == "posted"
    # Sibling gets the whitelisted fields but keeps its own date and status.
    sibling = await get_transaction(session, series[2].id, test_workspace.id)
    assert sibling is not None
    assert sibling.description == "Renamed"
    assert sibling.amount == Decimal("250.00")
    assert sibling.date == date(2026, 3, 15)
    assert sibling.status == "pending"
    # Earlier parcel is outside the "future" scope entirely.
    earlier = await get_transaction(session, series[0].id, test_workspace.id)
    assert earlier is not None
    assert earlier.description == "Notebook"
    assert earlier.amount == Decimal("100.00")
    assert earlier.date == date(2026, 1, 15)


@pytest.mark.asyncio
async def test_update_installment_scope_moves_account_for_whole_series(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Scoped edits apply an account move to every row of the series so the
    whole series lands on the new account together."""
    other = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Other Acc",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(other)
    await session.commit()

    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=2),
    )
    await update_transaction(
        session, series[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(account_id=other.id, apply_to="all"),
    )
    for tx in series:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.account_id == other.id


@pytest.mark.asyncio
async def test_update_installment_scope_ignored_for_non_installment(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    txn = _transaction(installment_account)
    session.add(txn)
    await session.commit()

    await update_transaction(
        session, txn.id, test_workspace.id, test_user.id,
        TransactionUpdate(description="Scoped but plain", apply_to="all"),
    )
    plain = await get_transaction(session, txn.id, test_workspace.id)
    assert plain is not None
    assert plain.description == "Scoped but plain"


@pytest.mark.asyncio
async def test_update_installment_scope_is_account_scoped(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Two series with identical purchase date/count on different accounts
    must NOT be treated as the same series."""
    other = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Other Acc",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(other)
    await session.commit()

    series_a = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=2),
    )
    series_b = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(other, installments=2),
    )

    await update_transaction(
        session, series_a[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(description="Account A only", apply_to="all"),
    )
    a_later = await get_transaction(session, series_a[1].id, test_workspace.id)
    b_first = await get_transaction(session, series_b[0].id, test_workspace.id)
    assert a_later is not None and b_first is not None
    assert a_later.description == "Account A only"
    assert b_first.description == "Notebook"


@pytest.mark.asyncio
async def test_update_installment_scope_uses_series_id_for_same_account_and_date(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    """Two same-day purchases on one account must remain separate series."""
    series_a = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    series_b = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )

    assert series_a[0].installment_series_id != series_b[0].installment_series_id

    await update_transaction(
        session, series_a[0].id, test_workspace.id, test_user.id,
        TransactionUpdate(description="Series A only", apply_to="all"),
    )

    for tx in series_a:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.description == "Series A only"
    for tx in series_b:
        reloaded = await get_transaction(session, tx.id, test_workspace.id)
        assert reloaded is not None
        assert reloaded.description == "Notebook"


# ---------------------------------------------------------------------------
# delete_transaction — installment scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_installment_this_only(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    assert await delete_transaction(session, series[1].id, test_workspace.id) is True
    assert await get_transaction(session, series[0].id, test_workspace.id) is not None
    assert await get_transaction(session, series[1].id, test_workspace.id) is None
    assert await get_transaction(session, series[2].id, test_workspace.id) is not None


@pytest.mark.asyncio
async def test_delete_installment_future(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    assert await delete_transaction(session, series[1].id, test_workspace.id, apply_to="future") is True
    assert await get_transaction(session, series[0].id, test_workspace.id) is not None
    assert await get_transaction(session, series[1].id, test_workspace.id) is None
    assert await get_transaction(session, series[2].id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_installment_all(
    session: AsyncSession, test_user, test_workspace, installment_account
):
    series = await create_installment_series(
        session, test_workspace.id, test_user.id,
        _series_payload(installment_account, installments=3),
    )
    assert await delete_transaction(session, series[0].id, test_workspace.id, apply_to="all") is True
    for tx in series:
        assert await get_transaction(session, tx.id, test_workspace.id) is None


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_create_installments(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Cadeira gamer",
                "amount": "150.00",
                "date": "2026-03-05",
                "type": "debit",
            },
            "installments": 3,
            "first_installment_status": "posted",
            "frequency": "monthly",
        },
    )
    assert response.status_code == 201
    items = response.json()
    assert len(items) == 3
    series_ids = {item["installment_series_id"] for item in items}
    assert len(series_ids) == 1
    assert next(iter(series_ids)) is not None
    for i, item in enumerate(items, start=1):
        assert item["installment_number"] == i
        assert item["total_installments"] == 3
        assert item["installment_total_amount"] == 450.00
        assert item["installment_purchase_date"] == "2026-03-05"
        assert item["source"] == "manual"
    assert items[0]["status"] == "posted"
    assert items[1]["status"] == "pending"
    assert [it["date"] for it in items] == ["2026-03-05", "2026-04-05", "2026-05-05"]


@pytest.mark.asyncio
async def test_api_create_installments_validation(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Single",
                "amount": "150.00",
                "date": "2026-03-05",
                "type": "debit",
            },
            "installments": 1,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_patch_installments_scope_all(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Sofa",
                "amount": "100.00",
                "date": "2026-03-05",
                "type": "debit",
            },
            "installments": 2,
        },
    )
    assert response.status_code == 201
    items = response.json()
    second = items[1]["id"]

    response = await client.patch(
        f"/api/transactions/{second}",
        headers=auth_headers,
        json={"description": "Sofa novo", "apply_to": "all"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Sofa novo"

    listing = await client.get("/api/transactions", headers=auth_headers)
    descs = {t["description"] for t in listing.json()["items"]}
    assert "Sofa novo" in descs
    assert "Sofa" not in descs


@pytest.mark.asyncio
async def test_api_delete_installments_scope_all(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Delete me",
                "amount": "50.00",
                "date": "2026-03-05",
                "type": "debit",
            },
            "installments": 3,
        },
    )
    items = response.json()
    target = items[1]["id"]

    response = await client.delete(
        f"/api/transactions/{target}",
        headers=auth_headers,
        params={"apply_to": "all"},
    )
    assert response.status_code == 204

    listing = await client.get("/api/transactions", headers=auth_headers)
    assert all(t["description"] != "Delete me" for t in listing.json()["items"])


@pytest.mark.asyncio
async def test_api_delete_installments_scope_this(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "base": {
                "account_id": str(test_account.id),
                "description": "Keep others",
                "amount": "50.00",
                "date": "2026-03-05",
                "type": "debit",
            },
            "installments": 3,
        },
    )
    items = response.json()
    target = items[1]["id"]

    response = await client.delete(
        f"/api/transactions/{target}", headers=auth_headers
    )
    assert response.status_code == 204

    listing = await client.get("/api/transactions", headers=auth_headers)
    remaining = [t for t in listing.json()["items"] if t["description"] == "Keep others"]
    assert len(remaining) == 2
