"""Preview or backfill masked card endings from stored provider payloads.

The default is a read-only dry run. It only prints aggregate counts and the
last four characters of each card number; it never prints a transaction,
provider payload or full card number.

Usage:
    cd backend
    uv run python scripts/backfill_transaction_card_numbers.py
    uv run python scripts/backfill_transaction_card_numbers.py --apply

`--apply` is intentionally separate and only works after migration 079 has
created ``transactions.card_masked_number``.
"""

import argparse
import asyncio
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import cast
from uuid import UUID

from sqlalchemy import inspect, select, update
from sqlalchemy.engine import CursorResult

# This script is called directly from ``backend/scripts``. Make the backend
# package importable without requiring callers to configure PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_maker, engine
from app.models.account import Account
from app.models.transaction import Transaction
from app.providers.base import mask_last4


def _tail_from_raw_data(raw_data: object) -> str | None:
    """Extract only a normalized final four from a stored Pluggy payload."""
    if not isinstance(raw_data, dict):
        return None
    metadata = raw_data.get("creditCardMetadata")
    if not isinstance(metadata, dict):
        return None
    card_number = metadata.get("cardNumber")
    return mask_last4(str(card_number)) if card_number else None


async def _has_target_column() -> bool:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: "card_masked_number"
            in {column["name"] for column in inspect(sync_connection).get_columns("transactions")}
        )


async def main(apply: bool, workspace_id: UUID | None) -> int:
    has_target_column = await _has_target_column()
    if apply and not has_target_column:
        print("Refusing --apply: migration 079 has not been applied.")
        return 2

    async with async_session_maker() as session:
        account_query = select(Account.id, Account.name)
        if workspace_id:
            account_query = account_query.where(Account.workspace_id == workspace_id)
        account_rows = await session.execute(account_query)
        account_names: dict[UUID, str] = {
            account_id: account_name for account_id, account_name in account_rows.all()
        }

        columns = [Transaction.id, Transaction.account_id, Transaction.raw_data]
        if has_target_column:
            columns.append(Transaction.card_masked_number)
        query = select(*columns)
        if workspace_id:
            query = query.where(Transaction.workspace_id == workspace_id)
        rows = (await session.execute(query)).all()

        candidate_ids_by_tail: dict[str, list[UUID]] = defaultdict(list)
        candidates_by_account: dict[UUID, Counter[str]] = defaultdict(Counter)
        totals = Counter(scanned=len(rows), with_card_number=0, already_backfilled=0)

        for row in rows:
            transaction_id, account_id, raw_data, *existing_value = row
            tail = _tail_from_raw_data(raw_data)
            if tail is None:
                continue
            totals["with_card_number"] += 1

            current_tail = existing_value[0] if existing_value else None
            if current_tail is not None:
                totals["already_backfilled"] += 1
                continue

            candidate_ids_by_tail[tail].append(transaction_id)
            candidates_by_account[account_id][tail] += 1

        totals["would_backfill"] = sum(len(ids) for ids in candidate_ids_by_tail.values())

        mode = "APPLY" if apply else "DRY RUN"
        print(f"--- transaction card-number backfill ({mode}) ---")
        print(f"transactions scanned: {totals['scanned']}")
        print(f"with usable card number: {totals['with_card_number']}")
        if has_target_column:
            print(f"already backfilled: {totals['already_backfilled']}")
        else:
            print("migration 079: not applied (preview is still read-only and valid)")
        print(f"would backfill: {totals['would_backfill']}")

        if candidates_by_account:
            print("\nby account (masked tails only):")
            for account_id, tail_counts in sorted(
                candidates_by_account.items(),
                key=lambda item: account_names.get(item[0], str(item[0])).lower(),
            ):
                account_name = account_names.get(account_id, "Unknown account")
                tails = ", ".join(
                    f"•{tail}: {count}" for tail, count in tail_counts.most_common()
                )
                print(f"  {account_name}: {sum(tail_counts.values())} ({tails})")

        if not apply:
            return 0

        updated = 0
        for tail, transaction_ids in candidate_ids_by_tail.items():
            result = await session.execute(
                update(Transaction)
                .where(
                    Transaction.id.in_(transaction_ids),
                    Transaction.card_masked_number.is_(None),
                )
                .values(card_masked_number=tail)
            )
            updated += cast(CursorResult, result).rowcount or 0
        await session.commit()
        print(f"updated: {updated}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write masked tails after migration 079; omit for a read-only preview",
    )
    parser.add_argument(
        "--workspace-id",
        type=UUID,
        help="optionally limit the operation to one workspace",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.apply, arguments.workspace_id)))
