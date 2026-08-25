import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.base import (
    AccountData,
    ConnectionData,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
)
from app.services import connection_service


@pytest.mark.asyncio
async def test_list_providers(client: AsyncClient, auth_headers):
    """Should return all known providers with their configuration status."""
    response = await client.get("/api/connections/providers", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    by_name = {p["name"]: p for p in data["providers"]}
    assert "pluggy" in by_name
    assert by_name["pluggy"]["configured"] is False
    assert by_name["pluggy"]["flow_type"] == "widget"
    assert by_name["pluggy"]["supports_asset_sync"] is True
    assert "enable_banking" in by_name
    assert by_name["enable_banking"]["flow_type"] == "oauth"
    assert by_name["enable_banking"]["requires_institution_select"] is True
    # Enable Banking (PSD2) exposes no investment holdings, so the asset-sync
    # opt-out is hidden for it; connectors that import holdings advertise it.
    assert by_name["enable_banking"]["supports_asset_sync"] is False
    assert by_name["simplefin"]["supports_asset_sync"] is True
    assert by_name["ibkr"]["flow_type"] == "token"
    assert by_name["ibkr"]["supports_asset_sync"] is True


@pytest.mark.asyncio
async def test_list_connections(
    client: AsyncClient, auth_headers, test_connection: BankConnection
):
    response = await client.get("/api/connections", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["institution_name"] == "Banco Teste"
    assert data[0]["provider"] == "test"
    assert data[0]["status"] == "active"


@pytest.mark.asyncio
async def test_list_connections_empty(client: AsyncClient, auth_headers):
    response = await client.get("/api/connections", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_oauth_url_unknown_provider(client: AsyncClient, auth_headers):
    """Should fail for unregistered provider."""
    response = await client.post(
        "/api/connections/oauth/url",
        headers=auth_headers,
        json={"provider": "nonexistent"},
    )
    assert response.status_code == 400
    assert "Unknown provider" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_connection(
    client: AsyncClient, auth_headers, test_connection: BankConnection
):
    response = await client.delete(
        f"/api/connections/{test_connection.id}", headers=auth_headers
    )
    assert response.status_code == 204

    # Verify it's gone
    response = await client.get("/api/connections", headers=auth_headers)
    assert response.json() == []


@pytest.mark.asyncio
async def test_delete_connection_not_found(client: AsyncClient, auth_headers, test_connection):
    response = await client.delete(
        "/api/connections/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_connections_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/connections")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_update_settings_not_found(client: AsyncClient, auth_headers):
    resp = await client.patch(
        f"/api/connections/{uuid.uuid4()}/settings",
        json={"payee_source": "merchant"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detect_transfers(client: AsyncClient, auth_headers):
    resp = await client.post("/api/connections/transfers/detect", headers=auth_headers)
    assert resp.status_code == 200
    assert "pairs_created" in resp.json()


@pytest.mark.asyncio
async def test_unlink_transfer_not_found(client: AsyncClient, auth_headers):
    resp = await client.delete(
        f"/api/connections/transfers/{uuid.uuid4()}", headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_connect_token_success(client: AsyncClient, auth_headers):
    mock_token = MagicMock()
    mock_token.access_token = "test-token-123"
    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.create_connect_token = AsyncMock(return_value=mock_token)
        resp = await client.post(
            "/api/connections/connect-token",
            json={"provider": "pluggy"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "test-token-123"


@pytest.mark.asyncio
async def test_create_connect_token_value_error(client: AsyncClient, auth_headers):
    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.side_effect = ValueError("Unknown provider")
        resp = await client.post(
            "/api/connections/connect-token",
            json={"provider": "invalid"},
            headers=auth_headers,
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_connect_token_server_error(client: AsyncClient, auth_headers):
    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.create_connect_token = AsyncMock(
            side_effect=RuntimeError("Provider down")
        )
        resp = await client.post(
            "/api/connections/connect-token",
            json={"provider": "pluggy"},
            headers=auth_headers,
        )
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_oauth_callback_success(client: AsyncClient, auth_headers):
    conn_data = MagicMock()
    conn_data.external_id = "ext-oauth-1"
    conn_data.institution_name = "Test Bank"
    conn_data.credentials = {"token": "abc"}
    conn_data.accounts = []

    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.handle_oauth_callback = AsyncMock(return_value=conn_data)
        resp = await client.post(
            "/api/connections/oauth/callback",
            json={"code": "auth-code-123", "provider": "pluggy"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["institution_name"] == "Test Bank"


@pytest.mark.asyncio
async def test_oauth_callback_token_reconnect_updates_existing(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User
):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="simplefin",
        external_id="old-simplefin", institution_name="Old SimpleFIN",
        credentials={"access_url_enc": "old"}, status="error",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    conn_data = MagicMock()
    conn_data.external_id = "new-simplefin"
    conn_data.institution_name = "New SimpleFIN"
    conn_data.logo_url = None
    conn_data.credentials = {"access_url_enc": "new"}
    conn_data.accounts = []

    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.handle_oauth_callback = AsyncMock(return_value=conn_data)
        resp = await client.post(
            "/api/connections/oauth/callback",
            json={
                "code": "fresh-simplefin-setup-token",
                "provider": "simplefin",
                "reconnect_connection_id": str(conn.id),
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(conn.id)
    assert body["external_id"] == "new-simplefin"
    assert body["status"] == "active"
    await session.refresh(conn)
    assert conn.credentials == {"access_url_enc": "new"}


@pytest.mark.asyncio
async def test_oauth_callback_failure(client: AsyncClient, auth_headers):
    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.handle_oauth_callback = AsyncMock(
            side_effect=Exception("OAuth failed")
        )
        resp = await client.post(
            "/api/connections/oauth/callback",
            json={"code": "bad-code", "provider": "pluggy"},
            headers=auth_headers,
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_token_callback_passes_provider_parameters(
    client: AsyncClient, auth_headers, test_connection: BankConnection
):
    with patch(
        "app.services.connection_service.handle_token_callback",
        new=AsyncMock(return_value=test_connection),
    ) as callback:
        resp = await client.post(
            "/api/connections/token/callback",
            json={
                "provider": "ibkr",
                "token": "flex-secret",
                "parameters": {"query_id": "123456"},
                "sync_assets": True,
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == str(test_connection.id)
    assert callback.await_args.args[3:5] == ("ibkr", "flex-secret")
    assert callback.await_args.kwargs["parameters"] == {"query_id": "123456"}
    assert callback.await_args.kwargs["sync_assets"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code"),
    [
        (ProviderRateLimited("Try later"), 429, "provider_rate_limited"),
        (ProviderTransientError("Still generating"), 503, "provider_transient"),
    ],
)
async def test_token_callback_surfaces_temporary_provider_errors(
    client: AsyncClient,
    auth_headers,
    provider_error,
    expected_status: int,
    expected_code: str,
):
    with patch(
        "app.services.connection_service.handle_token_callback",
        new=AsyncMock(side_effect=provider_error),
    ):
        resp = await client.post(
            "/api/connections/token/callback",
            json={
                "provider": "ibkr",
                "token": "flex-secret",
                "parameters": {"query_id": "123456"},
            },
            headers=auth_headers,
        )

    assert resp.status_code == expected_status
    assert resp.json()["detail"]["code"] == expected_code


@pytest.mark.asyncio
async def test_token_callback_redacts_unexpected_provider_errors(
    client: AsyncClient, auth_headers, caplog
):
    with patch(
        "app.services.connection_service.handle_token_callback",
        new=AsyncMock(side_effect=RuntimeError("upstream echoed flex-secret")),
    ):
        resp = await client.post(
            "/api/connections/token/callback",
            json={
                "provider": "ibkr",
                "token": "flex-secret",
                "parameters": {"query_id": "123456"},
            },
            headers=auth_headers,
        )

    assert resp.status_code == 502
    assert "flex-secret" not in str(resp.json())
    assert "flex-secret" not in caplog.text


@pytest.mark.asyncio
async def test_token_callback_rejects_non_token_provider(
    session: AsyncSession, test_connection: BankConnection
):
    provider = MagicMock(flow_type="oauth")
    provider.connect_with_token = AsyncMock()

    with patch("app.services.connection_service.get_provider", return_value=provider):
        with pytest.raises(ValueError, match="does not support token connections"):
            await connection_service.handle_token_callback(
                session,
                test_connection.workspace_id,
                test_connection.user_id,
                "enable_banking",
                "not-an-oauth-code",
            )

    provider.connect_with_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_reconnect_reuses_initial_report_without_duplicates(
    session: AsyncSession, test_connection: BankConnection
):
    test_connection.provider = "ibkr"
    test_connection.external_id = "ibkr:U123"
    test_connection.credentials = {"flex_token_enc": "old", "query_id": "111"}
    test_connection.settings = {"sync_assets": False}
    account = Account(
        user_id=test_connection.user_id,
        workspace_id=test_connection.workspace_id,
        connection_id=test_connection.id,
        external_id="U123:USD",
        name="Old cash account",
        type="investment",
        balance=Decimal("10"),
        currency="USD",
    )
    session.add(account)
    await session.flush()
    session.add(
        Transaction(
            user_id=test_connection.user_id,
            workspace_id=test_connection.workspace_id,
            account_id=account.id,
            external_id="ibkr:U123:USD:TX-1",
            description="Existing dividend",
            amount=Decimal("5"),
            currency="USD",
            date=date.today(),
            type="credit",
            source="sync",
            status="posted",
        )
    )
    await session.commit()

    provider = MagicMock(flow_type="token")
    provider.connect_with_token = AsyncMock(
        return_value=ConnectionData(
            external_id="ibkr:U123",
            institution_name="Interactive Brokers - Main",
            credentials={"flex_token_enc": "new", "query_id": "222"},
            accounts=[
                AccountData(
                    external_id="U123:USD",
                    name="Main Cash (USD)",
                    type="investment",
                    balance=Decimal("25"),
                    currency="USD",
                    masked_number="U123",
                )
            ],
        )
    )
    provider.get_transactions = AsyncMock(
        return_value=[
            TransactionData(
                external_id="ibkr:U123:USD:TX-1",
                description="Existing dividend",
                amount=Decimal("5"),
                currency="USD",
                date=date.today(),
                type="credit",
            ),
            TransactionData(
                external_id="ibkr:U123:USD:TX-2",
                description="New interest",
                amount=Decimal("2"),
                currency="USD",
                date=date.today(),
                type="credit",
            ),
        ]
    )

    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch(
            "app.services.connection_service.detect_transfer_pairs",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.connection_service.stamp_primary_amount",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.connection_service.apply_rules_to_transaction",
            new_callable=AsyncMock,
        ),
    ):
        reconnected = await connection_service.handle_token_callback(
            session,
            test_connection.workspace_id,
            test_connection.user_id,
            "ibkr",
            "new-secret",
            parameters={"query_id": "222"},
            sync_assets=False,
            reconnect_connection_id=test_connection.id,
        )

    assert reconnected.id == test_connection.id
    assert reconnected.credentials == {"flex_token_enc": "new", "query_id": "222"}
    assert reconnected.last_sync_at is not None
    await session.refresh(account)
    assert account.name == "Main Cash (USD)"
    assert account.balance == Decimal("25")
    ibkr_transactions = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.external_id.like("ibkr:%"),
            )
        )
    ).scalars().all()
    assert sorted(tx.external_id for tx in ibkr_transactions) == [
        "ibkr:U123:USD:TX-1",
        "ibkr:U123:USD:TX-2",
    ]
    provider.connect_with_token.assert_awaited_once()
    provider.get_transactions.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_connection_not_found(client: AsyncClient, auth_headers):
    resp = await client.post(
        f"/api/connections/{uuid.uuid4()}/sync", headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_connection_user_action_returns_conflict(
    client: AsyncClient, auth_headers
):
    with patch("app.services.connection_service.sync_connection") as mock_sync:
        mock_sync.side_effect = ProviderUserActionRequired(
            "SimpleFIN refused the request (403)",
            code="credentials_invalid",
            help_url="https://bridge.simplefin.org/",
        )
        resp = await client.post(
            f"/api/connections/{uuid.uuid4()}/sync", headers=auth_headers,
        )

    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "message": "SimpleFIN refused the request (403)",
        "code": "credentials_invalid",
        "help_url": "https://bridge.simplefin.org/",
    }


@pytest.mark.asyncio
async def test_sync_connection_session_expired_returns_gone(
    client: AsyncClient, auth_headers
):
    with patch("app.services.connection_service.sync_connection") as mock_sync:
        mock_sync.side_effect = SessionExpiredError("SimpleFIN access URL is missing")
        resp = await client.post(
            f"/api/connections/{uuid.uuid4()}/sync", headers=auth_headers,
        )

    assert resp.status_code == 410
    assert resp.json()["detail"] == "SimpleFIN access URL is missing"


@pytest.mark.asyncio
async def test_reconnect_token_not_found(client: AsyncClient, auth_headers):
    resp = await client.post(
        f"/api/connections/{uuid.uuid4()}/reconnect-token", headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reconnect_token_no_item_id(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User,
):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id="ext-recon-no-item", institution_name="NoItem Bank",
        credentials={}, status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    resp = await client.post(
        f"/api/connections/{conn.id}/reconnect-token", headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "item_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reconnect_token_with_item_id(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User,
):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id="ext-recon-ok", institution_name="Recon Bank",
        credentials={"item_id": "item-abc-123"}, status="error",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    mock_token = MagicMock()
    mock_token.access_token = "recon-token"
    with patch("app.services.connection_service.get_provider") as mock_gp:
        mock_gp.return_value.create_connect_token = AsyncMock(return_value=mock_token)
        resp = await client.post(
            f"/api/connections/{conn.id}/reconnect-token", headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "recon-token"


@pytest.mark.asyncio
async def test_update_settings_success(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User,
):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id="ext-settings-1", institution_name="Settings Bank",
        credentials={}, status="active", settings={"payee_source": "auto"},
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    resp = await client.patch(
        f"/api/connections/{conn.id}/settings",
        json={"payee_source": "merchant"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["payee_source"] == "merchant"
