"""Parity tests for opening_balance filtering across MCP tools and REST API (#1924)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from app.main import app
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


async def _seed_workspace_with_opening_balance(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> tuple[Account, Transaction, list[Transaction]]:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name="Main Checking",
        type="checking",
        balance=Decimal("50000.00"),
        currency="EUR",
    )
    session.add(account)
    await session.flush()

    now = datetime.now(timezone.utc)
    opening_tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        description="Opening balance",
        amount=Decimal("50000.00"),
        amount_primary=Decimal("50000.00"),
        currency="EUR",
        date=date(2026, 1, 1),
        effective_date=date(2026, 1, 1),
        type="credit",
        source="opening_balance",
        status="posted",
        created_at=now,
    )
    normal_txs = [
        Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account.id,
            description="Monthly salary",
            amount=Decimal("3200.00"),
            amount_primary=Decimal("3200.00"),
            currency="EUR",
            date=date(2026, 1, 5),
            effective_date=date(2026, 1, 5),
            type="credit",
            source="manual",
            status="posted",
            created_at=now,
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account.id,
            description="Groceries",
            amount=Decimal("450.00"),
            amount_primary=Decimal("450.00"),
            currency="EUR",
            date=date(2026, 1, 10),
            effective_date=date(2026, 1, 10),
            type="debit",
            source="manual",
            status="posted",
            created_at=now,
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account.id,
            description="Scheduled bill",
            amount=Decimal("100.00"),
            amount_primary=Decimal("100.00"),
            currency="EUR",
            date=date(2026, 1, 20),
            effective_date=date(2026, 1, 20),
            type="debit",
            source="manual",
            status="pending",
            created_at=now,
        ),
    ]
    session.add_all([opening_tx, *normal_txs])
    await session.commit()
    return account, opening_tx, normal_txs


@pytest.mark.asyncio
async def test_opening_balance_excluded_by_default_and_matches_rest(
    session: AsyncSession,
    client: AsyncClient,
    test_user,
    test_workspace,
    auth_headers,
):
    account, opening_tx, normal_txs = await _seed_workspace_with_opening_balance(
        session, user_id=test_user.id, workspace_id=test_workspace.id
    )
    ctx = CallContext(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        conversation_id=uuid.uuid4(),
    )
    agg_handler = REGISTRY["aggregate"].handler
    list_handler = REGISTRY["list_transactions"].handler

    rest_default = await client.get(
        "/api/transactions",
        headers={**auth_headers, "X-Workspace-Id": str(test_workspace.id)},
    )
    assert rest_default.status_code == 200
    rest_default_body = rest_default.json()
    assert rest_default_body["total"] == len(normal_txs)
    assert all(row["source"] != "opening_balance" for row in rest_default_body["items"])

    mcp_list_default = await list_handler(session=session, ctx=ctx)
    assert mcp_list_default["total"] == rest_default_body["total"] == len(normal_txs)
    assert str(opening_tx.id) not in {item["id"] for item in mcp_list_default["items"]}

    mcp_list_explicit_false = await list_handler(
        session=session, ctx=ctx, include_opening_balance=False
    )
    assert mcp_list_explicit_false["total"] == len(normal_txs)

    # Income aggregate must NOT be inflated by the 50,000 EUR opening balance.
    income_default = await agg_handler(session=session, ctx=ctx, tx_type="income")
    assert sum(item["value"] for item in income_default["items"]) == pytest.approx(3200.0)
    assert sum(item["count"] for item in income_default["items"]) == 1

    # All-status account aggregate count must match GET /api/transactions total and MCP list_transactions total.
    by_account_default = await agg_handler(
        session=session, ctx=ctx, group_by="account", status="all"
    )
    assert len(by_account_default["items"]) == 1
    assert by_account_default["items"][0]["bucket"] == str(account.id)
    assert by_account_default["items"][0]["count"] == rest_default_body["total"] == len(normal_txs)
    assert by_account_default["items"][0]["value"] == pytest.approx(3750.0)

    count_default = await agg_handler(
        session=session, ctx=ctx, metric="count", group_by="account", status="all"
    )
    assert count_default["items"][0]["value"] == pytest.approx(float(len(normal_txs)))
    assert count_default["items"][0]["count"] == len(normal_txs)


@pytest.mark.asyncio
async def test_opening_balance_included_when_requested_and_matches_rest(
    session: AsyncSession,
    client: AsyncClient,
    test_user,
    test_workspace,
    auth_headers,
):
    account, opening_tx, normal_txs = await _seed_workspace_with_opening_balance(
        session, user_id=test_user.id, workspace_id=test_workspace.id
    )
    ctx = CallContext(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        conversation_id=uuid.uuid4(),
    )
    agg_handler = REGISTRY["aggregate"].handler
    list_handler = REGISTRY["list_transactions"].handler
    expected_total = len(normal_txs) + 1

    rest_included = await client.get(
        "/api/transactions",
        headers={**auth_headers, "X-Workspace-Id": str(test_workspace.id)},
        params={"include_opening_balance": "true"},
    )
    assert rest_included.status_code == 200
    rest_included_body = rest_included.json()
    assert rest_included_body["total"] == expected_total
    assert any(row["id"] == str(opening_tx.id) for row in rest_included_body["items"])

    mcp_list_included = await list_handler(
        session=session, ctx=ctx, include_opening_balance=True
    )
    assert mcp_list_included["total"] == rest_included_body["total"] == expected_total
    assert str(opening_tx.id) in {item["id"] for item in mcp_list_included["items"]}

    income_included = await agg_handler(
        session=session,
        ctx=ctx,
        tx_type="income",
        include_opening_balance=True,
    )
    assert sum(item["value"] for item in income_included["items"]) == pytest.approx(53200.0)
    assert sum(item["count"] for item in income_included["items"]) == 2

    by_account_included = await agg_handler(
        session=session,
        ctx=ctx,
        group_by="account",
        status="all",
        include_opening_balance=True,
    )
    assert len(by_account_included["items"]) == 1
    assert by_account_included["items"][0]["bucket"] == str(account.id)
    assert by_account_included["items"][0]["count"] == rest_included_body["total"] == expected_total
    assert by_account_included["items"][0]["value"] == pytest.approx(53750.0)


@pytest.mark.asyncio
async def test_include_opening_balance_respects_workspace_boundary(
    session: AsyncSession,
    test_user,
    test_workspace,
):
    await _seed_workspace_with_opening_balance(
        session, user_id=test_user.id, workspace_id=test_workspace.id
    )
    other_ws = Workspace(
        id=uuid.uuid4(),
        name="Other Workspace",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="EUR",
        locale="en",
    )
    session.add(other_ws)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=other_ws.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    other_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other Checking",
        type="checking",
        balance=Decimal("99999.00"),
        currency="EUR",
    )
    session.add(other_account)
    await session.flush()
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=other_ws.id,
            account_id=other_account.id,
            description="Other opening balance",
            amount=Decimal("99999.00"),
            amount_primary=Decimal("99999.00"),
            currency="EUR",
            date=date(2026, 1, 1),
            effective_date=date(2026, 1, 1),
            type="credit",
            source="opening_balance",
            status="posted",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    ctx = CallContext(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        conversation_id=uuid.uuid4(),
    )
    agg_result = await REGISTRY["aggregate"].handler(
        session=session,
        ctx=ctx,
        tx_type="income",
        include_opening_balance=True,
    )
    assert sum(item["value"] for item in agg_result["items"]) == pytest.approx(53200.0)
    assert sum(item["count"] for item in agg_result["items"]) == 2


def test_mcp_and_openapi_schemas_document_include_opening_balance():
    agg_spec = REGISTRY["aggregate"]
    agg_prop = agg_spec.parameters["properties"]["include_opening_balance"]
    assert agg_prop["type"] == "boolean"
    assert agg_prop["default"] is False
    assert "opening_balance" in agg_prop["description"]
    assert "opening_balance" in agg_spec.description
    assert "include_opening_balance" in agg_spec.description

    list_spec = REGISTRY["list_transactions"]
    list_prop = list_spec.parameters["properties"]["include_opening_balance"]
    assert list_prop["type"] == "boolean"
    assert list_prop["default"] is False
    assert "opening_balance" in list_prop["description"]
    assert "opening_balance" in list_spec.description
    assert "include_opening_balance" in list_spec.description

    openapi = app.openapi()
    params = openapi["paths"]["/api/transactions"]["get"]["parameters"]
    param = next(p for p in params if p["name"] == "include_opening_balance")
    assert "opening_balance" in param.get("description", "")
    assert "total" in param.get("description", "")
