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
    resp = await client.get(
        "/api/users/lookup", params={"email": "anyone@example.com"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_lookup_returns_the_users_display_name(
    client, auth_headers, session, test_user
):
    """A caller linking a member gets the person's name, not only the email."""
    test_user.preferences = {**(test_user.preferences or {}), "display_name": "User Name"}
    session.add(test_user)
    await session.commit()

    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "User Name"


@pytest.mark.asyncio
async def test_lookup_reports_no_display_name_when_the_user_has_none(
    client, auth_headers, test_user
):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


@pytest.mark.asyncio
async def test_directory_returns_display_names(client, auth_headers, session, test_user):
    """The member picker needs the name of every user it offers."""
    test_user.preferences = {**(test_user.preferences or {}), "display_name": "User Name"}
    session.add(test_user)
    await session.commit()

    resp = await client.get("/api/users/directory", headers=auth_headers)

    assert resp.status_code == 200
    entry = next(u for u in resp.json() if u["id"] == str(test_user.id))
    assert entry["email"] == test_user.email
    assert entry["display_name"] == "User Name"
