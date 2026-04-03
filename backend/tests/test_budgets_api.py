"""Tests for budget comparison API after legacy standalone budget path removal."""
from datetime import date

import pytest


def _current_month_str() -> str:
    return date.today().replace(day=1).isoformat()


@pytest.mark.asyncio
async def test_budget_comparison_uses_category_owned_budget_state(
    client, auth_headers, test_categories
):
    cat = test_categories[0]
    update_resp = await client.patch(
        f"/api/categories/{cat.id}",
        json={"has_budget": True, "budget_amount": 200},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200

    comp_resp = await client.get(
        "/api/budgets/comparison",
        params={"month": _current_month_str()},
        headers=auth_headers,
    )
    assert comp_resp.status_code == 200
    comparisons = comp_resp.json()
    cat_comp = [c for c in comparisons if c["category_id"] == str(cat.id)]
    assert len(cat_comp) == 1
    assert float(cat_comp[0]["budget_amount"]) == 200.0
    assert "group_id" not in cat_comp[0]
    assert "group_name" not in cat_comp[0]
    assert cat_comp[0]["is_recurring"] is False


@pytest.mark.asyncio
async def test_legacy_standalone_budget_routes_are_removed(client, auth_headers):
    methods_and_paths = [
        ("get", "/api/budgets"),
        ("post", "/api/budgets"),
        ("patch", "/api/budgets/00000000-0000-0000-0000-000000000000"),
        ("delete", "/api/budgets/00000000-0000-0000-0000-000000000000"),
    ]

    for method, path in methods_and_paths:
        response = await getattr(client, method)(path, headers=auth_headers, json={} if method in {"post", "patch"} else None)
        assert response.status_code == 404
