from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.database import get_async_session
from app.core.workspace_context import current_writable_workspace
from app.main import app

from app.models.category import Category
from app.models.user import User
from app.services import category_service


@pytest.mark.asyncio
async def test_list_categories_empty(client: AsyncClient, auth_headers):
    """Listing categories with no data should return an empty list."""
    response = await client.get("/api/categories", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_categories_with_defaults(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User
):
    """After creating default categories (as registration does), listing returns them."""
    await category_service.create_default_categories(session, test_user.id, "pt-BR")

    response = await client.get("/api/categories", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 16
    names = {c["name"] for c in data}
    assert "Alimentação" in names
    assert "Transporte" in names
    assert "Outros" in names
    assert "Investimentos" in names


@pytest.mark.asyncio
async def test_list_categories_with_existing(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    response = await client.get("/api/categories", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


@pytest.mark.asyncio
async def test_create_category(client: AsyncClient, auth_headers, test_categories):
    response = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Educação", "icon": "📚", "color": "#9333EA"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Educação"
    assert data["icon"] == "📚"
    assert data["is_system"] is False


@pytest.mark.asyncio
async def test_update_category(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    cat_id = str(test_categories[0].id)
    response = await client.patch(
        f"/api/categories/{cat_id}",
        headers=auth_headers,
        json={"name": "Comida", "color": "#FF0000"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Comida"
    assert data["color"] == "#FF0000"
    assert data["icon"] == "🍔"  # unchanged


@pytest.mark.asyncio
async def test_update_category_not_found(client: AsyncClient, auth_headers, test_categories):
    response = await client.patch(
        "/api/categories/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
        json={"name": "Nope"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_category(client: AsyncClient, auth_headers, test_categories):
    # Create a non-system category first
    create_resp = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Temp"},
    )
    cat_id = create_resp.json()["id"]

    response = await client.delete(f"/api/categories/{cat_id}", headers=auth_headers)
    assert response.status_code == 204

    categories = await client.get("/api/categories", headers=auth_headers)
    assert cat_id not in {category["id"] for category in categories.json()}


@pytest.mark.asyncio
async def test_delete_referenced_category_returns_conflict(
    client: AsyncClient,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    fk_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    fk_session_factory = async_sessionmaker(
        fk_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    user_table = User.__table__
    category_table = Category.__table__
    assert isinstance(user_table, Table)
    assert isinstance(category_table, Table)

    try:
        async with fk_engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(user_table.create)
            await connection.exec_driver_sql(
                "CREATE TABLE workspaces (id CHAR(32) PRIMARY KEY)"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE category_groups (id CHAR(32) PRIMARY KEY)"
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO users (
                    id,
                    email,
                    hashed_password,
                    is_active,
                    is_superuser,
                    is_verified,
                    is_2fa_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_user.id.hex,
                    test_user.email,
                    test_user.hashed_password,
                    1,
                    0,
                    1,
                    0,
                ),
            )
            await connection.exec_driver_sql(
                "INSERT INTO workspaces (id) VALUES (?)",
                (test_workspace.id.hex,),
            )
            await connection.run_sync(category_table.create)
            await connection.exec_driver_sql(
                """
                CREATE TABLE transactions (
                    id CHAR(32) PRIMARY KEY,
                    category_id CHAR(32) NOT NULL REFERENCES categories(id)
                )
                """
            )

        async with fk_session_factory() as session:
            category = Category(
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                name="Referenced",
            )
            session.add(category)
            await session.commit()
            category_id = category.id
            transaction_id = "11111111111111111111111111111111"
            await session.execute(
                text(
                    "INSERT INTO transactions (id, category_id) "
                    "VALUES (:id, :category_id)"
                ),
                {"id": transaction_id, "category_id": category_id.hex},
            )
            await session.commit()

            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []

            async def override_test_session():
                yield session

            async def override_writable_workspace():
                return SimpleNamespace(
                    workspace=SimpleNamespace(id=test_workspace.id),
                    user_id=test_user.id,
                )

            with monkeypatch.context() as scoped_patch:
                scoped_patch.setitem(
                    app.dependency_overrides,
                    get_async_session,
                    override_test_session,
                )
                scoped_patch.setitem(
                    app.dependency_overrides,
                    current_writable_workspace,
                    override_writable_workspace,
                )
                response = await client.delete(f"/api/categories/{category_id}")

            assert response.status_code == 409
            assert response.json() == {
                "detail": (
                    "Category is still in use and cannot be deleted. "
                    "Remove its references first."
                )
            }

            category_result = await session.execute(
                select(Category).where(Category.id == category_id)
            )
            assert category_result.scalar_one_or_none() is not None
            referenced_category_id = (
                await session.execute(
                    text(
                        "SELECT category_id FROM transactions "
                        "WHERE id = :transaction_id"
                    ),
                    {"transaction_id": transaction_id},
                )
            ).scalar_one()
            assert referenced_category_id == category_id.hex

            recovery_category = Category(
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                name="Created after failed delete",
            )
            session.add(recovery_category)
            await session.commit()
            recovery_result = await session.execute(
                select(Category).where(Category.id == recovery_category.id)
            )
            assert recovery_result.scalar_one_or_none() is not None
    finally:
        await fk_engine.dispose()


@pytest.mark.asyncio
async def test_delete_category_does_not_translate_unrelated_errors(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    category = Category(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Unrelated failure",
    )
    session.add(category)
    await session.commit()

    async def override_test_session():
        yield session

    async def fail_commit():
        raise RuntimeError("unrelated deletion failure")

    try:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setitem(
                app.dependency_overrides,
                get_async_session,
                override_test_session,
            )
            scoped_patch.setattr(session, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="unrelated deletion failure"):
                await client.delete(
                    f"/api/categories/{category.id}",
                    headers=auth_headers,
                )
    finally:
        await session.rollback()


@pytest.mark.asyncio
async def test_delete_system_category_fails(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    cat_id = str(test_categories[0].id)  # system category
    response = await client.delete(f"/api/categories/{cat_id}", headers=auth_headers)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_categories_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/categories")
    assert response.status_code == 401
