"""CLI commands for managing the application."""
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.models.user import User


async def generate_recurring() -> None:
    """Generate pending recurring transactions for all users."""
    from app.core.app_clock import use_timezone
    from app.models.user import User as UserModel
    from app.services import recurring_transaction_service

    async with async_session_maker() as session, use_timezone(session, fresh=True):
        result = await session.execute(select(UserModel))
        users = result.scalars().all()
        total = 0
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
    from app.core.app_clock import use_timezone
    from app.services.primary_restamp_service import (
        fetch_missing_today_rates,
        restamp_user_primary_amounts,
    )

    async with async_session_maker() as session, use_timezone(session, fresh=True):
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
    else:
        print(f"Unknown command: {command}")
        print("Available commands: create-user, generate-recurring, restamp-primary-amounts")
        sys.exit(1)


if __name__ == "__main__":
    main()
