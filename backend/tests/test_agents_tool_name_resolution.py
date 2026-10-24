"""Tools resolve category names and explain their own validation errors.

In real conversations the model frequently has a category *name* but not
its id (it was in the user's sentence, not in a tool result), and the
old tools answered "category not found" — an error that gave the model
nothing to retry with. These tests cover the new self-correcting paths.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from mcp_server.tools._helpers import resolve_categories

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, workspace_id=None, conversation_id=None, agent_id=None)


async def _ws(session, ctx):
    from mcp_server.tools._helpers import resolve_workspace_id

    return await resolve_workspace_id(session, ctx)


async def test_resolve_by_exact_name_is_case_insensitive(session: AsyncSession, ctx, test_categories):
    ws = await _ws(session, ctx)
    ids, err = await resolve_categories(session, ws, names=["transporte"])
    assert err is None
    assert ids == [next(c.id for c in test_categories if c.name == "Transporte")]


async def test_resolve_accepts_a_name_in_the_id_slot(session: AsyncSession, ctx, test_categories):
    ws = await _ws(session, ctx)
    ids, err = await resolve_categories(session, ws, ids=["Receita"])
    assert err is None and len(ids) == 1


async def test_resolve_unique_substring(session: AsyncSession, ctx, test_categories):
    ws = await _ws(session, ctx)
    ids, err = await resolve_categories(session, ws, names=["aliment"])
    assert err is None and len(ids) == 1


async def test_resolve_unknown_name_suggests_alternatives(session: AsyncSession, ctx, test_categories):
    ws = await _ws(session, ctx)
    ids, err = await resolve_categories(session, ws, names=["Transporet"])  # typo
    assert ids == []
    assert err["error"].startswith("category not found")
    assert [s["name"] for s in err["did_you_mean"]] == ["Transporte"]
    assert uuid.UUID(err["did_you_mean"][0]["id"])


async def test_aggregate_by_category_name_and_uncategorized(session: AsyncSession, ctx, test_transactions, test_categories):
    handler = REGISTRY["aggregate"].handler
    by_name = await handler(session=session, ctx=ctx, category_names=["Transporte"], status="all")
    assert "error" not in by_name
    unknown = await handler(session=session, ctx=ctx, category_names=["Nope"], status="all")
    assert unknown["error"].startswith("category not found")
    uncategorized = await handler(session=session, ctx=ctx, uncategorized=True, status="all")
    assert "error" not in uncategorized
    for row in uncategorized["items"]:
        assert row["bucket"] in (None, "")  # only the null-category bucket


async def test_aggregate_hints_when_text_search_names_a_category(session: AsyncSession, ctx, test_transactions, test_categories):
    handler = REGISTRY["aggregate"].handler
    r = await handler(session=session, ctx=ctx, description_contains="Transporte", status="all")
    assert r["items"] == []
    assert "category_names" in r["hint"]


async def test_list_transactions_by_category_name(session: AsyncSession, ctx, test_transactions, test_categories):
    handler = REGISTRY["list_transactions"].handler
    ok = await handler(session=session, ctx=ctx, category_names=["Receita"])
    assert "error" not in ok
    bad = await handler(session=session, ctx=ctx, category_names=["Recieta"])
    assert bad["error"].startswith("category not found")
    assert bad["did_you_mean"][0]["name"] == "Receita"


async def test_propose_categorize_accepts_a_name(session: AsyncSession, ctx, test_transactions, test_categories):
    handler = REGISTRY["propose_categorize"].handler
    tx_id = str(test_transactions[0].id)
    r = await handler(session=session, ctx=ctx, transaction_ids=[tx_id], category_name="transporte")
    assert "error" not in r, r
    missing = await handler(session=session, ctx=ctx, transaction_ids=[tx_id], category_name="Transporet")
    assert missing["did_you_mean"][0]["name"] == "Transporte"
    neither = await handler(session=session, ctx=ctx, transaction_ids=[tx_id])
    assert neither["error"] == "pass category_id or category_name"
    assert "category_id" not in REGISTRY["propose_categorize"].parameters["required"]


async def test_recurring_derives_day_of_month_from_start_date(session: AsyncSession, ctx, test_account):
    handler = REGISTRY["propose_create_recurring_transaction"].handler
    r = await handler(
        session=session, ctx=ctx, description="Netflix", amount=55, type="debit", frequency="monthly",
        account_id=str(test_account.id), start_date="2026-03-10",
    )
    assert "error" not in r, r
    assert r["proposed"]["day_of_month"] == 10
    bare = await handler(
        session=session, ctx=ctx, description="Netflix", amount=55, type="debit", frequency="monthly",
        account_id=str(test_account.id),
    )
    assert bare["error"].startswith("day_of_month is required")
    assert "start_date" in bare["hint"]


async def test_invalid_rule_condition_returns_the_vocabulary(session: AsyncSession, ctx, test_categories):
    handler = REGISTRY["propose_create_rule"].handler
    r = await handler(
        session=session, ctx=ctx, name="Bad",
        conditions=[{"field": "merchant", "op": "contains", "value": "x"}],
        actions=[{"op": "set_category", "value": str(test_categories[0].id)}],
    )
    assert r["error"] == "Invalid rule condition"
    malformed = await handler(session=session, ctx=ctx, name="Bad", conditions=[{"field": "payee"}], actions=[{"value": "x"}])
    assert "allowed" in malformed and malformed["error"]
    assert "merchant" not in r["allowed"]["fields"]
    assert "payee" in r["allowed"]["fields"] and "contains" in r["allowed"]["ops"]
    assert "op is one of contains" in REGISTRY["propose_create_rule"].description


async def test_aggregate_reports_a_grand_total_and_skips_opening_balances(session: AsyncSession, ctx, test_transactions, test_account):
    from datetime import date

    from app.models.transaction import Transaction

    session.add(Transaction(
        id=uuid.uuid4(), user_id=ctx.user_id, workspace_id=test_account.workspace_id, account_id=test_account.id,
        description="Opening balance", amount=1_000_000, date=date(2026, 1, 1), type="debit", source="opening_balance",
        currency="BRL", status="posted",
    ))
    await session.commit()
    handler = REGISTRY["aggregate"].handler
    r = await handler(session=session, ctx=ctx, status="all", uncategorized=True)
    assert "error" not in r
    assert r["grand_total"] < 1_000_000
    assert r["grand_count"] == sum(i["count"] for i in r["items"])
