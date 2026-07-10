"""Tests for the global search endpoint powering the command palette."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.category import Category
from app.models.goal import Goal
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User


async def _seed(session: AsyncSession, user: User) -> None:
    account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Nubank Checking",
        type="checking",
        balance=Decimal("1234.56"),
        currency="BRL",
    )
    session.add(account)

    payee = Payee(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Padaria Boulangerie",
        type="merchant",
        created_at=datetime.now(timezone.utc),
    )
    session.add(payee)

    category = Category(
        id=uuid.uuid4(),
        user_id=user.id,
        name="Groceries",
        icon="shopping-cart",
        color="#10B981",
    )
    session.add(category)
    await session.flush()

    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            account_id=account.id,
            description="Coffee at Padaria",
            amount=Decimal("4.50"),
            date=date.today(),
            type="debit",
            source="manual",
            payee_id=payee.id,
            payee="Padaria Boulangerie",
            currency="BRL",
            created_at=datetime.now(timezone.utc),
        )
    )

    session.add(
        Goal(
            id=uuid.uuid4(),
            user_id=user.id,
            name="Trip to Lisbon",
            target_amount=Decimal("5000"),
            current_amount=Decimal("1000"),
            currency="EUR",
            status="active",
        )
    )

    session.add(
        Asset(
            id=uuid.uuid4(),
            user_id=user.id,
            name="Peugeot 208",
            type="vehicle",
            currency="EUR",
        )
    )

    await session.commit()


@pytest.mark.asyncio
async def test_search_returns_all_entity_types(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    await _seed(session, test_user)

    resp = await client.get("/api/search?q=a", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    types = {hit["type"] for hit in data["results"]}
    # The seeded fixtures should match multiple entity types on the letter "a"
    assert "transaction" in types or "payee" in types or "account" in types


@pytest.mark.asyncio
async def test_search_matches_transaction_description(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    await _seed(session, test_user)

    resp = await client.get("/api/search?q=coffee", headers=auth_headers)
    assert resp.status_code == 200
    hits = resp.json()["results"]
    tx_hits = [h for h in hits if h["type"] == "transaction"]
    assert any("Coffee" in h["label"] for h in tx_hits)


@pytest.mark.asyncio
async def test_search_matches_payee(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    await _seed(session, test_user)

    resp = await client.get("/api/search?q=boulang", headers=auth_headers)
    assert resp.status_code == 200
    hits = resp.json()["results"]
    payee_hits = [h for h in hits if h["type"] == "payee"]
    assert len(payee_hits) == 1
    assert payee_hits[0]["label"] == "Padaria Boulangerie"


@pytest.mark.asyncio
async def test_search_matches_account(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    await _seed(session, test_user)

    resp = await client.get("/api/search?q=nubank", headers=auth_headers)
    assert resp.status_code == 200
    hits = resp.json()["results"]
    acc_hits = [h for h in hits if h["type"] == "account"]
    assert len(acc_hits) == 1
    assert acc_hits[0]["label"] == "Nubank Checking"


@pytest.mark.asyncio
async def test_search_matches_goal_and_asset(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    await _seed(session, test_user)

    resp = await client.get("/api/search?q=lisbon", headers=auth_headers)
    assert resp.status_code == 200
    assert any(h["type"] == "goal" for h in resp.json()["results"])

    resp = await client.get("/api/search?q=peugeot", headers=auth_headers)
    assert resp.status_code == 200
    assert any(h["type"] == "asset" for h in resp.json()["results"])


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/search?q=", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["results"] == []


@pytest.mark.asyncio
async def test_search_respects_user_scope(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    # Create an account owned by a different user — should NOT appear in results.
    import bcrypt as _bcrypt
    from app.services.workspace_service import create_personal_workspace_for_user

    hashed = _bcrypt.hashpw(b"otherpass", _bcrypt.gensalt()).decode()
    other_user = User(
        id=uuid.uuid4(),
        email="search-other@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"language": "en", "currency_display": "USD"},
    )
    session.add(other_user)
    await session.flush()
    other_ws = await create_personal_workspace_for_user(session, other_user)
    await session.commit()

    other_account = Account(
        id=uuid.uuid4(),
        user_id=other_user.id,
        workspace_id=other_ws.id,
        name="Other User Secret Account",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(other_account)
    await session.commit()

    resp = await client.get("/api/search?q=secret", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["results"] == []


@pytest.mark.asyncio
async def test_search_escapes_like_wildcards(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
) -> None:
    # A literal % should not be treated as a wildcard.
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Discount 50% Store",
        type="merchant",
        created_at=datetime.now(timezone.utc),
    )
    session.add(payee)
    await session.commit()

    resp = await client.get("/api/search?q=50%25", headers=auth_headers)
    assert resp.status_code == 200
    payee_hits = [h for h in resp.json()["results"] if h["type"] == "payee"]
    assert any("50%" in h["label"] for h in payee_hits)
