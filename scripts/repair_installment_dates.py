#!/usr/bin/env python3
"""
Temporary data repair script for installment-target-parity.

Fixes fallback-detected transactions where installment_purchase_date was set
to the transaction date instead of being backdated by (installment_number - 1) months.

This caused each installment to form its own 1-row "purchase" instead of
grouping together under the same purchase_date.

Run: python3 scripts/repair_installment_dates.py
"""

import asyncio
from datetime import date
from calendar import monthrange

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models.transaction import Transaction
from app.core.config import get_settings


def add_months(d: date, months: int) -> date:
    month = d.month + months
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


async def repair():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Find transactions where:
        # - source is not manual (only fix synced transactions)
        # - installment_number > 1 (first installment is correct)
        # - installment_purchase_date equals the transaction date (un-backdated)
        q = select(Transaction).where(
            Transaction.source != "manual",
            Transaction.installment_number.isnot(None),
            Transaction.installment_number > 1,
            Transaction.installment_purchase_date == Transaction.date,
            Transaction.type == "debit",
            Transaction.is_ignored == False,
        )
        result = await session.execute(q)
        txs = result.scalars().all()

        print(f"Found {len(txs)} transactions to repair")

        fixed = 0
        for tx in txs:
            current = tx.installment_number
            new_purchase_date = add_months(tx.date, -(current - 1))
            old_date = tx.installment_purchase_date
            tx.installment_purchase_date = new_purchase_date
            fixed += 1
            print(
                f"  #{current}: {tx.date} -> purchase_date {old_date} -> {new_purchase_date}"
            )

        if fixed > 0:
            await session.commit()
            print(f"\nRepaired {fixed} transactions")
        else:
            print("\nNo transactions needed repair")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(repair())
