"""The owner's side of a change of login address (#556, ADR 0042).

An administrator may ask for the address on an account to change; only the
person whose account it is may accept it. Until they do, the account keeps the
address it has and every credential tied to it, password recovery included —
which is what makes the request useless as a way into the account.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import account_lookup_service

STALE_REQUEST = (
    "The address waiting for you is no longer the one you were shown"
)


class AccountGone(Exception):
    """The account this session belongs to was deleted while it ran (#567).

    An administrator deleting the account holds its row with a lock, so this
    call waits and then finds nothing. That is not a failure of the request:
    the answer is that the account is no longer there.
    """


async def confirm_pending_email(
    session: AsyncSession, user: User, *, expected_email: str
) -> User:
    """Move the account to the address its owner accepted, if it is still free.

    ``expected_email`` is the address the owner saw when they accepted: an
    administrator can replace a request that is still waiting, and accepting
    must be about the address that was read, never about whatever the row holds
    by the time this runs. The row is locked before it is read so a decline or
    a replacement in another request cannot land between the check and the
    write.
    """
    locked = await session.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # A deletion of the same account holds the row, so this waits for it and
    # then finds nothing; reading with `scalar_one` made that a 500 (#567).
    held = locked.scalar_one_or_none()
    if held is None:
        raise AccountGone
    user = held

    if not user.pending_email:
        raise ValueError("There is no pending change of email to confirm")
    requested = user.pending_email
    if requested != expected_email:
        raise ValueError(STALE_REQUEST)

    # The address may have been taken between the request and this moment.
    # Compared without regard to case: sign-in resolves the address that way,
    # so `Taken@example.com` and `taken@example.com` are the same account
    # (#556, ADR 0042). An instance that still holds two rows for the address
    # (#568) answers with one of them here instead of raising.
    taken = await account_lookup_service.find_account_by_email(
        session, requested, exclude_user_id=user.id
    )
    if taken is not None:
        raise ValueError("That address belongs to another account now")

    user.email = requested
    user.pending_email = None
    user.pending_email_requested_at = None
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Two accounts can be asked for the same address; both pass the check
        # above and the unique index decides at commit. That is the same
        # conflict, answered the same way, not a 500.
        await session.rollback()
        raise ValueError("That address belongs to another account now") from exc
    await session.refresh(user)
    return user


async def cancel_pending_email(session: AsyncSession, user: User) -> User:
    """Leave the account where it is: the owner declined the change.

    The row is taken before it is written, like the acceptance above: a
    deletion arriving first otherwise leaves an UPDATE that matched no row and
    the decline answers 500 (#567).
    """
    locked = await session.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    held = locked.scalar_one_or_none()
    if held is None:
        raise AccountGone
    user = held
    user.pending_email = None
    user.pending_email_requested_at = None
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
