import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.payee import Payee
from app.models.user import User


@pytest.mark.asyncio
async def test_list_transactions(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5
    assert data["page"] == 1
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_list_transactions_pagination(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    response = await client.get(
        "/api/transactions?page=1&limit=2", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_list_transactions_filter_by_account(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction], test_account: Account
):
    response = await client.get(
        f"/api/transactions?account_id={test_account.id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5  # all belong to same account


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    cat_id = test_categories[0].id  # Alimentação
    response = await client.get(
        f"/api/transactions?category_id={cat_id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1  # only IFOOD


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category_ids_multi(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Passing multiple ``category_ids`` should return the union of matches."""
    alimentacao = test_categories[0].id  # IFOOD
    transporte = test_categories[1].id   # UBER
    response = await client.get(
        f"/api/transactions?category_ids={alimentacao}&category_ids={transporte}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    descriptions = {item["description"] for item in data["items"]}
    assert descriptions == {"UBER TRIP", "IFOOD RESTAURANTE"}


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category_ids_single_element(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """A one-element ``category_ids`` list should behave like the legacy single filter."""
    cat_id = test_categories[0].id  # Alimentação
    response = await client.get(
        f"/api/transactions?category_ids={cat_id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["description"] == "IFOOD RESTAURANTE"


@pytest.mark.asyncio
async def test_list_transactions_merge_category_id_and_category_ids(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """When both the legacy ``category_id`` and the new ``category_ids`` are sent,
    results should be the union of both."""
    alimentacao = test_categories[0].id
    transporte = test_categories[1].id
    response = await client.get(
        f"/api/transactions?category_id={alimentacao}&category_ids={transporte}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    descriptions = {item["description"] for item in data["items"]}
    assert descriptions == {"UBER TRIP", "IFOOD RESTAURANTE"}


@pytest.mark.asyncio
async def test_list_transactions_filter_by_category_ids_no_match(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """A non-existent ``category_ids`` filter should return zero rows, not leak other txns."""
    ghost_id = uuid.uuid4()
    response = await client.get(
        f"/api/transactions?category_ids={ghost_id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_transactions_filter_by_exact_amount(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """Setting min_amount==max_amount matches only that exact amount (issue #212)."""
    response = await client.get(
        "/api/transactions?min_amount=45&max_amount=45", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["description"] == "IFOOD RESTAURANTE"


@pytest.mark.asyncio
async def test_list_transactions_filter_by_min_amount(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """min_amount alone is an open-ended lower bound."""
    response = await client.get(
        "/api/transactions?min_amount=40", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    descriptions = {item["description"] for item in data["items"]}
    # 45.00 IFOOD, 150.00 PIX, 8000.00 SALARIO — UBER 25.50 and NETFLIX 39.90 dropped
    assert descriptions == {"IFOOD RESTAURANTE", "PIX RECEBIDO", "SALARIO FEV"}
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_list_transactions_filter_by_max_amount(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """max_amount alone is an open-ended upper bound."""
    response = await client.get(
        "/api/transactions?max_amount=50", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    descriptions = {item["description"] for item in data["items"]}
    # 25.50 UBER, 39.90 NETFLIX, 45.00 IFOOD — PIX 150 and SALARIO 8000 dropped
    assert descriptions == {"UBER TRIP", "NETFLIX", "IFOOD RESTAURANTE"}
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_list_transactions_filter_by_amount_range(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """Combining min_amount + max_amount filters to a closed range."""
    response = await client.get(
        "/api/transactions?min_amount=40&max_amount=60", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["description"] == "IFOOD RESTAURANTE"


@pytest.mark.asyncio
async def test_list_transactions_amount_filter_combines_with_type(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """Amount filters compose with other filters (here: type=debit)."""
    response = await client.get(
        "/api/transactions?max_amount=50&type=debit", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    descriptions = {item["description"] for item in data["items"]}
    # Among <=50: UBER (debit), NETFLIX (debit), IFOOD (debit). PIX 150 excluded by amount.
    assert descriptions == {"UBER TRIP", "NETFLIX", "IFOOD RESTAURANTE"}
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_list_transactions_filter_by_account_ids_multi(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    test_transactions: list[Transaction],
):
    """Passing multiple ``account_ids`` should include transactions from every account."""
    # Create a second account under the same user with one transaction.
    second_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=None,
        external_id="acc-ext-second",
        name="Poupança",
        type="savings",
        balance=Decimal("500.00"),
        currency="BRL",
    )
    session.add(second_account)
    await session.flush()
    extra_txn = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=second_account.id,
        category_id=None,
        description="APORTE POUPANCA",
        amount=Decimal("100.00"),
        date=date.today(),
        type="credit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(extra_txn)
    await session.commit()

    response = await client.get(
        f"/api/transactions?account_ids={test_account.id}&account_ids={second_account.id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    # 5 from test_account + 1 from second_account
    assert data["total"] == 6
    account_ids_in_response = {item["account_id"] for item in data["items"]}
    assert str(test_account.id) in account_ids_in_response
    assert str(second_account.id) in account_ids_in_response


@pytest.mark.asyncio
async def test_list_transactions_filter_by_account_ids_isolates_other_accounts(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    test_transactions: list[Transaction],
):
    """``account_ids`` should exclude txns from accounts not in the list."""
    second_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=None,
        external_id="acc-ext-isolated",
        name="Isolada",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(second_account)
    await session.flush()
    isolated_txn = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=second_account.id,
        category_id=None,
        description="ISOLATED TXN",
        amount=Decimal("10.00"),
        date=date.today(),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(isolated_txn)
    await session.commit()

    response = await client.get(
        f"/api/transactions?account_ids={second_account.id}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["description"] == "ISOLATED TXN"


@pytest.mark.asyncio
async def test_list_transactions_filter_by_date(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    # Use actual fixture transaction dates (UBER and IFOOD)
    uber_date = test_transactions[0].date.isoformat()
    ifood_date = test_transactions[1].date.isoformat()
    date_from = min(uber_date, ifood_date)
    date_to = max(uber_date, ifood_date)
    response = await client.get(
        f"/api/transactions?from={date_from}&to={date_to}", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2  # at least UBER and IFOOD


@pytest.mark.asyncio
async def test_get_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn_id = str(test_transactions[0].id)
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "UBER TRIP"
    assert data["source"] == "manual"


@pytest.mark.asyncio
async def test_get_transaction_not_found(client: AsyncClient, auth_headers, test_transactions):
    response = await client.get(
        "/api/transactions/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_transaction(
    client: AsyncClient, auth_headers, test_account: Account, test_categories: list[Category]
):
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "category_id": str(test_categories[0].id),
            "description": "Almoço restaurante",
            "amount": "32.50",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Almoço restaurante"
    assert data["source"] == "manual"
    assert data["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_create_transaction_auto_categorize(
    client: AsyncClient, auth_headers, test_account: Account,
    test_rules, test_categories: list[Category],
):
    """Transaction with UBER in description should auto-categorize to Transporte."""
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "description": "UBER TRIP CENTRO",
            "amount": "18.00",
            "date": "2026-02-21",
            "type": "debit",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["category_id"] == str(test_categories[1].id)  # Transporte


@pytest.mark.asyncio
async def test_create_transaction_invalid_account(
    client: AsyncClient, auth_headers, test_account
):
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": "00000000-0000-0000-0000-000000000000",
            "description": "Test",
            "amount": "10.00",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_transaction(
    client: AsyncClient, auth_headers, session: AsyncSession, test_workspace,
    test_user: User, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    payee = Payee(user_id=test_user.id, workspace_id=test_workspace.id, name="Netflix")
    session.add(payee)
    await session.commit()

    txn_id = str(test_transactions[4].id)  # NETFLIX, no category
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"category_id": str(test_categories[0].id), "payee_id": str(payee.id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category_id"] == str(test_categories[0].id)
    assert data["category"]["id"] == str(test_categories[0].id)
    assert data["payee_id"] == str(payee.id)
    assert data["payee_name"] == "Netflix"


@pytest.mark.asyncio
async def test_update_transaction_remove_category(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Setting category_id to null must clear an existing category."""
    txn_id = str(test_transactions[1].id)  # IFOOD — has category (Alimentação)
    assert test_transactions[1].category_id is not None

    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"category_id": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category_id"] is None

    # Verify the change persisted
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["category_id"] is None


@pytest.mark.asyncio
async def test_update_transaction_date(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
):
    """Regression: updating the date field must not fail with 'input should be none'."""
    txn_id = str(test_transactions[0].id)
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={"date": "2026-06-15"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["date"] == "2026-06-15"


@pytest.mark.asyncio
async def test_update_transaction_all_fields(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Regression: updating multiple fields including date must succeed."""
    txn_id = str(test_transactions[0].id)
    response = await client.patch(
        f"/api/transactions/{txn_id}",
        headers=auth_headers,
        json={
            "description": "Updated description",
            "amount": "999.99",
            "date": "2026-12-25",
            "type": "credit",
            "currency": "USD",
            "category_id": str(test_categories[0].id),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "Updated description"
    assert float(data["amount"]) == 999.99
    assert data["date"] == "2026-12-25"
    assert data["type"] == "credit"
    assert data["currency"] == "USD"
    assert data["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_delete_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn_id = str(test_transactions[4].id)  # NETFLIX
    response = await client.delete(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone
    response = await client.get(f"/api/transactions/{txn_id}", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_transaction_not_found(client: AsyncClient, auth_headers, test_transactions):
    response = await client.delete(
        "/api/transactions/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_transactions_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/transactions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_transaction_without_account_fails(
    client: AsyncClient, auth_headers, test_account
):
    """account_id is required — omitting it must return 422."""
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "description": "No account",
            "amount": "10.00",
            "date": "2026-02-20",
            "type": "debit",
        },
    )
    assert response.status_code == 422


# --- exclude_transfers tests ---

@pytest_asyncio.fixture
async def test_transactions_with_transfers(
    session: AsyncSession, test_user: User, test_account: Account,
) -> list[Transaction]:
    """Create a mix of regular and transfer transactions."""
    today = date.today()
    pair_id = uuid.uuid4()
    transactions = []
    data = [
        ("GROCERIES", Decimal("50.00"), today, "debit", None, None),
        ("SALARY", Decimal("3000.00"), today, "credit", None, None),
        ("Transfer out", Decimal("200.00"), today, "debit", None, pair_id),
        ("Transfer in", Decimal("200.00"), today, "credit", None, pair_id),
    ]
    for desc, amount, dt, typ, cat_id, transfer_id in data:
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=test_account.id,
            category_id=cat_id,
            description=desc,
            amount=amount,
            date=dt,
            type=typ,
            source="transfer" if transfer_id else "manual",
            transfer_pair_id=transfer_id,
            created_at=datetime.now(timezone.utc),
        )
        session.add(txn)
        transactions.append(txn)
    await session.commit()
    for txn in transactions:
        await session.refresh(txn)
    return transactions


@pytest.mark.asyncio
async def test_list_transactions_includes_transfers_by_default(
    client: AsyncClient, auth_headers, test_transactions_with_transfers,
):
    """Without exclude_transfers, all transactions including transfers are returned."""
    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4
    descriptions = [item["description"] for item in data["items"]]
    assert "Transfer out" in descriptions
    assert "Transfer in" in descriptions


@pytest.mark.asyncio
async def test_list_transactions_exclude_transfers(
    client: AsyncClient, auth_headers, test_transactions_with_transfers,
):
    """With exclude_transfers=true, transfer transactions are hidden."""
    response = await client.get(
        "/api/transactions?exclude_transfers=true", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    descriptions = [item["description"] for item in data["items"]]
    assert "GROCERIES" in descriptions
    assert "SALARY" in descriptions
    assert "Transfer out" not in descriptions
    assert "Transfer in" not in descriptions


@pytest.mark.asyncio
async def test_list_transactions_user_pnl_only(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions_with_transfers,
):
    """user_pnl_only returns only rows that count toward dashboard/user P&L."""
    for txn in test_transactions_with_transfers:
        if txn.description == "GROCERIES":
            txn.is_ignored = True
    settlement = Transaction(
        id=uuid.uuid4(),
        user_id=test_transactions_with_transfers[0].user_id,
        account_id=test_transactions_with_transfers[0].account_id,
        description="Settlement credit",
        amount=Decimal("75.00"),
        date=date.today(),
        type="credit",
        source="settlement",
        created_at=datetime.now(timezone.utc),
    )
    session.add(settlement)
    await session.commit()

    response = await client.get("/api/transactions?user_pnl_only=true", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert [item["description"] for item in data["items"]] == ["SALARY"]


@pytest.mark.asyncio
async def test_exclude_transfers_false_includes_all(
    client: AsyncClient, auth_headers, test_transactions_with_transfers,
):
    """Explicitly setting exclude_transfers=false still includes transfers."""
    response = await client.get(
        "/api/transactions?exclude_transfers=false", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4


@pytest.mark.asyncio
async def test_export_csv_format(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get("/api/transactions/export", headers=auth_headers)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    content = resp.text
    assert content.startswith("\ufeff")
    assert "date" in content
    assert "description" in content
    assert "amount" in content


@pytest.mark.asyncio
async def test_export_csv_with_type_filter(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get(
        "/api/transactions/export",
        params={"type": "debit"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_export_csv_uncategorized(client: AsyncClient, auth_headers, test_transactions):
    resp = await client.get(
        "/api/transactions/export",
        params={"uncategorized": "true"},
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_bulk_categorize(
    client: AsyncClient, auth_headers, test_transactions, test_categories,
):
    txn_id = str(test_transactions[4].id)
    resp = await client.patch(
        "/api/transactions/bulk-categorize",
        json={"transaction_ids": [txn_id], "category_id": str(test_categories[0].id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1


@pytest.mark.asyncio
async def test_create_transfer_api(client: AsyncClient, auth_headers, test_account):
    dest_resp = await client.post(
        "/api/accounts",
        json={"name": "Transfer Dest", "type": "savings", "balance": 0, "currency": "BRL"},
        headers=auth_headers,
    )
    dest_id = dest_resp.json()["id"]
    resp = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": dest_id,
            "description": "API Transfer",
            "amount": 500,
            "date": date.today().isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_transfer_invalid_account(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(uuid.uuid4()),
            "to_account_id": str(uuid.uuid4()),
            "description": "Bad Transfer",
            "amount": 100,
            "date": date.today().isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_transactions_summary(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    """The list response carries an income/expense/net summary across all
    matching rows (issue #185). Fixture: credits 8000 + 150, debits
    25.50 + 45.00 + 39.90."""
    response = await client.get("/api/transactions", headers=auth_headers)
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary is not None
    assert summary["income"] == pytest.approx(8150.0)
    assert summary["expense"] == pytest.approx(110.4)
    assert summary["net"] == pytest.approx(8039.6)
    assert summary["currency"]


@pytest.mark.asyncio
async def test_list_transactions_summary_spans_all_pages(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    """The summary covers every matching row, not just the current page —
    so a paginated request still totals the full result set."""
    response = await client.get(
        "/api/transactions?page=1&limit=2", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2  # page is capped
    assert data["summary"]["net"] == pytest.approx(8039.6)  # total is not


@pytest.mark.asyncio
async def test_list_transactions_summary_respects_filters(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction],
    test_categories: list[Category],
):
    """Filtering narrows the summary the same way it narrows the rows."""
    cat_id = test_categories[1].id  # Transporte → UBER TRIP, 25.50 debit
    response = await client.get(
        f"/api/transactions?category_id={cat_id}", headers=auth_headers
    )
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["income"] == pytest.approx(0.0)
    assert summary["expense"] == pytest.approx(25.5)
    assert summary["net"] == pytest.approx(-25.5)
