"""The owner's side of a change of login address (#556, ADR 0042).

An administrator may ask for the address on an account to change; only the
person whose account it is may accept it. Until they do, the account keeps the
address it has and every credential tied to it, password recovery included —
which is what makes the request useless as a way into the account.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def confirm_pending_email(session: AsyncSession, user: User) -> User:
    """Move the account to the address it was asked for, if it is still free."""
    if not user.pending_email:
        raise ValueError("There is no pending change of email to confirm")

    requested = user.pending_email
    # The address may have been taken between the request and this moment.
    taken = await session.execute(
        select(User.id).where(User.email == requested, User.id != user.id)
    )
    if taken.scalar_one_or_none():
        raise ValueError("That address belongs to another account now")

    user.email = requested
    user.pending_email = None
    user.pending_email_requested_at = None
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def cancel_pending_email(session: AsyncSession, user: User) -> User:
    """Leave the account where it is: the owner declined the change."""
    user.pending_email = None
    user.pending_email_requested_at = None
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
