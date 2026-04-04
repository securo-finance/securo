import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.bank_connection import BankConnection
from app.models.user import User


@pytest.mark.asyncio
async def test_list_providers(client: AsyncClient, auth_headers):
    """Should return all known providers with their configuration status."""
    response = await client.get("/api/connections/providers", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["providers"]) == 1
    assert data["providers"][0]["name"] == "pluggy"
    assert data["providers"][0]["configured"] is False


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
async def test_sync_connection_requires_current_month(
    client: AsyncClient, auth_headers, test_connection: BankConnection, session
):
    await session.execute(
        update(User)
        .values(
            preferences={
                "language": "pt-BR",
                "date_format": "DD/MM/YYYY",
                "timezone": "America/Sao_Paulo",
                "currency_display": "BRL",
            }
        )
    )
    await session.commit()

    response = await client.post(
        f"/api/connections/{test_connection.id}/sync",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Mês Atual não definido" in response.json()["detail"]
