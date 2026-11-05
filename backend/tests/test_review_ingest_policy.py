"""PR-2 §8 ingest/default/exemption tests (`test_review_ingest_policy.py`).

Named cases: import rows land `needs_review` by default; sync rows land
`needs_review` by default; manual create lands `reviewed` with BOTH
stamps (gate §15-A1 actor-driven creation); synthetic rows are exempt;
placeholder upgrades keep review state; resync never resets review
state. Also covers the two workspace-policy knobs (§7.1) since they
change exactly these landing states.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.transaction import TransactionImport
from app.services.connection_service import sync_connection
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import create_recurring_transaction
from app.services.review_service import is_observed_money


async def _make_connection(session, user_id, name: str = "Test Bank", settings=None):
    """Local copy of the helper in tests/test_connection_service.py
    (helpers live per test module; services don't export them)."""
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name=name, credentials={"token": "fake"},
        status="active", settings=settings,
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def _run_import(session, test_workspace, test_user, account, txns, source="csv"):
    with patch("app.services.import_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        return await import_transactions(
            session, test_workspace.id, test_user.id, account.id, txns, source,
        )


def _assert_paired(tx) -> None:
    assert (tx.reviewed_at is None) == (tx.reviewed_by_id is None), (
        f"half stamps: reviewed_at={tx.reviewed_at!r} reviewed_by_id={tx.reviewed_by_id!r}"
    )


@pytest.mark.asyncio
async def test_import_rows_default_needs_review(
    session, test_user, test_account, test_workspace
):
    """Observed money from a file import lands `needs_review` by default
    (§7.1), with NO fabricated provenance (§15-A1)."""
    txns = [TransactionImport(
        description="Imported row",
        amount=Decimal("123.45"),
        date=date(2026, 3, 2),
        type="debit",
    )]
    imported, skipped, _, _ = await _run_import(session, test_workspace, test_user, test_account, txns)
    assert imported == 1 and skipped == 0

    row = (await session.execute(
        select(Transaction).where(Transaction.description == "Imported row")
    )).scalar_one()
    assert row.review_status == "needs_review"
    assert row.reviewed_at is None and row.reviewed_by_id is None
    _assert_paired(row)


@pytest.mark.asyncio
async def test_sync_rows_default_needs_review(session, test_user, test_workspace):
    """Provider sync (real `sync_connection` path, mocked provider) lands
    observed rows `needs_review` by default."""
    conn = await _make_connection(session, test_user.id, "Ingest Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="ing-acc-1", name="Checking",
            type="checking", balance=Decimal("900"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="ing-tx-1", description="SYNCED GROCERY",
            amount=Decimal("80"), date=date.today(), type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, merged, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"
    row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "ing-tx-1")
    )).scalar_one()
    assert row.review_status == "needs_review"
    assert row.reviewed_at is None and row.reviewed_by_id is None
    _assert_paired(row)
    assert merged == 0


@pytest.mark.asyncio
async def test_manual_create_default_reviewed_stamped(
    client: AsyncClient, auth_headers, test_user, test_account
):
    """Gate §15-A1, manual-create direction: creation is actor-driven, so
    a manual row landing `reviewed` (the §7.1 default) carries BOTH
    stamps — the creating user as the actor."""
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "Typed by hand",
            "amount": "10.00",
            "date": "2026-03-05",
            "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] is not None
    assert body["reviewed_by_id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_synthetic_rows_exempt(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """§7.2 exemptions: transfers, recurring placeholders, opening
    balances (and bill_charge synthetics) are the user's own machinery —
    they land `reviewed` with NULL stamps; only observed money can land
    `needs_review`."""
    # The rule itself, unit-checked at its truth table.
    assert is_observed_money("csv") is True
    assert is_observed_money("ofx") is True
    assert is_observed_money("sync", "pluggy-tx-9") is True
    assert is_observed_money("sync", "bill_charge:rc:77:202603") is False
    assert is_observed_money("manual") is False
    assert is_observed_money("transfer") is False
    assert is_observed_money("settlement") is False
    assert is_observed_money("recurring") is False
    assert is_observed_money("opening_balance") is False

    # A real transfer path (the API route drives the same service the
    # app uses): both legs must be quiet.
    dest_resp = await client.post(
        "/api/accounts",
        json={"name": "Transfer Dest", "type": "savings", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    assert dest_resp.status_code in (200, 201), dest_resp.text
    dest_id = uuid.UUID(dest_resp.json()["id"])
    xfer = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(dest_id),
            "description": "Transfer probe",
            "amount": 50,
            "date": date.today().isoformat(),
        },
        headers=auth_headers,
    )
    assert xfer.status_code == 201, xfer.text
    # This test creates no other rows on either account, so these are
    # exactly the two legs the transfer service wrote.
    legs = (await session.execute(
        select(Transaction).where(
            Transaction.account_id.in_([test_account.id, dest_id]),
        )
    )).scalars().all()
    assert len(legs) == 2, [f"{r.account_id}:{r.description}" for r in legs]
    for leg in legs:
        assert leg.review_status == "reviewed"
        assert leg.reviewed_at is None and leg.reviewed_by_id is None
        _assert_paired(leg)

    # Direct constructions of the other synthetic sources: DB default
    # applies (reviewed, no provenance), never needs_review.
    for src in ("recurring", "opening_balance", "settlement"):
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description=f"synthetic {src}",
            amount=Decimal("1.00"), date=date(2026, 3, 6), type="debit",
            source=src, created_at=datetime.now(timezone.utc),
        )
        session.add(tx)
    await session.commit()
    for src in ("recurring", "opening_balance", "settlement"):
        tx = (await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == test_workspace.id,
                Transaction.description == f"synthetic {src}",
            )
        )).scalar_one()
        assert tx.review_status == "reviewed", src
        assert tx.reviewed_at is None and tx.reviewed_by_id is None
        _assert_paired(tx)


@pytest.mark.asyncio
async def test_placeholder_upgrade_keeps_review_state(
    session, test_user, test_account, test_workspace
):
    """A recurring placeholder that was already reviewed keeps its
    stamps through the import merge — the upgrade path rewrites money
    fields, not triage history (risk R3)."""
    bill = await create_recurring_transaction(
        session, test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Netflix Subscription",
            amount=Decimal("39.90"),
            currency="BRL",
            type="debit",
            frequency="monthly",
            start_date=date(2026, 1, 10),
            account_id=test_account.id,
        ),
    )
    bill_id = bill.id
    bill.next_occurrence = date(2026, 2, 10)
    reviewed_at = datetime.now(timezone.utc)
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        workspace_id=test_workspace.id, description="Netflix Subscription",
        amount=Decimal("39.90"), currency="BRL", date=date(2026, 1, 10),
        type="debit", source="recurring", status="posted",
        recurring_transaction_id=bill_id, created_at=datetime.now(timezone.utc),
        review_status="reviewed", reviewed_at=reviewed_at,
        reviewed_by_id=test_user.id,
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    # Different case bypasses exact-match dedup, so the recurring merge
    # (case-insensitive similarity) is what catches it.
    txns = [TransactionImport(
        description="NETFLIX SUBSCRIPTION",
        amount=Decimal("39.90"), date=date(2026, 1, 11), type="debit", currency="BRL",
    )]
    imported, skipped, _, _ = await _run_import(
        session, test_workspace, test_user, test_account, txns
    )
    assert imported == 1

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.account_id == test_account.id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert len(rows) == 1  # merged, no duplicate
    row = rows[0]
    assert row.id == placeholder_id
    assert row.source == "csv"  # money fields upgraded...
    assert row.review_status == "reviewed"  # ...review state untouched
    assert row.reviewed_at == reviewed_at
    assert row.reviewed_by_id == test_user.id
    _assert_paired(row)
    refreshed_bill = await session.get(RecurringTransaction, bill_id)
    assert refreshed_bill.next_occurrence == date(2026, 2, 10)


@pytest.mark.asyncio
async def test_resync_does_not_reset_review_state(session, test_user, test_workspace):
    """A row the user already reviewed stays reviewed when the provider
    re-delivers it on the next sync (update path), and an unreviewed row
    stays unreviewed — resyncs never re-quarantine finished work."""
    conn = await _make_connection(session, test_user.id, "Resync Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="res-acc-1", name="Checking",
            type="checking", balance=Decimal("300"), currency="BRL",
        ),
    ])
    tx_data = TransactionData(
        external_id="res-tx-1", description="RESYNC ROW",
        amount=Decimal("70"), date=date.today(), type="debit", currency="BRL",
    )
    second_data = TransactionData(
        external_id="res-tx-2", description="NEVER REVIEWED ROW",
        amount=Decimal("12"), date=date.today(), type="debit", currency="BRL",
    )
    mock_provider.get_transactions = AsyncMock(return_value=[tx_data, second_data])

    patches = (
        patch("app.services.connection_service.get_provider", return_value=mock_provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    )
    with patches[0], patches[1], patches[2], patches[3]:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

        # First pass: both need review.
        first = (await session.execute(
            select(Transaction).where(Transaction.external_id == "res-tx-1")
        )).scalar_one()
        assert first.review_status == "needs_review"

        # User reviews it (through the real chokepoint semantics:
        # actor-driven transition stamps both columns).
        first.review_status = "reviewed"
        first.reviewed_at = datetime.now(timezone.utc)
        first.reviewed_by_id = test_user.id
        await session.commit()
        reviewed_stamp = first.reviewed_at

        # Provider re-delivers the same rows on the next sync.
        conn.last_sync_at = None
        await session.commit()
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    after = (await session.execute(
        select(Transaction).where(Transaction.external_id == "res-tx-1")
    )).scalar_one()
    assert after.review_status == "reviewed", "resync re-quarantined reviewed work"
    assert after.reviewed_at == reviewed_stamp
    assert after.reviewed_by_id == test_user.id
    _assert_paired(after)

    untouched = (await session.execute(
        select(Transaction).where(Transaction.external_id == "res-tx-2")
    )).scalar_one()
    assert untouched.review_status == "needs_review"
    _assert_paired(untouched)


@pytest.mark.asyncio
async def test_workspace_policy_ingest_reviewed(
    session, test_user, test_account, test_workspace
):
    """§7.1: `ingest` policy flips landing state at BOTH ingest sites
    (import + sync), and a policy-landed `reviewed` row still has NULL
    stamps — no fabricated reviewer on a quiet landing."""
    test_workspace.review_policy = {"ingest": "reviewed"}
    await session.commit()

    txns = [TransactionImport(
        description="Policy import row",
        amount=Decimal("9.99"), date=date(2026, 3, 7), type="debit",
    )]
    await _run_import(session, test_workspace, test_user, test_account, txns)
    row = (await session.execute(
        select(Transaction).where(Transaction.description == "Policy import row")
    )).scalar_one()
    assert row.review_status == "reviewed"
    assert row.reviewed_at is None and row.reviewed_by_id is None

    # And the same policy applies to sync rows.
    conn = await _make_connection(session, test_user.id, "Policy Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="pol-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="pol-tx-1", description="POLICY SYNC ROW",
            amount=Decimal("7"), date=date.today(), type="debit", currency="BRL",
        ),
    ])
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    sync_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "pol-tx-1")
    )).scalar_one()
    assert sync_row.review_status == "reviewed"
    assert sync_row.reviewed_at is None and sync_row.reviewed_by_id is None


@pytest.mark.asyncio
async def test_workspace_policy_manual_needs_review(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """§7.1: `manual` policy can quarantine typed-in rows — they land
    `needs_review` with NULL stamps (the stamping rule is per-state, not
    per-path: A1 never allows a stamp beside `needs_review`)."""
    test_workspace.review_policy = {"manual": "needs_review"}
    await session.commit()

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "Typed under quarantine policy",
            "amount": "4.20",
            "date": "2026-03-08",
            "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["review_status"] == "needs_review"
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None


@pytest.mark.asyncio
async def test_review_policy_write_path_rejects_bad_values(
    client: AsyncClient, auth_headers, test_workspace
):
    """§7.1 exit check: the policy is a closed Literal set on write."""
    bad = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"review_policy": {"ingest": "maybe"},
              "expected_review_policy": {"ingest": "needs_review", "manual": "reviewed"}},
    )
    assert bad.status_code == 400, bad.text

    unknown_key = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"review_policy": {"auto_review": "reviewed"},
              "expected_review_policy": {"ingest": "needs_review", "manual": "reviewed"}},
    )
    assert unknown_key.status_code == 400, unknown_key.text

    good = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"review_policy": {"ingest": "reviewed", "manual": "needs_review"},
              "expected_review_policy": {"ingest": "needs_review", "manual": "reviewed"}},
    )
    assert good.status_code == 200, good.text
    assert good.json()["review_policy"] == {
        "ingest": "reviewed", "manual": "needs_review",
    }
