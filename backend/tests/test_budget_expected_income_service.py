"""The expected-income upsert of the budget service: one row per workspace and month."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expected_income import ExpectedIncome
from app.models.fx_rate import FxRate
from app.models.workspace import Workspace
from app.services import budget_service
from app.services.budget_service import get_expected_income, set_expected_income

pytestmark = pytest.mark.asyncio

_MONTH = date(2026, 4, 1)


async def _stored_rows(session: AsyncSession, workspace_id) -> list[ExpectedIncome]:
    result = await session.execute(
        select(ExpectedIncome).where(ExpectedIncome.workspace_id == workspace_id)
    )
    return list(result.scalars())


async def test_set_creates_a_row_keeping_the_currency_it_was_typed_in(
    session: AsyncSession, test_workspace
):
    read = await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")
    assert read.month == _MONTH
    assert read.amount == Decimal("3450.00")
    assert read.currency == "USD"

    rows = await _stored_rows(session, test_workspace.id)
    assert len(rows) == 1
    assert rows[0].currency == "USD"  # the passed currency, not the reader's BRL
    assert isinstance(rows[0].amount, Decimal)  # money stays exact, never float


async def test_the_second_write_updates_the_same_row_instead_of_adding_one(
    session: AsyncSession, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")
    first_id = (await _stored_rows(session, test_workspace.id))[0].id

    second = await set_expected_income(
        session, test_workspace.id, _MONTH, Decimal("4000.555"), "USD", reader_currency="BRL"
    )
    assert second.amount == Decimal("4000.56")  # quantized to the money scale

    rows = await _stored_rows(session, test_workspace.id)
    assert len(rows) == 1
    assert rows[0].id == first_id  # same row, not a new one
    assert rows[0].amount == Decimal("4000.56")


async def test_the_month_is_normalized_to_its_first_day(session: AsyncSession, test_workspace):
    await set_expected_income(
        session, test_workspace.id, date(2026, 4, 17), Decimal("3450.00"), "USD",
        reader_currency="BRL",
    )
    rows = await _stored_rows(session, test_workspace.id)
    assert [row.month for row in rows] == [_MONTH]

    # Any other day of the same month answers the same row.
    read = await get_expected_income(session, test_workspace.id, date(2026, 4, 17), "BRL")
    assert read is not None and read.amount == Decimal("3450.00")


async def test_clearing_removes_the_row_and_clearing_an_absent_one_creates_nothing(
    session: AsyncSession, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")
    read = await set_expected_income(session, test_workspace.id, _MONTH, None, "USD", reader_currency="BRL")
    assert read.month == _MONTH
    assert read.amount is None
    assert read.currency is None
    assert await _stored_rows(session, test_workspace.id) == []

    again = await set_expected_income(session, test_workspace.id, _MONTH, None, "USD", reader_currency="BRL")
    assert again.amount is None
    assert await _stored_rows(session, test_workspace.id) == []


async def test_a_neighbouring_workspace_ne_sees_nor_touches_the_row(
    session: AsyncSession, test_user, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")

    other = Workspace(
        id=uuid.uuid4(),
        name="Vizinho",
        kind="business",
        created_by_user_id=test_user.id,
        default_currency="USD",
        locale="pt-BR",
    )
    session.add(other)
    await session.flush()

    assert await get_expected_income(session, other.id, _MONTH, "BRL") is None
    await set_expected_income(session, other.id, _MONTH, Decimal("9000.00"), "USD", reader_currency="BRL")

    rows = await _stored_rows(session, test_workspace.id)
    assert len(rows) == 1 and rows[0].amount == Decimal("3450.00")


async def test_the_read_converts_at_the_month_rate_without_storing_it(
    session: AsyncSession, test_workspace
):
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date(2026, 4, 1),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.flush()

    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")
    read = await get_expected_income(session, test_workspace.id, _MONTH, "BRL")
    assert read is not None
    assert read.amount == Decimal("3450.00")  # the stored figure keeps its currency
    assert read.currency == "USD"
    assert read.amount_primary == Decimal("17250.00")

    rows = await _stored_rows(session, test_workspace.id)
    assert rows[0].amount == Decimal("3450.00")  # nothing converted was persisted


async def test_a_missing_rate_leaves_the_primary_amount_empty_not_one_to_one(
    session: AsyncSession, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")
    read = await get_expected_income(session, test_workspace.id, _MONTH, "BRL")
    assert read is not None
    assert read.amount == Decimal("3450.00")
    assert read.amount_primary is None  # no rate: no fake 1:1


async def test_changing_the_reader_currency_keeps_the_stored_figure(
    session: AsyncSession, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")

    # The same workspace read as USD now: the stored row does not move.
    read = await get_expected_income(session, test_workspace.id, _MONTH, "USD")
    assert read is not None
    assert read.amount == Decimal("3450.00")
    assert read.amount_primary == Decimal("3450.00")  # same currency: rate 1, no rate row needed

    rows = await _stored_rows(session, test_workspace.id)
    assert rows[0].amount == Decimal("3450.00")
    assert rows[0].currency == "USD"


async def test_the_write_comes_back_with_the_month_and_the_amount(
    session: AsyncSession, test_workspace
):
    read = await set_expected_income(
        session, test_workspace.id, _MONTH, Decimal("3450.005"), "USD", reader_currency="USD"
    )
    assert read.month == _MONTH
    assert read.amount == Decimal("3450.01")  # the money scale rounds half up
    # USD stored read as USD: the writer sees the figure they typed, unconverted.
    assert read.amount_primary == Decimal("3450.01")


async def test_a_racing_insert_updates_the_row_it_collides_with(
    session: AsyncSession, test_workspace, monkeypatch
):
    # The rollback below expires every loaded object; keep the id at hand.
    workspace_id = test_workspace.id
    await set_expected_income(session, workspace_id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")

    original_select = budget_service.select
    calls = {"count": 0}

    def racing_select(*args, **kwargs):
        calls["count"] += 1
        statement = original_select(*args, **kwargs)
        if calls["count"] == 1:
            # The other request wrote the row between this read and its insert.
            return statement.where(false())
        return statement

    monkeypatch.setattr(budget_service, "select", racing_select)

    read = await set_expected_income(
        session, workspace_id, _MONTH, Decimal("4000.00"), "USD", reader_currency="BRL"
    )
    assert read.amount == Decimal("4000.00")

    rows = await _stored_rows(session, workspace_id)
    assert len(rows) == 1
    assert rows[0].amount == Decimal("4000.00")


async def test_the_update_writes_amount_and_currency_together(
    session: AsyncSession, test_workspace
):
    await set_expected_income(session, test_workspace.id, _MONTH, Decimal("3450.00"), "USD", reader_currency="BRL")

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE"):
            statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        # Same currency, new amount: attribute tracking would have written the
        # amount alone and let a concurrent save of the currency through.
        await set_expected_income(session, test_workspace.id, _MONTH, Decimal("4000.00"), "USD", reader_currency="BRL")
        # Same amount, new currency: the mirror case.
        await set_expected_income(session, test_workspace.id, _MONTH, Decimal("4000.00"), "EUR", reader_currency="BRL")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(statements) == 2
    for statement in statements:
        assert "amount" in statement
        assert "currency" in statement


async def test_the_write_response_converts_to_the_reader_currency(
    session: AsyncSession, test_workspace
):
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date(2026, 4, 1),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.flush()

    read = await set_expected_income(
        session, test_workspace.id, _MONTH, Decimal("10.00"), "USD", reader_currency="BRL"
    )
    assert read.amount == Decimal("10.00")  # the row keeps what was typed
    assert read.currency == "USD"
    assert read.amount_primary == Decimal("50.00")  # priced for the reader

    stored = await get_expected_income(session, test_workspace.id, _MONTH, "BRL")
    assert stored is not None and stored.amount_primary == Decimal("50.00")
