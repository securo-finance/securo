"""Reimbursement links in workspace backups (#1126)."""

import io
import json
import uuid
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models.reimbursement_allocation import ReimbursementAllocation
from app.models.transaction import Transaction
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import reimbursement_service


@pytest.mark.asyncio
async def test_a_backup_restores_the_links_between_the_copied_rows(
    client, session, auth_headers, test_user, test_account, test_workspace
):
    rows = {}
    for description, amount, type_ in (("Dinner", "100", "debit"), ("Payback", "40", "credit")):
        rows[description] = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description=description, amount=Decimal(amount),
            currency=test_account.currency, date=date(2026, 10, 10), type=type_, source="manual",
        )
        session.add(rows[description])
    await session.flush()
    await reimbursement_service.replace_allocations(
        session,
        rows["Dinner"],
        [ReimbursementAllocationInput(counterpart_id=rows["Payback"].id, amount=Decimal("40"))],
    )
    await session.commit()

    archive = await client.get("/api/export/backup", headers=auth_headers)
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as source:
        metadata = json.loads(source.read("metadata.json"))
    assert metadata["entity_counts"]["reimbursement_allocations"] == 1

    restored = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", archive.content, "application/zip")},
        data={"content": "both", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.text
    workspace_id = uuid.UUID(restored.json()["workspace_id"])

    anchor, counterpart = aliased(Transaction), aliased(Transaction)
    result = await session.execute(
        select(anchor.description, counterpart.description, ReimbursementAllocation.amount)
        .join(anchor, ReimbursementAllocation.anchor_id == anchor.id)
        .join(counterpart, ReimbursementAllocation.counterpart_id == counterpart.id)
        .where(ReimbursementAllocation.workspace_id == workspace_id)
    )
    assert result.all() == [("Dinner", "Payback", Decimal("40.00"))]
