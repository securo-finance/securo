"""Price history consumes the provenance persisted by the real CSV import API."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction


@pytest.fixture(autouse=True)
def frozen_recent_calendar(monkeypatch):
    monkeypatch.setattr("app.services.recurring_price_change_service.app_today",
                        lambda: date(2026, 3, 15))


@pytest.mark.parametrize("amounts", [("39.90", "39.90", "42.90"), ("17.00", "17.00", "15.00")])
async def test_csv_imported_linked_charges_feed_both_price_change_routes(
    client, auth_headers, session, test_user, test_workspace, test_account, amounts,
):
    series = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="Streaming subscription",
        amount=Decimal(amounts[-1]), currency="BRL", type="debit", frequency="monthly",
        start_date=date(2026, 1, 15), next_occurrence=date(2026, 5, 15),
    )
    session.add(series)
    # Already-linked occurrences are upgraded by the real import producer.
    # A fourth, unconfirmed projection must remain excluded from history.
    placeholders = [
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description=series.description,
            amount=Decimal(amount), currency="BRL", type="debit", date=date(2026, month, 15),
            source="recurring", status="pending", recurring_transaction_id=series.id,
        )
        for month, amount in enumerate((*amounts, "99.00"), start=1)
    ]
    session.add_all(placeholders)
    await session.commit()
    series_id = series.id
    placeholder_ids = [tx.id for tx in placeholders]
    history_path = f"/api/recurring-transactions/{series_id}/price-changes"
    before = await client.get(history_path, headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["eligible_occurrence_count"] == 0

    csv = "date,description,amount\n" + "".join(
        f"2026-{month:02d}-15,STREAMING SUBSCRIPTION,-{amount}\n"
        for month, amount in enumerate(amounts, start=1)
    )
    preview = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("streaming.csv", csv.encode(), "text/csv")},
    )
    assert preview.status_code == 200
    assert preview.json()["detected_format"] == "csv"
    assert len(preview.json()["transactions"]) == 3
    imported = await client.post(
        "/api/transactions/import", headers=auth_headers,
        json={"account_id": str(test_account.id), "filename": "streaming.csv",
              "detected_format": "csv", "transactions": preview.json()["transactions"]},
    )
    assert imported.status_code == 201
    assert imported.json()["imported"] == 3
    assert imported.json()["skipped"] == 0
    assert imported.json()["excluded"] == 0

    rows = list((await session.scalars(
        select(Transaction).where(Transaction.recurring_transaction_id == series_id)
        .order_by(Transaction.date).execution_options(populate_existing=True)
    )).all())
    assert [tx.id for tx in rows] == placeholder_ids
    assert [tx.source for tx in rows] == ["import", "import", "import", "recurring"]
    assert [tx.status for tx in rows] == ["posted", "posted", "posted", "pending"]
    assert [tx.amount for tx in rows[:3]] == [Decimal(amount) for amount in amounts]
    assert all(tx.workspace_id == test_workspace.id and tx.currency == "BRL" for tx in rows)

    history = await client.get(history_path, headers=auth_headers)
    assert history.status_code == 200
    body = history.json()
    assert body["eligible_occurrence_count"] == 3
    assert body["eligibility"] == "changes_detected"
    assert len(body["changes"]) == 1
    change = body["changes"][0]
    assert change["previous_transaction_id"] == str(placeholder_ids[1])
    assert change["current_transaction_id"] == str(placeholder_ids[2])
    assert change["previous_amount"] == amounts[1]
    assert change["current_amount"] == amounts[2]
    assert change["currency"] == "BRL"
    assert change["previous_date"] == "2026-02-15"
    assert change["current_date"] == "2026-03-15"

    recent = await client.get(
        "/api/recurring-transactions/price-changes/recent", headers=auth_headers,
    )
    assert recent.status_code == 200
    assert recent.json() == [change]
