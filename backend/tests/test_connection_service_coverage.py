"""Coverage-focused tests for app/services/connection_service.py.

Targets branches not exercised by test_connection_service.py:
- get_oauth_url / get_reauth_url (state storage + provider delegation)
- list_provider_institutions (normalization)
- _sync_holdings (create / archive / withdrawn paths)
- sync_connection: trigger_refresh outcomes, fuzzy-match merge, SessionExpired
  and ProviderUserActionRequired error handling, status reset on generic error
- handle_oauth_callback: reconnect path + invalid-state guards
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.base import (
    AccountData,
    ConnectionData,
    HoldingData,
    InstitutionData,
    InstitutionListData,
    ProviderNotConfiguredError,
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
)
from app.services.connection_service import (
    _sync_holdings,
    get_oauth_url,
    get_reauth_url,
    handle_oauth_callback,
    list_provider_institutions,
    sync_connection,
)


async def _make_connection(
    session: AsyncSession, user_id, name="Bank", settings=None,
) -> BankConnection:
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


def _patch_helpers():
    return (
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    )


# ---------------------------------------------------------------------------
# get_oauth_url / get_reauth_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_oauth_url(test_user, test_workspace):
    mock_provider = AsyncMock()
    mock_provider.redirect_uri = "https://app/redirect"
    mock_provider.get_oauth_url = AsyncMock(return_value="https://bank/authorize?state=xyz")
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.oauth_state.store_state",
               new_callable=AsyncMock, return_value="state-token"):
        url = await get_oauth_url("pluggy", test_user.id, test_workspace.id, {"country": "BR"})
    assert url == "https://bank/authorize?state=xyz"
    mock_provider.get_oauth_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_reauth_url(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, settings={"flow_params": {"country": "DE"}})
    mock_provider = AsyncMock()
    mock_provider.redirect_uri = "https://app/redirect"
    mock_provider.reauth_url = AsyncMock(return_value="https://bank/reauth")
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.oauth_state.store_state",
               new_callable=AsyncMock, return_value="state-token"):
        url = await get_reauth_url(session, conn.id, test_workspace.id, test_user.id)
    assert url == "https://bank/reauth"


@pytest.mark.asyncio
async def test_get_reauth_url_not_found(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="not found"):
        await get_reauth_url(session, uuid.uuid4(), test_workspace.id, test_user.id)


# ---------------------------------------------------------------------------
# list_provider_institutions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_provider_institutions():
    mock_provider = AsyncMock()
    mock_provider.list_institutions = AsyncMock(return_value=InstitutionListData(
        countries=["DE", "FR"],
        institutions=[
            InstitutionData(
                name="revolut_de", display_name="Revolut", country="DE",
                logo="logo.png", bic="REVODEB2", psu_types=["personal"],
                max_consent_days=90, max_history_days=730,
            ),
        ],
    ))
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        result = await list_provider_institutions("enable_banking", "DE")
    assert result["countries"] == ["DE", "FR"]
    assert len(result["institutions"]) == 1
    inst = result["institutions"][0]
    assert inst["name"] == "revolut_de"
    assert inst["display_name"] == "Revolut"
    assert inst["bic"] == "REVODEB2"


# ---------------------------------------------------------------------------
# _sync_holdings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_holdings_creates_asset(session: AsyncSession, test_user):
    conn = await _make_connection(session, test_user.id, "Broker")
    mock_provider = AsyncMock()
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="hold-1", name="VWCE ETF", currency="EUR",
            current_value=Decimal("1200.00"), quantity=Decimal("10"),
            purchase_price=Decimal("1000.00"), purchase_date=date(2026, 1, 1),
        ),
    ])
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()

    asset = (await session.execute(
        select(Asset).where(Asset.external_id == "hold-1")
    )).scalar_one()
    assert asset.name == "VWCE ETF"
    assert asset.type == "investment"
    assert asset.connection_id == conn.id


@pytest.mark.asyncio
async def test_sync_holdings_provider_error_swallowed(session: AsyncSession, test_user):
    """A failing get_holdings must not raise — investment data is best-effort."""
    conn = await _make_connection(session, test_user.id, "FlakyBroker")
    mock_provider = AsyncMock()
    mock_provider.get_holdings = AsyncMock(side_effect=RuntimeError("500"))
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})  # no raise


@pytest.mark.asyncio
async def test_sync_holdings_withdrawn_new_skipped(session: AsyncSession, test_user):
    """A brand-new holding reported as withdrawn is skipped entirely."""
    conn = await _make_connection(session, test_user.id, "ClosedBroker")
    mock_provider = AsyncMock()
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="dead-1", name="Closed Fund", currency="BRL",
            current_value=Decimal("0"), is_withdrawn=True,
        ),
    ])
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()
    rows = (await session.execute(
        select(Asset).where(Asset.external_id == "dead-1")
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_sync_holdings_archives_disappeared(session: AsyncSession, test_user):
    """A previously synced asset no longer in the provider response is archived."""
    conn = await _make_connection(session, test_user.id, "GoneBroker")
    existing = Asset(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        source="test", external_id="gone-1", name="Old", type="investment",
        currency="BRL", is_archived=False, valuation_method="manual",
    )
    session.add(existing)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings = AsyncMock(return_value=[])  # nothing returned
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()

    await session.refresh(existing)
    assert existing.is_archived is True


# ---------------------------------------------------------------------------
# sync_connection: trigger_refresh outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_trigger_refresh_needs_user_action(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "RefreshBank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.trigger_refresh = AsyncMock(return_value="needs_user_action")

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(RuntimeError, match="reconnect"):
            await sync_connection(
                session, conn.id, test_workspace.id, test_user.id,
                trigger_provider_refresh=True,
            )
    # status marked error and committed
    refreshed = (await session.execute(
        select(BankConnection).where(BankConnection.id == conn.id)
    )).scalar_one()
    assert refreshed.status == "error"


@pytest.mark.asyncio
async def test_sync_trigger_refresh_refreshed_then_reads(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "RefreshOK")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.trigger_refresh = AsyncMock(return_value="refreshed")
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="ra-1", name="Checking", type="checking",
                    balance=Decimal("10"), currency="BRL"),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        result, _ = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id,
            trigger_provider_refresh=True,
        )
    assert result.status == "active"
    mock_provider.trigger_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# sync_connection: fuzzy match merges a manual transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_fuzzy_matches_manual_transaction(session: AsyncSession, test_user, test_workspace):
    """A synced tx that fuzzy-matches an existing manual tx merges into it
    (incrementing merged_count) rather than inserting a duplicate."""
    conn = await _make_connection(session, test_user.id, "FuzzyBank")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        external_id="fz-acc-1", name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    manual = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="STARBUCKS COFFEE", amount=Decimal("25.00"),
        date=date(2026, 4, 10), type="debit", source="manual",
        external_id=None, created_at=datetime.now(timezone.utc),
    )
    session.add(manual)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="fz-acc-1", name="Checking", type="checking",
                    balance=Decimal("0"), currency="BRL"),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="fz-tx-1", description="STARBUCKS COFFEE",
            amount=Decimal("25.00"), date=date(2026, 4, 10),
            type="debit", currency="BRL", payee="Starbucks",
        ),
    ])

    p1, p2, p3 = _patch_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        result, merged = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result.status == "active"
    assert merged == 1
    await session.refresh(manual)
    assert manual.external_id == "fz-tx-1"
    assert manual.source == "sync"
    assert manual.payee == "Starbucks"


# ---------------------------------------------------------------------------
# sync_connection: SessionExpired / ProviderUserActionRequired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_unregistered_provider_keeps_status(
    session: AsyncSession, test_user, test_workspace,
):
    """A provider missing from the registry is a server misconfiguration (e.g.
    the Celery worker not loading .env) — the connection must keep its status
    instead of flipping to "error", which would show a misleading reconnect
    banner for perfectly healthy credentials."""
    conn = await _make_connection(session, test_user.id, "GhostBank")
    conn_id = conn.id

    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    refreshed = (await session.execute(
        select(BankConnection).where(BankConnection.id == conn_id)
    )).scalar_one()
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_sync_session_expired_marks_expired(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "ExpiredBank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(side_effect=SessionExpiredError("gone"))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(SessionExpiredError):
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    refreshed = (await session.execute(
        select(BankConnection).where(BankConnection.id == conn.id)
    )).scalar_one()
    assert refreshed.status == "expired"


@pytest.mark.asyncio
async def test_sync_user_action_required_marks_error_status(
    session: AsyncSession, test_user, test_workspace,
):
    conn = await _make_connection(session, test_user.id, "ActionBank")
    conn_id = conn.id
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(
        side_effect=ProviderUserActionRequired("link accounts", code="restricted_mode")
    )

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(ProviderUserActionRequired):
            await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    # User-action failures are visible in the UI via the reconnect banner.
    refreshed = (await session.execute(
        select(BankConnection).where(BankConnection.id == conn_id)
    )).scalar_one()
    assert refreshed.status == "error"


# ---------------------------------------------------------------------------
# handle_oauth_callback: state guards + reconnect path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_invalid_state(session: AsyncSession, test_user, test_workspace):
    with patch("app.services.connection_service.oauth_state.consume_state",
               new_callable=AsyncMock, return_value=None):
        with pytest.raises(ValueError, match="invalid or expired"):
            await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "code", state="bad-state",
            )


@pytest.mark.asyncio
async def test_oauth_callback_state_user_mismatch(session: AsyncSession, test_user, test_workspace):
    with patch("app.services.connection_service.oauth_state.consume_state",
               new_callable=AsyncMock,
               return_value={"user_id": str(uuid.uuid4()),
                             "workspace_id": str(test_workspace.id),
                             "provider": "test"}):
        with pytest.raises(ValueError, match="user does not match"):
            await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "code", state="s",
            )


@pytest.mark.asyncio
async def test_oauth_callback_missing_provider(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="missing provider"):
        await handle_oauth_callback(session, test_workspace.id, test_user.id, "code")


@pytest.mark.asyncio
async def test_oauth_callback_reconnect_updates_existing(
    session: AsyncSession, test_user, test_workspace,
):
    """A callback carrying reconnect_connection_id updates the existing row in
    place instead of creating a new connection."""
    existing = await _make_connection(session, test_user.id, "OldName")

    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="reconnected-ext",
        institution_name="NewName",
        credentials={"token": "fresh"},
        accounts=[],
    ))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.oauth_state.consume_state",
               new_callable=AsyncMock,
               return_value={
                   "user_id": str(test_user.id),
                   "workspace_id": str(test_workspace.id),
                   "provider": "test",
                   "reconnect_connection_id": str(existing.id),
               }):
        result = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "code", state="s",
        )

    assert result.id == existing.id
    assert result.external_id == "reconnected-ext"
    assert result.institution_name == "NewName"
    assert result.credentials == {"token": "fresh"}
    assert result.status == "active"
    assert result.last_sync_at is None


# ---------------------------------------------------------------------------
# sync_connection: existing-tx update (pending -> posted) + new insert + FX
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_updates_existing_pending_to_posted(
    session: AsyncSession, test_user, test_workspace,
):
    """An already-synced tx matched by external_id flips pending->posted."""
    conn = await _make_connection(session, test_user.id, "PendingPostedBank")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        external_id="pp-acc-1", name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        external_id="pp-tx-1", description="PENDING CHARGE",
        amount=Decimal("60"), date=date(2026, 4, 12), type="debit",
        currency="BRL", source="sync", status="pending",
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="pp-acc-1", name="Checking", type="checking",
                    balance=Decimal("0"), currency="BRL"),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="pp-tx-1", description="PENDING CHARGE",
            amount=Decimal("60"), date=date(2026, 4, 12), type="debit",
            currency="BRL", status="posted",
        ),
    ])

    p1, p2, p3 = _patch_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "pp-tx-1")
    )).scalar_one()
    assert row.status == "posted"


@pytest.mark.asyncio
async def test_sync_new_transaction_with_payee_and_fx(
    session: AsyncSession, test_user, test_workspace,
):
    """New synced tx with a payee and a bank-provided FX conversion exercises
    the payee resolution + amount_primary/fx_rate_used branch (no fallback
    stamp_primary_amount call)."""
    conn = await _make_connection(session, test_user.id, "FxBank")
    # User primary currency is BRL (from conftest). Account in BRL, tx in USD
    # with bank-provided BRL amount -> fx branch.
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="fx-acc-1", name="Checking", type="checking",
                    balance=Decimal("0"), currency="BRL"),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="fx-tx-1", description="AWS USD",
            amount=Decimal("10.00"), date=date(2026, 4, 1), type="debit",
            currency="USD", amount_in_account_currency=Decimal("52.00"),
            payee="Amazon Web Services",
        ),
    ])

    # Don't patch stamp_primary_amount here — the fx branch must bypass it.
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "fx-tx-1")
    )).scalar_one()
    assert row.payee == "Amazon Web Services"
    assert row.payee_id is not None
    assert row.amount_primary == Decimal("52.00")
    assert row.fx_rate_used == Decimal("5.2")


# ---------------------------------------------------------------------------
# _sync_holdings: update an existing asset in place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_holdings_updates_existing_asset(session: AsyncSession, test_user):
    """A holding matching an existing asset by external_id updates it in place
    (name/currency/units) rather than creating a duplicate."""
    conn = await _make_connection(session, test_user.id, "UpdBroker")
    existing = Asset(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        source="test", external_id="upd-1", name="Old Name", type="investment",
        currency="USD", units=Decimal("5"), is_archived=False,
        valuation_method="manual",
    )
    session.add(existing)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="upd-1", name="New Name", currency="EUR",
            current_value=Decimal("999.00"), quantity=Decimal("8"),
        ),
    ])
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()

    rows = (await session.execute(
        select(Asset).where(Asset.external_id == "upd-1")
    )).scalars().all()
    assert len(rows) == 1  # updated, not duplicated
    assert rows[0].name == "New Name"
    assert rows[0].currency == "EUR"
    assert rows[0].units == Decimal("8")
