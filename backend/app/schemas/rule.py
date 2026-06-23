# backend/app/schemas/rule.py
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class RuleCondition(BaseModel):
    field: str   # description, notes, amount, type, account_id, payee_id, date
    op: str      # contains, not_contains, equals, not_equals, starts_with, ends_with, regex, gt, gte, lt, lte
    value: Any   # str or number depending on field


class RuleAction(BaseModel):
    op: str      # set_category, append_notes
    value: Any   # category UUID str or notes string


class RuleCreate(BaseModel):
    name: str
    conditions_op: str = "and"
    conditions: list[RuleCondition]
    actions: list[RuleAction]
    priority: int = 0
    is_active: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    conditions_op: Optional[str] = None
    conditions: Optional[list[RuleCondition]] = None
    actions: Optional[list[RuleAction]] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class RuleRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    conditions_op: str
    conditions: list[dict]
    actions: list[dict]
    priority: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class RuleMutationResponse(RuleRead):
    """A changed rule plus how many existing transactions it just affected."""

    applied_count: int = 0


class RuleCreateResponse(RuleMutationResponse):
    """A created rule response kept for API compatibility."""


class RuleExportItem(BaseModel):
    name: str
    conditions_op: str = "and"
    conditions: list[RuleCondition]
    actions: list[RuleAction]
    priority: int = 0
    is_active: bool = True


class RuleExportPayload(BaseModel):
    format: str = "securo-categorization-rules"
    version: int = 1
    rules: list[RuleExportItem]


class RuleImportRequest(BaseModel):
    payload: RuleExportPayload
    overwrite: bool = False


class RuleImportResponse(BaseModel):
    imported: int
    skipped: int
    overwritten: int
