"""The transfer badge in the transactions list names the linked account.

Issue #810: the list marks transfer rows with a "Transfer" badge but says
nothing about what the row is connected to. The paginated list enriches
each transfer row with ``transfer_counterpart`` (the other leg's
account), one query per page.
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
from app.models.user import User


async def _register(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _tx(
    user: User,
    account: Account,
    description: str,
    amount: Decimal,
    txn_type: str,
    pair_id: uuid.UUID | None,
    user_id=None,
) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=user_id or user.id,
        account_id=account.id,
        description=description,
        amount=amount,
        date=date.today(),
        type=txn_type,
        source="transfer" if pair_id else "manual",
        transfer_pair_id=pair_id,
        created_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def transfer_accounts(
    session: AsyncSession, test_user: User, test_account: Account, test_connection
) -> tuple[Account, Account]:
    """The default account plus a savings account in the same workspace."""
    savings = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-savings",
        name="Conta Poupanca",
        type="savings",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(savings)
    await session.commit()
    await session.refresh(savings)
    return test_account, savings


@pytest_asyncio.fixture
async def transfer_pair(
    session: AsyncSession,
    test_user: User,
    transfer_accounts: tuple[Account, Account],
) -> tuple[Transaction, Transaction, Transaction]:
    """A pair across the two accounts, plus a regular row."""
    checking, savings = transfer_accounts
    pair_id = uuid.uuid4()
    leg_out = _tx(test_user, checking, "Transfer out", Decimal("200.00"), "debit", pair_id)
    leg_in = _tx(test_user, savings, "Transfer in", Decimal("200.00"), "credit", pair_id)
    regular = _tx(test_user, checking, "GROCERIES", Decimal("50.00"), "debit", None)
    for t in (leg_out, leg_in, regular):
        session.add(t)
    await session.commit()
    for t in (leg_out, leg_in, regular):
        await session.refresh(t)
    return leg_out, leg_in, regular


@pytest.mark.asyncio
async def test_transfer_rows_name_the_counterpart_account(
    client: AsyncClient, auth_headers, transfer_pair
):
    leg_out, leg_in, regular = transfer_pair
    resp = await client.get("/api/transactions", headers=auth_headers)
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["items"]}

    out = by_id[str(leg_out.id)]
    assert out["transfer_pair_id"] is not None
    assert out["transfer_counterpart"] is not None
    assert out["transfer_counterpart"]["account_name"] == "Conta Poupanca"
    # The counterpart is the other leg's account, never the row's own.
    assert out["transfer_counterpart"]["account_id"] != out["account_id"]

    inn = by_id[str(leg_in.id)]
    assert inn["transfer_counterpart"]["account_name"] == "Conta Corrente"
    assert inn["transfer_counterpart"]["account_id"] != inn["account_id"]

    assert by_id[str(regular.id)]["transfer_counterpart"] is None


@pytest.mark.asyncio
async def test_counterpart_found_when_other_leg_filtered_off_page(
    client: AsyncClient, auth_headers, transfer_pair, transfer_accounts
):
    """Filtering to one account keeps its leg on the page but not the
    other one; the badge still names the linked account."""
    leg_out, _, _ = transfer_pair
    checking, _ = transfer_accounts
    resp = await client.get(
        f"/api/transactions?account_id={checking.id}", headers=auth_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    row = next(t for t in items if t["id"] == str(leg_out.id))
    assert row["transfer_counterpart"]["account_name"] == "Conta Poupanca"


@pytest.mark.asyncio
async def test_transfer_counterpart_stays_inside_the_workspace(
    client: AsyncClient, auth_headers, session: AsyncSession,
    test_user: User, transfer_pair, transfer_accounts,
):
    """A second workspace holding a row with the same pair id must not
    leak into the first workspace's badge."""
    leg_out, leg_in, _ = transfer_pair
    _, savings = transfer_accounts
    friend_email = "transfer-scope@example.com"
    await _register(client, friend_email, "friendpassword12")
    friend_headers = await _login(client, friend_email, "friendpassword12")

    result = await session.execute(select(User).where(User.email == friend_email))
    friend = result.scalar_one()
    other = Account(
        id=uuid.uuid4(),
        user_id=friend.id,
        external_id="acc-ext-other-ws",
        name="Outra Conta",
        type="checking",
        balance=Decimal("0.00"),
        currency="BRL",
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)

    pair_id = leg_out.transfer_pair_id
    intruder = _tx(friend, other, "Same pair id", Decimal("200.00"), "debit", pair_id, user_id=friend.id)
    session.add(intruder)
    await session.commit()

    resp = await client.get("/api/transactions", headers=auth_headers)
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["items"]}
    assert by_id[str(leg_out.id)]["transfer_counterpart"]["account_name"] == "Conta Poupanca"
    assert by_id[str(leg_in.id)]["transfer_counterpart"]["account_name"] == "Conta Corrente"

    # And the friend's list resolves its leg within its own workspace only.
    resp2 = await client.get("/api/transactions", headers=friend_headers)
    assert resp2.status_code == 200
    friend_rows = {t["id"]: t for t in resp2.json()["items"]}
    assert friend_rows[str(intruder.id)]["transfer_counterpart"] is None


@pytest.mark.asyncio
async def test_counterpart_uses_the_account_display_name(
    client: AsyncClient, auth_headers, session: AsyncSession, transfer_pair, transfer_accounts
):
    """The badge shows the same name as the rest of the UI: the user's
    display name when set, the account name otherwise."""
    leg_out, leg_in, _ = transfer_pair
    _, savings = transfer_accounts
    savings.display_name = "Reserva"
    await session.commit()

    resp = await client.get("/api/transactions", headers=auth_headers)
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["items"]}
    assert by_id[str(leg_out.id)]["transfer_counterpart"]["account_name"] == "Reserva"
    assert by_id[str(leg_in.id)]["transfer_counterpart"]["account_name"] == "Conta Corrente"
