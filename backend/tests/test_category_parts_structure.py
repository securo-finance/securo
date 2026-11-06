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


def _call_name(node) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    return name if isinstance(name, str) else None


def _joins_where_it_reads(func, call) -> bool:
    """True when the query that reads `call` joins the category lines.

    Checked on the query itself, not the function or the statement: the
    filter must sit inside a `with_category_lines(...)` call (or a local
    helper that makes one), or be applied to a query chain built on one
    (`with_category_lines(select(...)).where(filter)`, or
    `q.where(filter)` where `q` was built that way). A joined subquery read
    by an unjoined outer query does not count: the outer query would
    cross-join the alias and multiply every row.

    A filter stored in a name (`window_filters = (...)`) counts when every
    place reading that name does so inside a joined query.
    """
    joining = {"with_category_lines"} | {
        inner.name
        for inner in ast.walk(func)
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
        and inner is not func
        and "with_category_lines" in _called_names(inner)
    }
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(func):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    joined_names: set[str] = set()

    def chain_joined(expr) -> bool:
        """The query chain `expr` (method calls on a base) starts joined."""
        while True:
            if _call_name(expr) in joining:
                return True
            if isinstance(expr, ast.Await):
                expr = expr.value
            elif isinstance(expr, ast.Call):
                expr = expr.func
            elif isinstance(expr, ast.Attribute):
                expr = expr.value
            elif isinstance(expr, ast.Name):
                return expr.id in joined_names
            else:
                return False

    changed = True
    while changed:
        changed = False
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and chain_joined(node.value):
                new = _assigned_names(node) - joined_names
                if new:
                    joined_names |= new
                    changed = True

    def read_inside_join(node) -> bool:
        child = node
        parent = parents.get(child)
        while parent is not None and not isinstance(parent, ast.stmt):
            if _call_name(parent) in joining:
                return True
            if (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Attribute)
                and child is not parent.func
                and chain_joined(parent.func.value)
            ):
                return True
            child, parent = parent, parents.get(parent)
        return False

    if read_inside_join(call):
        return True
    # Only a filter stored for later can be rescued by where it is used; a
    # query built without the join is wrong whatever reads its result.
    holder = parents.get(call)
    while holder is not None and not isinstance(holder, ast.stmt):
        holder = parents.get(holder)
    if holder is None or _called_names(holder) & QUERY_CALLS:
        return False
    stored = _assigned_names(holder)
    uses = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in stored
    ]
    return bool(uses) and all(read_inside_join(n) for n in uses)


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


def test_a_joined_subquery_does_not_excuse_the_outer_query():
    source = '''
async def report(session):
    parts = with_category_lines(select(Transaction.id))
    rows = await session.execute(
        select(Transaction).where(Transaction.id.in_(parts), counts_as_pnl(line_category_id()))
    )
'''
    assert _problems_in(source) == ["report:5"]


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
