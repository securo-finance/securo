"""Whether a request also sees what other workspaces share with the user.

A signed-in user sees, in every workspace, their share of group expenses
that live in someone else's workspace (they were invited to the group from
there). A request made with an access token is bound to the token's
workspace, so it gets none of that: the token, and any plugin holding it,
reads only rows that belong to its own workspace.

`current_workspace` marks token requests with `bound_to_workspace`; the
queries that bring in other workspaces' shares build their membership
subquery with `invited_group_member_ids`, which matches nothing there.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Select, false, select

_bound: ContextVar[bool] = ContextVar("bound_to_workspace", default=False)


@contextmanager
def bound_to_workspace(bound: bool = True) -> Generator[None]:
    """Run the block without other workspaces' shares when `bound`."""
    token = _bound.set(bound)
    try:
        yield
    finally:
        _bound.reset(token)


def other_workspace_shares_hidden() -> bool:
    return _bound.get()


def invited_group_member_ids(user_id: uuid.UUID) -> Select:
    """The user's memberships in groups they were invited to (not their own
    self-member), as a subquery of ids; empty when bound to one workspace."""
    from app.models.group import GroupMember

    query = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id,
        GroupMember.is_self.is_(False),
    )
    return query.where(false()) if other_workspace_shares_hidden() else query
