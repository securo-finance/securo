import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.user import User


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
    assert "enable_banking" in by_name
    assert by_name["enable_banking"]["flow_type"] == "oauth"
    assert by_name["enable_banking"]["requires_institution_select"] is True


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
async def test_sync_connection_not_found(client: AsyncClient, auth_headers):
    resp = await client.post(
        f"/api/connections/{uuid.uuid4()}/sync", headers=auth_headers,
    )
    assert resp.status_code == 404


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
    assert "reconnect identifier" in resp.json()["detail"]


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
