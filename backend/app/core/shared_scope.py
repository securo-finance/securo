"""Whether a request also sees what other workspaces share with the user.

A signed-in user sees, in every workspace, their share of group expenses
that live in someone else's workspace (they were invited to the group from
there). A request made with an access token is bound to the token's
workspace, so it gets none of that: the token, and any plugin holding it,
reads only rows that belong to its own workspace. Shares of expenses that do
live in that workspace (another member paid, the user owes part) still count.

`current_workspace` binds token requests with `bound_to_workspace`; the
queries that bring in shared expenses add `in_bound_workspace(...)` on the
expense's (or group's) workspace column, which is always true for a session.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, or_, select, true

_bound: ContextVar[uuid.UUID | None] = ContextVar("bound_workspace", default=None)


@contextmanager
def bound_to_workspace(workspace_id: uuid.UUID | None) -> Generator[None]:
    """Run the block seeing only `workspace_id`'s rows; None changes nothing."""
    token = _bound.set(workspace_id)
    try:
        yield
    finally:
        _bound.reset(token)


def bound_workspace_id() -> uuid.UUID | None:
    return _bound.get()


def in_bound_workspace(workspace_column: Any) -> ColumnElement[bool]:
    """`workspace_column` is the bound workspace; always true when unbound."""
    bound = _bound.get()
    return true() if bound is None else workspace_column == bound


def viewer_member_predicate(user_id: uuid.UUID) -> ColumnElement[bool]:
    """The `GroupMember` rows that represent the user in a group: every
    membership linked to them, plus their own self-member in a group they
    own.

    The self-member is often unlinked (the owner never gave an e-mail), so
    it is found through the group's owner instead of `linked_user_id`. Both
    ends of a cross-workspace split must match this: the invited member's
    rows and the owner's, or one side sees a share the other never does
    (#1305). Needs `Group` joined on `GroupMember.group_id`.
    """
    from app.models.group import Group, GroupMember

    return or_(
        GroupMember.linked_user_id == user_id,
        and_(GroupMember.is_self.is_(True), Group.user_id == user_id),
    )


def invited_group_member_ids(user_id: uuid.UUID) -> Select:
    """The member rows that represent the user, as a subquery of ids. See
    `viewer_member_predicate`."""
    from app.models.group import Group, GroupMember

    return (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(viewer_member_predicate(user_id))
    )
