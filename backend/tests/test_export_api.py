import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest
import pyzipper
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.user import User


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
        assert metadata["format_version"] == "1.0"
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
    assert "securo-backup-" in disposition
    assert ".zip" in disposition

    buf = io.BytesIO(response.content)
    with zipfile.ZipFile(buf) as zf:
        expected_files = [
            "accounts.json",
            "transactions.json",
            "categories.json",
            "category_groups.json",
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


@pytest.mark.asyncio
async def test_backup_with_assets(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user: User,
):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Export Asset",
        type="other", currency="BRL", purchase_price=Decimal("1000"),
    )
    session.add(asset)
    await session.flush()
    av = AssetValue(
        id=uuid.uuid4(), asset_id=asset.id,
        amount=Decimal("1200"), date=date.today(),
    )
    session.add(av)
    await session.commit()

    resp = await client.get("/api/export/backup", headers=auth_headers)
    assert resp.status_code == 200
    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf) as zf:
        assets = json.loads(zf.read("assets.json"))
        asset_values = json.loads(zf.read("asset_values.json"))
        assert len(assets) >= 1
        assert len(asset_values) >= 1


@pytest.mark.asyncio
async def test_backup_metadata_structure(client: AsyncClient, auth_headers):
    resp = await client.get("/api/export/backup", headers=auth_headers)
    assert resp.status_code == 200
    assert "application/zip" in resp.headers.get("content-type", "")
    buf = io.BytesIO(resp.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "metadata.json" in names
        meta = json.loads(zf.read("metadata.json"))
        assert "export_date" in meta
        assert meta["format_version"] == "1.0"
        assert "entity_counts" in meta


@pytest.mark.asyncio
async def test_backup_post_without_password_is_a_plain_zip(client: AsyncClient, auth_headers):
    """The password is optional: omitting it must not change the archive."""
    resp = await client.post("/api/export/backup", json={}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert json.loads(zf.read("metadata.json"))["format_version"] == "1.0"


@pytest.mark.asyncio
async def test_backup_post_with_password_encrypts_the_entries(
    client: AsyncClient, auth_headers, test_account: Account
):
    resp = await client.post(
        "/api/export/backup", json={"password": "correct horse battery"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    # Readable with the password, and the data really is in there.
    with pyzipper.AESZipFile(io.BytesIO(resp.content)) as zf:
        zf.setpassword(b"correct horse battery")
        accounts = json.loads(zf.read("accounts.json"))
    assert any(a["name"] == test_account.name for a in accounts)

    # ...and unreadable without it, which is the whole point.
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "accounts.json" in zf.namelist()
        with pytest.raises(RuntimeError):
            zf.read("accounts.json")


@pytest.mark.asyncio
async def test_backup_post_rejects_the_wrong_password(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/export/backup", json={"password": "correct horse battery"}, headers=auth_headers
    )

    with pyzipper.AESZipFile(io.BytesIO(resp.content)) as zf:
        zf.setpassword(b"wrong horse battery")
        with pytest.raises(RuntimeError):
            zf.read("metadata.json")


@pytest.mark.asyncio
async def test_backup_post_rejects_a_short_password(client: AsyncClient, auth_headers):
    """Refused up front rather than written into an archive that looks safe."""
    resp = await client.post("/api/export/backup", json={"password": "short"}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backup_post_unauthenticated(client: AsyncClient):
    resp = await client.post("/api/export/backup", json={"password": "correct horse battery"})
    assert resp.status_code == 401
