"""Linking reimbursements and refunds to the transaction they pay back (#1126).

Covers the write side: the invariants `reimbursement_service` keeps, the
edits it refuses, and the links' read model.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.category import Category
from app.models.reimbursement_allocation import ReimbursementAllocation
from app.models.transaction import Transaction
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import reimbursement_service


@pytest.fixture
def make_tx(session, test_user, test_account, test_workspace):
    async def make(amount, type_="debit", **extra) -> Transaction:
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=extra.pop("workspace_id", test_workspace.id),
            account_id=test_account.id,
            description=extra.pop("description", "Dinner"),
            amount=Decimal(str(amount)),
            currency=extra.pop("currency", test_account.currency),
            date=extra.pop("date", date(2026, 10, 10)),
            type=type_,
            source=extra.pop("source", "manual"),
            **extra,
        )
        session.add(tx)
        await session.flush()
        return tx

    return make


def _alloc(tx: Transaction, amount) -> ReimbursementAllocationInput:
    return ReimbursementAllocationInput(counterpart_id=tx.id, amount=Decimal(str(amount)))


async def _allocations(session, anchor_id) -> list[tuple[uuid.UUID, Decimal]]:
    result = await session.execute(
        select(ReimbursementAllocation.counterpart_id, ReimbursementAllocation.amount)
        .where(ReimbursementAllocation.anchor_id == anchor_id)
        .order_by(ReimbursementAllocation.amount)
    )
    return [(cid, Decimal(str(amount))) for cid, amount in result.all()]


@pytest.mark.asyncio
async def test_links_several_counterparts_to_an_anchor(session, make_tx):
    dinner = await make_tx(100)
    alice, bob = await make_tx(33, "credit"), await make_tx(33, "credit")

    await reimbursement_service.replace_allocations(
        session, dinner, [_alloc(alice, 33), _alloc(bob, 33)]
    )

    assert sorted(await _allocations(session, dinner.id)) == sorted(
        [(alice.id, Decimal("33.00")), (bob.id, Decimal("33.00"))]
    )


@pytest.mark.asyncio
async def test_replacing_drops_the_old_allocations(session, make_tx):
    dinner = await make_tx(100)
    alice, bob = await make_tx(33, "credit"), await make_tx(33, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(alice, 33)])

    await reimbursement_service.replace_allocations(session, dinner, [_alloc(bob, 20)])

    assert await _allocations(session, dinner.id) == [(bob.id, Decimal("20.00"))]


@pytest.mark.asyncio
async def test_an_empty_list_unlinks_the_anchor(session, make_tx):
    dinner, alice = await make_tx(100), await make_tx(33, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(alice, 33)])

    await reimbursement_service.replace_allocations(session, dinner, [])

    assert await _allocations(session, dinner.id) == []


@pytest.mark.asyncio
async def test_a_counterpart_can_be_split_across_anchors_up_to_its_amount(session, make_tx):
    first, second = await make_tx(30), await make_tx(30)
    payback = await make_tx(60, "credit")

    await reimbursement_service.replace_allocations(session, first, [_alloc(payback, 30)])
    await reimbursement_service.replace_allocations(session, second, [_alloc(payback, 30)])

    with pytest.raises(ValueError, match="beyond its amount"):
        third = await make_tx(10)
        await reimbursement_service.replace_allocations(session, third, [_alloc(payback, "0.01")])


@pytest.mark.asyncio
async def test_re_saving_an_anchor_does_not_count_its_own_allocation_twice(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 60)])

    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 60)])

    assert await _allocations(session, dinner.id) == [(payback.id, Decimal("60.00"))]


@pytest.mark.asyncio
async def test_an_anchor_has_no_cap(session, make_tx):
    gift = await make_tx(100)
    paybacks = [await make_tx(50, "credit") for _ in range(3)]

    await reimbursement_service.replace_allocations(
        session, gift, [_alloc(p, 50) for p in paybacks]
    )

    assert len(await _allocations(session, gift.id)) == 3


@pytest.mark.asyncio
async def test_refuses_a_counterpart_in_the_same_direction(session, make_tx):
    dinner, other = await make_tx(100), await make_tx(10)

    with pytest.raises(ValueError, match="opposite direction"):
        await reimbursement_service.replace_allocations(session, dinner, [_alloc(other, 10)])


@pytest.mark.asyncio
async def test_refuses_chains(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(30, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 30)])

    later = await make_tx(5)
    with pytest.raises(ValueError, match="of its own"):
        await reimbursement_service.replace_allocations(session, payback, [_alloc(later, 5)])
    income = await make_tx(500, "credit")
    with pytest.raises(ValueError, match="cannot be a reimbursement"):
        await reimbursement_service.replace_allocations(session, income, [_alloc(dinner, 10)])


@pytest.mark.asyncio
async def test_refuses_duplicates_and_self(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(30, "credit")

    with pytest.raises(ValueError, match="only once"):
        await reimbursement_service.replace_allocations(
            session, dinner, [_alloc(payback, 10), _alloc(payback, 10)]
        )
    with pytest.raises(ValueError, match="itself"):
        await reimbursement_service.replace_allocations(session, dinner, [_alloc(dinner, 10)])


@pytest.mark.asyncio
async def test_refuses_a_row_in_another_workspace(session, make_tx, test_user):
    from app.services.workspace_service import create_workspace

    other = await create_workspace(session, name="Other", creator=test_user)
    dinner = await make_tx(100)
    foreign = await make_tx(30, "credit", workspace_id=other.id)

    with pytest.raises(ValueError, match="not found"):
        await reimbursement_service.replace_allocations(session, dinner, [_alloc(foreign, 30)])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, match",
    [
        ({"transfer_pair_id": uuid.uuid4()}, "transfer"),
        ({"is_ignored": True}, "left out of reports"),
        ({"exclude_from_pnl": True}, "left out of reports"),
        ({"source": "settlement"}, "cannot be linked"),
        ({"source": "opening_balance"}, "cannot be linked"),
    ],
)
async def test_refuses_rows_reports_leave_out(session, make_tx, extra, match):
    dinner = await make_tx(100)
    blocked = await make_tx(30, "credit", **extra)

    with pytest.raises(ValueError, match=match):
        await reimbursement_service.replace_allocations(session, dinner, [_alloc(blocked, 30)])
    with pytest.raises(ValueError, match=match):
        await reimbursement_service.replace_allocations(
            session, await make_tx(30, **extra), [_alloc(await make_tx(5, "credit"), 5)]
        )


@pytest.mark.asyncio
async def test_refuses_a_row_in_an_ignored_or_transfer_category(
    session, make_tx, test_user, test_workspace
):
    for flags in ({"is_ignored": True}, {"treat_as_transfer": True}):
        category = Category(
            user_id=test_user.id, workspace_id=test_workspace.id,
            name=str(flags), icon="x", color="#000000", **flags,
        )
        session.add(category)
        await session.flush()
        dinner = await make_tx(100)
        blocked = await make_tx(30, "credit", category_id=category.id)

        with pytest.raises(ValueError, match="ignored or transfer category"):
            await reimbursement_service.replace_allocations(session, dinner, [_alloc(blocked, 30)])


@pytest.mark.asyncio
async def test_refuses_a_row_split_among_people(session, make_tx, test_user, test_workspace):
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    group = Group(user_id=test_user.id, workspace_id=test_workspace.id, name="Trip")
    session.add(group)
    await session.flush()
    member = GroupMember(group_id=group.id, workspace_id=test_workspace.id, name="Alice")
    session.add(member)
    await session.flush()
    dinner = await make_tx(100)
    session.add(
        TransactionSplit(
            transaction_id=dinner.id, workspace_id=test_workspace.id,
            group_member_id=member.id, share_amount=Decimal("50"),
        )
    )
    await session.flush()

    with pytest.raises(ValueError, match="split among people"):
        await reimbursement_service.replace_allocations(
            session, dinner, [_alloc(await make_tx(50, "credit"), 50)]
        )


@pytest.mark.asyncio
async def test_a_failed_replace_keeps_the_old_allocations(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(30, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 30)])

    with pytest.raises(ValueError):
        await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 31)])

    assert await _allocations(session, dinner.id) == [(payback.id, Decimal("30.00"))]


@pytest.mark.asyncio
async def test_check_edit_refuses_shrinking_below_the_allocations(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 40)])

    await reimbursement_service.check_edit(session, payback, new_amount=Decimal("40"))
    with pytest.raises(ValueError, match="less than what is allocated"):
        await reimbursement_service.check_edit(session, payback, new_amount=Decimal("39.99"))
    # An anchor has no cap, so it can shrink freely.
    await reimbursement_service.check_edit(session, dinner, new_amount=Decimal("1"))


@pytest.mark.asyncio
async def test_check_edit_refuses_flipping_a_linked_row(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 40)])

    for tx, flipped in ((dinner, "credit"), (payback, "debit")):
        with pytest.raises(ValueError, match="direction"):
            await reimbursement_service.check_edit(session, tx, new_type=flipped)
    await reimbursement_service.check_edit(session, await make_tx(5), new_type="credit")


@pytest.mark.asyncio
async def test_deleting_a_row_removes_its_allocations(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 40)])

    await session.delete(payback)
    await session.flush()

    assert await _allocations(session, dinner.id) == []


@pytest.mark.asyncio
async def test_read_from_the_anchor_side(session, make_tx):
    dinner = await make_tx(100)
    alice, bob = await make_tx(33, "credit"), await make_tx(50, "credit")
    await reimbursement_service.replace_allocations(
        session, dinner, [_alloc(alice, 33), _alloc(bob, 33)]
    )

    read = await reimbursement_service.get_reimbursements(session, dinner)

    assert read.anchors == []
    assert {c.counterpart_id: c.transaction.unallocated for c in read.counterparts} == {
        alice.id: Decimal("0.00"),
        bob.id: Decimal("17.00"),
    }
    assert read.net_amount == Decimal("34.00")
    assert read.over_allocated is False


@pytest.mark.asyncio
async def test_read_from_the_counterpart_side(session, make_tx):
    first, second = await make_tx(30), await make_tx(20)
    payback = await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, first, [_alloc(payback, 30)])
    await reimbursement_service.replace_allocations(session, second, [_alloc(payback, 20)])

    read = await reimbursement_service.get_reimbursements(session, payback)

    assert read.counterparts == []
    assert {a.anchor_id for a in read.anchors} == {first.id, second.id}
    assert read.unallocated == Decimal("10.00")
    assert read.net_amount is None


@pytest.mark.asyncio
async def test_read_flags_a_counterpart_a_sync_shrank(session, make_tx):
    dinner, payback = await make_tx(100), await make_tx(60, "credit")
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 60)])
    payback.amount = Decimal("50")
    await session.flush()

    read = await reimbursement_service.get_reimbursements(session, payback)

    assert read.over_allocated is True
    assert read.unallocated == Decimal("-10.00")


@pytest.mark.asyncio
async def test_net_amount_converts_a_counterpart_in_another_currency(session, make_tx):
    dinner = await make_tx(100, currency="EUR", amount_primary=Decimal("100"))
    payback = await make_tx(550, "credit", currency="SEK", amount_primary=Decimal("50"))
    await reimbursement_service.replace_allocations(session, dinner, [_alloc(payback, 550)])

    read = await reimbursement_service.get_reimbursements(session, dinner)

    assert read.net_amount == Decimal("50.00")


@pytest.mark.asyncio
async def test_net_amount_is_signed_once_paid_back_in_full(session, make_tx):
    gift = await make_tx(100)
    await reimbursement_service.replace_allocations(
        session, gift, [_alloc(await make_tx(150, "credit"), 150)]
    )

    read = await reimbursement_service.get_reimbursements(session, gift)

    assert read.net_amount == Decimal("-50.00")
