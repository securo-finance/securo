import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

NotificationKind = Literal[
    "connection_error",
    "recurring_failed",
    "budget_threshold",
    "flag_overdue",
    "reconciliation_pending",
    "invoice_overdue",
    "group_expense",
    "group_settlement",
    "backup_failed",
]

#: `open` holds now, `unread` is open and not read yet, `all` adds what was resolved.
NotificationStatus = Literal["open", "unread", "all"]


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: NotificationKind
    data: dict[str, Any]
    notified_at: datetime
    read_at: Optional[datetime] = None
    # What it said stopped being true.
    resolved_at: Optional[datetime] = None


class NotificationList(BaseModel):
    items: list[NotificationRead]
    # How many the filter matches, past this page.
    total: int
    # Open and unread, whatever the filter: the bell's number.
    unread_count: int


class NotificationPreferenceRead(BaseModel):
    kind: NotificationKind
    #: Each channel that reaches the person, and whether they want the kind there.
    channels: dict[str, bool]


class NotificationPreferenceUpdate(BaseModel):
    enabled: bool
