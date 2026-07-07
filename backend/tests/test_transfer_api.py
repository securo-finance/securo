import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.user import User


@pytest_asyncio.fixture
async def second_account(session: AsyncSession, test_user: User, test_connection: BankConnection) -> Account:
    """Create a second test account (same currency)."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-456",
        name="Poupança",
        type="savings",
        balance=Decimal("5000.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def usd_account(session: AsyncSession, test_user: User, test_connection: BankConnection) -> Account:
    """Create a USD test account for cross-currency tests."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-usd",
        name="USD Account",
        type="checking",
        balance=Decimal("1000.00"),
        currency="USD",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_create_same_currency_transfer(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 500.00,
            "date": date.today().isoformat(),
            "description": "Transfer to savings",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    # Check structure
    assert "debit" in data
    assert "credit" in data
    assert "transfer_pair_id" in data

    # Check debit
    assert data["debit"]["type"] == "debit"
    assert data["debit"]["source"] == "transfer"
    assert float(data["debit"]["amount"]) == 500.00
    assert data["debit"]["account_id"] == str(test_account.id)
    assert data["debit"]["transfer_pair_id"] == data["transfer_pair_id"]

    # Check credit
    assert data["credit"]["type"] == "credit"
    assert data["credit"]["source"] == "transfer"
    assert float(data["credit"]["amount"]) == 500.00
    assert data["credit"]["account_id"] == str(second_account.id)
    assert data["credit"]["transfer_pair_id"] == data["transfer_pair_id"]

    # Both share the same transfer_pair_id
    assert data["debit"]["transfer_pair_id"] == data["credit"]["transfer_pair_id"]


@pytest.mark.asyncio
async def test_create_cross_currency_transfer(
    client: AsyncClient, auth_headers, test_account: Account, usd_account: Account
):
    """Cross-currency transfer should convert amount."""
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(usd_account.id),
            "amount": 1000.00,
            "date": date.today().isoformat(),
            "description": "Transfer BRL to USD",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["debit"]["currency"] == "BRL"
    assert data["credit"]["currency"] == "USD"
    assert float(data["debit"]["amount"]) == 1000.00
    # Credit amount may differ due to FX conversion (or be 1000 if FX falls back to 1:1)
    assert float(data["credit"]["amount"]) > 0


@pytest.mark.asyncio
async def test_create_cross_currency_transfer_with_manual_fx_rate(
    client: AsyncClient, auth_headers, test_account: Account, usd_account: Account
):
    """Cross-currency transfer with manual FX rate should use provided rate."""
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(usd_account.id),
            "amount": 1000.00,
            "date": date.today().isoformat(),
            "description": "Transfer BRL to USD with manual rate",
            "fx_rate": 0.20,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["debit"]["currency"] == "BRL"
    assert data["credit"]["currency"] == "USD"
    assert float(data["debit"]["amount"]) == 1000.00
    # 1000 * 0.20 = 200.00
    assert float(data["credit"]["amount"]) == 200.00


@pytest.mark.asyncio
async def test_reject_same_account_transfer(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(test_account.id),
            "amount": 100.00,
            "date": date.today().isoformat(),
            "description": "Self transfer",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "same account" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reject_invalid_account_transfer(
    client: AsyncClient, auth_headers, test_account: Account
):
    fake_id = str(uuid.uuid4())
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": fake_id,
            "amount": 100.00,
            "date": date.today().isoformat(),
            "description": "Invalid transfer",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_cascades_to_paired_transaction(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    # Create transfer
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 200.00,
            "date": date.today().isoformat(),
            "description": "Transfer to delete",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    debit_id = data["debit"]["id"]
    credit_id = data["credit"]["id"]

    # Delete debit — should cascade to credit
    del_response = await client.delete(
        f"/api/transactions/{debit_id}", headers=auth_headers
    )
    assert del_response.status_code == 204

    # Both should be gone
    assert (await client.get(f"/api/transactions/{debit_id}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/transactions/{credit_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_update_cascades_to_paired_transaction(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    # Create transfer
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 300.00,
            "date": date.today().isoformat(),
            "description": "Original description",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    debit_id = data["debit"]["id"]
    credit_id = data["credit"]["id"]

    # Update debit description and date
    new_date = "2025-06-15"
    update_response = await client.patch(
        f"/api/transactions/{debit_id}",
        json={"description": "Updated description", "date": new_date},
        headers=auth_headers,
    )
    assert update_response.status_code == 200

    # Check credit was also updated
    credit_response = await client.get(
        f"/api/transactions/{credit_id}", headers=auth_headers
    )
    assert credit_response.status_code == 200
    credit_data = credit_response.json()
    assert credit_data["description"] == "Updated description"
    assert credit_data["date"] == new_date


@pytest.mark.asyncio
async def test_update_transfer_category_only_updates_edited_side_without_flag(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    second_account: Account,
    test_categories,
):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 180.00,
            "date": date.today().isoformat(),
            "description": "Transfer recategorize",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    debit_id = data["debit"]["id"]
    credit_id = data["credit"]["id"]

    update_response = await client.patch(
        f"/api/transactions/{debit_id}",
        json={"category_id": str(test_categories[0].id)},
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["category_id"] == str(test_categories[0].id)

    credit_response = await client.get(
        f"/api/transactions/{credit_id}", headers=auth_headers
    )
    assert credit_response.status_code == 200
    assert credit_response.json()["category_id"] is None


@pytest.mark.asyncio
async def test_update_transfer_category_can_apply_to_paired_transaction(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    second_account: Account,
    test_categories,
):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 220.00,
            "date": date.today().isoformat(),
            "description": "Transfer paired category",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    debit_id = data["debit"]["id"]
    credit_id = data["credit"]["id"]

    update_response = await client.patch(
        f"/api/transactions/{debit_id}",
        json={
            "category_id": str(test_categories[1].id),
            "apply_to_transfer_pair": True,
        },
        headers=auth_headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["category_id"] == str(test_categories[1].id)

    credit_response = await client.get(
        f"/api/transactions/{credit_id}", headers=auth_headers
    )
    assert credit_response.status_code == 200
    assert credit_response.json()["category_id"] == str(test_categories[1].id)


@pytest.mark.asyncio
async def test_clear_transfer_category_can_apply_to_paired_transaction(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    second_account: Account,
    test_categories,
):
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 260.00,
            "date": date.today().isoformat(),
            "description": "Transfer clear category",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    debit_id = data["debit"]["id"]
    credit_id = data["credit"]["id"]

    seed_response = await client.patch(
        f"/api/transactions/{debit_id}",
        json={
            "category_id": str(test_categories[0].id),
            "apply_to_transfer_pair": True,
        },
        headers=auth_headers,
    )
    assert seed_response.status_code == 200

    clear_response = await client.patch(
        f"/api/transactions/{debit_id}",
        json={"category_id": None, "apply_to_transfer_pair": True},
        headers=auth_headers,
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["category_id"] is None

    credit_response = await client.get(
        f"/api/transactions/{credit_id}", headers=auth_headers
    )
    assert credit_response.status_code == 200
    assert credit_response.json()["category_id"] is None


async def _create_manual_tx(
    client: AsyncClient,
    auth_headers: dict,
    account_id: uuid.UUID,
    *,
    type: str,
    amount: float,
    description: str,
    tx_date: str | None = None,
) -> dict:
    response = await client.post(
        "/api/transactions",
        json={
            "account_id": str(account_id),
            "description": description,
            "amount": amount,
            "type": type,
            "date": tx_date or date.today().isoformat(),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_link_existing_transactions_as_transfer(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    debit = await _create_manual_tx(
        client, auth_headers, test_account.id, type="debit", amount=200.00, description="Sent"
    )
    credit = await _create_manual_tx(
        client, auth_headers, second_account.id, type="credit", amount=200.00, description="Received"
    )

    response = await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [debit["id"], credit["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["debit"]["id"] == debit["id"]
    assert data["credit"]["id"] == credit["id"]
    assert data["debit"]["transfer_pair_id"] == data["credit"]["transfer_pair_id"]
    assert data["transfer_pair_id"] == data["debit"]["transfer_pair_id"]


@pytest.mark.asyncio
async def test_link_transfer_permissive_with_amount_mismatch(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Linking should succeed even if amounts differ — user is asserting intent."""
    debit = await _create_manual_tx(
        client, auth_headers, test_account.id, type="debit", amount=100.00, description="Sent with fee"
    )
    credit = await _create_manual_tx(
        client, auth_headers, second_account.id, type="credit", amount=98.50, description="Received minus fee"
    )

    response = await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [debit["id"], credit["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_link_transfer_rejects_same_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    debit = await _create_manual_tx(
        client, auth_headers, test_account.id, type="debit", amount=50.00, description="A"
    )
    credit = await _create_manual_tx(
        client, auth_headers, test_account.id, type="credit", amount=50.00, description="B"
    )

    response = await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [debit["id"], credit["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "different accounts" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_link_transfer_rejects_same_type(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    a = await _create_manual_tx(
        client, auth_headers, test_account.id, type="debit", amount=50.00, description="A"
    )
    b = await _create_manual_tx(
        client, auth_headers, second_account.id, type="debit", amount=50.00, description="B"
    )

    response = await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [a["id"], b["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "debit and one credit" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_link_transfer_rejects_already_linked(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    # Create an existing transfer
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 75.00,
            "date": date.today().isoformat(),
            "description": "Already linked",
        },
        headers=auth_headers,
    )
    debit_id = response.json()["debit"]["id"]

    # New unlinked transaction
    other = await _create_manual_tx(
        client, auth_headers, second_account.id, type="credit", amount=75.00, description="Other"
    )

    response = await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [debit_id, other["id"]]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "already part of a transfer" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_link_transfer_excludes_from_reports(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """After linking, both transactions should be excluded when exclude_transfers=true."""
    debit = await _create_manual_tx(
        client, auth_headers, test_account.id, type="debit", amount=300.00, description="To exclude"
    )
    credit = await _create_manual_tx(
        client, auth_headers, second_account.id, type="credit", amount=300.00, description="To exclude"
    )

    await client.post(
        "/api/transactions/link-transfer",
        json={"transaction_ids": [debit["id"], credit["id"]]},
        headers=auth_headers,
    )

    list_response = await client.get(
        "/api/transactions", params={"exclude_transfers": True}, headers=auth_headers
    )
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    ids = {tx["id"] for tx in items}
    assert debit["id"] not in ids
    assert credit["id"] not in ids


@pytest.mark.asyncio
async def test_transfer_candidates_returns_ranked_matches(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Closest date and amount candidates should rank first."""
    today = date.today()
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=500.00, description="Anchor",
        tx_date=today.isoformat(),
    )

    # Best match: same date, exact amount
    best = await _create_manual_tx(
        client, auth_headers, second_account.id,
        type="credit", amount=500.00, description="Best",
        tx_date=today.isoformat(),
    )
    # Same amount but a few days off
    far_date = await _create_manual_tx(
        client, auth_headers, second_account.id,
        type="credit", amount=500.00, description="Far date",
        tx_date=(today - __import__("datetime").timedelta(days=5)).isoformat(),
    )
    # Same date but different amount
    different_amount = await _create_manual_tx(
        client, auth_headers, second_account.id,
        type="credit", amount=499.00, description="Different amount",
        tx_date=today.isoformat(),
    )

    response = await client.get(
        f"/api/transactions/{anchor['id']}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 200
    candidates = response.json()
    ids = [c["id"] for c in candidates]
    assert best["id"] in ids
    assert different_amount["id"] in ids
    assert far_date["id"] in ids
    # Best (same date + exact amount) should rank first
    assert ids[0] == best["id"]
    # Same date with different amount should beat different date with same amount
    assert ids.index(different_amount["id"]) < ids.index(far_date["id"])


@pytest.mark.asyncio
async def test_transfer_candidates_excludes_same_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=100.00, description="Anchor",
    )
    same_account = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="credit", amount=100.00, description="Same account",
    )

    response = await client.get(
        f"/api/transactions/{anchor['id']}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert same_account["id"] not in ids


@pytest.mark.asyncio
async def test_transfer_candidates_excludes_same_type(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=100.00, description="Anchor",
    )
    same_type = await _create_manual_tx(
        client, auth_headers, second_account.id,
        type="debit", amount=100.00, description="Same type",
    )

    response = await client.get(
        f"/api/transactions/{anchor['id']}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert same_type["id"] not in ids


@pytest.mark.asyncio
async def test_transfer_candidates_excludes_already_linked(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=100.00, description="Anchor",
    )
    # Create a transfer pair that already exists in second_account
    pair_response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 100.00,
            "date": date.today().isoformat(),
            "description": "Existing transfer",
        },
        headers=auth_headers,
    )
    linked_credit_id = pair_response.json()["credit"]["id"]

    response = await client.get(
        f"/api/transactions/{anchor['id']}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert linked_credit_id not in ids


@pytest.mark.asyncio
async def test_transfer_candidates_respects_date_window(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    from datetime import timedelta
    today = date.today()
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=100.00, description="Anchor",
        tx_date=today.isoformat(),
    )
    out_of_window = await _create_manual_tx(
        client, auth_headers, second_account.id,
        type="credit", amount=100.00, description="Old",
        tx_date=(today - timedelta(days=60)).isoformat(),
    )

    response = await client.get(
        f"/api/transactions/{anchor['id']}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert out_of_window["id"] not in ids


@pytest.mark.asyncio
async def test_transfer_candidates_returns_404_for_unknown_anchor(
    client: AsyncClient, auth_headers
):
    fake_id = uuid.uuid4()
    response = await client.get(
        f"/api/transactions/{fake_id}/transfer-candidates",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_counterpart_from_debit(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Auto-create the credit counterpart for a debit anchor."""
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=200.00, description="Sent from checking",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    # Anchor stays the debit side; counterpart is the new credit.
    assert data["debit"]["id"] == anchor["id"]
    assert data["debit"]["account_id"] == str(test_account.id)
    assert data["credit"]["id"] != anchor["id"]
    assert data["credit"]["account_id"] == str(second_account.id)
    assert data["credit"]["type"] == "credit"
    assert data["credit"]["source"] == "transfer"
    assert float(data["credit"]["amount"]) == 200.00
    assert data["credit"]["description"] == "Sent from checking"

    # Both sides share the transfer pair id.
    assert data["debit"]["transfer_pair_id"] == data["transfer_pair_id"]
    assert data["credit"]["transfer_pair_id"] == data["transfer_pair_id"]


@pytest.mark.asyncio
async def test_create_counterpart_from_credit(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Auto-create the debit counterpart for a credit anchor."""
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="credit", amount=150.00, description="Money in",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["credit"]["id"] == anchor["id"]
    assert data["debit"]["id"] != anchor["id"]
    assert data["debit"]["account_id"] == str(second_account.id)
    assert data["debit"]["type"] == "debit"
    assert data["debit"]["source"] == "transfer"
    assert float(data["debit"]["amount"]) == 150.00


@pytest.mark.asyncio
async def test_create_counterpart_cross_currency(
    client: AsyncClient, auth_headers, test_account: Account, usd_account: Account
):
    """Counterpart in another currency takes the destination account's currency."""
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=1000.00, description="BRL to USD",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(usd_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()

    assert data["debit"]["currency"] == "BRL"
    assert data["credit"]["currency"] == "USD"
    # Amount may differ from the anchor's after FX conversion.
    assert float(data["credit"]["amount"]) > 0


@pytest.mark.asyncio
async def test_create_counterpart_rejects_same_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=50.00, description="Anchor",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(test_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "different account" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_counterpart_rejects_unknown_account(
    client: AsyncClient, auth_headers, test_account: Account
):
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=50.00, description="Anchor",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_counterpart_rejects_unknown_transaction(
    client: AsyncClient, auth_headers, second_account: Account
):
    response = await client.post(
        f"/api/transactions/{uuid.uuid4()}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_counterpart_rejects_already_linked(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """A transaction that is already part of a transfer cannot get another counterpart."""
    pair = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 75.00,
            "date": date.today().isoformat(),
            "description": "Already linked",
        },
        headers=auth_headers,
    )
    debit_id = pair.json()["debit"]["id"]

    response = await client.post(
        f"/api/transactions/{debit_id}/create-counterpart",
        json={"to_account_id": str(test_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "already part of a transfer" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_counterpart_preserves_anchor_category(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    second_account: Account,
    test_categories,
):
    """Marking a categorized transaction as a transfer keeps its category."""
    create_response = await client.post(
        "/api/transactions",
        json={
            "account_id": str(test_account.id),
            "description": "Categorized anchor",
            "amount": 120.00,
            "type": "debit",
            "date": date.today().isoformat(),
            "category_id": str(test_categories[0].id),
        },
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    anchor = create_response.json()
    assert anchor["category_id"] == str(test_categories[0].id)

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201

    refreshed = await client.get(
        f"/api/transactions/{anchor['id']}", headers=auth_headers
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["category_id"] == str(test_categories[0].id)


@pytest.mark.asyncio
async def test_create_counterpart_excludes_pair_from_reports(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Both sides drop out of the list when exclude_transfers=true."""
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=300.00, description="To exclude",
    )

    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    counterpart_id = response.json()["credit"]["id"]

    list_response = await client.get(
        "/api/transactions", params={"exclude_transfers": True}, headers=auth_headers
    )
    assert list_response.status_code == 200
    ids = {tx["id"] for tx in list_response.json()["items"]}
    assert anchor["id"] not in ids
    assert counterpart_id not in ids


@pytest.mark.asyncio
async def test_create_counterpart_cascades_on_delete(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    """Deleting the anchor removes the auto-created counterpart too."""
    anchor = await _create_manual_tx(
        client, auth_headers, test_account.id,
        type="debit", amount=90.00, description="Cascade delete",
    )
    response = await client.post(
        f"/api/transactions/{anchor['id']}/create-counterpart",
        json={"to_account_id": str(second_account.id)},
        headers=auth_headers,
    )
    assert response.status_code == 201
    counterpart_id = response.json()["credit"]["id"]

    del_response = await client.delete(
        f"/api/transactions/{anchor['id']}", headers=auth_headers
    )
    assert del_response.status_code == 204
    assert (await client.get(f"/api/transactions/{anchor['id']}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/transactions/{counterpart_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_transfers_appear_in_transaction_list(
    client: AsyncClient, auth_headers, test_account: Account, second_account: Account
):
    # Create transfer
    response = await client.post(
        "/api/transactions/transfer",
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(second_account.id),
            "amount": 100.00,
            "date": date.today().isoformat(),
            "description": "Listed transfer",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    transfer_pair_id = response.json()["transfer_pair_id"]

    # List transactions
    list_response = await client.get("/api/transactions", headers=auth_headers)
    assert list_response.status_code == 200
    items = list_response.json()["items"]

    transfer_txns = [tx for tx in items if tx.get("transfer_pair_id") == transfer_pair_id]
    assert len(transfer_txns) == 2
    assert {tx["type"] for tx in transfer_txns} == {"debit", "credit"}
