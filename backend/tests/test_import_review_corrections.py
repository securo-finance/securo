"""Real API discriminators for the importer's review corrections."""

import pytest
from sqlalchemy import select

from app.models.transaction import Transaction


@pytest.mark.parametrize("detect_duplicates", [True, False])
@pytest.mark.parametrize("split_requests", [True, False])
async def test_same_target_identical_rows_request_boundary(
    client, auth_headers, test_account, session, detect_duplicates, split_requests,
):
    """Characterize, not endorse, the per-request duplicate boundary.

    The ratified frontend groups shared destinations to preserve multiplicity.
    Split requests remain a negative control for the API's duplicate boundary.
    """
    content = (
        "Date,Description,Category,Amount,Account\n"
        "03/16/2026,Same coffee,,4.99,First\n"
        "03/16/2026,Same coffee,,4.99,Second\n"
    ).encode()
    preview = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("synthetic-same-target.csv", content, "text/csv")},
        data={"preset": "monarch"},
    )
    assert preview.status_code == 200
    rows = preview.json()["transactions"]
    assert len(rows) == 2
    assert [row["account_hint"] for row in rows] == ["First", "Second"]
    batches = [[row] for row in rows] if split_requests else [rows]
    results = []
    for batch in batches:
        response = await client.post(
            "/api/transactions/import", headers=auth_headers,
            json={
                "account_id": str(test_account.id), "transactions": batch,
                "filename": "synthetic-same-target.csv", "detected_format": "csv",
                "detect_duplicates": detect_duplicates,
            },
        )
        assert response.status_code == 201
        results.append(response.json())
    # The existing API intentionally dedups across calls; grouping avoids that loss.
    expected = 1 if split_requests and detect_duplicates else 2
    assert sum(result["imported"] for result in results) == expected
    assert sum(result["skipped"] for result in results) == 2 - expected
    landed = (await session.execute(
        select(Transaction.id).where(Transaction.account_id == test_account.id)
    )).all()
    assert len(landed) == expected


async def test_undefined_monarch_mapping_still_runs_real_category_rule(
    client, auth_headers, test_account, test_categories, test_rules, session,
):
    preview = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": (
            "synthetic-rule.csv",
            b"Date,Description,Category,Amount\n03/16/2026,UBER TRIP,Mystery Ledger,4.99\n",
            "text/csv",
        )},
        data={"preset": "monarch"},
    )
    assert preview.status_code == 200
    data = preview.json()
    assert data["preset_category_suggestions"] == [{
        "value": "Mystery Ledger", "row_count": 1,
        "suggested_category_id": None, "suggested_category_name": None,
    }]
    # Match the frontend payload: no explicit/suggested category contribution.
    row = {key: data["transactions"][0][key] for key in (
        "description", "amount", "date", "type", "category_name",
    )}
    row["force_uncategorized"] = False
    response = await client.post(
        "/api/transactions/import", headers=auth_headers,
        json={
            "account_id": str(test_account.id), "transactions": [row],
            "filename": "synthetic-rule.csv", "detected_format": "csv",
        },
    )
    assert response.status_code == 201
    assert response.json()["imported"] == 1
    category_id = (await session.execute(
        select(Transaction.category_id).where(Transaction.account_id == test_account.id)
    )).scalar_one()
    assert category_id == test_categories[1].id


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("detect_duplicates", [False, True])
async def test_grouped_hints_and_unassigned_preserve_occurrences(
    client, auth_headers, test_account, session, existing, detect_duplicates,
):
    preview = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("synthetic-group.csv", (
            "Date,Description,Category,Amount,Account\n"
            "03/16/2026,Same coffee,,4.99,First\n"
            "03/16/2026,Same coffee,,4.99,\n"
            "03/16/2026,Same coffee,,4.99,Second\n"
        ).encode(), "text/csv")}, data={"preset": "monarch"},
    )
    assert preview.status_code == 200
    rows = preview.json()["transactions"]
    assert [row["account_hint"] for row in rows] == ["First", None, "Second"]
    payload = {
        "account_id": str(test_account.id), "transactions": rows,
        "filename": "synthetic-group.csv", "detected_format": "csv",
        "detect_duplicates": detect_duplicates,
    }
    if existing:
        seed = await client.post("/api/transactions/import", headers=auth_headers,
                                 json={**payload, "transactions": rows[:1]})
        assert seed.status_code == 201
        assert seed.json()["imported"] == 1
    response = await client.post("/api/transactions/import", headers=auth_headers, json=payload)
    assert response.status_code == 201
    skipped = int(existing and detect_duplicates)
    assert response.json()["imported"] == 3 - skipped
    assert response.json()["skipped"] == skipped
    assert response.json()["excluded"] == 0
    landed = (await session.execute(
        select(Transaction.id).where(Transaction.account_id == test_account.id)
    )).all()
    assert len(landed) == int(existing) + 3 - skipped
