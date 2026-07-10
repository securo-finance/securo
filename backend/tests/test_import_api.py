import json

import pytest
from httpx import AsyncClient

from app.models.account import Account


@pytest.mark.asyncio
async def test_preview_csv_import(client: AsyncClient, auth_headers, test_account):
    csv_content = (
        b"data,descricao,valor\n10/02/2026,UBER TRIP,-25.50\n12/02/2026,PIX RECEBIDO,150.00\n"
    )
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("extrato.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["detected_format"] == "csv"
    assert len(data["transactions"]) == 2
    assert data["transactions"][0]["description"] == "UBER TRIP"
    assert data["transactions"][0]["type"] == "debit"
    assert data["transactions"][1]["type"] == "credit"


@pytest.mark.asyncio
async def test_preview_csv_returns_columns(client: AsyncClient, auth_headers, test_account):
    """The preview response exposes the CSV header columns for the mapping UI."""
    csv_content = b"Posted On,Memo Line,Movement\n2026-01-10,COFFEE,-12.50\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bank.csv", csv_content, "text/csv")},
        data={
            "column_mapping": json.dumps(
                {
                    "date": "Posted On",
                    "description": "Memo Line",
                    "amount": "Movement",
                }
            )
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["csv_columns"] == ["Posted On", "Memo Line", "Movement"]
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["description"] == "COFFEE"
    assert data["transactions"][0]["amount"] == "12.50"


@pytest.mark.asyncio
async def test_preview_csv_with_column_mapping(client: AsyncClient, auth_headers, test_account):
    """A CSV with non-standard headers parses once columns are mapped."""
    csv_content = b"transaction_date,details,value\n15/02/2026,GROCERY STORE,-80.00\n"
    # Without a mapping the description column can't be auto-detected: the
    # preview soft-fails (200) with a parse_error and no transactions.
    fail = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("export.csv", csv_content, "text/csv")},
    )
    assert fail.status_code == 200
    assert fail.json()["parse_error"]
    assert fail.json()["transactions"] == []

    # With a mapping it parses successfully.
    ok = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("export.csv", csv_content, "text/csv")},
        data={
            "column_mapping": json.dumps(
                {
                    "date": "transaction_date",
                    "description": "details",
                    "amount": "value",
                }
            )
        },
    )
    assert ok.status_code == 200
    data = ok.json()
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["description"] == "GROCERY STORE"
    assert data["transactions"][0]["type"] == "debit"


@pytest.mark.asyncio
async def test_preview_invalid_column_mapping_json(client: AsyncClient, auth_headers, test_account):
    """A malformed column_mapping payload returns a 400 instead of a 500."""
    csv_content = b"date,description,amount\n2026-01-10,X,-1.00\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("export.csv", csv_content, "text/csv")},
        data={"column_mapping": "{not valid json"},
    )
    assert response.status_code == 400
    assert "column_mapping" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_unrecognized_csv_soft_fails_with_columns(
    client: AsyncClient, auth_headers, test_account
):
    """A CSV whose columns can't be auto-detected returns a soft failure (200)
    with the detected columns, so the UI can offer column mapping."""
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bad.csv", b"col1,col2,col3\na,b,c\n", "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["detected_format"] == "csv"
    assert data["transactions"] == []
    assert data["csv_columns"] == ["col1", "col2", "col3"]
    assert data["parse_error"]  # must not be empty


@pytest.mark.asyncio
async def test_preview_unrecognized_csv_parse_error_is_specific(
    client: AsyncClient, auth_headers, test_account
):
    """The parse_error should tell the user what columns were found and what is expected."""
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bad.csv", b"foo,bar,baz\n1,2,3\n", "text/csv")},
    )
    assert response.status_code == 200
    parse_error = response.json()["parse_error"]
    # Should mention what columns were found in the file
    assert "foo" in parse_error
    # Should mention what columns are expected
    assert "date" in parse_error and "description" in parse_error and "amount" in parse_error


@pytest.mark.asyncio
async def test_import_transactions(client: AsyncClient, auth_headers, test_account: Account):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "UBER TRIP",
                    "amount": "25.50",
                    "date": "2026-02-10",
                    "type": "debit",
                },
                {
                    "description": "PIX RECEBIDO",
                    "amount": "150.00",
                    "date": "2026-02-15",
                    "type": "credit",
                },
            ],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["imported"] == 2


@pytest.mark.asyncio
async def test_import_to_invalid_account(client: AsyncClient, auth_headers, test_account):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": "00000000-0000-0000-0000-000000000000",
            "transactions": [
                {
                    "description": "Test",
                    "amount": "10.00",
                    "date": "2026-02-20",
                    "type": "debit",
                },
            ],
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_import_unauthenticated(client: AsyncClient, clean_db):
    response = await client.post(
        "/api/transactions/import/preview",
        files={"file": ("test.csv", b"a,b,c", "text/csv")},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_import_creates_log(client: AsyncClient, auth_headers, test_account: Account):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "UBER TRIP",
                    "amount": "25.50",
                    "date": "2026-02-10",
                    "type": "debit",
                },
                {
                    "description": "PIX RECEBIDO",
                    "amount": "150.00",
                    "date": "2026-02-15",
                    "type": "credit",
                },
            ],
            "filename": "extrato.csv",
            "detected_format": "csv",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["imported"] == 2
    assert "import_log_id" in data


@pytest.mark.asyncio
async def test_import_csv_with_duplicate_detection_disabled_allows_reimport(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
):
    payload = {
        "account_id": str(test_account.id),
        "transactions": [
            {
                "description": "REPEATED CSV",
                "amount": "10.00",
                "date": "2026-03-01",
                "type": "debit",
            },
        ],
        "filename": "repeated.csv",
        "detected_format": "csv",
        "detect_duplicates": False,
    }

    first = await client.post("/api/transactions/import", headers=auth_headers, json=payload)
    assert first.status_code == 201
    assert first.json()["imported"] == 1
    assert first.json()["skipped"] == 0

    second = await client.post("/api/transactions/import", headers=auth_headers, json=payload)
    assert second.status_code == 201
    assert second.json()["imported"] == 1
    assert second.json()["skipped"] == 0


@pytest.mark.asyncio
async def test_list_import_logs(client: AsyncClient, auth_headers, test_account: Account):
    # Create an import first
    await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "TEST TXN",
                    "amount": "10.00",
                    "date": "2026-02-20",
                    "type": "debit",
                },
            ],
            "filename": "test.csv",
            "detected_format": "csv",
        },
    )

    response = await client.get("/api/import-logs", headers=auth_headers)
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) >= 1
    log = logs[0]
    assert log["filename"] == "test.csv"
    assert log["format"] == "csv"
    assert log["transaction_count"] == 1


@pytest.mark.asyncio
async def test_delete_import_log(client: AsyncClient, auth_headers, test_account: Account):
    # Create an import
    resp = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "TO DELETE",
                    "amount": "50.00",
                    "date": "2026-02-20",
                    "type": "debit",
                },
            ],
            "filename": "delete_me.csv",
            "detected_format": "csv",
        },
    )
    import_log_id = resp.json()["import_log_id"]

    # Delete it
    delete_resp = await client.delete(f"/api/import-logs/{import_log_id}", headers=auth_headers)
    assert delete_resp.status_code == 204

    # Verify it's gone
    logs_resp = await client.get("/api/import-logs", headers=auth_headers)
    log_ids = [entry["id"] for entry in logs_resp.json()]
    assert import_log_id not in log_ids


async def test_preview_returns_suggested_categories(
    client: AsyncClient, auth_headers, test_account, test_categories, test_rules
):
    csv_content = (
        b"data,descricao,valor\n10/02/2026,UBER TRIP,-25.50\n12/02/2026,UNKNOWN TX,150.00\n"
    )
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("extrato.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    txns = data["transactions"]
    assert len(txns) == 2

    uber = txns[0]
    assert uber["description"] == "UBER TRIP"
    assert uber["suggested_category_id"] == str(test_categories[1].id)
    assert uber["suggested_category_name"] == "Transporte"

    unknown = txns[1]
    assert unknown["description"] == "UNKNOWN TX"
    assert unknown["suggested_category_id"] is None
    assert unknown["suggested_category_name"] is None


async def test_import_with_excluded_transactions(
    client: AsyncClient, auth_headers, test_account: Account
):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "INCLUDED",
                    "amount": "25.50",
                    "date": "2026-02-10",
                    "type": "debit",
                    "excluded": False,
                },
                {
                    "description": "EXCLUDED",
                    "amount": "100.00",
                    "date": "2026-02-11",
                    "type": "debit",
                    "excluded": True,
                },
                {
                    "description": "ALSO_INCLUDED",
                    "amount": "50.00",
                    "date": "2026-02-12",
                    "type": "credit",
                    "excluded": False,
                },
            ],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["imported"] == 2
    assert data["excluded"] == 1
    assert data["skipped"] == 0


async def test_import_with_category_override(
    client: AsyncClient, auth_headers, test_account: Account, test_categories: list
):
    override_cat_id = str(test_categories[0].id)
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "GROCERY",
                    "amount": "25.50",
                    "date": "2026-02-10",
                    "type": "debit",
                    "category_id": override_cat_id,
                },
            ],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["imported"] == 1

    import_log_id = data["import_log_id"]
    logs_resp = await client.get("/api/import-logs", headers=auth_headers)
    log = next(entry for entry in logs_resp.json() if entry["id"] == import_log_id)
    assert log["transaction_count"] == 1
