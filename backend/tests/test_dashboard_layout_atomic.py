"""Isolated file-SQLite/real-auth ASGI proofs; not PostgreSQL lock evidence."""
import asyncio
import copy
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException
from sqlalchemy import JSON, delete, event, null, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, object_session
from sqlalchemy.pool import NullPool

from app.api import dashboard
from app.core.auth import get_jwt_strategy
from app.core.database import Base, engine as configured_engine, get_async_session
from app.main import app
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.dashboard import DASHBOARD_WIDGET_IDS, DashboardLayoutWrite
from app.services import dashboard_layout_preferences as layout_service

def layout(workspace_id, widget="hero-balance"):
    return {
        "version": 1,
        "workspaceId": str(workspace_id),
        "items": [{"id": widget, "visible": True, "order": 0}],
    }


@pytest_asyncio.fixture
async def atomic_db(request, tmp_path, tmp_path_factory):
    proof_root = tmp_path_factory.getbasetemp().resolve()
    assert tmp_path.is_absolute()
    assert tmp_path.resolve().is_relative_to(proof_root)
    assert Path.cwd().resolve() == Path(__file__).resolve().parents[1]
    assert Path(dashboard.__file__).resolve() == Path(__file__).resolve().parents[1] / (
        "app/api/dashboard.py"
    )
    assert Path(layout_service.__file__).resolve() == Path(__file__).resolve().parents[1] / (
        "app/services/dashboard_layout_preferences.py"
    )
    db_path = tmp_path / "layout.sqlite"
    assert not db_path.exists()
    events = []
    origins = []
    configured_attempts = []
    bindings = {"auth": 0, "factory": 0}

    class ProofSession(Session):
        pass

    cache_mode = getattr(request, "param", None)
    assert cache_mode in (None, "shared-cache")
    # Shared-cache variant is solely a real SQLITE_LOCKED falsifier, not a
    # production setting or substitute for default independent-file proofs.
    database_url = (f"sqlite+aiosqlite:///file:{db_path}?cache=shared&uri=true"
                    if cache_mode else f"sqlite+aiosqlite:///{db_path}")
    engine = create_async_engine(
        database_url, poolclass=NullPool, connect_args={"timeout": 0.1}
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, sync_session_class=ProofSession, expire_on_commit=False,
        info={"role": "service"},
    )

    def forbid_configured(*args):
        configured_attempts.append("forbidden")
        raise AssertionError("Configured application engine must never connect")

    @event.listens_for(engine.sync_engine, "connect")
    def connected(connection, record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA database_list")
        origin = cursor.fetchall()[0][2]
        cursor.close()
        assert Path(origin) == db_path
        origins.append(origin)
        record.info["dbapi_id"] = id(connection)

    @event.listens_for(ProofSession, "after_begin")
    def tagged(session, transaction, connection):
        connection.info["role"] = session.info.get("role", "setup")

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def sql(connection, cursor, statement, parameters, context, executemany):
        events.append({"kind": "sql", "role": connection.info.get("role"),
                       "connection": id(connection),
                       "dbapi_id": connection.info["dbapi_id"], "sql": statement})

    @event.listens_for(engine.sync_engine, "commit")
    def committed(connection):
        events.append({"kind": "commit", "role": connection.info.get("role"),
                       "connection": id(connection)})

    @event.listens_for(engine.sync_engine, "rollback")
    def rolled_back(connection):
        events.append({"kind": "rollback", "role": connection.info.get("role"),
                       "connection": id(connection)})

    @event.listens_for(ProofSession, "before_flush")
    def flushing(session, context, instances):
        events.append({"kind": "flush", "role": session.info.get("role"),
                       "dirty_users": sum(isinstance(obj, User) for obj in session.dirty)})

    @event.listens_for(ProofSession, "before_commit")
    def committing(session):
        events.append({"kind": "commit_call", "role": session.info.get("role")})

    @event.listens_for(ProofSession, "after_commit")
    def commit_done(session):
        events.append({"kind": "commit_done", "role": session.info.get("role")})

    def assigned(target, value, oldvalue, initiator):
        session = object_session(target)
        if isinstance(session, ProofSession):
            events.append({"kind": "assignment", "role": session.info.get("role")})

    # do_connect fires BEFORE the DBAPI opens anything (connect is too late).
    # checkout also rejects any already-pooled connection, independent of URL.
    event.listen(configured_engine.sync_engine, "do_connect", forbid_configured)
    event.listen(configured_engine.sync_engine, "checkout", forbid_configured)
    event.listen(User.preferences, "set", assigned)
    previous = dict(app.dependency_overrides)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        actor_id, other_id, ws_id, second_id = [uuid.uuid4() for _ in range(4)]
        async with factory(info={"role": "setup"}) as session:
            actor = User(id=actor_id, email="atomic@example.invalid", hashed_password="unused",
                         is_active=True, is_verified=True, is_superuser=False,
                         preferences={"language": "en", "unknown": {"keep": [1, None]}})
            other = User(id=other_id, email="other@example.invalid", hashed_password="unused",
                         is_active=True, is_verified=True, is_superuser=False,
                         preferences={"other": True})
            session.add_all([actor, other])
            await session.flush()
            session.add_all([
                Workspace(id=ws_id, name="Target", created_by_user_id=other_id),
                Workspace(id=second_id, name="Second", created_by_user_id=other_id),
            ])
            await session.flush()
            session.add_all([
                WorkspaceMember(workspace_id=ws_id, user_id=actor_id, role="viewer"),
                WorkspaceMember(workspace_id=second_id, user_id=actor_id, role="owner"),
            ])
            await session.commit()
            token = await get_jwt_strategy().write_token(actor)

        async def auth_session():
            bindings["auth"] += 1
            async with factory(info={"role": "auth"}) as session:
                yield session
                events.append({"kind": "auth_release", "role": "auth"})

        def service_factory():
            bindings["factory"] += 1
            return factory

        app.dependency_overrides[get_async_session] = auth_session
        provider = dashboard.get_dashboard_layout_session_factory
        app.dependency_overrides[provider] = service_factory
        events.clear()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(
                client=client, factory=factory, engine=engine, actor_id=actor_id,
                other_id=other_id, ws_id=ws_id, second_id=second_id, events=events,
                bindings=bindings, db_path=db_path,
                proof_session=ProofSession, origins=origins, cache_mode=cache_mode,
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        event.remove(configured_engine.sync_engine, "do_connect", forbid_configured)
        event.remove(configured_engine.sync_engine, "checkout", forbid_configured)
        event.remove(User.preferences, "set", assigned)
        await engine.dispose()
        with (tmp_path / "fixture-proof.json").open("x") as output:
            json.dump({"case": request.node.nodeid, "database": str(db_path),
                       "connection_origins": origins, "bindings": bindings,
                       "cache_mode": cache_mode, "events": events,
                       "configured_engine_attempts": configured_attempts,
                       "overrides_restored": app.dependency_overrides == previous}, output)
        assert configured_attempts == []


async def preferences(db, user_id=None):
    async with db.factory(info={"role": "inspect"}) as session:
        return (await session.execute(
            select(User.preferences).where(User.id == (user_id or db.actor_id))
        )).scalar_one()


@pytest.mark.asyncio
async def test_valid_put_commits_only_actor_target(atomic_db):
    db = atomic_db
    body = layout(db.ws_id)
    response = await db.client.put(f"/api/dashboard/layouts/{db.ws_id}",
                                   json=body, headers=db.headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"workspaceId": str(db.ws_id), "layout": body}
    assert await preferences(db) == {
        "language": "en", "unknown": {"keep": [1, None]},
        "dashboard_layouts": {str(db.ws_id): body},
    }
    assert await preferences(db, db.other_id) == {"other": True}
    assert db.bindings == {"auth": 1, "factory": 1}
    commits = [e for e in db.events if e["kind"] == "commit" and e["role"] == "service"]
    assert len(commits) == 1
    assert not [e for e in db.events if e["kind"] == "commit" and e["role"] == "auth"]
    assert_transaction(db)


def role_events(db, role="service", kind=None):
    return [entry for entry in db.events if entry.get("role") == role
            and (kind is None or entry["kind"] == kind)]


def assert_transaction(db):
    service = role_events(db)
    statements = [entry for entry in service if entry["kind"] == "sql"]
    assert statements[0]["sql"] == "PRAGMA busy_timeout = 1000"
    assert statements[1]["sql"] == "BEGIN IMMEDIATE"
    assert all(not entry["sql"].lstrip().upper().startswith("SELECT")
               for entry in statements[:2])
    assert len({entry["connection"] for entry in statements}) == 1
    assert len(role_events(db, kind="assignment")) == 1
    assert len(role_events(db, kind="flush")) == 1
    assert role_events(db, kind="flush")[0]["dirty_users"] == 1
    for kind in ("commit", "commit_call", "commit_done"):
        assert len(role_events(db, kind=kind)) == 1
    writes = [entry for entry in statements if entry["sql"].upper().startswith("UPDATE")]
    assert len(writes) == 1
    assert writes[0]["sql"].split("WHERE")[0].strip() == "UPDATE users SET preferences=?"
    assert not role_events(db, kind="rollback")
    for kind in ("commit", "commit_call", "flush", "assignment"):
        assert not role_events(db, role="auth", kind=kind)
    auth_sql = role_events(db, role="auth", kind="sql")
    assert auth_sql and all(entry["sql"].lstrip().upper().startswith("SELECT")
                            for entry in auth_sql)
    assert set(entry["dbapi_id"] for entry in auth_sql).isdisjoint(
        entry["dbapi_id"] for entry in statements
    )
    # The still-open ordinary auth read session must not block service commit.
    assert db.events.index(role_events(db, kind="commit_done")[0]) < db.events.index(
        role_events(db, role="auth", kind="auth_release")[0]
    )


def assert_no_write(db, *, rollback=False):
    for kind in ("assignment", "flush", "commit", "commit_call", "commit_done"):
        assert not role_events(db, kind=kind)
    assert not [entry for entry in role_events(db, kind="sql")
                if entry["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    assert not role_events(db, role="auth", kind="commit")
    if rollback:
        assert len(role_events(db, kind="rollback")) == 1


async def set_preferences(db, value):
    async with db.factory(info={"role": "setup"}) as session:
        await session.execute(update(User).where(User.id == db.actor_id).values(preferences=value))
        await session.commit()
    db.events.clear()


async def put(db, body=None, *, workspace_id=None, headers=None):
    return await db.client.put(
        f"/api/dashboard/layouts/{workspace_id or db.ws_id}",
        json=layout(workspace_id or db.ws_id) if body is None else body,
        headers=db.headers if headers is None else headers,
    )


async def service_save(db, body=None, *, factory=None):
    submitted = DashboardLayoutWrite.model_validate(body or layout(db.ws_id))
    return await layout_service.save_dashboard_layout(
        actor_id=db.actor_id, workspace_id=uuid.UUID(submitted.workspaceId), layout=submitted,
        session_factory=db.factory if factory is None else factory,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["absent", "matching", "other", "malformed", "empty"])
async def test_header_is_compare_only(atomic_db, header):
    db = atomic_db
    headers = dict(db.headers)
    if header != "absent":
        headers["X-Workspace-Id"] = {
            "matching": str(db.ws_id), "other": str(db.second_id),
            "malformed": "not-a-uuid", "empty": "",
        }[header]
    before = await preferences(db)
    db.events.clear()
    response = await put(db, headers=headers)
    if header in ("absent", "matching"):
        assert response.status_code == 200, response.text
        assert response.json()["workspaceId"] == str(db.ws_id)
        assert str(db.second_id) not in (await preferences(db))["dashboard_layouts"]
        assert_transaction(db)
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "WORKSPACE_HEADER_CONFLICT"
        assert await preferences(db) == before
        assert_no_write(db)
        assert not role_events(db, kind="sql")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["malformed", "compact", "uppercase", "body-other", "body-compact"])
async def test_path_and_body_are_explicit_canonical_uuid(atomic_db, kind):
    db = atomic_db
    path = str(db.ws_id)
    body = layout(db.ws_id)
    if kind == "malformed":
        path = "not-a-uuid"
    elif kind == "compact":
        path = db.ws_id.hex
    elif kind == "uppercase":
        # Explicit letters avoid the tiny random all-numeric UUID possibility.
        path = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    elif kind == "body-other":
        body["workspaceId"] = str(db.second_id)
    else:
        body["workspaceId"] = db.ws_id.hex
    before = await preferences(db)
    response = await put(db, body, workspace_id=path)
    assert response.status_code == 422, response.text
    assert await preferences(db) == before
    assert_no_write(db)
    assert not role_events(db, kind="sql")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "version-bool", "version-false", "version-float", "version-string", "version-future",
    "version-null", "order-bool", "order-true", "order-float", "order-string", "order-negative", "order-null",
    "order-gap", "visible-int", "visible-string", "visible-null", "none-visible", "unknown-id",
    "duplicate-id", "duplicate-order", "empty-items", "over-64", "body-extra", "item-extra",
    "missing-version", "missing-workspace", "missing-items", "workspace-int", "items-object",
    "item-null", "root-array", "root-string", "missing-id", "missing-visible", "missing-order",
])
async def test_strict_write_rejections_leave_document_unchanged(atomic_db, case):
    db = atomic_db
    body = layout(db.ws_id)
    if case.startswith("version-"):
        body["version"] = {"bool": True, "false": False, "float": 1.0, "string": "1",
                           "future": 2, "null": None}[case.removeprefix("version-")]
    elif case == "order-true":
        body["items"].append({"id": "goals-progress", "visible": True, "order": True})
    elif case.startswith("order-"):
        body["items"][0]["order"] = {"bool": False, "float": 0.0, "string": "0",
                                    "negative": -1, "null": None, "gap": 1}[
                                        case.removeprefix("order-")]
    elif case.startswith("visible-"):
        body["items"][0]["visible"] = {"int": 1, "string": "true", "null": None}[
            case.removeprefix("visible-")]
    elif case == "none-visible":
        body["items"][0]["visible"] = False
    elif case == "unknown-id":
        body["items"][0]["id"] = "future-widget"
    elif case == "duplicate-id":
        body["items"].append({"id": "hero-balance", "visible": True, "order": 1})
    elif case == "duplicate-order":
        body["items"].append({"id": "goals-progress", "visible": True, "order": 0})
    elif case == "empty-items":
        body["items"] = []
    elif case == "over-64":
        body["items"] = [{"id": "hero-balance", "visible": True, "order": i} for i in range(65)]
    elif case == "body-extra":
        body["preferences"] = {"smuggled": True}
    elif case == "item-extra":
        body["items"][0]["unknown"] = "not accepted"
    elif case.startswith("missing-"):
        key = case.removeprefix("missing-")
        if key in ("id", "visible", "order"):
            del body["items"][0][key]
        else:
            del body["workspaceId" if key == "workspace" else key]
    elif case == "workspace-int":
        body["workspaceId"] = 1
    elif case == "items-object":
        body["items"] = {}
    elif case == "item-null":
        body["items"] = [None]
    elif case == "root-array":
        body = []
    else:
        body = "not an object"
    before = await preferences(db)
    response = await put(db, body)
    assert response.status_code == 422, response.text
    assert await preferences(db) == before
    assert_no_write(db)
    assert not role_events(db, kind="sql")


@pytest.mark.asyncio
async def test_six_registry_ids_and_contiguous_permutation_are_accepted(atomic_db):
    db = atomic_db
    assert DASHBOARD_WIDGET_IDS == {
        "hero-balance", "uncategorized-banner", "spending-charts", "balance-evolution",
        "goals-progress", "period-transactions",
    }
    body = layout(db.ws_id)
    body["items"] = [{"id": widget, "visible": order == 0, "order": order}
                     for order, widget in enumerate(sorted(DASHBOARD_WIDGET_IDS))]
    body["items"].reverse()
    response = await put(db, body)
    assert response.status_code == 200, response.text
    assert response.json() == {"workspaceId": str(db.ws_id), "layout": body}
    assert (await preferences(db))["dashboard_layouts"][str(db.ws_id)] == body
    assert_transaction(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["viewer", "editor", "owner", "manager", "missing",
                                   "inaccessible", "archived", "invalid-role"])
async def test_explicit_workspace_effective_access(atomic_db, access):
    db = atomic_db
    target = db.ws_id
    async with db.factory(info={"role": "setup"}) as session:
        if access == "missing":
            target = uuid.uuid4()
        elif access == "archived":
            await session.execute(update(Workspace).where(Workspace.id == target).values(
                is_archived=True))
        elif access in ("manager", "inaccessible"):
            await session.execute(delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == target, WorkspaceMember.user_id == db.actor_id))
            if access == "manager":
                await session.execute(update(Workspace).where(Workspace.id == target).values(
                    managed_by_user_id=db.actor_id))
        else:
            await session.execute(update(WorkspaceMember).where(
                WorkspaceMember.workspace_id == target, WorkspaceMember.user_id == db.actor_id
            ).values(role="corrupt-role" if access == "invalid-role" else access))
        await session.commit()
    before = await preferences(db)
    db.events.clear()
    response = await put(db, layout(target), workspace_id=target)
    if access in ("viewer", "editor", "owner", "manager"):
        assert response.status_code == 200, response.text
        assert response.json()["workspaceId"] == str(target)
        assert_transaction(db)
    else:
        assert response.status_code == 404, response.text
        assert await preferences(db) == before
        assert_no_write(db, rollback=True)
    service_sql = role_events(db, kind="sql")
    assert service_sql[1]["sql"] == "BEGIN IMMEDIATE"
    assert service_sql[2]["sql"].lstrip().upper().startswith("SELECT")


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["absent", "invalid", "inactive"])
async def test_requires_current_active_user_without_mutation(atomic_db, auth):
    db = atomic_db
    headers = {} if auth == "absent" else {"Authorization": "Bearer invalid-synthetic-token"}
    if auth == "inactive":
        async with db.factory(info={"role": "setup"}) as session:
            await session.execute(update(User).where(User.id == db.actor_id).values(is_active=False))
            await session.commit()
        headers = db.headers
    before = await preferences(db)
    db.events.clear()
    response = await put(db, headers=headers)
    assert response.status_code == 401, response.text
    assert await preferences(db) == before
    assert_no_write(db)
    assert not role_events(db, kind="sql")


@pytest.mark.asyncio
@pytest.mark.parametrize("framing", ["missing-length", "lying-length", "chunked", "single-huge-chunk"])
async def test_actual_stream_byte_cap_precedes_json_model_parsing(atomic_db, monkeypatch, framing):
    db = atomic_db
    before = await preferences(db)
    db.events.clear()
    loads = dashboard.json.loads
    parsed = []
    buffer_sizes = []

    class ObservedBuffer(bytearray):
        def extend(self, value):
            super().extend(value)
            buffer_sizes.append(len(self))
            assert len(self) <= 16_385, "Route accumulated an unbounded body"

    monkeypatch.setattr(dashboard, "bytearray", ObservedBuffer, raising=False)

    def bounded_loads(value, *args, **kwargs):
        if isinstance(value, bytearray):
            parsed.append(len(value))
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(dashboard.json, "loads", bounded_loads)
    consumed = []
    body = json.dumps(layout(db.ws_id)).encode()
    payload = body + b" " * (16_385 - len(body))

    async def chunks():
        if framing == "single-huge-chunk":
            consumed.append("huge")
            yield payload + b" " * 100_000
        else:
            consumed.append("limit")
            yield payload[:16_384]
            consumed.append("excess")
            yield payload[16_384:]
        consumed.append("must-not-read")
        raise AssertionError("Route consumed bytes after discovering overflow")

    headers = {**db.headers, "Content-Type": "application/json"}
    if framing == "lying-length":
        headers["Content-Length"] = "1"
    elif framing == "chunked":
        headers["Transfer-Encoding"] = "chunked"
    request = db.client.build_request("PUT", f"/api/dashboard/layouts/{db.ws_id}",
                                     content=chunks(), headers=headers)
    if framing == "missing-length":
        request.headers.pop("Content-Length", None)
        request.headers.pop("Transfer-Encoding", None)
    response = await db.client.send(request)
    assert response.status_code == 413, response.text
    assert parsed == []
    assert buffer_sizes and max(buffer_sizes) == 16_385
    assert "must-not-read" not in consumed
    assert await preferences(db) == before
    assert_no_write(db)
    assert not role_events(db, kind="sql")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [16_384, 16_385])
async def test_exact_raw_byte_boundary(atomic_db, size):
    db = atomic_db
    body = json.dumps(layout(db.ws_id)).encode()
    payload = body + b" " * (size - len(body))
    response = await db.client.put(f"/api/dashboard/layouts/{db.ws_id}", content=payload,
                                   headers={**db.headers, "Content-Type": "application/json"})
    assert response.status_code == (200 if size == 16_384 else 413), response.text
    if size == 16_384:
        assert response.json() == {"workspaceId": str(db.ws_id), "layout": layout(db.ws_id)}
        assert_transaction(db)
    else:
        assert_no_write(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"\xff", b"[" * 2000, b"{\"version\":NaN}"])
async def test_malformed_raw_json_is_closed_422(atomic_db, raw):
    db = atomic_db
    before = await preferences(db)
    response = await db.client.put(f"/api/dashboard/layouts/{db.ws_id}", content=raw,
                                   headers={**db.headers, "Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert await preferences(db) == before
    assert_no_write(db)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["sql-null", "json-null", "root-list", "root-string", "root-bool",
                                    "map-null", "map-list", "map-string", "map-bool", "missing-map"])
async def test_locked_sql_null_json_null_and_document_shapes(atomic_db, initial):
    db = atomic_db
    value = {
        "sql-null": null(), "json-null": JSON.NULL, "root-list": [], "root-string": "wrong",
        "root-bool": False, "map-null": {"dashboard_layouts": None},
        "map-list": {"dashboard_layouts": []}, "map-string": {"dashboard_layouts": "wrong"},
        "map-bool": {"dashboard_layouts": False}, "missing-map": {"keep": {"opaque": [1, None]}},
    }[initial]
    await set_preferences(db, value)
    before = await preferences(db)
    async with db.factory(info={"role": "inspect"}) as session:
        is_sql_null = (await session.execute(select(User.preferences.is_(None)).where(
            User.id == db.actor_id))).scalar_one()
    assert is_sql_null is (initial == "sql-null")
    db.events.clear()
    response = await put(db)
    if initial in ("sql-null", "missing-map"):
        assert response.status_code == 200, response.text
        expected = {} if initial == "sql-null" else value
        assert isinstance(expected, dict)
        assert await preferences(db) == {**expected, "dashboard_layouts": {
            str(db.ws_id): layout(db.ws_id)}}
        assert_transaction(db)
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "PREFERENCES_SHAPE_CONFLICT"
        assert await preferences(db) == before
        assert_no_write(db, rollback=True)
    statements = role_events(db, kind="sql")
    assert statements[1]["sql"] == "BEGIN IMMEDIATE"
    user_read = [entry for entry in statements if "preferences IS NULL" in entry["sql"]]
    assert len(user_read) == 1
    assert db.events.index(statements[1]) < db.events.index(user_read[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("malformation", [
    "null", "list", "string", "future", "version-bool", "version-string", "missing-version",
    "missing-workspace", "wrong-workspace", "noncanonical-workspace", "missing-items", "items-null",
    "order-bool", "order-true", "order-float", "visible-int", "unknown-id", "duplicate-id", "duplicate-order",
    "order-gap", "no-visible", "item-extra", "over-64",
])
async def test_locked_present_target_conflicts_without_downgrade(atomic_db, malformation):
    db = atomic_db
    target = layout(db.ws_id)
    if malformation in ("null", "list", "string"):
        target = {"null": None, "list": [], "string": "wrong"}[malformation]
    elif malformation == "future":
        target["version"] = 2
    elif malformation == "version-bool":
        target["version"] = True
    elif malformation == "version-string":
        target["version"] = "1"
    elif malformation.startswith("missing-"):
        field = malformation.removeprefix("missing-")
        del target["workspaceId" if field == "workspace" else field]
    elif malformation == "wrong-workspace":
        target["workspaceId"] = str(db.second_id)
    elif malformation == "noncanonical-workspace":
        target["workspaceId"] = db.ws_id.hex
    elif malformation == "items-null":
        target["items"] = None
    elif malformation == "order-bool":
        target["items"][0]["order"] = False
    elif malformation == "order-true":
        target["items"].append({"id": "goals-progress", "visible": True, "order": True})
    elif malformation == "order-float":
        target["items"][0]["order"] = 0.0
    elif malformation == "visible-int":
        target["items"][0]["visible"] = 1
    elif malformation == "unknown-id":
        target["items"][0]["id"] = "future-widget"
    elif malformation == "duplicate-id":
        target["items"].append({"id": "hero-balance", "visible": True, "order": 1})
    elif malformation == "duplicate-order":
        target["items"].append({"id": "goals-progress", "visible": True, "order": 0})
    elif malformation == "order-gap":
        target["items"][0]["order"] = 1
    elif malformation == "no-visible":
        target["items"][0]["visible"] = False
    elif malformation == "item-extra":
        target["items"][0]["future"] = True
    else:
        target["items"] = [{"id": "hero-balance", "visible": True, "order": i}
                           for i in range(65)]
    before = {"language": "en", "dashboard_layouts": {
        str(db.ws_id): target, str(db.second_id): {"opaque-sibling": [None, False]},
    }}
    await set_preferences(db, before)
    response = await put(db)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "LAYOUT_TARGET_CONFLICT"
    assert await preferences(db) == before
    assert_no_write(db, rollback=True)
    statements = role_events(db, kind="sql")
    assert statements[1]["sql"] == "BEGIN IMMEDIATE"
    assert any("preferences IS NULL" in entry["sql"] for entry in statements[2:])


@pytest.mark.asyncio
async def test_stored_target_extras_and_opaque_siblings_survive_reserved_replacement(atomic_db):
    db = atomic_db
    target = {**layout(db.ws_id), "future-metadata": {"opaque": [None, 1, False]}, "flag": None}
    sibling = ["opaque", {"future": 999}]
    before = dict[str, Any]({"language": "ja", "unknown": {"keep": [1, None]}, "dashboard_layouts": {
        str(db.ws_id): target, str(db.second_id): sibling,
    }})
    await set_preferences(db, before)
    submitted = layout(db.ws_id, "goals-progress")
    response = await put(db, submitted)
    assert response.status_code == 200, response.text
    assert response.json() == {"workspaceId": str(db.ws_id), "layout": submitted}
    assert await preferences(db) == {
        **before, "dashboard_layouts": {
            str(db.ws_id): {**target, **submitted}, str(db.second_id): sibling,
        },
    }
    assert before["dashboard_layouts"][str(db.ws_id)]["items"][0]["id"] == "hero-balance"
    assert_transaction(db)


def gated_factory(db):
    first_current = asyncio.Event()
    second_attempt = asyncio.Event()
    release_first = asyncio.Event()
    sessions = []

    class GateSession(AsyncSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sequence = len(sessions)
            sessions.append(self)

        async def execute(self, statement, *args, **kwargs):
            sql = str(statement)
            if self.sequence == 1 and sql == "BEGIN IMMEDIATE":
                second_attempt.set()
            result = await super().execute(statement, *args, **kwargs)
            if self.sequence == 0 and getattr(statement, "is_select", False) and (
                "preferences IS NULL" in sql
            ):
                first_current.set()
                await asyncio.wait_for(release_first.wait(), timeout=3)
            return result

    factory = async_sessionmaker(
        db.engine, class_=GateSession, sync_session_class=db.proof_session,
        expire_on_commit=False, info={"role": "service"},
    )
    return SimpleNamespace(factory=factory, first_current=first_current,
                           second_attempt=second_attempt, release_first=release_first)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
async def test_independent_connection_serialization_preserves_current_and_last_committer(
    atomic_db, same_key, reverse
):
    db = atomic_db
    sibling_id = str(uuid.uuid4())
    before: dict[str, Any] = {"language": "en", "unknown": {"keep": [1, None]},
              "dashboard_layouts": {sibling_id: {"future-opaque": [False, None]}}}
    await set_preferences(db, before)
    gate = gated_factory(db)

    def provider():
        db.bindings["factory"] += 1
        return gate.factory

    app.dependency_overrides[dashboard.get_dashboard_layout_session_factory] = provider
    first_ws, second_ws = (db.second_id, db.ws_id) if reverse else (db.ws_id, db.second_id)
    if same_key:
        first_ws = second_ws = db.ws_id
    first_widget, second_widget = ("goals-progress", "hero-balance") if reverse else (
        "hero-balance", "goals-progress"
    )
    first_body, second_body = layout(first_ws, first_widget), layout(second_ws, second_widget)
    first = asyncio.create_task(put(db, first_body, workspace_id=first_ws))
    second = None
    try:
        await asyncio.wait_for(gate.first_current.wait(), timeout=3)
        second = asyncio.create_task(put(db, second_body, workspace_id=second_ws))
        await asyncio.wait_for(gate.second_attempt.wait(), timeout=3)
        assert not second.done(), "Second writer did not wait for the held reservation"
        gate.release_first.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=4)
    finally:
        gate.release_first.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None),
                             return_exceptions=True)
    assert [response.status_code for response in responses] == [200, 200], [
        response.text for response in responses
    ]
    assert responses[0].json() == {"workspaceId": str(first_ws), "layout": first_body}
    assert responses[1].json() == {"workspaceId": str(second_ws), "layout": second_body}
    expected = copy.deepcopy(before)
    expected["dashboard_layouts"][str(first_ws)] = first_body
    expected["dashboard_layouts"][str(second_ws)] = second_body
    assert await preferences(db) == expected
    assert await preferences(db, db.other_id) == {"other": True}
    assert db.bindings == {"auth": 2, "factory": 2}
    service_sql = role_events(db, kind="sql")
    begins = [entry for entry in service_sql if entry["sql"] == "BEGIN IMMEDIATE"]
    assert len(begins) == 2
    assert begins[0]["dbapi_id"] != begins[1]["dbapi_id"]
    assert begins[0]["connection"] != begins[1]["connection"]
    for begin in begins:
        connection_sql = [entry for entry in service_sql
                          if entry["connection"] == begin["connection"]]
        assert connection_sql[0]["sql"] == "PRAGMA busy_timeout = 1000"
        assert connection_sql[1] == begin
        assert len([entry for entry in connection_sql if entry["sql"].startswith("UPDATE")]) == 1
    for kind in ("assignment", "flush", "commit", "commit_call", "commit_done"):
        assert len(role_events(db, kind=kind)) == 2
    assert not role_events(db, kind="rollback")
    assert not role_events(db, role="auth", kind="commit")
    current_reads = [entry for entry in service_sql if "preferences IS NULL" in entry["sql"]]
    assert len(current_reads) == 2
    assert db.events.index(role_events(db, kind="commit")[0]) < db.events.index(current_reads[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["future-target", "null-map", "unrelated-root"])
async def test_shape_and_merge_decision_reads_current_only_after_lock(atomic_db, changed):
    db = atomic_db
    before: dict[str, Any] = {"language": "en", "dashboard_layouts": {str(db.ws_id): layout(db.ws_id)}}
    await set_preferences(db, before)
    current = copy.deepcopy(before)
    if changed == "future-target":
        current["dashboard_layouts"][str(db.ws_id)]["version"] = 2
    elif changed == "null-map":
        current["dashboard_layouts"] = None
    else:
        current["just-committed"] = {"keep": [False, None]}
    attempt = asyncio.Event()

    class AttemptSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            if str(statement) == "BEGIN IMMEDIATE":
                attempt.set()
            return await super().execute(statement, *args, **kwargs)

    factory = async_sessionmaker(db.engine, class_=AttemptSession,
                                 sync_session_class=db.proof_session,
                                 expire_on_commit=False, info={"role": "service"})
    app.dependency_overrides[dashboard.get_dashboard_layout_session_factory] = lambda: factory
    async with db.factory(info={"role": "blocker"}) as blocker:
        await blocker.execute(text("BEGIN IMMEDIATE"))
        await blocker.execute(update(User).where(User.id == db.actor_id).values(preferences=current))
        task = asyncio.create_task(put(db, layout(db.ws_id, "goals-progress")))
        try:
            await asyncio.wait_for(attempt.wait(), timeout=3)
            assert not task.done()
            await blocker.commit()
            response = await asyncio.wait_for(task, timeout=3)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    if changed == "unrelated-root":
        assert response.status_code == 200, response.text
        assert await preferences(db) == {**current, "dashboard_layouts": {
            str(db.ws_id): layout(db.ws_id, "goals-progress")}}
        assert_transaction(db)
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "LAYOUT_TARGET_CONFLICT" if changed == "future-target" else "PREFERENCES_SHAPE_CONFLICT"
        )
        assert await preferences(db) == current
        assert_no_write(db, rollback=True)
    assert role_events(db, kind="sql")[1]["sql"] == "BEGIN IMMEDIATE"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["route", "service"])
@pytest.mark.parametrize("atomic_db", [None, "shared-cache"], indirect=True)
async def test_real_sqlite_busy_or_locked_is_bounded_503_rollback_without_retry(atomic_db, surface):
    db = atomic_db
    before = await preferences(db)
    async with db.engine.connect() as blocker:
        blocker.sync_connection.info["role"] = "blocker"
        await blocker.exec_driver_sql("BEGIN IMMEDIATE")
        db.events.clear()
        started = time.monotonic()
        if surface == "route":
            response = await put(db)
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == "PREFERENCES_STORAGE_UNAVAILABLE"
        else:
            with pytest.raises(HTTPException) as caught:
                await service_save(db)
            assert caught.value.status_code == 503
            assert caught.value.detail == "PREFERENCES_STORAGE_UNAVAILABLE"
            cause = caught.value.__cause__
            assert isinstance(cause, layout_service.OperationalError)
            assert isinstance(cause.orig, layout_service.sqlite3.OperationalError)
            assert isinstance(cause.orig.sqlite_errorcode, int)
            expected_code = (layout_service.sqlite3.SQLITE_LOCKED if db.cache_mode
                             else layout_service.sqlite3.SQLITE_BUSY)
            assert cause.orig.sqlite_errorcode & 0xFF == expected_code
        elapsed = time.monotonic() - started
        assert elapsed < 3, f"Busy wait was not bounded: {elapsed} seconds"
        await blocker.rollback()
    assert await preferences(db) == before
    assert_no_write(db, rollback=True)
    statements = role_events(db, kind="sql")
    assert [entry["sql"] for entry in statements] == [
        "PRAGMA busy_timeout = 1000", "BEGIN IMMEDIATE",
    ]
    assert len({entry["connection"] for entry in statements}) == 1


@pytest.mark.asyncio
async def test_service_factory_is_required_not_ambient(atomic_db):
    db = atomic_db
    before = await preferences(db)
    with pytest.raises(TypeError, match="session_factory"):
        # Omission is the negative fixture: the required factory must raise TypeError.
        await layout_service.save_dashboard_layout(  # ty: ignore[missing-argument]
            actor_id=db.actor_id, workspace_id=db.ws_id,
            layout=DashboardLayoutWrite.model_validate(layout(db.ws_id)),
        )
    assert await preferences(db) == before
    assert_no_write(db)


@pytest.mark.asyncio
async def test_unsupported_dialect_is_closed_without_database_calls(atomic_db):
    calls = []

    class UnsupportedSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            calls.append("close")

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="unsupported"))

        async def rollback(self):
            calls.append("rollback")

        async def execute(self, *args, **kwargs):
            raise AssertionError("Unsupported dialect must never read/write")

        async def commit(self):
            raise AssertionError("Unsupported dialect must never commit")

    with pytest.raises(HTTPException) as caught:
        await service_save(atomic_db, factory=UnsupportedSession)
    assert caught.value.status_code == 503
    assert caught.value.detail == "PREFERENCES_STORAGE_UNAVAILABLE"
    assert calls == ["rollback", "close"]
    assert_no_write(atomic_db)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["after-flush", "before-commit", "after-commit"])
async def test_flush_commit_failures_never_emit_success_or_blind_retry(atomic_db, monkeypatch, fault):
    db = atomic_db
    before = await preferences(db)
    db.events.clear()
    calls = []

    class UnknownOutcome(RuntimeError):
        pass

    class FaultSession(AsyncSession):
        async def flush(self, *args, **kwargs):
            calls.append("flush")
            await super().flush(*args, **kwargs)
            if fault == "after-flush":
                raise UnknownOutcome("Synthetic failure after real flush")

        async def commit(self):
            calls.append("commit")
            if fault == "before-commit":
                raise UnknownOutcome("Synthetic failure before real commit")
            await super().commit()
            raise UnknownOutcome("Synthetic failure after real commit; recheck required")

        async def rollback(self):
            calls.append("rollback")
            await super().rollback()

    factory = async_sessionmaker(db.engine, class_=FaultSession,
                                 sync_session_class=db.proof_session,
                                 expire_on_commit=False, info={"role": "service"})

    def forbidden_response(**kwargs):
        raise AssertionError("A failed operation must not construct a committed response")

    monkeypatch.setattr(layout_service, "CommittedDashboardLayout", forbidden_response)
    with pytest.raises(UnknownOutcome):
        await service_save(db, factory=factory)
    assert calls == (["flush", "rollback"] if fault == "after-flush"
                     else ["flush", "commit", "rollback"])
    assert len(role_events(db, kind="assignment")) == 1
    assert len(role_events(db, kind="flush")) == 1
    assert len([entry for entry in role_events(db, kind="sql")
                if entry["sql"].startswith("UPDATE")]) == 1
    if fault == "after-commit":
        assert len(role_events(db, kind="commit_done")) == 1
        assert await preferences(db) == {**before, "dashboard_layouts": {
            str(db.ws_id): layout(db.ws_id)}}
        # Rollback was attempted, but cannot undo an already committed write.
        assert not role_events(db, kind="rollback")
    else:
        assert not role_events(db, kind="commit_done")
        assert len(role_events(db, kind="rollback")) == 1
        assert await preferences(db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("expires", [False, True])
async def test_typed_scoped_response_constructed_only_after_real_commit(atomic_db, monkeypatch, expires):
    db = atomic_db
    constructor = layout_service.CommittedDashboardLayout
    calls = []
    factory = async_sessionmaker(db.engine, class_=AsyncSession,
                                 sync_session_class=db.proof_session,
                                 expire_on_commit=expires, info={"role": "service"})

    def after_commit_response(**kwargs):
        assert len(role_events(db, kind="commit_done")) == 1
        assert kwargs == {"workspaceId": str(db.ws_id),
                          "layout": DashboardLayoutWrite.model_validate(layout(db.ws_id))}
        calls.append("response")
        return constructor(**kwargs)

    monkeypatch.setattr(layout_service, "CommittedDashboardLayout", after_commit_response)
    response = await service_save(db, factory=factory)
    assert isinstance(response, constructor)
    assert response.model_dump() == {"workspaceId": str(db.ws_id), "layout": layout(db.ws_id)}
    assert calls == ["response"]
    statements = role_events(db, kind="sql")
    assert statements[-1]["sql"].startswith("UPDATE users SET preferences=")
    assert len(role_events(db, kind="commit")) == 1
    assert not role_events(db, role="auth", kind="sql")


@pytest.mark.asyncio
async def test_actor_nonpreference_columns_and_other_actor_are_unchanged(atomic_db):
    db = atomic_db

    async def actors():
        async with db.factory(info={"role": "inspect"}) as session:
            rows = (await session.execute(select(
                User.id, User.email, User.hashed_password, User.is_active, User.is_verified,
                User.is_superuser, User.oidc_issuer, User.oidc_subject,
            ).order_by(User.id))).all()
            return [tuple(row) for row in rows]

    before = await actors()
    db.events.clear()
    response = await put(db)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"workspaceId", "layout"}
    assert set(response.json()["layout"]) == {"version", "workspaceId", "items"}
    assert await actors() == before
    assert await preferences(db, db.other_id) == {"other": True}
    assert_transaction(db)
