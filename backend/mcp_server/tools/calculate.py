"""A calculator the model must use for any derived number.

Small models get tool numbers right and then multiply or add them wrong
while writing the sentence ("$14,716/month … about $63.6k/year"). The
runtime guardrail routes every sum, product, percentage or annualisation
through this tool so the arithmetic is done by Python, not by the model.

The expression is parsed with ``ast`` and only arithmetic nodes are
evaluated — no names, calls, attributes or subscripts beyond a tiny
whitelist of functions — so untrusted model output can never reach
``eval``.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.auth import CallContext
from mcp_server.registry import tool

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
}
_MAX_EXPR_LEN = 500
_MAX_ABS = 1e15


class _Unsafe(ValueError):
    pass


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 16:
            raise _Unsafe("exponent too large")
        return float(_BIN_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return float(_UNARY_OPS[type(node.op)](_eval(node.operand)))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        args = [_eval(a) for a in node.args]
        if node.func.id == "round" and len(args) == 2:
            args[1] = int(args[1])  # ndigits must be an int; everything else here is a float
        return float(_FUNCS[node.func.id](*args))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e) for e in node.elts]  # type: ignore[return-value]  (only meaningful inside sum/min/max)
    raise _Unsafe(f"unsupported syntax: {type(node).__name__}")


def evaluate(expression: str) -> float:
    """Evaluate an arithmetic expression safely. Raises ValueError on anything else."""
    text = (expression or "").strip()
    if not text:
        raise ValueError("empty expression")
    if len(text) > _MAX_EXPR_LEN:
        raise ValueError("expression too long")
    # Be forgiving about how models write money: "$1,234.50 * 12", "15 % of 200".
    text = text.replace("$", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)  # thousands separators only; keeps f(a, b)
    text = text.replace("% of ", "/100*").replace("%", "/100")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"could not parse: {exc.msg}") from exc
    try:
        value = _eval(tree)
    except ZeroDivisionError as exc:
        raise ValueError("division by zero") from exc
    except (_Unsafe, TypeError, OverflowError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(value, float) or math.isnan(value) or math.isinf(value) or abs(value) > _MAX_ABS:
        raise ValueError("result is not a finite number")
    return value


def _format(value: float) -> str:
    try:
        d = Decimal(repr(value))
    except InvalidOperation:
        return str(value)
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{float(d):,.2f}"


@tool(
    name="calculate",
    description=(
        "Evaluate an arithmetic expression exactly and return the result. Use it "
        "for EVERY derived number — adding two tool totals, multiplying a monthly "
        "amount by 12, a percentage of a total, an average, a difference — and "
        "quote the returned `result` instead of computing in your head. Supports "
        "+ - * / % ** parentheses, and abs/round/min/max/sum/sqrt. Money "
        "formatting is tolerated: '$4,383.75 * 2 + 1306.20' works. Examples: "
        "'14716 * 12', '(4383.75 + 4383.75) / 5948.14 * 100', 'round(17844.38 / 3, 2)'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "The arithmetic to evaluate, e.g. '5948.14 + 8767.50'."},
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
    tags=["read", "math"],
)
async def calculate(
    *,
    session: AsyncSession,
    ctx: CallContext,
    expression: str,
) -> dict[str, Any]:
    try:
        value = evaluate(expression)
    except ValueError as exc:
        return {
            "error": f"invalid expression: {exc}",
            "hint": "Use plain arithmetic like '4383.75 * 2 + 1306.2'. No variables or words.",
        }
    return {"expression": expression, "result": round(value, 6), "formatted": _format(round(value, 2))}
