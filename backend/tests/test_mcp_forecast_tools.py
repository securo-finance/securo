import uuid
import pytest
import pytest_asyncio

import mcp_server.tools  # noqa: F401
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_mcp_registry_has_forecast_tools():
    assert "get_cashflow_forecast" in REGISTRY
    assert "get_recurring_subscriptions" in REGISTRY


@pytest.mark.asyncio
async def test_mcp_cashflow_forecast_handler(session, ctx):
    handler = REGISTRY["get_cashflow_forecast"].handler
    result = await handler(session=session, ctx=ctx, horizon_days=30, include_discretionary=False)
    assert isinstance(result, dict)
    assert "summary" in result
    assert result["summary"]["horizon_days"] == 30
    assert len(result["daily_trajectory"]) == 31


@pytest.mark.asyncio
async def test_mcp_recurring_subscriptions_handler(session, ctx):
    handler = REGISTRY["get_recurring_subscriptions"].handler
    result = await handler(session=session, ctx=ctx, min_occurrences=2, lookback_days=180)
    assert isinstance(result, dict)
    assert "total_detected" in result
    assert result["total_detected"] == 0
    assert result["items"] == []
