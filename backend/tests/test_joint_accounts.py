"""Tests for cross-connection joint account deduplication and failover (#1341)."""
from __future__ import annotations

import uuid
import warnings
from datetime import date, datetime, timezone
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

    fp = hash_account_identifier("enable_banking", "DE89370400440532013001")
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

    fp = hash_account_identifier("pluggy", "1234567890")
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

    fp_a = hash_account_identifier("enable_banking", "DE89370400440532011111")
    fp_b = hash_account_identifier("enable_banking", "DE89370400440532022222")
    fp_j = hash_account_identifier("enable_banking", "DE89370400440532099999")
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
    dup = await connection_service._find_synced_duplicate(
        session,
        acc.id,
        rotated,
        {"new-eb-booked-1"},
        incoming_batch=[rotated],
        provider="enable_banking",
    )
    assert dup is not None and dup.id == settled_hold.id

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
            incoming_batch=[rotated, twin],
            provider="enable_banking",
        )
        is None
    )

    fp = hash_account_identifier("pluggy", "555566667777")
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
