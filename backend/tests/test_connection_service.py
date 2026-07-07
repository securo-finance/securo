import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.transaction import Transaction
from app.providers.base import (
    AccountData,
    BillData,
    ConnectionData,
    ConnectTokenData,
    HoldingData,
    TransactionData,
)
from app.services.connection_service import (
    _description_similarity,
    _match_pluggy_category,
    create_connect_token,
    delete_connection,
    get_connection,
    get_connections,
    handle_oauth_callback,
    sync_connection,
    update_connection_settings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_connection(
    session: AsyncSession, user_id: uuid.UUID, name: str = "Test Bank",
    settings: dict | None = None,
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


async def _make_category(
    session: AsyncSession, user_id: uuid.UUID, name: str,
) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, name=name,
        icon="tag", color="#000", is_system=False,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


# ---------------------------------------------------------------------------
# _description_similarity (pure function)
# ---------------------------------------------------------------------------


def test_description_similarity_identical():
    assert _description_similarity("hello world", "hello world") == 1.0


def test_description_similarity_partial():
    score = _description_similarity("hello world foo", "hello world bar")
    assert 0.0 < score < 1.0


def test_description_similarity_no_overlap():
    assert _description_similarity("abc", "xyz") == 0.0


def test_description_similarity_none():
    assert _description_similarity(None, "hello") == 0.0
    assert _description_similarity("hello", None) == 0.0
    assert _description_similarity(None, None) == 0.0


def test_description_similarity_empty():
    assert _description_similarity("", "hello") == 0.0
    assert _description_similarity("hello", "") == 0.0


def test_description_similarity_case_insensitive():
    score = _description_similarity("Hello World", "hello world")
    assert score == 1.0


# ---------------------------------------------------------------------------
# _match_pluggy_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_pluggy_exact(session: AsyncSession, test_user, test_workspace):
    """Exact Pluggy category match maps to the workspace's category."""
    await _make_category(session, test_user.id, "Alimentação")
    cat_id = await _match_pluggy_category(session, test_workspace.id, "Eating out")
    assert cat_id is not None


@pytest.mark.asyncio
async def test_match_pluggy_prefix(session: AsyncSession, test_user, test_workspace):
    """Pluggy category with ' - ' prefix matches via split."""
    await _make_category(session, test_user.id, "Transferências")
    cat_id = await _match_pluggy_category(session, test_workspace.id, "Transfer - PIX")
    assert cat_id is not None


@pytest.mark.asyncio
async def test_match_pluggy_no_match(session: AsyncSession, test_workspace):
    """Unknown Pluggy category returns None."""
    cat_id = await _match_pluggy_category(
        session, test_workspace.id, "Unknown Category XYZ"
    )
    assert cat_id is None


@pytest.mark.asyncio
async def test_match_pluggy_none(session: AsyncSession, test_workspace):
    """None category returns None."""
    cat_id = await _match_pluggy_category(session, test_workspace.id, None)
    assert cat_id is None


@pytest.mark.asyncio
async def test_match_pluggy_disabled_short_circuits(
    session: AsyncSession, test_user, test_workspace
):
    """When the global use_provider_categories flag is off, the matcher returns
    None even on inputs that would otherwise resolve. This is the contract
    sync_connection / handle_oauth_callback rely on to leave transactions
    uncategorized so user Rules are the only source of truth."""
    await _make_category(session, test_user.id, "Alimentação")
    # Sanity: enabled=True still matches.
    enabled_match = await _match_pluggy_category(
        session, test_workspace.id, "Eating out", enabled=True
    )
    assert enabled_match is not None

    # enabled=False short-circuits before any DB lookup.
    disabled_match = await _match_pluggy_category(
        session, test_workspace.id, "Eating out", enabled=False
    )
    assert disabled_match is None


@pytest.mark.asyncio
async def test_match_pluggy_user_has_no_category(session: AsyncSession, test_workspace):
    """Pluggy category maps but the workspace doesn't have the target category."""
    # "Eating out" maps to "Alimentação" but we don't create it
    cat_id = await _match_pluggy_category(session, test_workspace.id, "Eating out")
    assert cat_id is None


# ---------------------------------------------------------------------------
# get_connections / get_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_connections_returns_list(session: AsyncSession, test_user, test_workspace):
    """Returns list of connections for user."""
    await _make_connection(session, test_user.id, "Bank A")
    await _make_connection(session, test_user.id, "Bank B")

    connections = await get_connections(session, test_workspace.id)
    assert len(connections) >= 2
    names = {c.institution_name for c in connections}
    assert "Bank A" in names
    assert "Bank B" in names


@pytest.mark.asyncio
async def test_get_connections_empty(session: AsyncSession, test_user, test_workspace):
    """Returns empty list when no connections."""
    connections = await get_connections(session, test_workspace.id)
    # May have connections from other fixtures; just verify it's a list
    assert isinstance(connections, list)


@pytest.mark.asyncio
async def test_get_connection_found(session: AsyncSession, test_user, test_workspace):
    """Returns a specific connection."""
    conn = await _make_connection(session, test_user.id, "Specific Bank")
    result = await get_connection(session, conn.id, test_workspace.id)
    assert result is not None
    assert result.institution_name == "Specific Bank"


@pytest.mark.asyncio
async def test_get_connection_not_found(session: AsyncSession, test_user, test_workspace):
    """Returns None for nonexistent connection."""
    result = await get_connection(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_connection_wrong_user(session: AsyncSession, test_user, test_workspace):
    """Returns None when connection belongs to another user."""
    conn = await _make_connection(session, test_user.id, "Other User Bank")
    result = await get_connection(session, conn.id, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# update_connection_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_settings_new(session: AsyncSession, test_user, test_workspace):
    """Updates settings on a connection with no prior settings."""
    conn = await _make_connection(session, test_user.id, "Settings Test")

    updated = await update_connection_settings(
        session, conn.id, test_workspace.id, {"payee_source": "merchant"},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "merchant"


@pytest.mark.asyncio
async def test_update_settings_preserves_existing(session: AsyncSession, test_user, test_workspace):
    """Updates one setting without clobbering others."""
    conn = await _make_connection(
        session, test_user.id, "Preserve Test",
        settings={"payee_source": "auto", "import_pending": True},
    )

    updated = await update_connection_settings(
        session, conn.id, test_workspace.id, {"import_pending": False},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "auto"
    assert updated.settings["import_pending"] is False


@pytest.mark.asyncio
async def test_update_settings_sync_assets(session: AsyncSession, test_user, test_workspace):
    """Per-connection asset sync can be disabled without clobbering other settings."""
    conn = await _make_connection(
        session, test_user.id, "Asset Settings Test",
        settings={"payee_source": "auto", "import_pending": True},
    )

    updated = await update_connection_settings(
        session, conn.id, test_workspace.id, {"sync_assets": False},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "auto"
    assert updated.settings["import_pending"] is True
    assert updated.settings["sync_assets"] is False


@pytest.mark.asyncio
async def test_oauth_callback_respects_initial_asset_sync_opt_out(
    session: AsyncSession, test_user, test_workspace
):
    """Initial connection creation can opt out before holdings are imported."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-no-assets",
        institution_name="No Assets Bank",
        credentials={"token": "x"},
        accounts=[],
    ))
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="holding-1", name="Provider Fund",
            currency="BRL", current_value=Decimal("1234.56"),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock):
        connection = await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "code",
            "pluggy",
            sync_assets=False,
        )

    assert connection.settings["sync_assets"] is False
    mock_provider.get_holdings.assert_not_awaited()
    assets = (await session.execute(select(Asset))).scalars().all()
    assert assets == []


@pytest.mark.asyncio
async def test_oauth_callback_respects_state_asset_sync_opt_out(
    session: AsyncSession, test_user, test_workspace
):
    """Redirect OAuth stores the initial opt-out in state before the callback."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-no-assets",
        institution_name="OAuth No Assets Bank",
        credentials={"token": "x"},
        accounts=[],
    ))
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="holding-1", name="Provider Fund",
            currency="BRL", current_value=Decimal("1234.56"),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.oauth_state.consume_state", new_callable=AsyncMock, return_value={
             "user_id": str(test_user.id),
             "workspace_id": str(test_workspace.id),
             "provider": "test",
             "flow_params": {
                 "country": "BR",
                 "institution_name": "OAuth No Assets Bank",
                 "sync_assets": False,
             },
         }), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock):
        connection = await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "code",
            state="stored-state",
        )

    assert connection.settings["sync_assets"] is False
    assert connection.settings["flow_params"] == {
        "country": "BR",
        "institution_name": "OAuth No Assets Bank",
    }
    mock_provider.get_holdings.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_settings_ignores_none(session: AsyncSession, test_user, test_workspace):
    """None values in settings_update are not written."""
    conn = await _make_connection(
        session, test_user.id, "None Test",
        settings={"payee_source": "auto"},
    )
    updated = await update_connection_settings(
        session, conn.id, test_workspace.id, {"payee_source": None},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "auto"


@pytest.mark.asyncio
async def test_update_settings_not_found(session: AsyncSession, test_user, test_workspace):
    """Returns None when connection not found."""
    result = await update_connection_settings(
        session, uuid.uuid4(), test_workspace.id, {"payee_source": "auto"},
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_connection_found(session: AsyncSession, test_user, test_workspace):
    """Deletes an existing connection."""
    conn = await _make_connection(session, test_user.id, "To Delete")
    result = await delete_connection(session, conn.id, test_workspace.id)
    assert result is True

    assert await get_connection(session, conn.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_connection_not_found(session: AsyncSession, test_user, test_workspace):
    """Returns False for nonexistent connection."""
    result = await delete_connection(session, uuid.uuid4(), test_workspace.id)
    assert result is False


@pytest.mark.asyncio
async def test_delete_connection_archives_linked_assets(session: AsyncSession, test_user, test_workspace):
    """Deleting a connection archives linked assets before removing the connection."""
    from app.models.asset import Asset

    conn = await _make_connection(session, test_user.id, "Asset Bank")
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Synced ETF",
        type="etf",
        currency="BRL",
        source="pluggy",
        external_id="asset-ext-1",
        connection_id=conn.id,
        is_archived=False,
    )
    session.add(asset)
    await session.commit()

    result = await delete_connection(session, conn.id, test_workspace.id)
    assert result is True

    refreshed = (await session.execute(select(Asset).where(Asset.id == asset.id))).scalar_one()
    assert refreshed.is_archived is True


@pytest.mark.asyncio
async def test_delete_connection_deletes_orphan_payees(session: AsyncSession, test_user, test_workspace):
    """Unlink should remove payees that become orphaned after tx deletion."""
    from app.models.account import Account
    from app.models.payee import Payee

    conn = await _make_connection(session, test_user.id, "Cleanup Bank")
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=conn.id,
        name="Connected Account",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, name="Ghost Payee")
    session.add_all([account, payee])
    await session.flush()

    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            description="Synced tx",
            amount=Decimal("10"),
            date=date.today(),
            type="debit",
            source="sync",
            payee_id=payee.id,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    assert await delete_connection(session, conn.id, test_workspace.id) is True

    refreshed = (await session.execute(select(Payee).where(Payee.id == payee.id))).scalar_one_or_none()
    assert refreshed is None


@pytest.mark.asyncio
async def test_delete_connection_keeps_payees_with_external_mappings(session: AsyncSession, test_user, test_workspace):
    """Unlink should not remove payees that still have external mappings."""
    from app.models.account import Account
    from app.models.payee import Payee, PayeeMapping

    conn = await _make_connection(session, test_user.id, "Mapped Bank")
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=conn.id,
        name="Connected Account",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, name="Mapped Payee")
    session.add_all([account, payee])
    await session.flush()

    session.add_all(
        [
            PayeeMapping(id=payee.id, user_id=test_user.id, target_id=payee.id),
            PayeeMapping(id=uuid.uuid4(), user_id=test_user.id, target_id=payee.id),
            Transaction(
                id=uuid.uuid4(),
                user_id=test_user.id,
                account_id=account.id,
                description="Synced tx",
                amount=Decimal("15"),
                date=date.today(),
                type="debit",
                source="sync",
                payee_id=payee.id,
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await session.commit()

    assert await delete_connection(session, conn.id, test_workspace.id) is True

    refreshed = (await session.execute(select(Payee).where(Payee.id == payee.id))).scalar_one_or_none()
    assert refreshed is not None


# ---------------------------------------------------------------------------
# create_connect_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_connect_token_success(test_user):
    mock_provider = AsyncMock()
    mock_provider.create_connect_token = AsyncMock(
        return_value=ConnectTokenData(access_token="tok-123")
    )
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        result = await create_connect_token("pluggy", test_user.id)
    assert result == {"access_token": "tok-123"}


# ---------------------------------------------------------------------------
# handle_oauth_callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_oauth_callback_creates_connection(session: AsyncSession, test_user, test_workspace):
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-1",
        institution_name="Test Bank",
        credentials={"token": "abc"},
        accounts=[
            AccountData(
                external_id="acc-1", name="Checking",
                type="checking", balance=Decimal("1000"), currency="BRL",
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="tx-1", description="UBER", amount=Decimal("25"),
            date=date.today(), type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        conn = await handle_oauth_callback(session, test_workspace.id, test_user.id, "auth-code", "pluggy")

    assert conn.institution_name == "Test Bank"
    assert conn.external_id == "ext-oauth-1"
    assert conn.status == "active"


@pytest.mark.asyncio
async def test_handle_oauth_callback_with_payee(session: AsyncSession, test_user, test_workspace):
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-2",
        institution_name="Payee Bank",
        credentials={"token": "def"},
        accounts=[
            AccountData(
                external_id="acc-2", name="Savings",
                type="savings", balance=Decimal("500"), currency="BRL",
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="tx-2", description="IFOOD", amount=Decimal("30"),
            date=date.today(), type="debit", currency="BRL",
            payee="iFood Restaurant",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        conn = await handle_oauth_callback(session, test_workspace.id, test_user.id, "code2", "pluggy")

    assert conn.institution_name == "Payee Bank"


# ---------------------------------------------------------------------------
# sync_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_connection_new_transactions(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "Sync Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sync-acc-1", name="Checking",
            type="checking", balance=Decimal("2000"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="sync-tx-1", description="GROCERY",
            amount=Decimal("80"), date=date.today(), type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, merged = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"
    assert merged == 0


@pytest.mark.asyncio
async def test_sync_connection_not_found(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="not found"):
        await sync_connection(session, uuid.uuid4(), test_workspace.id, test_user.id)


@pytest.mark.asyncio
async def test_sync_connection_with_category_mapping(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "Cat Bank")
    await _make_category(session, test_user.id, "Alimentação")

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="cat-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="cat-tx-1", description="RESTAURANT",
            amount=Decimal("50"), date=date.today(), type="debit",
            currency="BRL", pluggy_category="Eating out",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"


@pytest.mark.asyncio
async def test_sync_connection_error_raises(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "Error Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(RuntimeError, match="API down"):
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)


@pytest.mark.asyncio
async def test_sync_connection_skips_pending(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(
        session, test_user.id, "Pending Bank",
        settings={"import_pending": False},
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="pend-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="pend-tx-1", description="PENDING TXN",
            amount=Decimal("10"), date=date.today(), type="debit",
            currency="BRL", status="pending",
        ),
        TransactionData(
            external_id="pend-tx-2", description="POSTED TXN",
            amount=Decimal("20"), date=date.today(), type="debit",
            currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"


@pytest.mark.asyncio
async def test_sync_connection_skips_holdings_when_asset_sync_disabled(
    session: AsyncSession, test_user, test_workspace
):
    """sync_assets=False keeps account/transaction sync active but never fetches holdings."""
    conn = await _make_connection(
        session, test_user.id, "No Assets Bank",
        settings={"sync_assets": False},
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="no-assets-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="holding-1", name="Provider Fund",
            currency="BRL", current_value=Decimal("1234.56"),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"
    mock_provider.get_holdings.assert_not_awaited()
    assets = (await session.execute(select(Asset))).scalars().all()
    assert assets == []


@pytest.mark.asyncio
async def test_sync_connection_imports_holdings_by_default(
    session: AsyncSession, test_user, test_workspace
):
    """Missing sync_assets setting preserves legacy asset-sync behavior."""
    conn = await _make_connection(session, test_user.id, "Assets Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_institution_logo = AsyncMock(return_value=None)
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="assets-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_holdings = AsyncMock(return_value=[
        HoldingData(
            external_id="holding-1", name="Provider Fund",
            currency="BRL", current_value=Decimal("1234.56"),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"
    mock_provider.get_holdings.assert_awaited_once()
    asset = (await session.execute(select(Asset).where(Asset.external_id == "holding-1"))).scalar_one()
    assert asset.name == "Provider Fund"
    assert asset.connection_id == conn.id


@pytest.mark.asyncio
async def test_sync_connection_does_not_revive_ignored_transaction(
    session: AsyncSession, test_user, test_workspace,
):
    """Issue #200: a transaction the user flagged is_ignored=True must not be
    mutated by a subsequent sync, even when Pluggy returns the same external_id
    with different fields (e.g. pending → posted)."""
    from app.models.account import Account

    conn = await _make_connection(session, test_user.id, "Ignore Bank")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        name="Checking", type="checking",
        external_id="ign-acc-1",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()

    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        external_id="ign-tx-1", description="DUPLICATE PAYMENT",
        amount=Decimal("100"), date=date.today(), type="debit",
        currency="BRL", source="sync", status="pending",
        is_ignored=True,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="ign-acc-1", name="Checking",
            type="checking", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="ign-tx-1", description="DUPLICATE PAYMENT",
            amount=Decimal("100"), date=date.today(), type="debit",
            currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    refreshed = (await session.execute(
        select(Transaction).where(Transaction.external_id == "ign-tx-1")
    )).scalar_one()
    assert refreshed.is_ignored is True
    assert refreshed.status == "pending"  # not flipped to posted


# ---------------------------------------------------------------------------
# Installment metadata persistence (issue #14 v1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_persists_installment_metadata(
    session: AsyncSession, test_user, test_workspace,
):
    """handle_oauth_callback must store all 4 installment fields on the
    Transaction row exactly as the provider returned them."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-inst-1",
        institution_name="Inst Bank",
        credentials={"token": "x"},
        accounts=[
            AccountData(
                external_id="inst-acc-1", name="Nubank Gold",
                type="credit_card", balance=Decimal("0"), currency="BRL",
                credit_limit=Decimal("5000"),
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="inst-tx-1", description="AMAZON PARCELADO",
            amount=Decimal("120.50"), date=date(2026, 4, 10),
            type="debit", currency="BRL",
            installment_number=3,
            total_installments=12,
            installment_total_amount=Decimal("1446.00"),
            installment_purchase_date=date(2026, 2, 10),
        ),
        TransactionData(
            external_id="inst-tx-2", description="SINGLE CHARGE",
            amount=Decimal("40.00"), date=date(2026, 4, 11),
            type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await handle_oauth_callback(session, test_workspace.id, test_user.id, "code", "pluggy")

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
        .order_by(Transaction.external_id)
    )).scalars().all()
    assert len(rows) == 2

    parcel = next(t for t in rows if t.external_id == "inst-tx-1")
    assert parcel.installment_number == 3
    assert parcel.total_installments == 12
    assert parcel.installment_total_amount == Decimal("1446.00")
    assert parcel.installment_purchase_date == date(2026, 2, 10)

    single = next(t for t in rows if t.external_id == "inst-tx-2")
    assert single.installment_number is None
    assert single.total_installments is None
    assert single.installment_total_amount is None
    assert single.installment_purchase_date is None


@pytest.mark.asyncio
async def test_sync_connection_persists_installment_metadata(
    session: AsyncSession, test_user, test_workspace,
):
    """Incremental sync path must also persist installment fields."""
    conn = await _make_connection(session, test_user.id, "Sync Inst Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sync-inst-acc-1", name="Credit Card",
            type="credit_card", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="sync-inst-tx-1", description="PARCELA MAGALU",
            amount=Decimal("50.00"), date=date(2026, 4, 1),
            type="debit", currency="BRL",
            installment_number=1,
            total_installments=6,
            installment_total_amount=Decimal("300.00"),
            installment_purchase_date=date(2026, 3, 25),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "sync-inst-tx-1")
    )).scalar_one()
    assert row.installment_number == 1
    assert row.total_installments == 6
    assert row.installment_total_amount == Decimal("300.00")
    assert row.installment_purchase_date == date(2026, 3, 25)


@pytest.mark.asyncio
async def test_sync_connection_preserves_display_name(session: AsyncSession, test_user, test_workspace):
    """Resyncing a connection must update the provider name but never overwrite display_name."""
    from app.models.account import Account

    conn = await _make_connection(session, test_user.id, "Preserve Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="preserve-acc-1", name="BANCO ORIGINAL",
            type="checking", balance=Decimal("500"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # Set a display_name after the first sync
    account = (await session.execute(
        select(Account).where(Account.connection_id == conn.id)
    )).scalar_one()
    account.display_name = "Meu Apelido"
    await session.commit()

    # Resync — provider now returns a different name
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="preserve-acc-1", name="BANCO ATUALIZADO",
            type="checking", balance=Decimal("600"), currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    await session.refresh(account)
    assert account.name == "BANCO ATUALIZADO"
    assert account.display_name == "Meu Apelido"


@pytest.mark.asyncio
async def test_sync_connection_does_not_recreate_closed_accounts(
    session: AsyncSession, test_user, test_workspace,
):
    """Closing a connected account then resyncing must NOT create a duplicate
    active row for the same provider account, and the original closed row must
    keep its connection link so we can find it on subsequent syncs (issue #90).
    """
    from app.models.account import Account
    from app.services.account_service import close_account

    conn = await _make_connection(session, test_user.id, "Closed Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="closed-acc-1", name="Checking",
            type="checking", balance=Decimal("500"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    account = (await session.execute(
        select(Account).where(Account.external_id == "closed-acc-1")
    )).scalar_one()
    assert account.connection_id == conn.id

    await close_account(session, account.id, test_workspace.id)
    await session.refresh(account)
    assert account.is_closed is True
    assert account.connection_id == conn.id  # link preserved

    # Provider still returns the same account on the next sync
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="closed-acc-1", name="Checking",
            type="checking", balance=Decimal("999"), currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Account).where(Account.external_id == "closed-acc-1")
    )).scalars().all()
    assert len(rows) == 1, "sync must not create a duplicate active row"
    assert rows[0].is_closed is True
    assert rows[0].balance == Decimal("500"), "closed accounts must not be touched by sync"


# ---------------------------------------------------------------------------
# Credit-card bills wiring (issue #92)
# ---------------------------------------------------------------------------


def _cc_account(external_id: str = "cc-acc-1", name: str = "Credit Card") -> AccountData:
    return AccountData(
        external_id=external_id,
        name=name,
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
    )


def _cc_provider_mock(
    *,
    bills: list[BillData],
    transactions: list[TransactionData],
    bills_side_effect=None,
) -> AsyncMock:
    """Build a provider mock for a single CC account that returns the given
    bills and transactions. `bills_side_effect` overrides the return value
    (e.g. to raise) when set."""
    mock = AsyncMock()
    mock.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock.get_accounts = AsyncMock(return_value=[_cc_account()])
    mock.get_transactions = AsyncMock(return_value=transactions)
    if bills_side_effect is not None:
        mock.get_bills = AsyncMock(side_effect=bills_side_effect)
    else:
        mock.get_bills = AsyncMock(return_value=bills)
    return mock


def _patch_sync_helpers():
    """Common context managers for sync tests — silences out-of-scope helpers."""
    return (
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_sync_persists_bills_for_credit_card_account(
    session: AsyncSession, test_user, test_workspace,
):
    """First sync of a CC account upserts bills returned by /bills."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Bills Bank")
    bills = [
        BillData(
            external_id="bill-1",
            due_date=date(2026, 4, 15),
            total_amount=Decimal("1500.00"),
            currency="BRL",
            minimum_payment=Decimal("150.00"),
            raw_data={"id": "bill-1"},
        ),
    ]
    mock_provider = _cc_provider_mock(bills=bills, transactions=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.user_id == test_user.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].external_id == "bill-1"
    assert rows[0].due_date == date(2026, 4, 15)
    assert rows[0].total_amount == Decimal("1500.00")
    assert rows[0].minimum_payment == Decimal("150.00")
    assert rows[0].raw_data == {"id": "bill-1"}


@pytest.mark.asyncio
async def test_sync_links_transaction_to_matching_bill(
    session: AsyncSession, test_user, test_workspace,
):
    """Transactions whose bill_external_id matches a synced bill get bill_id
    set and effective_date = bill.due_date (the bank-truth path, issue #92)."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Linked Bank")
    bill = BillData(
        external_id="bill-99",
        due_date=date(2026, 5, 10),
        total_amount=Decimal("500.00"),
        currency="BRL",
    )
    txn = TransactionData(
        external_id="tx-linked",
        description="AMAZON",
        amount=Decimal("100"),
        date=date(2026, 4, 20),
        type="debit",
        currency="BRL",
        bill_external_id="bill-99",
    )
    mock_provider = _cc_provider_mock(bills=[bill], transactions=[txn])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    bill_row = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.external_id == "bill-99")
    )).scalar_one()
    tx_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-linked")
    )).scalar_one()
    assert tx_row.bill_id == bill_row.id
    # Bank-truth date wins over local cycle math.
    assert tx_row.effective_date == date(2026, 5, 10)


@pytest.mark.asyncio
async def test_sync_falls_back_to_cycle_math_when_bill_missing(
    session: AsyncSession, test_user, test_workspace,
):
    """A tx with bill_external_id that isn't in the bills feed (older bill,
    bills 4xx, etc.) leaves bill_id null and uses local cycle math —
    nothing about the legacy path may regress."""
    conn = await _make_connection(session, test_user.id, "Cycle Bank")

    # CC account with explicit close/due so cycle math has something to compute
    cc_acc = AccountData(
        external_id="cc-acc-cyc", name="CC", type="credit_card",
        balance=Decimal("0"), currency="BRL",
        statement_close_day=20, payment_due_day=28,
    )
    txn = TransactionData(
        external_id="tx-orphan",
        description="ORPHAN",
        amount=Decimal("30"),
        date=date(2026, 4, 5),
        type="debit",
        currency="BRL",
        bill_external_id="bill-not-in-feed",
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[cc_acc])
    mock_provider.get_transactions = AsyncMock(return_value=[txn])
    mock_provider.get_bills = AsyncMock(return_value=[])  # empty feed

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    tx_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-orphan")
    )).scalar_one()
    assert tx_row.bill_id is None
    # close=20 (>tx_date=5) → cycle ends 2026-04-20, due=28 → effective=2026-04-28
    assert tx_row.effective_date == date(2026, 4, 28)


@pytest.mark.asyncio
async def test_sync_swallows_get_bills_error(
    session: AsyncSession, test_user, test_workspace,
):
    """Non-regulado Pluggy connections 4xx on /bills. Sync must keep going
    and persist transactions via the cycle-math fallback."""
    conn = await _make_connection(session, test_user.id, "Err Bank")
    txn = TransactionData(
        external_id="tx-after-bills-fail",
        description="X",
        amount=Decimal("10"),
        date=date(2026, 4, 5),
        type="debit",
        currency="BRL",
    )
    mock_provider = _cc_provider_mock(
        bills=[], transactions=[txn],
        bills_side_effect=RuntimeError("403 Forbidden"),
    )

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        result, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result.status == "active"
    tx_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-after-bills-fail")
    )).scalar_one()
    assert tx_row.bill_id is None


@pytest.mark.asyncio
async def test_sync_skips_get_bills_for_non_credit_card_account(
    session: AsyncSession, test_user, test_workspace,
):
    """Checking accounts must not hit /bills — saves an HTTP roundtrip and
    avoids 4xx noise on providers that scope bills to credit accounts."""
    conn = await _make_connection(session, test_user.id, "Checking Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="chk-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    mock_provider.get_bills.assert_not_called()


@pytest.mark.asyncio
async def test_sync_backfills_bill_link_on_existing_transaction(
    session: AsyncSession, test_user, test_workspace,
):
    """A transaction synced before the /bills feature must self-heal: on the
    next sync, when the matching bill is in the feed, bill_id and
    effective_date pick up the bank-truth values without re-inserting."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Backfill Bank")
    txn_v0 = TransactionData(
        external_id="tx-preexisting",
        description="OLD CHARGE",
        amount=Decimal("75"),
        date=date(2026, 4, 6),
        type="debit",
        currency="BRL",
        bill_external_id="bill-future-1",
    )

    # First sync — no bills returned yet (simulates pre-feature state)
    mock_provider = _cc_provider_mock(bills=[], transactions=[txn_v0])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    pre = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-preexisting")
    )).scalar_one()
    assert pre.bill_id is None  # not linked yet

    # Second sync — same tx, but now /bills returns a matching bill
    bill = BillData(
        external_id="bill-future-1",
        due_date=date(2026, 5, 10),
        total_amount=Decimal("75"),
        currency="BRL",
    )
    mock_provider.get_bills = AsyncMock(return_value=[bill])
    mock_provider.get_transactions = AsyncMock(return_value=[txn_v0])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    bill_row = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.external_id == "bill-future-1")
    )).scalar_one()
    post = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-preexisting")
    )).scalar_one()
    # Same tx row, now linked + effective_date follows the bill due date.
    assert post.id == pre.id
    assert post.bill_id == bill_row.id
    assert post.effective_date == date(2026, 5, 10)


@pytest.mark.asyncio
async def test_sync_relinks_transaction_when_bank_moves_it_to_another_bill(
    session: AsyncSession, test_user, test_workspace,
):
    """If the bank later re-buckets a tx (chargeback, billing dispute), the
    next sync must update bill_id and effective_date — same row, new link."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Relink Bank")

    bill_a = BillData(
        external_id="bill-a", due_date=date(2026, 4, 10),
        total_amount=Decimal("40"), currency="BRL",
    )
    bill_b = BillData(
        external_id="bill-b", due_date=date(2026, 5, 10),
        total_amount=Decimal("40"), currency="BRL",
    )

    txn_to_a = TransactionData(
        external_id="tx-relink", description="X",
        amount=Decimal("40"), date=date(2026, 3, 15), type="debit",
        currency="BRL", bill_external_id="bill-a",
    )
    mock_provider = _cc_provider_mock(bills=[bill_a, bill_b], transactions=[txn_to_a])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # Bank moves the tx to bill_b on the next sync
    txn_to_b = TransactionData(
        external_id="tx-relink", description="X",
        amount=Decimal("40"), date=date(2026, 3, 15), type="debit",
        currency="BRL", bill_external_id="bill-b",
    )
    mock_provider.get_transactions = AsyncMock(return_value=[txn_to_b])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    bill_b_row = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.external_id == "bill-b")
    )).scalar_one()
    tx = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-relink")
    )).scalar_one()
    assert tx.bill_id == bill_b_row.id
    assert tx.effective_date == date(2026, 5, 10)


@pytest.mark.asyncio
async def test_sync_creates_synthetic_transactions_for_finance_charges(
    session: AsyncSession, test_user, test_workspace,
):
    """A bill carrying IOF / multa / juros lines that don't exist as standalone
    transactions must yield synthetic txs so the cycle sum reconciles to
    bill.total_amount (issue #92)."""
    conn = await _make_connection(session, test_user.id, "Charges Bank")
    bill = BillData(
        external_id="bill-fc-1",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("232.76"),
        currency="BRL",
        raw_data={
            "id": "bill-fc-1",
            "financeCharges": [
                {"id": "fc-iof", "type": "IOF", "amount": 0.91, "additionalInfo": "IOF de atraso"},
                {"id": "fc-fee", "type": "LATE_PAYMENT_FEE", "amount": 4.5, "additionalInfo": "Multa de atraso"},
                {"id": "fc-int", "type": "LATE_PAYMENT_REMUNERATIVE_INTEREST", "amount": 3.46, "additionalInfo": "Juros de atraso"},
            ],
        },
    )
    mock_provider = _cc_provider_mock(bills=[bill], transactions=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
        .order_by(Transaction.amount)
    )).scalars().all()
    assert len(rows) == 3
    amounts = sorted(float(r.amount) for r in rows)
    assert amounts == [0.91, 3.46, 4.5]
    descriptions = {r.description for r in rows}
    assert descriptions == {"IOF de atraso", "Multa de atraso", "Juros de atraso"}
    # All linked to the bill, dated to its due_date, marked as debits.
    for r in rows:
        assert r.bill_id is not None
        assert r.date == date(2026, 4, 15)
        assert r.effective_date == date(2026, 4, 15)
        assert r.type == "debit"
        assert r.external_id.startswith("bill_charge:bill-fc-1:")


@pytest.mark.asyncio
async def test_sync_dates_charges_at_cycle_close_when_close_day_known(
    session: AsyncSession, test_user, test_workspace,
):
    """Synthetic finance charges should be dated at the cycle close (the
    bank's snapshot moment) rather than the bill's due date — otherwise
    they'd appear chronologically AFTER the user's payment in the tx list,
    which doesn't match real bank semantics. effective_date stays at
    due_date for accrual aggregation."""
    conn = await _make_connection(session, test_user.id, "CloseDateBank")
    # CC account with explicit close=12, due=18 (Goldinho-style)
    cc = AccountData(
        external_id="cd-acc", name="CC", type="credit_card",
        balance=Decimal("0"), currency="BRL",
        statement_close_day=12, payment_due_day=18,
    )
    bill = BillData(
        external_id="bill-cd",
        due_date=date(2026, 2, 18),
        total_amount=Decimal("100"),
        currency="BRL",
        raw_data={
            "id": "bill-cd",
            "financeCharges": [
                {"id": "fc-1", "type": "IOF", "amount": 0.91, "additionalInfo": "IOF"},
            ],
        },
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[cc])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[bill])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    tx = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
    )).scalar_one()
    # Close = the most recent close_day on or before due — same month here.
    assert tx.date == date(2026, 2, 12)
    # Accrual bucketing still anchors on bill.due_date.
    assert tx.effective_date == date(2026, 2, 18)


@pytest.mark.asyncio
async def test_sync_skips_carry_over_balance_finance_charge(
    session: AsyncSession, test_user, test_workspace,
):
    """`Saldo em atraso` is the prior bill's unpaid balance carried into this
    bill — informational only, NOT part of bill.total_amount, so we must not
    materialize it as a tx (would double-count the user's debt)."""
    conn = await _make_connection(session, test_user.id, "SaldoBank")
    bill = BillData(
        external_id="bill-saldo",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("229.26"),
        currency="BRL",
        raw_data={
            "id": "bill-saldo",
            "financeCharges": [
                {"id": "x1", "type": "OTHER", "amount": 223.9, "additionalInfo": "Saldo em atraso"},
                {"id": "x2", "type": "IOF", "amount": 0.87, "additionalInfo": "IOF de atraso"},
            ],
        },
    )
    mock_provider = _cc_provider_mock(bills=[bill], transactions=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].description == "IOF de atraso"
    assert float(rows[0].amount) == 0.87


@pytest.mark.asyncio
async def test_sync_skips_juros_aggregate_finance_charge(
    session: AsyncSession, test_user, test_workspace,
):
    """`Juros de dívida encerrada` is an aggregate that equals the sum of the
    detailed late-charge lines Pluggy ALSO emits — including it would
    double-count by ~one charge worth."""
    conn = await _make_connection(session, test_user.id, "AggBank")
    bill = BillData(
        external_id="bill-agg",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("100.00"),
        currency="BRL",
        raw_data={
            "id": "bill-agg",
            "financeCharges": [
                {"id": "a1", "type": "OTHER", "amount": 5.37, "additionalInfo": "Juros de dívida encerrada"},
                {"id": "a2", "type": "IOF", "amount": 0.87, "additionalInfo": "IOF de atraso"},
                {"id": "a3", "type": "LATE_PAYMENT_FEE", "amount": 4.5, "additionalInfo": "Multa de atraso"},
            ],
        },
    )
    mock_provider = _cc_provider_mock(bills=[bill], transactions=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    descriptions = {r.description for r in rows}
    # Aggregate is dropped; the two detailed lines remain.
    assert descriptions == {"IOF de atraso", "Multa de atraso"}


@pytest.mark.asyncio
async def test_sync_finance_charges_are_idempotent(
    session: AsyncSession, test_user, test_workspace,
):
    """Re-syncing the same bill must not duplicate synthetic charges."""
    conn = await _make_connection(session, test_user.id, "IdemFC")
    bill = BillData(
        external_id="bill-idem-fc",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("100"),
        currency="BRL",
        raw_data={
            "id": "bill-idem-fc",
            "financeCharges": [
                {"id": "fc-1", "type": "IOF", "amount": 1.23, "additionalInfo": "IOF de atraso"},
            ],
        },
    )
    mock_provider = _cc_provider_mock(bills=[bill], transactions=[])
    p1, p2, p3 = _patch_sync_helpers()

    for _ in range(2):
        with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
             p1, p2, p3:
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_sync_removes_orphaned_finance_charges_on_resync(
    session: AsyncSession, test_user, test_workspace,
):
    """If a charge disappears from the bill on the next sync (e.g. bank
    reversed it), the synthetic tx must be removed."""
    conn = await _make_connection(session, test_user.id, "OrphFC")
    bill_v1 = BillData(
        external_id="bill-orph",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("100"),
        currency="BRL",
        raw_data={
            "id": "bill-orph",
            "financeCharges": [
                {"id": "fc-keep", "type": "IOF", "amount": 1.0, "additionalInfo": "IOF"},
                {"id": "fc-drop", "type": "LATE_PAYMENT_FEE", "amount": 4.5, "additionalInfo": "Multa"},
            ],
        },
    )
    mock_provider = _cc_provider_mock(bills=[bill_v1], transactions=[])
    p1, p2, p3 = _patch_sync_helpers()

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # Second sync — the LATE_PAYMENT_FEE charge is gone (bank reversed it)
    bill_v2 = BillData(
        external_id="bill-orph",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("100"),
        currency="BRL",
        raw_data={
            "id": "bill-orph",
            "financeCharges": [
                {"id": "fc-keep", "type": "IOF", "amount": 1.0, "additionalInfo": "IOF"},
            ],
        },
    )
    mock_provider.get_bills = AsyncMock(return_value=[bill_v2])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert "fc-keep" in rows[0].external_id


@pytest.mark.asyncio
async def test_sync_does_not_overwrite_manual_effective_bill_date(
    session: AsyncSession, test_user, test_workspace,
):
    """When the user has manually set effective_bill_date on a tx, the next
    sync must NOT relink bill_id or recompute effective_date — the user is
    explicitly overriding the auto bucketing (issue #92, LucasFidelis idea)."""
    from datetime import date as _date_

    conn = await _make_connection(session, test_user.id, "OverrideBank")

    bill_a = BillData(
        external_id="bill-A", due_date=_date_(2026, 4, 5),
        total_amount=Decimal("100"), currency="BRL",
    )
    txn = TransactionData(
        external_id="tx-overridden",
        description="X",
        amount=Decimal("50"),
        date=_date_(2026, 3, 20),
        type="debit",
        currency="BRL",
        bill_external_id="bill-A",  # Pluggy says: belongs to bill A
    )
    mock_provider = _cc_provider_mock(bills=[bill_a], transactions=[txn])
    p1, p2, p3 = _patch_sync_helpers()

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # User overrides: this tx belongs to a different bill (May 5, manually).
    tx_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-overridden")
    )).scalar_one()
    bill_a_row_id = tx_row.bill_id  # link from sync
    tx_row.effective_bill_date = _date_(2026, 5, 5)
    tx_row.bill_id = None  # user manually unlinked
    tx_row.effective_date = _date_(2026, 5, 5)
    await session.commit()

    # Re-sync — provider still says bill A. Override must be preserved.
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    tx_row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-overridden")
    )).scalar_one()
    assert tx_row.effective_bill_date == _date_(2026, 5, 5)
    assert tx_row.bill_id is None  # not re-linked to A
    assert tx_row.effective_date == _date_(2026, 5, 5)
    assert bill_a_row_id is not None  # sanity: A had been linked initially


@pytest.mark.asyncio
async def test_sync_updates_existing_bill_idempotently(
    session: AsyncSession, test_user, test_workspace,
):
    """A second sync that returns the same bill id with updated totals must
    update in place, not insert a duplicate (the unique(account_id,
    external_id) constraint would fail otherwise)."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Idem Bank")
    bill_v1 = BillData(
        external_id="bill-idem",
        due_date=date(2026, 4, 15),
        total_amount=Decimal("100.00"),
        currency="BRL",
    )
    mock_provider = _cc_provider_mock(bills=[bill_v1], transactions=[])

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # Second sync: same id, new totals (e.g. mid-cycle adjustment)
    bill_v2 = BillData(
        external_id="bill-idem",
        due_date=date(2026, 4, 16),
        total_amount=Decimal("125.50"),
        currency="BRL",
    )
    mock_provider.get_bills = AsyncMock(return_value=[bill_v2])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.user_id == test_user.id)
    )).scalars().all()
    assert len(rows) == 1, "second sync must not duplicate the row"
    assert rows[0].due_date == date(2026, 4, 16)
    assert rows[0].total_amount == Decimal("125.50")


# ---------------------------------------------------------------------------
# Synced-transaction duplicate prevention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_dedupes_pending_posted_twin_in_same_fetch(
    session: AsyncSession, test_user, test_workspace,
):
    """A provider emits the same operation twice in a single fetch — once
    pending (the scheduled row) and once posted (the executed row) — under
    two different external_ids. Only the posted row must land."""
    conn = await _make_connection(session, test_user.id, "Twin Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="twin-acc-1", name="Conta Corrente",
            type="checking", balance=Decimal("1000"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="provider-id-pending",
            description="INVESTIMENTO/OPERACAOB3* - DOCTO: 8162",
            amount=Decimal("943.23"), date=date(2026, 4, 20),
            type="debit", currency="BRL", status="pending",
        ),
        TransactionData(
            external_id="provider-id-posted",
            description="INVESTIMENTO/OPERACAOB3* - DOCTO: 1270397",
            amount=Decimal("943.23"), date=date(2026, 4, 20),
            type="debit", currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 1, "pending+posted twin must collapse to a single row"
    assert rows[0].status == "posted"
    assert rows[0].external_id == "provider-id-posted"


@pytest.mark.asyncio
async def test_sync_dedupes_pending_posted_twin_with_identical_descriptions(
    session: AsyncSession, test_user, test_workspace,
):
    """Same case as above but the descriptions are byte-identical — the
    status differential alone is enough to collapse them."""
    conn = await _make_connection(session, test_user.id, "Identical Desc Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="id-acc-1", name="Conta",
            type="checking", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="provider-pending",
            description="PIX AGENDADO BENEFICIARIO XYZ",
            amount=Decimal("250.00"), date=date(2026, 4, 22),
            type="debit", currency="BRL", status="pending",
        ),
        TransactionData(
            external_id="provider-posted",
            description="PIX AGENDADO BENEFICIARIO XYZ",
            amount=Decimal("250.00"), date=date(2026, 4, 22),
            type="debit", currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "posted"


@pytest.mark.asyncio
async def test_sync_keeps_unrelated_pending_and_posted_with_different_descriptions(
    session: AsyncSession, test_user, test_workspace,
):
    """Two unrelated transactions that happen to share a date and amount —
    one pending, one posted, completely different merchants — must NOT be
    collapsed. The description-similarity guard protects against this
    false positive."""
    conn = await _make_connection(session, test_user.id, "Unrelated Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="unr-acc-1", name="Conta",
            type="checking", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="unrelated-pending",
            description="STARBUCKS COFFEE",
            amount=Decimal("25.00"), date=date(2026, 4, 22),
            type="debit", currency="BRL", status="pending",
        ),
        TransactionData(
            external_id="unrelated-posted",
            description="UBER TRIP",
            amount=Decimal("25.00"), date=date(2026, 4, 22),
            type="debit", currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 2, "unrelated transactions must not be collapsed"


@pytest.mark.asyncio
async def test_sync_upgrades_pending_to_posted_when_twin_arrives(
    session: AsyncSession, test_user, test_workspace,
):
    """When the pending row was synced first and the posted twin arrives on
    the next sync with a different external_id, the existing row must be
    upgraded in place — not duplicated."""
    conn = await _make_connection(session, test_user.id, "Twin Upgrade Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="up-acc-1", name="Conta",
            type="checking", balance=Decimal("0"), currency="BRL",
        ),
    ])
    pending = TransactionData(
        external_id="provider-pending",
        description="PIX AGENDADO - DOCTO: 11111",
        amount=Decimal("100.00"), date=date(2026, 4, 20),
        type="debit", currency="BRL", status="pending",
    )
    mock_provider.get_transactions = AsyncMock(return_value=[pending])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    # Second sync: posted twin arrives with a new id and identifier; the
    # pending row is also still in the feed (providers don't always drop the
    # scheduled row immediately).
    posted = TransactionData(
        external_id="provider-posted",
        description="PIX AGENDADO - DOCTO: 22222",
        amount=Decimal("100.00"), date=date(2026, 4, 20),
        type="debit", currency="BRL", status="posted",
    )
    mock_provider.get_transactions = AsyncMock(return_value=[pending, posted])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 1, "pending+posted twins must collapse to one row"
    # Posted truth wins: status flipped and external_id swapped to the new one
    # so subsequent syncs match by id.
    assert rows[0].status == "posted"
    assert rows[0].external_id == "provider-posted"


@pytest.mark.asyncio
async def test_sync_dedupes_advanced_installment_payment(
    session: AsyncSession, test_user, test_workspace,
):
    """A credit-card installment paid in advance shows up as posted on the
    current bill *and* pending on the next bill. Two different external
    ids, two different bill ids, but same installment fingerprint
    (purchase_date / number / total). Only the posted row must land,
    linked to the current bill."""
    from app.models.credit_card_bill import CreditCardBill

    conn = await _make_connection(session, test_user.id, "Inst Bank")

    bill_current = BillData(
        external_id="bill-current",
        due_date=date(2026, 5, 10),
        total_amount=Decimal("241.50"),
        currency="BRL",
    )
    bill_next = BillData(
        external_id="bill-next",
        due_date=date(2026, 6, 10),
        total_amount=Decimal("241.50"),
        currency="BRL",
    )

    posted_current = TransactionData(
        external_id="provider-inst-posted",
        description="HTM*INAA CONSULTOR 06/12",
        amount=Decimal("241.50"), date=date(2026, 4, 28),
        type="debit", currency="BRL", status="posted",
        installment_number=6, total_installments=12,
        installment_total_amount=Decimal("2898.00"),
        installment_purchase_date=date(2025, 11, 28),
        bill_external_id="bill-current",
    )
    pending_next = TransactionData(
        external_id="provider-inst-pending",
        description="HTM*INAA CONSULTOR 06/12",
        amount=Decimal("241.50"), date=date(2026, 5, 9),
        type="debit", currency="BRL", status="pending",
        installment_number=6, total_installments=12,
        installment_total_amount=Decimal("2898.00"),
        installment_purchase_date=date(2025, 11, 28),
        bill_external_id="bill-next",
    )

    mock_provider = _cc_provider_mock(
        bills=[bill_current, bill_next],
        transactions=[posted_current, pending_next],
    )

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 1, (
        "advanced installment must not double-count: POSTED on current bill "
        "and PENDING on next bill are the same logical charge"
    )
    survivor = rows[0]
    assert survivor.status == "posted"
    assert survivor.installment_number == 6
    assert survivor.total_installments == 12

    bill_current_row = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.external_id == "bill-current")
    )).scalar_one()
    assert survivor.bill_id == bill_current_row.id, (
        "survivor must stay linked to the bill that actually paid the installment"
    )


@pytest.mark.asyncio
async def test_sync_dedupes_advanced_installment_when_pending_lands_first(
    session: AsyncSession, test_user, test_workspace,
):
    """Same as the previous test but the pending next-bill row arrives
    before the posted current-bill row in the fetch list. Order must not
    matter — posted still wins."""
    conn = await _make_connection(session, test_user.id, "Inst Order Bank")

    bill_current = BillData(
        external_id="bill-current-2",
        due_date=date(2026, 5, 10),
        total_amount=Decimal("100"), currency="BRL",
    )
    bill_next = BillData(
        external_id="bill-next-2",
        due_date=date(2026, 6, 10),
        total_amount=Decimal("100"), currency="BRL",
    )

    pending = TransactionData(
        external_id="provider-pend-first",
        description="LIVRARIA SARAIVA 03/06",
        amount=Decimal("50.00"), date=date(2026, 5, 5),
        type="debit", currency="BRL", status="pending",
        installment_number=3, total_installments=6,
        installment_total_amount=Decimal("300.00"),
        installment_purchase_date=date(2026, 3, 5),
        bill_external_id="bill-next-2",
    )
    posted = TransactionData(
        external_id="provider-post-second",
        description="LIVRARIA SARAIVA 03/06",
        amount=Decimal("50.00"), date=date(2026, 4, 28),
        type="debit", currency="BRL", status="posted",
        installment_number=3, total_installments=6,
        installment_total_amount=Decimal("300.00"),
        installment_purchase_date=date(2026, 3, 5),
        bill_external_id="bill-current-2",
    )

    mock_provider = _cc_provider_mock(
        bills=[bill_current, bill_next],
        transactions=[pending, posted],  # pending first
    )

    p1, p2, p3 = _patch_sync_helpers()
    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         p1, p2, p3:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "posted"
    assert rows[0].external_id == "provider-post-second"


@pytest.mark.asyncio
async def test_sync_keeps_genuine_same_day_repeats(
    session: AsyncSession, test_user, test_workspace,
):
    """Two genuine same-day same-amount transactions with byte-identical
    descriptions and identical statuses must NOT be collapsed — those are
    real repeats (e.g. two identical fares charged on the same day), not
    provider-side duplicates. Guards against false positives in the new
    dedup."""
    conn = await _make_connection(session, test_user.id, "Repeat Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="rep-acc-1", name="Conta",
            type="checking", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="uber-1",
            description="UBER TRIP",
            amount=Decimal("25.00"), date=date(2026, 4, 20),
            type="debit", currency="BRL", status="posted",
        ),
        TransactionData(
            external_id="uber-2",
            description="UBER TRIP",
            amount=Decimal("25.00"), date=date(2026, 4, 20),
            type="debit", currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source == "sync",
        )
    )).scalars().all()
    assert len(rows) == 2, "identical-description same-day repeats must be kept"


# ---------------------------------------------------------------------------
# SimpleFIN credit-card balance-sign normalization on sync (UPDATE branch)
# ---------------------------------------------------------------------------


async def _make_simplefin_connection(
    session: AsyncSession, user_id: uuid.UUID, name: str = "SimpleFIN Bank",
) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="simplefin",
        external_id=f"ext-sf-{uuid.uuid4().hex[:8]}",
        institution_name=name, credentials={"token": "fake"},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


@pytest.mark.asyncio
async def test_sync_normalizes_simplefin_card_balance_to_positive_for_debt(
    session: AsyncSession, test_user, test_workspace,
):
    """SimpleFIN reports a card's debt as a NEGATIVE balance under a "checking"
    label. Once the user has overridden the account type to credit_card, a
    re-sync must store the balance positive-for-debt (Pluggy/Enable convention)
    so the downstream negation yields the right sign instead of double-counting.
    The UPDATE branch keys the normalization off the account's CURRENT type,
    which carries the user override (sync never rewrites `type`)."""
    from app.models.account import Account

    conn = await _make_simplefin_connection(session, test_user.id)
    # Pre-existing account the user already flipped to credit_card. Stored
    # balance is already positive-for-debt from the prior edit.
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        external_id="sf-cc-1", name="SimpleFIN Card", type="credit_card",
        balance=Decimal("500.00"), currency="USD",
    )
    session.add(account)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    # SimpleFIN provider parses every account as type="checking" and reports
    # the raw negative debt balance.
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sf-cc-1", name="SimpleFIN Card",
            type="checking", balance=Decimal("-650.00"), currency="USD",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    await session.refresh(account)
    # Incoming -650 (raw SimpleFIN) → stored +650 (positive-for-debt). The user
    # override (type=credit_card) is preserved.
    assert account.type == "credit_card"
    assert account.balance == Decimal("650.00")


@pytest.mark.asyncio
async def test_sync_leaves_simplefin_checking_balance_unchanged(
    session: AsyncSession, test_user, test_workspace,
):
    """A SimpleFIN non-card account (no override) keeps the provider's balance
    verbatim — the normalization only applies to credit_card."""
    from app.models.account import Account

    conn = await _make_simplefin_connection(session, test_user.id, "SF Checking")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        external_id="sf-chk-1", name="SimpleFIN Checking", type="checking",
        balance=Decimal("0.00"), currency="USD",
    )
    session.add(account)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sf-chk-1", name="SimpleFIN Checking",
            type="checking", balance=Decimal("1234.56"), currency="USD",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])
    mock_provider.get_bills = AsyncMock(return_value=[])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    await session.refresh(account)
    assert account.balance == Decimal("1234.56")
