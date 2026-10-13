from decimal import Decimal
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.transaction_category_allocation import CategoryAllocationsInput, CategoryAllocationInput
from app.services import category_allocation_service, import_service
from app.core.app_clock import app_today


class TestCSVSplitRoundtrip:

    @pytest.mark.asyncio
    async def test_export_split_transaction_multiple_rows(
        self, async_client, session: AsyncSession, user, workspace, account, categories
    ):
        """Export transaction with 2 allocations → 2 CSV rows with split_group."""
        # Create split transaction
        tx = Transaction(
            user_id=user.id,
            workspace_id=workspace.id,
            account_id=account.id,
            description="Groceries & Household",
            amount=Decimal("140.00"),
            date=app_today(),
            type="debit",
            currency=account.currency,
            status="posted",
            source="manual",
            category_id=categories[0].id,
        )
        session.add(tx)
        await session.flush()

        # Add allocations
        await category_allocation_service.replace_category_allocations(
            session,
            tx,
            CategoryAllocationsInput(
                allocations=[
                    CategoryAllocationInput(category_id=categories[0].id, amount=Decimal("100.00")),
                    CategoryAllocationInput(category_id=categories[1].id, amount=Decimal("40.00")),
                ]
            ),
            user.id,
        )
        await session.commit()

        # Export and verify
        resp = await async_client.get("/api/transactions/export")
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert "split_group" in lines[0]
        assert "split_index" in lines[0]
        assert "parent_category" in lines[0]

        # Verify rows share split_group
        parts1 = lines[1].split(",")
        parts2 = lines[2].split(",")
        split_group_idx = lines[0].split(",").index("split_group")
        parent_idx = lines[0].split(",").index("parent_category")
        assert parts1[split_group_idx] == parts2[split_group_idx]
        assert parts1[parent_idx] == parts2[parent_idx] == categories[0].name

    @pytest.mark.asyncio
    async def test_import_split_csv_recreates_allocations(
        self, async_client, session: AsyncSession, user, workspace, account, categories
    ):
        """Parse CSV with split_group → collapse into transaction with allocations."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Groceries & Household,100.00,debit,USD,Groceries,group1,1,Groceries
2026-01-05,Groceries & Household,40.00,debit,USD,Household,group1,2,Groceries"""

        txns, failed = import_service.parse_csv(csv_content)
        assert len(failed) == 0
        assert len(txns) == 1
        assert len(txns[0].allocations) == 2
        assert txns[0].amount == Decimal("140.00")
        assert txns[0].category_name == "Groceries"
        assert txns[0].allocations[0].category_name == "Groceries"
        assert txns[0].allocations[1].category_name == "Household"

    @pytest.mark.asyncio
    async def test_import_split_transaction_writes_allocations(
        self, async_client, session: AsyncSession, user, workspace, account, categories
    ):
        """Full import cycle: parsed split → stored with TransactionCategoryAllocation rows."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Groceries & Household,100.00,debit,USD,Groceries,group1,1,Groceries
2026-01-05,Groceries & Household,40.00,debit,USD,Household,group1,2,Groceries"""

        txns, _ = import_service.parse_csv(csv_content)
        imported, skipped, excluded, _ = await import_service.import_transactions(
            session,
            workspace.id,
            user.id,
            account.id,
            txns,
            "csv",
            "test.csv",
            "csv",
            detect_duplicates=False,
        )
        assert imported == 1

        from sqlalchemy import select
        result = await session.execute(
            select(Transaction).where(Transaction.workspace_id == workspace.id)
        )
        created_tx = result.scalars().first()
        assert created_tx is not None
        assert len(created_tx.category_allocations) == 2
        assert {a.amount for a in created_tx.category_allocations} == {Decimal("100.00"), Decimal("40.00")}

    @pytest.mark.asyncio
    async def test_uncategorized_parent_roundtrip(
        self, session: AsyncSession, user, workspace, account, categories
    ):
        """Empty parent_category → uncategorized transaction → category_id is None."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Mixed Purchase,100.00,debit,USD,Groceries,group1,1,
2026-01-05,Mixed Purchase,40.00,debit,USD,Household,group1,2,"""

        txns, _ = import_service.parse_csv(csv_content)
        assert len(txns) == 1
        assert txns[0].category_name is None or txns[0].category_name == ""
        assert txns[0].force_uncategorized is True

    def test_backward_compat_csv_no_split_columns(
        self, session: AsyncSession
    ):
        """CSV without split columns parses as single-row transactions (backward compat)."""
        csv_content = b"""date,description,amount,type,currency,category
2026-01-05,Purchase,100.00,debit,USD,Groceries
2026-01-05,Other,50.00,debit,USD,Household"""

        txns, failed = import_service.parse_csv(csv_content)
        assert len(failed) == 0
        assert len(txns) == 2
        assert all(len(t.allocations) == 0 for t in txns)

    def test_degradation_single_row_group(
        self, session: AsyncSession
    ):
        """Single-row group (no grouping) → leaves as plain transaction."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Purchase,100.00,debit,USD,Groceries,group1,1,Groceries"""

        txns, _ = import_service.parse_csv(csv_content)
        assert len(txns) == 1
        assert len(txns[0].allocations) == 0

    def test_degradation_duplicate_category_in_group(
        self, session: AsyncSession
    ):
        """Duplicate category in group → import as separate plain transactions."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Purchase,100.00,debit,USD,Groceries,group1,1,Groceries
2026-01-05,Purchase,40.00,debit,USD,Groceries,group1,2,Groceries"""

        txns, _ = import_service.parse_csv(csv_content)
        assert len(txns) == 2
        assert all(len(t.allocations) == 0 for t in txns)

    def test_degradation_mixed_type_in_group(
        self, session: AsyncSession
    ):
        """Mixed transaction type in group → leaves as separate plain transactions."""
        csv_content = b"""date,description,amount,type,currency,category,split_group,split_index,parent_category
2026-01-05,Purchase,100.00,debit,USD,Groceries,group1,1,Groceries
2026-01-05,Purchase,40.00,credit,USD,Household,group1,2,Groceries"""

        txns, _ = import_service.parse_csv(csv_content)
        assert len(txns) == 2
        assert all(len(t.allocations) == 0 for t in txns)
