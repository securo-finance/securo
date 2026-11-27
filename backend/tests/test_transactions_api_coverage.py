"""Coverage-focused tests for app/api/transactions.py.

Covers list filters, export (filtered + selection), update/ignore error
branches, bulk endpoints, link-transfer, and cross-workspace rejections.
"""
from datetime import date
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.group import Group, GroupMember
from app.models.transaction import Transaction
from app.services.workspace_service import create_workspace


NONEXISTENT = "00000000-0000-0000-0000-000000000000"


async def _manual_account(client: AsyncClient, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": name, "type": "checking", "balance": "0.00"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# list filters + summary
# ---------------------------------------------------------------------------


async def _seed_transfers(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_account: Account
) -> tuple[set[str], set[str]]:
    """A paired transfer plus a one-sided row in a `treat_as_transfer`
    category. Returns (transfer ids, transfer descriptions)."""
    other = await _manual_account(client, auth_headers, "Savings")
    resp = await client.post(
        "/api/transactions/transfer", headers=auth_headers,
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": other,
            "amount": "300.00",
            "date": date.today().isoformat(),
            "description": "PAIRED TRANSFER",
        },
    )
    assert resp.status_code == 201, resp.text
    paired_ids = {resp.json()["debit"]["id"], resp.json()["credit"]["id"]}

    investments = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Investments",
        icon="trending-up", color="#0EA5E9", treat_as_transfer=True,
    )
    session.add(investments)
    await session.flush()
    one_sided = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        category_id=investments.id, description="BROKER APPLICATION",
        amount=500, currency="BRL", date=date.today(), type="debit", source="manual",
    )
    session.add(one_sided)
    await session.commit()
    return paired_ids | {str(one_sided.id)}, {"PAIRED TRANSFER", "BROKER APPLICATION"}


@pytest.mark.asyncio
async def test_list_filter_by_type_transfer(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
    test_account: Account, test_transactions,
):
    transfer_ids, _ = await _seed_transfers(client, auth_headers, session, test_user, test_account)

    resp = await client.get("/api/transactions?type=transfer&limit=500", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Paired legs and the treat_as_transfer row, and none of the plain
    # income/expense fixtures.
    assert {t["id"] for t in items} == transfer_ids


@pytest.mark.asyncio
async def test_list_filter_by_income_expense_unchanged_by_transfers(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
    test_account: Account, test_transactions,
):
    transfer_ids, _ = await _seed_transfers(client, auth_headers, session, test_user, test_account)

    # Income/Expense stay a plain type filter, so a transfer leg of that
    # type still shows up there, exactly as before.
    for typ in ("credit", "debit"):
        resp = await client.get(f"/api/transactions?type={typ}&limit=500", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items and all(t["type"] == typ for t in items)
        assert any(t["id"] in transfer_ids for t in items)


@pytest.mark.asyncio
async def test_export_filter_by_type_transfer(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
    test_account: Account, test_transactions,
):
    _, descriptions = await _seed_transfers(client, auth_headers, session, test_user, test_account)

    resp = await client.get("/api/transactions/export?type=transfer", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.text
    for desc in descriptions:
        assert desc in body
    for fixture_tx in test_transactions:
        assert fixture_tx.description not in body


@pytest.mark.asyncio
async def test_list_search_query(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?q=UBER", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert "UBER" in items[0]["description"]


@pytest.mark.asyncio
async def test_list_uncategorized_only(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions?uncategorized=true", headers=auth_headers)
    assert resp.status_code == 200
    assert {t["description"] for t in resp.json()["items"]} == {"PIX RECEBIDO", "NETFLIX"}


@pytest.mark.asyncio
async def test_list_amount_bounds_and_sort(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get(
        "/api/transactions?min_amount=40&max_amount=200&sort_by=amount&sort_dir=asc",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    amounts = [abs(float(t["amount"])) for t in items]
    assert amounts == sorted(amounts)
    assert all(40 <= a <= 200 for a in amounts)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_filtered(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "date,description,amount" in body
    assert "UBER TRIP" in body


@pytest.mark.asyncio
async def test_export_selection_only(client: AsyncClient, auth_headers, test_transactions):
    tx_id = str(test_transactions[0].id)
    resp = await client.get(
        f"/api/transactions/export?transaction_ids={tx_id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.text
    assert test_transactions[0].description in body
    for other in test_transactions[1:]:
        assert other.description not in body


# ---------------------------------------------------------------------------
# get / create / update / delete / ignore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editing_transaction_does_not_persist_ignored_category_state(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace, test_account
):
    ignored = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Ignored",
        is_ignored=True,
    )
    normal = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Normal",
    )
    session.add_all([ignored, normal])
    await session.commit()

    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "category_id": str(ignored.id),
            "description": "Hidden fee",
            "amount": "12.50",
            "date": date.today().isoformat(),
            "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text

    transaction_id = resp.json()["id"]
    edit = await client.patch(
        f"/api/transactions/{transaction_id}", headers=auth_headers, json={"notes": "edited"}
    )
    assert edit.status_code == 200
    assert edit.json()["is_ignored"] is True
    persisted = await session.scalar(
        select(Transaction.is_ignored).where(Transaction.id == uuid.UUID(transaction_id))
    )
    assert persisted is False

    moved = await client.patch(
        f"/api/transactions/{transaction_id}",
        headers=auth_headers,
        json={"category_id": str(normal.id)},
    )
    assert moved.status_code == 200
    assert moved.json()["is_ignored"] is False
    persisted = await session.scalar(
        select(Transaction.is_ignored).where(Transaction.id == uuid.UUID(transaction_id))
    )
    assert persisted is False


@pytest.mark.asyncio
async def test_create_transaction_rejects_category_from_other_workspace(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_account
):
    other_ws = await create_workspace(
        session,
        name="Other",
        creator=test_user,
        self_membership=True,
        seed_defaults=False,
    )
    other_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other category",
    )
    session.add(other_category)
    await session.commit()

    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "category_id": str(other_category.id),
            "description": "Wrong category",
            "amount": "1.00",
            "date": date.today().isoformat(),
            "type": "debit",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Category not found"


@pytest.mark.asyncio
async def test_update_transaction_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(
        f"/api/transactions/{NONEXISTENT}", headers=auth_headers,
        json={"description": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_transaction_rejects_category_from_other_workspace(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_transactions
):
    other_ws = await create_workspace(
        session,
        name="Other",
        creator=test_user,
        self_membership=True,
        seed_defaults=False,
    )
    other_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other category",
    )
    session.add(other_category)
    await session.commit()

    tx = test_transactions[0]
    resp = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers,
        json={"category_id": str(other_category.id)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Category not found"


@pytest.mark.asyncio
async def test_toggle_ignore(client: AsyncClient, auth_headers, test_transactions):
    tx = test_transactions[0]
    resp = await client.patch(f"/api/transactions/{tx.id}/ignore", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_ignored"] is True


@pytest.mark.asyncio
async def test_toggle_ignore_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(f"/api/transactions/{NONEXISTENT}/ignore", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# bulk endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_delete_transactions(client: AsyncClient, auth_headers, test_transactions):
    ids = [str(t.id) for t in test_transactions[:2]]
    resp = await client.post(
        "/api/transactions/bulk-delete", headers=auth_headers,
        json={"transaction_ids": ids},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    for tx_id in ids:
        assert (await client.get(f"/api/transactions/{tx_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_bulk_delete_ignores_other_workspace_ids(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_transactions
):
    other_ws = await create_workspace(
        session,
        name="Other",
        creator=test_user,
        self_membership=True,
        seed_defaults=False,
    )
    other_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other account",
        type="checking",
        currency="USD",
    )
    session.add(other_account)
    await session.flush()
    other_tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        account_id=other_account.id,
        description="Other workspace tx",
        amount=10,
        currency="USD",
        date=date.today(),
        type="debit",
        source="manual",
    )
    session.add(other_tx)
    await session.commit()

    resp = await client.post(
        "/api/transactions/bulk-delete", headers=auth_headers,
        json={"transaction_ids": [str(other_tx.id), NONEXISTENT]},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    still_there = await session.get(Transaction, other_tx.id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_bulk_delete_cascades_transfer_pair(
    client: AsyncClient, auth_headers, test_account: Account
):
    other = await _manual_account(client, auth_headers, "Destino bulk delete")
    resp = await client.post(
        "/api/transactions/transfer", headers=auth_headers,
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": other,
            "amount": "50.00",
            "date": date.today().isoformat(),
            "description": "Transferência bulk delete",
        },
    )
    assert resp.status_code == 201, resp.text
    debit_id = resp.json()["debit"]["id"]
    credit_id = resp.json()["credit"]["id"]

    resp = await client.post(
        "/api/transactions/bulk-delete", headers=auth_headers,
        json={"transaction_ids": [debit_id]},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    # The paired leg goes too, mirroring single-transaction delete semantics.
    assert (await client.get(f"/api/transactions/{debit_id}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/transactions/{credit_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_bulk_categorize_rejects_category_from_other_workspace(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_transactions
):
    other_ws = await create_workspace(
        session,
        name="Other",
        creator=test_user,
        self_membership=True,
        seed_defaults=False,
    )
    other_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other category",
    )
    session.add(other_category)
    await session.commit()

    resp = await client.patch(
        "/api/transactions/bulk-categorize", headers=auth_headers,
        json={
            "transaction_ids": [str(test_transactions[0].id)],
            "category_id": str(other_category.id),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Category not found"


@pytest.mark.asyncio
async def test_bulk_add_and_remove_tags(client: AsyncClient, auth_headers, test_transactions):
    ids = [str(t.id) for t in test_transactions[:2]]
    add = await client.patch(
        "/api/transactions/bulk-add-tags", headers=auth_headers,
        json={"transaction_ids": ids, "tags": ["viagem", "reembolso"]},
    )
    assert add.status_code == 200
    assert add.json()["updated"] == 2

    remove = await client.patch(
        "/api/transactions/bulk-remove-tags", headers=auth_headers,
        json={"transaction_ids": ids, "tags": ["viagem"]},
    )
    assert remove.status_code == 200
    assert remove.json()["updated"] == 2

    for tx_id in ids:
        notes = (await client.get(f"/api/transactions/{tx_id}", headers=auth_headers)).json()["notes"]
        assert notes == "#reembolso"


@pytest.mark.asyncio
async def test_bulk_add_to_group_rejects_group_from_other_workspace(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_transactions
):
    other_ws = await create_workspace(
        session,
        name="Other",
        creator=test_user,
        self_membership=True,
        seed_defaults=False,
    )
    group = Group(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_ws.id,
        name="Other group",
        default_currency="BRL",
    )
    member = GroupMember(
        id=uuid.uuid4(),
        group_id=group.id,
        workspace_id=other_ws.id,
        name="Me",
        linked_user_id=test_user.id,
        is_self=True,
    )
    session.add_all([group, member])
    await session.commit()

    resp = await client.patch(
        "/api/transactions/bulk-add-to-group", headers=auth_headers,
        json={
            "transaction_ids": [str(test_transactions[0].id)],
            "group_id": str(group.id),
            "share_type": "equal",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Group not found"


# ---------------------------------------------------------------------------
# transfer / link / counterpart / candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_transfer_preserves_categories(
    client: AsyncClient, auth_headers, test_transactions, test_categories
):
    other = await _manual_account(client, auth_headers, "Destino linkado")
    debit = test_transactions[0]
    credit_resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": other,
            "category_id": str(test_categories[2].id),
            "description": "Linked credit",
            "amount": "25.50",
            "date": date.today().isoformat(),
            "type": "credit",
        },
    )
    assert credit_resp.status_code == 201, credit_resp.text

    resp = await client.post(
        "/api/transactions/link-transfer",
        headers=auth_headers,
        json={"transaction_ids": [str(debit.id), credit_resp.json()["id"]]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["debit"]["category_id"] == str(debit.category_id)
    assert data["credit"]["category_id"] == str(test_categories[2].id)

