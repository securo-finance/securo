import pytest


@pytest.mark.asyncio
async def test_lookup_existing_user(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"].lower() == test_user.email.lower()
    assert body["id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_lookup_case_insensitive(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email.upper()},
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lookup_missing_user(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": "nobody@example.org"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lookup_requires_auth(client):
    resp = await client.get("/api/users/lookup", params={"email": "anyone@example.com"})
    assert resp.status_code == 401
