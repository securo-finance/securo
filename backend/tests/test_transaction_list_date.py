"""The transaction list shows the date it files and orders each row by (#1386).

The list buckets and sorts by the reporting date (a reporting override, a
manual bill date, or in accrual mode the card bill's due date). Each row
carries that date as `reporting_date`, so the Date column can show what the
order is built on instead of a purchase date that does not explain it.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.account import Account
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.schemas.account import AccountUpdate
from app.services import account_service, admin_service
from app.services.credit_card_service import compute_effective_date
from app.services.transaction_service import get_transactions


async def _account(session, user_id, workspace_id, name, acc_type, **extra) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        type=acc_type,
        balance=Decimal("0"),
        currency="BRL",
        **extra,
    )
    session.add(account)
    await session.flush()
    return account


async def _tx(session, account, tx_date, description, **extra) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=account.user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal("10.00"),
        amount_primary=Decimal("10.00"),
        currency="BRL",
        date=tx_date,
        type="debit",
        source="manual",
        status="posted",
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    tx.effective_date = extra.get("effective_date") or tx_date
    session.add(tx)
    await session.flush()
    return tx


@pytest_asyncio.fixture
async def card_and_checking(session, test_user, test_workspace):
    # Card closing on the 31st, due on the 10th: a purchase on Mar 30 is
    # paid on Apr 10, so accrual files it in April.
    card = await _account(
        session, test_user.id, test_workspace.id, "Card", "credit_card",
        statement_close_day=31, payment_due_day=10,
    )
    checking = await _account(session, test_user.id, test_workspace.id, "Checking", "checking")
    purchase = await _tx(
        session, card, date(2026, 3, 30), "March purchase",
        effective_date=compute_effective_date(date(2026, 3, 30), 31, 10),
    )
    rent = await _tx(session, checking, date(2026, 4, 5), "April rent")
    await session.commit()
    return card, checking, purchase, rent


@pytest.mark.asyncio
async def test_accrual_list_shows_the_date_it_files_and_orders_by(
    client, session, auth_headers, card_and_checking
):
    _, _, purchase, rent = card_and_checking
    assert purchase.effective_date == date(2026, 4, 10)
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    await session.commit()

    resp = await client.get(
        "/api/transactions?from=2026-04-01&to=2026-04-30", headers=auth_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["description"] for i in items] == ["March purchase", "April rent"]
    # The March purchase sits on April's page, above an April 5 row, because
    # it is filed on its bill date. The row says so.
    assert [i["reporting_date"] for i in items] == ["2026-04-10", "2026-04-05"]
    assert items[0]["date"] == "2026-03-30"

    resp = await client.get(
        "/api/transactions?from=2026-04-01&to=2026-04-30&sort_by=date&sort_dir=asc",
        headers=auth_headers,
    )
    dates = [i["reporting_date"] for i in resp.json()["items"]]
    assert dates == ["2026-04-05", "2026-04-10"]


@pytest.mark.asyncio
async def test_cash_list_files_by_the_purchase_date(
    client, session, auth_headers, card_and_checking
):
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "cash")
    await session.commit()

    resp = await client.get(
        "/api/transactions?from=2026-03-01&to=2026-04-30", headers=auth_headers
    )
    items = resp.json()["items"]
    assert [(i["description"], i["reporting_date"]) for i in items] == [
        ("April rent", "2026-04-05"),
        ("March purchase", "2026-03-30"),
    ]


@pytest.mark.asyncio
async def test_manual_bill_date_is_the_row_date_in_cash_mode(
    session, test_user, test_workspace, card_and_checking
):
    _, _, purchase, _ = card_and_checking
    purchase.effective_bill_date = date(2026, 4, 20)
    await session.commit()

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        from_date=date(2026, 4, 1), to_date=date(2026, 4, 30), accounting_mode="cash",
    )
    assert [(t.description, t.reporting_date) for t in rows] == [
        ("March purchase", date(2026, 4, 20)),
        ("April rent", date(2026, 4, 5)),
    ]


@pytest.mark.asyncio
async def test_statement_view_rows_carry_the_purchase_date(
    session, test_user, test_workspace, card_and_checking
):
    card, _, purchase, _ = card_and_checking
    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id, account_id=card.id,
        from_date=date(2026, 3, 1), to_date=date(2026, 3, 31),
        accounting_mode="accrual", statement_view=True,
    )
    assert [(t.id, t.reporting_date) for t in rows] == [(purchase.id, date(2026, 3, 30))]


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_card_turned_checking_files_rows_by_their_own_date(
    session, test_user, test_workspace, test_connection, connected
):
    extra = {"connection_id": test_connection.id, "external_id": "ext-card"} if connected else {}
    card = await _account(
        session, test_user.id, test_workspace.id, "Itau", "credit_card",
        statement_close_day=1, payment_due_day=5, **extra,
    )
    tx = await _tx(
        session, card, date(2026, 9, 10), "Hotel",
        effective_date=compute_effective_date(date(2026, 9, 10), 1, 5),
    )
    await session.commit()
    assert tx.effective_date == date(2026, 10, 5)

    await account_service.update_account(
        session, card.id, test_workspace.id, AccountUpdate(type="checking")
    )
    stored = await session.scalar(select(Transaction).where(Transaction.id == tx.id))
    assert stored is not None
    assert stored.effective_date == date(2026, 9, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("type_only", [False, True], ids=["with-cycle", "type-only"])
async def test_card_round_trip_keeps_the_linked_bill_date(
    session, test_user, test_workspace, test_connection, connected, type_only
):
    """Card -> checking -> card: a row linked to a bank bill goes back to that
    bill's due date, not to cycle math. The sync only refiles a row when its
    bill_id changes, so nothing else would put it back. The way back may
    resend the cycle days or change the type alone; the account keeps its
    cycle days while it is checking."""
    extra = {"connection_id": test_connection.id, "external_id": "ext-card"} if connected else {}
    card = await _account(
        session, test_user.id, test_workspace.id, "Itau", "credit_card",
        statement_close_day=1, payment_due_day=5, **extra,
    )
    bill = CreditCardBill(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=card.id,
        external_id="bill-apr",
        due_date=date(2026, 4, 10),
        total_amount=Decimal("10.00"),
        currency="BRL",
    )
    session.add(bill)
    await session.flush()
    tx = await _tx(
        session, card, date(2026, 3, 30), "Hotel",
        bill_id=bill.id, effective_date=date(2026, 4, 10),
    )
    await session.commit()

    await account_service.update_account(
        session, card.id, test_workspace.id, AccountUpdate(type="checking")
    )
    stored = await session.scalar(select(Transaction).where(Transaction.id == tx.id))
    assert stored is not None
    assert stored.effective_date == date(2026, 3, 30)

    back = (
        AccountUpdate(type="credit_card")
        if type_only
        else AccountUpdate(type="credit_card", statement_close_day=1, payment_due_day=5)
    )
    await account_service.update_account(session, card.id, test_workspace.id, back)
    stored = await session.scalar(select(Transaction).where(Transaction.id == tx.id))
    assert stored is not None
    # Cycle math would say Apr 5; the bank's bill says Apr 10.
    assert stored.effective_date == date(2026, 4, 10)


@pytest.mark.asyncio
async def test_row_moved_from_another_card_ignores_that_cards_bill(
    session, test_user, test_workspace
):
    """A row moved to another card keeps the `bill_id` of the card it came
    from. Refiling the new card uses its own cycle, not the old card's bill."""
    old_card = await _account(
        session, test_user.id, test_workspace.id, "Old card", "credit_card",
        statement_close_day=1, payment_due_day=5,
    )
    new_card = await _account(
        session, test_user.id, test_workspace.id, "New card", "credit_card",
        statement_close_day=31, payment_due_day=20,
    )
    old_bill = CreditCardBill(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=old_card.id,
        external_id="bill-old",
        due_date=date(2026, 4, 10),
        total_amount=Decimal("10.00"),
        currency="BRL",
    )
    session.add(old_bill)
    await session.flush()
    tx = await _tx(
        session, new_card, date(2026, 3, 30), "Hotel",
        bill_id=old_bill.id, effective_date=date(2026, 4, 10),
    )
    await session.commit()

    await account_service.update_account(
        session, new_card.id, test_workspace.id,
        AccountUpdate(statement_close_day=31, payment_due_day=25),
    )
    stored = await session.scalar(select(Transaction).where(Transaction.id == tx.id))
    assert stored is not None
    assert stored.effective_date == compute_effective_date(date(2026, 3, 30), 31, 25)
    assert stored.effective_date != date(2026, 4, 10)
