"""Splitting one transaction across several categories (issue #425).

Covers the write side: creating, editing and undoing a split, and every
path that must not overwrite or corrupt one (rules, bulk edits, transfers,
group splits, category lifecycle, deletes).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.schemas.transaction_category_line import (
    TransactionCategoryLineInput,
    TransactionCategoryLinesInput,
)
from app.services import category_line_service


async def _account(client: AsyncClient, headers, name="Wallet", type_="checking") -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": name, "type": type_, "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create(client: AsyncClient, headers, account_id, **extra) -> dict:
    payload = {
        "account_id": account_id,
        "description": "Supermarket",
        "amount": 150,
        "date": "2026-10-10",
        "type": "debit",
        "currency": "USD",
    }
    payload.update(extra)
    resp = await client.post("/api/transactions", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _exact(*parts) -> dict:
    return {
        "share_type": "exact",
        "lines": [{"category_id": str(cid), "amount": amount} for cid, amount in parts],
    }


def _lines(body: dict) -> list[tuple[str, Decimal]]:
    return [(line["category_id"], Decimal(str(line["amount"]))) for line in body["category_lines"]]


@pytest.fixture
def cats(test_categories):
    food, transport, income = test_categories
    return food, transport, income


@pytest.mark.asyncio
async def test_create_split_by_exact_amounts(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)

    body = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    assert body["has_category_lines"] is True
    assert body["category_id"] is None
    assert _lines(body) == [(str(food.id), Decimal("100")), (str(transport.id), Decimal("50"))]
    assert [line["position"] for line in body["category_lines"]] == [0, 1]


@pytest.mark.asyncio
async def test_create_split_by_percent_puts_the_residual_on_the_last_line(
    client, auth_headers, cats
):
    food, transport, income = cats
    account = await _account(client, auth_headers)

    body = await _create(
        client, auth_headers, account["id"], amount="0.10",
        category_lines={
            "share_type": "percent",
            "lines": [
                {"category_id": str(food.id), "share_pct": "33.33"},
                {"category_id": str(transport.id), "share_pct": "33.33"},
                {"category_id": str(income.id), "share_pct": "33.34"},
            ],
        },
    )

    amounts = [amount for _, amount in _lines(body)]
    assert amounts == [Decimal("0.03"), Decimal("0.03"), Decimal("0.04")]
    assert sum(amounts) == Decimal("0.10")
    # The percentage the user typed comes back, not one derived from cents.
    assert [Decimal(str(line["share_pct"])) for line in body["category_lines"]] == [
        Decimal("33.33"), Decimal("33.33"), Decimal("33.34"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines_payload",
    [
        # One line is not a split.
        {"share_type": "exact", "lines": [{"category_id": "FOOD", "amount": 150}]},
        # Lines that do not add up to the amount.
        {
            "share_type": "exact",
            "lines": [
                {"category_id": "FOOD", "amount": 100},
                {"category_id": "TRANSPORT", "amount": 40},
            ],
        },
        # Percentages that do not add up to 100.
        {
            "share_type": "percent",
            "lines": [
                {"category_id": "FOOD", "share_pct": 50},
                {"category_id": "TRANSPORT", "share_pct": 40},
            ],
        },
        # A zero line.
        {
            "share_type": "exact",
            "lines": [
                {"category_id": "FOOD", "amount": 150},
                {"category_id": "TRANSPORT", "amount": 0},
            ],
        },
        # The same category twice.
        {
            "share_type": "exact",
            "lines": [
                {"category_id": "FOOD", "amount": 100},
                {"category_id": "FOOD", "amount": 50},
            ],
        },
    ],
)
async def test_invalid_splits_are_refused(client, auth_headers, cats, lines_payload):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    for line in lines_payload["lines"]:
        line["category_id"] = str(food.id if line["category_id"] == "FOOD" else transport.id)

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"], "description": "x", "amount": 150,
            "date": "2026-10-10", "type": "debit", "category_lines": lines_payload,
        },
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_a_category_and_lines_together_are_refused(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"], "description": "x", "amount": 150,
            "date": "2026-10-10", "type": "debit", "category_id": str(food.id),
            "category_lines": _exact((food.id, 100), (transport.id, 50)),
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["is_ignored", "is_hidden"])
async def test_ignored_and_hidden_categories_cannot_be_lines(
    client, auth_headers, session, cats, flag
):
    food, transport, _ = cats
    setattr(transport, flag, True)
    await session.commit()
    account = await _account(client, auth_headers)

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"], "description": "x", "amount": 150,
            "date": "2026-10-10", "type": "debit",
            "category_lines": _exact((food.id, 100), (transport.id, 50)),
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_a_category_from_another_workspace_is_refused(
    client, auth_headers, session, cats, test_user
):
    from app.models.workspace import Workspace

    food, _, _ = cats
    other_ws = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal",
        created_by_user_id=test_user.id, default_currency="USD",
    )
    session.add(other_ws)
    await session.flush()
    foreign = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_ws.id, name="Foreign"
    )
    session.add(foreign)
    await session.commit()
    account = await _account(client, auth_headers)

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"], "description": "x", "amount": 150,
            "date": "2026-10-10", "type": "debit",
            "category_lines": _exact((food.id, 100), (foreign.id, 50)),
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_a_credit_card_credit_cannot_have_a_transfer_line(
    client, auth_headers, session, cats
):
    food, transport, _ = cats
    transport.treat_as_transfer = True
    await session.commit()
    card = await _account(client, auth_headers, name="Card", type_="credit_card")

    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": card["id"], "description": "x", "amount": 150,
            "date": "2026-10-10", "type": "credit",
            "category_lines": _exact((food.id, 100), (transport.id, 50)),
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_split(client, auth_headers, viewer_auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(client, auth_headers, account["id"])

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=viewer_auth_headers,
        json={"category_lines": _exact((food.id, 100), (transport.id, 50))},
    )
    assert resp.status_code == 403


# --- Editing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_an_existing_transaction_and_undo_it(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(client, auth_headers, account["id"], category_id=str(food.id))

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_lines": _exact((food.id, 40), (transport.id, 110))},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category_id"] is None
    assert resp.json()["has_category_lines"] is True

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_lines": {"share_type": "exact", "lines": []}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_category_lines"] is False
    assert body["category_lines"] == []
    # The category of the largest line comes back.
    assert body["category_id"] == str(transport.id)


@pytest.mark.asyncio
async def test_undo_with_an_explicit_category_keeps_that_category(client, auth_headers, cats):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    tx = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={
            "category_lines": {"share_type": "exact", "lines": []},
            "category_id": str(income.id),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category_id"] == str(income.id)


@pytest.mark.asyncio
async def test_undo_from_the_editor_keeps_the_largest_line_category(client, auth_headers, cats):
    """The editor sends `category_id: null` alongside the empty lines."""
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_id": None, "category_lines": {"share_type": "exact", "lines": []}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category_id"] == str(food.id)


@pytest.mark.asyncio
async def test_saving_a_split_row_with_a_null_category_keeps_the_split(
    client, auth_headers, cats
):
    """The editor sends `category_id: null` on every save."""
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_id": None, "description": "Groceries and cleaning"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_category_lines"] is True
    assert len(resp.json()["category_lines"]) == 2


@pytest.mark.asyncio
async def test_a_single_category_on_a_split_row_is_refused(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_id": str(food.id)},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_changing_the_amount_needs_the_lines(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    refused = await client.patch(
        f"/api/transactions/{tx['id']}", headers=auth_headers, json={"amount": 200}
    )
    assert refused.status_code == 400

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"amount": 200, "category_lines": _exact((food.id, 120), (transport.id, 80))},
    )
    assert resp.status_code == 200, resp.text
    assert sum(amount for _, amount in _lines(resp.json())) == Decimal("200")


@pytest.mark.asyncio
async def test_card_account_move_rechecks_transfer_lines(client, auth_headers, session, cats):
    food, transport, _ = cats
    transport.treat_as_transfer = True
    await session.commit()
    checking = await _account(client, auth_headers)
    card = await _account(client, auth_headers, name="Card", type_="credit_card")
    tx = await _create(
        client, auth_headers, checking["id"], type="credit",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/transactions/{tx['id']}", headers=auth_headers, json={"account_id": card["id"]}
    )
    assert resp.status_code == 400


# --- Mutual exclusions -----------------------------------------------------


@pytest.mark.asyncio
async def test_group_split_and_category_split_exclude_each_other(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    group = (
        await client.post(
            "/api/groups",
            headers=auth_headers,
            json={"name": "Home", "kind": "social", "default_currency": "USD"},
        )
    ).json()
    members = [
        (
            await client.post(
                f"/api/groups/{group['id']}/members",
                headers=auth_headers,
                json={"name": name, "is_self": name == "Me"},
            )
        ).json()
        for name in ("Me", "Ana")
    ]
    people = {"share_type": "equal", "splits": [{"group_member_id": m["id"]} for m in members]}

    shared = await _create(client, auth_headers, account["id"], splits=people)
    resp = await client.patch(
        f"/api/transactions/{shared['id']}",
        headers=auth_headers,
        json={"category_lines": _exact((food.id, 100), (transport.id, 50))},
    )
    assert resp.status_code == 400

    split = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    resp = await client.patch(
        f"/api/transactions/{split['id']}", headers=auth_headers, json={"splits": people}
    )
    assert resp.status_code == 400

    resp = await client.patch(
        "/api/transactions/bulk-add-to-group",
        headers=auth_headers,
        json={
            "transaction_ids": [split["id"]],
            "group_id": group["id"],
            "share_type": "equal",
            "member_splits": [{"group_member_id": m["id"]} for m in members],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_a_split_row_cannot_be_linked_as_a_transfer(client, auth_headers, cats):
    food, transport, _ = cats
    checking = await _account(client, auth_headers)
    savings = await _account(client, auth_headers, name="Savings", type_="savings")
    split = await _create(
        client, auth_headers, checking["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    other = await _create(client, auth_headers, savings["id"], type="credit")

    resp = await client.post(
        "/api/transactions/link-transfer",
        headers=auth_headers,
        json={"transaction_ids": [split["id"], other["id"]]},
    )
    assert resp.status_code == 400

    resp = await client.post(
        f"/api/transactions/{split['id']}/create-counterpart",
        headers=auth_headers,
        json={"to_account_id": savings["id"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_an_ignored_transaction_cannot_be_split(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    tx = await _create(client, auth_headers, account["id"])
    await client.patch(f"/api/transactions/{tx['id']}", headers=auth_headers, json={"is_ignored": True})

    resp = await client.patch(
        f"/api/transactions/{tx['id']}",
        headers=auth_headers,
        json={"category_lines": _exact((food.id, 100), (transport.id, 50))},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_an_installment_series_is_split_installment_by_installment(client, auth_headers, cats):
    """Each installment carries the same parts (they all have the base amount)."""
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "installments": 3,
            "base": {
                "account_id": account["id"], "description": "TV", "amount": 150,
                "date": "2026-10-10", "type": "debit",
                "category_lines": _exact((food.id, 100), (transport.id, 50)),
            },
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert len(created) == 3
    for row in created:
        assert row["has_category_lines"] is True
        assert row["category_id"] is None
        assert _lines(row) == [(str(food.id), Decimal("100")), (str(transport.id), Decimal("50"))]


# --- Automations never overwrite a split -----------------------------------


@pytest.mark.asyncio
async def test_bulk_categorize_skips_split_rows(client, auth_headers, cats):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    split = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    plain = await _create(client, auth_headers, account["id"])

    resp = await client.patch(
        "/api/transactions/bulk-categorize",
        headers=auth_headers,
        json={"transaction_ids": [split["id"], plain["id"]], "category_id": str(income.id)},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 1, "skipped_split": 1}

    after = (await client.get(f"/api/transactions/{split['id']}", headers=auth_headers)).json()
    assert after["category_id"] is None
    assert after["has_category_lines"] is True


@pytest.mark.asyncio
async def test_splitting_one_installment_keeps_the_others_categorized(client, auth_headers, cats):
    """The editor sends a null category with the parts; it must not reach
    sibling installments edited in the same request and wipe theirs."""
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions/installments", headers=auth_headers,
        json={"installments": 3, "base": {
            "account_id": account["id"], "description": "Desk", "amount": 150,
            "date": "2026-10-10", "type": "debit", "category_id": str(food.id),
        }},
    )
    assert resp.status_code == 201, resp.text
    first = resp.json()[0]

    resp = await client.patch(
        f"/api/transactions/{first['id']}", headers=auth_headers,
        json={"apply_to": "all", "description": "Desk (office)", "category_id": None,
              "category_lines": _exact((food.id, 100), (transport.id, 50))},
    )
    assert resp.status_code == 200, resp.text
    rows = (await client.get("/api/transactions", headers=auth_headers, params={"q": "Desk"})).json()["items"]
    assert len(rows) == 3
    assert all(r["description"] == "Desk (office)" for r in rows)
    # Every installment stays categorized, one way or the other.
    assert all(r["category_id"] or r["has_category_lines"] for r in rows)


@pytest.mark.asyncio
async def test_rules_never_overwrite_a_split(client, auth_headers, cats):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    split = await _create(
        client, auth_headers, account["id"], description="SUPERMARKET 123",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    # Applied to history with overwrite on, then re-applied from scratch.
    resp = await client.post(
        "/api/rules",
        headers=auth_headers,
        json={
            "name": "Supermarket",
            "conditions": [{"field": "description", "op": "contains", "value": "SUPERMARKET"}],
            "actions": [{"op": "set_category", "value": str(income.id)}],
            "apply_to_existing": True,
            "overwrite_existing_categories": True,
        },
    )
    assert resp.status_code == 201, resp.text
    assert (await client.post("/api/rules/apply-all", headers=auth_headers)).status_code == 200

    after = (await client.get(f"/api/transactions/{split['id']}", headers=auth_headers)).json()
    assert after["category_id"] is None
    assert after["has_category_lines"] is True
    assert len(after["category_lines"]) == 2

    # A new row created with lines is not categorized by the rule either.
    fresh = await _create(
        client, auth_headers, account["id"], description="SUPERMARKET 456",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    assert fresh["category_id"] is None
    assert fresh["has_category_lines"] is True


@pytest.mark.asyncio
async def test_search_marks_split_rows(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    split = await _create(
        client, auth_headers, account["id"], description="WAREHOUSE 77",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    plain = await _create(
        client, auth_headers, account["id"], description="WAREHOUSE 78",
        category_id=str(food.id),
    )

    resp = await client.get("/api/search", headers=auth_headers, params={"q": "WAREHOUSE"})
    assert resp.status_code == 200, resp.text
    meta = {hit["id"]: hit["meta"] for hit in resp.json()["results"] if hit["type"] == "transaction"}
    assert meta[split["id"]]["has_category_lines"] is True
    assert meta[split["id"]]["category_id"] is None
    assert meta[plain["id"]]["has_category_lines"] is False


@pytest.mark.asyncio
async def test_rule_preview_marks_split_rows(client, auth_headers, cats):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    split = await _create(
        client, auth_headers, account["id"], description="PHARMACY 9",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.post(
        "/api/rules/preview",
        headers=auth_headers,
        json={
            "conditions": [{"field": "description", "op": "contains", "value": "PHARMACY"}],
            "actions": [{"op": "set_category", "value": str(income.id)}],
            "apply_to_existing": True,
            "overwrite_existing_categories": True,
        },
    )
    assert resp.status_code == 200, resp.text
    (item,) = [i for i in resp.json()["sample"] if i["id"] == split["id"]]
    assert item["current_has_category_lines"] is True
    assert item["will_change"] is False


@pytest.mark.asyncio
async def test_each_line_carries_its_category(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    body = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    assert [line["category"]["name"] for line in body["category_lines"]] == [food.name, transport.name]

    listed = (await client.get("/api/transactions", headers=auth_headers)).json()["items"]
    (row,) = [r for r in listed if r["id"] == body["id"]]
    assert row["category_lines"][1]["category"]["id"] == str(transport.id)


# --- Category lifecycle ----------------------------------------------------


@pytest.mark.asyncio
async def test_category_used_in_lines_counts_as_in_use(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.delete(f"/api/categories/{transport.id}", headers=auth_headers)
    assert resp.status_code in (400, 409), resp.text


@pytest.mark.asyncio
async def test_deleting_a_category_with_a_destination_moves_its_lines(
    client, auth_headers, session, cats
):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    split = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.delete(
        f"/api/categories/{transport.id}",
        headers=auth_headers,
        params={"transfer_to_category_id": str(income.id)},
    )
    assert resp.status_code == 204, resp.text

    after = (await client.get(f"/api/transactions/{split['id']}", headers=auth_headers)).json()
    assert [line["category_id"] for line in after["category_lines"]] == [
        str(food.id), str(income.id),
    ]


@pytest.mark.asyncio
async def test_moving_into_a_category_the_split_holds_merges_the_parts(
    client, auth_headers, session, test_user, cats
):
    food, transport, income = cats
    extra = Category(id=uuid.uuid4(), user_id=test_user.id, name="Extra", icon="x", color="#000000")
    session.add(extra)
    await session.commit()
    account = await _account(client, auth_headers)
    three = await _create(
        client, auth_headers, account["id"], amount=90,
        category_lines={"share_type": "percent", "lines": [
            {"category_id": str(food.id), "share_pct": 50},
            {"category_id": str(transport.id), "share_pct": 30},
            {"category_id": str(income.id), "share_pct": 20},
        ]},
    )
    two = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    template = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={
            "description": "Rent", "amount": 100, "currency": "USD", "type": "debit",
            "frequency": "monthly", "day_of_month": 5, "start_date": "2026-12-05",
            "account_id": account["id"],
            "category_lines": _exact((food.id, 70), (transport.id, 30)),
        },
    )
    assert template.status_code == 201, template.text

    resp = await client.delete(
        f"/api/categories/{transport.id}", headers=auth_headers,
        params={"transfer_to_category_id": str(food.id)},
    )
    assert resp.status_code == 204, resp.text

    # Three parts, two of them now in food: one food part holding both.
    after = (await client.get(f"/api/transactions/{three['id']}", headers=auth_headers)).json()
    assert _lines(after) == [(str(food.id), Decimal("72")), (str(income.id), Decimal("18"))]
    assert [line["share_pct"] for line in after["category_lines"]] == ["80.00", "20.00"]
    # Two parts that end up in one category are a plain row in it.
    after = (await client.get(f"/api/transactions/{two['id']}", headers=auth_headers)).json()
    assert (after["has_category_lines"], after["category_id"], after["category_lines"]) == (
        False, str(food.id), [],
    )
    listed = (await client.get("/api/recurring-transactions", headers=auth_headers)).json()
    rec = next(row for row in listed if row["id"] == template.json()["id"])
    assert (rec["has_category_lines"], rec["category_id"]) == (False, str(food.id))


@pytest.mark.asyncio
async def test_a_card_credit_part_keeps_its_category_out_of_transfers(
    client, auth_headers, session, test_user, cats
):
    food, transport, _ = cats
    savings = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Savings", icon="x", color="#000000",
        treat_as_transfer=True,
    )
    session.add(savings)
    await session.commit()
    card = await _account(client, auth_headers, name="Card", type_="credit_card")
    await _create(
        client, auth_headers, card["id"], type="credit",
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    # Neither flagging the category nor moving it into a transfer one.
    resp = await client.patch(
        f"/api/categories/{transport.id}", headers=auth_headers, json={"treat_as_transfer": True}
    )
    assert resp.status_code == 400
    resp = await client.delete(
        f"/api/categories/{transport.id}", headers=auth_headers,
        params={"transfer_to_category_id": str(savings.id)},
    )
    assert resp.status_code == 400
    assert await session.get(Category, transport.id) is not None


@pytest.mark.asyncio
async def test_a_card_credit_template_part_keeps_its_category_out_of_transfers(
    client, auth_headers, session, test_user, cats
):
    """Same guard when only a recurring template uses the category."""
    food, transport, _ = cats
    savings = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Savings", icon="x", color="#000000",
        treat_as_transfer=True,
    )
    session.add(savings)
    await session.commit()
    card = await _account(client, auth_headers, name="Card", type_="credit_card")
    template = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={
            "description": "Cashback", "amount": 150, "currency": "USD", "type": "credit",
            "frequency": "monthly", "day_of_month": 5, "start_date": "2026-12-05",
            "account_id": card["id"],
            "category_lines": _exact((food.id, 100), (transport.id, 50)),
        },
    )
    assert template.status_code == 201, template.text

    resp = await client.patch(
        f"/api/categories/{transport.id}", headers=auth_headers, json={"treat_as_transfer": True}
    )
    assert resp.status_code == 400
    resp = await client.delete(
        f"/api/categories/{transport.id}", headers=auth_headers,
        params={"transfer_to_category_id": str(savings.id)},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_moving_a_category_keeps_the_notes_of_merged_parts(
    client, auth_headers, session, test_user, cats
):
    food, transport, income = cats
    account = await _account(client, auth_headers)
    three = await _create(
        client, auth_headers, account["id"], amount=90,
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(food.id), "amount": 40, "notes": "bread"},
            {"category_id": str(transport.id), "amount": 30, "notes": "bus"},
            {"category_id": str(income.id), "amount": 20},
        ]},
    )
    two = await _create(
        client, auth_headers, account["id"], notes="weekly",
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(food.id), "amount": 100},
            {"category_id": str(transport.id), "amount": 50, "notes": "taxi"},
        ]},
    )

    resp = await client.delete(
        f"/api/categories/{transport.id}", headers=auth_headers,
        params={"transfer_to_category_id": str(food.id)},
    )
    assert resp.status_code == 204, resp.text

    after = (await client.get(f"/api/transactions/{three['id']}", headers=auth_headers)).json()
    assert [line["notes"] for line in after["category_lines"]] == ["bread; bus", None]
    # The last part's note moves to the transaction once the split is gone.
    after = (await client.get(f"/api/transactions/{two['id']}", headers=auth_headers)).json()
    assert (after["has_category_lines"], after["notes"]) == (False, "weekly; taxi")


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["is_ignored", "is_hidden"])
async def test_a_category_in_use_by_lines_cannot_become_ignored_or_hidden(
    client, auth_headers, cats, flag
):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    resp = await client.patch(
        f"/api/categories/{transport.id}", headers=auth_headers, json={flag: True}
    )
    assert resp.status_code == 400


# --- Deleting --------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_transactions_removes_their_lines(client, auth_headers, session, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    one = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )
    two = await _create(
        client, auth_headers, account["id"],
        category_lines=_exact((food.id, 100), (transport.id, 50)),
    )

    assert (await client.delete(f"/api/transactions/{one['id']}", headers=auth_headers)).status_code == 204
    resp = await client.post(
        "/api/transactions/bulk-delete", headers=auth_headers, json={"transaction_ids": [two["id"]]}
    )
    assert resp.status_code == 200, resp.text

    remaining = await session.scalar(select(func.count()).select_from(TransactionCategoryLine))
    assert remaining == 0


# --- Service: materializing and rescaling ----------------------------------


def _line(category_id, share_type, value) -> TransactionCategoryLineInput:
    if share_type == "exact":
        return TransactionCategoryLineInput(category_id=category_id, amount=Decimal(str(value)))
    return TransactionCategoryLineInput(category_id=category_id, share_pct=Decimal(str(value)))


def _payload(share_type, *parts):
    return TransactionCategoryLinesInput(
        share_type=share_type,
        lines=[_line(uuid.uuid4(), share_type, v) for v in parts],
    )


def test_materialize_exact_and_percent():
    exact = category_line_service.materialize(Decimal("150"), _payload("exact", 100, 50))
    assert [amount for _, amount, _, _ in exact] == [Decimal("100.00"), Decimal("50.00")]

    percent = category_line_service.materialize(Decimal("100"), _payload("percent", 60, 40))
    assert [(amount, pct) for _, amount, pct, _ in percent] == [
        (Decimal("60.00"), Decimal("60")), (Decimal("40.00"), Decimal("40")),
    ]


def test_materialize_keeps_every_part_positive_when_a_split_exists():
    """4 cents at 37.5/37.5/25: rounding each part on its own leaves the last
    with nothing, though 1/2/1 cents is a valid split."""
    parts = category_line_service.materialize(Decimal("0.04"), _payload("percent", "37.5", "37.5", 25))
    assert [amount for _, amount, _, _ in parts] == [
        Decimal("0.01"), Decimal("0.02"), Decimal("0.01"),
    ]


def test_materialize_gives_the_odd_cent_to_the_largest_fraction():
    parts = category_line_service.materialize(Decimal("10"), _payload("percent", "33.33", "33.34", "33.33"))
    assert [amount for _, amount, _, _ in parts] == [
        Decimal("3.33"), Decimal("3.34"), Decimal("3.33"),
    ]


@pytest.mark.asyncio
async def test_finer_than_cents_and_hundredths_is_refused(client, auth_headers, cats):
    """Validating 33.333 + 66.667 = 100 and then storing 33.33/66.67 would
    save something other than what was checked."""
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    for lines in (
        {"share_type": "percent", "lines": [
            {"category_id": str(food.id), "share_pct": "33.333"},
            {"category_id": str(transport.id), "share_pct": "66.667"},
        ]},
        {"share_type": "exact", "lines": [
            {"category_id": str(food.id), "amount": "100.005"},
            {"category_id": str(transport.id), "amount": "49.995"},
        ]},
    ):
        resp = await client.post(
            "/api/transactions", headers=auth_headers,
            json={"account_id": account["id"], "description": "x", "amount": 150,
                  "date": "2026-10-10", "type": "debit", "currency": "USD",
                  "category_lines": lines},
        )
        assert resp.status_code == 422, resp.text


async def _split_row(session, test_user, cats, share_type, *parts, amount="150"):
    food, transport, _ = cats
    from app.models.account import Account

    account = Account(user_id=test_user.id, name="A", type="checking", balance=0, currency="USD")
    session.add(account)
    await session.flush()
    tx = Transaction(
        user_id=test_user.id, account_id=account.id, description="Rent",
        amount=Decimal(amount), currency="USD", date=date(2026, 10, 1),
        type="debit", source="recurring", status="pending",
    )
    session.add(tx)
    await session.flush()
    payload = TransactionCategoryLinesInput(
        share_type=share_type,
        lines=[_line(cid, share_type, v) for cid, v in zip((food.id, transport.id), parts)],
    )
    await category_line_service.replace_lines(session, tx, payload)
    return tx


@pytest.mark.asyncio
async def test_rescale_keeps_exact_proportions(session, test_user, cats):
    tx = await _split_row(session, test_user, cats, "exact", 100, 50)
    old = tx.amount
    tx.amount = Decimal("151")
    await category_line_service.rescale_lines(session, tx, old)

    amounts = [line.amount for line in tx.category_lines]
    assert amounts == [Decimal("100.67"), Decimal("50.33")]
    assert sum(amounts) == Decimal("151")


@pytest.mark.asyncio
async def test_rescale_keeps_percentages(session, test_user, cats):
    tx = await _split_row(session, test_user, cats, "percent", 70, 30, amount="1000")
    old = tx.amount
    tx.amount = Decimal("1200")
    await category_line_service.rescale_lines(session, tx, old)

    assert [line.amount for line in tx.category_lines] == [Decimal("840.00"), Decimal("360.00")]
    assert [line.share_pct for line in tx.category_lines] == [Decimal("70"), Decimal("30")]


@pytest.mark.asyncio
async def test_rescale_to_a_too_small_amount_undoes_the_split(session, test_user, cats):
    food, transport, _ = cats
    tx = await _split_row(session, test_user, cats, "exact", 100, 50)
    old = tx.amount
    tx.amount = Decimal("0.01")
    await category_line_service.rescale_lines(session, tx, old)

    assert tx.has_category_lines is False
    assert tx.category_id == food.id


@pytest.mark.asyncio
async def test_import_confirming_a_split_placeholder_keeps_the_split_resized(
    session, test_user, test_workspace, test_account, cats
):
    """A recurring placeholder split by percent, confirmed by an imported
    charge for a slightly different amount, stays split with the same
    percentages and lines that add up to what was actually charged."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch

    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.schemas.transaction import TransactionImport
    from app.services import reconciliation_policy, reconciliation_rule_service
    from app.services.import_service import import_transactions
    from app.services.recurring_transaction_service import create_recurring_transaction

    food, transport, _ = cats
    await reconciliation_rule_service.upsert_override(
        session, test_workspace.id, test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"], "same_account_exact",
        {"when": {"amount": {"match": "tolerance", "percent": "5"}, "unique_candidate": True}},
    )
    bill = await create_recurring_transaction(
        session, test_workspace.id, test_user.id,
        RecurringTransactionCreate(
            description="Rent and condo", amount=Decimal("1000"), currency="BRL",
            type="debit", frequency="monthly", start_date=date(2026, 1, 10),
            account_id=test_account.id,
        ),
    )
    bill.next_occurrence = date(2026, 2, 10)
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        workspace_id=test_workspace.id, description="Rent and condo",
        amount=Decimal("1000"), currency="BRL", date=date(2026, 1, 10),
        amount_primary=Decimal("1000"), fx_rate_used=Decimal("1"),
        type="debit", source="recurring", status="posted",
        recurring_transaction_id=bill.id, created_at=datetime.now(timezone.utc),
    )
    session.add(placeholder)
    await session.flush()
    await category_line_service.replace_lines(
        session, placeholder,
        TransactionCategoryLinesInput(
            share_type="percent",
            lines=[
                TransactionCategoryLineInput(category_id=food.id, share_pct=Decimal("70")),
                TransactionCategoryLineInput(category_id=transport.id, share_pct=Decimal("30")),
            ],
        ),
    )
    await session.commit()

    txns = [TransactionImport(description="RENT AND CONDO", amount=Decimal("1030"),
                              date=date(2026, 1, 11), type="debit", currency="BRL")]
    with patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        imported, _, _, _ = await import_transactions(
            session, test_workspace.id, test_user.id, test_account.id, txns, "csv",
        )

    assert imported == 1
    await session.refresh(placeholder)
    await session.refresh(placeholder, ["category_lines"])
    assert placeholder.amount == Decimal("1030")
    assert placeholder.category_id is None
    assert placeholder.has_category_lines is True
    assert [line.amount for line in placeholder.category_lines] == [
        Decimal("721.00"), Decimal("309.00"),
    ]


@pytest.mark.asyncio
async def test_a_series_edit_cannot_reprice_a_split_installment(client, auth_headers, cats):
    food, transport, _ = cats
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions/installments",
        headers=auth_headers,
        json={
            "installments": 3,
            "base": {
                "account_id": account["id"], "description": "TV", "amount": 150,
                "date": "2026-10-10", "type": "debit",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    first, second, _ = resp.json()
    split = await client.patch(
        f"/api/transactions/{second['id']}",
        headers=auth_headers,
        json={"category_lines": _exact((food.id, 100), (transport.id, 50))},
    )
    assert split.status_code == 200, split.text

    refused = await client.patch(
        f"/api/transactions/{first['id']}",
        headers=auth_headers,
        json={"amount": 160, "apply_to": "all"},
    )
    assert refused.status_code == 400

    # A description change across the series is fine, even with the
    # editor's null category riding along.
    renamed = await client.patch(
        f"/api/transactions/{first['id']}",
        headers=auth_headers,
        json={"description": "Television", "category_id": None, "apply_to": "all"},
    )
    assert renamed.status_code == 200, renamed.text
    after = (await client.get(f"/api/transactions/{second['id']}", headers=auth_headers)).json()
    assert after["description"] == "Television"
    assert after["has_category_lines"] is True
