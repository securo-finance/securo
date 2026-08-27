import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payee import Payee, PayeeMapping
from app.models.transaction import Transaction
from app.models.account import Account
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.payee import PayeeCreate, PayeeUpdate
from app.services.payee_service import (
    create_payee,
    delete_payee,
    get_or_create_payee,
    get_payee,
    get_payees,
    get_payee_summary,
    merge_payees,
    update_payee,
    bulk_delete_payees,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_account(session: AsyncSession, user: User) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Test Account",
        type="checking",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    return account


# ---------------------------------------------------------------------------
# create_payee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_payee(session: AsyncSession, test_user, test_workspace):
    data = PayeeCreate(name="Starbucks", type="company")
    payee = await create_payee(session, test_workspace.id, test_user.id, data)

    assert payee.name == "Starbucks"
    assert payee.type == "company"
    # Stamped by the code path, not by the caller: this is the manual one.
    assert payee.source == "manual"
    assert payee.is_favorite is False
    assert payee.transaction_count == 0  # type: ignore[attr-defined]
    assert payee.user_id == test_user.id


@pytest.mark.asyncio
async def test_create_payee_with_notes(session: AsyncSession, test_user, test_workspace):
    data = PayeeCreate(name="Coffee Shop", type="company", notes="Morning coffee")
    payee = await create_payee(session, test_workspace.id, test_user.id, data)

    assert payee.notes == "Morning coffee"


@pytest.mark.asyncio
async def test_create_payee_duplicate_name_rejected(session: AsyncSession, test_user, test_workspace):
    await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Starbucks"))
    with pytest.raises(ValueError, match="already exists"):
        await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="starbucks"))  # case-insensitive


@pytest.mark.asyncio
async def test_create_payee_creates_self_mapping(session: AsyncSession, test_user, test_workspace):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Mapped"))
    from sqlalchemy import select
    result = await session.execute(
        select(PayeeMapping).where(PayeeMapping.id == payee.id)
    )
    mapping = result.scalar_one_or_none()
    assert mapping is not None
    assert mapping.target_id == payee.id


# ---------------------------------------------------------------------------
# get_payee / get_payees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_payee(session: AsyncSession, test_user, test_workspace):
    created = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Target"))
    fetched = await get_payee(session, created.id, test_workspace.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "Target"


@pytest.mark.asyncio
async def test_get_payee_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_payee(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_payees_empty(session: AsyncSession, test_user, test_workspace):
    payees = await get_payees(session, test_workspace.id)
    assert payees == []


@pytest.mark.asyncio
async def test_get_payees_with_transaction_counts(session: AsyncSession, test_user, test_workspace):
    p1 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Payee A"))
    p2 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Payee B"))

    account = await _make_account(session, test_user)

    # Add 2 transactions for p1, 1 for p2
    for i in range(2):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=f"Tx {i}", amount=Decimal("10"), date=date.today(),
            type="debit", source="manual", payee_id=p1.id,
            created_at=datetime.now(timezone.utc),
        ))
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Tx single", amount=Decimal("5"), date=date.today(),
        type="debit", source="manual", payee_id=p2.id,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    payees = await get_payees(session, test_workspace.id)
    payees_by_name = {p.name: p for p in payees}

    assert payees_by_name["Payee A"].transaction_count == 2  # type: ignore[attr-defined]
    assert payees_by_name["Payee B"].transaction_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_get_payees_ordered_by_name(session: AsyncSession, test_user, test_workspace):
    await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Zebra"))
    await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Apple"))
    await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Mango"))

    payees = await get_payees(session, test_workspace.id)
    names = [p.name for p in payees]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_get_payees_includes_zero_transaction_payees(session: AsyncSession, test_user, test_workspace):
    orphan = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="No Tx"))
    active = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Has Tx"))
    account = await _make_account(session, test_user)

    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Paid", amount=Decimal("12"), date=date.today(),
        type="debit", source="manual", payee_id=active.id,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    payees = await get_payees(session, test_workspace.id)
    ids = {p.id for p in payees}

    assert active.id in ids
    assert orphan.id in ids

    payees_by_id = {p.id: p for p in payees}
    assert payees_by_id[active.id].transaction_count == 1  # type: ignore[attr-defined]
    assert payees_by_id[orphan.id].transaction_count == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# get_or_create_payee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_payee_creates_new(session: AsyncSession, test_user, test_workspace):
    payee = await get_or_create_payee(
        session, test_user.id, "New Payee", workspace_id=test_workspace.id
    )
    assert payee.name == "New Payee"
    assert payee.user_id == test_user.id


@pytest.mark.asyncio
async def test_get_or_create_payee_returns_existing(session: AsyncSession, test_user, test_workspace):
    original = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Existing"))
    found = await get_or_create_payee(
        session, test_user.id, "existing", workspace_id=test_workspace.id
    )
    assert found.id == original.id


@pytest.mark.asyncio
async def test_get_or_create_payee_strips_whitespace(session: AsyncSession, test_user, test_workspace):
    payee = await get_or_create_payee(
        session, test_user.id, "  Trimmed  ", workspace_id=test_workspace.id
    )
    assert payee.name == "Trimmed"


@pytest.mark.asyncio
async def test_get_or_create_payee_empty_raises(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="cannot be empty"):
        await get_or_create_payee(
            session, test_user.id, "  ", workspace_id=test_workspace.id
        )


@pytest.mark.asyncio
async def test_get_or_create_payee_returns_case_insensitive_concurrent_winner(
    session: AsyncSession, test_user, test_workspace, monkeypatch
):
    winner = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="Same Payee"),
    )

    real_execute = session.execute
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    first_lookup = True

    async def miss_once(*args, **kwargs):
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return missing
        return await real_execute(*args, **kwargs)

    monkeypatch.setattr(session, "execute", miss_once)

    result = await get_or_create_payee(
        session, test_user.id, "same payee", workspace_id=test_workspace.id
    )

    assert result.id == winner.id


@pytest.mark.asyncio
async def test_payee_name_is_case_insensitive_within_workspace(
    session: AsyncSession, test_user, test_workspace
):
    session.add(Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Case Variant",
    ))
    await session.flush()
    session.add(Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="  case variant  ",
    ))

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_same_user_can_have_same_payee_name_in_different_workspaces(
    session: AsyncSession, test_user, test_workspace
):
    second_workspace = Workspace(
        name="Second",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
    )
    session.add(second_workspace)
    await session.flush()

    first = await get_or_create_payee(
        session,
        test_user.id,
        "Shared Name",
        workspace_id=test_workspace.id,
    )
    second = await get_or_create_payee(
        session,
        test_user.id,
        "shared name",
        workspace_id=second_workspace.id,
    )

    assert first.id != second.id
    assert first.workspace_id == test_workspace.id
    assert second.workspace_id == second_workspace.id


# ---------------------------------------------------------------------------
# update_payee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_payee(session: AsyncSession, test_user, test_workspace):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Old Name"))
    updated = await update_payee(
        session, payee.id, test_workspace.id,
        PayeeUpdate(name="New Name", is_favorite=True),
    )

    assert updated is not None
    assert updated.name == "New Name"
    assert updated.is_favorite is True
    # Unchanged, and unchanged means null: nothing ever stated a legal
    # nature for this one, which is the resting state now that there is no
    # catch-all default.
    assert updated.type is None


@pytest.mark.asyncio
async def test_create_and_update_payee_strip_names(
    session: AsyncSession, test_user, test_workspace
):
    payee = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="  Trimmed Create  "),
    )
    assert payee.name == "Trimmed Create"

    updated = await update_payee(
        session,
        payee.id,
        test_workspace.id,
        PayeeUpdate(name="  Trimmed Update  "),
    )
    assert updated is not None
    assert updated.name == "Trimmed Update"


@pytest.mark.asyncio
async def test_update_payee_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_payee(
        session, uuid.uuid4(), test_workspace.id, PayeeUpdate(name="Nope")
    )
    assert result is None


@pytest.mark.asyncio
async def test_update_payee_duplicate_name_rejected(session: AsyncSession, test_user, test_workspace):
    await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="A"))
    b = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="B"))

    with pytest.raises(ValueError, match="already exists"):
        await update_payee(session, b.id, test_workspace.id, PayeeUpdate(name="A"))


# ---------------------------------------------------------------------------
# delete_payee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_payee(session: AsyncSession, test_user, test_workspace):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="ToDelete"))
    assert await delete_payee(session, payee.id, test_workspace.id) is True
    assert await get_payee(session, payee.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_payee_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_payee(session, uuid.uuid4(), test_workspace.id) is False


@pytest.mark.asyncio
async def test_delete_payee_nulls_transaction_refs(session: AsyncSession, test_user, test_workspace):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Linked"))
    account = await _make_account(session, test_user)

    tx = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Linked Tx", amount=Decimal("50"), date=date.today(),
        type="debit", source="manual", payee_id=payee.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()

    await delete_payee(session, payee.id, test_workspace.id)

    await session.refresh(tx)
    assert tx.payee_id is None


@pytest.mark.asyncio
async def test_bulk_delete_payees(session: AsyncSession, test_user, test_workspace):
    p1 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Bulk1"))
    p2 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Bulk2"))
    p3 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Bulk3"))

    account = await _make_account(session, test_user)

    tx = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Linked Tx", amount=Decimal("50"), date=date.today(),
        type="debit", source="manual", payee_id=p1.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()

    deleted = await bulk_delete_payees(session, test_workspace.id, [p1.id, p2.id])
    assert deleted == 2

    assert await get_payee(session, p1.id, test_workspace.id) is None
    assert await get_payee(session, p2.id, test_workspace.id) is None
    assert await get_payee(session, p3.id, test_workspace.id) is not None

    await session.refresh(tx)
    assert tx.payee_id is None


@pytest.mark.asyncio
async def test_bulk_delete_payees_invalid_ids(session: AsyncSession, test_user, test_workspace):
    deleted = await bulk_delete_payees(session, test_workspace.id, [uuid.uuid4(), uuid.uuid4()])
    assert deleted == 0


@pytest.mark.asyncio
async def test_bulk_delete_payees_workspace_isolation(session: AsyncSession, test_user, test_workspace):
    from app.models.workspace import Workspace
    
    other_workspace = Workspace(id=uuid.uuid4(), name="Other")
    session.add(other_workspace)
    await session.commit()

    p = await create_payee(session, other_workspace.id, test_user.id, PayeeCreate(name="OtherWS"))
    deleted = await bulk_delete_payees(session, test_workspace.id, [p.id])
    assert deleted == 0

    assert await get_payee(session, p.id, other_workspace.id) is not None


@pytest.mark.asyncio
async def test_delete_payee_cleans_up_mappings(session: AsyncSession, test_user, test_workspace):
    from sqlalchemy import select

    p = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Mapped"))
    mapping = PayeeMapping(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        target_id=p.id,
    )
    session.add(mapping)
    await session.commit()

    await delete_payee(session, p.id, test_workspace.id)

    result = await session.execute(select(PayeeMapping).where(PayeeMapping.target_id == p.id))
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# merge_payees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_payees(session: AsyncSession, test_user, test_workspace):
    target = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Target"))
    source1 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Source1"))
    source2 = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Source2"))

    account = await _make_account(session, test_user)

    # Create transactions assigned to source payees
    for p in [source1, source2]:
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=f"Tx for {p.name}", amount=Decimal("10"), date=date.today(),
            type="debit", source="manual", payee_id=p.id,
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    reassigned = await merge_payees(
        session, test_workspace.id, target.id, [source1.id, source2.id]
    )

    assert reassigned == 2

    # Sources should be deleted
    assert await get_payee(session, source1.id, test_workspace.id) is None
    assert await get_payee(session, source2.id, test_workspace.id) is None

    # Target should still exist
    assert await get_payee(session, target.id, test_workspace.id) is not None


@pytest.mark.asyncio
async def test_merge_payees_target_not_found(session: AsyncSession, test_user, test_workspace):
    source = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Source"))
    with pytest.raises(ValueError, match="Target payee not found"):
        await merge_payees(session, test_workspace.id, uuid.uuid4(), [source.id])


@pytest.mark.asyncio
async def test_merge_payees_source_not_found(session: AsyncSession, test_user, test_workspace):
    target = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Target"))
    with pytest.raises(ValueError, match="Source payee"):
        await merge_payees(session, test_workspace.id, target.id, [uuid.uuid4()])


# ---------------------------------------------------------------------------
# get_payee_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_payee_summary(session: AsyncSession, test_user, test_workspace, test_categories):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Summary"))
    account = await _make_account(session, test_user)
    cat = test_categories[0]

    today = date.today()
    # 2 debits, 1 credit
    for desc, amount, typ in [
        ("Buy 1", Decimal("100"), "debit"),
        ("Buy 2", Decimal("50"), "debit"),
        ("Refund", Decimal("30"), "credit"),
    ]:
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=desc, amount=amount, date=today,
            type=typ, source="manual", payee_id=payee.id,
            category_id=cat.id, created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    summary = await get_payee_summary(session, payee.id, test_workspace.id)

    assert summary["total_spent"] == Decimal("150")
    assert summary["total_received"] == Decimal("30")
    assert summary["transaction_count"] == 3
    assert summary["last_transaction_date"] == today
    assert summary["most_common_category"] is not None
    assert summary["most_common_category"].name == cat.name


@pytest.mark.asyncio
async def test_get_payee_summary_not_found(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="Payee not found"):
        await get_payee_summary(session, uuid.uuid4(), test_workspace.id)


@pytest.mark.asyncio
async def test_get_payee_summary_no_transactions(session: AsyncSession, test_user, test_workspace):
    payee = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name="Empty"))
    summary = await get_payee_summary(session, payee.id, test_workspace.id)

    assert summary["total_spent"] == Decimal("0")
    assert summary["total_received"] == Decimal("0")
    assert summary["transaction_count"] == 0
    assert summary["last_transaction_date"] is None
    assert summary["most_common_category"] is None
