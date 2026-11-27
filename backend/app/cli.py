"""CLI commands for managing the application."""
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.row_security import workspace_makers
from app.models.user import User


async def generate_recurring() -> None:
    """Generate pending recurring transactions for all users."""
    from app.core.app_clock import use_timezone
    from app.models.user import User as UserModel
    from app.services import recurring_transaction_service

    total = 0
    for maker in await workspace_makers(async_session_maker):
        async with maker() as session, use_timezone(session, fresh=True):
            result = await session.execute(select(UserModel))
            users = result.scalars().all()
            for user in users:
                count = await recurring_transaction_service.generate_pending(session, user.id)
                if count > 0:
                    print(f"Generated {count} transactions for {user.email}")
                    total += count
    print(f"Total generated: {total}")


async def create_user(email: str, password: str) -> None:
    """Create a new user with the given email and password."""
    from pwdlib import PasswordHash

    hasher = PasswordHash.recommended()

    async with async_session_maker() as session:
        # Check if user already exists
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Error: User with email '{email}' already exists.")
            sys.exit(1)

        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=hasher.hash(password),
            is_active=True,
            is_superuser=False,
            is_verified=True,
            preferences={
                "language": "pt-BR",
                "date_format": "DD/MM/YYYY",
                "timezone": "America/Sao_Paulo",
                "currency_display": "USD",
            },
        )
        session.add(user)
        await session.commit()
        print(f"User created successfully: {email}")


async def restamp_primary_amounts(email: str | None = None) -> None:
    """Rewrite stored primary amounts in each user's current display currency.

    Heals rows stamped before a display currency change (#1381). Safe to run
    again: the result depends only on the rows and the stored rates.
    """
    for maker in await workspace_makers(async_session_maker):
        await _restamp_in(maker, email)


async def _restamp_in(session_maker, email: str | None) -> None:
    from app.core.app_clock import use_timezone
    from app.services.primary_restamp_service import (
        fetch_missing_today_rates,
        restamp_user_primary_amounts,
    )

    async with session_maker() as session, use_timezone(session, fresh=True):
        stmt = select(User.id, User.email, User.preferences)
        if email is not None:
            stmt = stmt.where(User.email == email)
        users = (await session.execute(stmt)).all()
        if email is not None and not users:
            print(f"Error: no user with email '{email}'.")
            sys.exit(1)
        failed = 0
        for user_id, user_email, preferences in users:
            currency = (preferences or {}).get(
                "currency_display", get_settings().default_currency
            )
            try:
                await fetch_missing_today_rates(session, user_id, currency)
                stats = await restamp_user_primary_amounts(session, user_id)
                await session.commit()
            except Exception as error:
                # One user's failure must not leave the users after it unhealed.
                await session.rollback()
                failed += 1
                print(f"{user_email}: failed: {error}")
                continue
            print(f"{user_email}: {stats}")
        if failed:
            print(f"Error: {failed} of {len(users)} users failed.")
            sys.exit(1)


async def apply_row_security(role: str) -> None:
    """Grant an existing role what the app needs and cover the tables with the
    workspace policies (ADR 0043). Run as the owner of the tables."""
    from sqlalchemy import text

    import app.agents.models  # noqa: F401
    import app.models  # noqa: F401
    from app.core.database import Base, engine
    from app.core.row_security import covered_tables, setup_statements

    tables = dict(Base.metadata.tables)
    async with engine.begin() as conn:
        attributes = (await conn.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": role}
        )).one_or_none()
        if attributes is None or attributes[0]:
            print(f"Error: '{role}' must be an existing role without SUPERUSER or BYPASSRLS.")
            sys.exit(1)
        schema = await conn.scalar(text("SELECT current_schema()"))
        quote = conn.dialect.identifier_preparer.quote
        for statement in setup_statements(tables, role, schema, quote):
            await conn.exec_driver_sql(statement)
    print(f"{len(covered_tables(tables))} tables covered for role '{role}'.")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m app.cli create-user <email> <password>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "create-user":
        if len(sys.argv) != 4:
            print("Usage: python -m app.cli create-user <email> <password>")
            sys.exit(1)
        email, password = sys.argv[2], sys.argv[3]
        asyncio.run(create_user(email, password))
    elif command == "generate-recurring":
        asyncio.run(generate_recurring())
    elif command == "restamp-primary-amounts":
        asyncio.run(restamp_primary_amounts(sys.argv[2] if len(sys.argv) > 2 else None))
    elif command == "apply-row-security" and len(sys.argv) == 3:
        asyncio.run(apply_row_security(sys.argv[2]))
    else:
        print(f"Unknown command: {command}")
        print(
            "Available commands: create-user, generate-recurring, restamp-primary-amounts, "
            "apply-row-security <role>"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
