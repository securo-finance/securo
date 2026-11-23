"""Reimbursement allocations on real PostgreSQL (#1126).

SQLite test databases do not enforce foreign keys or run `to_char`, so the
cascade on raw deletes, the table's constraints, migration 130 and the
report's period labels are checked here.
"""

import asyncio
import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.reimbursement_allocation import ReimbursementAllocation
from app.models.transaction import Transaction
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import reimbursement_service
from app.services.reimbursement_netting import reimbursement_offset_parts

pytestmark = pytest.mark.asyncio

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
(_MIGRATION_PATH,) = _VERSIONS.glob("*_reimbursement_allocations.py")
_SPEC = importlib.util.spec_from_file_location("reimbursement_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


async def _linked_pair(session, user, workspace, on: date):
    food = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Food")
    account = Account(
        user_id=user.id, workspace_id=workspace.id, name="Checking", type="checking",
        balance=0, currency="USD",
    )
    session.add_all([food, account])
    await session.flush()
    rows = []
    for amount, type_ in (("100", "debit"), ("60", "credit")):
        tx = Transaction(
            user_id=user.id, workspace_id=workspace.id, account_id=account.id,
            description="Row", amount=Decimal(amount), currency="USD", date=on,
            effective_date=on, type=type_, source="manual", status="posted",
            category_id=food.id if type_ == "debit" else None,
        )
        session.add(tx)
        rows.append(tx)
    await session.flush()
    dinner, payback = rows
    await reimbursement_service.replace_allocations(
        session, dinner,
        [ReimbursementAllocationInput(counterpart_id=payback.id, amount=Decimal("60"))],
    )
    return food, dinner, payback


async def _count(session) -> int:
    return await session.scalar(select(func.count(ReimbursementAllocation.id))) or 0


async def test_a_raw_delete_of_either_side_cascades(session, test_user, test_workspace):
    _, dinner, payback = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    assert await _count(session) == 1

    await session.execute(delete(Transaction).where(Transaction.id == payback.id))

    assert await _count(session) == 0
    _, dinner, _ = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    await session.execute(delete(Transaction).where(Transaction.id == dinner.id))
    assert await _count(session) == 0


async def test_the_constraints_hold(session, test_user, test_workspace):
    _, dinner, payback = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))

    for anchor_id, counterpart_id, amount in (
        (dinner.id, payback.id, Decimal("1")),  # the pair is already linked
        (dinner.id, dinner.id, Decimal("1")),  # a row cannot pay itself back
        (payback.id, dinner.id, Decimal("0")),  # amounts are positive
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    ReimbursementAllocation(
                        workspace_id=test_workspace.id, anchor_id=anchor_id,
                        counterpart_id=counterpart_id, amount=amount,
                    )
                )
                await session.flush()


async def test_the_netting_labels_periods_on_postgres(session, test_user, test_workspace):
    from app.services.report_service import _interval_label_expr, reporting_date_col

    on = app_today().replace(day=1)
    food, _, _ = await _linked_pair(session, test_user, test_workspace, on)
    label = _interval_label_expr("monthly", reporting_date_col("cash")).label("period")

    parts = await reimbursement_offset_parts(
        session, test_user.id, on, app_today(), workspace_id=test_workspace.id,
        end_inclusive=True, label_expr=label,
    )

    assert {(p.label, p.category_id, p.type, p.amount) for p in parts} == {
        (on.strftime("%Y-%m"), food.id, "debit", Decimal("60.00")),
        (on.strftime("%Y-%m"), None, "credit", Decimal("60.00")),
    }


async def test_the_migration_rolls_back_and_forward(session):
    schema = session.bind.get_execution_options()["schema_translate_map"][None]

    def run(sync_conn, direction):
        # Point the migration at the test schema the way a real database's
        # search_path would.
        sync_conn.exec_driver_sql(f'SET search_path TO "{schema}"')
        with Operations.context(MigrationContext.configure(sync_conn)):
            getattr(migration, direction)()

    def table_exists() -> str:
        return f"SELECT to_regclass('\"{schema}\".reimbursement_allocations')"

    conn = await session.connection()
    await conn.run_sync(run, "downgrade")
    assert (await conn.execute(text(table_exists()))).scalar_one() is None
    await conn.run_sync(run, "upgrade")
    assert (await conn.execute(text(table_exists()))).scalar_one() is not None


async def test_counterparts_are_locked_in_id_order_before_the_cap_is_checked(
    session, test_user, test_workspace
):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    # Two saves for different anchors must queue on the counterpart, or both
    # can pass the cap on the same stale total and over-allocate it.
    # The table is schema-qualified in these per-test schemas.
    locking = [s for s in statements if s.startswith("SELECT") and s.endswith("FOR UPDATE")]
    assert any(" FROM " in s and "ORDER BY" in s and s.endswith("transactions.id FOR UPDATE") for s in locking), statements


async def test_the_candidate_search_runs_on_postgres(session, test_user, test_workspace):
    food, dinner, payback = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    other = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=dinner.account_id,
        description="Other", amount=Decimal("40"), currency="USD", date=date(2026, 10, 10),
        effective_date=date(2026, 10, 10), type="debit", source="manual", status="posted",
    )
    session.add(other)
    await session.flush()

    # The payback is already linked to the dinner, so it stays on its list; it
    # is used up for any other expense, so it drops off theirs.
    mine = await reimbursement_service.find_candidates(session, dinner, looking_for="counterparts")
    others = await reimbursement_service.find_candidates(session, other, looking_for="counterparts")
    anchors = await reimbursement_service.find_candidates(session, payback, looking_for="anchors")

    assert [row.id for row in mine] == [payback.id]
    assert others == []
    # The dinner it already pays back is listed too; the dialog filters linked rows.
    assert {row.id for row in anchors} == {dinner.id, other.id}


async def test_a_second_save_of_one_anchor_waits_for_the_first(
    session, postgres_sessions, test_user, test_workspace
):
    from app.services import transaction_service

    _, dinner, _ = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    await session.commit()

    async with postgres_sessions() as first, postgres_sessions() as second:
        # The route loads its anchor this way before it replaces the links.
        held = await transaction_service.get_transaction(
            first, dinner.id, test_workspace.id, for_update=True
        )
        assert held is not None

        waiting = asyncio.create_task(
            transaction_service.get_transaction(second, dinner.id, test_workspace.id, for_update=True)
        )
        done, _pending = await asyncio.wait({waiting}, timeout=1.0)
        assert not done, "a second save took the anchor while the first held it"

        await first.commit()
        assert await asyncio.wait_for(waiting, timeout=10) is not None
        await second.rollback()


async def test_two_saves_of_one_anchor_end_with_only_the_second_saves_links(
    session, postgres_sessions, test_user, test_workspace
):
    from app.services import transaction_service

    _, dinner, alice = await _linked_pair(session, test_user, test_workspace, date(2026, 10, 10))
    bob = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=dinner.account_id,
        description="Bob", amount=Decimal("40"), currency="USD", date=date(2026, 10, 11),
        effective_date=date(2026, 10, 11), type="credit", source="manual", status="posted",
    )
    session.add(bob)
    await session.flush()
    await reimbursement_service.replace_allocations(session, dinner, [])  # start unlinked
    await session.commit()

    async def save(db, counterpart, amount):
        # What the route does: lock the anchor, replace its links, then commit.
        anchor = await transaction_service.get_transaction(
            db, dinner.id, test_workspace.id, for_update=True
        )
        assert anchor is not None
        await reimbursement_service.replace_allocations(
            db, anchor,
            [ReimbursementAllocationInput(counterpart_id=counterpart.id, amount=Decimal(amount))],
        )

    async with postgres_sessions() as first, postgres_sessions() as second:
        await save(first, alice, "60")  # not committed yet: holds the anchor
        later = asyncio.create_task(save(second, bob, "40"))
        done, _pending = await asyncio.wait({later}, timeout=1.0)
        assert not done, "the second save ran while the first still held the anchor"

        await first.commit()
        await asyncio.wait_for(later, timeout=10)
        await second.commit()

    async with postgres_sessions() as check:
        rows = (await check.execute(
            select(ReimbursementAllocation.counterpart_id, ReimbursementAllocation.amount)
            .where(ReimbursementAllocation.anchor_id == dinner.id)
        )).all()
    # Without the lock both saves clear and insert on the same empty set and
    # the anchor ends linked to both payers.
    assert rows == [(bob.id, Decimal("40.00"))]
