"""The mortgage payment API against real PostgreSQL: foreign-key cascades and the
principal-cleanup trigger only behave like production there."""
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.database import get_async_session
from app.main import app
from app.models.mortgage_payment_allocation import (
    MORTGAGE_PRINCIPAL_SOURCE,
    MortgagePaymentAllocation,
)
from app.models.transaction import Transaction
from tests.test_mortgage_payments_api import (  # noqa: F401 -- rerun on PostgreSQL
    _make_payment_and_loan,
    _post_breakdown,
    test_bank_connected_loan_keeps_split_without_posting_principal,
    test_breakdown_payment_cannot_be_ignored,
    test_breakdown_rows_cannot_become_transfers,
    test_breakdown_can_be_removed_before_editing_payment,
    test_deleting_payment_removes_its_principal_entry,
    test_historical_mortgage_payment_links_without_double_reducing_current_balance,
    test_linked_loan_without_breakdowns_can_become_another_type,
    test_loan_with_breakdowns_cannot_be_deleted_or_retyped,
    test_moving_the_opening_balance_reapplies_the_posting_rule,
    test_payment_after_balance_snapshot_posts_principal_to_loan_ledger,
    test_payment_can_allocate_principal_and_interest_across_loan_parts,
    test_payment_on_snapshot_date_is_already_in_the_balance,
    test_principal_entry_is_owned_by_its_breakdown,
    test_replacing_payment_removes_only_its_previous_principal_transaction,
)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_one_statement_deleting_payment_and_principal_succeeds(
    client, auth_headers, session, test_user, test_workspace
):
    """Deleting a user's or workspace's rows hits the payment and its principal
    entry in one statement; the cleanup trigger must not reject that."""
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)

    await session.execute(delete(Transaction).where(Transaction.user_id == test_user.id))
    await session.commit()

    assert await session.scalar(select(func.count()).select_from(MortgagePaymentAllocation)) == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.source == MORTGAGE_PRINCIPAL_SOURCE)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_concurrent_breakdown_saves_take_turns(
    client, auth_headers, session, test_user, test_workspace
):
    import asyncio

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    body = {
        "allocations": [
            {"loan_account_id": str(loan.id), "principal_amount": 167.76, "interest_amount": 65.07}
        ]
    }

    responses = await asyncio.gather(
        *(
            client.put(f"/api/mortgage-payments/{payment.id}", headers=auth_headers, json=body)
            for _ in range(4)
        )
    )

    assert [response.status_code for response in responses] == [200] * 4
    assert await session.scalar(select(func.count()).select_from(MortgagePaymentAllocation)) == 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.source == MORTGAGE_PRINCIPAL_SOURCE)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_payment_edit_waits_for_a_breakdown_being_saved(
    client, auth_headers, session, postgres_sessions, test_user, test_workspace
):
    """An edit that passed its breakdown check just before a save commits would
    change the payment under a breakdown that no longer matches it. The edit
    has to queue behind the save and then see the breakdown."""
    import asyncio
    from decimal import Decimal

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )

    async with postgres_sessions() as saving:
        await saving.execute(
            select(Transaction.id).where(Transaction.id == payment.id).with_for_update()
        )
        edit = asyncio.create_task(
            client.patch(
                f"/api/transactions/{payment.id}", headers=auth_headers, json={"amount": 999}
            )
        )
        await asyncio.sleep(0.5)
        assert not edit.done()

        saving.add(
            MortgagePaymentAllocation(
                workspace_id=test_workspace.id,
                payment_transaction_id=payment.id,
                loan_account_id=loan.id,
                principal_amount=Decimal("167.76"),
                interest_amount=Decimal("65.07"),
            )
        )
        await saving.commit()
        response = await asyncio.wait_for(edit, timeout=10)

    assert response.status_code == 400
    assert "mortgage breakdown" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_category_waits_for_a_breakdown_being_saved(
    client, auth_headers, session, postgres_sessions, test_user, test_workspace
):
    """Moving payments into an ignored category checks for breakdowns first; a
    breakdown committed between that check and the update must still be seen."""
    import asyncio
    import uuid
    from decimal import Decimal

    from app.models.category import Category

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    ignored = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        is_ignored=True,
    )
    session.add(ignored)
    await session.commit()

    async with postgres_sessions() as saving:
        await saving.execute(
            select(Transaction.id).where(Transaction.id == payment.id).with_for_update()
        )
        bulk = asyncio.create_task(
            client.patch(
                "/api/transactions/bulk-categorize",
                headers=auth_headers,
                json={"transaction_ids": [str(payment.id)], "category_id": str(ignored.id)},
            )
        )
        await asyncio.sleep(0.5)
        assert not bulk.done()

        saving.add(
            MortgagePaymentAllocation(
                workspace_id=test_workspace.id,
                payment_transaction_id=payment.id,
                loan_account_id=loan.id,
                principal_amount=Decimal("167.76"),
                interest_amount=Decimal("65.07"),
            )
        )
        await saving.commit()
        response = await asyncio.wait_for(bulk, timeout=10)

    assert response.status_code == 400
    assert "mortgage breakdown" in response.json()["detail"]
