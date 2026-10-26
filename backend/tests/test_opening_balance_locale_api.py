"""Backend-to-wire proof; UI translation is covered separately by Vitest."""

import json
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


@pytest.mark.asyncio
async def test_opening_balance_source_and_raw_description_survive_locale_changes(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    response = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={
            "name": "Synthetic locale regression",
            "type": "checking",
            "balance": "100.00",
            "balance_date": "2026-09-12",
            "currency": "BRL",
        },
    )
    assert response.status_code == 201
    account = response.json()
    account_id = account["id"]

    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account_id,
            "description": "Saldo inicial",
            "amount": "50.00",
            "currency": "BRL",
            "date": "2026-09-11",
            "type": "credit",
        },
    )
    assert response.status_code == 201
    manual = response.json()
    assert manual["source"] == "manual"
    assert manual["description"] == "Saldo inicial"

    async def reread():
        # Evict identities before every SELECT: never compare a stale ORM cache.
        session.expunge_all()
        rows = (
            await session.scalars(
                select(Transaction).where(Transaction.account_id == uuid.UUID(account_id))
            )
        ).all()
        snapshot = {
            str(row.id): {
                column.key: getattr(row, column.key) for column in Transaction.__table__.columns
            }
            for row in rows
        }
        await session.rollback()
        return snapshot

    before = await reread()
    assert len(before) == 2
    assert before[manual["id"]]["source"] == "manual"
    opening_id = next(row_id for row_id in before if row_id != manual["id"])
    assert before[opening_id]["source"] == "opening_balance"
    assert before[opening_id]["amount"] == Decimal("100.00")
    assert before[opening_id]["date"] == date(2026, 9, 12)
    assert before[manual["id"]]["amount"] == Decimal("50.00")
    assert before[manual["id"]]["date"] == date(2026, 9, 11)
    assert all(row["description"] == "Saldo inicial" for row in before.values())

    locale_responses = {}
    for language in ("en", "pt-BR", "en"):
        response = await client.patch(
            "/api/users/me",
            headers=auth_headers,
            json={"preferences": {"language": language}},
        )
        assert response.status_code == 200
        assert response.json()["preferences"]["language"] == language
        response = await client.get("/api/users/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["preferences"]["language"] == language

        response = await client.get(
            "/api/transactions",
            headers=auth_headers,
            params={"account_id": account_id, "include_opening_balance": "true"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert len(payload["items"]) == 2
        wire = {row["id"]: row for row in payload["items"]}
        locale_responses[language] = payload
        assert set(wire) == set(before)
        assert wire[opening_id]["source"] == "opening_balance"
        assert wire[manual["id"]]["source"] == "manual"
        for row_id, row in wire.items():
            assert row["description"] == "Saldo inicial"
            assert row["account_id"] == account_id
            assert Decimal(str(row["amount"])) == before[row_id]["amount"]
            assert row["date"] == before[row_id]["date"].isoformat()

        # Both the omitted default and explicit false exclude only the synthetic row.
        for inclusion in ({}, {"include_opening_balance": "false"}):
            response = await client.get(
                "/api/transactions",
                headers=auth_headers,
                params={"account_id": account_id, **inclusion},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 1
            assert len(payload["items"]) == 1
            assert payload["items"][0]["id"] == manual["id"]
            assert payload["items"][0]["source"] == "manual"
            assert payload["items"][0]["description"] == "Saldo inicial"

        # Includes every persisted column, not just source/description.
        assert await reread() == before

    fixture_path = os.environ.get("SECURO_1022_WIRE_FIXTURE_OUT")
    if fixture_path:
        Path(fixture_path).write_text(
            json.dumps(
                {"account": account, "responses": locale_responses, "persisted_rows": before},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
