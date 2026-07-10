"""Cover mcp_server/tools/groups.py — the three group/balance/settlement
tools exposed to the agent. Service-layer behaviour is already covered
in tests/test_groups.py; here we focus on the MCP serialization layer:
name resolution, error pass-through, decimal coercion, etc.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import mcp_server.tools.groups as groups_tool
from mcp_server.auth import CallContext


def _ctx(user_id):
    return CallContext(user_id=user_id)


# --------------------------------------------------------------------- list_groups


@pytest.mark.asyncio
async def test_list_groups_serializes_group_with_members(session, test_user, monkeypatch):
    gid = uuid.uuid4()
    mid1 = uuid.uuid4()
    mid2 = uuid.uuid4()
    fake_group = SimpleNamespace(
        id=gid,
        name="Amigos",
        kind="expense",
        default_currency="BRL",
        is_archived=False,
        members=[
            SimpleNamespace(id=mid1, name="Tássio", is_self=True),
            SimpleNamespace(id=mid2, name="Bia", is_self=False),
        ],
    )

    async def fake_list_groups(s, ws_id, uid, *, include_archived):
        assert uid == test_user.id
        assert include_archived is True
        return [fake_group]

    monkeypatch.setattr(groups_tool.group_service, "list_groups", fake_list_groups)

    result = await groups_tool.list_groups(
        session=session, ctx=_ctx(test_user.id), include_archived=True
    )
    assert result["total"] == 1
    g = result["items"][0]
    assert g["id"] == str(gid)
    assert g["name"] == "Amigos"
    assert g["kind"] == "expense"
    assert g["default_currency"] == "BRL"
    assert g["is_archived"] is False
    assert len(g["members"]) == 2
    assert {m["name"] for m in g["members"]} == {"Tássio", "Bia"}
    assert next(m for m in g["members"] if m["name"] == "Tássio")["is_self"] is True


@pytest.mark.asyncio
async def test_list_groups_handles_missing_members(session, test_user, monkeypatch):
    """If a group has no members (or None), the tool must not crash."""
    fake_group = SimpleNamespace(
        id=uuid.uuid4(),
        name="Solo",
        kind="expense",
        default_currency="USD",
        is_archived=True,
        members=None,
    )

    async def fake(s, ws_id, uid, *, include_archived):
        return [fake_group]

    monkeypatch.setattr(groups_tool.group_service, "list_groups", fake)
    result = await groups_tool.list_groups(session=session, ctx=_ctx(test_user.id))
    assert result["items"][0]["members"] == []


@pytest.mark.asyncio
async def test_list_groups_defaults_include_archived_false(session, test_user, monkeypatch):
    captured = {}

    async def fake(s, ws_id, uid, *, include_archived):
        captured["include_archived"] = include_archived
        return []

    monkeypatch.setattr(groups_tool.group_service, "list_groups", fake)
    await groups_tool.list_groups(session=session, ctx=_ctx(test_user.id))
    assert captured["include_archived"] is False


# --------------------------------------------------------------------- get_group_balances


@pytest.mark.asyncio
async def test_get_group_balances_returns_error_when_group_missing(session, test_user, monkeypatch):
    async def fake_compute(s, gid, ws_id, uid):
        return None  # service signals not-visible/not-found via None

    monkeypatch.setattr(groups_tool.balance_service, "compute_balances", fake_compute)

    result = await groups_tool.get_group_balances(
        session=session, ctx=_ctx(test_user.id), group_id=str(uuid.uuid4())
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_get_group_balances_resolves_member_names(session, test_user, monkeypatch):
    """Happy path: compute_balances returns raw lines; the tool resolves
    member names and is_self flags from the GroupMember table."""
    from app.models.group import Group, GroupMember

    g = Group(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Roomies",
        kind="expense",
        default_currency="USD",
        is_archived=False,
    )
    me = GroupMember(id=uuid.uuid4(), group_id=g.id, name="Me", is_self=True)
    them = GroupMember(id=uuid.uuid4(), group_id=g.id, name="Alex", is_self=False)
    session.add_all([g, me, them])
    await session.commit()

    async def fake_compute(s, gid, ws_id, uid):
        assert gid == g.id
        return {
            "group_id": g.id,
            "self_member_id": me.id,
            "default_currency": "USD",
            "lines": [
                {
                    "member_id": them.id,
                    "currency": "USD",
                    "amount": Decimal("12.50"),
                    "amount_in_default_currency": Decimal("12.50"),
                },
            ],
        }

    monkeypatch.setattr(groups_tool.balance_service, "compute_balances", fake_compute)

    result = await groups_tool.get_group_balances(
        session=session, ctx=_ctx(test_user.id), group_id=str(g.id)
    )
    assert result["group_id"] == str(g.id)
    assert result["self_member_id"] == str(me.id)
    assert result["default_currency"] == "USD"
    assert len(result["lines"]) == 1
    ln = result["lines"][0]
    assert ln["member_id"] == str(them.id)
    assert ln["member_name"] == "Alex"
    assert ln["is_self"] is False
    assert ln["amount"] == 12.5
    assert ln["amount_in_default_currency"] == 12.5


@pytest.mark.asyncio
async def test_get_group_balances_handles_none_self_member(session, test_user, monkeypatch):
    """If the user isn't a member, self_member_id comes back None — we
    must serialize that as null, not crash."""
    gid = uuid.uuid4()

    async def fake_compute(s, g, ws_id, u):
        return {
            "group_id": gid,
            "self_member_id": None,
            "default_currency": "BRL",
            "lines": [],
        }

    monkeypatch.setattr(groups_tool.balance_service, "compute_balances", fake_compute)
    result = await groups_tool.get_group_balances(
        session=session, ctx=_ctx(test_user.id), group_id=str(gid)
    )
    assert result["self_member_id"] is None


# --------------------------------------------------------------------- list_group_settlements


@pytest.mark.asyncio
async def test_list_group_settlements_returns_error_when_group_missing(
    session, test_user, monkeypatch
):
    async def fake_list(s, gid, ws_id, uid):
        return None

    monkeypatch.setattr(groups_tool.settlement_service, "list_settlements", fake_list)

    result = await groups_tool.list_group_settlements(
        session=session, ctx=_ctx(test_user.id), group_id=str(uuid.uuid4())
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_list_group_settlements_serializes_rows(session, test_user, monkeypatch):
    s1 = SimpleNamespace(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        from_member_id=uuid.uuid4(),
        to_member_id=uuid.uuid4(),
        amount=Decimal("99.95"),
        currency="EUR",
        date=date(2026, 5, 1),
        notes="brunch",
        transaction_id=uuid.uuid4(),
    )
    s2 = SimpleNamespace(
        id=uuid.uuid4(),
        group_id=s1.group_id,
        from_member_id=uuid.uuid4(),
        to_member_id=uuid.uuid4(),
        amount=Decimal("10.00"),
        currency="EUR",
        date=None,  # exercise the None-date branch
        notes=None,
        transaction_id=None,  # exercise the None-txn branch
    )

    async def fake(s, gid, ws_id, uid):
        return [s1, s2]

    monkeypatch.setattr(groups_tool.settlement_service, "list_settlements", fake)

    result = await groups_tool.list_group_settlements(
        session=session, ctx=_ctx(test_user.id), group_id=str(s1.group_id)
    )
    assert result["total"] == 2
    first, second = result["items"]
    assert first["id"] == str(s1.id)
    assert first["amount"] == 99.95
    assert first["currency"] == "EUR"
    assert first["date"] == "2026-05-01"
    assert first["notes"] == "brunch"
    assert first["transaction_id"] == str(s1.transaction_id)
    assert second["date"] is None
    assert second["transaction_id"] is None
    assert second["amount"] == 10.0
