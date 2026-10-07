"""The calculator does the model's arithmetic — and nothing else."""
from __future__ import annotations

import pytest

import mcp_server.tools  # noqa: F401
from app.agents.runtime.executor import _RUNTIME_GUARDRAIL
from mcp_server.registry import REGISTRY
from mcp_server.tools.calculate import evaluate

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("14716 * 12", 176592),
        ("$14,716 × 12", 176592),
        ("4383.75 + 4383.75 + 1306.20", 10073.70),
        ("(4383.75 + 4383.75) / 5948.14 * 100", 147.40),
        ("round(1,234.5 + 1,000, 2)", 2234.5),
        ("15% of 200", 30),
        ("round(17844.38 / 3, 2)", 5948.13),
        ("sum([1, 2, 3.5])", 6.5),
        ("-(2 ** 10)", -1024),
        ("max(3, 9, 4) - min(1, 2)", 8),
    ],
)
def test_evaluate(expr, expected):
    assert round(evaluate(expr), 2) == round(expected, 2)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__",
        "a + 1",
        "2 ** 99999",
        "1 / 0",
        "",
        "x" * 600,
        "[1,2][0]",
        "lambda: 1",
    ],
)
def test_evaluate_rejects_anything_but_arithmetic(expr):
    with pytest.raises(ValueError):
        evaluate(expr)


async def test_tool_returns_result_and_formatted(session, test_user):
    from mcp_server.auth import CallContext

    ctx = CallContext(user_id=test_user.id, workspace_id=None, conversation_id=None, agent_id=None)
    handler = REGISTRY["calculate"].handler
    r = await handler(session=session, ctx=ctx, expression="$14,716 * 12")
    assert r["result"] == 176592 and r["formatted"] == "176,592"
    bad = await handler(session=session, ctx=ctx, expression="salary * 12")
    assert bad["error"].startswith("invalid expression") and "hint" in bad


def test_guardrail_routes_arithmetic_through_the_tool():
    assert "`calculate`" in _RUNTIME_GUARDRAIL
    assert "do not volunteer annualised" in _RUNTIME_GUARDRAIL
