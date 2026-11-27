"""An import reads what every row asks about once, and writes the same rows.

Issue #1919: duplicate detection, rules, the recurring rule with its bills
and placeholders, payees and the currency stamp were each asked once per file
row, so an import of 300 rows ran several thousand queries and took seconds.
These tests pin that the number of statements no longer grows with the file,
and that a file exercising every lookup still lands the same way.
"""
import uuid
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.payee import Payee
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions

START = date(2026, 2, 1)
KINDS = ["UBER TRIP", "IFOOD", "SALARY ACME", "MARKET", "BAKERY", "PIX JOHN", "BANK FEE"]


def _file(rows: int) -> list[TransactionImport]:
    """`rows` distinct rows, then a tenth of them again, with and without FITID."""
    file_rows = []
    for i in range(rows):
        kind = i % len(KINDS)
        file_rows.append(TransactionImport(
            description=f"{KINDS[kind]} {i}",
            amount=Decimal(f"{(i * 13) % 400 + 1}.{(i * 7) % 100:02d}"),
            date=START + timedelta(days=i % 45),
            type="credit" if kind == 2 else "debit",
            payee_raw=f"Payee {i % 5}" if i % 4 == 0 else None,
            external_id=f"FIT{i}" if i % 2 == 0 else None,
        ))
    repeats = file_rows[: rows // 10]
    return file_rows + [row.model_copy() for row in repeats]


async def _account(session: AsyncSession, user_id, workspace_id, name: str) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name,
        type="checking", balance=Decimal("0.00"), currency="BRL",
    )
    session.add(account)
    for i in range(200):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
            account_id=account.id, description=f"OLD {i}", original_description=f"OLD {i}",
            amount=Decimal(f"{i + 1}.00"), date=START - timedelta(days=200) + timedelta(days=i),
            type="debit", status="posted", source="import", currency="BRL",
        ))
    await session.commit()
    return account


async def _rules(session: AsyncSession, user_id, workspace_id) -> Category:
    ride = Category(user_id=user_id, workspace_id=workspace_id, name="Ride", icon="car", color="#000")
    session.add(ride)
    await session.flush()
    session.add_all([
        Rule(
            id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name="uber",
            conditions_op="or",
            conditions=[{"field": "description", "op": "starts_with", "value": "UBER"}],
            actions=[{"op": "set_category", "value": str(ride.id)}],
            priority=10, is_active=True,
        ),
        Rule(
            id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name="fee",
            conditions_op="or",
            conditions=[{"field": "description", "op": "contains", "value": "BANK FEE"}],
            actions=[{"op": "set_description", "value": "Bank fee"}],
            priority=5, is_active=True,
        ),
    ])
    await session.commit()
    return ride


async def _count_import_statements(session: AsyncSession, user, workspace, rows: int) -> int:
    account = await _account(session, user.id, workspace.id, f"Reads {rows}")
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = await session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        imported, skipped, _, _ = await import_transactions(
            session, workspace.id, user.id, account.id, _file(rows), "import",
            filename="statement.csv", detected_format="csv",
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)
    # The repeats carrying a FITID are skipped; the others are new entries.
    repeats_with_fitid = sum(1 for i in range(rows // 10) if i % 2 == 0)
    assert (imported, skipped) == (rows + rows // 10 - repeats_with_fitid, repeats_with_fitid)
    # The rows themselves are written in batches whose size depends on the
    # driver (SQLite splits them more than Postgres); what is pinned here is
    # everything else the import asks.
    return sum(1 for statement in statements if not statement.lstrip().upper().startswith("INSERT"))


@pytest.mark.asyncio
async def test_an_import_reads_the_same_whatever_the_file_size(
    session: AsyncSession, test_user, test_workspace,
):
    await _rules(session, test_user.id, test_workspace.id)
    small = await _count_import_statements(session, test_user, test_workspace, 50)
    large = await _count_import_statements(session, test_user, test_workspace, 300)

    # Asked per row, 300 rows ran about 3,700 of these against 620 for 50.
    # Read once for the file, six times the rows costs no more of them.
    assert large <= small + 5, (small, large)


@pytest.mark.asyncio
async def test_a_300_row_file_lands_as_before(
    session: AsyncSession, test_user, test_workspace,
):
    user_id, workspace_id = test_user.id, test_workspace.id
    ride_id = (await _rules(session, user_id, workspace_id)).id
    account = await _account(session, user_id, workspace_id, "Statement")
    account_id = account.id
    file_rows = _file(300)

    # A connection already brought one row in, two days off the file's date.
    synced = file_rows[1]
    session.add(Transaction(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, account_id=account.id,
        description=synced.description, original_description=synced.description,
        amount=synced.amount, date=synced.date + timedelta(days=2), type=synced.type,
        status="posted", source="sync", currency="BRL",
    ))
    # A bill with a pending placeholder the file pays.
    netflix = RecurringTransaction(
        user_id=user_id, workspace_id=workspace_id, account_id=account.id,
        description="NETFLIX.COM", amount=Decimal("39.90"), currency="BRL", type="debit",
        frequency="monthly", start_date=date(2025, 1, 6),
        next_occurrence=START + timedelta(days=36), day_of_month=6,
    )
    session.add(netflix)
    await session.flush()
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, account_id=account.id,
        description="NETFLIX.COM", original_description="NETFLIX.COM",
        amount=Decimal("39.90"), date=START + timedelta(days=36), type="debit",
        status="pending", source="recurring", currency="BRL",
        recurring_transaction_id=netflix.id,
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    file_rows += [
        TransactionImport(
            # A day before the placeholder's date, as banks post it.
            description="NETFLIX.COM", amount=Decimal("39.90"),
            date=START + timedelta(days=35), type="debit",
        ),
        # Rows that only collide as the column stores the amount.
        TransactionImport(
            description="ROUND", amount=Decimal("10.005"), date=START, type="debit",
        ),
        TransactionImport(
            description="ROUND", amount=Decimal("10.01"), date=START, type="debit",
        ),
        TransactionImport(
            description="ROUND", amount=Decimal("10.01"), date=START, type="debit",
        ),
    ]

    netflix_id = netflix.id
    synced_description = synced.description

    imported, skipped, _, log_id = await import_transactions(
        session, workspace_id, user_id, account_id, file_rows, "import",
        filename="statement.csv", detected_format="csv",
    )

    # Skipped: the 15 repeats carrying a FITID and the row the connection
    # brought in. A repeat without one is a second entry of the same file
    # (two equal coffees on one day) and lands, and so do the three ROUND
    # rows. The bill's row pays the placeholder instead of adding one.
    assert (imported, skipped) == (318, 16)

    session.expire_all()
    rows = (await session.execute(
        select(Transaction).where(Transaction.account_id == account_id, Transaction.date >= START)
    )).scalars().all()
    assert Counter(r.source for r in rows) == {"import": 318, "sync": 1}
    assert sum(1 for r in rows if r.description == synced_description) == 2  # sync + the repeat

    rides = [r for r in rows if r.description.startswith("UBER")]
    assert rides and all(r.category_id == ride_id for r in rides)
    fees = [r for r in rows if r.original_description and r.original_description.startswith("BANK FEE")]
    assert fees and all(r.description == "Bank fee" for r in fees)

    payees = (await session.execute(
        select(Payee).where(Payee.workspace_id == workspace_id)
    )).scalars().all()
    assert sorted(p.name for p in payees) == [f"Payee {i}" for i in range(5)]
    by_payee = {p.id for p in payees}
    assert sum(1 for r in rows if r.payee_id in by_payee) == 75

    upgraded = await session.get(Transaction, placeholder_id)
    assert upgraded is not None
    assert upgraded.source == "import"
    assert upgraded.import_id == log_id
    assert upgraded.recurring_transaction_id == netflix_id
    assert [r.amount for r in rows if r.description == "ROUND"] == [Decimal("10.01")] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", [None, "FIT-616"])
async def test_a_reimport_finds_the_amount_the_column_rounded(
    session: AsyncSession, test_user, test_workspace, external_id,
):
    """A file's 10.005 lands as 10.01, and importing the file again skips it.

    Issue #616: asked once per row, the duplicate check ran in the database,
    which rounds the bound amount to the column's scale before comparing.
    Read once for the file, the amount is judged in memory, so both sides
    have to be rounded the same way or the row lands twice. Both keys are
    covered: the fields a CSV fallback uses and an OFX FITID.
    """
    account = await _account(session, test_user.id, test_workspace.id, "Reimport")
    file_row = TransactionImport(
        description="ROUND TRIP", amount=Decimal("10.005"), date=START, type="debit",
        external_id=external_id,
    )

    imported, skipped, _, _ = await import_transactions(
        session, test_workspace.id, test_user.id, account.id, [file_row], "import",
        filename="statement.csv", detected_format="csv",
    )
    assert (imported, skipped) == (1, 0)
    # The second import is another request: it reads the row back from the column.
    session.expunge_all()
    stored = (await session.execute(
        select(Transaction.amount).where(
            Transaction.account_id == account.id,
            Transaction.description == "ROUND TRIP",
        )
    )).scalars().all()
    assert stored == [Decimal("10.01")]

    imported, skipped, _, _ = await import_transactions(
        session, test_workspace.id, test_user.id, account.id, [file_row.model_copy()],
        "import", filename="statement.csv", detected_format="csv",
    )
    assert (imported, skipped) == (0, 1)
    amounts = (await session.execute(
        select(Transaction.amount).where(
            Transaction.account_id == account.id,
            Transaction.description == "ROUND TRIP",
        )
    )).scalars().all()
    assert amounts == [Decimal("10.01")]


@pytest.mark.asyncio
async def test_a_synced_row_is_found_at_the_amount_the_column_rounded(
    session: AsyncSession, test_user, test_workspace,
):
    """A file row collides with a connection's row at the amount both store.

    Both carry 29.994; the column keeps 29.99 for each. The per-row query
    rounded the file's amount before comparing, and so must the in-memory
    match against the loaded rows (issue #616).
    """
    account = await _account(session, test_user.id, test_workspace.id, "Synced")
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account.id, description="GYM", original_description="GYM",
        amount=Decimal("29.994"), date=START + timedelta(days=2), type="debit",
        status="posted", source="sync", currency="BRL",
    ))
    await session.commit()

    imported, skipped, _, _ = await import_transactions(
        session, test_workspace.id, test_user.id, account.id,
        [TransactionImport(
            description="GYM", amount=Decimal("29.994"), date=START, type="debit",
        )],
        "import", filename="statement.csv", detected_format="csv",
    )

    assert (imported, skipped) == (0, 1)
