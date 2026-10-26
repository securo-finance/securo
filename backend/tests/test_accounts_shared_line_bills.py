"""Issue #684: per-card open bill on shared credit lines in the account list.

Every card on one consolidated line repeats the contract-wide balance, so the
list also reports the card's own open bill (earliest due_date on or after
today). Non-shared cards and shared cards without an open bill get null.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.credit_card_bill import CreditCardBill
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction

SHARED_LINE = "test-provider:line-684"


async def _make_card(
    session: AsyncSession,
    user_id,
    workspace_id,
    name: str,
    external_id: str,
    *,
    shared: bool = True,
    currency: str = "BRL",
    opening: Decimal = Decimal("1500.00"),
) -> Account:
    """A manual credit card with an opening balance transaction."""
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        type="credit_card",
        shared_balance_group=SHARED_LINE if shared else None,
        currency=currency,
        external_id=external_id,
    )
    session.add(account)
    await session.flush()
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            account_id=account.id,
            description="OPENING BALANCE",
            amount=opening,
            date=app_today().replace(day=1),
            type="debit",
            source="opening_balance",
        )
    )
    await session.commit()
    await session.refresh(account)
    return account


async def _add_bill(
    session: AsyncSession,
    user_id,
    workspace_id,
    account_id,
    external_id: str,
    due_in_days: int,
    total,
    currency: str = "BRL",
):
    bill = CreditCardBill(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        external_id=external_id,
        due_date=app_today() + timedelta(days=due_in_days),
        total_amount=Decimal(str(total)),
        currency=currency,
    )
    session.add(bill)
    await session.commit()


@pytest.mark.asyncio
async def test_list_accounts_shared_line_per_card_open_bill(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Each card of a shared line reports its own open bill, not the line's balance."""
    one = await _make_card(session, test_user.id, test_workspace.id, "R684 Card One", "acc-684-1")
    two = await _make_card(session, test_user.id, test_workspace.id, "R684 Card Two", "acc-684-2")
    await _add_bill(session, test_user.id, test_workspace.id, one.id, "bill-1", 5, "820.50")
    await _add_bill(session, test_user.id, test_workspace.id, two.id, "bill-2", 12, "2140.00")

    resp = await client.get("/api/accounts", headers=auth_headers)
    assert resp.status_code == 200
    by_name = {a["name"]: a for a in resp.json()}
    card_one, card_two = by_name["R684 Card One"], by_name["R684 Card Two"]

    # The line's balance is still reported on both cards: that duplication is
    # what the sidebar hides now, not something the API stops sending.
    assert card_one["current_balance"] == pytest.approx(-1500.0)
    assert card_two["current_balance"] == pytest.approx(-1500.0)

    # Each card's row carries its own open bill.
    assert card_one["open_bill_total"] == pytest.approx(820.50)
    assert card_two["open_bill_total"] == pytest.approx(2140.00)
    # BRL is this user's primary currency: no separate primary field.
    assert card_one["open_bill_total_primary"] is None


@pytest.mark.asyncio
async def test_list_accounts_shared_line_earliest_open_bill_wins(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """With several open bills, the earliest due one is the card's open bill."""
    card = await _make_card(session, test_user.id, test_workspace.id, "R684 Card", "acc-684-3")
    await _add_bill(session, test_user.id, test_workspace.id, card.id, "bill-early", 3, "100.00")
    await _add_bill(session, test_user.id, test_workspace.id, card.id, "bill-late", 10, "900.00")

    resp = await client.get("/api/accounts", headers=auth_headers)
    card_row = next(a for a in resp.json() if a["name"] == "R684 Card")
    assert card_row["open_bill_total"] == pytest.approx(100.00)


@pytest.mark.asyncio
async def test_list_accounts_shared_line_no_open_bill_is_null(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Only past-due bills, or no bills at all: the row falls back to the balance."""
    past = await _make_card(session, test_user.id, test_workspace.id, "R684 Card Past", "acc-684-4")
    await _make_card(session, test_user.id, test_workspace.id, "R684 Card Bare", "acc-684-5")
    await _add_bill(session, test_user.id, test_workspace.id, past.id, "bill-past", -2, "300.00")

    resp = await client.get("/api/accounts", headers=auth_headers)
    by_name = {a["name"]: a for a in resp.json()}
    assert by_name["R684 Card Past"]["open_bill_total"] is None
    assert by_name["R684 Card Bare"]["open_bill_total"] is None
    # ...and the balance still shows as before.
    assert by_name["R684 Card Past"]["current_balance"] == pytest.approx(-1500.0)


@pytest.mark.asyncio
async def test_list_accounts_non_shared_card_has_no_open_bill(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """The field is restricted to shared lines: a solo card reports null."""
    card = await _make_card(
        session, test_user.id, test_workspace.id, "R684 Solo Card", "acc-684-6", shared=False
    )
    await _add_bill(session, test_user.id, test_workspace.id, card.id, "bill-solo", 5, "700.00")

    resp = await client.get("/api/accounts", headers=auth_headers)
    card_row = next(a for a in resp.json() if a["name"] == "R684 Solo Card")
    assert card_row["open_bill_total"] is None
    assert card_row["open_bill_total_primary"] is None


@pytest.mark.asyncio
async def test_list_accounts_shared_line_open_bill_foreign_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """A foreign-currency card's bill converts with the same rate as its balance."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    card = await _make_card(
        session, test_user.id, test_workspace.id, "R684 USD Card", "acc-684-7",
        currency="USD", opening=Decimal("100.00"),
    )
    await _add_bill(
        session, test_user.id, test_workspace.id, card.id, "bill-usd", 5, "200.00", currency="USD"
    )

    resp = await client.get("/api/accounts", headers=auth_headers)
    card_row = next(a for a in resp.json() if a["name"] == "R684 USD Card")
    assert card_row["open_bill_total"] == pytest.approx(200.0)
    assert card_row["open_bill_total_primary"] == pytest.approx(1000.0)
    assert card_row["balance_primary"] == pytest.approx(-500.0)
    assert card_row["fx_fallback"] is False

    # The single-account route converts the same way as the list.
    single = (await client.get(f"/api/accounts/{card.id}", headers=auth_headers)).json()
    assert single["open_bill_total_primary"] == pytest.approx(1000.0)
    assert single["balance_primary"] == pytest.approx(-500.0)
