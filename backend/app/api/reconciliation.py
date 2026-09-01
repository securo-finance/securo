"""The matching rules, and the questions matching could not answer.

Two surfaces that belong together: the rules decide, and the queue is
where a decision was not confident enough to be made alone. Seeing them
side by side is the point — somebody staring at a long queue should be
one click from the rule that keeps sending things there.

Not gated on the invoices module. The recurring rules apply to a personal
workspace that never issues a document, and gating the whole router would
hide them from the people the recurring matcher was written for. The
invoice *set* is marked inactive instead, which is the honest signal.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.models.invoice import Invoice
from app.models.reconciliation import ReconciliationRule
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.reconciliation import (
    ReconciliationNodeRead,
    ReconciliationRuleCreate,
    ReconciliationRuleRead,
    ReconciliationRuleUpdate,
    SuggestionRead,
)
from app.services import (
    invoice_service,
    reconciliation_rule_service as rules,
    reconciliation_suggestion_service as suggestions,
)
from app.services.module_service import ModuleId, resolve_modules
from app.services.reconciliation_policy import MATCH_INVOICE

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


def _http(error: rules.RuleError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": error.code, "message": error.message},
    )


def _as_read(node: str, strategy: dict, position: int) -> ReconciliationRuleRead:
    return ReconciliationRuleRead(
        id=strategy["id"],
        node=node,
        name=strategy.get("name"),
        origin=strategy.get("origin", "default"),
        customised=bool(strategy.get("customised")),
        enabled=bool(strategy.get("enabled", True)),
        outcome=strategy.get("outcome", "suggest"),
        when=strategy.get("when", {}),
        position=position,
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@router.get("/rules", response_model=list[ReconciliationNodeRead])
async def list_rules(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Every rule this workspace runs, in the order they are tried.

    Shipped ∪ changed, composed on read. Nothing here is stored as a list
    of rules — that is what keeps an untouched rule improving when we
    improve it.
    """
    enabled_modules = resolve_modules(ctx.workspace)
    out: list[ReconciliationNodeRead] = []
    for node in rules.EDITABLE_NODES:
        policy = await rules.resolve(session, ctx.workspace.id, node)
        out.append(
            ReconciliationNodeRead(
                node=node,
                active=(
                    ModuleId.INVOICES.value in enabled_modules
                    if node == MATCH_INVOICE["node"]
                    else True
                ),
                rules=[
                    _as_read(node, strategy, index)
                    for index, strategy in enumerate(policy["strategies"])
                ],
            )
        )
    return out


@router.patch("/rules/{node}/{strategy_id}", response_model=ReconciliationRuleRead)
async def update_rule(
    node: str,
    strategy_id: str,
    payload: ReconciliationRuleUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Change a rule — one we ship, or one the workspace wrote."""
    data = payload.model_dump(exclude_unset=True)
    position = data.pop("position", None)
    existing = await _find_custom(session, ctx.workspace.id, node, strategy_id)

    try:
        if existing is not None:
            merged = {**existing.config, **data}
            await rules.update_custom(session, existing, config=merged, position=position)
        else:
            await rules.upsert_override(
                session,
                ctx.workspace.id,
                ctx.user_id,
                node,
                strategy_id,
                data,
                position=position,
            )
    except rules.RuleError as exc:
        raise _http(exc)

    await session.commit()
    return await _one(session, ctx.workspace.id, node, strategy_id)


@router.post(
    "/rules", response_model=ReconciliationRuleRead, status_code=status.HTTP_201_CREATED
)
async def create_rule(
    payload: ReconciliationRuleCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        row = await rules.create_custom(
            session,
            ctx.workspace.id,
            ctx.user_id,
            payload.node,
            payload.name,
            {
                "enabled": payload.enabled,
                "outcome": payload.outcome,
                "when": payload.when,
            },
            position=payload.position,
        )
    except rules.RuleError as exc:
        raise _http(exc)

    await session.commit()
    return await _one(session, ctx.workspace.id, payload.node, row.strategy_id)


@router.delete("/rules/{node}/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_rule(
    node: str,
    strategy_id: str,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a workspace's own rule, or put a shipped one back.

    One verb for both, because from the page they are the same gesture:
    "stop doing my version of this". What comes back for a shipped rule is
    whatever we ship *today*, not what shipped the day it was changed.
    """
    await rules.reset(session, ctx.workspace.id, node, strategy_id)
    await session.commit()


async def _find_custom(
    session: AsyncSession, workspace_id: uuid.UUID, node: str, strategy_id: str
):
    result = await session.execute(
        select(ReconciliationRule).where(
            ReconciliationRule.workspace_id == workspace_id,
            ReconciliationRule.node == node,
            ReconciliationRule.strategy_id == strategy_id,
            ReconciliationRule.origin == "custom",
        )
    )
    return result.scalar_one_or_none()


async def _one(
    session: AsyncSession, workspace_id: uuid.UUID, node: str, strategy_id: str
) -> ReconciliationRuleRead:
    policy = await rules.resolve(session, workspace_id, node)
    for index, strategy in enumerate(policy["strategies"]):
        if strategy["id"] == strategy_id:
            return _as_read(node, strategy, index)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")


# ---------------------------------------------------------------------------
# The doubtful space
# ---------------------------------------------------------------------------
@router.get("/suggestions", response_model=list[SuggestionRead])
async def list_suggestions(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Matches nobody has answered yet, oldest first.

    Stale ones are retired on the way in rather than by a scheduled job:
    the queue is small, this is the only place it is read, and a nightly
    task that exists solely to change a string is a moving part with no
    reason to exist.
    """
    await suggestions.expire_stale(session, ctx.workspace.id)
    await session.commit()

    rows = await suggestions.open_for(session, ctx.workspace.id)
    return [await _with_label(session, row) for row in rows]


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionRead)
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Take the match, and write it the way its kind is written.

    An invoice gets an allocation; a recurring bill gets the charge bound
    to it. The two are genuinely different writes, which is why the
    suggestion service marks and this route links.
    """
    row = await suggestions.get(session, ctx.workspace.id, suggestion_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found"
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_answered", "message": "This one is already settled"},
        )

    if row.expectation_kind == "invoice":
        invoice = await session.get(Invoice, row.expectation_id)
        if invoice is None or invoice.workspace_id != ctx.workspace.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
            )
        try:
            await invoice_service.allocate(
                session,
                invoice,
                row.transaction_id,
                amount=row.amount,
                # The rule that suggested it, not "manual": the person
                # agreed with a specific rule, and that is worth keeping.
                method=row.strategy_id,
            )
        except invoice_service.InvoiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": exc.code, "message": str(exc)},
            )
    else:
        bill = await session.get(RecurringTransaction, row.expectation_id)
        if bill is None or bill.workspace_id != ctx.workspace.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Recurring bill not found"
            )
        from app.services import recurring_match_service

        transaction = row.transaction
        transaction.recurring_transaction_id = bill.id
        recurring_match_service.advance_past(bill, transaction.date)

    await suggestions.mark_accepted(session, row, ctx.user_id)
    await session.commit()
    return await _with_label(session, row)


@router.post("/suggestions/{suggestion_id}/decline", response_model=SuggestionRead)
async def decline_suggestion(
    suggestion_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Say no. The row stays, so this pair is never offered again."""
    row = await suggestions.get(session, ctx.workspace.id, suggestion_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found"
        )
    await suggestions.decline(session, row, ctx.user_id)
    await session.commit()
    return await _with_label(session, row)


async def _invoice_name(session: AsyncSession, invoice: Invoice) -> Optional[str]:
    """The number as the client would recognise it.

    Mirrors what every invoice screen shows, and for the same reason: the
    snapshot taken at issue is authoritative — including when it recorded
    *no* prefix, which is an answer rather than a gap. An invoice issued
    as "2" must keep reading as "2" after somebody sets a prefix, or the
    queue would name a document differently from the copy the client is
    holding. An imported document keeps the name it arrived with; ours
    would rename a supplier's reference.
    """
    if invoice.origin == "imported":
        return invoice.external_number
    if invoice.number is None:
        return None
    if invoice.snapshot is not None:
        prefix = invoice.snapshot.get("number_prefix") or ""
    else:
        settings = await invoice_service.get_settings(session, invoice.workspace_id)
        prefix = settings.number_prefix or ""
    return f"{prefix}{invoice.number}"


async def _with_label(session: AsyncSession, row) -> SuggestionRead:
    """Name the promise a suggestion points at.

    Resolved here rather than stored on the row: an invoice that gets
    renumbered or a bill that gets renamed should read correctly in the
    queue, not as it was called on the day we became unsure about it.
    """
    label = None
    if row.expectation_kind == "invoice":
        invoice = await session.get(Invoice, row.expectation_id)
        if invoice is not None:
            label = await _invoice_name(session, invoice)
    else:
        bill = await session.get(RecurringTransaction, row.expectation_id)
        if bill is not None:
            label = bill.description

    read = SuggestionRead.model_validate(row)
    read.expectation_label = label
    return read
