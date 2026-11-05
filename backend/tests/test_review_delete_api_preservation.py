"""Authenticated refusal characterization; shared SQLite, not PostgreSQL proof."""

import json
import platform
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api import admin as admin_api
from app.core.database import Base
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import admin_service
from app.services.transaction_service import set_review_status


@pytest.mark.asyncio
async def test_admin_delete_reviewer_preserves_rows_without_dml_or_commit(
    client: AsyncClient,
    session: AsyncSession,
    admin_auth_headers: dict,
    test_superuser: User,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    request: pytest.FixtureRequest,
):
    """Trace only await client.delete: exclude setup, snapshots and teardown."""
    backend = Path(__file__).resolve().parents[1]
    for module, relative in (
        (admin_service, "app/services/admin_service.py"),
        (admin_api, "app/api/admin.py"),
    ):
        origin = str(Path(module.__file__).resolve())
        assert origin == str(backend / relative)
        request.node.user_properties.append((module.__name__ + ".__file__", origin))
    request.node.user_properties.append(
        ("runtime", f"{platform.python_implementation()} {sys.version}; {sys.executable}")
    )

    def identity(label):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"pr2-http-preservation/{label}")

    reviewer = User(
        id=identity("reviewer-b"),
        email="reviewer-b@http-preservation.example",
        hashed_password="synthetic-unused-no-login",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(reviewer)
    await session.flush()
    session.add(WorkspaceMember(
        id=identity("editor-membership"),
        workspace_id=test_workspace.id,
        user_id=reviewer.id,
        role="editor",
    ))
    session.add(AppSetting(key="http-preservation-control", value="synthetic-unchanged"))
    transactions = []
    for label in ("reviewed", "control"):
        transaction = Transaction(
            id=identity(label),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description=f"Synthetic HTTP preservation {label}",
            amount=Decimal("10.00"),
            currency="BRL",
            date=date(2026, 10, 15),
            effective_date=date(2026, 10, 15),
            type="debit",
            source="manual",
            review_status="needs_review",
        )
        session.add(transaction)
        transactions.append(transaction)
    await session.flush()
    reviewed, control = transactions
    assert reviewed.reviewed_at is None and reviewed.reviewed_by_id is None
    assert await set_review_status(
        session, test_workspace.id, reviewed, "reviewed", reviewer.id
    ) == 1
    await session.commit()
    assert not (session.new or session.dirty or session.deleted)

    async def snapshot():
        # Core rows include every column and every row, not ORM identity-map state.
        state = {}
        for table in Base.metadata.sorted_tables:
            rows = (await session.execute(
                select(table).order_by(*table.primary_key.columns)
            )).mappings().all()
            state[table.name] = [dict(row) for row in rows]
        return state

    before = await snapshot()
    users = {row["id"]: row for row in before["users"]}
    assert {test_superuser.id, test_user.id, reviewer.id} <= users.keys()
    assert len({test_superuser.id, test_user.id, reviewer.id}) == 3
    assert users[test_superuser.id]["is_superuser"] is True
    assert users[reviewer.id]["is_superuser"] is False
    accounts = {row["id"]: row for row in before["accounts"]}
    assert accounts[test_account.id]["user_id"] == test_user.id
    assert accounts[test_account.id]["workspace_id"] == test_workspace.id
    assert any(
        row["user_id"] == reviewer.id and row["role"] == "editor"
        and row["workspace_id"] == test_workspace.id
        for row in before["workspace_members"]
    )
    rows = {row["id"]: row for row in before["transactions"]}
    stamp = rows[reviewed.id]
    assert stamp["user_id"] == test_user.id != reviewer.id
    assert stamp["workspace_id"] == test_workspace.id
    assert stamp["account_id"] == test_account.id
    assert stamp["review_status"] == "reviewed"
    assert stamp["reviewed_by_id"] == reviewer.id
    assert stamp["reviewed_at"] is not None
    assert rows[control.id]["review_status"] == "needs_review"
    assert rows[control.id]["reviewed_at"] is None
    assert rows[control.id]["reviewed_by_id"] is None
    foreign_keys = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()

    # conftest's session and get_async_session override share TestSessionLocal's
    # bind. Observe that actual bind, never an imported/recreated fixture engine.
    assert isinstance(session.bind, AsyncEngine)
    engine = session.bind.sync_engine
    statements = []
    commits = []

    def observe_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append((" ".join(statement.lower().split()), parameters))

    def observe_commit(conn):
        commits.append(True)

    event.listen(engine, "before_cursor_execute", observe_sql)
    try:
        event.listen(engine, "commit", observe_commit)
        try:
            # Only this real authenticated ASGI request is inside the witness.
            response = await client.delete(
                f"/api/admin/users/{reviewer.id}", headers=admin_auth_headers
            )
        finally:
            event.remove(engine, "commit", observe_commit)
    finally:
        event.remove(engine, "before_cursor_execute", observe_sql)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Cannot delete a user who reviewed another user's transactions"
    }
    assert statements, "A dormant listener must not pass the no-DML oracle"
    assert all(sql.startswith("select ") for sql, _ in statements), statements
    guards = [(sql, params) for sql, params in statements if (
        "from transactions" in sql and "transactions.reviewed_by_id =" in sql
        and "transactions.user_id !=" in sql
    )]
    assert len(guards) == 1
    assert tuple(guards[0][1])[:2] == (reviewer.id.hex, reviewer.id.hex)
    assert commits == []
    after = await snapshot()
    assert after == before
    request.node.user_properties.append(("witness", json.dumps({
        "http_status": response.status_code,
        "selects": len(statements), "guard_queries": len(guards),
        "insert_update_delete": 0, "commits": len(commits),
        "snapshot_tables": len(before), "snapshot_rows": sum(map(len, before.values())),
        "all_rows_unchanged": after == before, "sqlite_foreign_keys": foreign_keys,
    }, sort_keys=True)))
