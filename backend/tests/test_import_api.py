import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.import_log import ImportLog
from app.models.transaction import Transaction


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
async def test_preview_csv_returns_columns(client: AsyncClient, auth_headers, test_account):
    """The preview response exposes the CSV header columns for the mapping UI."""
    csv_content = b"Posted On,Memo Line,Movement\n2026-01-10,COFFEE,-12.50\n"
    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("bank.csv", csv_content, "text/csv")},
        data={"column_mapping": json.dumps({
            "date": "Posted On",
            "description": "Memo Line",
            "amount": "Movement",
        })},
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
        data={"column_mapping": json.dumps({
            "date": "transaction_date",
            "description": "details",
            "amount": "value",
        })},
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
    assert response.headers.get("X-Import-Outcome") == "not-committed"


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
async def test_list_import_logs_returns_account_currency(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Totals are in the account's currency, not the user's display one (#881)."""
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    test_workspace.default_currency = "USD"
    eur_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Euro Account",
        type="checking",
        balance=0,
        currency="EUR",
    )
    session.add(eur_account)
    await session.commit()

    resp = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(eur_account.id),
            "transactions": [
                {"description": "EUR TXN", "amount": "25.00", "date": "2026-02-20", "type": "debit"},
            ],
            "filename": "eur.csv",
            "detected_format": "csv",
        },
    )
    assert resp.status_code == 201

    logs = (await client.get("/api/import-logs", headers=auth_headers)).json()
    log = next(entry for entry in logs if entry["filename"] == "eur.csv")
    assert log["account_currency"] == "EUR"
    assert log["account_name"] == "Euro Account"
    assert float(log["total_debit"]) == 25.0


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


async def test_preview_returns_suggested_categories(
    client: AsyncClient, auth_headers, test_account, test_categories, test_rules
):
    csv_content = b"data,descricao,valor\n10/02/2026,UBER TRIP,-25.50\n12/02/2026,UNKNOWN TX,150.00\n"
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


async def test_preview_uses_csv_category_as_default(
    client: AsyncClient, auth_headers, test_categories
):
    csv_content = (
        "data,descricao,valor,categoria\n"
        "10/02/2026,COMPRA QUALQUER,-25.50,Alimenta\u00e7\u00e3o\n"
    ).encode("utf-8")

    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("extrato.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    txn = response.json()["transactions"][0]
    assert txn["suggested_category_id"] == str(test_categories[0].id)
    assert txn["suggested_category_name"] == "Alimenta\u00e7\u00e3o"


async def test_preview_rule_overrides_csv_category_default(
    client: AsyncClient, auth_headers, test_categories, test_rules
):
    csv_content = (
        "data,descricao,valor,categoria\n"
        "10/02/2026,UBER TRIP,-25.50,Alimenta\u00e7\u00e3o\n"
    ).encode("utf-8")

    response = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("extrato.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    txn = response.json()["transactions"][0]
    assert txn["suggested_category_id"] == str(test_categories[1].id)
    assert txn["suggested_category_name"] == "Transporte"


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
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    test_categories: list,
    test_rules,
    session: AsyncSession,
):
    override_cat_id = str(test_categories[0].id)
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "UBER EXPLICIT OVERRIDE",
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

    result = await session.execute(
        select(Transaction).where(
            Transaction.import_id == uuid.UUID(import_log_id),
        )
    )
    imported = result.scalar_one()
    assert imported.category_id == test_categories[0].id


async def test_import_rule_overrides_csv_category_default(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    test_categories: list,
    test_rules,
    session: AsyncSession,
):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "UBER CSV DEFAULT",
                    "amount": "25.50",
                    "date": "2026-02-11",
                    "type": "debit",
                    "category_name": test_categories[0].name,
                },
            ],
        },
    )

    assert response.status_code == 201
    import_log_id = uuid.UUID(response.json()["import_log_id"])
    result = await session.execute(
        select(Transaction).where(Transaction.import_id == import_log_id)
    )
    imported = result.scalar_one()
    assert imported.category_id == test_categories[1].id


async def test_import_uses_csv_category_when_no_rule_matches(
    client: AsyncClient,
    auth_headers,
    test_account: Account,
    test_categories: list,
    test_rules,
    session: AsyncSession,
):
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": [
                {
                    "description": "NO MATCH CSV DEFAULT",
                    "amount": "25.50",
                    "date": "2026-02-12",
                    "type": "debit",
                    "category_name": test_categories[0].name,
                },
            ],
        },
    )

    assert response.status_code == 201
    import_log_id = uuid.UUID(response.json()["import_log_id"])
    result = await session.execute(
        select(Transaction).where(Transaction.import_id == import_log_id)
    )
    imported = result.scalar_one()
    assert imported.category_id == test_categories[0].id


@pytest.mark.parametrize("detect_duplicates", [True, False])
async def test_sequential_import_rejection_preserves_committed_bucket(
    client, auth_headers, session, test_account, test_user, test_workspace, detect_duplicates,
):
    """Real route: first commits, second is rejected before writes, then only
    the restored second destination is retried. No idempotency assumption."""
    missing_id = uuid.uuid4()
    rows = [{"description": "Synthetic retained bucket", "amount": "5.00", "date": "2026-01-05", "type": "debit"}]
    payload = {"transactions": rows, "filename": "synthetic.csv", "detected_format": "csv", "detect_duplicates": detect_duplicates}
    first = await client.post("/api/transactions/import", headers=auth_headers, json={**payload, "account_id": str(test_account.id)})
    assert first.status_code == 201
    assert first.json()["imported"] == 1
    failed = await client.post("/api/transactions/import", headers=auth_headers, json={**payload, "account_id": str(missing_id)})
    assert failed.status_code == 404
    assert failed.headers["X-Import-Outcome"] == "not-committed"
    assert len((await session.execute(select(Transaction))).scalars().all()) == 1
    assert len((await session.execute(select(ImportLog))).scalars().all()) == 1
    session.add(Account(id=missing_id, user_id=test_user.id, workspace_id=test_workspace.id, name="Synthetic restored target", type="checking", currency="USD", balance=0))
    await session.commit()
    resumed = await client.post("/api/transactions/import", headers=auth_headers, json={**payload, "account_id": str(missing_id)})
    assert resumed.status_code == 201
    assert resumed.json()["imported"] == 1
    transactions = (await session.execute(select(Transaction))).scalars().all()
    logs = (await session.execute(select(ImportLog))).scalars().all()
    assert len(transactions) == len(logs) == 2
    assert sum(tx.account_id == test_account.id for tx in transactions) == 1
    assert sum(str(log.id) == first.json()["import_log_id"] for log in logs) == 1


async def test_failure_after_commit_is_not_marked_safe_to_replay(
    client, auth_headers, session, test_account, monkeypatch,
):
    """Inject a lost acknowledgement after the real import service commits."""
    from fastapi import HTTPException
    from app.services import import_service

    original = import_service.import_transactions

    async def commit_then_lose_response(*args, **kwargs):
        await original(*args, **kwargs)
        raise HTTPException(status_code=503, detail="Synthetic lost acknowledgement")

    monkeypatch.setattr(import_service, "import_transactions", commit_then_lose_response)
    response = await client.post(
        "/api/transactions/import", headers=auth_headers,
        json={"account_id": str(test_account.id), "filename": "synthetic-uncertain.csv", "transactions": [
            {"description": "Synthetic committed charge", "amount": "5.00", "date": "2026-01-05", "type": "debit"},
        ]},
    )
    assert response.status_code == 503
    assert "X-Import-Outcome" not in response.headers
    assert len((await session.execute(select(Transaction))).scalars().all()) == 1
    assert len((await session.execute(select(ImportLog))).scalars().all()) == 1


@pytest.mark.parametrize("flip", [None, True, False])
async def test_confirmed_monarch_preview_roundtrips_and_date_override(
    client, auth_headers, test_account, flip,
):
    content = b"Date,Description,Amount,Category\n03/05/2026,Synthetic charge,10.00,Shopping\n"
    options = {"preset": "monarch"}
    if flip is not None:
        options["flip_amount"] = str(flip).lower()
    for extra in [{}, {"dry_run": "true", "account_id": str(test_account.id)}, {}]:
        response = await client.post(
            "/api/transactions/import/preview", headers=auth_headers,
            files={"file": ("synthetic.csv", content, "text/csv")}, data={**options, **extra},
        )
        assert response.status_code == 200
        row = response.json()["transactions"][0]
        assert row["type"] == ("credit" if flip is False else "debit")
        assert row["date"] == "2026-03-05"
    explicit_auto = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("synthetic.csv", content, "text/csv")}, data={**options, "date_format": "auto"},
    )
    assert explicit_auto.status_code == 200
    assert explicit_auto.json()["transactions"][0]["date"] == "2026-05-03"


@pytest.mark.parametrize("preset", [None, "auto", "generic", "monarch"])
@pytest.mark.parametrize("with_account", [False, True])
async def test_categorized_headers_do_not_confirm_monarch(
    client, auth_headers, test_account, preset, with_account,
):
    header = "Date,Description,Amount,Category" + (",Account" if with_account else "")
    row = "03/05/2026,Synthetic credit,10.00,Shopping" + (",Synthetic" if with_account else "")
    response = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("categorized.csv", f"{header}\n{row}\n".encode(), "text/csv")},
        data={"preset": preset} if preset else {},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parse_error"] is None
    assert data["preset"] == ("monarch" if preset == "monarch" else None)
    assert data["transactions"][0]["type"] == ("debit" if preset == "monarch" else "credit")
    assert data["transactions"][0]["date"] == ("2026-03-05" if preset == "monarch" else "2026-05-03")


# --- PR-3 / Monarch preset (design packet §8.3) ---------------------------

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "import"


def _monarch_fixture(name: str = "monarch-combined") -> bytes:
    return _FIXTURES.joinpath(f"{name}.csv").read_bytes()


@pytest.mark.asyncio
async def test_preview_monarch_preset_form_field(
    client: AsyncClient, auth_headers, test_account
):
    """Explicit Monarch applies the preset; auto/absent and generic decline
    header-only inference. Unknown selections are a 400, not a crash."""
    content = _monarch_fixture()

    forced = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"preset": "monarch"},
    )
    assert forced.status_code == 200
    data = forced.json()
    assert data["preset"] == "monarch"
    # Preset sign convention applied: first row is income -1200.00 → credit.
    assert data["transactions"][0]["type"] == "credit"
    assert data["transactions"][0]["amount"] == "1200.00"

    # Explicit flip_amount=false beats the preset's sign flag (§5.2/§7.1).
    unflipped = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"preset": "monarch", "flip_amount": "false"},
    )
    assert unflipped.status_code == 200
    assert unflipped.json()["transactions"][0]["type"] == "debit"

    # Auto (no field) does not establish provenance from ordinary headers.
    auto = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
    )
    assert auto.status_code == 200
    assert auto.json()["preset"] is None

    # Generic declines: no preset echoed, mapping dropdowns take over.
    generic = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"preset": "generic"},
    )
    assert generic.status_code == 200
    assert generic.json()["preset"] is None
    assert generic.json()["parse_error"]  # description column not auto-detectable

    # Unknown preset value is rejected explicitly.
    unknown = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"preset": "quicken"},
    )
    assert unknown.status_code == 400
    assert "Unknown preset" in unknown.json()["detail"]


@pytest.mark.asyncio
async def test_preview_dry_run_requires_account_and_is_read_only(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account
):
    """§7.3/§8.2: dry_run without account_id is a 400; with one it returns
    counts and writes nothing — the transactions table is untouched."""
    from sqlalchemy import func

    content = _monarch_fixture()

    missing = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"dry_run": "true"},
    )
    assert missing.status_code == 400
    assert "account_id" in missing.json()["detail"]

    count_before = (
        await session.execute(select(func.count()).select_from(Transaction))
    ).scalar_one()

    ok = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", content, "text/csv")},
        data={"preset": "monarch", "dry_run": "true", "account_id": str(test_account.id)},
    )
    assert ok.status_code == 200
    summary = ok.json()["dry_run"]
    assert summary is not None
    assert set(summary) == {
        "rows_in", "would_import", "would_skip_dupes", "failed_rows", "unmapped_categories",
    }
    assert summary["rows_in"] == 17  # 14 parsed + 3 failed rows
    assert summary["would_import"] == 14
    assert summary["would_skip_dupes"] == 0
    assert summary["failed_rows"] == 3

    await session.rollback()
    count_after = (
        await session.execute(select(func.count()).select_from(Transaction))
    ).scalar_one()
    assert count_after == count_before


@pytest.mark.asyncio
async def test_legacy_preview_matches_per_account_import_without_cross_account_dupes(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Single-target files only: a synced twin in A must not consume B's rows.

    This is not combined-file routing or recurring-placeholder (#886) coverage.
    """
    accounts = [
        Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, type="checking", balance=0, currency=test_workspace.default_currency,
        )
        for name in ("Synthetic Target A", "Synthetic Target B")
    ]
    account_a, account_b = accounts
    session.add_all(accounts)
    twin = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account_a.id, external_id="synthetic-fernwood-sync",
        description="Fernwood Coffee", amount=Decimal("4.99"),
        currency=account_a.currency, date=date(2026, 3, 16), type="debit",
        source="sync", status="posted",
    )
    session.add(twin)
    await session.commit()

    # Each file represents only its named account, even though the rows match.
    files = {
        account.id: (
            "Date,Merchant,Category,Amount,Notes,Account\n"
            f"03/16/2026,Fernwood Coffee,,4.99,,{account.name}\n"
            f"03/16/2026,Fernwood Coffee,,4.99,,{account.name}\n"
        ).encode()
        for account in accounts
    }

    async def snapshot():
        # Column rows avoid ORM identity-map caching across ASGI request sessions.
        transactions = (await session.execute(
            select(*Transaction.__table__.c).where(
                Transaction.workspace_id == test_workspace.id,
            ).order_by(Transaction.id)
        )).all()
        logs = (await session.execute(
            select(*ImportLog.__table__.c).where(
                ImportLog.workspace_id == test_workspace.id,
            ).order_by(ImportLog.id)
        )).all()
        return transactions, logs

    async def preview(content, account_id):
        response = await client.post(
            "/api/transactions/import/preview", headers=auth_headers,
            files={"file": ("synthetic-per-account.csv", content, "text/csv")},
            data={"preset": "monarch", "dry_run": "true", "account_id": str(account_id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["parse_error"] is None
        assert data["failed_rows"] == []
        assert len(data["transactions"]) == data["dry_run"]["rows_in"] == 2
        return data

    before = await snapshot()
    assert len(before[0]) == 1
    assert before[0][0].id == twin.id
    assert before[0][0].account_id == account_a.id
    assert before[1] == []
    previews = {}
    for account in accounts:
        previews[account.id] = await preview(files[account.id], account.id)
        assert await snapshot() == before

    summary_a = previews[account_a.id]["dry_run"]
    summary_b = previews[account_b.id]["dry_run"]
    assert (summary_a["would_import"], summary_a["would_skip_dupes"]) == (1, 1)
    assert (summary_b["would_import"], summary_b["would_skip_dupes"]) == (2, 0)

    # Falsifier: B's file previewed against the WRONG account sees A's twin.
    # Account hints do not route legacy preview; dropping account scoping would
    # make B's correct preview equal this control and fail the assertions.
    wrong_target = (await preview(files[account_b.id], account_a.id))["dry_run"]
    assert (wrong_target["would_import"], wrong_target["would_skip_dupes"]) == (1, 1)
    assert wrong_target["would_skip_dupes"] != summary_b["would_skip_dupes"]
    assert await snapshot() == before

    previous = before
    for account in accounts:
        data = previews[account.id]
        response = await client.post(
            "/api/transactions/import", headers=auth_headers,
            json={
                "account_id": str(account.id), "transactions": data["transactions"],
                "filename": f"{account.name}.csv", "detected_format": "csv",
                "detect_duplicates": True,
            },
        )
        assert response.status_code == 201
        result = response.json()
        assert result["excluded"] == 0
        assert (result["imported"], result["skipped"]) == (
            data["dry_run"]["would_import"], data["dry_run"]["would_skip_dupes"],
        )
        current = await snapshot()
        # Prior rows/logs, including the synced twin and the other target, survive unchanged.
        assert all(row in current[0] for row in previous[0])
        assert all(log in current[1] for log in previous[1])
        new_rows = [row for row in current[0] if row not in previous[0]]
        new_logs = [log for log in current[1] if log not in previous[1]]
        assert len(new_rows) == result["imported"]
        assert len(new_logs) == 1
        log = new_logs[0]
        assert str(log.id) == result["import_log_id"]
        assert log.account_id == account.id
        assert log.transaction_count == len(new_rows)
        assert log.filename == f"{account.name}.csv"
        assert all(row.account_id == account.id and row.import_id == log.id for row in new_rows)
        assert sum(row.account_id == account.id for row in current[0]) == 2
        previous = current


@pytest.mark.asyncio
async def test_import_monarch_end_to_end_creates_account_currency_log(
    client: AsyncClient, auth_headers, session: AsyncSession, test_account
):
    """§8.3: preview → import → GET /api/import-logs surfaces the log in the
    target account's currency (the #1003 precedent, kept not rebuilt)."""
    preview = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": ("monarch-combined.csv", _monarch_fixture(), "text/csv")},
        data={"preset": "monarch"},
    )
    assert preview.status_code == 200
    rows = preview.json()["transactions"]
    assert len(rows) == 14

    resp = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": rows,
            "filename": "monarch-combined.csv",
            "detected_format": "csv",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["imported"] == 14
    assert resp.json()["skipped"] == 0

    logs = (await client.get("/api/import-logs", headers=auth_headers)).json()
    log = next(entry for entry in logs if entry["filename"] == "monarch-combined.csv")
    assert log["format"] == "csv"
    assert log["account_currency"] == test_account.currency
    assert log["account_name"] == test_account.name
    assert log["transaction_count"] == 14
    assert float(log["total_credit"]) == pytest.approx(
        1200.00 + 0.05 + 1234.56 + 12.50 + 5.00, abs=1e-6
    )
    assert float(log["total_debit"]) == pytest.approx(
        45.00 + 1234.56 + 1234.56 + 4.99 + 4.99 + 27.40 + 950.00 + 12.99 + 60.00,
        abs=1e-6,
    )
