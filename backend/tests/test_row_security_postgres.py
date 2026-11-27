"""The workspace boundary as Postgres enforces it (ADR 0043).

Every test connects as a real restricted role, and seeds each covered table with
a row in workspace A and one in workspace B.
"""
import importlib
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, event, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.types import JSON, LargeBinary, Uuid

from app.agents.models import *  # noqa: F401,F403
from app.core import row_security
from app.core.config import get_settings
from app.core.database import Base, get_async_session
from app.main import app
from app.models import *  # noqa: F401,F403
from app.models.transaction import Transaction
from tests.test_workspace_boundary_schema import VIA_PARENT as BOUNDARY_VIA_PARENT

A, B = uuid.uuid4(), uuid.uuid4()
TABLES = dict(Base.metadata.tables)
COVERED = row_security.covered_tables(TABLES)


def test_every_workspace_table_is_covered_or_says_why():
    owned = {n for n, t in TABLES.items() if "workspace_id" in t.c}
    assert owned - row_security.NOT_COVERED.keys() <= set(COVERED)
    assert row_security.NOT_COVERED.keys() <= owned, "stale NOT_COVERED entry"
    assert {n: c for n, (c, _) in row_security.VIA_PARENT.items()} == BOUNDARY_VIA_PARENT
    for name, (column, parent) in row_security.VIA_PARENT.items():
        assert [fk.column.table.name for fk in TABLES[name].c[column].foreign_keys] == [parent]
        assert parent in COVERED


def _value(column):
    kind = column.type
    if isinstance(kind, Uuid):
        return uuid.uuid4()
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, Integer):
        return 1
    if isinstance(kind, Numeric):
        return Decimal("1")
    if isinstance(kind, DateTime):
        return datetime.now(timezone.utc) if kind.timezone else datetime(2026, 1, 1)
    if isinstance(kind, Date):
        return date(2026, 1, 1)
    if isinstance(kind, String):
        return uuid.uuid4().hex[: kind.length or 32]
    if isinstance(kind, LargeBinary):
        return b"x"
    if isinstance(kind, JSON):
        return {}
    python = getattr(kind, "python_type", None)
    return {} if python in (dict, None) else python()


def _row(table, **fixed):
    values = {
        c.name: _value(c) for c in table.columns
        if c.name not in fixed and (not c.nullable or c.primary_key)
    }
    return values | fixed


class Stack:
    def __init__(self, owner, app):
        self.owner, self.app, self.rows = owner, app, {}

    def bound(self, workspace_id):
        return row_security.scoped_maker(self.app, workspace_id)


@pytest_asyncio.fixture
async def stack(postgres_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "row_security_enabled", True)
    base = make_url(os.environ["POSTGRES_TEST_URL"])
    options = postgres_sessions.kw["bind"].get_execution_options()
    schema = options["schema_translate_map"][None]
    role = f"app_{schema[-12:]}"
    path = {"server_settings": {"search_path": f"{schema},public"}}
    owner = create_async_engine(base, connect_args=path)
    app = create_async_engine(base.set(username=role, password="restricted"), connect_args=path)
    made = Stack(
        async_sessionmaker(owner, expire_on_commit=False), async_sessionmaker(app, expire_on_commit=False)
    )
    try:
        async with owner.begin() as conn:
            await conn.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD 'restricted'")
            for _ in range(2):  # running it again changes nothing
                for statement in row_security.setup_statements(
                    TABLES, role, schema, conn.dialect.identifier_preparer.quote
                ):
                    await conn.exec_driver_sql(statement)
        yield made
    finally:
        await app.dispose()
        async with owner.begin() as conn:
            await conn.exec_driver_sql(f"DROP OWNED BY {role}")
            await conn.exec_driver_sql(f"DROP ROLE {role}")
        await owner.dispose()


@asynccontextmanager
async def _unchecked(stack):
    """Owner session that seeds rows without satisfying unrelated constraints."""
    async with stack.owner() as session, session.begin():
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        yield session


async def _seed(stack):
    async with _unchecked(stack) as session:
        checks = (await session.execute(text(
            "SELECT conrelid::regclass::text, quote_ident(conname) FROM pg_constraint "
            "WHERE contype = 'c' AND connamespace = current_schema()::regnamespace"
        ))).all()
        for table, name in checks:
            await session.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {name}"))
    async with _unchecked(stack) as session:
        for workspace in (A, B):
            table = TABLES["workspaces"]
            await session.execute(insert(table).values(**_row(table, id=workspace)))
        order = [n for n in COVERED if n not in row_security.VIA_PARENT] + list(row_security.VIA_PARENT)
        for name in order:
            table = TABLES[name]
            for workspace in (A, B):
                if name in row_security.VIA_PARENT:
                    column, parent = row_security.VIA_PARENT[name]
                    row = _row(table, **{column: stack.rows[parent, workspace]["id"]})
                else:
                    row = _row(table, workspace_id=workspace)
                stack.rows[name, workspace] = row
                await session.execute(insert(table).values(**row))


def _is(table, row):
    return [c == row[c.name] for c in table.primary_key]


async def test_every_covered_table_is_isolated(stack):
    await _seed(stack)
    problems = []
    for name in COVERED:
        table = TABLES[name]
        mine, theirs = stack.rows[name, A], stack.rows[name, B]
        async with stack.app() as session:
            if await session.scalar(select(func.count()).select_from(table)):
                problems.append(f"{name}: readable without a workspace")
        async with stack.bound(A)() as session:
            count = select(func.count()).select_from(table)
            if await session.scalar(count) != 1 or await session.scalar(count.where(*_is(table, mine))) != 1:
                problems.append(f"{name}: reads more or less than its own row")
            touched = next(iter(table.primary_key))
            changed = await session.execute(
                table.update().where(*_is(table, theirs)).values({touched: theirs[touched.name]})
            )
            removed = await session.execute(table.delete().where(*_is(table, theirs)))
            if changed.rowcount or removed.rowcount:
                problems.append(f"{name}: writes another workspace's row")
            column = row_security.VIA_PARENT.get(name, ("workspace_id",))[0]
            # Pointing its own row, or a new one, at the other workspace is refused.
            for statement in (
                insert(table).values(**_row(table, **{column: theirs[column]})),
                table.update().where(*_is(table, mine)).values({column: theirs[column]}),
            ):
                try:
                    async with session.begin_nested():
                        await session.execute(statement)
                except DBAPIError as error:
                    if "row-level security" not in str(error):
                        problems.append(f"{name}: refused for another reason: {error.orig}")
                else:
                    problems.append(f"{name}: accepts a row of another workspace")
            await session.rollback()
    assert not problems, "\n".join(problems)


async def test_the_workspace_outlives_a_commit_and_does_not_leak_to_the_pool(stack):
    await _seed(stack)
    count = select(func.count()).select_from(Transaction)
    async with stack.app() as session:
        assert await session.scalar(count) == 0
        await row_security.bind_workspace(session, A)  # transaction already open
        assert await session.scalar(count) == 1
        await session.commit()
        assert await session.scalar(count) == 1
        await session.rollback()
        assert await session.scalar(count) == 1
        await row_security.bind_workspace(session, B)
        assert await session.scalar(select(Transaction.workspace_id)) == B
    async with stack.app() as session:  # same pooled connection
        assert await session.scalar(count) == 0


async def test_shared_expense_is_read_only_for_the_member(stack):
    await _seed(stack)
    member_user, other_user = uuid.uuid4(), uuid.uuid4()
    shared = stack.rows["transactions", B]["id"]
    async with _unchecked(stack) as session:
        group = _row(TABLES["groups"], workspace_id=B)
        member = _row(
            TABLES["group_members"], workspace_id=B, group_id=group["id"], linked_user_id=member_user
        )
        split = _row(
            TABLES["transaction_splits"], workspace_id=B, transaction_id=shared, group_member_id=member["id"]
        )
        for name, row in (("groups", group), ("group_members", member), ("transaction_splits", split)):
            await session.execute(insert(TABLES[name]).values(**row))
    table = TABLES["transactions"]
    for user, seen in ((member_user, 2), (other_user, 1), (None, 1)):
        async with stack.app() as session:
            await row_security.bind_workspace(session, A, user)
            assert await session.scalar(select(func.count()).select_from(table)) == seen
            assert (await session.execute(table.delete().where(table.c.id == shared))).rowcount == 0
            assert (await session.execute(
                table.update().where(table.c.id == shared).values(description="x")
            )).rowcount == 0


async def test_the_quota_counts_connections_across_workspaces_and_reads_nothing_else(stack):
    """ADR 0049: bound to one workspace, the request still learns how many
    connections every workspace its user created holds, and only that number."""
    from app.services import quota_service

    await _seed(stack)
    user = uuid.uuid4()
    async with _unchecked(stack) as session:
        await session.execute(TABLES["workspaces"].update().values(created_by_user_id=user))
    connections = TABLES["bank_connections"]
    async with stack.bound(A)() as session:
        assert await session.scalar(select(func.count()).select_from(connections)) == 1
        assert await quota_service.count_bank_connections(session, user) == 2
        assert await quota_service.count_bank_connections(session, uuid.uuid4()) == 0
    async with _unchecked(stack) as session:
        await session.execute(TABLES["workspaces"].update().where(TABLES["workspaces"].c.id == B).values(is_archived=True))
    async with stack.bound(A)() as session:
        assert await quota_service.count_bank_connections(session, user) == 1
    async with stack.owner() as session:
        public = await session.scalar(text(
            "SELECT has_function_privilege('public', 'securo_owned_bank_connections(uuid)', 'EXECUTE')"
        ))
        assert public is False


async def test_jobs_run_one_workspace_at_a_time(stack):
    await _seed(stack)
    seen = []
    for maker in await row_security.workspace_makers(stack.app):
        async with maker() as session:
            seen += [(await session.scalars(select(Transaction.workspace_id))).all()]
    assert sorted(seen) == sorted([[A], [B]])
    for name in row_security.LOOKUP_TABLES:
        maker = await row_security.maker_for_row(stack.app, name, stack.rows[name, B]["id"])
        async with maker() as session:
            assert (await session.scalars(select(TABLES[name].c.workspace_id))).all() == [B]
    gone = await row_security.maker_for_row(stack.app, "agents", uuid.uuid4())
    async with gone() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


async def test_startup_refuses_a_role_that_bypasses_the_policies(stack):
    async with stack.app() as session:
        await row_security.check_connection(session)
    async with stack.owner() as session:
        with pytest.raises(RuntimeError, match="bypasses row security"):
            await row_security.check_connection(session)
        await session.execute(text("ALTER TABLE transactions DISABLE ROW LEVEL SECURITY"))
        await session.commit()
    async with stack.app() as session:
        with pytest.raises(RuntimeError, match="not set up"):
            await row_security.check_connection(session)


async def test_off_changes_nothing(stack, monkeypatch):
    monkeypatch.setattr(get_settings(), "row_security_enabled", False)
    assert await row_security.workspace_makers(stack.app) == [stack.app]
    assert await row_security.maker_for_row(stack.app, "agents", uuid.uuid4()) is stack.app
    async with stack.app() as session:
        await row_security.bind_workspace(session, A)
        assert row_security.bound_to(session) is None


TASK_MODULES = sorted(
    path for folder in ("app/tasks", "app/agents/tasks")
    for path in (Path(__file__).parents[1] / folder).glob("*.py")
    if "create_async_engine" in path.read_text()
)


@pytest.mark.parametrize("path", TASK_MODULES, ids=lambda path: path.stem)
def test_every_job_module_takes_its_sessions_by_workspace(path):
    assert re.search(r"await (workspace_makers|maker_for_row)\(", path.read_text())


JOBS = [
    ("app.tasks.sync_tasks", "_make_session_maker", "_sync_all"),
    ("app.tasks.recurring_tasks", "_make_session_maker", "_generate_all"),
    ("app.tasks.asset_tasks", "_make_session_maker", "_apply_growth_rules"),
    ("app.tasks.asset_tasks", "_make_session_maker", "_refresh_market_prices"),
    ("app.tasks.fx_rate_tasks", "_make_session_maker", "_restamp_recurring_fx"),
    ("app.tasks.fx_rate_tasks", "_make_session_maker", "_restamp_fallback_transactions"),
    ("app.tasks.fx_backfill_tasks", "_make_session_maker", "_backfill_primary_amounts"),
    ("app.tasks.invoice_schedule_tasks", "_make_session_maker", "_generate_all"),
    ("app.tasks.plugin_tasks", "_session_maker", "_sync_due"),
    ("app.tasks.backup_tasks", "_make_session_maker", "_run_scheduled_backups"),
    ("app.tasks.notification_tasks", "_make_session_maker", "_evaluate_all"),
    ("app.tasks.notification_tasks", "_make_session_maker", "_deliver"),
]


async def _add_workspaces(stack):
    """Two workspaces with one owner each. Returns {workspace id: user id}."""
    owners = {A: uuid.uuid4(), B: uuid.uuid4()}
    async with _unchecked(stack) as session:
        for workspace, user in owners.items():
            await session.execute(insert(TABLES["users"]).values(**_row(
                TABLES["users"], id=user, email=f"{user}@example.com", preferences={"currency_display": "USD"}
            )))
            await session.execute(insert(TABLES["workspaces"]).values(**_row(
                TABLES["workspaces"], id=workspace, kind="personal", default_currency="USD"
            )))
    return owners


@pytest.mark.parametrize(("module", "maker", "job"), JOBS, ids=[f"{m}.{j}" for m, _, j in JOBS])
async def test_each_job_works_every_workspace_and_only_inside_one(stack, monkeypatch, module, maker, job):
    await _add_workspaces(stack)
    tasks = importlib.import_module(module)

    class Engine:
        async def dispose(self):
            pass

    monkeypatch.setattr(tasks, maker, lambda: (Engine(), stack.app))
    # A query on a covered table outside a workspace is the job skipping the boundary.
    covered = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO) \"?(%s)\"?\b" % "|".join(COVERED))
    entered, loose = set(), []

    def watch(state):
        bound = state.session.info.get(row_security._WORKSPACE)
        entered.add(bound)
        if bound is None and covered.search(str(state.statement)):
            loose.append(str(state.statement))

    event.listen(Session, "do_orm_execute", watch)
    try:
        await getattr(tasks, job)()
    finally:
        event.remove(Session, "do_orm_execute", watch)
    assert {A, B} <= entered
    assert not loose


async def test_recurring_job_creates_each_workspace_its_own_rows(stack, monkeypatch):
    from app.tasks import recurring_tasks

    owners = await _add_workspaces(stack)
    table, accounts = TABLES["recurring_transactions"], TABLES["accounts"]
    async with _unchecked(stack) as session:
        for workspace, user in owners.items():
            account = _row(accounts, workspace_id=workspace, user_id=user, type="checking", currency="USD")
            await session.execute(insert(accounts).values(**account))
            await session.execute(insert(table).values(**_row(
                table, workspace_id=workspace, user_id=user, account_id=account["id"], type="debit",
                currency="USD", frequency="monthly", weekend_adjustment="none", is_active=True, auto_generate=True, start_date=date(2026, 1, 1),
                next_occurrence=date(2026, 1, 1), end_date=date(2026, 1, 31),
            )))

    class Engine:
        async def dispose(self):
            pass

    monkeypatch.setattr(recurring_tasks, "_make_session_maker", lambda: (Engine(), stack.app))
    assert await recurring_tasks._generate_all() == 2
    async with stack.owner() as session:
        made = (await session.execute(select(Transaction.workspace_id, Transaction.user_id))).all()
    assert sorted(map(tuple, made)) == sorted(owners.items())


async def test_a_bill_whose_account_moved_away_writes_nothing(stack, monkeypatch):
    """The policy hides the moved account, so the job must not read None as fine."""
    from app.tasks import recurring_tasks

    owners = await _add_workspaces(stack)
    table, accounts = TABLES["recurring_transactions"], TABLES["accounts"]
    async with _unchecked(stack) as session:
        account = _row(accounts, workspace_id=B, user_id=owners[B], type="checking", currency="USD")
        await session.execute(insert(accounts).values(**account))
        bill = _row(
            table, workspace_id=A, user_id=owners[A], account_id=account["id"], type="debit",
            currency="USD", frequency="monthly", weekend_adjustment="none", is_active=True,
            auto_generate=True, start_date=date(2026, 1, 1), next_occurrence=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        await session.execute(insert(table).values(**bill))

    class Engine:
        async def dispose(self):
            pass

    monkeypatch.setattr(recurring_tasks, "_make_session_maker", lambda: (Engine(), stack.app))
    assert await recurring_tasks._generate_all() == 0
    async with stack.owner() as session:
        assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
        pointer = await session.scalar(select(table.c.next_occurrence).where(table.c.id == bill["id"]))
    assert pointer == date(2026, 1, 1)


async def test_an_access_token_sees_nothing_shared(stack):
    """A token's request is bound without its creator, so the shared policy gives it nothing."""
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.core.workspace_context import current_workspace
    from app.models.user import User

    owners = await _add_workspaces(stack)
    person = owners[A]
    transactions = TABLES["transactions"]
    async with _unchecked(stack) as session:
        await session.execute(insert(TABLES["workspace_members"]).values(**_row(
            TABLES["workspace_members"], workspace_id=A, user_id=person, role="owner"
        )))
        for workspace in (A, B):
            await session.execute(insert(transactions).values(**_row(transactions, workspace_id=workspace)))
        shared = await session.scalar(select(transactions.c.id).where(transactions.c.workspace_id == B))
        group = _row(TABLES["groups"], workspace_id=B)
        member = _row(TABLES["group_members"], workspace_id=B, group_id=group["id"], linked_user_id=person)
        split = _row(
            TABLES["transaction_splits"], workspace_id=B, transaction_id=shared, group_member_id=member["id"]
        )
        for name, row in (("groups", group), ("group_members", member), ("transaction_splits", split)):
            await session.execute(insert(TABLES[name]).values(**row))
    for token, seen in ((None, 2), (SimpleNamespace(workspace_id=A), 1)):
        async with stack.app() as session:
            user = await session.get(User, person)
            assert user is not None
            request = Request({"type": "http", "headers": [], "state": {"access_token": token}})
            # Driven the way FastAPI drives a yield dependency: up to the yield, then on to its end.
            dependency = current_workspace(request, None, user, session)
            assert (await anext(dependency)).id == A
            assert await session.scalar(select(func.count()).select_from(transactions)) == seen
            assert await anext(dependency, None) is None


async def test_the_api_works_under_the_restricted_role(stack, monkeypatch):
    """Sign up, then read and write over HTTP, with a second person kept out."""
    async def sessions():
        async with stack.app() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        people = []
        for name in ("one", "two"):
            email, password = f"{name}@example.com", "Correct-horse-9"
            made = await client.post("/api/auth/register", json={"email": email, "password": password})
            assert made.status_code == 201, made.text
            login = await client.post("/api/auth/login", data={"username": email, "password": password})
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            account = await client.post(
                "/api/accounts", headers=headers,
                json={"name": f"{name} bank", "type": "checking", "balance": 10, "currency": "USD"},
            )
            assert account.status_code == 201, account.text
            tx = await client.post("/api/transactions", headers=headers, json={
                "account_id": account.json()["id"], "description": f"{name} lunch", "amount": 5,
                "date": "2026-01-05", "type": "debit", "currency": "USD",
            })
            assert tx.status_code == 201, tx.text
            people.append((name, headers, account.json()["id"], tx.json()["id"]))
        for (name, headers, *_), (_, _, account_id, tx_id) in (people, people[::-1]):
            listed = (await client.get("/api/accounts", headers=headers)).json()
            assert f"{name} bank" in {a["name"] for a in listed}
            assert account_id not in {a["id"] for a in listed}
            assert (await client.get("/api/categories", headers=headers)).json()
            assert (await client.get(f"/api/accounts/{account_id}", headers=headers)).status_code == 404
            patched = await client.patch(f"/api/transactions/{tx_id}", headers=headers, json={"description": "x"})
            assert patched.status_code == 404
            for path in ("/api/transactions", "/api/dashboard/summary", "/api/budgets", "/api/payees"):
                assert (await client.get(path, headers=headers)).status_code == 200, path
