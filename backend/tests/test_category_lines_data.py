"""A split across categories survives export, backup and the MCP tools (#425)."""

import csv
import io
import json
import uuid
import zipfile
from decimal import Decimal

import pytest
from httpx import AsyncClient

import mcp_server.tools  # noqa: F401 -- registers the MCP handlers
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

pytestmark = pytest.mark.asyncio


async def _account(client: AsyncClient, headers, currency="USD") -> dict:
    resp = await client.post(
        "/api/accounts", headers=headers,
        json={"name": "Checking", "type": "checking", "balance": 0, "currency": currency},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _split(client, headers, account_id, parts, amount=150, description="Warehouse") -> dict:
    resp = await client.post(
        "/api/transactions", headers=headers,
        json={
            "account_id": account_id, "description": description, "amount": amount,
            "date": "2026-10-10", "type": "debit", "currency": "USD",
            "category_lines": {
                "share_type": "exact",
                "lines": [{"category_id": str(c.id), "amount": a} for c, a in parts],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _export_rows(client, headers) -> list[dict]:
    resp = await client.get("/api/transactions/export", headers=headers)
    assert resp.status_code == 200, resp.text
    return list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))


# --- CSV export -------------------------------------------------------------


async def test_export_writes_one_row_per_part(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    split = await _split(client, auth_headers, account["id"], [(food, 100), (transport, 50)])
    await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account["id"], "description": "Coffee", "amount": 5,
              "date": "2026-10-11", "type": "debit", "currency": "USD",
              "category_id": str(food.id)},
    )

    rows = await _export_rows(client, auth_headers)

    parts = [r for r in rows if r["split_group"]]
    assert [(r["category"], Decimal(r["amount"]), r["split_index"]) for r in parts] == [
        (food.name, Decimal("100.00"), "1"), (transport.name, Decimal("50.00"), "2"),
    ]
    assert {r["split_group"] for r in parts} == {split["id"]}
    # A plain transaction leaves the split columns empty.
    (coffee,) = [r for r in rows if r["description"] == "Coffee"]
    assert coffee["split_group"] == "" and coffee["split_index"] == ""
    # Summing the column still gives the money that moved.
    assert sum(Decimal(r["amount"]) for r in rows) == Decimal("155")


async def test_export_spreads_the_primary_amount_across_parts(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers, currency="EUR")
    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account["id"], "description": "Hotel", "amount": 100,
              "date": "2026-10-10", "type": "debit", "currency": "EUR", "amount_primary": "610.01",
              "category_lines": {"share_type": "percent", "lines": [
                  {"category_id": str(food.id), "share_pct": 30},
                  {"category_id": str(transport.id), "share_pct": 70},
              ]}},
    )
    assert resp.status_code == 201, resp.text

    parts = [r for r in await _export_rows(client, auth_headers) if r["split_group"]]
    primaries = [Decimal(r["amount_primary"]) for r in parts]
    assert sum(primaries) == Decimal("610.01")
    assert primaries == [Decimal("183.00"), Decimal("427.01")]


# --- CSV import -------------------------------------------------------------


async def _preview(client, headers, content: bytes) -> list[dict]:
    resp = await client.post(
        "/api/transactions/import/preview", headers=headers,
        files={"file": ("export.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def _import(client, headers, account_id, transactions) -> dict:
    resp = await client.post(
        "/api/transactions/import", headers=headers,
        json={"account_id": account_id, "transactions": transactions,
              "filename": "export.csv", "detected_format": "csv", "detect_duplicates": False},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_an_exported_split_imports_back_as_the_same_split(
    client, auth_headers, test_categories
):
    food, transport, _ = test_categories
    source = await _account(client, auth_headers)
    await _split(client, auth_headers, source["id"], [(food, 100), (transport, 50)])
    resp = await client.get("/api/transactions/export", headers=auth_headers)

    preview = await _preview(client, auth_headers, resp.content)
    (row,) = preview
    assert Decimal(str(row["amount"])) == Decimal("150")
    assert row["category_name"] is None
    assert [(line["category_name"], Decimal(str(line["amount"]))) for line in row["category_lines"]] == [
        (food.name, Decimal("100")), (transport.name, Decimal("50")),
    ]

    target = await _account(client, auth_headers)
    assert (await _import(client, auth_headers, target["id"], preview))["imported"] == 1

    listed = (await client.get(
        "/api/transactions", headers=auth_headers, params={"account_id": target["id"]},
    )).json()["items"]
    (imported,) = listed
    assert imported["has_category_lines"] is True
    assert imported["category_id"] is None
    assert [(line["category_id"], Decimal(str(line["amount"]))) for line in imported["category_lines"]] == [
        (str(food.id), Decimal("100.00")), (str(transport.id), Decimal("50.00")),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        # Only one part left in the file.
        [("Warehouse", "100", "Alimentação", "g1", "1")],
        # Parts that disagree on the description.
        [("Warehouse", "100", "Alimentação", "g1", "1"), ("Other", "50", "Transporte", "g1", "2")],
        # A part without a category.
        [("Warehouse", "100", "Alimentação", "g1", "1"), ("Warehouse", "50", "", "g1", "2")],
        # The same index twice.
        [("Warehouse", "100", "Alimentação", "g1", "1"), ("Warehouse", "50", "Transporte", "g1", "1")],
    ],
)
async def test_an_ambiguous_group_imports_row_by_row(client, auth_headers, test_categories, rows):
    lines = ["date,description,amount,type,currency,category,split_group,split_index"]
    lines += [f"2026-10-10,{d},-{a},debit,USD,{c},{g},{i}" for d, a, c, g, i in rows]
    preview = await _preview(client, auth_headers, "\n".join(lines).encode())
    assert len(preview) == len(rows)
    assert all(not row.get("category_lines") for row in preview)


async def test_a_part_in_a_category_missing_here_imports_the_row_unsplit(
    client, auth_headers, test_categories
):
    food, _, _ = test_categories
    content = (
        "date,description,amount,type,currency,category,split_group,split_index\n"
        f"2026-10-10,Warehouse,-100,debit,USD,{food.name},g1,1\n"
        "2026-10-10,Warehouse,-50,debit,USD,Not Here,g1,2\n"
    ).encode()
    preview = await _preview(client, auth_headers, content)
    assert len(preview[0]["category_lines"]) == 2

    account = await _account(client, auth_headers)
    assert (await _import(client, auth_headers, account["id"], preview))["imported"] == 1
    (row,) = (await client.get(
        "/api/transactions", headers=auth_headers, params={"account_id": account["id"]},
    )).json()["items"]
    assert row["has_category_lines"] is False
    assert Decimal(str(row["amount"])) == Decimal("150")


async def test_a_csv_without_the_split_columns_is_unchanged(client, auth_headers, test_categories):
    content = b"date,description,amount,category\n2026-10-10,Warehouse,-100,Food\n2026-10-10,Warehouse,-50,Home\n"
    preview = await _preview(client, auth_headers, content)
    assert [(r["description"], r["category_name"], r.get("category_lines")) for r in preview] == [
        ("Warehouse", "Food", None), ("Warehouse", "Home", None),
    ]


# --- Backup -----------------------------------------------------------------


async def test_the_backup_keeps_the_parts(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    split = await _split(client, auth_headers, account["id"], [(food, 100), (transport, 50)])

    resp = await client.get("/api/export/backup", headers=auth_headers)
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        lines = json.loads(archive.read("transaction_category_lines.json"))
        metadata = json.loads(archive.read("metadata.json"))
    assert [(line["transaction_id"], line["category_id"], line["amount"]) for line in lines] == [
        (split["id"], str(food.id), "100.00"), (split["id"], str(transport.id), "50.00"),
    ]
    assert metadata["entity_counts"]["transaction_category_lines"] == 2


async def test_the_backup_keeps_a_split_recurring_bill(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={
            "description": "Bundle", "amount": 100, "currency": "BRL", "type": "debit",
            "frequency": "monthly", "day_of_month": 5, "start_date": "2026-12-05",
            "account_id": account["id"],
            "category_lines": {"share_type": "percent", "lines": [
                {"category_id": str(food.id), "share_pct": 60},
                {"category_id": str(transport.id), "share_pct": 40},
            ]},
        },
    )
    assert resp.status_code == 201, resp.text
    template = resp.json()

    resp = await client.get("/api/export/backup", headers=auth_headers)
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        lines = json.loads(archive.read("recurring_transaction_category_lines.json"))
        metadata = json.loads(archive.read("metadata.json"))
    assert [
        (line["recurring_transaction_id"], line["category_id"], line["amount"], line["share_pct"])
        for line in lines
    ] == [
        (template["id"], str(food.id), "60.00", "60.00"),
        (template["id"], str(transport.id), "40.00", "40.00"),
    ]
    assert metadata["entity_counts"]["recurring_transaction_category_lines"] == 2


# --- MCP --------------------------------------------------------------------


async def test_list_transactions_shows_the_parts(session, client, auth_headers, test_user, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    split = await _split(client, auth_headers, account["id"], [(food, 100), (transport, 50)])

    result = await REGISTRY["list_transactions"].handler(
        session=session, ctx=CallContext(user_id=test_user.id, conversation_id=uuid.uuid4()),
    )
    (row,) = [r for r in result["items"] if r["id"] == split["id"]]
    assert row["category_id"] is None
    assert row["category_lines"] == [
        {"category_id": str(food.id), "category_name": food.name, "amount": 100.0, "share_pct": None},
        {"category_id": str(transport.id), "category_name": transport.name, "amount": 50.0, "share_pct": None},
    ]


async def test_propose_categorize_leaves_a_split_alone(session, client, auth_headers, test_user, test_categories):
    food, transport, income = test_categories
    account = await _account(client, auth_headers)
    split = await _split(client, auth_headers, account["id"], [(food, 100), (transport, 50)])

    result = await REGISTRY["propose_categorize"].handler(
        session=session,
        ctx=CallContext(user_id=test_user.id, external=True),
        transaction_ids=[split["id"]], category_id=str(income.id), apply=True,
    )
    assert result["skipped_split_count"] == 1
    assert result["affected"][0]["is_category_split"] is True
    assert result["updated_count"] == 0
    after = (await client.get(f"/api/transactions/{split['id']}", headers=auth_headers)).json()
    assert after["has_category_lines"] is True


async def test_propose_create_transaction_with_parts(session, client, auth_headers, test_user, test_categories):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    handler = REGISTRY["propose_create_transaction"].handler
    args = dict(
        description="Receipt", amount=200, type="debit", account_id=account["id"],
        category_lines={"share_type": "percent", "lines": [
            {"category_id": str(food.id), "share_pct": 60},
            {"category_id": str(transport.id), "share_pct": 40},
        ]},
    )

    preview = await handler(session=session, ctx=CallContext(user_id=test_user.id), **args)
    assert [(line["category_name"], line["amount"]) for line in preview["proposed"]["category_lines"]["lines"]] == [
        (food.name, 120.0), (transport.name, 80.0),
    ]

    applied = await handler(
        session=session, ctx=CallContext(user_id=test_user.id, external=True), apply=True, **args,
    )
    assert applied["applied"] is True
    created = (await client.get(f"/api/transactions/{applied['id']}", headers=auth_headers)).json()
    assert [Decimal(str(line["amount"])) for line in created["category_lines"]] == [
        Decimal("120.00"), Decimal("80.00"),
    ]

    # A split that does not add up never reaches a preview.
    bad = await handler(
        session=session, ctx=CallContext(user_id=test_user.id),
        **{**args, "category_lines": {"share_type": "percent", "lines": [
            {"category_id": str(food.id), "share_pct": 60},
            {"category_id": str(transport.id), "share_pct": 30},
        ]}},
    )
    assert "error" in bad


# --- Review follow-ups ------------------------------------------------------


async def test_export_keeps_zero_cent_primary_shares(client, auth_headers, test_categories):
    """Tiny primary amounts still add up in the column, with zero-cent parts."""
    food, transport, _ = test_categories
    account = await _account(client, auth_headers, currency="EUR")
    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account["id"], "description": "Tiny", "amount": 1,
              "date": "2026-10-10", "type": "debit", "currency": "EUR", "amount_primary": "0.01",
              "category_lines": {"share_type": "exact", "lines": [
                  {"category_id": str(food.id), "amount": "0.50"},
                  {"category_id": str(transport.id), "amount": "0.50"},
              ]}},
    )
    assert resp.status_code == 201, resp.text
    parts = [r for r in await _export_rows(client, auth_headers) if r["split_group"]]
    primaries = [Decimal(r["amount_primary"]) for r in parts]
    assert sum(primaries) == Decimal("0.01")
    assert Decimal("0.00") in primaries


@pytest.mark.parametrize(
    "second",
    [
        # Another rate: not the same transaction.
        "2026-10-10,Warehouse,-50,debit,USD,Transporte,g1,2,2.0",
        # A zero part is not a valid line.
        "2026-10-10,Warehouse,0,debit,USD,Transporte,g1,2,",
    ],
)
async def test_parts_that_disagree_import_row_by_row(client, auth_headers, test_categories, second):
    content = (
        "date,description,amount,type,currency,category,split_group,split_index,fx_rate\n"
        "2026-10-10,Warehouse,-100,debit,USD,Alimentação,g1,1,\n"
        f"{second}\n"
    ).encode()
    preview = await _preview(client, auth_headers, content)
    assert len(preview) == 2
    assert all(not row.get("category_lines") for row in preview)


async def test_a_category_name_shared_by_two_categories_imports_unsplit(
    client, auth_headers, session, test_user, test_categories
):
    from app.models.category import Category

    food, transport, _ = test_categories
    session.add(Category(id=uuid.uuid4(), user_id=test_user.id, name=food.name))
    await session.commit()
    content = (
        "date,description,amount,type,currency,category,split_group,split_index\n"
        f"2026-10-10,Warehouse,-100,debit,USD,{food.name},g1,1\n"
        f"2026-10-10,Warehouse,-50,debit,USD,{transport.name},g1,2\n"
    ).encode()
    preview = await _preview(client, auth_headers, content)
    account = await _account(client, auth_headers)
    await _import(client, auth_headers, account["id"], preview)
    (row,) = (await client.get(
        "/api/transactions", headers=auth_headers, params={"account_id": account["id"]},
    )).json()["items"]
    assert row["has_category_lines"] is False


async def test_an_imported_split_fills_an_uncategorized_placeholder(
    client, auth_headers, session, test_user, test_categories
):
    from datetime import date as _date

    from app.services.recurring_transaction_service import generate_pending

    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={"description": "Warehouse", "amount": 150, "currency": "USD", "type": "debit",
              "frequency": "monthly", "day_of_month": 10, "start_date": "2026-10-10",
              "account_id": account["id"]},
    )
    assert resp.status_code == 201, resp.text
    await generate_pending(session, test_user.id, up_to=_date(2026, 10, 10), include_manual=True)

    content = (
        "date,description,amount,type,currency,category,split_group,split_index\n"
        f"2026-10-10,Warehouse,-100,debit,USD,{food.name},g1,1\n"
        f"2026-10-10,Warehouse,-50,debit,USD,{transport.name},g1,2\n"
    ).encode()
    preview = await _preview(client, auth_headers, content)
    await _import(client, auth_headers, account["id"], preview)

    rows = (await client.get(
        "/api/transactions", headers=auth_headers, params={"account_id": account["id"]},
    )).json()["items"]
    (row,) = rows
    assert row["has_category_lines"] is True
    assert [Decimal(str(line["amount"])) for line in row["category_lines"]] == [
        Decimal("100.00"), Decimal("50.00"),
    ]


async def test_list_transactions_reports_the_matched_part(
    session, client, auth_headers, test_user, test_categories
):
    food, transport, _ = test_categories
    account = await _account(client, auth_headers)
    split = await _split(client, auth_headers, account["id"], [(food, 100), (transport, 50)])

    result = await REGISTRY["list_transactions"].handler(
        session=session, ctx=CallContext(user_id=test_user.id, conversation_id=uuid.uuid4()),
        category_ids=[str(food.id)],
    )
    (row,) = [r for r in result["items"] if r["id"] == split["id"]]
    assert row["matched_amount"] == 100.0


async def test_propose_create_transaction_refuses_an_ignored_part(
    session, client, auth_headers, test_user, test_categories
):
    from app.models.category import Category

    food, _, _ = test_categories
    ignored = Category(id=uuid.uuid4(), user_id=test_user.id, name="Ignored", is_ignored=True)
    session.add(ignored)
    await session.commit()
    account = await _account(client, auth_headers)
    result = await REGISTRY["propose_create_transaction"].handler(
        session=session, ctx=CallContext(user_id=test_user.id),
        description="Receipt", amount=100, type="debit", account_id=account["id"],
        category_lines={"share_type": "exact", "lines": [
            {"category_id": str(food.id), "amount": 50},
            {"category_id": str(ignored.id), "amount": 50},
        ]},
    )
    assert "error" in result
