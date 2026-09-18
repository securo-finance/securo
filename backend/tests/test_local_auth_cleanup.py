"""Removing an enrolled factor stays possible, and stays guarded, in OIDC-only mode.

A server that flips to `local_auth_enabled = false` keeps the users who enrolled
TOTP or a passkey beforehand. There are no recovery codes, so these cleanup routes
are the only way out and must not pick up `require_local_auth_enabled`.
"""
from datetime import datetime, timezone

import pyotp
import pytest
from sqlalchemy import select

from app.core.auth import get_jwt_strategy
from app.models.passkey import UserPasskey


@pytest.mark.parametrize("bad_factor", ["password", "totp"])
async def test_disabled_local_auth_cleanup_requires_both_factors(
    client, test_user, session, oidc_only_settings, bad_factor,
):
    secret = pyotp.random_base32()
    test_user.totp_secret = secret
    test_user.is_2fa_enabled = True
    await session.commit()
    token = await get_jwt_strategy().write_token(test_user)
    totp = pyotp.TOTP(secret)
    now = datetime.now(timezone.utc)
    # Pick a six-digit code outside every accepted window, without a rare collision.
    invalid_code = next(
        f"{value:06d}"
        for value in range(10)
        if not totp.verify(f"{value:06d}", for_time=now, valid_window=1)
    )
    response = await client.post(
        "/api/auth/2fa/disable",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "password": "wrong" if bad_factor == "password" else "testpass123",
            "code": invalid_code if bad_factor == "totp" else totp.at(now),
        },
    )
    assert response.status_code == 400
    expected = "Invalid password" if bad_factor == "password" else "Invalid 2FA code"
    assert response.json()["detail"] == expected
    await session.refresh(test_user)
    assert test_user.is_2fa_enabled is True
    assert test_user.totp_secret == secret


async def test_disabled_local_auth_passkey_cleanup_is_owner_scoped(
    client, test_user, test_user_with_2fa, session, oidc_only_settings,
):
    own = UserPasskey(
        user_id=test_user.id, credential_id="own", public_key="synthetic", name="Own key"
    )
    other = UserPasskey(
        user_id=test_user_with_2fa.id,
        credential_id="other",
        public_key="synthetic",
        name="Other key",
    )
    session.add_all([own, other])
    await session.commit()
    token = await get_jwt_strategy().write_token(test_user)
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/auth/passkeys")).status_code == 401
    assert (await client.delete(f"/api/auth/passkeys/{own.id}")).status_code == 401

    listed = await client.get("/api/auth/passkeys", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(own.id)]

    deleted_other = await client.delete(f"/api/auth/passkeys/{other.id}", headers=headers)
    assert deleted_other.status_code == 404
    deleted_own = await client.delete(f"/api/auth/passkeys/{own.id}", headers=headers)
    assert deleted_own.status_code == 204

    remaining = await session.execute(select(UserPasskey))
    assert [item.id for item in remaining.scalars()] == [other.id]
