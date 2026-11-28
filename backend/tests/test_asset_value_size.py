"""Valuations of a billion and more (issue #641).

`asset_values.amount` held nine integer digits, so a flat in IDR, VND or JPY
failed at flush with a numeric field overflow and the route answered 500. The
column now holds the thirteen integer digits of the other money columns, and
anything past them is refused by the schema with a 422 before the database
sees it. SQLite stores any number, so the same writes rerun on PostgreSQL in
`test_asset_value_size_postgres.py`.
"""
import pytest
from httpx import AsyncClient

ONE_BILLION = "1000000000"
NINE_POINT_EIGHT_BILLION = "9800000000"
LARGEST = "9999999999999.99"
PAST_IT = "10000000000000"


async def _flat(client: AsyncClient, auth_headers: dict, currency: str = "IDR", **extra) -> dict:
    response = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={"name": f"Flat in {currency}", "type": "real_estate", "currency": currency, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [ONE_BILLION, NINE_POINT_EIGHT_BILLION, LARGEST])
async def test_a_valuation_of_a_billion_or_more_is_recorded(
    client: AsyncClient, auth_headers: dict, amount: str
):
    flat = await _flat(client, auth_headers)

    response = await client.post(
        f"/api/assets/{flat['id']}/values",
        headers=auth_headers,
        json={"amount": amount, "date": "2026-01-15"},
    )

    assert response.status_code == 201, response.text
    values = (await client.get(f"/api/assets/{flat['id']}/values", headers=auth_headers)).json()
    assert [v["amount"] for v in values] == [float(amount)]


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [ONE_BILLION, NINE_POINT_EIGHT_BILLION])
async def test_an_asset_created_with_a_current_value_of_a_billion_is_kept(
    client: AsyncClient, auth_headers: dict, amount: str
):
    flat = await _flat(client, auth_headers, currency="JPY", current_value=amount)

    assert flat["current_value"] == float(amount)
    assert flat["value_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [PAST_IT, f"-{PAST_IT}", "NaN", "Infinity"])
async def test_a_valuation_past_the_column_is_a_422_without_sql(
    client: AsyncClient, auth_headers: dict, amount: str
):
    flat = await _flat(client, auth_headers)

    response = await client.post(
        f"/api/assets/{flat['id']}/values",
        headers=auth_headers,
        json={"amount": amount, "date": "2026-01-15"},
    )

    assert response.status_code == 422
    body = response.text.lower()
    assert "amount" in body
    assert "numeric" not in body and "sql" not in body and "asyncpg" not in body
    values = (await client.get(f"/api/assets/{flat['id']}/values", headers=auth_headers)).json()
    assert values == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["current_value", "purchase_price", "sell_price"])
async def test_an_asset_created_past_the_column_is_a_422_without_sql(
    client: AsyncClient, auth_headers: dict, field: str
):
    response = await client.post(
        "/api/assets",
        headers=auth_headers,
        json={"name": "Too big", "type": "real_estate", "currency": "IDR", field: PAST_IT},
    )

    assert response.status_code == 422
    body = response.text.lower()
    assert field in body
    assert "numeric" not in body and "sql" not in body
    listed = (await client.get("/api/assets", headers=auth_headers)).json()
    assert all(a["name"] != "Too big" for a in listed)


@pytest.mark.asyncio
async def test_a_goal_on_a_flat_worth_billions_shows_its_value_and_history(
    client: AsyncClient, auth_headers: dict
):
    flat = await _flat(client, auth_headers, current_value=ONE_BILLION, purchase_date="2025-06-01")
    recorded = await client.post(
        f"/api/assets/{flat['id']}/values",
        headers=auth_headers,
        json={"amount": NINE_POINT_EIGHT_BILLION, "date": "2026-01-15"},
    )
    assert recorded.status_code == 201, recorded.text

    goal = await client.post(
        "/api/goals",
        headers=auth_headers,
        json={
            "name": "Own the flat",
            "target_amount": "12000000000",
            "currency": "IDR",
            "tracking_type": "asset",
            "asset_id": flat["id"],
        },
    )
    assert goal.status_code == 201, goal.text
    # The value it was created with is dated today, after the January one.
    assert float(goal.json()["current_amount"]) == float(ONE_BILLION)

    trend = await client.get(f"/api/assets/{flat['id']}/value-trend", headers=auth_headers)
    assert trend.status_code == 200
    amounts = [point["amount"] for point in trend.json()]
    assert float(NINE_POINT_EIGHT_BILLION) in amounts
    assert amounts[-1] == float(ONE_BILLION)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["purchase_price", "sell_price"])
async def test_an_asset_edited_past_the_column_is_a_422_without_sql(
    client: AsyncClient, auth_headers: dict, field: str
):
    flat = await _flat(client, auth_headers)

    response = await client.patch(
        f"/api/assets/{flat['id']}", headers=auth_headers, json={field: PAST_IT}
    )

    assert response.status_code == 422
    body = response.text.lower()
    assert f"{field} must be at most 9999999999999.99" in body
    assert "numeric" not in body and "sql" not in body
