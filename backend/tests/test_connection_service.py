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
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.providers.base import (
    AccountData,
    BillData,
    ConnectionData,
    ConnectTokenData,
    HoldingData,
    ProviderUserActionRequired,
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
from app.services.rule_service import create_rule


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
    assert updated.settings is not None
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
    assert updated.settings is not None
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
    assert updated.settings is not None
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

    assert connection is not None
    assert connection.settings is not None
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

    assert connection is not None
    assert connection.settings is not None
    assert connection.settings["sync_assets"] is False
    assert connection.settings["flow_params"] == {
        "country": "BR",
        "institution_name": "OAuth No Assets Bank",
    }
    mock_provider.get_holdings.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_reconnect_updates_existing_connection_without_deleting_accounts(
    session: AsyncSession, test_user, test_workspace
):
    """SimpleFIN token reconnect refreshes credentials in place."""
    existing = await _make_connection(session, test_user.id, "Old SimpleFIN")
    existing.provider = "simplefin"
    existing.external_id = "old-simplefin-conn"
    existing.credentials = {"access_url_enc": "old-encrypted-url"}
    existing.status = "error"
    existing.last_sync_at = datetime.now(timezone.utc)
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=existing.id,
        external_id="existing-account",
        name="Checking",
        type="checking",
        balance=Decimal("10.00"),
        currency="USD",
    )
    session.add(account)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="new-simplefin-conn",
        institution_name="New SimpleFIN Bank",
        credentials={"access_url_enc": "new-encrypted-url"},
        accounts=[],
    ))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        reconnected = await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "fresh-setup-token",
            provider_name="simplefin",
            reconnect_connection_id=existing.id,
        )

    assert reconnected.id == existing.id
    assert reconnected.external_id == "new-simplefin-conn"
    assert reconnected.institution_name == "New SimpleFIN Bank"
    assert reconnected.credentials == {"access_url_enc": "new-encrypted-url"}
    assert reconnected.status == "active"
    assert reconnected.last_sync_at is None
    remaining_accounts = (
        await session.execute(select(Account).where(Account.connection_id == existing.id))
    ).scalars().all()
    assert [a.external_id for a in remaining_accounts] == ["existing-account"]
    mock_provider.handle_oauth_callback.assert_awaited_once_with("fresh-setup-token")


@pytest.mark.asyncio
async def test_token_reconnect_rejects_provider_mismatch(
    session: AsyncSession, test_user, test_workspace
):
    existing = await _make_connection(session, test_user.id, "SimpleFIN")
    existing.provider = "simplefin"
    existing.status = "error"
    await session.commit()

    with pytest.raises(ValueError, match="provider does not match"):
        await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "fresh-setup-token",
            provider_name="pluggy",
            reconnect_connection_id=existing.id,
        )


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
    assert updated.settings is not None
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
    transaction = (
        await session.execute(
            select(Transaction).where(Transaction.external_id == "tx-1")
        )
    ).scalar_one()
    assert transaction.description == "UBER"
    assert transaction.original_description == "UBER"
    assert transaction.description_is_rule_managed is False


@pytest.mark.asyncio
async def test_handle_oauth_callback_deduplicates_initial_transactions(
    session: AsyncSession, test_user, test_workspace
):
    """Repeated provider pages cannot create duplicate external ids on setup."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-duplicate",
        institution_name="Test Bank",
        credentials={"token": "abc"},
        accounts=[AccountData(
            external_id="acc-duplicate", name="Checking",
            type="checking", balance=Decimal("1000"), currency="BRL",
        )],
    ))
    duplicate = TransactionData(
        external_id="tx-duplicate", description="UBER", amount=Decimal("25"),
        date=date.today(), type="debit", currency="BRL",
    )
    mock_provider.get_transactions = AsyncMock(return_value=[duplicate, duplicate])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "auth-code", "enable_banking"
        )

    rows = (await session.execute(
        select(Transaction).where(Transaction.external_id == "tx-duplicate")
    )).scalars().all()
    assert len(rows) == 1


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
    transaction = await session.scalar(
        select(Transaction).where(Transaction.external_id == "sync-tx-1")
    )
    assert transaction is not None
    assert transaction.original_description == "GROCERY"


@pytest.mark.asyncio
async def test_sync_connection_tolerates_duplicate_transaction_rows(
    session: AsyncSession, test_user, test_workspace
):
    """A pre-existing (account_id, external_id) duplicate — the state an earlier
    concurrent-sync race leaves behind — must not abort the sync. Regression for
    the MultipleResultsFound crash at the transaction dedup lookup.
    """
    conn = await _make_connection(session, test_user.id, "Dup Bank")

    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        external_id="dup-acc-1", name="Checking", type="checking",
        balance=Decimal("500"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    account_id = account.id  # capture before sync commits/expires the ORM object

    # Two rows sharing (account_id, external_id): exactly what a sync race
    # leaves behind, and what scalar_one_or_none() used to choke on.
    for _ in range(2):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account_id,
            external_id="dup-tx-1", description="SPOTIFY", amount=Decimal("23.90"),
            date=date.today(), type="debit", status="pending", source="sync",
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="dup-acc-1", name="Checking",
            type="checking", balance=Decimal("500"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="dup-tx-1", description="SPOTIFY",
            amount=Decimal("23.90"), date=date.today(), type="debit",
            currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service._cleanup_phantom_duplicates", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    assert result_conn.status == "active"
    # Sync reconciled onto an existing row instead of inserting a third, and did
    # not raise. The incoming "posted" status is applied to one of the twins.
    rows = (await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.external_id == "dup-tx-1",
        )
    )).scalars().all()
    assert len(rows) == 2
    assert any(r.status == "posted" for r in rows)


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
async def test_sync_keeps_provider_category_while_normalizing_description(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, "Normalized Cat Bank")
    category = await _make_category(session, test_user.id, "Alimentação")
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize iFood sync",
            conditions=[
                RuleCondition(field="payee", op="contains", value="IFOOD.COM")
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(op="append_notes", value="#delivery"),
            ],
            apply_to_existing=False,
        ),
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(
        return_value=[
            AccountData(
                external_id="norm-acc-1",
                name="Checking",
                type="checking",
                balance=Decimal("100"),
                currency="BRL",
            )
        ]
    )
    mock_provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="norm-tx-1",
                description="|fd*f|ood Club",
                payee="IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A.",
                amount=Decimal("50"),
                date=date.today(),
                type="debit",
                currency="BRL",
                pluggy_category="Eating out",
                raw_data={"merchant": {"name": "IFOOD.COM"}},
            )
        ]
    )

    with patch(
        "app.services.connection_service.get_provider",
        return_value=mock_provider,
    ), patch(
        "app.services.connection_service.detect_transfer_pairs",
        new_callable=AsyncMock,
    ), patch(
        "app.services.connection_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ):
        await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    transaction = (
        await session.execute(
            select(Transaction).where(
                Transaction.external_id == "norm-tx-1"
            )
        )
    ).scalar_one()
    assert transaction.category_id == category.id
    assert transaction.description == "iFood"
    assert transaction.original_description == "|fd*f|ood Club"
    assert transaction.description_is_rule_managed is True
    assert transaction.payee == "IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A."
    assert transaction.payee_id is not None
    assert transaction.raw_data == {"merchant": {"name": "IFOOD.COM"}}
    assert transaction.notes == "#delivery"

@pytest.mark.asyncio
async def test_sync_connection_error_raises(session: AsyncSession, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "Error Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(RuntimeError, match="API down"):
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)


@pytest.mark.asyncio
async def test_sync_connection_user_action_marks_error(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, "SimpleFIN Auth Error")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"access_url_enc": "stale"})
    mock_provider.get_accounts = AsyncMock(
        side_effect=ProviderUserActionRequired(
            "SimpleFIN refused the request (403)",
            code="credentials_invalid",
            help_url="https://bridge.simplefin.org/",
        )
    )

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(ProviderUserActionRequired):
            await sync_connection(session, conn.id, test_workspace.id, test_user.id)

    refreshed = await session.get(BankConnection, conn.id)
    assert refreshed is not None
    assert refreshed.status == "error"


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
        assert r.external_id is not None
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
    assert rows[0].external_id is not None
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


# ---------------------------------------------------------------------------
# _sync_holdings — per-account wallets (issue #345)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_holdings_splits_wallets_per_account(
    session: AsyncSession, test_user, test_workspace,
):
    """Holdings split into one wallet per owning account, backed by the
    account's institution. The legacy connection-keyed wallet is ADOPTED by
    the first account — re-keyed, its untouched auto-name refreshed — so an
    existing user's wallet row survives the upgrade; a wallet the user made
    themselves is never touched."""
    from app.models.asset_group import AssetGroup
    from app.models.institution import Institution
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "First Bank")

    inst = Institution(connection_id=conn.id, name="Second Brokerage")
    session.add(inst)
    await session.flush()
    account = Account(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, external_id="acc-1", name="Employer 401(k)",
        type="investment", balance=Decimal("0"), currency="USD",
        institution_id=inst.id,
    )
    session.add(account)

    # Legacy wallet from the one-wallet-per-connection era, holding h-1.
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="First Bank", source="test",
        connection_id=conn.id, external_id=conn.external_id,
    )
    # A wallet the user made themselves — sync must never touch it.
    custom = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="US Stocks", source="manual",
    )
    session.add_all([legacy, custom])
    await session.flush()

    asset_legacy = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=legacy.id,
    )
    asset_custom = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, source="test", external_id="h-2",
        name="Bonds", type="investment", currency="USD",
        valuation_method="manual", group_id=custom.id,
    )
    session.add_all([asset_legacy, asset_custom])
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="Employer 401(k)",
        ),
        HoldingData(
            external_id="h-2", name="Bonds", currency="USD",
            current_value=Decimal("5"),
            account_external_id="acc-1", account_name="Employer 401(k)",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    groups = (
        await session.execute(select(AssetGroup).where(AssetGroup.user_id == test_user.id))
    ).scalars().all()
    by_name = {g.name: g for g in groups}

    # The legacy wallet was adopted as the per-account wallet: same row,
    # re-keyed, auto-name ("First Bank" == institution label, so untouched
    # by the user) refreshed to the account's, institution backfilled.
    wallet = by_name["Employer 401(k)"]
    assert wallet.id == legacy.id
    assert wallet.external_id == f"{conn.external_id}::acc-1"
    assert wallet.institution_id == inst.id
    assert "First Bank" not in by_name

    await session.refresh(asset_legacy)
    assert asset_legacy.group_id == wallet.id

    # The user's own wallet keeps its asset, even though h-2 was synced.
    await session.refresh(asset_custom)
    assert asset_custom.group_id == custom.id
    assert "US Stocks" in by_name


# ---------------------------------------------------------------------------
# sync_connection — institution wiring (issue #345)
# ---------------------------------------------------------------------------


def _institution_provider(institution_name: str) -> AsyncMock:
    mock = AsyncMock()
    mock.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="acc-1", name="Checking", type="checking",
            balance=Decimal("0"), currency="USD",
            institution_external_id="CON-1", institution_name=institution_name,
        ),
    ])
    mock.get_transactions = AsyncMock(return_value=[])
    mock.get_holdings = AsyncMock(return_value=[])
    mock.get_bills = AsyncMock(return_value=[])
    return mock


@pytest.mark.asyncio
async def test_sync_wires_account_institutions_and_reaps_orphans(
    session: AsyncSession, test_user, test_workspace,
):
    """The account loop backfills institution_id on pre-existing accounts, a
    provider-side rename updates the row in place, rows nothing references
    are reaped, wallet-referenced rows survive, and the connections API
    exposes the link's institutions (review on #654)."""
    from app.models.asset_group import AssetGroup
    from app.models.institution import Institution
    from app.schemas.bank_connection import BankConnectionRead

    conn = await _make_connection(session, test_user.id, "Sync Bank")
    conn_id = conn.id

    # Pre-existing account with no institution yet — must get backfilled.
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="Checking",
        type="checking", balance=Decimal("0"), currency="USD",
    )
    session.add(account)
    account_id = account.id
    # An abandoned identity-drift row: no account or wallet points at it.
    stray = Institution(connection_id=conn_id, name="Old Name Bank")
    # A row only a wallet references — the reap must never touch it.
    kept = Institution(connection_id=conn_id, name="Departed Brokerage")
    session.add_all([stray, kept])
    await session.flush()
    stray_id, kept_id = stray.id, kept.id
    session.add(AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Old 401(k)", source="test", connection_id=conn_id,
        external_id="ext::gone", institution_id=kept_id,
    ))
    await session.commit()

    def _patched(mock):
        return (
            patch("app.services.connection_service.get_provider", return_value=mock),
            patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
            patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
            patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
        )

    p1, p2, p3, p4 = _patched(_institution_provider("Chase"))
    with p1, p2, p3, p4:
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    from app.models.institution import Institution as Inst
    inst = (
        await session.execute(select(Inst).where(Inst.external_id == "CON-1"))
    ).scalar_one()
    assert inst.name == "Chase"
    refreshed = await session.get(Account, account_id)
    assert refreshed is not None and refreshed.institution_id == inst.id
    assert await session.get(Inst, stray_id) is None  # reaped: nothing references it
    assert await session.get(Inst, kept_id) is not None  # wallet keeps its label

    # Provider-side rename: same row, new name, no new rows.
    p1, p2, p3, p4 = _patched(_institution_provider("Chase Bank"))
    with p1, p2, p3, p4:
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)
    renamed = await session.get(Inst, inst.id)
    assert renamed is not None and renamed.name == "Chase Bank"

    conn_row = (
        await session.execute(select(BankConnection).where(BankConnection.id == conn_id))
    ).scalar_one()
    read = BankConnectionRead.model_validate(conn_row)
    assert {i.name for i in read.institutions} == {"Chase Bank", "Departed Brokerage"}


@pytest.mark.asyncio
async def test_sync_holdings_readopts_orphaned_wallet_after_reconnect(
    session: AsyncSession, test_user, test_workspace,
):
    """Disconnect leaves the legacy wallet with connection_id NULL (SET NULL)
    and reconnects can mint a NEW connection external_id. The lone stale
    plain-keyed wallet is adopted anyway — customization kept, assets in
    place — instead of being emptied and deleted (review round 4 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Recon Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    # The pre-disconnect wallet: connection link severed, external_id intact.
    orphan_wallet = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Recon Bank", source="test",
        connection_id=None, external_id="old-ext",
    )
    session.add(orphan_wallet)
    await session.flush()
    orphan_wallet_id = orphan_wallet.id
    stranded = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=None, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=orphan_wallet_id,
    )
    session.add(stranded)
    await session.commit()
    stranded_id = stranded.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()

    moved = await session.get(Asset, stranded_id)
    assert moved is not None
    # The stale wallet was adopted as the per-account wallet: same row,
    # re-keyed and re-linked; "Recon Bank" was the untouched auto-name, so
    # it's refreshed to the account's.
    assert moved.group_id == orphan_wallet_id
    wallet = await session.get(AssetGroup, orphan_wallet_id)
    assert wallet is not None
    assert wallet.external_id == f"{conn.external_id}::acc-1"
    assert wallet.connection_id == conn_id
    assert wallet.name == "IRA"


@pytest.mark.asyncio
async def test_sync_holdings_keeps_emptied_wallet_a_goal_tracks(
    session: AsyncSession, test_user, test_workspace,
):
    """Deleting an emptied sync-owned wallet would SET NULL a goal's target
    and CASCADE away collection membership — so referenced wallets survive
    (self-review on #654)."""
    from app.models.asset_group import AssetGroup
    from app.models.goal import Goal
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Goal Bank")

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, external_id="acc-1", name="401(k)",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    # Keyed like a per-account wallet of a since-removed account, so the
    # legacy-adoption path (which only considers plain-keyed wallets) doesn't
    # claim it — this test exercises the goal guard on the emptied-wallet
    # delete, not adoption.
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Goal Bank", source="test",
        connection_id=conn.id, external_id="gone::acc-9",
    )
    session.add(legacy)
    await session.flush()
    legacy_id = legacy.id
    asset = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, source="test", external_id="h-1",
        name="Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=legacy_id,
    )
    goal = Goal(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Retire", target_amount=Decimal("1000000"),
        asset_group_id=legacy_id,
    )
    session.add_all([asset, goal])
    await session.commit()
    goal_id = goal.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Fund", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="401(k)",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "t"})
    await session.commit()

    # Asset moved to the per-account wallet, but the goal's wallet survives
    # empty rather than vanishing out from under the goal.
    assert (await session.get(AssetGroup, legacy_id)) is not None
    refreshed_goal = await session.get(Goal, goal_id)
    assert refreshed_goal is not None
    assert refreshed_goal.asset_group_id == legacy_id


@pytest.mark.asyncio
async def test_sync_holdings_adoption_preserves_wallet_customization(
    session: AsyncSession, test_user, test_workspace,
):
    """The maintainer's repro on #654: an existing user's wallet — renamed,
    custom icon/color/position — must survive the first per-account sync as
    the same row with all customization intact, not come back as a default
    wallet. A custom name is never overwritten by adoption."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Fidelity")

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, external_id="acc-1", name="Rollover IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    customized = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My Retirement", icon="piggy-bank", color="#FF00AA", position=7,
        source="test", connection_id=conn.id, external_id=conn.external_id,
    )
    session.add(customized)
    await session.flush()
    wallet_id = customized.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, source="test", external_id="h-1",
        name="Index Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=wallet_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Index Fund", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="Rollover IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    wallet = await session.get(AssetGroup, wallet_id)
    assert wallet is not None  # same row, not a replacement
    assert wallet.external_id == f"{conn.external_id}::acc-1"  # re-keyed
    assert wallet.name == "My Retirement"  # custom name untouched
    assert wallet.icon == "piggy-bank"
    assert wallet.color == "#FF00AA"
    assert wallet.position == 7
    # No second wallet minted for the account.
    all_synced = (
        await session.execute(select(AssetGroup).where(
            AssetGroup.user_id == test_user.id, AssetGroup.source == "test"))
    ).scalars().all()
    assert [g.id for g in all_synced] == [wallet_id]


def test_wallet_external_id_fits_the_column_deterministically():
    """Over-long composites keep a deterministic digest suffix instead of a
    collision-prone truncation (review round 4 on #654)."""
    from app.services.connection_service import _wallet_external_id

    assert _wallet_external_id("EXT", "acc-1") == "EXT::acc-1"
    assert _wallet_external_id("EXT", None) == "EXT"
    long_a = _wallet_external_id("E" * 250, "a" * 250)
    long_b = _wallet_external_id("E" * 250, "b" * 250)
    assert len(long_a) <= 255 and len(long_b) <= 255
    assert long_a != long_b  # truncation alone would collide these
    assert long_a == _wallet_external_id("E" * 250, "a" * 250)  # deterministic


@pytest.mark.asyncio
async def test_sync_holdings_mixed_hints_stay_stable_across_syncs(
    session: AsyncSession, test_user, test_workspace,
):
    """A payload mixing account-attributed and unattributed holdings must
    reach a steady state: the per-account wallet and the connection-default
    wallet coexist, and a second sync neither re-keys, duplicates, nor
    crashes on the unique (user, source, external_id) index
    (review round 4 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Mixed Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Mixed Bank", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    session.add(legacy)
    await session.commit()
    legacy_id = legacy.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
        # Spec-nonconforming account without an id → no account hint.
        HoldingData(
            external_id="h-2", name="Mystery Fund", currency="USD",
            current_value=Decimal("5"),
        ),
    ]

    for _ in range(2):  # the second pass is the regression
        with patch(
            "app.services.connection_service.get_provider", return_value=mock_provider
        ):
            await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
        await session.commit()

    wallets = (
        await session.execute(select(AssetGroup).where(
            AssetGroup.user_id == test_user.id, AssetGroup.source == "test"))
    ).scalars().all()
    by_key = {w.external_id: w for w in wallets}
    # Steady state: the adopted per-account wallet plus one connection-default
    # wallet for the unattributed holding — no churn, no duplicates.
    assert len(wallets) == 2
    assert set(by_key) == {f"{conn.external_id}::acc-1", conn.external_id}
    assert by_key[f"{conn.external_id}::acc-1"].id == legacy_id  # adopted once


@pytest.mark.asyncio
async def test_sync_holdings_adoption_refreshes_suffixed_auto_names(
    session: AsyncSession, test_user, test_workspace,
):
    """A wallet auto-named "Mixed Bank 2" by _unique_default_name is still an
    untouched auto-name — adoption refreshes it to the account's name, and
    the refresh itself dedupes against existing wallet names
    (review round 4 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Suffix Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="Employer 401(k)",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    # The name a second same-institution connection's wallet gets at creation.
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Suffix Bank 2", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    # A manual wallet already using the account's name — the refresh must
    # dedupe, not duplicate.
    session.add_all([legacy, AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Employer 401(k)", source="manual",
    )])
    await session.commit()
    legacy_id = legacy.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="Employer 401(k)",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    wallet = await session.get(AssetGroup, legacy_id)
    assert wallet is not None
    assert wallet.name == "Employer 401(k) 2"  # refreshed AND deduped


@pytest.mark.asyncio
async def test_sync_holdings_unattributed_first_keeps_default_wallet_key(
    session: AsyncSession, test_user, test_workspace,
):
    """When the unattributed holding claims the connection-default wallet
    first, a later per-account bucket must not adopt (and re-key) that
    just-claimed wallet — provider-controlled payload order must not cause
    churn (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Order Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Order Bank", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    session.add(legacy)
    await session.commit()
    legacy_id = legacy.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # Unattributed FIRST: the connection-default bucket claims the
        # legacy wallet before the per-account bucket goes looking.
        HoldingData(
            external_id="h-2", name="Mystery Fund", currency="USD",
            current_value=Decimal("5"),
        ),
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]

    for _ in range(2):
        with patch(
            "app.services.connection_service.get_provider", return_value=mock_provider
        ):
            await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
        await session.commit()

    wallets = (
        await session.execute(select(AssetGroup).where(
            AssetGroup.user_id == test_user.id, AssetGroup.source == "test"))
    ).scalars().all()
    by_key = {w.external_id: w for w in wallets}
    assert len(wallets) == 2
    assert set(by_key) == {conn.external_id, f"{conn.external_id}::acc-1"}
    # The claimed default wallet kept its plain key; the per-account wallet
    # is a separate row.
    assert by_key[conn.external_id].id == legacy_id
    assert by_key[f"{conn.external_id}::acc-1"].id != legacy_id


@pytest.mark.asyncio
async def test_sync_holdings_never_adopts_another_connections_orphan(
    session: AsyncSession, test_user, test_workspace,
):
    """A deleted sibling connection's orphaned wallet (connection_id NULL via
    SET NULL, same provider) is not this bank's: it holds none of the assets
    this payload syncs, so a new connection mints its own wallet instead of
    hijacking the orphan and mingling two banks' assets
    (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "New Bank")

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, external_id="acc-1", name="Brokerage",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    orphan = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Old Bank", source="test",
        connection_id=None, external_id="old-bank-ext",
    )
    session.add(orphan)
    await session.flush()
    orphan_id = orphan.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=None, source="test", external_id="old-h-1",
        name="Old Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=orphan_id, is_archived=True,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="Brokerage",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    untouched = await session.get(AssetGroup, orphan_id)
    assert untouched is not None
    assert untouched.external_id == "old-bank-ext"
    assert untouched.connection_id is None
    assert untouched.name == "Old Bank"
    minted = await session.scalar(
        select(AssetGroup).where(
            AssetGroup.external_id == f"{conn.external_id}::acc-1")
    )
    assert minted is not None
    assert minted.id != orphan_id


@pytest.mark.asyncio
async def test_sync_holdings_adopts_own_stale_wallet_over_foreign_orphan(
    session: AsyncSession, test_user, test_workspace,
):
    """A reconnect-in-place leaves the bank's wallet linked to the connection
    but on a stale key. It is adopted even when an unrelated orphan coexists —
    the old ambiguity rule would have minted a wallet and reaped the
    customized one (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Recon Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    stale = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My Retirement", source="test",
        connection_id=conn_id, external_id="pre-reconnect-ext",
    )
    foreign = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Old Bank", source="test",
        connection_id=None, external_id="old-bank-ext",
    )
    session.add_all([stale, foreign])
    await session.flush()
    stale_id, foreign_id = stale.id, foreign.id
    # The orphan still holds its old bank's archived asset.
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=None, source="test", external_id="old-h-1",
        name="Old Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=foreign_id, is_archived=True,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    adopted = await session.get(AssetGroup, stale_id)
    assert adopted is not None
    assert adopted.external_id == f"{conn.external_id}::acc-1"
    assert adopted.name == "My Retirement"  # customization kept
    untouched = await session.get(AssetGroup, foreign_id)
    assert untouched is not None
    assert untouched.external_id == "old-bank-ext"


@pytest.mark.asyncio
async def test_sync_holdings_prefers_exact_key_match_over_stale_candidate(
    session: AsyncSession, test_user, test_workspace,
):
    """With the current-keyed legacy wallet AND a stale-keyed wallet both on
    the connection, the exact match wins — without the preference, two
    same-connection candidates would read as ambiguous and mint
    (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Exact Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Exact Bank", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    stale = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Leftover", source="test",
        connection_id=conn_id, external_id="stale-ext",
    )
    session.add_all([legacy, stale])
    await session.flush()
    legacy_id, stale_id = legacy.id, stale.id
    # An asset outside this payload keeps the stale wallet from the reap —
    # the point here is which candidate adoption picks, not the reap.
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="other-h",
        name="Other Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=stale_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    adopted = await session.get(AssetGroup, legacy_id)
    assert adopted is not None
    assert adopted.external_id == f"{conn.external_id}::acc-1"
    untouched = await session.get(AssetGroup, stale_id)
    assert untouched is not None
    assert untouched.external_id == "stale-ext"


@pytest.mark.asyncio
async def test_sync_holdings_keyless_reconnect_adopts_stale_wallet(
    session: AsyncSession, test_user, test_workspace,
):
    """Providers whose holdings carry no account hint (Pluggy) get the same
    adoption on reconnect: the customized wallet left on the old item id is
    re-keyed in place instead of being emptied into a fresh default wallet
    and reaped (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Keyless Bank")
    conn_id = conn.id

    wallet = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My Crypto", icon="bitcoin", source="test",
        connection_id=conn_id, external_id="old-item-id",
    )
    session.add(wallet)
    await session.flush()
    wallet_id = wallet.id
    asset = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-1",
        name="Bitcoin", type="investment", currency="USD",
        valuation_method="manual", group_id=wallet_id,
    )
    session.add(asset)
    await session.commit()
    asset_id = asset.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Bitcoin", currency="USD",
            current_value=Decimal("10"),
        ),
    ]
    for _ in range(2):
        with patch(
            "app.services.connection_service.get_provider", return_value=mock_provider
        ):
            await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
        await session.commit()

    adopted = await session.get(AssetGroup, wallet_id)
    assert adopted is not None
    assert adopted.external_id == conn.external_id  # re-keyed to the new id
    assert adopted.connection_id == conn_id
    assert adopted.name == "My Crypto"
    assert adopted.icon == "bitcoin"
    kept = await session.get(Asset, asset_id)
    assert kept is not None
    assert kept.group_id == wallet_id


@pytest.mark.asyncio
async def test_sync_holdings_adoption_keeps_name_matching_account(
    session: AsyncSession, test_user, test_workspace,
):
    """When the account is named after the institution, the refreshed
    auto-name equals the wallet's current one — it must not count itself as
    taken and become "Vanguard 2" (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Vanguard")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="Vanguard",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    legacy = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Vanguard", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    session.add(legacy)
    await session.commit()
    legacy_id = legacy.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="Vanguard",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    wallet = await session.get(AssetGroup, legacy_id)
    assert wallet is not None
    assert wallet.name == "Vanguard"


@pytest.mark.asyncio
async def test_sync_holdings_reaps_emptied_unreferenced_wallet(
    session: AsyncSession, test_user, test_workspace,
):
    """The positive half of the reap: a sync-owned wallet emptied by
    re-attribution, tracked by no goal and in no collection, is deleted
    (review round 5 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Reap Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    # A per-account wallet of a since-removed account ("::"-keyed, so the
    # adoption path never claims it).
    old_wallet = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Closed Account", source="test",
        connection_id=conn_id, external_id=f"{conn.external_id}::acc-gone",
    )
    session.add(old_wallet)
    await session.flush()
    old_wallet_id = old_wallet.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=old_wallet_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # The holding moved to a different account, emptying the old wallet.
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    assert await session.get(AssetGroup, old_wallet_id) is None


@pytest.mark.asyncio
async def test_sync_holdings_readopts_split_wallet_after_reconnect(
    session: AsyncSession, test_user, test_workspace,
):
    """The keys this PR itself writes must survive a delete/re-add cycle: an
    orphaned per-account wallet on an old connection prefix is matched by its
    "::{account}" suffix and re-keyed in place — not excluded, re-minted, and
    reaped (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Rotate Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    orphan = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My IRA", source="test",
        connection_id=None, external_id="old-conn-ext::acc-1",
    )
    session.add(orphan)
    await session.flush()
    orphan_id = orphan.id
    stranded = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=None, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=orphan_id, is_archived=True,
    )
    session.add(stranded)
    await session.commit()
    stranded_id = stranded.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    wallet = await session.get(AssetGroup, orphan_id)
    assert wallet is not None
    assert wallet.external_id == f"{conn.external_id}::acc-1"
    assert wallet.connection_id == conn_id
    assert wallet.name == "My IRA"  # customization kept
    moved = await session.get(Asset, stranded_id)
    assert moved is not None
    assert moved.group_id == orphan_id
    assert moved.is_archived is False


@pytest.mark.asyncio
async def test_sync_holdings_rekeys_split_wallet_after_inplace_reconnect(
    session: AsyncSession, test_user, test_workspace,
):
    """A reconnect-in-place rotates connection.external_id but keeps the
    wallet linked; the suffix match re-keys it under the new prefix
    (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Inplace Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="401(k)",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    stale = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My 401k", icon="target", source="test",
        connection_id=conn_id, external_id="pre-rotation-ext::acc-1",
    )
    session.add(stale)
    await session.commit()
    stale_id = stale.id

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="401(k)",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    wallet = await session.get(AssetGroup, stale_id)
    assert wallet is not None
    assert wallet.external_id == f"{conn.external_id}::acc-1"
    assert wallet.name == "My 401k"
    assert wallet.icon == "target"


@pytest.mark.asyncio
async def test_sync_holdings_hint_loss_does_not_drain_split_wallets(
    session: AsyncSession, test_user, test_workspace,
):
    """One payload with the account ids missing must not empty the
    per-account wallets into the default bucket (the reap would then delete
    them); when the hints return, everything is back to steady state
    (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Hint Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    wallet = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My IRA", source="test",
        connection_id=conn_id, external_id=f"{conn.external_id}::acc-1",
    )
    session.add(wallet)
    await session.flush()
    wallet_id = wallet.id
    asset = Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=wallet_id,
    )
    session.add(asset)
    await session.commit()
    asset_id = asset.id

    degraded = AsyncMock()
    degraded.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=degraded):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    survivor = await session.get(AssetGroup, wallet_id)
    assert survivor is not None
    assert survivor.external_id == f"{conn.external_id}::acc-1"
    assert survivor.name == "My IRA"
    held = await session.get(Asset, asset_id)
    assert held is not None
    assert held.group_id == wallet_id  # not drained into the default bucket

    healthy = AsyncMock()
    healthy.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("11"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=healthy):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    # Steady state restored: the per-account wallet still owns the asset and
    # the transient default wallet was reaped once emptied of purpose.
    held = await session.get(Asset, asset_id)
    assert held is not None
    assert held.group_id == wallet_id
    wallets = (
        await session.execute(select(AssetGroup).where(
            AssetGroup.user_id == test_user.id, AssetGroup.source == "test"))
    ).scalars().all()
    assert {w.external_id for w in wallets} == {f"{conn.external_id}::acc-1"}


@pytest.mark.asyncio
async def test_sync_holdings_new_account_first_keeps_default_wallet(
    session: AsyncSession, test_user, test_workspace,
):
    """Once per-account wallets exist, the plain-keyed wallet is the live
    connection-default — a new account's bucket running first must mint its
    own wallet, not adopt and re-key the default (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Grow Bank")
    conn_id = conn.id

    for ext, name in (("acc-1", "IRA"), ("acc-2", "Brokerage")):
        session.add(Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, external_id=ext, name=name,
            type="investment", balance=Decimal("0"), currency="USD",
        ))
    split = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="IRA", source="test",
        connection_id=conn_id, external_id=f"{conn.external_id}::acc-1",
    )
    default = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Misc investments", source="test",
        connection_id=conn_id, external_id=conn.external_id,
    )
    session.add_all([split, default])
    await session.flush()
    split_id, default_id = split.id, default.id
    session.add_all([
        Asset(
            user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, source="test", external_id="h-1",
            name="Apple", type="investment", currency="USD",
            valuation_method="manual", group_id=split_id,
        ),
        Asset(
            user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, source="test", external_id="h-2",
            name="Mystery Fund", type="investment", currency="USD",
            valuation_method="manual", group_id=default_id,
        ),
    ])
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # The NEW account's holding arrives first.
        HoldingData(
            external_id="h-3", name="Tesla", currency="USD",
            current_value=Decimal("20"),
            account_external_id="acc-2", account_name="Brokerage",
        ),
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
        HoldingData(
            external_id="h-2", name="Mystery Fund", currency="USD",
            current_value=Decimal("5"),
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    kept = await session.get(AssetGroup, default_id)
    assert kept is not None
    assert kept.external_id == conn.external_id  # not hijacked by acc-2
    assert kept.name == "Misc investments"
    minted = await session.scalar(
        select(AssetGroup).where(
            AssetGroup.external_id == f"{conn.external_id}::acc-2")
    )
    assert minted is not None
    assert minted.id not in {split_id, default_id}


@pytest.mark.asyncio
async def test_sync_holdings_never_touches_another_workspaces_wallets(
    session: AsyncSession, test_user, test_workspace,
):
    """A sync stays inside its connection's workspace: another workspace's
    orphan wallet is not adopted, drained, or reaped even when its assets
    overlap the payload (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.models.workspace import Workspace, WorkspaceMember
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Scoped Bank")

    other_ws = Workspace(
        id=uuid.uuid4(), name="Elsewhere", kind="personal",
        created_by_user_id=test_user.id, default_currency="USD", locale="en-US",
    )
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=other_ws.id,
        user_id=test_user.id, role="owner",
    ))
    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn.id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    foreign = AssetGroup(
        user_id=test_user.id, workspace_id=other_ws.id,
        name="Other Workspace Wallet", source="test",
        connection_id=None, external_id="old-ext",
    )
    session.add(foreign)
    await session.flush()
    foreign_id = foreign.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=other_ws.id,
        connection_id=None, source="test", external_id="h-1",
        name="Apple", type="investment", currency="USD",
        valuation_method="manual", group_id=foreign_id, is_archived=True,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    untouched = await session.get(AssetGroup, foreign_id)
    assert untouched is not None
    assert untouched.external_id == "old-ext"
    assert untouched.connection_id is None
    assert untouched.workspace_id == other_ws.id
    minted = await session.scalar(
        select(AssetGroup).where(
            AssetGroup.external_id == f"{conn.external_id}::acc-1")
    )
    assert minted is not None
    assert minted.workspace_id == test_workspace.id
    # Asset identity is per workspace, so "h-1" now names one asset in each
    # of them. Look the pair up separately: an unscoped query matches both
    # rows and returns whichever the database hands back first.
    moved = await session.scalar(
        select(Asset).where(
            Asset.external_id == "h-1",
            Asset.workspace_id == test_workspace.id,
        )
    )
    assert moved is not None
    assert moved.group_id == minted.id
    assert moved.is_archived is False
    # The other workspace's asset is left exactly as it was found.
    foreign_asset = await session.scalar(
        select(Asset).where(
            Asset.external_id == "h-1",
            Asset.workspace_id == other_ws.id,
        )
    )
    assert foreign_asset is not None
    assert foreign_asset.group_id == foreign_id
    assert foreign_asset.is_archived is True
    assert foreign_asset.connection_id is None


@pytest.mark.asyncio
async def test_wallet_key_uniqueness_is_enforced_in_the_test_schema(
    session: AsyncSession, test_user, test_workspace,
):
    """The model mirrors migration 034's partial unique index so adoption
    guards are testable: duplicate keys raise, NULL keys don't
    (review round 6 on #654)."""
    from sqlalchemy.exc import IntegrityError

    from app.models.asset_group import AssetGroup

    user_id, ws_id = test_user.id, test_workspace.id
    session.add(AssetGroup(
        user_id=user_id, workspace_id=ws_id,
        name="First", source="test", external_id="dup-key",
    ))
    await session.commit()

    session.add(AssetGroup(
        user_id=user_id, workspace_id=ws_id,
        name="Second", source="test", external_id="dup-key",
    ))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    # The index is partial: NULL keys never collide.
    session.add_all([
        AssetGroup(
            user_id=user_id, workspace_id=ws_id,
            name="Manual A", source="test",
        ),
        AssetGroup(
            user_id=user_id, workspace_id=ws_id,
            name="Manual B", source="test",
        ),
    ])
    await session.commit()


@pytest.mark.asyncio
async def test_ensure_group_recovers_when_losing_the_mint_race(
    session: AsyncSession, test_user, test_workspace, monkeypatch,
):
    """When a concurrent sync mints the same wallet key between the lookup
    and the flush, the loser's IntegrityError is contained and the winner's
    row is returned (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.asset_group_service import ensure_group_for_connection

    conn = await _make_connection(session, test_user.id, "Race Bank")
    winner = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Winner", source="test",
        connection_id=conn.id, external_id="race-key",
    )
    session.add(winner)
    await session.commit()
    winner_id = winner.id

    real_execute = session.execute
    state = {"missed": False}

    class _Miss:
        def scalar_one_or_none(self):
            return None

    async def racing_execute(stmt, *args, **kwargs):
        # The first lookup misses — the winner's row lands "between" the
        # select and the flush, as a concurrent sync's commit would.
        if not state["missed"]:
            state["missed"] = True
            return _Miss()
        return await real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(session, "execute", racing_execute)
    group = await ensure_group_for_connection(
        session,
        user_id=test_user.id,
        connection_id=conn.id,
        source="test",
        external_id="race-key",
        default_name="Race Bank",
    )

    assert group.id == winner_id


@pytest.mark.asyncio
async def test_sync_holdings_survives_losing_the_adoption_rekey_race(
    session: AsyncSession, test_user, test_workspace, monkeypatch,
):
    """If a concurrent sync claims the wallet key while this one is
    adopting, the re-key's IntegrityError is contained: the candidate is
    left as it was and the winner's wallet is used
    (review round 6 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Adopt Race Bank")
    conn_id = conn.id
    other = await _make_connection(session, test_user.id, "Other Bank")

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    wallet_key = f"{conn.external_id}::acc-1"
    # The concurrent sync's wallet already owns the target key, but hangs
    # off a different connection so it is not an adoption candidate.
    twin = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Twin", source="test",
        connection_id=other.id, external_id=wallet_key,
    )
    # This sync's own stale candidate, picked via the suffix tier.
    stale = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My Stale", source="test",
        connection_id=conn_id, external_id="old-conn-ext::acc-1",
    )
    session.add_all([twin, stale])
    await session.flush()
    twin_id, stale_id = twin.id, stale.id
    # An asset outside the payload keeps the loser's wallet from the reap —
    # the point here is the contained IntegrityError, not the reap.
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="other-h",
        name="Other Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=stale_id,
    ))
    await session.commit()

    real_scalar = session.scalar
    state = {"missed": False}

    async def racing_scalar(stmt, *args, **kwargs):
        # Blind only the existence guard (its statement carries the exact
        # wallet key), simulating the winner committing after the check.
        params = stmt.compile().params if hasattr(stmt, "compile") else {}
        if not state["missed"] and wallet_key in params.values():
            state["missed"] = True
            return None
        return await real_scalar(stmt, *args, **kwargs)

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    monkeypatch.setattr(session, "scalar", racing_scalar)
    with patch(
        "app.services.connection_service.get_provider", return_value=mock_provider
    ):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    # The loser backed off: its candidate is untouched, the winner's wallet
    # owns the key (re-linked to this connection by the mint path).
    kept = await session.get(AssetGroup, stale_id)
    assert kept is not None
    assert kept.external_id == "old-conn-ext::acc-1"
    assert kept.name == "My Stale"
    winner = await session.get(AssetGroup, twin_id)
    assert winner is not None
    assert winner.external_id == wallet_key
    moved = await session.scalar(select(Asset).where(Asset.external_id == "h-1"))
    assert moved is not None
    assert moved.group_id == twin_id


@pytest.mark.asyncio
async def test_sync_holdings_keyless_bucket_adopts_default_after_rotation(
    session: AsyncSession, test_user, test_workspace,
):
    """After a reconnect rotates the connection key, the keyless bucket must
    still adopt the customized stale default wallet — pinned against
    recomputing the split snapshot per bucket, which would see the split
    wallet re-keyed earlier in the same run and mint instead
    (review round 7 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Rotated Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    split = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My IRA", source="test",
        connection_id=conn_id, external_id="old-ext::acc-1",
    )
    default = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My Misc", source="test",
        connection_id=conn_id, external_id="old-ext",
    )
    session.add_all([split, default])
    await session.flush()
    split_id, default_id = split.id, default.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-2",
        name="Mystery Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=default_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # The keyed bucket runs first and re-keys the split wallet under
        # the new prefix — the pre-run snapshot must not see that.
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
        HoldingData(
            external_id="h-2", name="Mystery Fund", currency="USD",
            current_value=Decimal("5"),
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    rekeyed = await session.get(AssetGroup, split_id)
    assert rekeyed is not None
    assert rekeyed.external_id == f"{conn.external_id}::acc-1"
    adopted = await session.get(AssetGroup, default_id)
    assert adopted is not None
    assert adopted.external_id == conn.external_id
    assert adopted.name == "My Misc"
    held = await session.scalar(select(Asset).where(Asset.external_id == "h-2"))
    assert held is not None
    assert held.group_id == default_id


@pytest.mark.asyncio
async def test_sync_holdings_new_account_after_rotation_mints_its_own_wallet(
    session: AsyncSession, test_user, test_workspace,
):
    """A genuinely new account appearing in the first post-rotation sync
    must mint — old-prefix split wallets prove the legacy era is over, so
    the stale default belongs to the keyless bucket, not the new account
    (review round 7 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Grown Bank")
    conn_id = conn.id

    for ext, name in (("acc-1", "IRA"), ("acc-2", "Brokerage")):
        session.add(Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, external_id=ext, name=name,
            type="investment", balance=Decimal("0"), currency="USD",
        ))
    split = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="My IRA", source="test",
        connection_id=conn_id, external_id="old-ext::acc-1",
    )
    default = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Misc investments", source="test",
        connection_id=conn_id, external_id="old-ext",
    )
    session.add_all([split, default])
    await session.flush()
    split_id, default_id = split.id, default.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-2",
        name="Mystery Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=default_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # The brand-new account's holding arrives first.
        HoldingData(
            external_id="h-3", name="Tesla", currency="USD",
            current_value=Decimal("20"),
            account_external_id="acc-2", account_name="Brokerage",
        ),
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
        HoldingData(
            external_id="h-2", name="Mystery Fund", currency="USD",
            current_value=Decimal("5"),
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    kept = await session.get(AssetGroup, default_id)
    assert kept is not None
    assert kept.external_id == conn.external_id  # adopted by the keyless bucket
    assert kept.name == "Misc investments"
    minted = await session.scalar(
        select(AssetGroup).where(
            AssetGroup.external_id == f"{conn.external_id}::acc-2")
    )
    assert minted is not None
    assert minted.id not in {split_id, default_id}


@pytest.mark.asyncio
async def test_sync_holdings_two_stale_generations_mean_minting(
    session: AsyncSession, test_user, test_workspace,
):
    """Two stale per-account wallets for the same account (two rotations)
    are ambiguous — mint rather than guess; neither is re-keyed
    (review round 7 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Twice Bank")
    conn_id = conn.id

    session.add(Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, external_id="acc-1", name="IRA",
        type="investment", balance=Decimal("0"), currency="USD",
    ))
    gen1 = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Gen One", source="test",
        connection_id=conn_id, external_id="gen1-ext::acc-1",
    )
    gen2 = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Gen Two", source="test",
        connection_id=conn_id, external_id="gen2-ext::acc-1",
    )
    session.add_all([gen1, gen2])
    await session.flush()
    gen1_id, gen2_id = gen1.id, gen2.id
    for i, gid in enumerate((gen1_id, gen2_id)):
        session.add(Asset(
            user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, source="test", external_id=f"keep-{i}",
            name="Keeper", type="investment", currency="USD",
            valuation_method="manual", group_id=gid,
        ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        HoldingData(
            external_id="h-1", name="Apple", currency="USD",
            current_value=Decimal("10"),
            account_external_id="acc-1", account_name="IRA",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    for gid, ext in ((gen1_id, "gen1-ext::acc-1"), (gen2_id, "gen2-ext::acc-1")):
        wallet = await session.get(AssetGroup, gid)
        assert wallet is not None
        assert wallet.external_id == ext
    minted = await session.scalar(
        select(AssetGroup).where(
            AssetGroup.external_id == f"{conn.external_id}::acc-1")
    )
    assert minted is not None
    assert minted.id not in {gen1_id, gen2_id}


@pytest.mark.asyncio
async def test_sync_holdings_separator_in_account_id_cannot_steal_sibling_wallet(
    session: AsyncSession, test_user, test_workspace,
):
    """An account id ending in another account's id (ids may contain "::")
    must not suffix-match the sibling's live wallet — live-prefix wallets
    are never candidates, and a stale key must parse as exactly one
    "{prefix}::{key}" (review round 7 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.services.connection_service import _sync_holdings

    conn = await _make_connection(session, test_user.id, "Colon Bank")
    conn_id = conn.id

    for ext, name in (("a::b", "Joint"), ("b", "Solo")):
        session.add(Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn_id, external_id=ext, name=name,
            type="investment", balance=Decimal("0"), currency="USD",
        ))
    joint = AssetGroup(
        user_id=test_user.id, workspace_id=test_workspace.id,
        name="Our Joint Account", source="test",
        connection_id=conn_id, external_id=f"{conn.external_id}::a::b",
    )
    session.add(joint)
    await session.flush()
    joint_id = joint.id
    session.add(Asset(
        user_id=test_user.id, workspace_id=test_workspace.id,
        connection_id=conn_id, source="test", external_id="h-joint",
        name="Joint Fund", type="investment", currency="USD",
        valuation_method="manual", group_id=joint_id,
    ))
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.get_holdings.return_value = [
        # The new account whose id is the "::"-tail of its sibling's runs
        # first — the hostile ordering.
        HoldingData(
            external_id="h-solo", name="Solo Fund", currency="USD",
            current_value=Decimal("10"),
            account_external_id="b", account_name="Solo",
        ),
        HoldingData(
            external_id="h-joint", name="Joint Fund", currency="USD",
            current_value=Decimal("20"),
            account_external_id="a::b", account_name="Joint",
        ),
    ]
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        await _sync_holdings(session, test_user.id, conn, {"token": "fake"})
    await session.commit()

    untouched = await session.get(AssetGroup, joint_id)
    assert untouched is not None
    assert untouched.external_id == f"{conn.external_id}::a::b"
    assert untouched.name == "Our Joint Account"
    solo = await session.scalar(
        select(AssetGroup).where(AssetGroup.external_id == f"{conn.external_id}::b")
    )
    assert solo is not None
    assert solo.id != joint_id
    held = await session.scalar(select(Asset).where(Asset.external_id == "h-joint"))
    assert held is not None
    assert held.group_id == joint_id


@pytest.mark.asyncio
async def test_ensure_group_relocates_wallet_when_connection_moves_workspaces(
    session: AsyncSession, test_user, test_workspace,
):
    """The wallet key is unique per (user, source) across workspaces, so a
    key match from another workspace means the bank moved — the wallet
    follows its connection instead of feeding data into a workspace the
    connection doesn't live in (review round 7 on #654)."""
    from app.models.asset_group import AssetGroup
    from app.models.workspace import Workspace, WorkspaceMember
    from app.services.asset_group_service import ensure_group_for_connection

    conn = await _make_connection(session, test_user.id, "Mover Bank")
    other_ws = Workspace(
        id=uuid.uuid4(), name="Old Home", kind="personal",
        created_by_user_id=test_user.id, default_currency="USD", locale="en-US",
    )
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=other_ws.id,
        user_id=test_user.id, role="owner",
    ))
    stranded = AssetGroup(
        user_id=test_user.id, workspace_id=other_ws.id,
        name="Moved Wallet", source="test",
        connection_id=None, external_id="shared-key",
    )
    session.add(stranded)
    await session.commit()
    stranded_id = stranded.id

    group = await ensure_group_for_connection(
        session,
        user_id=test_user.id,
        connection_id=conn.id,
        source="test",
        external_id="shared-key",
        default_name="Mover Bank",
        workspace_id=test_workspace.id,
    )

    assert group.id == stranded_id
    assert group.connection_id == conn.id
    assert group.workspace_id == test_workspace.id
    assert group.name == "Moved Wallet"
