"""SQLite must preserve PostgreSQL-model UUIDs instead of coercing them to numbers."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from app.models.category import Category
from app.models.transaction import Transaction


async def test_uuid_identity_survives_sqlite_roundtrip_and_foreign_key_lookup(
    session, test_user, test_account, test_workspace
):
    ids = [
        UUID("50109654-5514-4562-8123-456789012345"),
        UUID("50109654-5514-4562-8123-456789012346"),  # Same float, distinct UUID.
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("12345678-1234-4123-8123-123456789e10"),
        UUID("abcdefab-cdef-4abc-8def-abcdefabcdef"),
    ]
    for value in ids:
        session.add(Category(
            id=value,
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            name=str(value),
        ))
        await session.flush()
        transaction = Transaction(
            id=value,
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=value,
            description="UUID roundtrip",
            amount=Decimal("1.00"),
            date=date(2026, 8, 15),
            type="debit",
            source="manual",
        )
        session.add(transaction)
        await session.commit()
        await session.refresh(transaction)
        assert transaction.id == value
        assert transaction.category_id == value

        result = await session.execute(
            select(Transaction.id, Transaction.category_id, Category.id)
            .join(Category, Transaction.category_id == Category.id)
            .where(Transaction.id == value)
        )
        assert result.one() == (value, value, value)

    stored = await session.execute(text(
        "SELECT id, category_id, typeof(id), typeof(category_id) "
        "FROM transactions ORDER BY id"
    ))
    assert stored.all() == sorted((value.hex, value.hex, "text", "text") for value in ids)


def test_postgresql_uuid_columns_keep_native_ddl():
    for table in (Transaction.__table__, Category.__table__):
        for column in table.columns:
            if isinstance(column.type, postgresql.UUID):
                assert column.type.compile(dialect=postgresql.dialect()) == "UUID"
