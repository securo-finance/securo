from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.passkey import UserPasskey


class _RedisStore:
    def __init__(self):
        self.store = {}
        self.get = AsyncMock(side_effect=self._get)
        self.getdel = AsyncMock(side_effect=self._getdel)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)
        pipe = AsyncMock()
        pipe.zremrangebyscore = AsyncMock()
        pipe.zcard = AsyncMock()
        pipe.zadd = AsyncMock()
        pipe.expire = AsyncMock()
        pipe.execute = AsyncMock(return_value=[0, 0, True, True])
        self.pipeline = lambda: pipe

    async def _get(self, key):
        return self.store.get(key)

    async def _getdel(self, key):
        return self.store.pop(key, None)

    async def _set(self, key, value, ex=None):
        self.store[key] = value

    async def _delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _passkey_redis_store(_mock_redis):
    redis = _RedisStore()

    async def _fake():
        return redis

    with patch("app.core.redis.get_redis", _fake), \
         patch("app.core.rate_limit.get_redis", _fake), \
         patch("app.api.custom_auth.get_redis", _fake), \
         patch("app.api.two_factor.get_redis", _fake), \
         patch("app.api.passkeys.get_redis", _fake):
        yield redis


async def test_register_options_requires_auth(client: AsyncClient):
    response = await client.post("/api/auth/passkeys/register/options", json={"name": "YubiKey"})

    assert response.status_code == 401


async def test_register_and_list_passkey(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user,
):
    options_response = await client.post(
        "/api/auth/passkeys/register/options",
        json={"name": "YubiKey"},
        headers=auth_headers,
    )
    assert options_response.status_code == 200
    options_data = options_response.json()
    assert options_data["challenge_id"]
    assert options_data["options"]["rp"]["name"] == "Securo"
    assert options_data["options"]["user"]["name"] == test_user.email

    verification = SimpleNamespace(
        credential_id=b"credential-1",
        credential_public_key=b"public-key-1",
        sign_count=7,
        aaguid="test-aaguid",
        fmt="none",
        credential_type="public-key",
    )
    with patch("app.api.passkeys.verify_registration_response", return_value=verification):
        verify_response = await client.post(
            "/api/auth/passkeys/register/verify",
            json={
                "challenge_id": options_data["challenge_id"],
                "name": "YubiKey",
                "credential": {
                    "id": "credential-1",
                    "rawId": "Y3JlZGVudGlhbC0x",
                    "response": {
                        "attestationObject": "YXR0ZXN0YXRpb24",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                    "transports": ["usb", "nfc"],
                },
            },
            headers=auth_headers,
        )

    assert verify_response.status_code == 200
    created = verify_response.json()
    assert created["name"] == "YubiKey"
    assert "credential_id" not in created

    list_response = await client.get("/api/auth/passkeys", headers=auth_headers)
    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()] == ["YubiKey"]

    result = await session.execute(select(UserPasskey).where(UserPasskey.user_id == test_user.id))
    passkey = result.scalar_one()
    assert passkey.public_key == "cHVibGljLWtleS0x"
    assert passkey.sign_count == 7
    assert passkey.transports == ["usb", "nfc"]


async def test_authenticate_passkey_returns_jwt_and_bypasses_2fa(
    client: AsyncClient,
    session: AsyncSession,
    test_user_with_2fa,
):
    passkey = UserPasskey(
        user_id=test_user_with_2fa.id,
        credential_id="Y3JlZGVudGlhbC0y",
        public_key="cHVibGljLWtleS0y",
        sign_count=3,
        name="Platform passkey",
    )
    session.add(passkey)
    await session.commit()
    await session.refresh(passkey)

    email_options_response = await client.post(
        "/api/auth/passkeys/authenticate/options",
        json={"email": test_user_with_2fa.email.upper()},
    )
    assert email_options_response.status_code == 200
    email_options = email_options_response.json()["options"]
    assert email_options.get("allowCredentials") in (None, [])
    challenge_id = email_options_response.json()["challenge_id"]

    verification = SimpleNamespace(
        credential_id=b"credential-2",
        new_sign_count=8,
    )
    with patch("app.api.passkeys.verify_authentication_response", return_value=verification):
        verify_response = await client.post(
            "/api/auth/passkeys/authenticate/verify",
            json={
                "challenge_id": challenge_id,
                "credential": {
                    "id": "credential-2",
                    "rawId": "Y3JlZGVudGlhbC0y",
                    "response": {
                        "authenticatorData": "YXV0aC1kYXRh",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                        "signature": "c2lnbmF0dXJl",
                        "userHandle": None,
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
        )

    assert verify_response.status_code == 200
    data = verify_response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "requires_2fa" not in data

    await session.refresh(passkey)
    assert passkey.sign_count == 8
    assert isinstance(passkey.last_used_at, datetime)


async def test_password_login_with_passkey_requires_second_factor(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
):
    session.add(
        UserPasskey(
            user_id=test_user.id,
            credential_id="cGFzc3dvcmQtbWZhLWtleQ",
            public_key="cHVibGljLWtleQ",
            sign_count=1,
            name="Security key",
        )
    )
    await session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": test_user.email, "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["requires_2fa"] is True
    assert data["temp_token"]
    assert data["available_methods"] == ["passkey"]
    assert "access_token" not in data


async def test_passkey_second_factor_returns_jwt_for_same_user_passkey(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
):
    passkey = UserPasskey(
        user_id=test_user.id,
        credential_id="c2FtZS11c2VyLWtleQ",
        public_key="c2FtZS11c2VyLXB1YmxpYw",
        sign_count=2,
        name="Security key",
    )
    session.add(passkey)
    await session.commit()
    await session.refresh(passkey)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": test_user.email, "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temp_token = login_response.json()["temp_token"]

    options_response = await client.post("/api/auth/passkeys/2fa/options", json={"temp_token": temp_token})
    assert options_response.status_code == 200
    options_data = options_response.json()
    assert options_data["options"]["allowCredentials"][0]["id"] == passkey.credential_id

    verification = SimpleNamespace(credential_id=b"same-user-key", new_sign_count=9)
    with patch("app.api.passkeys.verify_authentication_response", return_value=verification):
        verify_response = await client.post(
            "/api/auth/passkeys/2fa/verify",
            json={
                "temp_token": temp_token,
                "challenge_id": options_data["challenge_id"],
                "credential": {
                    "id": "same-user-key",
                    "rawId": "c2FtZS11c2VyLWtleQ",
                    "response": {
                        "authenticatorData": "YXV0aC1kYXRh",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                        "signature": "c2lnbmF0dXJl",
                        "userHandle": None,
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
        )

    assert verify_response.status_code == 200
    data = verify_response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    await session.refresh(passkey)
    assert passkey.sign_count == 9
    assert isinstance(passkey.last_used_at, datetime)


async def test_passkey_second_factor_rejects_other_users_passkey(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
    test_user_with_2fa,
):
    own_passkey = UserPasskey(
        user_id=test_user.id,
        credential_id="b3duZXItbWZhLWtleQ",
        public_key="b3duZXItcHVibGlj",
        sign_count=1,
        name="Own key",
    )
    other_passkey = UserPasskey(
        user_id=test_user_with_2fa.id,
        credential_id="b3RoZXItbWZhLWtleQ",
        public_key="b3RoZXItcHVibGlj",
        sign_count=1,
        name="Other key",
    )
    session.add_all([own_passkey, other_passkey])
    await session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": test_user.email, "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temp_token = login_response.json()["temp_token"]

    options_response = await client.post("/api/auth/passkeys/2fa/options", json={"temp_token": temp_token})
    assert options_response.status_code == 200

    with patch("app.api.passkeys.verify_authentication_response") as verify_mock:
        verify_response = await client.post(
            "/api/auth/passkeys/2fa/verify",
            json={
                "temp_token": temp_token,
                "challenge_id": options_response.json()["challenge_id"],
                "credential": {
                    "id": "other-mfa-key",
                    "rawId": "b3RoZXItbWZhLWtleQ",
                    "response": {
                        "authenticatorData": "YXV0aC1kYXRh",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                        "signature": "c2lnbmF0dXJl",
                        "userHandle": None,
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
        )

    assert verify_response.status_code == 401
    verify_mock.assert_not_called()


async def test_authenticate_email_bound_challenge_rejects_other_users_passkey(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
    test_user_with_2fa,
):
    expected_passkey = UserPasskey(
        user_id=test_user_with_2fa.id,
        credential_id="ZXhwZWN0ZWQ",
        public_key="cHVibGljLWtleS1leHBlY3RlZA",
        sign_count=1,
        name="Expected key",
    )
    other_passkey = UserPasskey(
        user_id=test_user.id,
        credential_id="b3RoZXIta2V5",
        public_key="cHVibGljLWtleS1vdGhlcg",
        sign_count=1,
        name="Other key",
    )
    session.add_all([expected_passkey, other_passkey])
    await session.commit()

    options_response = await client.post(
        "/api/auth/passkeys/authenticate/options",
        json={"email": test_user_with_2fa.email},
    )
    assert options_response.status_code == 200

    with patch("app.api.passkeys.verify_authentication_response") as verify_mock:
        verify_response = await client.post(
            "/api/auth/passkeys/authenticate/verify",
            json={
                "challenge_id": options_response.json()["challenge_id"],
                "credential": {
                    "id": "other-key",
                    "rawId": "b3RoZXIta2V5",
                    "response": {
                        "authenticatorData": "YXV0aC1kYXRh",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                        "signature": "c2lnbmF0dXJl",
                        "userHandle": None,
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
        )

    assert verify_response.status_code == 401
    verify_mock.assert_not_called()


async def test_authenticate_email_bound_challenge_rejects_unknown_email(
    client: AsyncClient,
    session: AsyncSession,
    test_user,
):
    passkey = UserPasskey(
        user_id=test_user.id,
        credential_id="b3duZXIta2V5",
        public_key="cHVibGljLWtleS1vd25lcg",
        sign_count=1,
        name="Owner key",
    )
    session.add(passkey)
    await session.commit()

    options_response = await client.post(
        "/api/auth/passkeys/authenticate/options",
        json={"email": "missing@example.com"},
    )
    assert options_response.status_code == 200

    with patch("app.api.passkeys.verify_authentication_response") as verify_mock:
        verify_response = await client.post(
            "/api/auth/passkeys/authenticate/verify",
            json={
                "challenge_id": options_response.json()["challenge_id"],
                "credential": {
                    "id": "owner-key",
                    "rawId": "b3duZXIta2V5",
                    "response": {
                        "authenticatorData": "YXV0aC1kYXRh",
                        "clientDataJSON": "Y2xpZW50LWRhdGE",
                        "signature": "c2lnbmF0dXJl",
                        "userHandle": None,
                    },
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
        )

    assert verify_response.status_code == 401
    verify_mock.assert_not_called()


async def test_delete_passkey_is_owner_scoped(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user,
    test_user_with_2fa,
):
    own_passkey = UserPasskey(
        user_id=test_user.id,
        credential_id="b3du",
        public_key="cHVi",
        name="Own key",
    )
    other_passkey = UserPasskey(
        user_id=test_user_with_2fa.id,
        credential_id="b3RoZXI",
        public_key="cHVi",
        name="Other key",
    )
    session.add_all([own_passkey, other_passkey])
    await session.commit()
    await session.refresh(own_passkey)
    await session.refresh(other_passkey)

    forbidden = await client.delete(f"/api/auth/passkeys/{other_passkey.id}", headers=auth_headers)
    assert forbidden.status_code == 404

    deleted = await client.delete(f"/api/auth/passkeys/{own_passkey.id}", headers=auth_headers)
    assert deleted.status_code == 204

    remaining = await session.execute(select(UserPasskey).order_by(UserPasskey.name))
    assert [item.name for item in remaining.scalars().all()] == ["Other key"]


async def test_register_verify_deletes_challenge_after_failure(
    client: AsyncClient,
    auth_headers: dict,
    _passkey_redis_store,
):
    options_response = await client.post(
        "/api/auth/passkeys/register/options",
        json={"name": "Key"},
        headers=auth_headers,
    )
    challenge_id = options_response.json()["challenge_id"]

    with patch("app.api.passkeys.verify_registration_response", side_effect=ValueError("bad")):
        response = await client.post(
            "/api/auth/passkeys/register/verify",
            json={
                "challenge_id": challenge_id,
                "name": "Key",
                "credential": {
                    "id": "bad",
                    "rawId": "YmFk",
                    "response": {"attestationObject": "bad", "clientDataJSON": "bad"},
                    "type": "public-key",
                    "clientExtensionResults": {},
                },
            },
            headers=auth_headers,
        )

    assert response.status_code == 400
    _passkey_redis_store.getdel.assert_called_once()
    _passkey_redis_store.delete.assert_not_called()
    assert _passkey_redis_store.store == {}
