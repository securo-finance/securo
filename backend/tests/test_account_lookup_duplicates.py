"""One address that names two accounts must never answer 500 (#568).

The column's unique index is case-sensitive, so an instance built before the
case-insensitive checks can already hold `ana@example.com` and
`Ana@example.com`. Every lookup that expected one row raised
``MultipleResultsFound``: the administrator's change of address, the member
invite, the address lookup behind it, the group member and sign-in itself
answered 500, which left both accounts unreachable. Each door now resolves the
address to one account, the same one on every request: the row written in the
spelling that was typed, so each spelling reaches its own account.
"""
import uuid

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy
from app.models.user import User

pytestmark = pytest.mark.asyncio

REASON = "Support asked for the address on the ticket"
ADDRESS = "pair@example.com"
OTHER_CASE = "Pair@example.com"


async def _add_account(
    session: AsyncSession,
    email: str,
    password: str = "legacypass123",
    account_id: uuid.UUID | None = None,
) -> User:
    """An account stored exactly as typed, the way an older release left one."""
    user = User(
        id=account_id or uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _two_accounts_for_one_address(session: AsyncSession) -> list[User]:
    """The pair, with the row written in ADDRESS given the higher id.

    Ordering by id alone would pick the other one, so every test below proves
    the exact spelling is what decides, not the random UUID (#571).
    """
    return [
        await _add_account(session, ADDRESS, account_id=uuid.UUID(int=2)),
        await _add_account(
            session, OTHER_CASE, "otherpass123", account_id=uuid.UUID(int=1)
        ),
    ]


def _written_as(accounts: list[User], spelling: str) -> User:
    """The account the lookup answers with: the exact spelling wins.

    The id is a random UUID, so it cannot tell the two accounts apart: only
    the spelling the person typed can (#568, #571).
    """
    return next(account for account in accounts if account.email == spelling)


async def _headers_for(user: User) -> dict:
    token = await get_jwt_strategy().write_token(user)
    return {"Authorization": f"Bearer {token}"}


async def _accounts_for_the_address(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(func.lower(User.email) == ADDRESS.lower())
    )
    return result.scalar_one()


async def test_the_admin_change_is_refused_instead_of_failing(
    client: AsyncClient,
    admin_auth_headers: dict,
    test_user: User,
    session: AsyncSession,
):
    accounts = await _two_accounts_for_one_address(session)

    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": ADDRESS},
        headers=admin_auth_headers,
    )

    assert response.status_code == 400, response.text
    await session.refresh(test_user)
    assert test_user.pending_email is None
    assert {account.id for account in accounts} != {test_user.id}


async def test_the_owner_confirmation_is_refused_instead_of_failing(
    client: AsyncClient,
    admin_auth_headers: dict,
    test_user: User,
    session: AsyncSession,
):
    asked = "wasfree@example.com"
    requested = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "email": asked},
        headers=admin_auth_headers,
    )
    assert requested.status_code == 200, requested.text
    await _add_account(session, asked)
    await _add_account(session, asked.upper())

    response = await client.post(
        "/api/users/me/email-change",
        json={"email": asked},
        headers=await _headers_for(test_user),
    )

    assert response.status_code == 400, response.text
    await session.refresh(test_user)
    assert test_user.email == "test@example.com"


async def test_the_invite_adds_the_person_instead_of_failing(
    client: AsyncClient,
    auth_headers: dict,
    test_workspace,
    session: AsyncSession,
):
    accounts = await _two_accounts_for_one_address(session)

    response = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        json={"email": ADDRESS, "role": "editor"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["email"] in {account.email for account in accounts}
    # None of the doors created a third account for the same mailbox.
    assert await _accounts_for_the_address(session) == 2


async def test_the_address_lookup_answers_with_the_exact_spelling(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
):
    accounts = await _two_accounts_for_one_address(session)

    response = await client.get(
        "/api/users/lookup", params={"email": ADDRESS}, headers=auth_headers
    )
    other = await client.get(
        "/api/users/lookup", params={"email": OTHER_CASE}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(_written_as(accounts, ADDRESS).id)
    assert other.status_code == 200, other.text
    assert other.json()["id"] == str(_written_as(accounts, OTHER_CASE).id)


async def test_a_spelling_written_on_neither_row_falls_back_to_the_lowest_id(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
):
    """When neither row is written the way that was typed, the lowest id still
    answers, so the fallback never depends on row order."""
    accounts = await _two_accounts_for_one_address(session)

    response = await client.get(
        "/api/users/lookup", params={"email": "PAIR@EXAMPLE.COM"}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(min(accounts, key=lambda a: a.id).id)


async def test_the_group_member_links_to_the_exact_spelling(
    client: AsyncClient,
    auth_headers: dict,
    test_workspace,
    session: AsyncSession,
):
    accounts = await _two_accounts_for_one_address(session)
    group = await client.post(
        "/api/groups",
        json={"name": "House"},
        headers=auth_headers,
    )
    assert group.status_code == 201, group.text

    response = await client.post(
        f"/api/groups/{group.json()['id']}/members",
        json={"name": "Ana", "email": ADDRESS},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["linked_user_id"] == str(_written_as(accounts, ADDRESS).id)


async def test_sign_in_reaches_the_account_of_the_typed_spelling(
    client: AsyncClient,
    clean_db,
    session: AsyncSession,
):
    await _two_accounts_for_one_address(session)
    passwords = {ADDRESS: "legacypass123", OTHER_CASE: "otherpass123"}

    for spelling, password in passwords.items():
        signed_in = await client.post(
            "/api/auth/login",
            data={"username": spelling, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert signed_in.status_code == 200, signed_in.text
        me = await client.get(
            "/api/users/me",
            headers={
                "Authorization": f"Bearer {signed_in.json()['access_token']}"
            },
        )
        assert me.status_code == 200, me.text
        # Each spelling reaches its own account, never the other one.
        assert me.json()["email"] == spelling

    # The other account's password on this spelling is a wrong password, not
    # an error page and not a sign-in to the wrong account.
    refused = await client.post(
        "/api/auth/login",
        data={"username": ADDRESS, "password": passwords[OTHER_CASE]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert refused.status_code == 400, refused.text
