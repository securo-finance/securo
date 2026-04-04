import io
import json
import zipfile

import pytest
from httpx import AsyncClient

from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction


@pytest.mark.asyncio
async def test_backup_unauthenticated(client: AsyncClient):
    response = await client.get("/api/export/backup")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_backup_empty(client: AsyncClient, auth_headers):
    response = await client.get("/api/export/backup", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "metadata.json" in names
        assert "accounts.json" in names
        assert "transactions.json" in names

        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["format_version"] == "2.0"
        assert metadata["compatibility"]["legacy_category_groups_included"] is False
        assert metadata["compatibility"]["category_budget_source"] == "categories"
        assert "export_date" in metadata
        for count in metadata["entity_counts"].values():
            assert count == 0


@pytest.mark.asyncio
async def test_backup_with_data(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    test_transactions: list[Transaction],
    test_categories: list[Category],
    test_rules: list[Rule],
):
    response = await client.get("/api/export/backup", headers=auth_headers)
    assert response.status_code == 200

    # Verify Content-Disposition header contains filename
    disposition = response.headers.get("content-disposition", "")
    assert "flux-backup-" in disposition
    assert ".zip" in disposition

    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf) as zf:
        expected_files = [
            "accounts.json",
            "transactions.json",
            "categories.json",
            "rules.json",
            "recurring_transactions.json",
            "budgets.json",
            "assets.json",
            "asset_values.json",
            "import_logs.json",
            "metadata.json",
        ]
        for fname in expected_files:
            assert fname in zf.namelist(), f"{fname} missing from ZIP"

        # Verify entity counts match
        metadata = json.loads(zf.read("metadata.json"))
        counts = metadata["entity_counts"]
        assert metadata["format_version"] == "2.0"
        assert metadata["compatibility"]["legacy_category_groups_included"] is False
        assert counts["accounts"] == 1
        assert counts["transactions"] == len(test_transactions)
        assert counts["categories"] == len(test_categories)
        assert counts["rules"] == len(test_rules)

        # Verify JSON content is parseable and has expected fields
        accounts = json.loads(zf.read("accounts.json"))
        assert len(accounts) == 1
        assert accounts[0]["name"] == "Conta Corrente"

        transactions = json.loads(zf.read("transactions.json"))
        assert len(transactions) == len(test_transactions)
        # Verify UUIDs are serialized as strings
        assert isinstance(transactions[0]["id"], str)
        # Verify dates are serialized as ISO strings
        assert isinstance(transactions[0]["date"], str)
        # Verify decimals are serialized as strings
        assert isinstance(transactions[0]["amount"], str)
