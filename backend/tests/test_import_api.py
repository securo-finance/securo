import pytest
from httpx import AsyncClient

from app.models.account import Account


@pytest.mark.asyncio
async def test_preview_csv_import(client: AsyncClient, auth_headers, test_account):
    csv_content = b"data,descricao,valor\n10/02/2026,UBER TRIP,-25.50\n12/02/2026,PIX RECEBIDO,150.00\n"
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
async def test_preview_invalid_file(client: AsyncClient, auth_headers, test_account):
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bad.csv", b"col1,col2,col3\na,b,c\n", "text/csv")},
    )
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert data["detail"]  # must not be empty


@pytest.mark.asyncio
async def test_preview_invalid_file_returns_specific_error(
    client: AsyncClient, auth_headers, test_account
):
    """Error response should tell the user what columns were found and what is expected."""
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bad.csv", b"foo,bar,baz\n1,2,3\n", "text/csv")},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    # Should mention what columns were found in the file
    assert "foo" in detail
    # Should mention what columns are expected
    assert "date" in detail and "description" in detail and "amount" in detail


@pytest.mark.asyncio
async def test_import_transactions(
    client: AsyncClient, auth_headers, test_account: Account
):
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
                {"description": "UBER TRIP", "amount": "25.50", "date": "2026-02-10", "type": "debit"},
                {"description": "PIX RECEBIDO", "amount": "150.00", "date": "2026-02-15", "type": "credit"},
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
    client: AsyncClient, auth_headers, test_account: Account,
):
    payload = {
        "account_id": str(test_account.id),
        "transactions": [
            {"description": "REPEATED CSV", "amount": "10.00", "date": "2026-03-01", "type": "debit"},
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
                {"description": "TEST TXN", "amount": "10.00", "date": "2026-02-20", "type": "debit"},
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
                {"description": "TO DELETE", "amount": "50.00", "date": "2026-02-20", "type": "debit"},
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
