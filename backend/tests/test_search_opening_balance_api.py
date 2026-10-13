"""Search carries provenance without translating or mutating stored descriptions."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.services.workspace_service import create_personal_workspace_for_user


@pytest.mark.parametrize("language", ["en", "pt-BR", "nl"])
async def test_search_exposes_source_preserves_raw_fields_and_workspace_scope(
    client, auth_headers, session, test_user, test_account, language
):
    rows = []
    for source in ["opening_balance", "manual"]:
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
            description="Saldo inicial", source=source, amount=Decimal("100.25"),
            currency="BRL", type="credit", date=date(2026, 9, 12),
            created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        )
        session.add(tx)
        rows.append(tx)

    other = User(
        id=uuid.uuid4(), email="search-opening-other@example.test",
        hashed_password="synthetic-unused", is_active=True, is_verified=True,
    )
    session.add(other)
    await session.flush()
    workspace = await create_personal_workspace_for_user(session, other)
    account = Account(
        id=uuid.uuid4(), user_id=other.id, workspace_id=workspace.id,
        name="Private", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    foreign = Transaction(
        id=uuid.uuid4(), user_id=other.id, workspace_id=workspace.id,
        account_id=account.id, description="Saldo inicial", source="opening_balance",
        amount=Decimal("999"), currency="BRL", type="credit", date=date(2026, 9, 12),
    )
    session.add(foreign)
    await session.commit()
    before = [(tx.id, tx.description, tx.source, tx.amount, tx.date) for tx in rows]

    response = await client.get(
        "/api/search", params={"q": "saldo"},
        headers={**auth_headers, "Accept-Language": language},
    )
    assert response.status_code == 200
    hits = [hit for hit in response.json()["results"] if hit["type"] == "transaction"]
    assert {hit["id"] for hit in hits} == {str(tx.id) for tx in rows}
    by_id = {hit["id"]: hit for hit in hits}
    for tx in rows:
        hit = by_id[str(tx.id)]
        assert hit["label"] == "Saldo inicial"
        assert hit["amount"] == 100.25
        assert hit["currency"] == "BRL"
        assert hit["date"] == "2026-09-12"
        assert hit["meta"] == {
            "account_id": str(test_account.id), "category_id": None,
            "tx_type": "credit", "source": tx.source,
        }
    session.expire_all()
    persisted = (await session.execute(select(Transaction).where(
        Transaction.id.in_([row[0] for row in before])
    ))).scalars().all()
    assert sorted((tx.id, tx.description, tx.source, tx.amount, tx.date) for tx in persisted) == sorted(before)
    # Characterize the deferred query-policy decision: display localization
    # does not make an English phrase match raw Portuguese stored fields.
    english_query = await client.get(
        "/api/search", params={"q": "opening balance"}, headers=auth_headers,
    )
    assert english_query.status_code == 200
    assert english_query.json()["results"] == []


async def test_search_still_requires_authentication(client):
    response = await client.get("/api/search", params={"q": "saldo"})
    assert response.status_code == 401