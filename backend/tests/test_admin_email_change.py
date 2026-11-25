"""Changing an account's address is the owner's decision (ADR 0042, #556).

The production audit found the path: an administrator changed a customer's
email to their own mailbox, asked for a recovery link and walked into the
account. The address is a recovery credential, so an administrator may ask for
the change but never apply it: the account keeps its address, and the password
recovery that goes with it, until the person whose account it is confirms the
new one from their own session.
"""
import re
import uuid
from urllib.parse import unquote

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy
from app.models.admin_access_log import AdminAccessLog
from app.models.user import User

pytestmark = pytest.mark.asyncio

REASON = "Customer wrote in from the support inbox"
VICTIM_EMAIL = "test@example.com"
VICTIM_PASSWORD = "testpass123"
NEW_EMAIL = "attacker@example.com"


async def _add_user(session: AsyncSession, email: str, password: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _headers_for(user: User) -> dict:
    token = await get_jwt_strategy().write_token(user)
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post(
        "/api/auth/login", data={"username": email, "password": password}
    )


def _token_from(text: str) -> str:
    """The recovery token carried by the link in the message."""
    match = re.search(r"[?&]token=([^&\s]+)", text)
    assert match, f"no reset link in the message: {text}"
    return unquote(match.group(1))


async def _log_entries(session: AsyncSession) -> list[AdminAccessLog]:
    result = await session.execute(
        select(AdminAccessLog).order_by(AdminAccessLog.created_at)
    )
    return list(result.scalars().all())


async def test_the_administrator_cannot_change_the_login_address_alone(
    client: AsyncClient,
    admin_auth_headers: dict,
    test_user: User,
    session: AsyncSession,
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    # The account keeps its address; the new one waits for its owner.
    assert body["email"] == VICTIM_EMAIL
    assert body["pending_email"] == NEW_EMAIL

    await session.refresh(test_user)
    assert test_user.email == VICTIM_EMAIL
    assert test_user.pending_email == NEW_EMAIL

    # The point of the audit: the account is where it was, and the address the
    # administrator planted opens nothing.
    assert (await _login(client, NEW_EMAIL, VICTIM_PASSWORD)).status_code != 200
    assert (await _login(client, VICTIM_EMAIL, VICTIM_PASSWORD)).status_code == 200


async def test_the_access_log_shows_the_old_address_the_new_one_and_the_fields(
    client: AsyncClient,
    admin_auth_headers: dict,
    test_user: User,
    session: AsyncSession,
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={
            "reason": REASON,
            "email": NEW_EMAIL,
            "preferences": {"date_format": "dmy"},
        },
        headers=admin_auth_headers,
    )
    assert response.status_code == 200

    entries = await _log_entries(session)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.target_email == VICTIM_EMAIL
    assert entry.changes is not None
    assert entry.changes["email"] == {
        "from": VICTIM_EMAIL,
        "to": NEW_EMAIL,
        "pending": True,
    }
    assert entry.changes["preferences"] == {"changed": True}

    # The affected side reads the same thing through the API.
    mine = await client.get(
        "/api/access-log/mine", headers=await _headers_for(test_user)
    )
    assert mine.status_code == 200
    item = mine.json()["items"][0]
    assert item["target_email"] == VICTIM_EMAIL
    assert item["changes"]["email"]["to"] == NEW_EMAIL


async def test_the_owner_confirms_and_only_then_the_address_changes(
    client: AsyncClient, admin_auth_headers: dict, test_user: User
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    headers = await _headers_for(test_user)
    confirmed = await client.post(
        "/api/users/me/email-change", json={"email": NEW_EMAIL}, headers=headers
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == NEW_EMAIL
    assert confirmed.json()["pending_email"] is None

    assert (await _login(client, VICTIM_EMAIL, VICTIM_PASSWORD)).status_code != 200
    assert (await _login(client, NEW_EMAIL, VICTIM_PASSWORD)).status_code == 200


async def test_the_owner_can_decline(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    headers = await _headers_for(test_user)
    declined = await client.delete("/api/users/me/email-change", headers=headers)

    assert declined.status_code == 204
    await session.refresh(test_user)
    assert test_user.email == VICTIM_EMAIL
    assert test_user.pending_email is None
    assert (await _login(client, VICTIM_EMAIL, VICTIM_PASSWORD)).status_code == 200


async def test_confirming_without_a_pending_change_is_refused(
    client: AsyncClient, test_user: User
):
    response = await client.post(
        "/api/users/me/email-change",
        json={"email": "whatever@example.com"},
        headers=await _headers_for(test_user),
    )
    assert response.status_code == 400


async def test_a_pending_address_cannot_belong_to_another_account(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await _add_user(session, "taken@example.com", "whatever12345")

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": "taken@example.com"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    await session.refresh(test_user)
    assert test_user.pending_email is None


async def test_an_own_address_pasted_in_another_case_is_no_request(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """The account's own address, capitalized by someone's mail client.

    `Test@example.com` and `test@example.com` are one mailbox, and it is the
    one this account already answers to: there is nothing to ask the owner
    for. Comparing the two byte for byte would open a request to move the
    account onto the address it already has.
    """
    test_user.email = "Test@example.com"
    await session.commit()

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": "test@example.com"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["email"] == "Test@example.com"
    assert response.json()["pending_email"] is None


async def test_a_pending_address_in_another_case_still_blocks_the_request(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """An address another account is waiting to confirm is not free.

    The waiting address is what the other account is about to move onto, so
    handing it to this one would give two accounts the same mailbox the
    moment the other owner accepts. Compared without regard to case, like
    every other address check here.
    """
    other = await _add_user(session, "other@example.com", "whatever12345")
    other.pending_email = "new@example.com"
    await session.commit()

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": "NEW@example.com"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    await session.refresh(test_user)
    assert test_user.pending_email is None


async def test_an_address_differing_only_in_case_is_taken_too(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """One mailbox cannot feed two accounts (#556).

    Sign-in resolves the address without regard to case, so an account
    holding `taken@example.com` already owns `Taken@example.com`. The
    request must be refused here, not accepted and then lose a race at the
    unique index that a capital letter walks around.
    """
    await _add_user(session, "taken@example.com", "whatever12345")

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": "Taken@example.com"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400
    await session.refresh(test_user)
    assert test_user.pending_email is None


async def test_confirming_an_address_differing_only_in_case_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """The same rule at the moment the owner accepts the address."""
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )
    await _add_user(session, "ATTACKER@example.com", "whatever12345")

    headers = await _headers_for(test_user)
    response = await client.post(
        "/api/users/me/email-change", json={"email": NEW_EMAIL}, headers=headers
    )

    assert response.status_code == 400
    await session.refresh(test_user)
    assert test_user.email == VICTIM_EMAIL


async def test_confirming_an_address_taken_meanwhile_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )
    await _add_user(session, NEW_EMAIL, "whatever12345")

    headers = await _headers_for(test_user)
    response = await client.post(
        "/api/users/me/email-change", json={"email": NEW_EMAIL}, headers=headers
    )

    assert response.status_code == 400
    await session.refresh(test_user)
    assert test_user.email == VICTIM_EMAIL
    # The request stays so its owner can look at it; the address just changed.
    assert test_user.pending_email == NEW_EMAIL


async def test_accepting_an_address_replaced_meanwhile_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """The owner accepts the address they read, not whatever is stored by then.

    An administrator can replace a request that is still waiting. If the owner
    had the dialog open on one address, pressing accept must not move the
    account to a second one they never saw.
    """
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": "Customer asked for another address", "email": "later@example.com"},
        headers=admin_auth_headers,
    )

    headers = await _headers_for(test_user)
    stale = await client.post(
        "/api/users/me/email-change", json={"email": NEW_EMAIL}, headers=headers
    )

    assert stale.status_code == 400
    await session.refresh(test_user)
    assert test_user.email == VICTIM_EMAIL
    assert test_user.pending_email == "later@example.com"

    # Accepting the one that is actually waiting goes through.
    fresh = await client.post(
        "/api/users/me/email-change", json={"email": "later@example.com"}, headers=headers
    )
    assert fresh.status_code == 200
    assert fresh.json()["email"] == "later@example.com"


async def test_accepting_without_naming_an_address_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    response = await client.post(
        "/api/users/me/email-change",
        json={"email": "not an address"},
        headers=await _headers_for(test_user),
    )

    assert response.status_code == 422


async def test_a_conflict_decided_at_commit_is_the_same_refusal(
    test_user: User, session: AsyncSession, monkeypatch
):
    """Two accounts can be asked for one address and both pass the check.

    The unique index decides at commit; the owner still gets the "belongs to
    another account" answer instead of a 500.
    """
    from sqlalchemy.exc import IntegrityError

    from app.services import account_email_service

    test_user.pending_email = NEW_EMAIL
    session.add(test_user)
    await session.commit()

    async def _conflict():
        raise IntegrityError("UPDATE users", {}, Exception("unique"))

    monkeypatch.setattr(session, "commit", _conflict)
    with pytest.raises(ValueError, match="another account"):
        await account_email_service.confirm_pending_email(
            session, test_user, expected_email=NEW_EMAIL
        )


async def test_the_owner_changing_their_own_address_clears_the_pending_one(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    headers = await _headers_for(test_user)
    own = await client.patch(
        "/api/users/me", json={"email": "owner@example.com"}, headers=headers
    )
    assert own.status_code == 200

    await session.refresh(test_user)
    assert test_user.email == "owner@example.com"
    assert test_user.pending_email is None


async def test_setting_the_address_back_to_the_current_one_cancels_the_request(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": NEW_EMAIL},
        headers=admin_auth_headers,
    )

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": "Customer changed their mind", "email": VICTIM_EMAIL},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["email"] == VICTIM_EMAIL
    assert response.json()["pending_email"] is None
    await session.refresh(test_user)
    assert test_user.pending_email is None


async def test_with_recovery_on_the_administrator_still_cannot_take_the_account(
    client: AsyncClient,
    admin_auth_headers: dict,
    test_user: User,
    monkeypatch,
):
    """The whole sequence from #556, with the recovery route from #540 present.

    Skipped on a branch where the email transport does not exist yet: the
    takeover needs both features, and this is the test that keeps the pair
    honest once they are together.
    """
    pytest.importorskip(
        "app.api.password_reset", reason="the recovery route from #540 is not here"
    )

    from importlib import import_module
    from typing import Any

    from app.core.config import get_settings

    # The transport's settings and module only exist on the branch from #540,
    # so they are reached by name: this file has to type-check on both.
    email_service = import_module("app.services.email_service")
    settings = get_settings()
    relay = {
        "email_enabled": True,
        "frontend_url": "https://app.example.com",
        "smtp_host": "smtp.example.com",
        "email_from": "Securo <no-reply@example.com>",
    }
    previous = {name: getattr(settings, name) for name in relay}
    for name, value in relay.items():
        setattr(settings, name, value)
    sent: list[Any] = []

    async def _fake_send(message, _settings=None):
        sent.append(message)

    monkeypatch.setattr(email_service, "send_email", _fake_send)
    try:
        # The administrator plants their own mailbox on the account.
        await client.patch(
            f"/api/admin/users/{test_user.id}",
            json={"reason": REASON, "email": NEW_EMAIL},
            headers=admin_auth_headers,
        )

        # Asking for a link to that address reaches nothing: no account has it.
        asked = await client.post(
            "/api/auth/forgot-password", json={"email": NEW_EMAIL}
        )
        assert asked.status_code == 202
        assert sent == []

        # And the address itself opens nothing.
        assert (await _login(client, NEW_EMAIL, VICTIM_PASSWORD)).status_code != 200

        # The owner, who still has the account and its address, recovers it:
        # the message goes to the address on the account, and its link sets a
        # new password that signs in — while the planted address still has no
        # account to sign in to.
        asked = await client.post(
            "/api/auth/forgot-password", json={"email": VICTIM_EMAIL}
        )
        assert asked.status_code == 202
        assert len(sent) == 1
        assert sent[0].to == VICTIM_EMAIL

        token = _token_from(sent[0].text)
        new_password = "ownerchose123"
        reset = await client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": new_password},
        )
        assert reset.status_code == 200

        assert (
            await _login(client, VICTIM_EMAIL, new_password)
        ).status_code == 200
        assert (await _login(client, NEW_EMAIL, new_password)).status_code != 200
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)
