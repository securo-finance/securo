import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.mortgage_payment_allocation import (
    MORTGAGE_PRINCIPAL_SOURCE,
    MortgagePaymentAllocation,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


async def _make_payment_and_loan(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    *,
    snapshot_date: date,
):
    property_asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Home",
        type="real_estate",
        currency="EUR",
        valuation_method="manual",
    )
    session.add(property_asset)
    await session.flush()
    bank_account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Current",
        type="checking",
        balance=Decimal("0"),
        currency="EUR",
    )
    loan_account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Mortgage",
        type="loan",
        balance=Decimal("48802.47"),
        currency="EUR",
        secured_asset_id=property_asset.id,
    )
    session.add_all([bank_account, loan_account])
    await session.flush()
    payment = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=bank_account.id,
        description="Nationale-Nederlanden",
        amount=Decimal("232.83"),
        currency="EUR",
        date=date(2026, 6, 2),
        effective_date=date(2026, 6, 2),
        type="debit",
        source="sync",
        status="posted",
    )
    snapshot = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=loan_account.id,
        description="Saldo initial",
        amount=Decimal("48802.47"),
        currency="EUR",
        date=snapshot_date,
        effective_date=snapshot_date,
        type="debit",
        source="opening_balance",
        status="posted",
    )
    session.add_all([payment, snapshot])
    await session.commit()
    return payment, loan_account


@pytest.mark.asyncio
async def test_historical_mortgage_payment_links_without_double_reducing_current_balance(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 9, 29),
    )

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["principal_total"] == 167.76
    assert response.json()["allocations"][0]["historical"] is True
    allocation = await session.scalar(
        select(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id,
        )
    )
    assert allocation is not None
    assert allocation.loan_transaction_id is None
    loan_transactions = await session.scalars(
        select(Transaction).where(Transaction.account_id == loan.id)
    )
    assert [(tx.type, tx.amount) for tx in loan_transactions.all()] == [
        ("debit", Decimal("48802.47")),
    ]


@pytest.mark.asyncio
async def test_payment_after_balance_snapshot_posts_principal_to_loan_ledger(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    allocation = await session.scalar(
        select(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id,
        )
    )
    assert allocation is not None and allocation.loan_transaction_id is not None
    loan_transaction = await session.get(Transaction, allocation.loan_transaction_id)
    assert loan_transaction is not None
    assert loan_transaction.type == "credit"
    assert loan_transaction.amount == Decimal("167.76")
    assert response.json()["allocations"][0]["historical"] is False


@pytest.mark.asyncio
async def test_payment_can_allocate_principal_and_interest_across_loan_parts(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, first_loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )
    second_loan = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Mortgage part 2",
        type="loan",
        balance=Decimal("10000.00"),
        currency="EUR",
        secured_asset_id=first_loan.secured_asset_id,
    )
    session.add(second_loan)
    await session.commit()

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(first_loan.id),
                    "principal_amount": 100,
                    "interest_amount": 20,
                },
                {
                    "loan_account_id": str(second_loan.id),
                    "principal_amount": 67.76,
                    "interest_amount": 45.07,
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["principal_total"] == 167.76
    assert body["interest_total"] == 65.07
    assert len(body["allocations"]) == 2
    allocations = (
        await session.scalars(
            select(MortgagePaymentAllocation).where(
                MortgagePaymentAllocation.payment_transaction_id == payment.id,
            )
        )
    ).all()
    assert {row.loan_account_id for row in allocations} == {first_loan.id, second_loan.id}
    for row in allocations:
        assert row.loan_transaction_id is not None
        loan_transaction = await session.get(Transaction, row.loan_transaction_id)
        assert loan_transaction is not None
        assert loan_transaction.account_id == row.loan_account_id
        assert loan_transaction.amount == row.principal_amount
        assert loan_transaction.type == "credit"


@pytest.mark.asyncio
async def test_pending_payment_does_not_post_a_repayment(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )
    payment.status = "pending"
    await session.commit()

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert "posted" in response.json()["detail"]
    assert (
        await session.scalars(
            select(MortgagePaymentAllocation).where(
                MortgagePaymentAllocation.payment_transaction_id == payment.id,
            )
        )
    ).all() == []


@pytest.mark.asyncio
async def test_interest_only_payment_has_no_zero_value_loan_transaction(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 0,
                    "interest_amount": 232.83,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["allocations"][0]["historical"] is False
    allocation = await session.scalar(
        select(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id,
        )
    )
    assert allocation is not None
    assert allocation.loan_transaction_id is None
    assert (
        len(
            (
                await session.scalars(
                    select(Transaction).where(
                        Transaction.account_id == loan.id,
                    )
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_replacing_payment_removes_only_its_previous_principal_transaction(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )
    url = f"/api/mortgage-payments/{payment.id}"
    first = await client.put(
        url,
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )
    assert first.status_code == 200, first.text
    original = await session.scalar(
        select(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id,
        )
    )
    assert original is not None
    original_transaction_id = original.loan_transaction_id
    assert original_transaction_id is not None

    replacement = await client.put(
        url,
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 150,
                    "interest_amount": 82.83,
                }
            ]
        },
    )

    assert replacement.status_code == 200, replacement.text
    assert await session.get(Transaction, original_transaction_id) is None
    loan_transactions = (
        await session.scalars(
            select(Transaction).where(
                Transaction.account_id == loan.id,
            )
        )
    ).all()
    assert sorted((tx.type, tx.amount) for tx in loan_transactions) == [
        ("credit", Decimal("150.00")),
        ("debit", Decimal("48802.47")),
    ]


@pytest.mark.asyncio
async def test_payment_rejects_fractional_cents(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": "167.765",
                    "interest_amount": "65.065",
                }
            ]
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_breakdown_can_be_removed_before_editing_payment(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session,
        test_user,
        test_workspace,
        snapshot_date=date(2026, 5, 31),
    )
    url = f"/api/mortgage-payments/{payment.id}"
    created = await client.put(
        url,
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )
    assert created.status_code == 200, created.text

    blocked = await client.patch(
        f"/api/transactions/{payment.id}",
        headers=auth_headers,
        json={"amount": 233.83},
    )
    assert blocked.status_code == 400
    assert "Remove the mortgage breakdown" in blocked.json()["detail"]

    removed = await client.delete(url, headers=auth_headers)
    assert removed.status_code == 204, removed.text
    assert (await client.get(url, headers=auth_headers)).json()["allocations"] == []
    assert (
        await session.scalars(
            select(Transaction).where(
                Transaction.account_id == loan.id,
                Transaction.source == MORTGAGE_PRINCIPAL_SOURCE,
            )
        )
    ).all() == []

    changed = await client.patch(
        f"/api/transactions/{payment.id}",
        headers=auth_headers,
        json={"amount": 233.83},
    )
    assert changed.status_code == 200, changed.text


async def _post_breakdown(client: AsyncClient, auth_headers, payment, loan):
    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _principal_entry(session: AsyncSession, payment) -> Transaction:
    allocation = await session.scalar(
        select(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id,
        )
    )
    assert allocation is not None and allocation.loan_transaction_id is not None
    entry = await session.get(Transaction, allocation.loan_transaction_id)
    assert entry is not None
    return entry


@pytest.mark.asyncio
async def test_principal_entry_is_owned_by_its_breakdown(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)
    entry = await _principal_entry(session, payment)
    assert entry.source == MORTGAGE_PRINCIPAL_SOURCE
    assert entry.exclude_from_pnl is True

    edited = await client.patch(
        f"/api/transactions/{entry.id}", headers=auth_headers, json={"amount": 100}
    )
    assert edited.status_code == 400
    deleted = await client.delete(f"/api/transactions/{entry.id}", headers=auth_headers)
    assert deleted.status_code == 400
    bulk = await client.post(
        "/api/transactions/bulk-delete",
        headers=auth_headers,
        json={"transaction_ids": [str(entry.id)]},
    )
    assert bulk.status_code == 400

    breakdown = (
        await client.get(f"/api/mortgage-payments/{payment.id}", headers=auth_headers)
    ).json()
    assert breakdown["principal_total"] == 167.76
    stored = await session.scalar(select(Transaction.amount).where(Transaction.id == entry.id))
    assert stored == Decimal("167.76")


@pytest.mark.asyncio
async def test_deleting_payment_removes_its_principal_entry(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)
    entry = await _principal_entry(session, payment)

    response = await client.delete(f"/api/transactions/{payment.id}", headers=auth_headers)

    assert response.status_code == 204, response.text
    assert await session.scalar(select(Transaction.id).where(Transaction.id == entry.id)) is None


@pytest.mark.asyncio
async def test_payment_on_snapshot_date_is_already_in_the_balance(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 6, 2)
    )

    body = await _post_breakdown(client, auth_headers, payment, loan)

    assert body["allocations"][0]["historical"] is True
    entries = (
        await session.scalars(
            select(Transaction).where(Transaction.source == MORTGAGE_PRINCIPAL_SOURCE)
        )
    ).all()
    assert entries == []


@pytest.mark.asyncio
async def test_bank_connected_loan_keeps_split_without_posting_principal(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    from datetime import datetime, timezone

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Lender",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    loan.connection_id = connection.id
    await session.commit()

    body = await _post_breakdown(client, auth_headers, payment, loan)

    row = body["allocations"][0]
    assert row["bank_managed"] is True
    assert row["historical"] is False
    assert body["principal_total"] == 167.76
    entries = (
        await session.scalars(
            select(Transaction).where(Transaction.source == MORTGAGE_PRINCIPAL_SOURCE)
        )
    ).all()
    assert entries == []


@pytest.mark.asyncio
async def test_loan_with_breakdowns_cannot_be_deleted_or_retyped(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)

    deleted = await client.delete(f"/api/accounts/{loan.id}", headers=auth_headers)
    assert deleted.status_code == 400
    retyped = await client.patch(
        f"/api/accounts/{loan.id}", headers=auth_headers, json={"type": "checking"}
    )
    assert retyped.status_code == 400
    assert "mortgage payment breakdowns" in retyped.json()["detail"]

    removed = await client.delete(f"/api/mortgage-payments/{payment.id}", headers=auth_headers)
    assert removed.status_code == 204
    assert (
        await client.delete(f"/api/accounts/{loan.id}", headers=auth_headers)
    ).status_code == 204


@pytest.mark.asyncio
async def test_linked_loan_without_breakdowns_can_become_another_type(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    _, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )

    response = await client.patch(
        f"/api/accounts/{loan.id}", headers=auth_headers, json={"type": "checking"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["type"] == "checking"
    assert response.json()["secured_asset_id"] is None


@pytest.mark.asyncio
async def test_breakdown_rows_cannot_become_transfers(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)
    entry = await _principal_entry(session, payment)
    other = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=loan.id,
        description="Lender credit",
        amount=Decimal("232.83"),
        currency="EUR",
        date=date(2026, 6, 2),
        effective_date=date(2026, 6, 2),
        type="credit",
        source="manual",
        status="posted",
    )
    session.add(other)
    await session.commit()

    linked = await client.post(
        "/api/transactions/link-transfer",
        headers=auth_headers,
        json={"transaction_ids": [str(payment.id), str(other.id)]},
    )
    assert linked.status_code == 400
    counterpart = await client.post(
        f"/api/transactions/{payment.id}/create-counterpart",
        headers=auth_headers,
        json={"to_account_id": str(loan.id)},
    )
    assert counterpart.status_code == 400
    principal_counterpart = await client.post(
        f"/api/transactions/{entry.id}/create-counterpart",
        headers=auth_headers,
        json={"to_account_id": str(payment.account_id)},
    )
    assert principal_counterpart.status_code == 400

    from app.services.transfer_detection_service import _pool

    accounts = {
        account.id: account
        for account in (
            await session.scalars(select(Account).where(Account.workspace_id == test_workspace.id))
        ).all()
    }
    pool_ids = {tx.id for tx in await _pool(session, test_workspace.id, set(), [], accounts)}
    assert payment.id not in pool_ids
    assert entry.id not in pool_ids
    assert other.id in pool_ids


@pytest.mark.asyncio
async def test_breakdown_payment_cannot_be_ignored(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    from app.models.category import Category

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)
    entry = await _principal_entry(session, payment)
    ignored_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        is_ignored=True,
    )
    session.add(ignored_category)
    await session.commit()

    for tx_id in (payment.id, entry.id):
        toggled = await client.patch(f"/api/transactions/{tx_id}/ignore", headers=auth_headers)
        assert toggled.status_code == 400
        flagged = await client.patch(
            f"/api/transactions/{tx_id}", headers=auth_headers, json={"is_ignored": True}
        )
        assert flagged.status_code == 400
    moved = await client.patch(
        f"/api/transactions/{payment.id}",
        headers=auth_headers,
        json={"category_id": str(ignored_category.id)},
    )
    assert moved.status_code == 400
    bulk = await client.patch(
        "/api/transactions/bulk-categorize",
        headers=auth_headers,
        json={"transaction_ids": [str(payment.id)], "category_id": str(ignored_category.id)},
    )
    assert bulk.status_code == 400


@pytest.mark.asyncio
async def test_ignored_payments_cannot_be_allocated_or_uncategorized(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    from app.models.category import Category

    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    ignored_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        is_ignored=True,
    )
    session.add(ignored_category)
    payment.category_id = ignored_category.id
    await session.commit()

    allocation = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )
    assert allocation.status_code == 400

    # Simulate an older or externally imported breakdown attached before the
    # category was marked ignored: moving it out must also preserve the guard.
    payment.category_id = None
    await session.commit()
    await _post_breakdown(client, auth_headers, payment, loan)
    payment.category_id = ignored_category.id
    await session.commit()
    moved = await client.patch(
        f"/api/transactions/{payment.id}", headers=auth_headers, json={"category_id": None}
    )
    assert moved.status_code == 400


@pytest.mark.asyncio
async def test_moving_the_opening_balance_reapplies_the_posting_rule(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    payment, loan = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 5, 31)
    )
    await _post_breakdown(client, auth_headers, payment, loan)

    async def principal_rows():
        return (
            await session.scalars(
                select(Transaction.amount).where(
                    Transaction.account_id == loan.id,
                    Transaction.source == MORTGAGE_PRINCIPAL_SOURCE,
                )
            )
        ).all()

    assert await principal_rows() == [Decimal("167.76")]

    # A June snapshot already includes the June payment's repayment.
    later = await client.patch(
        f"/api/accounts/{loan.id}",
        headers=auth_headers,
        json={"balance": 48634.71, "balance_date": "2026-06-30"},
    )
    assert later.status_code == 200, later.text
    assert await principal_rows() == []
    breakdown = (
        await client.get(f"/api/mortgage-payments/{payment.id}", headers=auth_headers)
    ).json()
    assert breakdown["allocations"][0]["historical"] is True
    assert breakdown["principal_total"] == 167.76

    earlier = await client.patch(
        f"/api/accounts/{loan.id}",
        headers=auth_headers,
        json={"balance": 48802.47, "balance_date": "2026-05-31"},
    )
    assert earlier.status_code == 200, earlier.text
    assert await principal_rows() == [Decimal("167.76")]


async def _other_workspace(session: AsyncSession, user: User) -> Workspace:
    """A second workspace the same user owns, to probe the workspace boundary."""
    other = Workspace(
        id=uuid.uuid4(), name="Other household", kind="personal", created_by_user_id=user.id
    )
    session.add(other)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=other.id, user_id=user.id, role="owner"
        )
    )
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_a_breakdown_of_another_workspace_is_not_reachable(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
):
    """Every route scopes the payment to the active workspace.

    The same user owns both workspaces here, so only the scoping keeps one
    out of the other; membership alone would let all three routes through.
    """
    other = await _other_workspace(session, test_user)
    payment, loan = await _make_payment_and_loan(
        session, test_user, other, snapshot_date=date(2026, 9, 29)
    )
    body = {
        "allocations": [
            {
                "loan_account_id": str(loan.id),
                "principal_amount": 167.76,
                "interest_amount": 65.07,
            }
        ]
    }
    owner_headers = {**auth_headers, "X-Workspace-Id": str(other.id)}

    read = await client.get(f"/api/mortgage-payments/{payment.id}", headers=auth_headers)
    write = await client.put(
        f"/api/mortgage-payments/{payment.id}", headers=auth_headers, json=body
    )
    remove = await client.delete(
        f"/api/mortgage-payments/{payment.id}", headers=auth_headers
    )
    from_owner = await client.put(
        f"/api/mortgage-payments/{payment.id}", headers=owner_headers, json=body
    )

    assert read.status_code == 404
    assert write.status_code == 404
    assert remove.status_code == 404
    assert from_owner.status_code == 200, from_owner.text
    assert await session.scalar(
        select(MortgagePaymentAllocation.workspace_id).where(
            MortgagePaymentAllocation.payment_transaction_id == payment.id
        )
    ) == other.id


@pytest.mark.asyncio
async def test_a_loan_from_another_workspace_cannot_take_a_part(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    """The loan id arrives from the client, so a payment here must not be able
    to name a loan over there and move its balance."""
    payment, _ = await _make_payment_and_loan(
        session, test_user, test_workspace, snapshot_date=date(2026, 9, 29)
    )
    other = await _other_workspace(session, test_user)
    _, other_loan = await _make_payment_and_loan(
        session, test_user, other, snapshot_date=date(2026, 9, 29)
    )

    response = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(other_loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )

    assert response.status_code == 400
    assert "loan in this workspace" in response.json()["detail"]
    assert await session.scalar(
        select(Transaction.id).where(
            Transaction.account_id == other_loan.id,
            Transaction.source == MORTGAGE_PRINCIPAL_SOURCE,
        )
    ) is None
