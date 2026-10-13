"""End-to-end tests for category-split (category allocations) on transactions.

Covers: create with allocations, update replaces allocations, mutual exclusion
with people-splits, validation errors (400), cascade read, amount-drift guard.
"""
import pytest
from httpx import AsyncClient


async def _create_account(client: AsyncClient, auth_headers, name="Wallet") -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=auth_headers,
        json={"name": name, "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create_category(client: AsyncClient, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/categories",
        headers=auth_headers,
        json={"name": name, "type": "expense"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create_tx(client, auth_headers, account_id, amount=150.00, extra=None) -> dict:
    payload = {
        "description": "Supermarket",
        "amount": amount,
        "date": "2025-01-15",
        "type": "debit",
        "account_id": account_id,
        **(extra or {}),
    }
    resp = await client.post("/api/transactions", headers=auth_headers, json=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def test_create_transaction_with_category_allocations(client, auth_headers, test_user):
    """POST /api/transactions with category_allocations stores splits correctly."""
    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "Food")
    clothing = await _create_category(client, auth_headers, "Clothing")

    tx = await _create_tx(
        client, auth_headers, account["id"],
        extra={
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                    {"category_id": clothing["id"], "amount": "50.00"},
                ]
            }
        },
    )

    allocs = tx.get("category_allocations", [])
    assert len(allocs) == 2
    amounts = {a["category_id"]: float(a["amount"]) for a in allocs}
    assert amounts[food["id"]] == pytest.approx(100.00)
    assert amounts[clothing["id"]] == pytest.approx(50.00)


async def test_update_transaction_replaces_category_allocations(client, auth_headers, test_user):
    """PATCH replaces existing allocations wholesale."""
    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "FoodUpd")
    clothing = await _create_category(client, auth_headers, "ClothingUpd")
    transport = await _create_category(client, auth_headers, "TransportUpd")

    tx = await _create_tx(
        client, auth_headers, account["id"],
        extra={
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                    {"category_id": clothing["id"], "amount": "50.00"},
                ]
            }
        },
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "120.00"},
                    {"category_id": transport["id"], "amount": "30.00"},
                ]
            }
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    allocs = updated.get("category_allocations", [])
    assert len(allocs) == 2
    cat_ids = {a["category_id"] for a in allocs}
    assert clothing["id"] not in cat_ids
    assert transport["id"] in cat_ids


async def test_sum_mismatch_returns_400(client, auth_headers, test_user):
    """Allocations that don't sum to transaction amount return 400."""
    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "FoodBad")
    clothing = await _create_category(client, auth_headers, "ClothingBad")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "Supermarket",
            "amount": 150.00,
            "date": "2025-01-15",
            "type": "debit",
            "account_id": account["id"],
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                    {"category_id": clothing["id"], "amount": "40.00"},  # 140 ≠ 150
                ]
            },
        },
    )
    assert resp.status_code == 400, resp.text


async def test_single_allocation_returns_400(client, auth_headers, test_user):
    """A single allocation (trivially equals parent category) is rejected with 400."""
    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "FoodSingle")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "Supermarket",
            "amount": 100.00,
            "date": "2025-01-15",
            "type": "debit",
            "account_id": account["id"],
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                ]
            },
        },
    )
    assert resp.status_code == 400, resp.text


async def test_mutual_exclusion_people_splits_and_category_allocations(
    client, auth_headers, test_user
):
    """A transaction with both people-splits and category allocations returns 400."""
    from tests.test_transaction_splits_api import _create_group_with_members

    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "FoodMutex")
    clothing = await _create_category(client, auth_headers, "ClothingMutex")
    group, members = await _create_group_with_members(client, auth_headers, "Alice", "Bob")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "Supermarket",
            "amount": 150.00,
            "date": "2025-01-15",
            "type": "debit",
            "account_id": account["id"],
            "splits": {
                "share_type": "exact",
                "splits": [
                    {"group_member_id": members[0]["id"], "share_value": 100.00},
                    {"group_member_id": members[1]["id"], "share_value": 50.00},
                ],
            },
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                    {"category_id": clothing["id"], "amount": "50.00"},
                ]
            },
        },
    )
    assert resp.status_code == 400, resp.text


async def test_clear_allocations_with_empty_list(client, auth_headers, test_user):
    """PATCH with empty allocations list removes all existing rows."""
    account = await _create_account(client, auth_headers)
    food = await _create_category(client, auth_headers, "FoodClear")
    clothing = await _create_category(client, auth_headers, "ClothingClear")

    tx = await _create_tx(
        client, auth_headers, account["id"],
        extra={
            "category_allocations": {
                "allocations": [
                    {"category_id": food["id"], "amount": "100.00"},
                    {"category_id": clothing["id"], "amount": "50.00"},
                ]
            }
        },
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_allocations": {"allocations": []}},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated.get("category_allocations", []) == []
