"""Date-only moves keep typed primary conversions across all three paths (ADR 0039, #1816).

A typed conversion is a statement about the amount, not the date:
- moving only the date of a transfer leg keeps the typed figure on BOTH legs,
  while restamping any non-primary foreign account leg to the new date;
- changing a transfer leg's amount still drops the paired leg's typed figure;
- posting a pending row (or transfer pair) dated in the past to today keeps
  the typed figure, while a derived stamp still restamps to today's rate.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

D1 = date(2025, 3, 10)
D2 = date(2025, 3, 20)
D3 = date(2025, 3, 25)


@pytest_asyncio.fixture
async def fx_rates(session: AsyncSession, clean_db) -> None:
    """Distinct USD rates across three past dates so any restamp is observable."""
    # D1: 1 USD = 5 BRL, 0.90 EUR
    # D2: 1 USD = 4 BRL, 0.80 EUR
    # D3: 1 USD = 6 BRL, 0.75 EUR
    for quote, rate, on in (
        ("BRL", Decimal("5"), D1),
        ("EUR", Decimal("0.90"), D1),
        ("BRL", Decimal("4"), D2),
        ("EUR", Decimal("0.80"), D2),
        ("BRL", Decimal("6"), D3),
        ("EUR", Decimal("0.75"), D3),
    ):
        session.add(
            FxRate(base_currency="USD", quote_currency=quote, date=on, rate=rate, source="test")
        )
    await session.commit()


async def _set_display_currency(
    client: AsyncClient, auth_headers: dict, user: User, currency: str
) -> None:
    prefs = {**(user.preferences or {}), "currency_display": currency}
    resp = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert resp.status_code == 200, resp.text


async def _create_account(
    client: AsyncClient, auth_headers: dict, name: str, currency: str
) -> str:
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": name, "type": "checking", "balance": "0", "currency": currency},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _stored(session: AsyncSession, row_id: str) -> Transaction:
    tx = await session.get(Transaction, uuid.UUID(row_id), populate_existing=True)
    assert tx is not None
    return tx


async def _create_usd_transfer_with_typed_brl(
    client: AsyncClient,
    auth_headers: dict,
    user: User,
    *,
    amount: str = "300.00",
    typed_brl: str = "1560.00",
    on_date: date = D1,
    type_both: bool = True,
) -> tuple[str, str]:
    await _set_display_currency(client, auth_headers, user, "BRL")
    usd_from = await _create_account(client, auth_headers, "Wise USD", "USD")
    usd_to = await _create_account(client, auth_headers, "Broker USD", "USD")

    created = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": usd_from,
            "to_account_id": usd_to,
            "amount": amount,
            "date": on_date.isoformat(),
            "description": "USD internal transfer",
        },
    )
    assert created.status_code == 201, created.text
    debit_id = created.json()["debit"]["id"]
    credit_id = created.json()["credit"]["id"]

    for leg_id in (debit_id, credit_id) if type_both else (debit_id,):
        resp = await client.patch(
            f"/api/transactions/{leg_id}",
            headers=auth_headers,
            json={"amount_primary": typed_brl},
        )
        assert resp.status_code == 200, resp.text

    return debit_id, credit_id


@pytest.mark.asyncio
async def test_transfer_date_only_move_keeps_typed_conversion_on_both_legs(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, test_user: User, fx_rates
):
    """Moving only the date of a transfer leg keeps the typed BRL conversion on
    both legs instead of overwriting the counterpart with the new date's market
    rate (#1816)."""
    debit_id, credit_id = await _create_usd_transfer_with_typed_brl(
        client, auth_headers, test_user
    )

    # Pure date-only PATCH
    moved = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"date": D2.isoformat()},
    )
    assert moved.status_code == 200, moved.text

    for leg_id in (debit_id, credit_id):
        tx = await _stored(session, leg_id)
        assert tx.date == D2
        assert tx.amount == Decimal("300.00")
        assert tx.amount_primary == Decimal("1560.00")
        assert tx.amount_primary_typed == Decimal("1560.00")
        assert tx.amount_primary_typed_currency == "BRL"
        assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")

    # Dialog resave that sends the unchanged amount alongside another date move
    moved_again = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"amount": "300.00", "date": D3.isoformat()},
    )
    assert moved_again.status_code == 200, moved_again.text

    for leg_id in (debit_id, credit_id):
        tx = await _stored(session, leg_id)
        assert tx.date == D3
        assert tx.amount_primary == Decimal("1560.00")
        assert tx.amount_primary_typed == Decimal("1560.00")
        assert tx.amount_primary_typed_currency == "BRL"
        assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_transfer_date_only_move_restamps_non_primary_foreign_account_leg(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, test_user: User, fx_rates
):
    """On a paired USD leg held in a EUR account (non-primary foreign account),
    a date-only move keeps the owner's typed BRL conversion while restamping
    `amount_account` to the new date's USD->EUR rate."""
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    usd_account = await _create_account(client, auth_headers, "USD Wallet", "USD")
    eur_account = await _create_account(client, auth_headers, "EUR Account", "EUR")

    debit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": usd_account,
            "description": "Transfer to EUR card",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "amount_primary": "1560.00",
        },
    )
    assert debit_resp.status_code == 201, debit_resp.text
    debit_id = debit_resp.json()["id"]

    credit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": eur_account,
            "description": "Transfer to EUR card",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "credit",
            "amount_primary": "1560.00",
        },
    )
    assert credit_resp.status_code == 201, credit_resp.text
    credit_id = credit_resp.json()["id"]

    if credit_resp.json()["transfer_pair_id"] is None:
        linked = await client.post(
            "/api/transactions/link-transfer",
            headers=auth_headers,
            json={"transaction_ids": [debit_id, credit_id]},
        )
        assert linked.status_code == 200, linked.text

    credit_before = await _stored(session, credit_id)
    assert credit_before.transfer_pair_id is not None
    assert credit_before.amount_account == Decimal("270.00")  # 300 * 0.90 on D1
    assert credit_before.amount_account_source is None

    moved = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"date": D2.isoformat()},
    )
    assert moved.status_code == 200, moved.text

    credit_after = await _stored(session, credit_id)
    assert credit_after.date == D2
    assert credit_after.amount_primary == Decimal("1560.00")
    assert credit_after.amount_primary_typed == Decimal("1560.00")
    assert credit_after.amount_primary_typed_currency == "BRL"
    assert Decimal(str(credit_after.fx_rate_used)) == Decimal("5.2")
    # Account leg restamped to D2's USD->EUR rate (0.80): 300 * 0.80 = 240.00
    assert credit_after.amount_account == Decimal("240.00")
    assert credit_after.amount_account_source is None


@pytest.mark.asyncio
async def test_transfer_date_only_move_keeps_owner_currency_account_leg_on_pair(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, test_user: User, fx_rates
):
    """When the paired leg is a USD transaction on a BRL account (owner's display
    currency), its typed BRL value stays in both `amount_primary` and
    `amount_account` after a date-only move."""
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    usd_account = await _create_account(client, auth_headers, "USD Wallet", "USD")
    brl_account = await _create_account(client, auth_headers, "BRL Account", "BRL")

    debit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": usd_account,
            "description": "Transfer to BRL card",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "amount_primary": "1560.00",
        },
    )
    assert debit_resp.status_code == 201, debit_resp.text
    debit_id = debit_resp.json()["id"]

    credit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": brl_account,
            "description": "Transfer to BRL card",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "credit",
            "amount_primary": "1560.00",
        },
    )
    assert credit_resp.status_code == 201, credit_resp.text
    credit_id = credit_resp.json()["id"]

    if credit_resp.json()["transfer_pair_id"] is None:
        linked = await client.post(
            "/api/transactions/link-transfer",
            headers=auth_headers,
            json={"transaction_ids": [debit_id, credit_id]},
        )
        assert linked.status_code == 200, linked.text

    credit_before = await _stored(session, credit_id)
    assert credit_before.amount_account == Decimal("1560.00")
    assert credit_before.amount_account_source == "user"

    moved = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"date": D2.isoformat()},
    )
    assert moved.status_code == 200, moved.text

    credit_after = await _stored(session, credit_id)
    assert credit_after.date == D2
    assert credit_after.amount_primary == Decimal("1560.00")
    assert credit_after.amount_primary_typed == Decimal("1560.00")
    assert credit_after.amount_primary_typed_currency == "BRL"
    assert credit_after.amount_account == Decimal("1560.00")
    assert credit_after.amount_account_source == "user"


@pytest.mark.asyncio
async def test_transfer_date_only_move_restamps_derived_paired_leg(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, test_user: User, fx_rates
):
    """A paired leg without a typed conversion still follows the new date's rate."""
    debit_id, credit_id = await _create_usd_transfer_with_typed_brl(
        client, auth_headers, test_user, type_both=False
    )

    moved = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"date": D2.isoformat()},
    )
    assert moved.status_code == 200, moved.text

    debit = await _stored(session, debit_id)
    credit = await _stored(session, credit_id)
    assert debit.amount_primary == Decimal("1560.00")
    assert debit.amount_primary_typed == Decimal("1560.00")
    assert credit.date == D2
    assert credit.amount_primary == Decimal("1200.00")  # 300 * 4 on D2
    assert credit.amount_primary_typed is None
    assert credit.amount_primary_typed_currency is None
    assert Decimal(str(credit.fx_rate_used)) == Decimal("4")


@pytest.mark.asyncio
async def test_transfer_amount_edit_drops_paired_leg_typed_conversion(
    client: AsyncClient, session: AsyncSession, auth_headers: dict, test_user: User, fx_rates
):
    """Changing the amount of one transfer leg drops the counterpart's typed
    conversion and restamps it at the market rate."""
    debit_id, credit_id = await _create_usd_transfer_with_typed_brl(
        client, auth_headers, test_user
    )

    edited = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=auth_headers,
        json={"amount": "400.00"},
    )
    assert edited.status_code == 200, edited.text

    credit = await _stored(session, credit_id)
    assert credit.amount == Decimal("400.00")
    assert credit.amount_primary == Decimal("2000.00")  # 400 * 5 on D1
    assert credit.amount_primary_typed is None
    assert credit.amount_primary_typed_currency is None
    assert Decimal(str(credit.fx_rate_used)) == Decimal("5")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account_currency", "expected_account_amount", "expected_account_source"),
    [
        ("USD", None, None),
        ("BRL", Decimal("1560.00"), "user"),
        ("EUR", Decimal("240.00"), None),
    ],
    ids=["same-currency-account", "owner-currency-account", "foreign-non-primary-account"],
)
async def test_post_today_past_pending_row_keeps_typed_conversion(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    fx_rates,
    monkeypatch,
    account_currency: str,
    expected_account_amount: Decimal | None,
    expected_account_source: str | None,
):
    """Posting a past-dated pending row to today keeps its typed primary figure,
    keeps the typed account leg on an owner-currency account, and restamps a
    non-primary foreign account leg to today's rate."""
    monkeypatch.setattr("app.services.transaction_service.app_today", lambda: D2)
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    account_id = await _create_account(
        client, auth_headers, f"Pending {account_currency}", account_currency
    )

    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account_id,
            "description": "Pending hotel",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "status": "pending",
            "amount_primary": "1560.00",
        },
    )
    assert created.status_code == 201, created.text
    tx_id = created.json()["id"]

    posted = await client.post(
        f"/api/transactions/{tx_id}/post-today", headers=auth_headers
    )
    assert posted.status_code == 200, posted.text

    tx = await _stored(session, tx_id)
    assert tx.status == "posted"
    assert tx.date == D2
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_primary_typed == Decimal("1560.00")
    assert tx.amount_primary_typed_currency == "BRL"
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")
    assert tx.amount_account == expected_account_amount
    assert tx.amount_account_source == expected_account_source


@pytest.mark.asyncio
async def test_post_today_past_pending_transfer_keeps_typed_conversion_on_both_legs(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    fx_rates,
    monkeypatch,
):
    """Posting a pending transfer pair dated in the past to today keeps the
    typed conversion on both legs."""
    monkeypatch.setattr("app.services.transaction_service.app_today", lambda: D2)
    debit_id, credit_id = await _create_usd_transfer_with_typed_brl(
        client, auth_headers, test_user
    )

    for leg_id in (debit_id, credit_id):
        tx = await session.get(Transaction, uuid.UUID(leg_id))
        assert tx is not None
        tx.status = "pending"
    await session.commit()

    posted = await client.post(
        f"/api/transactions/{debit_id}/post-today", headers=auth_headers
    )
    assert posted.status_code == 200, posted.text

    for leg_id in (debit_id, credit_id):
        tx = await _stored(session, leg_id)
        assert tx.status == "posted"
        assert tx.date == D2
        assert tx.amount_primary == Decimal("1560.00")
        assert tx.amount_primary_typed == Decimal("1560.00")
        assert tx.amount_primary_typed_currency == "BRL"
        assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_post_today_past_pending_derived_row_restamps_to_today_rate(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    fx_rates,
    monkeypatch,
):
    """A pending row with a derived (non-typed) stamp still restamps to today's
    rate when posted today."""
    monkeypatch.setattr("app.services.transaction_service.app_today", lambda: D3)
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    usd_account = await _create_account(client, auth_headers, "USD Derived", "USD")

    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": usd_account,
            "description": "Pending derived flight",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "status": "pending",
        },
    )
    assert created.status_code == 201, created.text
    tx_id = created.json()["id"]

    before = await _stored(session, tx_id)
    assert before.amount_primary == Decimal("1500.00")  # 300 * 5 on D1
    assert before.amount_primary_typed is None

    posted = await client.post(
        f"/api/transactions/{tx_id}/post-today", headers=auth_headers
    )
    assert posted.status_code == 200, posted.text

    after = await _stored(session, tx_id)
    assert after.status == "posted"
    assert after.date == D3
    assert after.amount_primary == Decimal("1800.00")  # 300 * 6 on D3
    assert after.amount_primary_typed is None
    assert after.amount_primary_typed_currency is None
    assert Decimal(str(after.fx_rate_used)) == Decimal("6")


async def _add_eur_editor(
    client: AsyncClient, session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[User, dict]:
    """Add a second editor member whose display currency is EUR to the workspace."""
    email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    password = "memberpassword12"
    reg = await client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert reg.status_code in (200, 201), reg.text
    member = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    member.preferences = {**(member.preferences or {}), "currency_display": "EUR"}
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=member.id,
            role="editor",
        )
    )
    await session.commit()

    login = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 200, login.text
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Workspace-Id": str(workspace_id),
    }
    return member, headers


@pytest.mark.asyncio
async def test_shared_workspace_member_transfer_date_move_keeps_owners_typed_conversion(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    fx_rates,
):
    """When a EUR workspace member moves the date of their transfer leg, a paired
    leg owned by `test_user` (BRL) keeps `test_user`'s typed BRL conversion while
    restamping its non-primary foreign account leg to the new date."""
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    member, member_headers = await _add_eur_editor(client, session, test_workspace.id)

    usd_account = await _create_account(client, member_headers, "Member USD", "USD")
    eur_account = await _create_account(client, auth_headers, "Owner EUR", "EUR")

    debit_resp = await client.post(
        "/api/transactions",
        headers=member_headers,
        json={
            "account_id": usd_account,
            "description": "Shared transfer",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
        },
    )
    assert debit_resp.status_code == 201, debit_resp.text
    debit_id = debit_resp.json()["id"]

    credit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": eur_account,
            "description": "Shared transfer",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "credit",
            "amount_primary": "1560.00",
        },
    )
    assert credit_resp.status_code == 201, credit_resp.text
    credit_id = credit_resp.json()["id"]

    if credit_resp.json()["transfer_pair_id"] is None:
        linked = await client.post(
            "/api/transactions/link-transfer",
            headers=member_headers,
            json={"transaction_ids": [debit_id, credit_id]},
        )
        assert linked.status_code == 200, linked.text

    debit_before = await _stored(session, debit_id)
    credit_before = await _stored(session, credit_id)
    assert debit_before.user_id == member.id
    assert credit_before.user_id == test_user.id
    assert credit_before.amount_account == Decimal("270.00")  # 300 * 0.90 on D1

    moved = await client.patch(
        f"/api/transactions/{debit_id}",
        headers=member_headers,
        json={"date": D2.isoformat()},
    )
    assert moved.status_code == 200, moved.text

    debit_after = await _stored(session, debit_id)
    assert debit_after.date == D2
    assert debit_after.amount_primary == Decimal("240.00")  # 300 * 0.80 on D2 for EUR member

    credit_after = await _stored(session, credit_id)
    assert credit_after.date == D2
    assert credit_after.amount_primary == Decimal("1560.00")
    assert credit_after.amount_primary_typed == Decimal("1560.00")
    assert credit_after.amount_primary_typed_currency == "BRL"
    assert Decimal(str(credit_after.fx_rate_used)) == Decimal("5.2")
    assert credit_after.amount_account == Decimal("240.00")  # 300 * 0.80 on D2
    assert credit_after.amount_account_source is None


@pytest.mark.asyncio
async def test_shared_workspace_member_post_today_keeps_owners_typed_conversion(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    fx_rates,
    monkeypatch,
):
    """A second workspace member (EUR) calling post-today on `test_user`'s (BRL)
    pending typed row preserves `test_user`'s typed BRL conversion."""
    monkeypatch.setattr("app.services.transaction_service.app_today", lambda: D2)
    await _set_display_currency(client, auth_headers, test_user, "BRL")
    _, member_headers = await _add_eur_editor(client, session, test_workspace.id)

    usd_account = await _create_account(client, auth_headers, "Owner USD Pending", "USD")
    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": usd_account,
            "description": "Shared pending hotel",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "status": "pending",
            "amount_primary": "1560.00",
        },
    )
    assert created.status_code == 201, created.text
    tx_id = created.json()["id"]

    posted = await client.post(
        f"/api/transactions/{tx_id}/post-today", headers=member_headers
    )
    assert posted.status_code == 200, posted.text

    tx = await _stored(session, tx_id)
    assert tx.user_id == test_user.id
    assert tx.status == "posted"
    assert tx.date == D2
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_primary_typed == Decimal("1560.00")
    assert tx.amount_primary_typed_currency == "BRL"
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_post_today_mixed_manual_and_synced_transfer_pair(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    fx_rates,
    monkeypatch,
):
    """Posting a manual pending transfer leg paired with a synchronized leg keeps
    the manual leg's typed conversion while updating only status on the synced leg."""
    monkeypatch.setattr("app.services.transaction_service.app_today", lambda: D2)
    await _set_display_currency(client, auth_headers, test_user, "BRL")

    manual_account = await _create_account(client, auth_headers, "Manual USD", "USD")
    sync_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced USD",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(sync_account)
    await session.flush()

    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": manual_account,
            "description": "Mixed transfer",
            "amount": "300.00",
            "currency": "USD",
            "date": D1.isoformat(),
            "type": "debit",
            "status": "pending",
            "amount_primary": "1560.00",
        },
    )
    assert created.status_code == 201, created.text
    manual_id = created.json()["id"]

    pair_id = uuid.uuid4()
    manual_tx = await session.get(Transaction, uuid.UUID(manual_id))
    assert manual_tx is not None
    manual_tx.transfer_pair_id = pair_id

    synced_tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=sync_account.id,
        description="Mixed transfer",
        amount=Decimal("300.00"),
        currency="USD",
        date=D1,
        effective_date=D1,
        type="credit",
        source="sync",
        status="pending",
        transfer_pair_id=pair_id,
        amount_primary=Decimal("1650.00"),
        fx_rate_used=Decimal("5.5"),
    )
    session.add(synced_tx)
    await session.commit()

    posted = await client.post(
        f"/api/transactions/{manual_id}/post-today", headers=auth_headers
    )
    assert posted.status_code == 200, posted.text

    manual_after = await _stored(session, manual_id)
    assert manual_after.status == "posted"
    assert manual_after.date == D2
    assert manual_after.amount_primary == Decimal("1560.00")
    assert manual_after.amount_primary_typed == Decimal("1560.00")
    assert manual_after.amount_primary_typed_currency == "BRL"
    assert Decimal(str(manual_after.fx_rate_used)) == Decimal("5.2")

    synced_after = await _stored(session, str(synced_tx.id))
    assert synced_after.status == "posted"
    assert synced_after.date == D1
    assert synced_after.effective_date == D1
    assert synced_after.amount_primary == Decimal("1650.00")
    assert synced_after.amount_primary_typed is None
    assert Decimal(str(synced_after.fx_rate_used)) == Decimal("5.5")
