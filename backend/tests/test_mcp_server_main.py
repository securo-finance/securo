"""Cover the MCP server's HTTP surface (mcp_server/main.py).

Strategy: drive the FastAPI app directly with httpx.ASGITransport so we
exercise auth + JSON-RPC routing without needing the agents backend
running. Tool calls go against the SQLite test DB and are limited to
read-only paths (avoid pgvector-dependent tools).
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.agents.mcp.auth import mint_token


def _client():
    # Import here so the import counts toward coverage and the test DB
    # is already configured by conftest before mcp_server.main loads.
    from mcp_server.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://mcp.test")


def _auth_headers(user_id) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id)}"}


def _external_auth_headers(user_id) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id=user_id, external=True)}"}


async def _call_tool(user_id, monkeypatch, name, arguments=None, *, external=False):
    from tests.conftest import TestSessionLocal
    import mcp_server.main as mcp_main

    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)
    headers = _external_auth_headers(user_id) if external else _auth_headers(user_id)
    async with _client() as cli:
        response = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
            headers=headers,
        )
    result = response.json()["result"]
    assert result["isError"] is False
    return result["structuredContent"]


@pytest.mark.asyncio
async def test_health_endpoint_lists_registered_tool_count(test_user):
    async with _client() as cli:
        r = await cli.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tools"] > 0


@pytest.mark.asyncio
async def test_mcp_rejects_unauthenticated():
    async with _client() as cli:
        r = await cli.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_mcp_rejects_bad_token():
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer not.a.real.token"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_mcp_rejects_malformed_body(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", content="not json", headers=_auth_headers(test_user.id))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_mcp_rejects_non_object_body(test_user):
    async with _client() as cli:
        r = await cli.post("/mcp", json=[1, 2, 3], headers=_auth_headers(test_user.id))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_mcp_rejects_wrong_jsonrpc_version(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "1.0", "id": 7, "method": "tools/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_mcp_rejects_non_string_method(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": 42},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_mcp_initialize_returns_protocol_handshake(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "securo-builtin"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_mcp_tools_list(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    # A representative sampling — these all live in mcp_server/tools/.
    assert {"list_accounts", "list_categories", "list_payees", "aggregate", "list_groups"} <= names


@pytest.mark.asyncio
async def test_mcp_tools_list_advertises_advanced_rule_management(test_user):
    async with _client() as cli:
        response = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=_auth_headers(test_user.id),
        )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert {
        "list_rules",
        "preview_rule",
        "propose_create_rule",
        "propose_update_rule",
        "propose_delete_rule",
    } <= {tool["name"] for tool in tools}


@pytest.mark.asyncio
async def test_mcp_tools_call_missing_name(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_mcp_tools_call_unknown_tool(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            },
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_unknown_method(test_user):
    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_tools_call_runs_real_tool(test_user, monkeypatch):
    """End-to-end happy path: auth → tools/call → real tool → structured
    JSON response. Uses list_categories because it has no pgvector
    dependency and is safe on SQLite.

    Routes the mcp_server's `async_session_maker` to the conftest test
    DB; otherwise CI's mcp_server points at the real Postgres which has
    no schema in the test environment and the tool raises."""
    from tests.conftest import TestSessionLocal
    import mcp_server.main as mcp_main

    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)

    async with _client() as cli:
        r = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "list_categories", "arguments": {}},
            },
            headers=_auth_headers(test_user.id),
        )
    assert r.status_code == 200
    body = r.json()
    result = body["result"]
    assert result["isError"] is False
    # `structuredContent` mirrors the tool's dict return value.
    assert "items" in result["structuredContent"]
    # `content[0].text` is JSON-encoded for clients that prefer text.
    parsed = json.loads(result["content"][0]["text"])
    assert "items" in parsed


@pytest.mark.asyncio
async def test_mcp_list_rules_returns_advanced_rule_definition(
    session, test_user, test_workspace, test_categories, monkeypatch
):
    from app.models.rule import Rule

    rule = Rule(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Rent",
        conditions_op="and",
        conditions=[
            {"field": "payee_id", "op": "equals", "value": str(test_user.id)},
            {"field": "type", "op": "equals", "value": "debit"},
        ],
        actions=[
            {"op": "set_description", "value": "Rent"},
            {"op": "set_category", "value": str(test_categories[0].id)},
        ],
        priority=7,
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    listed = await _call_tool(test_user.id, monkeypatch, "list_rules")
    assert listed == {
        "items": [
            {
                "id": str(rule.id),
                "name": "Rent",
                "conditions_op": "and",
                "conditions": rule.conditions,
                "actions": rule.actions,
                "priority": 7,
                "is_active": True,
            }
        ],
        "total": 1,
    }


@pytest.mark.asyncio
async def test_mcp_preview_rule_reports_effect_without_writing(
    session, test_user, test_transactions, monkeypatch
):
    from sqlalchemy import func, select
    from app.models.rule import Rule

    before = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    preview = await _call_tool(
        test_user.id,
        monkeypatch,
        "preview_rule",
        {
            "conditions_op": "and",
            "conditions": [
                {"field": "description", "op": "contains", "value": "UBER"},
                {"field": "type", "op": "equals", "value": "debit"},
            ],
            "actions": [{"op": "set_description", "value": "Ride"}],
            "apply_to_existing": True,
        },
    )
    assert preview["matched"] == 1
    assert preview["will_change"] == 1
    assert preview["will_apply"] is True
    assert preview["sample"][0]["description"] == "UBER TRIP"
    after = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_mcp_propose_create_rule_preserves_advanced_draft_without_writing(
    session, test_user, test_categories, monkeypatch
):
    from sqlalchemy import func, select
    from app.models.rule import Rule

    arguments = {
        "name": "Rent",
        "conditions_op": "or",
        "conditions": [
            {"field": "description", "op": "equals", "value": "Bankslip"},
            {"field": "type", "op": "equals", "value": "debit"},
        ],
        "actions": [
            {"op": "set_description", "value": "Rent"},
            {"op": "set_category", "value": str(test_categories[0].id)},
        ],
        "priority": 5,
        "apply_to_existing": False,
    }
    before = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    proposal = await _call_tool(
        test_user.id, monkeypatch, "propose_create_rule", arguments
    )
    assert proposal["kind"] == "create_rule"
    assert proposal["proposed"] == {
        **arguments,
        "is_active": True,
        "overwrite_existing_categories": False,
    }
    assert proposal["preview"]["will_apply"] is False
    after = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_mcp_external_apply_creates_rule_and_applies_it_to_history(
    session, test_user, test_transactions, monkeypatch
):
    from sqlalchemy import select
    from app.models.rule import Rule

    netflix = next(tx for tx in test_transactions if tx.description == "NETFLIX")
    applied = await _call_tool(
        test_user.id,
        monkeypatch,
        "propose_create_rule",
        {
            "name": "Normalize Netflix",
            "conditions": [
                {"field": "description", "op": "equals", "value": "NETFLIX"}
            ],
            "actions": [{"op": "set_description", "value": "Netflix subscription"}],
            "apply_to_existing": True,
            "apply": True,
        },
        external=True,
    )
    assert applied["applied"] is True
    assert applied["applied_count"] == 1
    rule = (
        await session.execute(select(Rule).where(Rule.id == uuid.UUID(applied["id"])))
    ).scalar_one()
    assert rule.actions == [{"op": "set_description", "value": "Netflix subscription"}]
    await session.refresh(netflix)
    assert netflix.description == "Netflix subscription"


@pytest.mark.asyncio
async def test_mcp_propose_update_rule_previews_changes_without_writing(
    session, test_user, test_rules, monkeypatch
):
    rule = test_rules[0]
    original_actions = list(rule.actions)
    proposal = await _call_tool(
        test_user.id,
        monkeypatch,
        "propose_update_rule",
        {
            "rule_id": str(rule.id),
            "actions": [{"op": "set_description", "value": "Ride"}],
            "apply_to_existing": False,
        },
    )
    assert proposal["kind"] == "update_rule"
    assert proposal["target"]["id"] == str(rule.id)
    assert proposal["changes"] == {
        "actions": [{"op": "set_description", "value": "Ride"}],
        "apply_to_existing": False,
    }
    await session.refresh(rule)
    assert rule.actions == original_actions


@pytest.mark.asyncio
async def test_mcp_external_apply_updates_rule_and_matching_history(
    session, test_user, test_rules, test_transactions, monkeypatch
):
    rule = test_rules[0]
    uber = next(tx for tx in test_transactions if tx.description == "UBER TRIP")
    applied = await _call_tool(
        test_user.id,
        monkeypatch,
        "propose_update_rule",
        {
            "rule_id": str(rule.id),
            "actions": [{"op": "set_description", "value": "Ride"}],
            "apply_to_existing": True,
            "apply": True,
        },
        external=True,
    )
    assert applied["applied"] is True
    assert applied["applied_count"] == 1
    await session.refresh(rule)
    await session.refresh(uber)
    assert rule.actions == [{"op": "set_description", "value": "Ride"}]
    assert uber.description == "Ride"


@pytest.mark.asyncio
async def test_mcp_propose_delete_rule_identifies_target_without_deleting(
    session, test_user, test_rules, monkeypatch
):
    from app.models.rule import Rule

    rule = test_rules[0]
    proposal = await _call_tool(
        test_user.id,
        monkeypatch,
        "propose_delete_rule",
        {"rule_id": str(rule.id)},
    )
    assert proposal["kind"] == "delete_rule"
    assert proposal["target"]["id"] == str(rule.id)
    assert await session.get(Rule, rule.id) is not None


@pytest.mark.asyncio
async def test_mcp_external_apply_deletes_rule(
    session, test_user, test_rules, monkeypatch
):
    from app.models.rule import Rule

    rule_id = test_rules[0].id
    applied = await _call_tool(
        test_user.id,
        monkeypatch,
        "propose_delete_rule",
        {"rule_id": str(rule_id), "apply": True},
        external=True,
    )
    assert applied["applied"] is True
    session.expire_all()
    assert await session.get(Rule, rule_id) is None
