"""Replayed history fits a token budget and compacts stale tool payloads.

Live transcripts on a local 27B model averaged ~32k prompt tokens per call
because every past tool result was replayed verbatim; past ~40 rows the
newest question was buried and the agent re-answered old ones. The replay
now keeps the newest turn whole, compacts older tool results with a visible
marker, drops rows beyond a token budget, and still opens on a user message.
"""
from __future__ import annotations

import pytest

from app.agents.runtime.executor import _replay_history

pytestmark = pytest.mark.asyncio


class _Row:
    def __init__(self, role, content=None, tool_calls=None, tool_result=None):
        self.role, self.content, self.tool_calls, self.tool_result = role, content, tool_calls, tool_result


def _turn(i, payload_chars):
    return [
        _Row("user", f"q{i}"),
        _Row("assistant", tool_calls=[{"id": f"c{i}", "name": "securo__list_transactions", "arguments": {"page": i}}]),
        _Row("tool", tool_result={"tool_call_id": f"c{i}", "data": {"items": "x" * payload_chars}}),
        _Row("assistant", f"a{i}"),
    ]


def test_newest_turn_is_full_and_older_tool_results_are_compacted():
    rows = _turn(0, 5000) + _turn(1, 5000)
    out = _replay_history(rows, token_budget=100_000, compact_chars=200)
    tools = [m for m in out if m.role == "tool"]
    assert len(tools) == 2
    assert len(tools[0].content) < 400 and "compacted to 200 chars" in tools[0].content
    assert len(tools[1].content) > 5000 and "compacted" not in tools[1].content
    assert [m.content for m in out if m.role == "user"] == ["q0", "q1"]


def test_budget_drops_oldest_rows_and_opens_on_a_user_message():
    rows = _turn(0, 2000) + _turn(1, 2000) + _turn(2, 2000)
    # Each full turn costs ~(2000/4 + overhead) tokens; allow roughly one turn.
    out = _replay_history(rows, token_budget=700, compact_chars=0)
    assert out[0].role == "user" and out[0].content == "q2"
    assert out[-1].content == "a2"
    assert all(m.content != "q0" for m in out)


def test_budget_never_drops_the_newest_row():
    rows = [_Row("user", "x" * 10_000)]
    out = _replay_history(rows, token_budget=1, compact_chars=0)
    assert len(out) == 1 and out[0].role == "user"


def test_compaction_disabled_with_zero():
    rows = _turn(0, 3000) + _turn(1, 10)
    out = _replay_history(rows, token_budget=100_000, compact_chars=0)
    assert all("compacted" not in (m.content or "") for m in out)


def test_orphaned_tool_results_are_never_replayed_first():
    rows = _turn(0, 10)
    # Cut mid-turn: start at the tool row.
    out = _replay_history(rows[2:], token_budget=100_000, compact_chars=0)
    assert out == [] or out[0].role == "user"
