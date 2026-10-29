"""MCP tools for editing goals and for editing or deleting budgets.

Exercises the handlers directly with seeded data (same pattern as
test_mcp_transfer_tools.py): preview without a write, apply only on the
external transport, workspace scoping and the service-layer validation.
The last tests drive the same tools through the /mcp JSON-RPC endpoint.
"""
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from app.agents.mcp.auth import mint_token
from app.models.budget import Budget
from app.models.goal import Goal
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    """Internal runtime context (Securo's own agent): can never apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    """External MCP token context (Claude Desktop, n8n): may apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


@pytest_asyncio.fixture
async def other_workspace(session: AsyncSession, test_user) -> Workspace:
    """A second workspace for the same user (newer, so it is NOT the default)."""
    ws = Workspace(
        id=uuid.uuid4(),
        name="Outro Espaco",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="BRL",
        locale="pt-BR",
    )
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    await session.commit()
    await session.refresh(ws)
    return ws


async def _mk_goal(
    session: AsyncSession,
    *,
    user,
    ws,
    name: str = "Viagem",
    target: str = "10000.00",
    current: str = "2000.00",
    target_date: date | None = date(2027, 6, 30),
    status: str = "active",
    tracking_type: str = "manual",
) -> Goal:
    goal = Goal(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=getattr(ws, "id", ws),
        name=name,
        target_amount=Decimal(target),
        current_amount=Decimal(current),
        currency="BRL",
        target_date=target_date,
        tracking_type=tracking_type,
        status=status,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def _mk_budget(
    session: AsyncSession,
    *,
    user,
    ws,
    category,
    amount: str = "800.00",
    month: date = date(2026, 10, 1),
    is_recurring: bool = False,
) -> Budget:
    budget = Budget(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=getattr(ws, "id", ws),
        category_id=category.id,
        amount=Decimal(amount),
        month=month,
        is_recurring=is_recurring,
        currency="BRL",
    )
    session.add(budget)
    await session.commit()
    await session.refresh(budget)
    return budget


async def _goal(session: AsyncSession, goal_id) -> Goal:
    session.expire_all()
    return (await session.execute(select(Goal).where(Goal.id == goal_id))).scalar_one()


async def _budget(session: AsyncSession, budget_id) -> Budget | None:
    session.expire_all()
    return (
        await session.execute(select(Budget).where(Budget.id == budget_id))
    ).scalar_one_or_none()


async def _budget_amount(session: AsyncSession, budget_id) -> Decimal | None:
    row = await _budget(session, budget_id)
    return row.amount if row is not None else None


def _call(name: str):
    return REGISTRY[name].handler


# --- Registry shape ---------------------------------------------------------


def test_registry_contains_goal_and_budget_tools():
    for name in ("propose_update_goal", "propose_update_budget", "propose_delete_budget"):
        assert name in REGISTRY, f"missing tool: {name}"
        assert REGISTRY[name].is_proposal is True
        assert "apply" in REGISTRY[name].parameters["properties"]


def test_tool_schemas_reach_only_their_fields():
    """The goal tool cannot relink tracking; the budget tool only moves the amount."""
    goal_props = set(REGISTRY["propose_update_goal"].parameters["properties"])
    assert goal_props == {
        "goal_id", "name", "target_amount", "current_amount", "target_date", "status", "apply",
    }
    assert REGISTRY["propose_update_goal"].parameters["properties"]["status"]["enum"] == [
        "active", "completed", "paused", "archived",
    ]
    assert set(REGISTRY["propose_update_budget"].parameters["properties"]) == {
        "budget_id", "amount", "apply",
    }
    assert set(REGISTRY["propose_delete_budget"].parameters["properties"]) == {
        "budget_id", "apply",
    }


# --- propose_update_goal ----------------------------------------------------


async def test_goal_preview_shows_current_values_and_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ctx, goal_id=str(goal.id),
        current_amount=3500, target_date="2027-12-31", status="paused",
    )

    assert result["kind"] == "update_goal"
    assert result["target"] == {
        "id": str(goal.id),
        "name": "Viagem",
        "target_amount": 10000.0,
        "current_amount": 2000.0,
        "currency": "BRL",
        "target_date": "2027-06-30",
        "status": "active",
        "tracking_type": "manual",
    }
    assert result["changes"] == {
        "current_amount": 3500.0,
        "target_date": "2027-12-31",
        "status": "paused",
    }
    assert result["apply_endpoint"] == f"PATCH /api/goals/{goal.id}"
    assert "applied" not in result
    row = await _goal(session, goal.id)
    assert (row.current_amount, row.status) == (Decimal("2000.00"), "active")


async def test_goal_changes_leave_out_values_already_stored(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ctx, goal_id=str(goal.id),
        name="  Viagem  ", target_amount=12000, status="active",
    )

    assert result["changes"] == {"target_amount": 12000.0}


async def test_goal_empty_target_date_removes_the_deadline(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(goal.id), target_date="", apply=True
    )

    assert result["changes"] == {"target_date": None}
    assert result.get("applied") is True, result
    assert (await _goal(session, goal.id)).target_date is None


async def test_goal_no_changes_and_nothing_to_change(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    none_given = await _call("propose_update_goal")(
        session=session, ctx=ctx, goal_id=str(goal.id)
    )
    same_values = await _call("propose_update_goal")(
        session=session, ctx=ctx, goal_id=str(goal.id), current_amount=2000, status="active"
    )

    assert none_given == {"error": "no changes provided"}
    assert "nothing to change" in same_values["error"]


async def test_goal_invalid_status_is_refused_by_the_schema(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(goal.id), status="done", apply=True
    )

    assert "applied" not in result
    assert "status must be active, completed, paused, or archived" in result["error"]
    assert (await _goal(session, goal.id)).status == "active"


async def test_goal_invalid_values_are_refused(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)
    handler = _call("propose_update_goal")

    blank_name = await handler(session=session, ctx=ctx, goal_id=str(goal.id), name="   ")
    zero_target = await handler(session=session, ctx=ctx, goal_id=str(goal.id), target_amount=0)
    negative = await handler(session=session, ctx=ctx, goal_id=str(goal.id), current_amount=-5)
    bad_date = await handler(session=session, ctx=ctx, goal_id=str(goal.id), target_date="31/12/2027")

    assert blank_name == {"error": "name cannot be empty"}
    assert zero_target == {"error": "target_amount must be greater than zero"}
    assert negative == {"error": "current_amount cannot be negative"}
    assert "invalid target_date" in bad_date["error"]


async def test_goal_tracking_an_account_refuses_a_manual_amount(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(
        session, user=test_user, ws=test_workspace, tracking_type="net_worth"
    )

    result = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(goal.id), current_amount=5000, apply=True
    )

    assert result == {"error": "this goal tracks its net worth, so its progress cannot be set by hand"}
    assert (await _goal(session, goal.id)).current_amount == Decimal("2000.00")


async def test_goal_unknown_and_cross_workspace(
    session: AsyncSession, ext_ctx: CallContext, test_user, other_workspace
):
    foreign = await _mk_goal(session, user=test_user, ws=other_workspace, name="Carro")

    unknown = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(uuid.uuid4()), name="x"
    )
    cross = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(foreign.id), status="archived", apply=True
    )

    assert unknown == {"error": "goal not found"}
    assert cross == {"error": "goal not found"}
    assert (await _goal(session, foreign.id)).status == "active"


async def test_goal_internal_apply_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ctx, goal_id=str(goal.id), status="completed", apply=True
    )

    assert "applied" not in result
    assert (await _goal(session, goal.id)).status == "active"


async def test_goal_external_apply_changes_only_the_requested_fields(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _call("propose_update_goal")(
        session=session, ctx=ext_ctx, goal_id=str(goal.id),
        name="Viagem ao Japão", current_amount=4250.5, status="paused", apply=True,
    )

    assert result.get("applied") is True, result
    assert result["id"] == str(goal.id)
    row = await _goal(session, goal.id)
    assert row.name == "Viagem ao Japão"
    assert row.current_amount == Decimal("4250.50")
    assert row.status == "paused"
    assert row.target_amount == Decimal("10000.00")
    assert row.target_date == date(2027, 6, 30)
    assert row.tracking_type == "manual"


# --- propose_update_budget --------------------------------------------------


async def test_budget_update_preview_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0], is_recurring=True
    )

    result = await _call("propose_update_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id), amount=1200
    )

    assert result["kind"] == "update_budget"
    assert result["target"] == {
        "id": str(budget.id),
        "category_id": str(test_categories[0].id),
        "category_name": "Alimentação",
        "month": "2026-10-01",
        "amount": 800.0,
        "currency": "BRL",
        "is_recurring": True,
    }
    assert result["changes"] == {"amount": 1200.0}
    assert result["apply_endpoint"] == f"PATCH /api/budgets/{budget.id}"
    assert "applied" not in result
    assert await _budget_amount(session, budget.id) == Decimal("800.00")


async def test_budget_update_same_or_invalid_amount(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )

    same = await _call("propose_update_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id), amount=800
    )
    zero = await _call("propose_update_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id), amount=0
    )

    assert "nothing to change" in same["error"]
    assert zero == {"error": "amount must be greater than zero"}


async def test_budget_update_internal_apply_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )

    result = await _call("propose_update_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id), amount=950, apply=True
    )

    assert "applied" not in result
    assert await _budget_amount(session, budget.id) == Decimal("800.00")


async def test_budget_update_external_apply_changes_only_the_amount(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[1], is_recurring=True
    )
    category_id = test_categories[1].id

    result = await _call("propose_update_budget")(
        session=session, ctx=ext_ctx, budget_id=str(budget.id), amount=650.25, apply=True
    )

    assert result.get("applied") is True, result
    assert result["id"] == str(budget.id)
    row = await _budget(session, budget.id)
    assert row is not None
    assert row.amount == Decimal("650.25")
    assert (row.category_id, row.month, row.is_recurring) == (
        category_id, date(2026, 10, 1), True,
    )


async def test_budget_update_unknown_and_cross_workspace(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_categories, other_workspace
):
    foreign = await _mk_budget(
        session, user=test_user, ws=other_workspace, category=test_categories[0]
    )

    unknown = await _call("propose_update_budget")(
        session=session, ctx=ext_ctx, budget_id=str(uuid.uuid4()), amount=10
    )
    cross = await _call("propose_update_budget")(
        session=session, ctx=ext_ctx, budget_id=str(foreign.id), amount=10, apply=True
    )

    assert unknown == {"error": "budget not found"}
    assert cross == {"error": "budget not found"}
    assert await _budget_amount(session, foreign.id) == Decimal("800.00")


# --- propose_delete_budget --------------------------------------------------


async def test_budget_delete_preview_does_not_delete(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )

    result = await _call("propose_delete_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id)
    )

    assert result["kind"] == "delete_budget"
    assert result["target"]["id"] == str(budget.id)
    assert result["target"]["category_name"] == "Alimentação"
    assert result["target"]["is_recurring"] is False
    assert result["apply_endpoint"] == f"DELETE /api/budgets/{budget.id}"
    assert "applied" not in result
    assert await _budget(session, budget.id) is not None


async def test_budget_delete_internal_apply_does_not_delete(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace, test_categories
):
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )

    result = await _call("propose_delete_budget")(
        session=session, ctx=ctx, budget_id=str(budget.id), apply=True
    )

    assert "applied" not in result
    assert await _budget(session, budget.id) is not None


async def test_budget_delete_external_apply_deletes_only_that_budget(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace, test_categories
):
    doomed = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )
    kept = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[1]
    )
    doomed_id, kept_id = doomed.id, kept.id

    result = await _call("propose_delete_budget")(
        session=session, ctx=ext_ctx, budget_id=str(doomed_id), apply=True
    )

    assert result.get("applied") is True, result
    assert result["deleted"] is True
    assert await _budget(session, doomed_id) is None
    assert await _budget(session, kept_id) is not None


async def test_budget_delete_unknown_and_cross_workspace(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_categories, other_workspace
):
    foreign = await _mk_budget(
        session, user=test_user, ws=other_workspace, category=test_categories[0]
    )

    unknown = await _call("propose_delete_budget")(
        session=session, ctx=ext_ctx, budget_id=str(uuid.uuid4()), apply=True
    )
    cross = await _call("propose_delete_budget")(
        session=session, ctx=ext_ctx, budget_id=str(foreign.id), apply=True
    )

    assert unknown == {"error": "budget not found"}
    assert cross == {"error": "budget not found"}
    assert await _budget(session, foreign.id) is not None


# --- Through the /mcp endpoint ----------------------------------------------


async def _mcp_call(user_id, monkeypatch, name, arguments, *, external=False):
    """Call a tool over JSON-RPC with a minted token, as an MCP client does."""
    import mcp_server.main as mcp_main
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)
    token = mint_token(user_id=user_id, external=external)
    transport = httpx.ASGITransport(app=mcp_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as cli:
        response = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def test_mcp_endpoint_internal_token_previews_goal_update(
    session: AsyncSession, test_user, test_workspace, monkeypatch
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)

    result = await _mcp_call(
        test_user.id, monkeypatch, "propose_update_goal",
        {"goal_id": str(goal.id), "current_amount": 3000, "apply": True},
    )

    assert result["changes"] == {"current_amount": 3000.0}
    assert "applied" not in result
    assert (await _goal(session, goal.id)).current_amount == Decimal("2000.00")


async def test_mcp_endpoint_external_token_applies_after_preview(
    session: AsyncSession, test_user, test_workspace, test_categories, monkeypatch
):
    goal = await _mk_goal(session, user=test_user, ws=test_workspace)
    budget = await _mk_budget(
        session, user=test_user, ws=test_workspace, category=test_categories[0]
    )
    user_id, goal_id, budget_id = test_user.id, goal.id, budget.id

    preview = await _mcp_call(
        user_id, monkeypatch, "propose_update_budget",
        {"budget_id": str(budget_id), "amount": 1000}, external=True,
    )
    assert "applied" not in preview
    assert await _budget_amount(session, budget_id) == Decimal("800.00")

    updated = await _mcp_call(
        user_id, monkeypatch, "propose_update_budget",
        {"budget_id": str(budget_id), "amount": 1000, "apply": True}, external=True,
    )
    goal_done = await _mcp_call(
        user_id, monkeypatch, "propose_update_goal",
        {"goal_id": str(goal_id), "status": "completed", "apply": True}, external=True,
    )
    deleted = await _mcp_call(
        user_id, monkeypatch, "propose_delete_budget",
        {"budget_id": str(budget_id), "apply": True}, external=True,
    )

    assert updated["applied"] is True
    assert goal_done["applied"] is True
    assert deleted["applied"] is True
    assert (await _goal(session, goal_id)).status == "completed"
    assert await _budget(session, budget_id) is None
