# backend/tests/test_new_rules_api.py
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services.category_service import create_default_categories
from app.services.rule_service import create_default_rules, create_rule



@pytest.mark.asyncio
async def test_list_rules_empty(client: AsyncClient, auth_headers, test_categories):
    """Listing rules with no data should return an empty list."""
    response = await client.get("/api/rules", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_rules_with_defaults(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User
):
    """After creating default categories and rules (as registration does), listing returns them."""
    await create_default_categories(session, test_user.id, "pt-BR")
    await create_default_rules(session, test_user.id, "pt-BR")

    response = await client.get("/api/rules", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3
    # Each rule has the expected structure
    rule = data[0]
    assert "conditions" in rule
    assert "actions" in rule
    assert "conditions_op" in rule


@pytest.mark.asyncio
async def test_create_rule(client: AsyncClient, auth_headers, test_categories):
    cat_id = str(test_categories[0].id)
    payload = {
        "name": "Test Rule",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
        "actions": [{"op": "set_category", "value": cat_id}],
        "priority": 5,
        "is_active": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Rule"
    assert data["conditions"][0]["value"] == "IFOOD"
    assert "applied_count" in data


@pytest.mark.asyncio
async def test_create_rule_applies_to_existing_transactions(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Creating a rule immediately applies it to existing transactions and
    reports how many were affected, without needing apply-all.

    NETFLIX starts uncategorised in the fixture, so the new rule categorises it.
    """
    cat_food = str(test_categories[0].id)
    payload = {
        "name": "Netflix",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 5,
        "is_active": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["applied_count"] >= 1

    # The matching transaction is categorised without an explicit apply-all call.
    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    netflix = {t["description"]: t for t in items}.get("NETFLIX")
    assert netflix is not None
    assert netflix["category_id"] == cat_food


@pytest.mark.asyncio
async def test_create_rule_does_not_overwrite_existing_category(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Auto-apply on create is non-destructive: an already-categorised
    transaction keeps its category instead of being clobbered."""
    original = str(test_categories[0].id)  # IFOOD RESTAURANTE is pre-set to this
    other = str(test_categories[1].id)
    payload = {
        "name": "iFood recat",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
        "actions": [{"op": "set_category", "value": other}],
        "priority": 5,
        "is_active": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["applied_count"] == 0

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    ifood = {t["description"]: t for t in items}.get("IFOOD RESTAURANTE")

    assert ifood is not None
    assert ifood["category_id"] == original


@pytest.mark.asyncio
async def test_create_rule_can_overwrite_existing_category_when_requested(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    original = str(test_categories[0].id)  # IFOOD RESTAURANTE is pre-set to this
    other = str(test_categories[1].id)
    payload = {
        "name": "iFood overwrite",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
        "actions": [{"op": "set_category", "value": other}],
        "priority": 5,
        "is_active": True,
        "overwrite_existing_categories": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["applied_count"] >= 1

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    ifood = {t["description"]: t for t in items}.get("IFOOD RESTAURANTE")

    assert ifood is not None
    assert ifood["category_id"] != original
    assert ifood["category_id"] == other


@pytest.mark.asyncio
async def test_create_rule_no_match_reports_zero(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """A rule that matches nothing reports applied_count == 0."""
    payload = {
        "name": "No match",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "ZZZ_NOMATCH"}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
        "priority": 5,
        "is_active": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["applied_count"] == 0


@pytest.mark.asyncio
async def test_create_rule_rejects_unsafe_regex_before_persistence_or_history(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    before_items = (
        await client.get("/api/transactions", headers=auth_headers)
    ).json()["items"]
    before_categories = {item["id"]: item["category_id"] for item in before_items}
    target_category = str(test_categories[1].id)
    rule_name = "Unsafe regex create"

    response = await client.post(
        "/api/rules",
        json={
            "name": rule_name,
            "conditions_op": "and",
            "conditions": [
                {"field": "description", "op": "regex", "value": "foo|"}
            ],
            "actions": [{"op": "set_category", "value": target_category}],
            "priority": 5,
            "is_active": True,
            "apply_to_existing": True,
        },
        headers=auth_headers,
    )

    persisted_rules = [
        rule
        for rule in (await client.get("/api/rules", headers=auth_headers)).json()
        if rule["name"] == rule_name
    ]
    after_items = (
        await client.get("/api/transactions", headers=auth_headers)
    ).json()["items"]
    changed_transactions = {
        item["description"]: {
            "before_category_id": before_categories[item["id"]],
            "after_category_id": item["category_id"],
        }
        for item in after_items
        if item["category_id"] != before_categories[item["id"]]
    }

    assert {
        "status": response.status_code,
        "detail": response.json().get("detail"),
        "applied_count": response.json().get("applied_count"),
        "persisted_conditions": [
            rule["conditions"] for rule in persisted_rules
        ],
        "changed_transactions": changed_transactions,
    } == {
        "status": 400,
        "detail": "Regular expression must not match an empty string",
        "applied_count": None,
        "persisted_conditions": [],
        "changed_transactions": {},
    }


@pytest.mark.asyncio
async def test_create_rule_rejects_malformed_regex(
    client: AsyncClient, auth_headers, test_categories
):
    rule_name = "Malformed regex create"
    response = await client.post(
        "/api/rules",
        json={
            "name": rule_name,
            "conditions": [
                {"field": "description", "op": "regex", "value": "["}
            ],
            "actions": [
                {"op": "set_category", "value": str(test_categories[0].id)}
            ],
            "is_active": True,
        },
        headers=auth_headers,
    )
    persisted_names = {
        rule["name"]
        for rule in (await client.get("/api/rules", headers=auth_headers)).json()
    }

    assert (
        response.status_code,
        response.json().get("detail"),
        rule_name in persisted_names,
    ) == (400, "Invalid regular expression", False)


@pytest.mark.asyncio
async def test_update_rule(client: AsyncClient, auth_headers, test_rules):
    rule_id = str(test_rules[0].id)
    response = await client.patch(
        f"/api/rules/{rule_id}",
        json={"name": "Updated Name"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert "applied_count" in data


@pytest.mark.asyncio
async def test_update_rule_name_does_not_apply_to_existing_transactions(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_transactions,
    test_categories,
):
    cat_food = str(test_categories[0].id)
    rule = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Netflix direct",
            conditions_op="and",
            conditions=[RuleCondition(field="description", op="contains", value="NETFLIX")],
            actions=[RuleAction(op="set_category", value=cat_food)],
            priority=5,
            is_active=True,
        ),
    )

    response = await client.patch(
        f"/api/rules/{rule.id}",
        json={"name": "Netflix renamed"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["applied_count"] == 0

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    netflix = {t["description"]: t for t in items}.get("NETFLIX")
    assert netflix is not None
    assert netflix["category_id"] is None


@pytest.mark.asyncio
async def test_update_rule_applies_to_existing_transactions(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Updating a rule immediately applies the new definition to history.

    This covers adding a new merchant/pattern to an existing rule from a
    transaction detail modal without requiring an explicit apply-all call.
    """
    cat_food = str(test_categories[0].id)
    create_payload = {
        "name": "Streaming",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "ZZZ_NOMATCH"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 5,
        "is_active": True,
    }
    create_response = await client.post("/api/rules", json=create_payload, headers=auth_headers)
    assert create_response.status_code == 201
    assert create_response.json()["applied_count"] == 0

    rule_id = create_response.json()["id"]
    update_response = await client.patch(
        f"/api/rules/{rule_id}",
        json={"conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}]},
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["applied_count"] >= 1

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    netflix = {t["description"]: t for t in items}.get("NETFLIX")
    assert netflix is not None
    assert netflix["category_id"] == cat_food


@pytest.mark.asyncio
async def test_update_rule_rejects_unsafe_regex_atomically(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    create_response = await client.post(
        "/api/rules",
        json={
            "name": "Safe rule before unsafe update",
            "conditions_op": "and",
            "conditions": [
                {
                    "field": "description",
                    "op": "regex",
                    "value": "ZZZ_NOMATCH",
                }
            ],
            "actions": [
                {"op": "set_category", "value": str(test_categories[1].id)}
            ],
            "priority": 5,
            "is_active": True,
        },
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    rule_id = create_response.json()["id"]
    original_conditions = create_response.json()["conditions"]
    before_items = (
        await client.get("/api/transactions", headers=auth_headers)
    ).json()["items"]
    before_categories = {item["id"]: item["category_id"] for item in before_items}

    update_response = await client.patch(
        f"/api/rules/{rule_id}",
        json={
            "conditions": [
                {"field": "description", "op": "regex", "value": "foo|"}
            ]
        },
        headers=auth_headers,
    )

    persisted_rule = next(
        rule
        for rule in (await client.get("/api/rules", headers=auth_headers)).json()
        if rule["id"] == rule_id
    )
    after_items = (
        await client.get("/api/transactions", headers=auth_headers)
    ).json()["items"]
    changed_transactions = {
        item["description"]: {
            "before_category_id": before_categories[item["id"]],
            "after_category_id": item["category_id"],
        }
        for item in after_items
        if item["category_id"] != before_categories[item["id"]]
    }

    assert {
        "status": update_response.status_code,
        "detail": update_response.json().get("detail"),
        "applied_count": update_response.json().get("applied_count"),
        "persisted_conditions": persisted_rule["conditions"],
        "changed_transactions": changed_transactions,
    } == {
        "status": 400,
        "detail": "Regular expression must not match an empty string",
        "applied_count": None,
        "persisted_conditions": original_conditions,
        "changed_transactions": {},
    }


@pytest.mark.asyncio
async def test_update_rule_can_skip_existing_transactions(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    cat_food = str(test_categories[0].id)
    create_payload = {
        "name": "Streaming skip",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "ZZZ_NOMATCH"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 5,
        "is_active": True,
    }
    create_response = await client.post("/api/rules", json=create_payload, headers=auth_headers)
    assert create_response.status_code == 201

    update_response = await client.patch(
        f"/api/rules/{create_response.json()['id']}",
        json={
            "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
            "apply_to_existing": False,
        },
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["applied_count"] == 0

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    netflix = {t["description"]: t for t in items}.get("NETFLIX")
    assert netflix is not None
    assert netflix["category_id"] is None


@pytest.mark.asyncio
async def test_update_rule_can_overwrite_existing_category_when_requested(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    original = str(test_categories[0].id)  # IFOOD RESTAURANTE is pre-set to this
    other = str(test_categories[1].id)
    create_payload = {
        "name": "iFood update overwrite",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "ZZZ_NOMATCH"}],
        "actions": [{"op": "set_category", "value": other}],
        "priority": 5,
        "is_active": True,
    }
    create_response = await client.post("/api/rules", json=create_payload, headers=auth_headers)
    assert create_response.status_code == 201
    assert create_response.json()["applied_count"] == 0

    update_response = await client.patch(
        f"/api/rules/{create_response.json()['id']}",
        json={
            "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
            "overwrite_existing_categories": True,
        },
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["applied_count"] >= 1

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    ifood = {t["description"]: t for t in items}.get("IFOOD RESTAURANTE")

    assert ifood is not None
    assert ifood["category_id"] != original
    assert ifood["category_id"] == other


@pytest.mark.asyncio
async def test_update_rule_inactive_to_active_applies_to_existing_transactions(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    cat_food = str(test_categories[0].id)
    create_payload = {
        "name": "Inactive Netflix",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 5,
        "is_active": False,
    }
    create_response = await client.post("/api/rules", json=create_payload, headers=auth_headers)
    assert create_response.status_code == 201
    assert create_response.json()["applied_count"] == 0

    update_response = await client.patch(
        f"/api/rules/{create_response.json()['id']}",
        json={"is_active": True},
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["applied_count"] >= 1

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    netflix = {t["description"]: t for t in items}.get("NETFLIX")
    assert netflix is not None
    assert netflix["category_id"] == cat_food


@pytest.mark.asyncio
async def test_delete_rule(client: AsyncClient, auth_headers, test_rules):
    rule_id = str(test_rules[0].id)
    response = await client.delete(f"/api/rules/{rule_id}", headers=auth_headers)
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_apply_all_rules(client: AsyncClient, auth_headers, test_rules, test_transactions):
    response = await client.post("/api/rules/apply-all", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "applied" in data
    assert data["applied"] >= 3  # 3 of 5 transactions match rules


# --- Integration tests: category assignment, tags, priority, isolation ---


@pytest.mark.asyncio
async def test_apply_all_verifies_category_assignment(
    client: AsyncClient, auth_headers, test_rules, test_transactions, test_categories,
):
    """After apply-all, GET transactions and confirm correct category_id."""
    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["items"]

    # Build a lookup by description
    by_desc = {t["description"]: t for t in items}

    # UBER TRIP should be categorised as Transporte (test_categories[1])
    uber = by_desc.get("UBER TRIP")
    assert uber is not None
    assert uber["category_id"] == str(test_categories[1].id)

    # IFOOD RESTAURANTE -> Alimentação (test_categories[0])
    ifood = by_desc.get("IFOOD RESTAURANTE")
    assert ifood is not None
    assert ifood["category_id"] == str(test_categories[0].id)

    # SALARIO FEV -> Receita (test_categories[2])
    salario = by_desc.get("SALARIO FEV")
    assert salario is not None
    assert salario["category_id"] == str(test_categories[2].id)


@pytest.mark.asyncio
async def test_apply_all_resets_before_reapply(
    client: AsyncClient, auth_headers, test_rules, test_transactions, test_categories,
):
    """Category/notes are reset before re-applying, so results are idempotent."""
    # Apply once
    await client.post("/api/rules/apply-all", headers=auth_headers)

    # Apply again — should produce same results
    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    by_desc = {t["description"]: t for t in items}

    uber = by_desc.get("UBER TRIP")

    assert uber is not None
    assert uber["category_id"] == str(test_categories[1].id)


@pytest.mark.asyncio
async def test_conflicting_rules_priority(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Two rules match same transaction; lower priority number wins category."""
    cat_food = str(test_categories[0].id)  # Alimentação
    cat_transport = str(test_categories[1].id)  # Transporte

    # Create a low-priority rule (runs first) matching UBER -> Alimentação
    low_rule = {
        "name": "Low priority UBER",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 1,
        "is_active": True,
    }
    # Create a high-priority rule (runs later) matching UBER -> Transporte
    high_rule = {
        "name": "High priority UBER",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [{"op": "set_category", "value": cat_transport}],
        "priority": 99,
        "is_active": True,
    }
    resp1 = await client.post("/api/rules", json=low_rule, headers=auth_headers)
    resp2 = await client.post("/api/rules", json=high_rule, headers=auth_headers)
    assert resp1.status_code == 201
    assert resp2.status_code == 201

    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    by_desc = {t["description"]: t for t in items}

    uber = by_desc.get("UBER TRIP")
    assert uber is not None
    # Lower priority (1) wins, so category should be Alimentação
    assert uber["category_id"] == cat_food


@pytest.mark.asyncio
async def test_tag_attribution_via_rules(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Rule with append_notes applies tags, verify on transaction."""
    cat_transport = str(test_categories[1].id)
    rule_payload = {
        "name": "Tag UBER trips",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [
            {"op": "set_category", "value": cat_transport},
            {"op": "append_notes", "value": "#transport #rideshare"},
        ],
        "priority": 1,
        "is_active": True,
    }
    resp = await client.post("/api/rules", json=rule_payload, headers=auth_headers)
    assert resp.status_code == 201

    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    by_desc = {t["description"]: t for t in items}

    uber = by_desc.get("UBER TRIP")
    assert uber is not None
    assert "#transport" in (uber.get("notes") or "")
    assert "#rideshare" in (uber.get("notes") or "")


@pytest.mark.asyncio
async def test_multiple_tags_from_multiple_rules(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Two rules append different tags to the same transaction."""
    rule1 = {
        "name": "Tag debit",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [{"op": "append_notes", "value": "#expense"}],
        "priority": 1,
        "is_active": True,
    }
    rule2 = {
        "name": "Tag rideshare",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [{"op": "append_notes", "value": "#rideshare"}],
        "priority": 2,
        "is_active": True,
    }
    resp1 = await client.post("/api/rules", json=rule1, headers=auth_headers)
    resp2 = await client.post("/api/rules", json=rule2, headers=auth_headers)
    assert resp1.status_code == 201
    assert resp2.status_code == 201

    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    by_desc = {t["description"]: t for t in items}

    uber = by_desc.get("UBER TRIP")
    assert uber is not None
    notes = uber.get("notes") or ""
    assert "#expense" in notes
    assert "#rideshare" in notes


@pytest.mark.asyncio
async def test_inactive_rule_is_skipped(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    """Disabled rule should not apply."""
    cat_food = str(test_categories[0].id)
    rule_payload = {
        "name": "Inactive NETFLIX rule",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
        "actions": [{"op": "set_category", "value": cat_food}],
        "priority": 1,
        "is_active": False,
    }
    resp = await client.post("/api/rules", json=rule_payload, headers=auth_headers)
    assert resp.status_code == 201

    await client.post("/api/rules/apply-all", headers=auth_headers)

    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    by_desc = {t["description"]: t for t in items}

    netflix = by_desc.get("NETFLIX")
    assert netflix is not None
    # No active rule matches NETFLIX, so category should be None
    assert netflix["category_id"] is None


@pytest.mark.asyncio
async def test_rule_user_isolation(
    client: AsyncClient, auth_headers, test_rules, test_transactions, session: AsyncSession,
):
    """One user's rules don't affect another user's transactions."""
    import bcrypt as _bcrypt
    from app.services.workspace_service import create_personal_workspace_for_user

    # Create second user
    hashed = _bcrypt.hashpw(b"otherpass123", _bcrypt.gensalt()).decode()
    user2 = User(
        id=uuid.uuid4(),
        email="other@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={"language": "pt-BR", "date_format": "DD/MM/YYYY",
                      "timezone": "America/Sao_Paulo", "currency_display": "BRL"},
    )
    session.add(user2)
    await session.flush()
    await create_personal_workspace_for_user(session, user2)
    await session.commit()

    # Login as user2
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": "other@example.com", "password": "otherpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_resp.status_code == 200
    user2_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    # User2 apply-all should not process user1's transactions
    response = await client.post("/api/rules/apply-all", headers=user2_headers)
    assert response.status_code == 200
    assert response.json()["applied"] == 0

    # User1's transactions should remain unchanged by user2's apply-all
    response = await client.get("/api/transactions", headers=auth_headers)
    items = response.json()["items"]
    # Verify user1 still has transactions
    assert len(items) >= 5


@pytest.mark.asyncio
async def test_rule_pack_install_is_scoped_to_selected_workspace(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
):
    from app.services.workspace_service import create_workspace

    second = await create_workspace(
        session,
        name="Second",
        creator=test_user,
        self_membership=True,
        seed_defaults=True,
    )
    second_headers = {**auth_headers, "X-Workspace-Id": str(second.id)}

    response = await client.post("/api/rules/packs/BR/install", headers=second_headers)
    assert response.status_code == 200
    assert response.json()["installed"] > 0

    second_packs = (await client.get("/api/rules/packs", headers=second_headers)).json()
    assert next(p for p in second_packs if p["code"] == "BR")["installed"] is True

    default_packs = (await client.get("/api/rules/packs", headers=auth_headers)).json()
    assert next(p for p in default_packs if p["code"] == "BR")["installed"] is False

    default_rules = (await client.get("/api/rules", headers=auth_headers)).json()
    second_rules = (await client.get("/api/rules", headers=second_headers)).json()
    assert "iFood / Rappi" not in {rule["name"] for rule in default_rules}
    assert "iFood / Rappi" in {rule["name"] for rule in second_rules}


@pytest.mark.asyncio
async def test_export_rules_serializes_category_actions_by_name(client: AsyncClient, auth_headers, test_rules, test_categories):
    response = await client.get("/api/rules/export", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith("attachment;")
    payload = response.json()
    assert payload["format"] == "securo-categorization-rules"
    assert payload["version"] == 1
    exported = {rule["name"]: rule for rule in payload["rules"]}
    assert set(exported) == {"UBER rule", "IFOOD rule", "SALARIO rule"}
    uber_action = exported["UBER rule"]["actions"][0]
    assert uber_action == {"op": "set_category", "value": "Transporte"}
    assert "id" not in exported["UBER rule"]
    assert "user_id" not in exported["UBER rule"]


@pytest.mark.asyncio
async def test_import_rules_requires_overwrite_confirmation_when_rules_exist(
    client: AsyncClient, auth_headers, test_rules, test_categories
):
    payload = {
        "format": "securo-categorization-rules",
        "version": 1,
        "rules": [
            {
                "name": "Imported Netflix",
                "conditions_op": "and",
                "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
                "actions": [{"op": "set_category", "value": "Alimentação"}],
                "priority": 7,
                "is_active": True,
            }
        ],
    }

    response = await client.post("/api/rules/import", json={"payload": payload}, headers=auth_headers)

    assert response.status_code == 409
    assert "overwrite" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_import_rules_overwrites_existing_rules_and_maps_categories_by_name(
    client: AsyncClient, auth_headers, test_rules, test_categories
):
    payload = {
        "format": "securo-categorization-rules",
        "version": 1,
        "rules": [
            {
                "name": "Imported Netflix",
                "conditions_op": "and",
                "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
                "actions": [{"op": "set_category", "value": "Alimentação"}],
                "priority": 7,
                "is_active": True,
            },
            {
                "name": "Missing category rule",
                "conditions_op": "and",
                "conditions": [{"field": "description", "op": "contains", "value": "UNKNOWN"}],
                "actions": [{"op": "set_category", "value": "Does Not Exist"}],
                "priority": 8,
                "is_active": True,
            },
        ],
    }

    response = await client.post(
        "/api/rules/import",
        json={"payload": payload, "overwrite": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"imported": 1, "skipped": 1, "overwritten": 3}
    rules_response = await client.get("/api/rules", headers=auth_headers)
    rules = rules_response.json()
    assert [r["name"] for r in rules] == ["Imported Netflix"]
    assert rules[0]["actions"] == [{"op": "set_category", "value": str(test_categories[0].id)}]


@pytest.mark.asyncio
async def test_import_rules_with_overwrite_preserves_existing_when_every_rule_is_skipped(
    client: AsyncClient, auth_headers, test_rules
):
    payload = {
        "format": "securo-categorization-rules",
        "version": 1,
        "rules": [
            {
                "name": "External groceries rule",
                "conditions_op": "and",
                "conditions": [{"field": "description", "op": "contains", "value": "MARKET"}],
                "actions": [{"op": "set_category", "value": "Groceries"}],
                "priority": 7,
                "is_active": True,
            }
        ],
    }

    before_response = await client.get("/api/rules", headers=auth_headers)
    before_names = [rule["name"] for rule in before_response.json()]

    response = await client.post(
        "/api/rules/import",
        json={"payload": payload, "overwrite": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"imported": 0, "skipped": 1, "overwritten": 0}
    after_response = await client.get("/api/rules", headers=auth_headers)
    assert [rule["name"] for rule in after_response.json()] == before_names


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_create_rule_rejects_blank_condition_value(
    client: AsyncClient, auth_headers, test_categories, blank
):
    """A blank condition value matches every transaction, so it must be rejected
    rather than silently recategorising the whole ledger (issue #438)."""
    payload = {
        "name": "Blank condition",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": blank}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 422

    # And nothing was persisted.
    listed = await client.get("/api/rules", headers=auth_headers)
    assert all(r["name"] != "Blank condition" for r in listed.json())


@pytest.mark.asyncio
async def test_update_rule_rejects_blank_condition_value(
    client: AsyncClient, auth_headers, test_categories
):
    created = await client.post(
        "/api/rules",
        json={
            "name": "Ifood",
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": "IFOOD"}],
            "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
        },
        headers=auth_headers,
    )
    assert created.status_code == 201

    response = await client.patch(
        f"/api/rules/{created.json()['id']}",
        json={"conditions": [{"field": "description", "op": "contains", "value": ""}]},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rule_allows_zero_value(
    client: AsyncClient, auth_headers, test_categories
):
    """0 is a real value, not a blank one."""
    payload = {
        "name": "Positive amounts",
        "conditions_op": "and",
        "conditions": [{"field": "amount", "op": "gt", "value": 0}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201


# ─── nested condition groups (mixing AND and OR) ───


@pytest.mark.asyncio
async def test_create_rule_with_condition_group(
    client: AsyncClient, auth_headers, test_categories
):
    """`type is debit AND (description contains UBER OR contains 99POP)`."""
    payload = {
        "name": "Rides",
        "conditions_op": "and",
        "conditions": [
            {"field": "type", "op": "equals", "value": "debit"},
            {"op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "UBER"},
                {"field": "description", "op": "contains", "value": "99POP"},
            ]},
        ],
        "actions": [{"op": "set_category", "value": str(test_categories[1].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201
    stored = response.json()["conditions"]
    assert stored[0]["field"] == "type"
    assert stored[1]["op"] == "or"
    assert [c["value"] for c in stored[1]["conditions"]] == ["UBER", "99POP"]


@pytest.mark.asyncio
async def test_grouped_rule_applies_to_existing_transactions(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    """The group's OR branch decides which transactions the AND rule reaches."""
    cat_transport = str(test_categories[1].id)
    payload = {
        "name": "Rides",
        "conditions_op": "and",
        "conditions": [
            {"field": "type", "op": "equals", "value": "debit"},
            {"op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "UBER"},
                {"field": "description", "op": "contains", "value": "NETFLIX"},
            ]},
        ],
        "actions": [{"op": "set_category", "value": cat_transport}],
        "apply_to_existing": True,
        "overwrite_existing_categories": True,
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 201

    items = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    by_desc = {t["description"]: t for t in items}
    assert by_desc["UBER TRIP"]["category_id"] == cat_transport
    assert by_desc["NETFLIX"]["category_id"] == cat_transport
    # Credit transactions fail the outer AND, so the group never applies.
    assert by_desc["PIX RECEBIDO"]["category_id"] is None


@pytest.mark.asyncio
async def test_create_rule_rejects_nested_group(
    client: AsyncClient, auth_headers, test_categories
):
    """Groups hold leaves only — rule depth is capped at two levels."""
    payload = {
        "name": "Too deep",
        "conditions_op": "and",
        "conditions": [
            {"op": "or", "conditions": [
                {"op": "and", "conditions": [
                    {"field": "description", "op": "contains", "value": "UBER"},
                ]},
            ]},
        ],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rule_rejects_empty_group(
    client: AsyncClient, auth_headers, test_categories
):
    payload = {
        "name": "Empty group",
        "conditions_op": "and",
        "conditions": [{"op": "or", "conditions": []}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_rule_rejects_blank_value_inside_group(
    client: AsyncClient, auth_headers, test_categories
):
    """A blank value matches everything wherever it sits, group included."""
    payload = {
        "name": "Blank inside group",
        "conditions_op": "and",
        "conditions": [
            {"op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "UBER"},
                {"field": "description", "op": "contains", "value": "  "},
            ]},
        ],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }
    response = await client.post("/api/rules", json=payload, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_grouped_rules_survive_export_import(
    client: AsyncClient, auth_headers, test_categories
):
    payload = {
        "name": "Rides",
        "conditions_op": "and",
        "conditions": [
            {"field": "type", "op": "equals", "value": "debit"},
            {"op": "or", "conditions": [
                {"field": "description", "op": "contains", "value": "UBER"},
                {"field": "description", "op": "contains", "value": "99POP"},
            ]},
        ],
        "actions": [{"op": "set_category", "value": str(test_categories[1].id)}],
    }
    assert (await client.post("/api/rules", json=payload, headers=auth_headers)).status_code == 201

    exported = await client.get("/api/rules/export", headers=auth_headers)
    assert exported.status_code == 200

    imported = await client.post(
        "/api/rules/import",
        json={"payload": exported.json(), "overwrite": True},
        headers=auth_headers,
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1

    listed = (await client.get("/api/rules", headers=auth_headers)).json()
    conditions = next(r["conditions"] for r in listed if r["name"] == "Rides")
    assert conditions[1]["op"] == "or"
    assert [c["value"] for c in conditions[1]["conditions"]] == ["UBER", "99POP"]


@pytest.mark.asyncio
async def test_preview_rule_reports_matches_without_saving(
    client: AsyncClient, auth_headers, test_categories, test_transactions
):
    """Preview an unsaved rule: it reports matches but changes nothing."""
    target = test_categories[0]
    response = await client.post(
        "/api/rules/preview",
        json={
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
            "actions": [{"op": "set_category", "value": str(target.id)}],
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["matched"] == 1
    assert data["will_change"] == 1

    item = data["sample"][0]
    assert item["description"] == "NETFLIX"
    assert item["amount"] == 39.90
    assert item["current_category_id"] is None
    assert item["new_category_name"] == target.name
    assert item["will_change"] is True

    # Nothing was persisted and no rule was created.
    assert (await client.get("/api/rules", headers=auth_headers)).json() == []
    txn = (await client.get(f"/api/transactions/{item['id']}", headers=auth_headers)).json()
    assert txn["category_id"] is None


@pytest.mark.asyncio
async def test_preview_rule_flags_already_categorized_as_unchanged(
    client: AsyncClient, auth_headers, test_categories, test_transactions
):
    """A match that keeps its category is reported as matched but unchanged."""
    body = {
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
    }

    data = (await client.post("/api/rules/preview", json=body, headers=auth_headers)).json()
    assert data["matched"] == 1
    assert data["will_change"] == 0
    item = data["sample"][0]
    assert item["will_change"] is False
    # Without overwrite the transaction keeps the category it already has.
    assert item["new_category_name"] == item["current_category_name"] == test_categories[1].name

    overwritten = (
        await client.post(
            "/api/rules/preview",
            json={**body, "overwrite_existing_categories": True},
            headers=auth_headers,
        )
    ).json()
    assert overwritten["matched"] == 1
    assert overwritten["will_change"] == 1
    assert overwritten["sample"][0]["new_category_name"] == test_categories[0].name


@pytest.mark.asyncio
async def test_preview_rule_honors_condition_groups_and_sample_limit(
    client: AsyncClient, auth_headers, test_categories, test_transactions
):
    response = await client.post(
        "/api/rules/preview",
        json={
            "conditions_op": "and",
            "conditions": [
                {"field": "type", "op": "equals", "value": "debit"},
                {"op": "or", "conditions": [
                    {"field": "description", "op": "contains", "value": "UBER"},
                    {"field": "description", "op": "contains", "value": "NETFLIX"},
                ]},
            ],
            "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
            "limit": 1,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["matched"] == 2
    # The sample is capped by `limit`, the counts still cover every match.
    assert len(data["sample"]) == 1


@pytest.mark.asyncio
async def test_preview_rule_pages_through_the_matches(
    client: AsyncClient, auth_headers, test_categories, test_transactions
):
    """Every match is reachable, one window at a time.

    A rule matching four figures of transactions is exactly the one worth
    inspecting, so the sample has to be pageable rather than a fixed first
    screenful. The windows tile the match list without gaps or repeats, and
    the counts stay exact whichever window is asked for.
    """
    body = {
        "conditions_op": "and",
        "conditions": [{"field": "amount", "op": "gt", "value": "0"}],
        "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
        "limit": 2,
    }

    seen: list[str] = []
    for offset in (0, 2, 4):
        data = (
            await client.post(
                "/api/rules/preview",
                json={**body, "offset": offset},
                headers=auth_headers,
            )
        ).json()
        assert data["matched"] == len(test_transactions)
        assert data["offset"] == offset
        seen.extend(item["id"] for item in data["sample"])

    # Three windows of two over five matches: 2 + 2 + 1, every row once.
    assert len(seen) == len(set(seen)) == len(test_transactions)
    assert set(seen) == {str(tx.id) for tx in test_transactions}

    # Past the end is empty, not an error — the counts still come back.
    past_end = (
        await client.post(
            "/api/rules/preview",
            json={**body, "offset": len(test_transactions)},
            headers=auth_headers,
        )
    ).json()
    assert past_end["matched"] == len(test_transactions)
    assert past_end["sample"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, detail",
    [
        ({"op": "set_category", "value": "not-a-uuid"}, "Category not found"),
        ({"op": "set_payee", "value": str(uuid.uuid4())}, "Payee not found"),
        ({"op": "set_description", "value": "   "}, "Description cannot be blank"),
    ],
)
async def test_preview_rule_validates_actions_like_the_save_path(
    client: AsyncClient, auth_headers, test_transactions, action, detail
):
    """The preview runs the draft's actions, so it has to vet them first.

    Otherwise a malformed action reaches `apply_rule_actions` unvalidated and
    the preview quietly reports the no-op it degrades into, rather than the
    error the same draft would raise on save.
    """
    response = await client.post(
        "/api/rules/preview",
        json={
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": "UBER"}],
            "actions": [action],
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == detail


@pytest.mark.asyncio
async def test_preview_rule_rejects_blank_condition_value(
    client: AsyncClient, auth_headers, test_categories
):
    response = await client.post(
        "/api/rules/preview",
        json={
            "conditions_op": "and",
            "conditions": [{"field": "description", "op": "contains", "value": "  "}],
            "actions": [{"op": "set_category", "value": str(test_categories[0].id)}],
        },
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags, reason",
    [
        ({"is_active": False}, "an inactive rule is never applied"),
        ({"apply_to_existing": False}, "existing transactions are left alone"),
    ],
)
async def test_preview_rule_reports_no_change_when_the_draft_would_not_be_applied(
    client: AsyncClient, auth_headers, test_categories, test_transactions, flags, reason
):
    """The preview forecasts saving, and these flags mean saving changes nothing.

    The matches still come back — they are what the conditions select, and that
    is worth seeing while writing the rule — but nothing is reported as
    changing, because `apply_single_rule` would not run at all.
    """
    target = test_categories[0]
    body = {
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "NETFLIX"}],
        "actions": [{"op": "set_category", "value": str(target.id)}],
    }

    data = (
        await client.post("/api/rules/preview", json={**body, **flags}, headers=auth_headers)
    ).json()
    assert data["matched"] == 1, reason
    assert data["will_change"] == 0
    assert data["will_apply"] is False
    item = data["sample"][0]
    assert item["will_change"] is False
    # Nothing is applied, so the row keeps the category it already has.
    assert item["new_category_id"] == item["current_category_id"] is None

    # Same draft with the flag on: the match is now a change.
    applied = (
        await client.post("/api/rules/preview", json=body, headers=auth_headers)
    ).json()
    assert applied["will_apply"] is True
    assert applied["will_change"] == 1
    assert applied["sample"][0]["new_category_name"] == target.name
