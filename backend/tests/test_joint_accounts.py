"""Tests for cross-connection joint account deduplication and failover (#1341)."""
from __future__ import annotations

import uuid
import warnings
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SAWarning
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.account_connection_link import AccountConnectionLink
from app.models.bank_connection import BankConnection
from app.models.institution import Institution
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.providers.base import (
    AccountData,
    ConnectionData,
    TransactionData,
    hash_account_identifier,
)
from app.services import connection_service


def _patch_sync_helpers():
    return (
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_cross_connection_joint_account_linking_and_failover(
    session: AsyncSession, test_user: User, test_workspace
):
    """A second workspace member connecting the same joint account creates a
    secondary link without duplicating transactions, and fails over if primary
    expires (#1341)."""
    partner = User(
        id=uuid.uuid4(),
        email=f"partner-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(partner)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=partner.id,
            role="editor",
        )
    )

    fp = hash_account_identifier(
        "enable_banking", "DE89370400440532013001", institution="DE:jointbank"
    )
    assert fp is not None
    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="sess-a",
        institution_name="JointBank",
        credentials={"session_id_enc": "a"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=partner.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="sess-b",
        institution_name="JointBank",
        credentials={"session_id_enc": "b"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    joint_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="uid-a",
        stable_external_id="st-a",
        account_fingerprint=fp,
        masked_number="3001",
        name="Joint Checking",
        type="checking",
        balance=Decimal("500.00"),
        currency="EUR",
    )
    session.add(joint_acc)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            connection_id=conn_a.id,
            external_id="uid-a",
            stable_external_id="st-a",
            account_fingerprint=fp,
            is_primary_feed=True,
            status="active",
        )
    )
    await session.commit()

    partner_acc_data = AccountData(
        external_id="uid-b",
        stable_external_id="st-b",
        account_fingerprint=fp,
        masked_number="3001",
        name="Joint Checking",
        type="checking",
        balance=Decimal("525.00"),
        currency="EUR",
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"session_id_enc": "b"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[partner_acc_data])
    mock_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="tx-from-b",
                description="Groceries",
                amount=Decimal("25.00"),
                date=date(2026, 10, 10),
                type="debit",
                currency="EUR",
            )
        ]
    )
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, partner.id
        )

    # Primary is still active: no duplicate account, no duplicate tx, user_id preserved.
    accs = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(accs) == 1
    assert accs[0].connection_id == conn_a.id
    assert accs[0].user_id == test_user.id
    assert accs[0].balance == Decimal("525.00")
    mock_provider.get_transactions.assert_not_awaited()

    links = (
        await session.execute(
            select(AccountConnectionLink).where(
                AccountConnectionLink.account_id == joint_acc.id
            )
        )
    ).scalars().all()
    assert len(links) == 2
    by_conn = {link.connection_id: link for link in links}
    assert by_conn[conn_a.id].is_primary_feed is True
    assert by_conn[conn_b.id].is_primary_feed is False
    assert by_conn[conn_b.id].status == "active"

    # Now expire primary connection and sync conn_b -> failover promotes conn_b.
    conn_a.status = "expired"
    await session.commit()

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, partner.id
        )

    await session.refresh(joint_acc)
    assert joint_acc.connection_id == conn_b.id
    assert joint_acc.external_id == "uid-b"
    mock_provider.get_transactions.assert_awaited_once()
    txns = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == joint_acc.id,
                Transaction.external_id == "tx-from-b",
            )
        )
    ).scalars().all()
    assert len(txns) == 1


@pytest.mark.asyncio
async def test_pending_candidate_confirm_and_reject_flow(
    session: AsyncSession, test_user: User, test_workspace
):
    """Lower-confidence match creates pending link; rejecting allows separate account."""
    conn1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="p1",
        institution_name="Nubank",
        credentials={"token": "1"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="p2",
        institution_name="Nubank",
        credentials={"token": "2"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn1, conn2])
    await session.flush()
    acc1 = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn1.id,
        external_id="ext-1",
        masked_number="4321",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("100.00"),
        currency="BRL",
    )
    session.add(acc1)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=acc1.id,
            connection_id=conn1.id,
            external_id="ext-1",
            is_primary_feed=True,
            status="active",
        )
    )
    await session.commit()

    incoming = AccountData(
        external_id="ext-2",
        masked_number="4321",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("200.00"),
        currency="BRL",
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "2"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[incoming])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await connection_service.sync_connection(
            session, conn2.id, test_workspace.id, test_user.id
        )

    pending = (
        await session.execute(
            select(AccountConnectionLink).where(
                AccountConnectionLink.connection_id == conn2.id
            )
        )
    ).scalar_one()
    assert pending.status == "pending"
    assert pending.account_id == acc1.id

    rejected = await connection_service.resolve_account_link(
        session, test_workspace.id, conn2.id, pending.id, "reject"
    )
    assert rejected.status == "rejected"

    # Next sync creates a separate Account for conn2 while keeping the rejected row.
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await connection_service.sync_connection(
            session, conn2.id, test_workspace.id, test_user.id
        )

    accs = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(accs) == 2


@pytest.mark.asyncio
async def test_find_synced_duplicate_path_4b_booked_rotated_uid(
    session: AsyncSession, test_user: User, test_workspace
):
    """Path 4b deduplicates a booked row without entry_reference when external_id rotates,
    but fails closed when an identical twin exists in the incoming batch."""
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="EUR",
    )
    session.add(acc)
    await session.flush()
    existing = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=acc.id,
        external_id="old-fp-1",
        description="Coffee Shop Berlin",
        original_description="Coffee Shop Berlin",
        amount=Decimal("4.50"),
        currency="EUR",
        date=date(2026, 10, 15),
        type="debit",
        source="sync",
        status="posted",
        raw_data={
            "transaction_amount": {"amount": "4.50", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-15",
            "creditor": {"name": "Coffee Shop"},
        },
        created_at=datetime.now(timezone.utc),
    )
    session.add(existing)
    await session.commit()

    rotated = TransactionData(
        external_id="new-fp-1",
        description="Coffee Shop Berlin",
        amount=Decimal("4.50"),
        date=date(2026, 10, 15),
        type="debit",
        currency="EUR",
        status="posted",
        raw_data={
            "transaction_amount": {"amount": "4.50", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-15",
            "creditor": {"name": "Coffee Shop"},
        },
    )
    dup = await connection_service._find_synced_duplicate(
        session,
        acc.id,
        rotated,
        {"new-fp-1"},
        live_external_ids={"new-fp-1"},
        incoming_batch=[rotated],
        provider="enable_banking",
    )
    assert dup is not None and dup.id == existing.id

    # Non-enable_banking providers (e.g. Pluggy) must not match Path 4b unless failover is active.
    assert (
        await connection_service._find_synced_duplicate(
            session,
            acc.id,
            rotated,
            {"new-fp-1"},
            incoming_batch=[rotated],
            provider="pluggy",
        )
        is None
    )

    # Two identical booked charges in the same batch must fail closed.
    twin = TransactionData(
        external_id="new-fp-2",
        description="Coffee Shop Berlin",
        amount=Decimal("4.50"),
        date=date(2026, 10, 15),
        type="debit",
        currency="EUR",
        status="posted",
        raw_data=rotated.raw_data,
    )
    dup_twin = await connection_service._find_synced_duplicate(
        session,
        acc.id,
        rotated,
        {"new-fp-1", "new-fp-2"},
        live_external_ids={"new-fp-1", "new-fp-2"},
        incoming_batch=[rotated, twin],
        provider="enable_banking",
    )
    assert dup_twin is None


@pytest.mark.asyncio
async def test_delete_connection_promotes_joint_account_and_deletes_exclusive(
    session: AsyncSession, test_user: User, test_workspace
):
    """Deleting a connection promotes joint accounts onto the surviving feed
    and deletes exclusive accounts without ORM cascade warnings (#1341)."""
    conn1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="del-c1",
        institution_name="Bank",
        credentials={"t": "1"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="del-c2",
        institution_name="Bank",
        credentials={"t": "2"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn1, conn2])
    await session.flush()

    joint_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn1.id,
        external_id="j1",
        name="Joint",
        type="checking",
        balance=Decimal("100"),
        currency="EUR",
    )
    exclusive_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn1.id,
        external_id="e1",
        name="Exclusive",
        type="checking",
        balance=Decimal("50"),
        currency="EUR",
    )
    session.add_all([joint_acc, exclusive_acc])
    await session.flush()
    session.add_all([
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            connection_id=conn1.id,
            external_id="j1",
            is_primary_feed=True,
            status="active",
        ),
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            connection_id=conn2.id,
            external_id="j2",
            stable_external_id="st-j2",
            account_fingerprint="f" * 64,
            is_primary_feed=False,
            status="active",
        ),
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=exclusive_acc.id,
            connection_id=conn1.id,
            external_id="e1",
            is_primary_feed=True,
            status="active",
        ),
    ])
    await session.commit()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        ok = await connection_service.delete_connection(
            session, conn1.id, test_workspace.id, keep_accounts=False
        )
    assert ok is True
    assert [w for w in caught if issubclass(w.category, SAWarning)] == []

    conn2_id = conn2.id
    exclusive_acc_id = exclusive_acc.id
    joint_acc_id = joint_acc.id
    session.expire_all()
    assert await session.get(Account, exclusive_acc_id) is None
    surviving_joint = await session.get(Account, joint_acc_id)
    assert surviving_joint is not None
    assert surviving_joint.connection_id == conn2_id
    assert surviving_joint.stable_external_id == "st-j2"
    assert surviving_joint.account_fingerprint == "f" * 64


@pytest.mark.asyncio
async def test_oauth_takeover_promotes_joint_account_without_duplicating_transactions(
    session: AsyncSession, test_user: User, test_workspace
):
    """Promoting a new OAuth connection onto an existing joint account whose
    primary is unhealthy does not duplicate existing synced transactions."""
    partner = User(
        id=uuid.uuid4(),
        email=f"oauth-partner-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(partner)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=partner.id,
            role="editor",
        )
    )

    fp = hash_account_identifier(
        "pluggy", "1234567890", institution="connector:201"
    )
    assert fp is not None
    old_conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="pluggy-item-old",
        institution_name="Joint Pluggy Bank",
        credentials={"token": "old"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    session.add(old_conn)
    await session.flush()

    joint_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=old_conn.id,
        external_id="pluggy-acc-a",
        account_fingerprint=fp,
        masked_number="7890",
        name="Joint Checking",
        type="checking",
        balance=Decimal("100.00"),
        currency="USD",
    )
    session.add(joint_acc)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            connection_id=old_conn.id,
            external_id="pluggy-acc-a",
            account_fingerprint=fp,
            is_primary_feed=True,
            status="active",
        )
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            external_id="pluggy-tx-1",
            description="Existing Coffee",
            original_description="Existing Coffee",
            amount=Decimal("5.50"),
            currency="USD",
            date=date(2026, 10, 10),
            type="debit",
            source="sync",
            status="posted",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    new_acc_data = AccountData(
        external_id="pluggy-acc-b",
        account_fingerprint=fp,
        masked_number="7890",
        name="Joint Checking",
        type="checking",
        balance=Decimal("90.00"),
        currency="USD",
        institution_name="Joint Pluggy Bank",
        institution_external_id="inst-pluggy-1",
    )
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(
        return_value=ConnectionData(
            external_id="pluggy-item-new",
            institution_name="Joint Pluggy Bank",
            credentials={"token": "new"},
            accounts=[new_acc_data],
        )
    )
    mock_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="pluggy-tx-1",
                description="Existing Coffee",
                amount=Decimal("5.50"),
                date=date(2026, 10, 10),
                type="debit",
                currency="USD",
                status="posted",
            ),
            TransactionData(
                external_id="pluggy-tx-2",
                description="New Bakery",
                amount=Decimal("10.00"),
                date=date(2026, 10, 11),
                type="debit",
                currency="USD",
                status="posted",
            ),
        ]
    )
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        new_conn = await connection_service.handle_oauth_callback(
            session,
            test_workspace.id,
            partner.id,
            "code-takeover",
            provider_name="pluggy",
        )

    await session.refresh(joint_acc)
    assert joint_acc.connection_id == new_conn.id
    assert joint_acc.external_id == "pluggy-acc-b"
    assert joint_acc.institution_id is not None

    tx_1_rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == joint_acc.id,
                Transaction.external_id == "pluggy-tx-1",
            )
        )
    ).scalars().all()
    assert len(tx_1_rows) == 1

    tx_2_rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == joint_acc.id,
                Transaction.external_id == "pluggy-tx-2",
            )
        )
    ).scalars().all()
    assert len(tx_2_rows) == 1


@pytest.mark.asyncio
async def test_oauth_partner_login_does_not_overwrite_active_first_partner_via_joint_account(
    session: AsyncSession, test_user: User, test_workspace
):
    """Connecting Partner B's login ([Personal_B, Joint_AB]) in a shared
    workspace while Partner A's login ([Personal_A, Joint_AB]) is active must
    NOT overwrite Partner A's credentials; instead it creates Partner B's
    connection, imports Personal_B, and attaches a secondary link to Joint_AB."""
    partner_b = User(
        id=uuid.uuid4(),
        email=f"partner-b-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(partner_b)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=partner_b.id,
            role="editor",
        )
    )

    fp_a = hash_account_identifier(
        "enable_banking", "DE89370400440532011111", institution="DE:householdbank"
    )
    fp_b = hash_account_identifier(
        "enable_banking", "DE89370400440532022222", institution="DE:householdbank"
    )
    fp_j = hash_account_identifier(
        "enable_banking", "DE89370400440532099999", institution="DE:householdbank"
    )
    assert fp_a and fp_b and fp_j

    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="sess-partner-a",
        institution_name="HouseholdBank",
        credentials={"session_id_enc": "partner-a-secret"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn_a)
    await session.flush()

    personal_a = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="uid-pa",
        stable_external_id="st-pa",
        account_fingerprint=fp_a,
        masked_number="1111",
        name="Personal A",
        type="checking",
        balance=Decimal("100.00"),
        currency="EUR",
    )
    joint_ab = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="uid-ja",
        stable_external_id="st-j",
        account_fingerprint=fp_j,
        masked_number="9999",
        name="Joint AB",
        type="checking",
        balance=Decimal("500.00"),
        currency="EUR",
    )
    session.add_all([personal_a, joint_ab])
    await session.flush()
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=personal_a.id,
                connection_id=conn_a.id,
                external_id="uid-pa",
                stable_external_id="st-pa",
                account_fingerprint=fp_a,
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=joint_ab.id,
                connection_id=conn_a.id,
                external_id="uid-ja",
                stable_external_id="st-j",
                account_fingerprint=fp_j,
                is_primary_feed=True,
                status="active",
            ),
        ]
    )
    await session.commit()

    incoming_accounts = [
        AccountData(
            external_id="uid-pb",
            stable_external_id="st-pb",
            account_fingerprint=fp_b,
            masked_number="2222",
            name="Personal B",
            type="checking",
            balance=Decimal("200.00"),
            currency="EUR",
        ),
        AccountData(
            external_id="uid-jb",
            stable_external_id="st-j",
            account_fingerprint=fp_j,
            masked_number="9999",
            name="Joint AB",
            type="checking",
            balance=Decimal("500.00"),
            currency="EUR",
        ),
    ]
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(
        return_value=ConnectionData(
            external_id="sess-partner-b",
            institution_name="HouseholdBank",
            credentials={"session_id_enc": "partner-b-secret"},
            accounts=incoming_accounts,
        )
    )
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        conn_b = await connection_service.handle_oauth_callback(
            session,
            test_workspace.id,
            partner_b.id,
            "code-partner-b",
            provider_name="enable_banking",
        )

    assert conn_b.id != conn_a.id
    await session.refresh(conn_a)
    assert conn_a.external_id == "sess-partner-a"
    assert conn_a.credentials == {"session_id_enc": "partner-a-secret"}

    accs = (
        await session.execute(select(Account).where(Account.workspace_id == test_workspace.id))
    ).scalars().all()
    assert {a.name for a in accs} == {"Personal A", "Personal B", "Joint AB"}
    joint_links = (
        await session.execute(
            select(AccountConnectionLink).where(AccountConnectionLink.account_id == joint_ab.id)
        )
    ).scalars().all()
    assert len(joint_links) == 2
    by_conn = {link.connection_id: link for link in joint_links}
    assert by_conn[conn_a.id].is_primary_feed is True
    assert by_conn[conn_b.id].is_primary_feed is False


@pytest.mark.asyncio
async def test_path_4b_matches_eb_settled_hold_beyond_1d_and_pluggy_failover_dedup(
    session: AsyncSession, test_user: User, test_workspace
):
    """Path 4b matches EB booked rows that settled a hold >1 day earlier (via
    stored_date) and Pluggy joint-account failover deduplicates booked
    transactions with distinct per-item external IDs."""
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="EB Checking",
        type="checking",
        balance=Decimal("0"),
        currency="EUR",
    )
    session.add(acc)
    await session.flush()

    settled_hold = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=acc.id,
        external_id="old-eb-hold-1",
        description="Hotel Berlin",
        original_description="Hotel Berlin",
        amount=Decimal("120.00"),
        currency="EUR",
        date=date(2026, 10, 10),
        type="debit",
        source="sync",
        status="posted",
        raw_data={
            "transaction_amount": {"amount": "120.00", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-14",
            "creditor": {"name": "Hotel Berlin"},
        },
        created_at=datetime.now(timezone.utc),
    )
    session.add(settled_hold)
    await session.commit()

    rotated = TransactionData(
        external_id="new-eb-booked-1",
        description="Hotel Berlin",
        amount=Decimal("120.00"),
        date=date(2026, 10, 14),
        type="debit",
        currency="EUR",
        status="posted",
        raw_data={
            "transaction_amount": {"amount": "120.00", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-14",
            "creditor": {"name": "Hotel Berlin"},
        },
    )
    # Complete Enable Banking feed (`live_external_ids` set) matches the settled hold.
    dup = await connection_service._find_synced_duplicate(
        session,
        acc.id,
        rotated,
        {"new-eb-booked-1"},
        live_external_ids={"new-eb-booked-1"},
        incoming_batch=[rotated],
        provider="enable_banking",
    )
    assert dup is not None and dup.id == settled_hold.id

    # Truncated Enable Banking feed (`live_external_ids=None`) must NOT match
    # Path 4b even when `allow_booked_dedup=True`, because absence from a partial
    # batch cannot prove the stored row left the feed.
    assert (
        await connection_service._find_synced_duplicate(
            session,
            acc.id,
            rotated,
            {"new-eb-booked-1"},
            live_external_ids=None,
            incoming_batch=[rotated],
            provider="enable_banking",
            allow_booked_dedup=True,
        )
        is None
    )

    twin = TransactionData(
        external_id="new-eb-booked-2",
        description="Hotel Berlin",
        amount=Decimal("120.00"),
        date=date(2026, 10, 14),
        type="debit",
        currency="EUR",
        status="posted",
        raw_data=rotated.raw_data,
    )
    assert (
        await connection_service._find_synced_duplicate(
            session,
            acc.id,
            rotated,
            {"new-eb-booked-1", "new-eb-booked-2"},
            live_external_ids={"new-eb-booked-1", "new-eb-booked-2"},
            incoming_batch=[rotated, twin],
            provider="enable_banking",
        )
        is None
    )

    fp = hash_account_identifier(
        "pluggy", "555566667777", institution="connector:201"
    )
    assert fp is not None
    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="pluggy-item-a",
        institution_name="PluggyBank",
        credentials={"token": "a"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="pluggy-item-b",
        institution_name="PluggyBank",
        credentials={"token": "b"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    joint = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="p-acc-a",
        account_fingerprint=fp,
        masked_number="7777",
        name="Joint Pluggy",
        type="checking",
        balance=Decimal("200.00"),
        currency="BRL",
    )
    session.add(joint)
    await session.flush()
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=joint.id,
                connection_id=conn_a.id,
                external_id="p-acc-a",
                account_fingerprint=fp,
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=joint.id,
                connection_id=conn_b.id,
                external_id="p-acc-b",
                account_fingerprint=fp,
                is_primary_feed=False,
                status="active",
            ),
            Transaction(
                id=uuid.uuid4(),
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                account_id=joint.id,
                external_id="pluggy-item-a-tx-1",
                description="Padaria Central",
                original_description="Padaria Central",
                amount=Decimal("18.50"),
                currency="BRL",
                date=date(2026, 10, 12),
                type="debit",
                source="sync",
                status="posted",
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await session.commit()

    b_acc_data = AccountData(
        external_id="p-acc-b",
        account_fingerprint=fp,
        masked_number="7777",
        name="Joint Pluggy",
        type="checking",
        balance=Decimal("181.50"),
        currency="BRL",
    )
    mock_pluggy = AsyncMock()
    mock_pluggy.refresh_credentials = AsyncMock(return_value={"token": "b"})
    mock_pluggy.get_institution_logo = AsyncMock(return_value=None)
    mock_pluggy.get_accounts = AsyncMock(return_value=[b_acc_data])
    mock_pluggy.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="pluggy-item-b-tx-999",
                description="Padaria Central",
                amount=Decimal("18.50"),
                date=date(2026, 10, 12),
                type="debit",
                currency="BRL",
                status="posted",
            )
        ]
    )
    mock_pluggy.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_pluggy), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )

    synced_txs = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == joint.id,
                Transaction.source == "sync",
            )
        )
    ).scalars().all()
    assert len(synced_txs) == 1
    assert synced_txs[0].external_id == "pluggy-item-b-tx-999"


@pytest.mark.asyncio
async def test_primary_promotion_updates_institution_and_enforces_single_active_primary(
    session: AsyncSession, test_user: User, test_workspace
):
    """Partial unique index enforces single active primary link per account, and
    primary promotion updates or clears stale Account.institution_id."""
    conn1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-1",
        institution_name="SimpleFIN",
        credentials={"url": "https://a"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    conn2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-2",
        institution_name="SimpleFIN",
        credentials={"url": "https://b"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn1, conn2])
    await session.flush()

    old_inst = Institution(
        id=uuid.uuid4(),
        connection_id=conn1.id,
        external_id="inst-old",
        name="Old Bank",
    )
    new_inst = Institution(
        id=uuid.uuid4(),
        connection_id=conn2.id,
        external_id="inst-new",
        name="New Bank",
    )
    session.add_all([old_inst, new_inst])
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn1.id,
        external_id="ext-1",
        institution_id=old_inst.id,
        name="Shared Account",
        type="checking",
        balance=Decimal("100.00"),
        currency="USD",
    )
    session.add(acc)
    await session.flush()

    link1 = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc.id,
        connection_id=conn1.id,
        external_id="ext-1",
        is_primary_feed=True,
        status="active",
    )
    session.add(link1)
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                AccountConnectionLink(
                    workspace_id=test_workspace.id,
                    account_id=acc.id,
                    connection_id=conn2.id,
                    external_id="ext-2",
                    is_primary_feed=True,
                    status="active",
                )
            )
            await session.flush()

    link2 = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc.id,
        connection_id=conn2.id,
        external_id="ext-2",
        is_primary_feed=False,
        status="active",
    )
    session.add(link2)
    await session.commit()

    promoted = await connection_service._maybe_promote_secondary_link(
        session,
        acc,
        link2,
        conn2,
        AccountData(
            external_id="ext-2",
            name="Shared Account",
            type="checking",
            balance=Decimal("100.00"),
            currency="USD",
        ),
        test_user.id,
        institution=new_inst,
    )
    await session.commit()
    assert promoted is True
    await session.refresh(acc)
    assert acc.institution_id == new_inst.id

    conn3 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-3",
        institution_name="SimpleFIN",
        credentials={"url": "https://c"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn3)
    await session.flush()
    conn2.status = "expired"
    pending_link = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc.id,
        connection_id=conn3.id,
        external_id="ext-3",
        is_primary_feed=False,
        status="pending",
    )
    session.add(pending_link)
    await session.commit()

    await connection_service.resolve_account_link(
        session, test_workspace.id, conn3.id, pending_link.id, "confirm"
    )
    await session.refresh(acc)
    assert acc.connection_id == conn3.id
    assert acc.institution_id is None


@pytest.mark.asyncio
async def test_cross_connection_tier2_survives_key_rotation_and_tier1_scoped_by_provider(
    session: AsyncSession, test_user: User, test_workspace
):
    """Tier 2 matches identical same-provider stable_external_id + mask +
    currency + type even after SECRET_KEY rotation, and Tier 1 is provider-scoped."""
    conn_eb1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="eb-1",
        institution_name="EuroBank",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_eb2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="eb-2",
        institution_name="EuroBank",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_plugin = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="plugin:acme/evil",
        external_id="pl-1",
        institution_name="Other",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_eb1, conn_eb2, conn_plugin])
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_eb1.id,
        external_id="uid-1",
        stable_external_id="eb-stable-xyz",
        account_fingerprint="1" * 64,
        masked_number="4321",
        name="Euro Joint",
        type="checking",
        balance=Decimal("10.00"),
        currency="EUR",
    )
    session.add(acc)
    await session.commit()

    tier2 = await connection_service._find_cross_connection_match(
        session,
        conn_eb2,
        AccountData(
            external_id="uid-2",
            stable_external_id="eb-stable-xyz",
            account_fingerprint="2" * 64,
            masked_number="4321",
            name="Euro Joint",
            type="checking",
            balance=Decimal("10.00"),
            currency="EUR",
        ),
        set(),
        set(),
    )
    assert tier2 is not None
    assert tier2[0].id == acc.id
    assert tier2[1] == "active"

    cross_prov = await connection_service._find_cross_connection_match(
        session,
        conn_plugin,
        AccountData(
            external_id="pl-acc-1",
            account_fingerprint="1" * 64,
            name="Unrelated",
            type="checking",
            balance=Decimal("10.00"),
            currency="EUR",
        ),
        set(),
        set(),
    )
    assert cross_prov is None


@pytest.mark.asyncio
async def test_secondary_link_without_durable_ids_rebinds_on_rotated_external_id(
    session: AsyncSession, test_user: User, test_workspace
):
    """A secondary link lacking fingerprint & stable_external_id rebinds when
    external_id rotates instead of creating a duplicate account."""
    conn_primary = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-prim",
        institution_name="SimpleFIN",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_secondary = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-sec",
        institution_name="SimpleFIN",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_primary, conn_secondary])
    await session.flush()

    joint = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_primary.id,
        external_id="prim-ext-1",
        masked_number="8888",
        name="Household Savings",
        type="savings",
        balance=Decimal("300.00"),
        currency="USD",
    )
    session.add(joint)
    await session.flush()
    sec_link = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=joint.id,
        connection_id=conn_secondary.id,
        external_id="sec-ext-old",
        stable_external_id=None,
        account_fingerprint=None,
        is_primary_feed=False,
        status="active",
    )
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=joint.id,
                connection_id=conn_primary.id,
                external_id="prim-ext-1",
                is_primary_feed=True,
                status="active",
            ),
            sec_link,
        ]
    )
    await session.commit()

    rotated = AccountData(
        external_id="sec-ext-rotated",
        masked_number="8888",
        name="Household Savings",
        type="savings",
        balance=Decimal("300.00"),
        currency="USD",
    )
    matched_acc, matched_link, status_tag = await connection_service._resolve_sync_account(
        session,
        conn_secondary,
        rotated,
        None,
        {"sec-ext-rotated"},
        frozenset(),
        set(),
    )
    assert status_tag == "secondary"
    assert matched_acc is not None and matched_acc.id == joint.id
    assert matched_link is not None and matched_link.id == sec_link.id
    assert matched_link.external_id == "sec-ext-rotated"


@pytest.mark.asyncio
async def test_secondary_sync_skips_closed_account_and_failover_preserves_tx_user_id(
    session: AsyncSession, test_user: User, test_workspace
):
    """Secondary sync does not promote or overwrite balance on a closed account,
    and failover preserves Partner A's historical Transaction.user_id."""
    partner = User(
        id=uuid.uuid4(),
        email=f"l11-partner-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(partner)
    await session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=test_workspace.id,
            user_id=partner.id,
            role="editor",
        )
    )

    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="l11-a",
        institution_name="Bank",
        credentials={"t": "a"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=partner.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="l11-b",
        institution_name="Bank",
        credentials={"t": "b"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    closed_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="closed-a",
        name="Closed Joint",
        type="checking",
        balance=Decimal("42.00"),
        currency="EUR",
        is_closed=True,
    )
    open_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="open-a",
        name="Open Joint",
        type="checking",
        balance=Decimal("100.00"),
        currency="EUR",
        is_closed=False,
    )
    session.add_all([closed_acc, open_acc])
    await session.flush()

    hist_tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=open_acc.id,
        external_id="hist-tx-1",
        description="Dinner",
        original_description="Dinner",
        amount=Decimal("50.00"),
        currency="EUR",
        date=date(2026, 10, 1),
        type="debit",
        source="sync",
        status="posted",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=closed_acc.id,
                connection_id=conn_a.id,
                external_id="closed-a",
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=closed_acc.id,
                connection_id=conn_b.id,
                external_id="closed-b",
                is_primary_feed=False,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=open_acc.id,
                connection_id=conn_a.id,
                external_id="open-a",
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=open_acc.id,
                connection_id=conn_b.id,
                external_id="open-b",
                is_primary_feed=False,
                status="active",
            ),
            hist_tx,
        ]
    )
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"t": "b"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(
        return_value=[
            AccountData(
                external_id="closed-b",
                name="Closed Joint",
                type="checking",
                balance=Decimal("999.00"),
                currency="EUR",
            ),
            AccountData(
                external_id="open-b",
                name="Open Joint",
                type="checking",
                balance=Decimal("100.00"),
                currency="EUR",
            ),
        ]
    )
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, partner.id
        )

    await session.refresh(closed_acc)
    await session.refresh(open_acc)
    await session.refresh(hist_tx)
    assert closed_acc.connection_id == conn_a.id
    assert closed_acc.balance == Decimal("42.00")
    assert open_acc.connection_id == conn_b.id
    assert hist_tx.user_id == test_user.id

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, partner.id
        )
    await session.refresh(hist_tx)
    assert hist_tx.user_id == test_user.id


@pytest.mark.asyncio
async def test_different_pluggy_banks_with_same_account_number_do_not_fold(
    session: AsyncSession, test_user: User, test_workspace
):
    """Two Pluggy connections at different institutions whose branch/account
    numbers normalize identically (Itaú 1234-56789 vs Bradesco 1234/5678-9)
    must stay separate accounts, keeping independent balances and importing
    transactions for both."""
    from app.providers.enable_banking import _aspsp_institution_key, _details_summary
    from app.providers.pluggy import PluggyProvider, _build_account_data

    # Fail closed when institution scope is absent
    assert hash_account_identifier("pluggy", "1234-56789") is None
    assert hash_account_identifier("pluggy", "1234-56789", institution="   ") is None

    # Enable Banking ASPSP + country scope separates same BBAN across banks/countries
    eb_raw = {
        "display_name": "Checking",
        "cash_account_type": "CACC",
        "currency": "EUR",
        "account_id": {"other": {"identification": "1234567890"}},
    }
    nl_ing = _details_summary(
        eb_raw, institution=_aspsp_institution_key({"name": "ING", "country": "NL"})
    )["account_fingerprint"]
    de_ing = _details_summary(
        eb_raw, institution=_aspsp_institution_key({"name": "ING", "country": "DE"})
    )["account_fingerprint"]
    nl_rabo = _details_summary(
        eb_raw, institution=_aspsp_institution_key({"name": "Rabobank", "country": "NL"})
    )["account_fingerprint"]
    assert nl_ing is not None and de_ing is not None and nl_rabo is not None
    assert len({nl_ing, de_ing, nl_rabo}) == 3

    # Build real Pluggy AccountData rows for Itaú vs Bradesco with colliding numbers
    itau_data = _build_account_data(
        {
            "id": "itau-acc-1",
            "name": "Conta Corrente",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 1000.00,
            "number": "1234-56789",
        },
        PluggyProvider._map_account_type,
        connector_id=201,
        connector_name="Itaú",
    )
    bradesco_data = _build_account_data(
        {
            "id": "bradesco-acc-1",
            "name": "Conta Corrente",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 250.00,
            "number": "1234/5678-9",
        },
        PluggyProvider._map_account_type,
        connector_id=205,
        connector_name="Bradesco",
    )
    itau_partner_data = _build_account_data(
        {
            "id": "itau-acc-partner",
            "name": "Conta Corrente",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 1000.00,
            "number": "1234/5678-9",
        },
        PluggyProvider._map_account_type,
        connector_id=201,
        connector_name="Itaú",
    )
    assert itau_data.account_fingerprint is not None
    assert bradesco_data.account_fingerprint is not None
    assert itau_data.account_fingerprint != bradesco_data.account_fingerprint
    assert itau_data.account_fingerprint == itau_partner_data.account_fingerprint

    conn_itau = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-itau",
        institution_name="Itaú",
        credentials={"item_id": "item-itau", "connector_id": "201", "connector_name": "Itaú"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_bradesco = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-bradesco",
        institution_name="Bradesco",
        credentials={
            "item_id": "item-bradesco",
            "connector_id": "205",
            "connector_name": "Bradesco",
        },
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_itau, conn_bradesco])
    await session.commit()

    itau_provider = AsyncMock()
    itau_provider.refresh_credentials = AsyncMock(return_value=conn_itau.credentials)
    itau_provider.get_institution_logo = AsyncMock(return_value=None)
    itau_provider.get_accounts = AsyncMock(return_value=[itau_data])
    itau_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="itau-tx-1",
                description="Padaria Itau",
                amount=Decimal("20.00"),
                date=date(2026, 10, 5),
                type="debit",
                currency="BRL",
            )
        ]
    )
    itau_provider.get_bills = AsyncMock(return_value=[])

    bradesco_provider = AsyncMock()
    bradesco_provider.refresh_credentials = AsyncMock(return_value=conn_bradesco.credentials)
    bradesco_provider.get_institution_logo = AsyncMock(return_value=None)
    bradesco_provider.get_accounts = AsyncMock(return_value=[bradesco_data])
    bradesco_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="bradesco-tx-1",
                description="Mercado Bradesco",
                amount=Decimal("45.00"),
                date=date(2026, 10, 6),
                type="debit",
                currency="BRL",
            )
        ]
    )
    bradesco_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=itau_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_itau.id, test_workspace.id, test_user.id
        )

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=bradesco_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_bradesco.id, test_workspace.id, test_user.id
        )

    accs = (
        await session.execute(
            select(Account)
            .where(Account.workspace_id == test_workspace.id)
            .order_by(Account.balance.desc())
        )
    ).scalars().all()
    assert len(accs) == 2
    by_conn = {a.connection_id: a for a in accs}
    assert by_conn[conn_itau.id].balance == Decimal("1000.00")
    assert by_conn[conn_bradesco.id].balance == Decimal("250.00")

    txns = (
        await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == test_workspace.id,
                Transaction.external_id.is_not(None),
            )
        )
    ).scalars().all()
    assert {tx.external_id: tx.account_id for tx in txns} == {
        "itau-tx-1": by_conn[conn_itau.id].id,
        "bradesco-tx-1": by_conn[conn_bradesco.id].id,
    }


@pytest.mark.asyncio
async def test_tier1_fingerprint_match_requires_same_account_type(
    session: AsyncSession, test_user: User, test_workspace
):
    """Tier 1 matching must require acc.type == acc_data.type so a checking and
    savings account sharing an underlying bank identifier never fold together."""
    shared_fp = hash_account_identifier(
        "pluggy", "99887766", institution="connector:201"
    )
    assert shared_fp is not None

    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-type-a",
        institution_name="Itaú",
        credentials={"item_id": "item-type-a", "connector_id": "201"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-type-b",
        institution_name="Itaú",
        credentials={"item_id": "item-type-b", "connector_id": "201"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    checking_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="itau-chk-1",
        account_fingerprint=shared_fp,
        masked_number="7766",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("800.00"),
        currency="BRL",
    )
    session.add(checking_acc)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=checking_acc.id,
            connection_id=conn_a.id,
            external_id="itau-chk-1",
            account_fingerprint=shared_fp,
            is_primary_feed=True,
            status="active",
        )
    )
    await session.commit()

    savings_incoming = AccountData(
        external_id="itau-sav-1",
        account_fingerprint=shared_fp,
        masked_number="7766",
        name="Poupança",
        type="savings",
        balance=Decimal("3000.00"),
        currency="BRL",
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value=conn_b.credentials)
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[savings_incoming])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )

    accs = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(accs) == 2
    assert {a.type for a in accs} == {"checking", "savings"}


def test_grouped_masked_identifiers_rejected_and_pluggy_transfer_number_scope_stable():
    """Grouped masks like 'XX XX 1234' must not produce fingerprints; optional
    Pluggy transferNumber metadata must match feeds without it while distinct
    known COMPE codes under the same connector conflict across separate feeds."""
    from app.providers.base import (
        fingerprints_conflict,
        fingerprints_match,
        prefer_fingerprint,
    )
    from app.providers.pluggy import PluggyProvider, _build_account_data

    assert hash_account_identifier("pluggy", "XX XX 1234", institution="connector:201") is None
    assert hash_account_identifier("pluggy", "XX-XX-1234", institution="connector:201") is None
    assert hash_account_identifier("pluggy", "x.x.x.x 1234", institution="connector:201") is None
    assert hash_account_identifier("pluggy", "•••• 123456", institution="connector:201") is None
    assert hash_account_identifier("pluggy", "1234-56789", institution="connector:201") is not None

    with_transfer = _build_account_data(
        {
            "id": "p-1",
            "name": "Conta Corrente",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 100.00,
            "number": "1234-56789",
            "bankData": {"transferNumber": "260/1234/56789"},
        },
        PluggyProvider._map_account_type,
        connector_id=201,
        connector_name="Itaú",
    )
    without_transfer = _build_account_data(
        {
            "id": "p-2",
            "name": "Conta Corrente",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 100.00,
            "number": "1234-56789",
        },
        PluggyProvider._map_account_type,
        connector_id=201,
        connector_name="Itaú",
    )
    assert with_transfer.account_fingerprint is not None
    assert without_transfer.account_fingerprint is not None
    assert fingerprints_match(
        with_transfer.account_fingerprint, without_transfer.account_fingerprint
    )
    assert fingerprints_match(
        without_transfer.account_fingerprint, with_transfer.account_fingerprint
    )
    assert not fingerprints_conflict(
        with_transfer.account_fingerprint, without_transfer.account_fingerprint
    )
    # Persisting a feed that omitted transferNumber preserves the stored COMPE tag.
    assert (
        prefer_fingerprint(
            with_transfer.account_fingerprint, without_transfer.account_fingerprint
        )
        == with_transfer.account_fingerprint
    )

    # Two separate single-account feeds under the same multi-institution connector
    # (e.g. XP Bank COMPE 348 vs XP Brokerage COMPE 102) with the same number must
    # NOT match and must conflict even without _annotate_institutions running.
    xp_bank = _build_account_data(
        {
            "id": "xp-bank",
            "name": "Conta",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 100.0,
            "number": "0001-99999",
            "bankData": {"transferNumber": "348/0001/99999"},
        },
        PluggyProvider._map_account_type,
        connector_id=601,
        connector_name="XP",
    )
    xp_broker = _build_account_data(
        {
            "id": "xp-broker",
            "name": "Conta",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currencyCode": "BRL",
            "balance": 50.0,
            "number": "0001-99999",
            "bankData": {"transferNumber": "102/0001/99999"},
        },
        PluggyProvider._map_account_type,
        connector_id=601,
        connector_name="XP",
    )
    assert xp_bank.account_fingerprint is not None
    assert xp_broker.account_fingerprint is not None
    assert xp_bank.account_fingerprint != xp_broker.account_fingerprint
    assert not fingerprints_match(
        xp_bank.account_fingerprint, xp_broker.account_fingerprint
    )
    assert fingerprints_conflict(
        xp_bank.account_fingerprint, xp_broker.account_fingerprint
    )


@pytest.mark.asyncio
async def test_secret_key_rotation_preserves_pluggy_joint_matching_with_and_without_fallbacks(
    session: AsyncSession, test_user: User, test_workspace, monkeypatch
):
    """After SECRET_KEY rotation:
    - Without old env key in secret_key_fallbacks, Tier 3 surfaces a pending candidate
      instead of rejecting the unknown-key stored fingerprint as a conflict.
    - With old key in secret_key_fallbacks, a different account sharing the last-4
      mask is still rejected as a known-fallback-key conflict, while the matching joint
      account auto-links in sync_connection and upgrades account.account_fingerprint.
    - Once upgraded to the current key, a different account is rejected as a conflict."""
    from app.core.config import get_settings
    from app.providers.base import hash_account_identifier_with_fallbacks

    monkeypatch.setenv("SECRET_KEY", "old-env-secret-key-aaa")
    get_settings.cache_clear()
    try:
        old_fp = hash_account_identifier(
            "pluggy", "1234-56789", institution="connector:201"
        )
        assert old_fp is not None

        conn_a = BankConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            provider="pluggy",
            external_id="item-rot-a",
            institution_name="Itaú",
            credentials={"item_id": "item-rot-a", "connector_id": "201"},
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        conn_b = BankConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            provider="pluggy",
            external_id="item-rot-b",
            institution_name="Itaú",
            credentials={"item_id": "item-rot-b", "connector_id": "201"},
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        conn_c = BankConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            provider="pluggy",
            external_id="item-rot-c",
            institution_name="Itaú",
            credentials={"item_id": "item-rot-c", "connector_id": "201"},
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add_all([conn_a, conn_b, conn_c])
        await session.flush()

        acc = Account(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            connection_id=conn_a.id,
            external_id="p-rot-a",
            account_fingerprint=old_fp,
            masked_number="6789",
            name="Conta Corrente",
            type="checking",
            balance=Decimal("400.00"),
            currency="BRL",
        )
        session.add(acc)
        await session.flush()
        session.add(
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=acc.id,
                connection_id=conn_a.id,
                external_id="p-rot-a",
                account_fingerprint=old_fp,
                is_primary_feed=True,
                status="active",
            )
        )
        await session.commit()

        # Rotate SECRET_KEY without retaining old env key in secret_key_fallbacks.
        monkeypatch.setenv("SECRET_KEY", "new-env-secret-key-bbb")
        get_settings.cache_clear()
        new_fp, new_fallbacks = hash_account_identifier_with_fallbacks(
            "pluggy", "1234-56789", institution="connector:201"
        )
        assert new_fp is not None and new_fp != old_fp
        assert old_fp not in new_fallbacks

        rotated_no_fb = await connection_service._find_cross_connection_match(
            session,
            conn_b,
            AccountData(
                external_id="p-rot-b",
                account_fingerprint=new_fp,
                fallback_account_fingerprints=new_fallbacks,
                masked_number="6789",
                name="Conta Corrente",
                type="checking",
                balance=Decimal("400.00"),
                currency="BRL",
            ),
            set(),
            set(),
        )
        assert rotated_no_fb is not None
        assert rotated_no_fb[0].id == acc.id
        assert rotated_no_fb[1] == "pending"

        # Configure old key as fallback: a DIFFERENT account (9999-56789) with the
        # same last-4 mask must still conflict under the known fallback key.
        settings = get_settings()
        settings._secret_key_fallbacks = ("old-env-secret-key-aaa", *settings._secret_key_fallbacks)
        other_fp, other_with_fb = hash_account_identifier_with_fallbacks(
            "pluggy", "9999-56789", institution="connector:201"
        )
        assert other_fp is not None
        known_fb_conflict = await connection_service._find_cross_connection_match(
            session,
            conn_c,
            AccountData(
                external_id="p-other-known-fb",
                account_fingerprint=other_fp,
                fallback_account_fingerprints=other_with_fb,
                masked_number="6789",
                name="Conta Corrente",
                type="checking",
                balance=Decimal("100.00"),
                currency="BRL",
            ),
            set(),
            set(),
        )
        assert known_fb_conflict is None

        # Run real sync_connection with fallback configured: conn_b matches Tier 1
        # and upgrades the stored account.account_fingerprint to new_fp2.
        new_fp2, with_fb = hash_account_identifier_with_fallbacks(
            "pluggy", "1234-56789", institution="connector:201"
        )
        assert new_fp2 is not None and old_fp in with_fb
        mock_provider = AsyncMock()
        mock_provider.refresh_credentials = AsyncMock(return_value=conn_b.credentials)
        mock_provider.get_institution_logo = AsyncMock(return_value=None)
        mock_provider.get_accounts = AsyncMock(
            return_value=[
                AccountData(
                    external_id="p-rot-b",
                    account_fingerprint=new_fp2,
                    fallback_account_fingerprints=with_fb,
                    masked_number="6789",
                    name="Conta Corrente",
                    type="checking",
                    balance=Decimal("425.00"),
                    currency="BRL",
                )
            ]
        )
        mock_provider.get_transactions = AsyncMock(return_value=[])
        mock_provider.get_bills = AsyncMock(return_value=[])
        p1, p2, p3 = _patch_sync_helpers()
        with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
            await connection_service.sync_connection(
                session, conn_b.id, test_workspace.id, test_user.id
            )

        session.expunge_all()
        reloaded_acc = await session.get(Account, acc.id)
        assert reloaded_acc is not None
        assert reloaded_acc.account_fingerprint == new_fp2

        # With stored fingerprint now upgraded to current key by sync_connection,
        # a different account sharing the last-4 mask is rejected as a conflict.
        conn_c_reloaded = await session.get(BankConnection, conn_c.id)
        assert conn_c_reloaded is not None
        conflict = await connection_service._find_cross_connection_match(
            session,
            conn_c_reloaded,
            AccountData(
                external_id="p-other-c",
                account_fingerprint=other_fp,
                masked_number="6789",
                name="Conta Corrente",
                type="checking",
                balance=Decimal("100.00"),
                currency="BRL",
            ),
            set(),
            set(),
        )
        assert conflict is None
    finally:
        monkeypatch.delenv("SECRET_KEY", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_simplefin_fallback_links_preserves_institution_guard(
    session: AsyncSession, test_user: User, test_workspace
):
    """fallback_links must not rebind a primary SimpleFIN link from Institution A
    to a same-named account from Institution B while Institution A is still in the payload."""
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="simplefin",
        external_id="sf-guard",
        institution_name="SimpleFIN",
        credentials={"access_url": "https://example.invalid"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()

    inst_a = Institution(
        id=uuid.uuid4(),
        connection_id=conn.id,
        external_id="org-a",
        name="Alpha Bank",
    )
    inst_b = Institution(
        id=uuid.uuid4(),
        connection_id=conn.id,
        external_id="org-b",
        name="Beta Bank",
    )
    session.add_all([inst_a, inst_b])
    await session.flush()

    acc_a = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn.id,
        external_id="sf-a-checking",
        institution_id=inst_a.id,
        name="Checking",
        type="checking",
        balance=Decimal("500.00"),
        currency="USD",
    )
    session.add(acc_a)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=acc_a.id,
            connection_id=conn.id,
            external_id="sf-a-checking",
            is_primary_feed=True,
            status="active",
        )
    )
    await session.commit()

    # Institution A is still in the payload (e.g. only its savings arrived), while
    # Institution B arrives with a new account also named "Checking".
    resolved_acc, resolved_link, status = await connection_service._resolve_sync_account(
        session,
        conn,
        AccountData(
            external_id="sf-b-checking",
            name="Checking",
            type="checking",
            balance=Decimal("75.00"),
            currency="USD",
            institution_external_id="org-b",
            institution_name="Beta Bank",
        ),
        inst_b,
        incoming_external_ids={"sf-a-savings", "sf-b-checking"},
        incoming_institution_external_ids=frozenset({"org-a", "org-b"}),
        claimed_account_ids=set(),
    )
    assert status == "new"
    assert resolved_acc is None and resolved_link is None


@pytest.mark.asyncio
async def test_first_import_truncated_keeps_checkpoint_and_failover_ids(
    session: AsyncSession, test_user: User, test_workspace
):
    """When _run_first_import encounters a truncated or incomplete feed during OAuth
    takeover, last_sync_at stays unset and failover dedup markers are retained."""
    fp = hash_account_identifier("pluggy", "88776655", institution="connector:201")
    assert fp is not None

    conn_expired = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-exp-1",
        institution_name="Itaú",
        credentials={"item_id": "item-exp-1", "connector_id": "201"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn_expired)
    await session.flush()

    joint_acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_expired.id,
        external_id="p-old-1",
        account_fingerprint=fp,
        masked_number="6655",
        name="Joint Checking",
        type="checking",
        balance=Decimal("100.00"),
        currency="BRL",
    )
    session.add(joint_acc)
    await session.flush()
    session.add(
        AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=joint_acc.id,
            connection_id=conn_expired.id,
            external_id="p-old-1",
            account_fingerprint=fp,
            is_primary_feed=True,
            status="active",
        )
    )
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.truncated_account_ids = {"p-new-1"}
    mock_provider.incomplete_account_ids = set()
    mock_provider.handle_oauth_callback = AsyncMock(
        return_value=ConnectionData(
            external_id="item-new-1",
            institution_name="Itaú",
            credentials={"item_id": "item-new-1", "connector_id": "201"},
            accounts=[
                AccountData(
                    external_id="p-new-1",
                    account_fingerprint=fp,
                    masked_number="6655",
                    name="Joint Checking",
                    type="checking",
                    balance=Decimal("100.00"),
                    currency="BRL",
                )
            ],
        )
    )
    mock_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="p-new-trunc-tx-1",
                description="Padaria Truncated Page 1",
                amount=Decimal("15.00"),
                date=date(2026, 10, 10),
                type="debit",
                currency="BRL",
                status="posted",
            )
        ]
    )
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        conn_new = await connection_service.handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "code-trunc",
            provider_name="pluggy",
        )

    assert conn_new.status == "active"
    assert conn_new.last_sync_at is None
    assert str(joint_acc.id) in connection_service._failover_dedup_account_ids(
        conn_new.settings
    )
    imported_tx = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == joint_acc.id,
                Transaction.external_id == "p-new-trunc-tx-1",
            )
        )
    ).scalar_one_or_none()
    assert imported_tx is not None
    new_link = (
        await session.execute(
            select(AccountConnectionLink).where(
                AccountConnectionLink.connection_id == conn_new.id,
                AccountConnectionLink.account_id == joint_acc.id,
            )
        )
    ).scalar_one()
    assert new_link.last_synced_at is None


@pytest.mark.asyncio
async def test_promoted_secondary_feed_requests_full_history_after_balance_refreshes(
    session: AsyncSession, test_user: User, test_workspace
):
    """A secondary link that refreshed balances on day 29 must still request full
    history (since=None) on its first transaction import when promoted on day 30,
    and then switch to the incremental window once that import completes."""
    fp = hash_account_identifier("pluggy", "44332211", institution="connector:201")
    assert fp is not None

    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-hist-a",
        institution_name="Itaú",
        credentials={"item_id": "item-hist-a", "connector_id": "201"},
        status="active",
        last_sync_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-hist-b",
        institution_name="Itaú",
        credentials={"item_id": "item-hist-b", "connector_id": "201"},
        status="active",
        last_sync_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    joint = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="p-hist-a",
        account_fingerprint=fp,
        masked_number="2211",
        name="Joint Checking",
        type="checking",
        balance=Decimal("500.00"),
        currency="BRL",
    )
    session.add(joint)
    await session.flush()
    link_a = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=joint.id,
        connection_id=conn_a.id,
        external_id="p-hist-a",
        account_fingerprint=fp,
        is_primary_feed=True,
        status="active",
        last_synced_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    link_b = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=joint.id,
        connection_id=conn_b.id,
        external_id="p-hist-b",
        account_fingerprint=fp,
        is_primary_feed=False,
        status="active",
        last_synced_at=None,
    )
    session.add_all([link_a, link_b])
    await session.commit()

    acc_b_data = AccountData(
        external_id="p-hist-b",
        account_fingerprint=fp,
        masked_number="2211",
        name="Joint Checking",
        type="checking",
        balance=Decimal("470.00"),
        currency="BRL",
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value=conn_b.credentials)
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[acc_b_data])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    # Day 29: secondary refreshes balance only — must NOT stamp link_b.last_synced_at.
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )
    await session.refresh(link_b)
    assert link_b.last_synced_at is None
    assert mock_provider.get_transactions.call_count == 0

    # Day 30: primary expires; secondary promotes and MUST request since=None (full history).
    conn_a.status = "expired"
    await session.commit()

    seen_sinces: list[date | None] = []

    async def capture_since(_creds, _ext_id, since=None, payee_source="auto"):
        seen_sinces.append(since)
        return []

    mock_provider.get_transactions = AsyncMock(side_effect=capture_since)
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )

    assert seen_sinces == [None]
    await session.refresh(link_b)
    await session.refresh(conn_b)
    assert link_b.last_synced_at is not None
    assert conn_b.last_sync_at is not None
    expected_since = (
        min(link_b.last_synced_at.date(), conn_b.last_sync_at.date())
        - timedelta(days=14)
    )

    # Subsequent sync uses the incremental 14-day window.
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )
    assert len(seen_sinces) == 2
    assert seen_sinces[1] == expected_since

    # Rejected pending link: even though conn_b already has last_sync_at set,
    # its first independent import after rejection must request full history (since=None).
    other_primary = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="p-hist-other-a",
        masked_number="9988",
        name="Household Savings",
        type="savings",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(other_primary)
    await session.flush()
    rejected_pending = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=other_primary.id,
        connection_id=conn_b.id,
        external_id="p-hist-other-b",
        is_primary_feed=False,
        status="rejected",
        last_synced_at=None,
    )
    session.add(rejected_pending)
    await session.commit()

    seen_by_ext: dict[str, list[date | None]] = {}

    async def capture_by_ext(_creds, ext_id, since=None, payee_source="auto"):
        seen_by_ext.setdefault(ext_id, []).append(since)
        return []

    mock_provider.get_accounts = AsyncMock(
        return_value=[
            acc_b_data,
            AccountData(
                external_id="p-hist-other-b",
                masked_number="9988",
                name="Household Savings",
                type="savings",
                balance=Decimal("250.00"),
                currency="BRL",
            ),
        ]
    )
    mock_provider.get_transactions = AsyncMock(side_effect=capture_by_ext)
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )
    assert seen_by_ext["p-hist-b"] != [None]
    assert seen_by_ext["p-hist-other-b"] == [None]


@pytest.mark.asyncio
async def test_truncated_eb_failover_defers_ambiguous_booked_rekey_until_complete_sync(
    session: AsyncSession, test_user: User, test_workspace
):
    """During a truncated Enable Banking failover read, a reference-less booked
    purchase matching a stored row under the old UID is deferred instead of
    inserting a duplicate, then reconciled in place on the first complete sync."""
    fp = hash_account_identifier(
        "enable_banking", "DE89370400440532013001", institution="DE:jointbank"
    )
    assert fp is not None

    conn_a = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="eb-sess-a",
        institution_name="JointBank",
        credentials={"session_id_enc": "a"},
        status="expired",
        created_at=datetime.now(timezone.utc),
    )
    conn_b = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="eb-sess-b",
        institution_name="JointBank",
        credentials={"session_id_enc": "b"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_a, conn_b])
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_a.id,
        external_id="eb-uid-a",
        account_fingerprint=fp,
        masked_number="3001",
        name="Joint Euro",
        type="checking",
        balance=Decimal("500.00"),
        currency="EUR",
    )
    session.add(acc)
    await session.flush()
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=acc.id,
                connection_id=conn_a.id,
                external_id="eb-uid-a",
                account_fingerprint=fp,
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=acc.id,
                connection_id=conn_b.id,
                external_id="eb-uid-b",
                account_fingerprint=fp,
                is_primary_feed=False,
                status="active",
            ),
            Transaction(
                id=uuid.uuid4(),
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                account_id=acc.id,
                external_id="eb-uid-a-booked-1",
                description="Baeckerei Mitte",
                original_description="Baeckerei Mitte",
                amount=Decimal("12.40"),
                currency="EUR",
                date=date(2026, 10, 12),
                type="debit",
                source="sync",
                status="posted",
                raw_data={
                    "transaction_amount": {"amount": "12.40", "currency": "EUR"},
                    "credit_debit_indicator": "DBIT",
                    "booking_date": "2026-10-12",
                    "creditor": {"name": "Baeckerei Mitte"},
                },
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await session.commit()

    acc_b_data = AccountData(
        external_id="eb-uid-b",
        account_fingerprint=fp,
        masked_number="3001",
        name="Joint Euro",
        type="checking",
        balance=Decimal("487.60"),
        currency="EUR",
    )
    rotated_tx = TransactionData(
        external_id="eb-uid-b-booked-1",
        description="Baeckerei Mitte",
        amount=Decimal("12.40"),
        date=date(2026, 10, 12),
        type="debit",
        currency="EUR",
        status="posted",
        raw_data={
            "transaction_amount": {"amount": "12.40", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-10-12",
            "creditor": {"name": "Baeckerei Mitte"},
        },
    )

    # First sync after promotion is truncated: must NOT insert a duplicate row.
    mock_eb = AsyncMock()
    mock_eb.refresh_credentials = AsyncMock(return_value=conn_b.credentials)
    mock_eb.get_institution_logo = AsyncMock(return_value=None)
    mock_eb.get_accounts = AsyncMock(return_value=[acc_b_data])
    mock_eb.get_transactions = AsyncMock(return_value=[rotated_tx])
    mock_eb.get_bills = AsyncMock(return_value=[])
    mock_eb.truncated_account_ids = {"eb-uid-b"}
    mock_eb.incomplete_account_ids = set()

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_eb), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )

    txs_after_partial = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == acc.id,
                Transaction.source == "sync",
            )
        )
    ).scalars().all()
    assert len(txs_after_partial) == 1
    assert txs_after_partial[0].external_id == "eb-uid-a-booked-1"

    # Next sync completes whole: Path 4b reconciles the stored row onto the new UID.
    mock_eb.truncated_account_ids = set()
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_eb), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_b.id, test_workspace.id, test_user.id
        )

    txs_after_complete = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == acc.id,
                Transaction.source == "sync",
            )
        )
    ).scalars().all()
    assert len(txs_after_complete) == 1
    assert txs_after_complete[0].external_id == "eb-uid-b-booked-1"


@pytest.mark.asyncio
async def test_enable_banking_display_fallback_bank_does_not_scope_fingerprints(
    monkeypatch,
):
    """Enable Banking's 'Bank' display fallback for an unnamed ASPSP must never
    be saved as an institution fingerprint scope or produce a 'DE:bank' digest."""
    import httpx

    from app.providers.enable_banking import (
        EnableBankingProvider,
        _aspsp_institution_key,
    )
    from app.providers.psu import PsuContext, psu_present

    assert _aspsp_institution_key({"country": "DE"}) is None
    assert _aspsp_institution_key({"name": "", "country": "DE"}) is None
    assert _aspsp_institution_key({"name": "   ", "country": "DE"}) is None
    assert _aspsp_institution_key({"name": "Bank", "country": "DE"}) is None
    assert _aspsp_institution_key({"name": "  bank ", "country": "DE"}) is None
    assert _aspsp_institution_key({"name": "Deutsche Bank", "country": "DE"}) == "DE:deutsche bank"

    monkeypatch.setattr(EnableBankingProvider, "_jwt_token", classmethod(lambda cls, _s=None: "jwt"))

    def _mock_client(handler):
        return httpx.AsyncClient(
            base_url="https://api.enablebanking.com",
            transport=httpx.MockTransport(handler),
        )

    eb_account = {
        "uid": "eb-unnamed-uid-1",
        "display_name": "Girokonto",
        "cash_account_type": "CACC",
        "currency": "EUR",
        "account_id": {"iban": "DE89370400440532013001"},
    }

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "sess-unnamed-1",
                    "accounts": [eb_account],
                    "aspsp": {"country": "DE"},
                    "access": {"valid_until": "2027-01-01T00:00:00Z"},
                },
            )
        if request.url.path.endswith("/balances"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balance_type": "CLBD",
                            "balance_amount": {"amount": "100.00", "currency": "EUR"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"aspsps": []})

    provider = EnableBankingProvider()
    monkeypatch.setattr(provider, "_client", lambda: _mock_client(oauth_handler))
    conn_data = await provider.handle_oauth_callback("code-unnamed")
    assert conn_data.institution_name == "Bank"
    assert conn_data.credentials["aspsp"]["name"] is None
    assert conn_data.credentials["aspsp"]["country"] == "DE"
    assert conn_data.accounts[0].account_fingerprint is None

    # Fresh-details sync with the user present must still fail closed (no 'DE:bank'),
    # both for new connections (name=None) and legacy rows that persisted name='Bank'.
    def sync_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/sessions/"):
            return httpx.Response(
                200,
                json={
                    "status": "AUTHORIZED",
                    "aspsp": {"country": "DE"},
                    "accounts_data": [{"uid": "eb-unnamed-uid-1"}],
                },
            )
        if request.url.path.endswith("/details"):
            return httpx.Response(200, json=eb_account)
        if request.url.path.endswith("/balances"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {
                            "balance_type": "CLBD",
                            "balance_amount": {"amount": "100.00", "currency": "EUR"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    provider2 = EnableBankingProvider()
    monkeypatch.setattr(provider2, "_client", lambda: _mock_client(sync_handler))
    user_ctx = PsuContext(headers={"Psu-Ip-Address": "203.0.113.10", "Psu-User-Agent": "Test"})
    with psu_present(user_ctx):
        fresh_from_none = await provider2.get_accounts(dict(conn_data.credentials))
        legacy_bank_creds = {
            "session_id": "sess-legacy",
            "aspsp": {"name": "Bank", "country": "DE"},
        }
        fresh_from_legacy = await provider2.get_accounts(legacy_bank_creds)

    assert fresh_from_none[0].account_fingerprint is None
    assert fresh_from_legacy[0].account_fingerprint is None

    # Real named bank in DE still produces a country+name-scoped fingerprint.
    def named_sync_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/sessions/"):
            return httpx.Response(
                200,
                json={
                    "status": "AUTHORIZED",
                    "aspsp": {"name": "Commerzbank", "country": "DE"},
                    "accounts_data": [{"uid": "eb-unnamed-uid-1"}],
                },
            )
        return sync_handler(request)

    provider3 = EnableBankingProvider()
    monkeypatch.setattr(provider3, "_client", lambda: _mock_client(named_sync_handler))
    with psu_present(user_ctx):
        named_accounts = await provider3.get_accounts(
            {"session_id": "sess-named", "aspsp": {"name": "Commerzbank", "country": "DE"}}
        )
    assert named_accounts[0].account_fingerprint == hash_account_identifier(
        "enable_banking", "DE89370400440532013001", institution="DE:commerzbank"
    )


@pytest.mark.asyncio
async def test_feed_rebinding_requires_compatible_type_and_currency(
    session: AsyncSession, test_user: User, test_workspace
):
    """Fingerprint rebinding (including singleton candidates in _disambiguate_account_candidates,
    _resolve_sync_account, and _find_existing_connected_account) must reject mismatched type
    or currency while still rebinding genuine same-type, same-currency rekeys."""
    shared_fp = hash_account_identifier(
        "pluggy", "5566-778899", institution="connector:201"
    )
    assert shared_fp is not None

    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-rebind-type",
        institution_name="Itaú",
        credentials={"item_id": "item-rebind-type", "connector_id": "201"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()

    checking = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn.id,
        external_id="pluggy-chk-old",
        account_fingerprint=shared_fp,
        masked_number="8899",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("1200.00"),
        currency="BRL",
    )
    session.add(checking)
    await session.flush()
    link = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=checking.id,
        connection_id=conn.id,
        external_id="pluggy-chk-old",
        account_fingerprint=shared_fp,
        is_primary_feed=True,
        status="active",
    )
    session.add(link)
    await session.commit()

    savings_same_fp = AccountData(
        external_id="pluggy-sav-new",
        account_fingerprint=shared_fp,
        masked_number="8899",
        name="Poupança",
        type="savings",
        balance=Decimal("5000.00"),
        currency="BRL",
    )
    usd_checking_same_fp = AccountData(
        external_id="pluggy-usd-new",
        account_fingerprint=shared_fp,
        masked_number="8899",
        name="USD Checking",
        type="checking",
        balance=Decimal("300.00"),
        currency="USD",
    )
    brl_checking_rekeyed = AccountData(
        external_id="pluggy-chk-rekeyed",
        account_fingerprint=shared_fp,
        masked_number="8899",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("1250.00"),
        currency="BRL",
    )

    # Singleton candidate helper must enforce type and currency.
    assert connection_service._disambiguate_account_candidates([checking], savings_same_fp) is None
    assert connection_service._disambiguate_account_candidates([checking], usd_checking_same_fp) is None
    assert connection_service._disambiguate_account_candidates([checking], brl_checking_rekeyed) is checking

    # Legacy _find_existing_connected_account must enforce type and currency too.
    assert (
        await connection_service._find_existing_connected_account(
            session, conn, savings_same_fp, None, {"pluggy-sav-new"}
        )
        is None
    )
    assert (
        await connection_service._find_existing_connected_account(
            session, conn, usd_checking_same_fp, None, {"pluggy-usd-new"}
        )
        is None
    )

    # Sync payload where checking external_id disappeared and only savings + USD checking
    # sharing the fingerprint arrive: neither may overwrite the BRL checking ledger.
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value=conn.credentials)
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(
        return_value=[savings_same_fp, usd_checking_same_fp]
    )
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    await session.refresh(checking)
    await session.refresh(link)
    assert checking.external_id == "pluggy-chk-old"
    assert checking.balance == Decimal("1200.00")
    assert link.external_id == "pluggy-chk-old"
    accs = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(accs) == 3
    assert {(a.type, a.currency, a.external_id) for a in accs} == {
        ("checking", "BRL", "pluggy-chk-old"),
        ("savings", "BRL", "pluggy-sav-new"),
        ("checking", "USD", "pluggy-usd-new"),
    }

    # Genuine same-type, same-currency rekey still rebinds the original checking account.
    mock_provider.get_accounts = AsyncMock(
        return_value=[brl_checking_rekeyed, savings_same_fp, usd_checking_same_fp]
    )
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    await session.refresh(checking)
    await session.refresh(link)
    assert checking.external_id == "pluggy-chk-rekeyed"
    assert checking.balance == Decimal("1250.00")
    assert link.external_id == "pluggy-chk-rekeyed"
    accs_after = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(accs_after) == 3


@pytest.mark.asyncio
async def test_prefer_fingerprint_retains_compe_tag_across_secret_key_rotation(
    session: AsyncSession, test_user: User, test_workspace, monkeypatch
):
    """After SECRET_KEY rotation, omitting Pluggy transferNumber must retain the
    stored COMPE-tagged fingerprint on both account and primary link so a different
    COMPE under the same connector cannot auto-link until a compatible tagged
    replacement arrives under the new key."""
    from app.core.config import get_settings
    from app.providers.base import (
        hash_account_identifier_with_fallbacks,
        prefer_fingerprint,
    )

    monkeypatch.setenv("SECRET_KEY", "rotation-compe-old-key-a")
    get_settings.cache_clear()
    try:
        fp_348_old = hash_account_identifier(
            "pluggy",
            "0001-99999",
            institution="connector:601",
            sub_institution="compe:348",
        )
        assert fp_348_old is not None

        conn_a = BankConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            provider="pluggy",
            external_id="item-rot-compe-a",
            institution_name="XP",
            credentials={"item_id": "item-rot-compe-a", "connector_id": "601"},
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        conn_other = BankConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            provider="pluggy",
            external_id="item-rot-compe-other",
            institution_name="XP",
            credentials={"item_id": "item-rot-compe-other", "connector_id": "601"},
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add_all([conn_a, conn_other])
        await session.flush()

        acc = Account(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            connection_id=conn_a.id,
            external_id="xp-348-acc",
            account_fingerprint=fp_348_old,
            masked_number="9999",
            name="Conta XP",
            type="checking",
            balance=Decimal("500.00"),
            currency="BRL",
        )
        session.add(acc)
        await session.flush()
        primary_link = AccountConnectionLink(
            workspace_id=test_workspace.id,
            account_id=acc.id,
            connection_id=conn_a.id,
            external_id="xp-348-acc",
            account_fingerprint=fp_348_old,
            is_primary_feed=True,
            status="active",
        )
        session.add(primary_link)
        await session.commit()

        # Rotate SECRET_KEY to B with A in secret_key_fallbacks.
        monkeypatch.setenv("SECRET_KEY", "rotation-compe-new-key-b")
        get_settings.cache_clear()
        settings = get_settings()
        settings._secret_key_fallbacks = (
            "rotation-compe-old-key-a",
            *settings._secret_key_fallbacks,
        )

        # Primary sync omits transferNumber under key B -> must retain fp_348_old.
        fp_unspec_new, fp_unspec_fbs = hash_account_identifier_with_fallbacks(
            "pluggy", "0001-99999", institution="connector:601", sub_institution=None
        )
        assert fp_unspec_new is not None
        assert (
            prefer_fingerprint(
                fp_348_old, fp_unspec_new, fallback_fps=fp_unspec_fbs
            )
            == fp_348_old
        )

        mock_a = AsyncMock()
        mock_a.refresh_credentials = AsyncMock(return_value=conn_a.credentials)
        mock_a.get_institution_logo = AsyncMock(return_value=None)
        mock_a.get_accounts = AsyncMock(
            return_value=[
                AccountData(
                    external_id="xp-348-acc",
                    account_fingerprint=fp_unspec_new,
                    fallback_account_fingerprints=fp_unspec_fbs,
                    masked_number="9999",
                    name="Conta XP",
                    type="checking",
                    balance=Decimal("510.00"),
                    currency="BRL",
                )
            ]
        )
        mock_a.get_transactions = AsyncMock(return_value=[])
        mock_a.get_bills = AsyncMock(return_value=[])

        p1, p2, p3 = _patch_sync_helpers()
        with patch("app.services.connection_service.get_provider", return_value=mock_a), p1, p2, p3:
            await connection_service.sync_connection(
                session, conn_a.id, test_workspace.id, test_user.id
            )

        await session.refresh(acc)
        await session.refresh(primary_link)
        assert acc.account_fingerprint == fp_348_old
        assert primary_link.account_fingerprint == fp_348_old

        # A different COMPE (102) under key B must NOT match the COMPE 348 account.
        fp_102_new, fp_102_fbs = hash_account_identifier_with_fallbacks(
            "pluggy",
            "0001-99999",
            institution="connector:601",
            sub_institution="compe:102",
        )
        assert fp_102_new is not None
        cross = await connection_service._find_cross_connection_match(
            session,
            conn_other,
            AccountData(
                external_id="xp-102-acc",
                account_fingerprint=fp_102_new,
                fallback_account_fingerprints=fp_102_fbs,
                masked_number="9999",
                name="Conta XP",
                type="checking",
                balance=Decimal("50.00"),
                currency="BRL",
            ),
            set(),
            set(),
        )
        assert cross is None

        # Once the primary feed supplies COMPE 348 under key B, both upgrade to key B.
        fp_348_new, fp_348_fbs = hash_account_identifier_with_fallbacks(
            "pluggy",
            "0001-99999",
            institution="connector:601",
            sub_institution="compe:348",
        )
        assert fp_348_new is not None
        mock_a.get_accounts = AsyncMock(
            return_value=[
                AccountData(
                    external_id="xp-348-acc",
                    account_fingerprint=fp_348_new,
                    fallback_account_fingerprints=fp_348_fbs,
                    masked_number="9999",
                    name="Conta XP",
                    type="checking",
                    balance=Decimal("520.00"),
                    currency="BRL",
                )
            ]
        )
        p1, p2, p3 = _patch_sync_helpers()
        with patch("app.services.connection_service.get_provider", return_value=mock_a), p1, p2, p3:
            await connection_service.sync_connection(
                session, conn_a.id, test_workspace.id, test_user.id
            )

        await session.refresh(acc)
        await session.refresh(primary_link)
        assert acc.account_fingerprint == fp_348_new
        assert primary_link.account_fingerprint == fp_348_new
    finally:
        monkeypatch.delenv("SECRET_KEY", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pluggy_connector_id_recovery_and_name_to_id_migration(
    session: AsyncSession, test_user: User, test_workspace, monkeypatch
):
    """Pluggy retries connector_id lookup even when connector_name is already stored,
    migrates stored connector_name digests to the recovered connector_id digest via
    fallback fingerprints, and never equates two distinct known connector IDs."""
    import httpx

    from app.providers.base import fingerprints_conflict, fingerprints_match
    from app.providers.pluggy import PluggyProvider, _build_account_data

    raw_acc = {
        "id": "pluggy-itau-1",
        "name": "Conta Corrente",
        "type": "BANK",
        "subtype": "CHECKING_ACCOUNT",
        "currencyCode": "BRL",
        "balance": 750.00,
        "number": "1234-56789",
    }

    # Initial callback returned connector name without connector id.
    provider = PluggyProvider()
    monkeypatch.setattr(provider, "_ensure_api_key", AsyncMock(return_value="api-key"))
    item_calls: list[str] = []

    def callback_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/items/item-no-cid":
            return httpx.Response(200, json={"id": "item-no-cid", "connector": {"name": "Itaú"}})
        if request.url.path == "/accounts":
            return httpx.Response(200, json={"results": [raw_acc]})
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.providers.pluggy.httpx.AsyncClient",
        lambda *a, **kw: real_async_client(transport=httpx.MockTransport(callback_handler)),
    )
    conn_data = await provider.handle_oauth_callback("item-no-cid")
    assert "connector_id" not in conn_data.credentials
    assert conn_data.credentials["connector_name"] == "Itaú"
    name_fp = conn_data.accounts[0].account_fingerprint
    assert name_fp == hash_account_identifier(
        "pluggy", "1234-56789", institution="connector_name:itaú"
    )

    # Later get_accounts must retry /items/{id} because connector_id is missing,
    # even though connector_name is stored, and also when connector_id is unusable.
    def recovery_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/items/item-no-cid":
            item_calls.append(request.url.path)
            return httpx.Response(
                200,
                json={"id": "item-no-cid", "connector": {"id": 201, "name": "Itaú"}},
            )
        if request.url.path == "/accounts":
            return httpx.Response(200, json={"results": [raw_acc]})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app.providers.pluggy.httpx.AsyncClient",
        lambda *a, **kw: real_async_client(transport=httpx.MockTransport(recovery_handler)),
    )
    creds = dict(conn_data.credentials)
    recovered_accounts = await provider.get_accounts(creds)
    assert item_calls == ["/items/item-no-cid"]
    assert creds["connector_id"] == "201"
    id201_fp = recovered_accounts[0].account_fingerprint
    assert id201_fp == hash_account_identifier(
        "pluggy", "1234-56789", institution="connector:201"
    )
    assert name_fp in recovered_accounts[0].fallback_account_fingerprints

    # Unusable connector_id (blank/bool) also triggers lookup.
    bad_creds = {"item_id": "item-no-cid", "connector_id": "  ", "connector_name": "Itaú"}
    await provider.get_accounts(bad_creds)
    assert bad_creds["connector_id"] == "201"
    assert len(item_calls) == 2

    # Different known connector IDs (201 vs 202) sharing connector_name="Itaú"
    # must NEVER match and MUST conflict.
    id202_data = _build_account_data(
        raw_acc,
        PluggyProvider._map_account_type,
        connector_id=202,
        connector_name="Itaú",
    )
    assert id202_data.account_fingerprint is not None
    assert not fingerprints_match(
        id201_fp,
        id202_data.account_fingerprint,
        fallback_fps=id202_data.fallback_account_fingerprints,
    )
    assert fingerprints_conflict(
        id201_fp,
        id202_data.account_fingerprint,
        fallback_fps=id202_data.fallback_account_fingerprints,
    )

    # Full migration flow: connection stored under name_fp upgrades cleanly when
    # connector_id 201 is recovered, and a separate connector 202 connection stays separate.
    conn_201 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-no-cid",
        institution_name="Itaú",
        credentials={"item_id": "item-no-cid", "connector_name": "Itaú"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_202 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="item-cid-202",
        institution_name="Itaú",
        credentials={"item_id": "item-cid-202", "connector_id": "202", "connector_name": "Itaú"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_201, conn_202])
    await session.flush()
    acc_201 = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_201.id,
        external_id="pluggy-itau-old-ext",
        account_fingerprint=name_fp,
        masked_number="6789",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("750.00"),
        currency="BRL",
    )
    session.add(acc_201)
    await session.flush()
    link_201 = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc_201.id,
        connection_id=conn_201.id,
        external_id="pluggy-itau-old-ext",
        account_fingerprint=name_fp,
        is_primary_feed=True,
        status="active",
    )
    session.add(link_201)
    await session.commit()

    # Sync conn_201 with recovered connector_id=201 and rotated external_id:
    # must rebind via name_fp fallback and upgrade both account and link to id201_fp.
    mock_201 = AsyncMock()
    mock_201.refresh_credentials = AsyncMock(return_value=creds)
    mock_201.get_institution_logo = AsyncMock(return_value=None)
    mock_201.get_accounts = AsyncMock(return_value=recovered_accounts)
    mock_201.get_transactions = AsyncMock(return_value=[])
    mock_201.get_bills = AsyncMock(return_value=[])
    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_201), p1, p2, p3:
        await connection_service.sync_connection(
            session, conn_201.id, test_workspace.id, test_user.id
        )

    await session.refresh(acc_201)
    await session.refresh(link_201)
    assert acc_201.external_id == "pluggy-itau-1"
    assert acc_201.account_fingerprint == id201_fp
    assert link_201.account_fingerprint == id201_fp

    # Now conn_202 (different known connector_id=202, same name "Itaú" & number) must NOT match.
    cross_202 = await connection_service._find_cross_connection_match(
        session, conn_202, id202_data, set(), set()
    )
    assert cross_202 is None


@pytest.mark.asyncio
async def test_tier1_matches_surviving_pluggy_link_after_enable_banking_takeover(
    session: AsyncSession, test_user: User, test_workspace
):
    """Tier 1 must compare each active link's fingerprint against THAT link's own
    connection provider: after Enable Banking takes over as primary on an account
    with an active Pluggy link, a second Pluggy connection still matches via Tier 1,
    while a Pluggy connection cannot match an Enable Banking link's fingerprint."""
    eb_fp = hash_account_identifier(
        "enable_banking", "DE89370400440532013001", institution="DE:jointbank"
    )
    pluggy_fp = hash_account_identifier(
        "pluggy", "1234-56789", institution="connector:201"
    )
    assert eb_fp is not None and pluggy_fp is not None

    conn_eb = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="enable_banking",
        external_id="eb-primary-takeover",
        institution_name="JointBank",
        credentials={"session_id_enc": "eb"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_pluggy_1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="pluggy-surviving-link",
        institution_name="Itaú",
        credentials={"item_id": "pluggy-surviving-link", "connector_id": "201"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_pluggy_2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="pluggy-second-coowner",
        institution_name="Itaú",
        credentials={"item_id": "pluggy-second-coowner", "connector_id": "201"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_eb, conn_pluggy_1, conn_pluggy_2])
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_eb.id,
        external_id="eb-uid-primary",
        account_fingerprint=eb_fp,
        masked_number="6789",
        name="Joint Checking",
        type="checking",
        balance=Decimal("900.00"),
        currency="BRL",
    )
    session.add(acc)
    await session.flush()
    session.add_all(
        [
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=acc.id,
                connection_id=conn_eb.id,
                external_id="eb-uid-primary",
                account_fingerprint=eb_fp,
                is_primary_feed=True,
                status="active",
            ),
            AccountConnectionLink(
                workspace_id=test_workspace.id,
                account_id=acc.id,
                connection_id=conn_pluggy_1.id,
                external_id="pluggy-ext-1",
                account_fingerprint=pluggy_fp,
                is_primary_feed=False,
                status="active",
            ),
        ]
    )
    await session.commit()

    # Second Pluggy feed matches the surviving active Pluggy link in Tier 1.
    matched = await connection_service._find_cross_connection_match(
        session,
        conn_pluggy_2,
        AccountData(
            external_id="pluggy-ext-2",
            account_fingerprint=pluggy_fp,
            masked_number="6789",
            name="Conta Conjunta",
            type="checking",
            balance=Decimal("900.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert matched is not None
    assert matched[0].id == acc.id
    assert matched[1] == "active"

    # A Pluggy feed carrying the Enable Banking fingerprint must NOT match the EB link.
    forged_cross_provider = await connection_service._find_cross_connection_match(
        session,
        conn_pluggy_2,
        AccountData(
            external_id="pluggy-forged",
            account_fingerprint=eb_fp,
            masked_number="0000",
            name="Unrelated",
            type="checking",
            balance=Decimal("900.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert forged_cross_provider is None


@pytest.mark.asyncio
async def test_tier1_rejects_conflicting_compe_even_with_unspecified_secondary_link(
    session: AsyncSession, test_user: User, test_workspace
):
    """When an account has a known COMPE (348) on one active feed and an unspecified
    COMPE on another active feed, a third feed with a conflicting COMPE (102) must
    be rejected rather than matching via the unspecified link, while compatible feeds
    (COMPE 348 or unspecified) still match."""
    fp_348 = hash_account_identifier(
        "pluggy", "0001-99999", institution="connector:601", sub_institution="compe:348"
    )
    fp_102 = hash_account_identifier(
        "pluggy", "0001-99999", institution="connector:601", sub_institution="compe:102"
    )
    fp_unspec = hash_account_identifier(
        "pluggy", "0001-99999", institution="connector:601", sub_institution=None
    )
    assert fp_348 is not None and fp_102 is not None and fp_unspec is not None

    conn_1 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="xp-c1",
        institution_name="XP",
        credentials={"item_id": "xp-c1", "connector_id": "601"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_2 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="xp-c2",
        institution_name="XP",
        credentials={"item_id": "xp-c2", "connector_id": "601"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    conn_3 = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="pluggy",
        external_id="xp-c3",
        institution_name="XP",
        credentials={"item_id": "xp-c3", "connector_id": "601"},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([conn_1, conn_2, conn_3])
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn_1.id,
        external_id="xp-acc-1",
        account_fingerprint=fp_348,
        masked_number="9999",
        name="Conta XP",
        type="checking",
        balance=Decimal("100.00"),
        currency="BRL",
    )
    session.add(acc)
    await session.flush()
    link_1 = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc.id,
        connection_id=conn_1.id,
        external_id="xp-acc-1",
        account_fingerprint=fp_348,
        is_primary_feed=True,
        status="active",
    )
    link_2 = AccountConnectionLink(
        workspace_id=test_workspace.id,
        account_id=acc.id,
        connection_id=conn_2.id,
        external_id="xp-acc-2",
        account_fingerprint=fp_unspec,
        is_primary_feed=False,
        status="active",
    )
    session.add_all([link_1, link_2])
    await session.commit()

    # Conflicting COMPE 102 must be rejected (neither Tier 1 nor Tier 3).
    res_102 = await connection_service._find_cross_connection_match(
        session,
        conn_3,
        AccountData(
            external_id="xp-acc-3-102",
            account_fingerprint=fp_102,
            masked_number="9999",
            name="Conta XP",
            type="checking",
            balance=Decimal("50.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert res_102 is None

    # Compatible COMPE 348 and unspecified COMPE still match in Tier 1.
    res_348 = await connection_service._find_cross_connection_match(
        session,
        conn_3,
        AccountData(
            external_id="xp-acc-3-348",
            account_fingerprint=fp_348,
            masked_number="9999",
            name="Conta XP",
            type="checking",
            balance=Decimal("100.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert res_348 is not None and res_348[0].id == acc.id and res_348[1] == "active"

    res_unspec = await connection_service._find_cross_connection_match(
        session,
        conn_3,
        AccountData(
            external_id="xp-acc-3-unspec",
            account_fingerprint=fp_unspec,
            masked_number="9999",
            name="Conta XP",
            type="checking",
            balance=Decimal("100.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert res_unspec is not None and res_unspec[0].id == acc.id and res_unspec[1] == "active"

    # Reverse orientation: primary holds unspecified COMPE while secondary holds COMPE 348.
    acc.account_fingerprint = fp_unspec
    link_1.account_fingerprint = fp_unspec
    link_2.account_fingerprint = fp_348
    await session.commit()

    res_102_rev = await connection_service._find_cross_connection_match(
        session,
        conn_3,
        AccountData(
            external_id="xp-acc-3-102-rev",
            account_fingerprint=fp_102,
            masked_number="9999",
            name="Conta XP",
            type="checking",
            balance=Decimal("50.00"),
            currency="BRL",
        ),
        set(),
        set(),
    )
    assert res_102_rev is None


@pytest.mark.asyncio
async def test_reconnect_during_sync_registration_gap_aborts_stale_import(
    session: AsyncSession, test_user: User, test_workspace
):
    """If a reconnect commits in the window between sync's registration commit and
    lock reacquisition, sync must abort without importing stale accounts_data or
    overwriting the reset last_sync_at / link checkpoints (including Pluggy
    update-mode reconnects where external_id and credentials stay unchanged)."""
    from sqlalchemy import update as sa_update

    ws_id = test_workspace.id
    uid = test_user.id
    initial_sync_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    eb_conn_id = uuid.uuid4()
    conn = BankConnection(
        id=eb_conn_id,
        user_id=uid,
        workspace_id=ws_id,
        provider="enable_banking",
        external_id="eb-sess-old",
        institution_name="JointBank",
        credentials={"session_id_enc": "old-enc"},
        status="active",
        last_sync_at=initial_sync_at,
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    stale_acc = AccountData(
        external_id="eb-old-uid-1",
        name="Checking",
        type="checking",
        balance=Decimal("250.00"),
        currency="EUR",
    )
    stale_tx = TransactionData(
        external_id="stale-tx-1",
        description="Stale Auth Purchase",
        amount=Decimal("19.99"),
        date=date(2026, 10, 2),
        type="debit",
        currency="EUR",
    )

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"session_id_enc": "old-enc"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[stale_acc])
    mock_provider.get_transactions = AsyncMock(return_value=[stale_tx])
    mock_provider.get_bills = AsyncMock(return_value=[])

    real_get_connection = connection_service.get_connection
    for_update_calls = 0

    async def get_connection_with_mid_sync_reconnect(
        sess, conn_id, workspace_id, *, for_update: bool = False
    ):
        nonlocal for_update_calls
        if for_update and conn_id == eb_conn_id:
            for_update_calls += 1
            if for_update_calls == 2:
                # Simulate a reconnect committing right after sync's registration
                # commit and before sync reacquires the connection row lock.
                target = await sess.get(BankConnection, conn_id, populate_existing=True)
                assert target is not None
                connection_service._reactivate_connection(
                    target,
                    ConnectionData(
                        external_id="eb-sess-new",
                        institution_name="JointBank",
                        credentials={"session_id_enc": "new-enc"},
                        accounts=[],
                    ),
                )
                await sess.execute(
                    sa_update(AccountConnectionLink)
                    .where(AccountConnectionLink.connection_id == conn_id)
                    .values(last_synced_at=None)
                )
                await sess.commit()
        return await real_get_connection(sess, conn_id, workspace_id, for_update=for_update)

    p1, p2, p3 = _patch_sync_helpers()
    with (
        patch("app.services.connection_service.get_provider", return_value=mock_provider),
        patch(
            "app.services.connection_service.get_connection",
            side_effect=get_connection_with_mid_sync_reconnect,
        ),
        p1,
        p2,
        p3,
    ):
        with pytest.raises(connection_service.ConnectionReconnectedError):
            await connection_service.sync_connection(
                session, eb_conn_id, ws_id, uid
            )

    assert mock_provider.get_transactions.call_count == 0
    session.expunge_all()
    reloaded = await session.get(BankConnection, eb_conn_id)
    assert reloaded is not None
    assert reloaded.status == "active"
    assert reloaded.external_id == "eb-sess-new"
    assert reloaded.credentials == {"session_id_enc": "new-enc"}
    assert reloaded.last_sync_at is None
    links = (
        await session.execute(
            select(AccountConnectionLink).where(
                AccountConnectionLink.connection_id == eb_conn_id
            )
        )
    ).scalars().all()
    assert links and all(link.last_synced_at is None for link in links)
    txns = (
        await session.execute(
            select(Transaction).where(Transaction.workspace_id == ws_id)
        )
    ).scalars().all()
    assert txns == []

    # Same protection for Pluggy update-mode reconnect where external_id,
    # credentials, and last_sync_at=None are all identical and only
    # RECONNECT_GENERATION_KEY changes.
    pluggy_conn_id = uuid.uuid4()
    pluggy_creds = {"item_id": "pluggy-item-same", "connector_id": "201"}
    pluggy_conn = BankConnection(
        id=pluggy_conn_id,
        user_id=uid,
        workspace_id=ws_id,
        provider="pluggy",
        external_id="pluggy-item-same",
        institution_name="Itaú",
        credentials=dict(pluggy_creds),
        status="active",
        last_sync_at=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(pluggy_conn)
    await session.commit()

    pluggy_mock = AsyncMock()
    pluggy_mock.refresh_credentials = AsyncMock(return_value=dict(pluggy_creds))
    pluggy_mock.get_institution_logo = AsyncMock(return_value=None)
    pluggy_mock.get_accounts = AsyncMock(
        return_value=[
            AccountData(
                external_id="pluggy-same-acc",
                name="Conta Corrente",
                type="checking",
                balance=Decimal("100.00"),
                currency="BRL",
            )
        ]
    )
    pluggy_mock.get_transactions = AsyncMock(return_value=[stale_tx])
    pluggy_mock.get_bills = AsyncMock(return_value=[])

    pluggy_lock_calls = 0

    async def get_connection_pluggy_update_reconnect(
        sess, conn_id, workspace_id, *, for_update: bool = False
    ):
        nonlocal pluggy_lock_calls
        if for_update and conn_id == pluggy_conn_id:
            pluggy_lock_calls += 1
            if pluggy_lock_calls == 2:
                target = await sess.get(BankConnection, conn_id, populate_existing=True)
                assert target is not None
                connection_service._reactivate_connection(
                    target,
                    ConnectionData(
                        external_id="pluggy-item-same",
                        institution_name="Itaú",
                        credentials=dict(pluggy_creds),
                        accounts=[],
                    ),
                )
                await sess.commit()
        return await real_get_connection(sess, conn_id, workspace_id, for_update=for_update)

    p1, p2, p3 = _patch_sync_helpers()
    with (
        patch("app.services.connection_service.get_provider", return_value=pluggy_mock),
        patch(
            "app.services.connection_service.get_connection",
            side_effect=get_connection_pluggy_update_reconnect,
        ),
        p1,
        p2,
        p3,
    ):
        with pytest.raises(connection_service.ConnectionReconnectedError):
            await connection_service.sync_connection(
                session, pluggy_conn_id, ws_id, uid
            )

    assert pluggy_mock.get_transactions.call_count == 0
    session.expunge_all()
    reloaded_pluggy = await session.get(BankConnection, pluggy_conn_id)
    assert reloaded_pluggy is not None
    assert reloaded_pluggy.status == "active"
    assert reloaded_pluggy.last_sync_at is None
