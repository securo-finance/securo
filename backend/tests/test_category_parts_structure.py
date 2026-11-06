"""Aggregations read category parts, mechanically checked (#425).

A transaction split across categories keeps one row; its parts live in
`transaction_category_lines`. `counts_as_pnl` and its siblings take the
category to judge as a required argument, so every caller has to choose:

- `line_category_id()` (or a variable holding it): the query must also join
  the parts with `with_category_lines`, or the alias would be pulled in as
  a cross join and every row multiplied;
- `Transaction.category_id`: judges the whole row. Only right where a row
  cannot be split, so each such call site is listed below with its reason.

Adding a new aggregation that judges whole rows means adding it here and
saying why, which is the point: the default is to read the parts.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILTERS = {"counts_as_pnl", "counts_as_user_pnl", "is_transfer", "is_not_transfer"}

#: (file, enclosing function) -> why judging the whole row is right there.
WHOLE_ROW: dict[tuple[str, str], str] = {
    ("app/services/_query_filters.py", "is_transfer_row"):
        "list filter; split rows are matched through any_line_in",
    ("app/services/_query_filters.py", "counts_as_user_pnl_row"):
        "list filter; split rows are matched through their lines",
    ("app/services/_query_filters.py", "owner_split_offset_pnl"):
        "group-split shares; a group-split row cannot be split by category",
    ("app/services/_query_filters.py", "owner_split_offset_by_category"):
        "group-split shares; a group-split row cannot be split by category",
    ("app/services/_query_filters.py", "viewer_shared_pnl"):
        "group-split shares; a group-split row cannot be split by category",
    ("app/services/_query_filters.py", "viewer_shared_spending_by_category"):
        "group-split shares; a group-split row cannot be split by category",
    ("app/services/dashboard_service.py", "get_summary"):
        "pending-categorization count (split rows excluded) and viewer shares",
    ("app/services/report_service.py", "get_income_expenses_report"):
        "owner and viewer group-split offsets",
}


def _sources():
    for folder in ("app", "mcp_server"):
        yield from sorted((ROOT / folder).rglob("*.py"))


def _calls(tree):
    """(enclosing function, call node) for every call to a filter."""
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in FILTERS:
                yield func, node


def _calls_name(func, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == name
        for node in ast.walk(func)
    )


def _is_whole_row(arg) -> bool:
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "category_id"
        and getattr(arg.value, "id", None) == "Transaction"
    )


def test_every_filter_call_reads_parts_or_says_why_not():
    whole_row_seen: set[tuple[str, str]] = set()
    problems: list[str] = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text())
        for func, call in _calls(tree):
            where = f"{rel}:{call.lineno} in {func.name}"
            if not call.args:
                problems.append(f"{where}: no category given")
                continue
            arg = call.args[0]
            if _is_whole_row(arg):
                key = (rel, func.name)
                whole_row_seen.add(key)
                if key not in WHOLE_ROW:
                    problems.append(
                        f"{where}: judges whole rows; read the parts or add it to WHOLE_ROW"
                    )
                continue
            # A call nested in another filter's definition just forwards
            # its own parameter.
            if rel == "app/services/_query_filters.py" and func.name in FILTERS:
                continue
            if not _calls_name(func, "with_category_lines"):
                problems.append(f"{where}: reads parts but never joins them")
    assert not problems, "\n".join(problems)
    stale = set(WHOLE_ROW) - whole_row_seen
    assert not stale, f"WHOLE_ROW lists sites that no longer exist: {sorted(stale)}"
