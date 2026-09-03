import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
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
        json={
            "name": "Educação",
            "icon": "📚",
            "color": "#9333EA",
            "is_hidden": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Educação"
    assert data["icon"] == "📚"
    assert data["is_system"] is False
    assert data["is_hidden"] is False


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
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    monkeypatch,
):
    """A category still referenced by another row answers 409, not 500."""
    category = Category(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Referenced",
    )
    session.add(category)
    await session.commit()
    category_id = category.id

    async def override_test_session():
        yield session

    # The suite runs on SQLite, which does not enforce foreign keys, so raise
    # the IntegrityError Postgres would raise for a still-referenced category.
    async def fail_commit():
        raise IntegrityError("DELETE FROM categories", {}, Exception("FK violation"))

    try:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setitem(
                app.dependency_overrides,
                get_async_session,
                override_test_session,
            )
            scoped_patch.setattr(session, "commit", fail_commit)
            response = await client.delete(
                f"/api/categories/{category_id}",
                headers=auth_headers,
            )

        assert response.status_code == 409
        assert response.json() == {
            "detail": (
                "Category is still in use and cannot be deleted. "
                "Remove its references first."
            )
        }
    finally:
        await session.rollback()

    # The failed delete rolled back, so the category is still listed.
    categories = await client.get("/api/categories", headers=auth_headers)
    assert str(category_id) in {item["id"] for item in categories.json()}


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
async def test_hide_system_category_filters_default_list(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    cat_id = str(test_categories[0].id)

    update_response = await client.patch(
        f"/api/categories/{cat_id}",
        headers=auth_headers,
        json={"is_hidden": True},
    )
    assert update_response.status_code == 200
    assert update_response.json()["is_hidden"] is True

    response = await client.get("/api/categories", headers=auth_headers)
    assert response.status_code == 200
    assert cat_id not in {category["id"] for category in response.json()}

    include_hidden_response = await client.get(
        "/api/categories?include_hidden=true",
        headers=auth_headers,
    )
    assert include_hidden_response.status_code == 200
    hidden_category = next(
        category for category in include_hidden_response.json() if category["id"] == cat_id
    )
    assert hidden_category["is_hidden"] is True


@pytest.mark.asyncio
async def test_cannot_hide_user_category(client: AsyncClient, auth_headers):
    create_response = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": "Custom"},
    )
    assert create_response.status_code == 201
    category_id = create_response.json()["id"]

    update_response = await client.patch(
        f"/api/categories/{category_id}",
        headers=auth_headers,
        json={"is_hidden": True},
    )

    assert update_response.status_code == 400
    assert update_response.json()["detail"] == "Only system categories can be hidden"

    unhide_response = await client.patch(
        f"/api/categories/{category_id}",
        headers=auth_headers,
        json={"is_hidden": False},
    )
    assert unhide_response.status_code == 200
    assert unhide_response.json()["is_hidden"] is False

    list_response = await client.get(
        "/api/categories?include_hidden=true",
        headers=auth_headers,
    )
    category = next(item for item in list_response.json() if item["id"] == category_id)
    assert category["is_hidden"] is False


@pytest.mark.asyncio
async def test_categories_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/categories")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_hidden_category_is_not_assigned_by_rules(
    client: AsyncClient, auth_headers, test_categories: list[Category], test_account
):
    """A category the user retired must not come back through a rule."""
    cat_id = str(test_categories[0].id)
    rule_response = await client.post(
        "/api/rules",
        headers=auth_headers,
        json={
            "name": "Delivery",
            "conditions_op": "or",
            "conditions": [
                {"field": "description", "op": "contains", "value": "IFOOD"}
            ],
            "actions": [{"op": "set_category", "value": cat_id}],
            "priority": 0,
        },
    )
    assert rule_response.status_code == 201

    payload = {
        "account_id": str(test_account.id),
        "description": "IFOOD PEDIDO",
        "amount": -42.0,
        "date": "2026-08-24",
        "type": "expense",
    }

    matched = await client.post("/api/transactions", headers=auth_headers, json=payload)
    assert matched.status_code == 201
    assert matched.json()["category_id"] == cat_id

    hide = await client.patch(
        f"/api/categories/{cat_id}", headers=auth_headers, json={"is_hidden": True}
    )
    assert hide.status_code == 200

    after_hiding = await client.post(
        "/api/transactions", headers=auth_headers, json=payload
    )
    assert after_hiding.status_code == 201
    assert after_hiding.json()["category_id"] is None


@pytest.mark.asyncio
async def test_rule_usage_lists_rules_assigning_the_category(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    cat_id = str(test_categories[0].id)
    other_id = str(test_categories[1].id)

    for name, target in [("Food rule", cat_id), ("Transport rule", other_id)]:
        response = await client.post(
            "/api/rules",
            headers=auth_headers,
            json={
                "name": name,
                "conditions_op": "or",
                "conditions": [
                    {"field": "description", "op": "contains", "value": name}
                ],
                "actions": [{"op": "set_category", "value": target}],
                "priority": 0,
            },
        )
        assert response.status_code == 201

    usage = await client.get(
        f"/api/categories/{cat_id}/rule-usage", headers=auth_headers
    )
    assert usage.status_code == 200
    assert [rule["name"] for rule in usage.json()["rules"]] == ["Food rule"]


@pytest.mark.asyncio
async def test_hiding_can_deactivate_the_rules_that_assign_the_category(
    client: AsyncClient, auth_headers, test_categories: list[Category]
):
    cat_id = str(test_categories[0].id)
    create = await client.post(
        "/api/rules",
        headers=auth_headers,
        json={
            "name": "Food rule",
            "conditions_op": "or",
            "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
            "actions": [{"op": "set_category", "value": cat_id}],
            "priority": 0,
        },
    )
    assert create.status_code == 201
    rule_id = create.json()["id"]

    hide = await client.patch(
        f"/api/categories/{cat_id}?deactivate_rules=true",
        headers=auth_headers,
        json={"is_hidden": True},
    )
    assert hide.status_code == 200

    rules = await client.get("/api/rules", headers=auth_headers)
    rule = next(item for item in rules.json() if item["id"] == rule_id)
    assert rule["is_active"] is False

    # Hiding without the flag leaves rules alone.
    other_id = str(test_categories[1].id)
    create_other = await client.post(
        "/api/rules",
        headers=auth_headers,
        json={
            "name": "Transport rule",
            "conditions_op": "or",
            "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
            "actions": [{"op": "set_category", "value": other_id}],
            "priority": 0,
        },
    )
    assert create_other.status_code == 201
    await client.patch(
        f"/api/categories/{other_id}", headers=auth_headers, json={"is_hidden": True}
    )
    rules = await client.get("/api/rules", headers=auth_headers)
    untouched = next(
        item for item in rules.json() if item["id"] == create_other.json()["id"]
    )
    assert untouched["is_active"] is True
