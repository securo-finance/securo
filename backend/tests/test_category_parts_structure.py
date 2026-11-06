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


def _called_names(node) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if isinstance(name, str):
                names.add(name)
    return names


def _loaded_names(node) -> set[str]:
    """Names read (not assigned) anywhere in `node`."""
    return {
        n.id for n in ast.walk(node)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }


#: Calls that make a statement a query (or its result) rather than a filter
#: being prepared for one.
QUERY_CALLS = {"select", "execute", "scalar", "scalars", "stream"}


def _assigned_names(stmt) -> set[str]:
    targets = stmt.targets if isinstance(stmt, ast.Assign) else (
        [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else []
    )
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _joins_where_it_reads(func, call) -> bool:
    """True when the statement using `call` also joins the category lines.

    Checked per statement, not per function: a function with two queries
    must join the lines in both, or the second one cross-joins the alias
    and multiplies every row. A join counts when the statement calls
    `with_category_lines` (or a local helper that does), or reads a name
    built from one (`q = with_category_lines(...)`, later `q.where(...)`).
    A filter stored in a name (`window_filters = (...)`) counts when a
    statement that joins reads that name.
    """
    joining = {"with_category_lines"} | {
        inner.name
        for inner in ast.walk(func)
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
        and inner is not func
        and "with_category_lines" in _called_names(inner)
    }
    statements = [n for n in ast.walk(func) if isinstance(n, ast.stmt) and n is not func]
    joined_names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for stmt in statements:
            if (_called_names(stmt) & joining) or (_loaded_names(stmt) & joined_names):
                new = _assigned_names(stmt) - joined_names
                if new:
                    joined_names |= new
                    changed = True

    def joins(stmt) -> bool:
        return bool(_called_names(stmt) & joining) or bool(_loaded_names(stmt) & joined_names)

    # The innermost statement holding the call.
    holder = min(
        (s for s in statements if any(n is call for n in ast.walk(s))),
        key=lambda s: sum(1 for _ in ast.walk(s)),
    )
    if joins(holder):
        return True
    # Only a filter stored for later can be rescued by the statement that
    # uses it. A query built here without the join is wrong whatever happens
    # to its result afterwards (`result.all()`, or a joined query that
    # happens to reuse the name `result`).
    if _called_names(holder) & QUERY_CALLS:
        return False
    stored = _assigned_names(holder)
    return any(
        joins(stmt) and (_loaded_names(stmt) & stored)
        for stmt in statements
        if stmt is not holder
    )


def _is_whole_row(arg) -> bool:
    return (
        isinstance(arg, ast.Attribute)
        and arg.attr == "category_id"
        and getattr(arg.value, "id", None) == "Transaction"
    )


def _problems_in(source: str) -> list[str]:
    tree = ast.parse(source)
    return [
        f"{func.name}:{call.lineno}"
        for func, call in _calls(tree)
        if not _is_whole_row(call.args[0]) and not _joins_where_it_reads(func, call)
    ]


def test_the_check_is_per_query_not_per_function():
    """The guard must catch a second query that forgot the join even when
    the first one in the same function has it."""
    source = '''
async def report(session):
    first = await session.execute(
        with_category_lines(select(x)).where(counts_as_pnl(line_category_id()))
    )
    second = await session.execute(
        select(y).where(counts_as_pnl(line_category_id()))
    )
'''
    assert _problems_in(source) == ["report:7"]


def test_a_query_result_is_not_a_stored_filter():
    """Reusing a name, or reading the result later, never excuses a query
    that reads parts without joining them."""
    reused = '''
async def report(session):
    result = await session.execute(
        with_category_lines(select(x)).where(counts_as_pnl(line_category_id()))
    )
    result = await session.execute(
        select(y).where(counts_as_pnl(line_category_id()))
    )
'''
    read_later = '''
async def report(session):
    result = await session.execute(select(y).where(counts_as_pnl(line_category_id())))
    rows = result.all()
'''
    assert _problems_in(reused) == ["report:7"]
    assert _problems_in(read_later) == ["report:3"]


def test_the_check_follows_joined_names_and_stored_filters():
    source = '''
async def tool(session):
    q = with_category_lines(select(x))
    q = q.where(is_not_transfer(category_col))
    filters = (counts_as_user_pnl(line_category_id()),)
    rows = await session.execute(with_category_lines(select(y)).where(*filters))
'''
    assert _problems_in(source) == []


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
            if not _joins_where_it_reads(func, call):
                problems.append(f"{where}: reads parts in a query that never joins them")
    assert not problems, "\n".join(problems)
    stale = set(WHOLE_ROW) - whole_row_seen
    assert not stale, f"WHOLE_ROW lists sites that no longer exist: {sorted(stale)}"
