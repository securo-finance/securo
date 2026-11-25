"""Resolve one login address to the one account it names.

An address names one mailbox, but an instance can already hold two rows for it
in different case: the column's unique index is case-sensitive, and the
case-insensitive checks arrived later (#568). A lookup that expected a single
row raised ``MultipleResultsFound`` and the route answered 500, which made the
account unreachable from every screen that names it by address. Every lookup
goes through here: the same row is chosen on every request, and asking whether
an address is taken never raises.
"""

import uuid
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


def _address_matches(email: str):
    return func.lower(User.email) == email.strip().lower()


async def find_account_by_email(
    session: AsyncSession,
    email: str,
    *,
    exclude_user_id: Optional[uuid.UUID] = None,
) -> Optional[User]:
    """The account `email` names, or None.

    Without regard to case, the way sign-in resolves the address. An instance
    that still holds two accounts for one mailbox answers the row written in
    the exact spelling that was asked for; only when neither is written that
    way does the lowest id decide, so the same account answers on every
    request instead of depending on the order the database returns. The id is
    a random UUID, so it cannot be what tells two accounts apart: the person
    who typed their own spelling would land on the other one.
    """
    address = email.strip()
    query = select(User).where(_address_matches(address))
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    query = query.order_by(
        case((User.email == address, 0), else_=1), User.id
    ).limit(1)
    return (await session.execute(query)).scalars().first()
