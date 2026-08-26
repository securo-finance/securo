"""Tests for setup API endpoints.

Tests: GET /api/setup/status, POST /api/setup/create-admin.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_setup_status_has_users(client: AsyncClient, test_user):
    """Setup status returns has_users=True when users exist."""
    response = await client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json()["has_users"] is True


@pytest.mark.asyncio
async def test_setup_status_no_users(client: AsyncClient, clean_db):
    """Setup status returns has_users=False when no users exist."""
    response = await client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json()["has_users"] is False


@pytest.mark.asyncio
async def test_create_admin_success(client: AsyncClient, clean_db):
    """Create admin succeeds when no users exist."""
    response = await client.post(
        "/api/setup/create-admin",
        json={
            "email": "admin@test.com",
            "password": "StrongPass123!",
            "currency": "BRL",
            "name": "Admin",
            "language": "pt-BR",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_create_admin_forbidden_when_local_auth_disabled(
    client: AsyncClient,
    clean_db,
    oidc_only_settings,
):
    response = await client.post(
        "/api/setup/create-admin",
        json={"email": "admin@test.com", "password": "StrongPass123!"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"
    status_response = await client.get("/api/setup/status")
    assert status_response.json()["has_users"] is False


@pytest.mark.asyncio
async def test_create_admin_already_exists(client: AsyncClient, test_user):
    """Create admin fails when users already exist."""
    response = await client.post(
        "/api/setup/create-admin",
        json={
            "email": "another@test.com",
            "password": "Pass123!",
        },
    )
    assert response.status_code == 403
    assert "already completed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_admin_english_wallet(client: AsyncClient, clean_db):
    """Create admin with English language creates 'Wallet' account."""
    response = await client.post(
        "/api/setup/create-admin",
        json={
            "email": "en-admin@test.com",
            "password": "StrongPass123!",
            "currency": "USD",
            "language": "en",
        },
    )
    assert response.status_code == 200
    data = response.json()

    # Use the token to check accounts
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    accounts = await client.get("/api/accounts", headers=headers)
    assert accounts.status_code == 200
    names = [a["name"] for a in accounts.json()]
    assert "Wallet" in names


@pytest.mark.asyncio
async def test_create_admin_portuguese_wallet(client: AsyncClient, clean_db):
    """Create admin with Portuguese language creates 'Carteira' account."""
    response = await client.post(
        "/api/setup/create-admin",
        json={
            "email": "pt-admin@test.com",
            "password": "StrongPass123!",
            "currency": "BRL",
            "language": "pt-BR",
        },
    )
    assert response.status_code == 200
    data = response.json()

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    accounts = await client.get("/api/accounts", headers=headers)
    assert accounts.status_code == 200
    names = [a["name"] for a in accounts.json()]
    assert "Carteira" in names
