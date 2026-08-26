# backend/app/schemas/rule.py
import uuid
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator


class RuleCondition(BaseModel):
    field: str   # description, payee, notes, amount, type, account_id, payee_id, date
    op: str      # contains, not_contains, equals, not_equals, starts_with, ends_with, regex, gt, gte, lt, lte
    value: Any   # str or number depending on field

    @field_validator("value")
    @classmethod
    def value_must_not_be_blank(cls, v: Any) -> Any:
        """Reject blank values — they silently match every transaction.

        A blank value turns `contains`/`starts_with`/`ends_with`/`regex` into a
        tautology, and numeric comparisons fall back to 0, so the rule applies
        its actions to the whole ledger. Explicit `0` and `False` stay valid.
        """
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError("Condition value cannot be blank")
        return v


class RuleConditionGroup(BaseModel):
    """A nested group of conditions joined by its own operator.

    Groups let a rule mix AND and OR — `type is debit AND (contains UBER OR
    contains 99POP)`. They hold leaf conditions only: `conditions` is typed as
    `list[RuleCondition]`, so a nested group fails validation and rule depth
    stays capped at two levels, which is what the engine and editor support.
    """

    op: str = "or"   # and, or
    conditions: list[RuleCondition]

    @field_validator("op")
    @classmethod
    def op_must_be_and_or(cls, v: str) -> str:
        if v not in ("and", "or"):
            raise ValueError("Condition group operator must be 'and' or 'or'")
        return v

    @field_validator("conditions")
    @classmethod
    def group_must_not_be_empty(cls, v: list[RuleCondition]) -> list[RuleCondition]:
        """An empty group never matches, so it can only make a rule confusing."""
        if not v:
            raise ValueError("Condition group cannot be empty")
        return v


# A rule's condition list mixes leaves and one level of groups. The two shapes
# are disjoint — a leaf has no `conditions`, a group has no `field`/`value` — so
# Pydantic's smart union resolves them without a discriminator.
RuleConditionNode = Union[RuleConditionGroup, RuleCondition]


class RuleAction(BaseModel):
    op: str      # set_category, set_payee, set_description, append_notes, ignore
    value: Any   # entity UUID or text depending on action


class RuleCreate(BaseModel):
    name: str
    conditions_op: str = "and"
    conditions: list[RuleConditionNode]
    actions: list[RuleAction]
    priority: int = 0
    is_active: bool = True
    apply_to_existing: bool = True
    overwrite_existing_categories: bool = False


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    conditions_op: Optional[str] = None
    conditions: Optional[list[RuleConditionNode]] = None
    actions: Optional[list[RuleAction]] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    apply_to_existing: Optional[bool] = None
    overwrite_existing_categories: bool = False


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
    conditions: list[RuleConditionNode]
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
