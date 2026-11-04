"""The matching rules, and the questions matching could not answer.

Two surfaces that belong together: the rules decide, and the queue is
where a decision was not confident enough to be made alone. Seeing them
side by side is the point: somebody staring at a long queue should be
one click from the rule that keeps sending things there.

**Open to every workspace, gated per set.** The router itself asks only
for a workspace, because one of the sets it serves belongs to no module:
pairing the two legs of a transfer has run on every sync for every
workspace, personal ones included, since long before modules existed.
Gating the router on invoicing would have hidden the rules for the
automation that touches the most people's data.

Which *set* you may touch is still a second question, asked per node by
`_assert_node`, and it answers 404 rather than 403: a workspace without
invoicing should not learn that invoice matching exists by being told it
may not look.

This has moved twice, and both moves were the same correction. The
router was open while recurring rules were editable here, then closed to
the modules when those left the page, and a personal workspace was left
able to write and reorder rules that could never fire: configuration
accumulating for a module nobody had. The answer was never the gate. It
was that the page had nothing on it for a personal workspace. Now it
does.
"""
import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.reconciliation import ReconciliationRule
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.reconciliation import (
    DiscardedRuleRead,
    ReconciliationImportRequest,
    ReconciliationImportResponse,
    ReconciliationNodeRead,
    ReconciliationRuleCreate,
    ReconciliationRuleRead,
    ReconciliationOrder,
    ReconciliationRuleUpdate,
    HistoryEventRead,
    SuggestionCovers,
    SuggestionRead,
)
from app.services import (
    invoice_service,
    reconciliation_history_service as history,
    reconciliation_portability as portability,
    reconciliation_rule_service as rules,
    reconciliation_suggestion_service as suggestions,
)
from app.services.module_service import resolve_modules

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

#: A workspace, and for writes a role that may write. No module: every
#: workspace has transfer pairing, so there is no version of this page
#: that is empty, and a gate that hid it would be hiding the rules for
#: something already running on the reader's own data. The per-node check
#: below is what keeps the invoice set out of a workspace without it.
_read = current_workspace
_write = current_writable_workspace


def _assert_node(ctx: WorkspaceContext, node: str) -> None:
    """Refuse a set this workspace does not have, the same way as a route.

    404 rather than 403, matching the module gate above: a workspace
    without invoicing should not learn that invoice matching exists by
    being told it may not touch it.
    """
    if node not in rules.nodes_for(resolve_modules(ctx.workspace)):
        raise HTTPException(status_code=404, detail="Not found")


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
        trigger=strategy.get("trigger", "money_arrives"),
        when=strategy.get("when", {}),
        position=position,
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@router.get("/rules", response_model=list[ReconciliationNodeRead])
async def list_rules(
    ctx: WorkspaceContext = Depends(_read),
    session: AsyncSession = Depends(get_async_session),
):
    """Every rule this workspace runs, in the order they are tried.

    Shipped ∪ changed, composed on read. Nothing here is stored as a list
    of rules: that is what keeps an untouched rule improving when we
    improve it.
    """
    out: list[ReconciliationNodeRead] = []
    for node in rules.nodes_for(resolve_modules(ctx.workspace)):
        policy = await rules.resolve(session, ctx.workspace.id, node)
        out.append(
            ReconciliationNodeRead(
                node=node,
                # Structural: `nodes_for` already dropped the sets this
                # workspace does not have, so a set that is listed is a
                # set that runs. The flag stays on the wire for the day a
                # node depends on something else.
                active=True,
                rules=[
                    _as_read(node, strategy, index)
                    for index, strategy in enumerate(policy["strategies"])
                ],
                discarded=[
                    DiscardedRuleRead(**item) for item in policy.get("discarded", [])
                ],
            )
        )
    return out


@router.patch("/rules/{node}/{strategy_id}", response_model=ReconciliationRuleRead)
async def update_rule(
    node: str,
    strategy_id: str,
    payload: ReconciliationRuleUpdate,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Change a rule: one we ship, or one the workspace wrote."""
    _assert_node(ctx, node)
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


@router.put(
    "/rules/{node}/order", response_model=list[ReconciliationRuleRead]
)
async def reorder_rules(
    node: str,
    payload: ReconciliationOrder,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Set the order rules are tried in.

    The first rule that matches wins, so this is not cosmetic: it is how a
    band is expressed: *link under two per cent, ask between two and
    five* is one rule placed above another, with no lower bound written
    anywhere.
    """
    _assert_node(ctx, node)
    try:
        await rules.reorder(
            session, ctx.workspace.id, ctx.user_id, node, payload.order
        )
    except rules.RuleError as exc:
        raise _http(exc)
    await session.commit()

    policy = await rules.resolve(session, ctx.workspace.id, node)
    return [
        _as_read(node, strategy, index)
        for index, strategy in enumerate(policy["strategies"])
    ]


@router.post(
    "/rules", response_model=ReconciliationRuleRead, status_code=status.HTTP_201_CREATED
)
async def create_rule(
    payload: ReconciliationRuleCreate,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    _assert_node(ctx, payload.node)
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
                "trigger": payload.trigger,
                "when": payload.when,
            },
            position=payload.position,
        )
    except rules.RuleError as exc:
        raise _http(exc)

    await session.commit()
    return await _one(session, ctx.workspace.id, payload.node, row.strategy_id)


@router.delete("/rules/{node}/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    node: str,
    strategy_id: str,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Get rid of a rule, including one of ours.

    There is no rule we refuse to remove. What happens underneath differs:
    a workspace's own rule is a row and goes; one of ours is a document
    in the image, so a tombstone records that this workspace does not run
    it, but that is our problem, not something to make a person learn.
    """
    _assert_node(ctx, node)
    try:
        await rules.delete_rule(session, ctx.workspace.id, node, strategy_id)
    except rules.RuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message)
    await session.commit()


@router.post("/rules/{node}/{strategy_id}/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_rule(
    node: str,
    strategy_id: str,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Forget everything this workspace did to one of our rules.

    One verb for every kind of disagreement, because they are all the same
    row: a threshold somebody moved, a place in the order, and a rule
    somebody deleted all go back together. What comes back is whatever we
    ship *today*, not what shipped the day it was changed.
    """
    _assert_node(ctx, node)
    await rules.reset(session, ctx.workspace.id, node, strategy_id)
    await session.commit()


# ---------------------------------------------------------------------------
# Carrying a policy elsewhere
# ---------------------------------------------------------------------------
@router.get("/rules/export")
async def export_rules(
    node: Optional[str] = None,
    ctx: WorkspaceContext = Depends(_read),
    session: AsyncSession = Depends(get_async_session),
):
    """The matching policy as a file, with ids resolved to names.

    `node` narrows it to one set. Each set is its own card on the page
    with its own button, and a button under *Transfers* that quietly
    hands over the invoice rules too is a button that lies. Without it
    the file carries everything this workspace has, which is what the
    page-level export always meant.
    """
    available = rules.nodes_for(resolve_modules(ctx.workspace))
    if node is not None:
        _assert_node(ctx, node)
        available = (node,)
    payload = await portability.export_policy(session, ctx.workspace.id, available)
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="securo-reconciliation-rules.json"',
        },
    )


@router.post("/rules/import", response_model=ReconciliationImportResponse)
async def import_rules(
    data: ReconciliationImportRequest,
    node: Optional[str] = None,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Take a policy file back in.

    `node` narrows it the same way the export does, and for the stronger
    reason: the button sits on one card, so a file dropped on *Transfers*
    must not be able to rewrite the invoice rules. Sets the file carries
    and this call did not ask for are skipped and counted, exactly as a
    set this workspace does not have already is.
    """
    available = rules.nodes_for(resolve_modules(ctx.workspace))
    if node is not None:
        _assert_node(ctx, node)
        available = (node,)
    try:
        result = await portability.import_policy(
            session,
            ctx.workspace.id,
            ctx.user_id,
            data.payload,
            overwrite=data.overwrite,
            nodes=available,
        )
    except rules.ExistingPolicyError as exc:
        # 409 rather than 400: nothing is wrong with the file, and the
        # answer is a question for the person rather than a correction.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except rules.RuleError as exc:
        # The same shape as every other error here, code included: "12
        # imported, 40 skipped" does not say what was wrong with the file,
        # and a refusal that cannot name its reason is a refusal nobody
        # can act on.
        raise _http(exc)
    await session.commit()
    return ReconciliationImportResponse(**result)


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
    ctx: WorkspaceContext = Depends(_read),
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

    # One row per *question*. A payment offered against three invoices is
    # one thing to decide, and listing it three times would invite exactly
    # the inconsistent answer the grouping exists to prevent.
    seen: set[uuid.UUID] = set()
    out = []
    for row in rows:
        if row.group_id is not None:
            if row.group_id in seen:
                continue
            seen.add(row.group_id)
        out.append(await _with_label(session, row))
    return out


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionRead)
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Take the match, and write it the way its kind is written.

    An invoice gets an allocation; a recurring bill gets the charge bound
    to it. The two are genuinely different writes, which is why the
    suggestion service marks and this route links.

    **A payment offered against several invoices is accepted whole.** The
    question was "does this cover these three", so answering it two thirds
    of the way would leave the payment short on the rest with nothing
    having warned anybody. All the writes happen inside one savepoint: if
    the last invoice refuses, the first two are not left standing.
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

    members = await suggestions.members_of(session, row)
    try:
        async with session.begin_nested():
            for member in members:
                await _settle(session, ctx, member)
    except invoice_service.InvoiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        )

    for member in members:
        await suggestions.mark_accepted(session, member, ctx.user_id)
    # One event for the whole question, written where the whole act is known.
    await suggestions.answered(session, members, "accepted", ctx.user_id)
    await session.commit()
    return await _with_label(session, row)


#: What must still be true of two rows before they may be bound as one
#: transfer, whatever rule proposed them.
#:
#: The first four are the definition: money crossing between two accounts
#: moves the opposite way on each side, in one currency. They are checked
#: rather than assumed because a transaction stays editable while its
#: suggestion sits in the queue.
#:
#: The amounts are checked against what was recorded when the question
#: was raised, not against each other. A rule may well have matched an
#: approximate pair, so "are they equal" is the wrong question; "are they
#: what the person is being shown" is the right one.
def _transfer_still_holds(moved: Transaction, counterpart: Transaction, row) -> bool:
    if moved.account_id == counterpart.account_id:
        return False
    if moved.type == counterpart.type:
        return False
    if moved.currency != counterpart.currency:
        return False

    scores = row.scores or {}
    for leg, key in ((moved, "amount_moved"), (counterpart, "amount_expected")):
        recorded = scores.get(key)
        if recorded is None:
            # An older row from before the breakdown carried these. The
            # definitional checks above still ran; there is nothing more
            # to compare against, and refusing every one of them would be
            # answering uncertainty with a dead queue.
            continue
        try:
            if abs(Decimal(leg.amount)) != Decimal(str(recorded)):
                return False
        except (ArithmeticError, ValueError):
            return False
    return True


async def _settle(
    session: AsyncSession, ctx: WorkspaceContext, row
) -> None:
    """Write one member of an accepted question."""
    if row.expectation_kind == "transaction":
        # The two legs of a transfer. Nothing is allocated and nothing is
        # replaced: they are both real rows that stay, and agreeing means
        # they learn they are halves of one movement.
        #
        # **Both rows are locked, not just the suggestion.** The lock on
        # the suggestion guards this row and no other, and one transaction
        # can sit in two pending questions at once: the ambiguous case
        # that raises them puts the same credit against two debits, side
        # by side in the queue with a button each. Two accepts a moment
        # apart would both read it as unpaired, both write, and leave the
        # loser's other leg carrying a pair id with nothing on the far
        # end. Ordered by id so two requests cannot take the locks in
        # opposite orders and deadlock.
        locked = await session.execute(
            select(Transaction)
            .where(
                Transaction.id.in_([row.transaction_id, row.expectation_id]),
                Transaction.workspace_id == ctx.workspace.id,
            )
            .order_by(Transaction.id)
            .with_for_update(of=Transaction)
        )
        by_id = {leg.id: leg for leg in locked.scalars().all()}
        moved = by_id.get(row.transaction_id)
        counterpart = by_id.get(row.expectation_id)
        if moved is None or counterpart is None:
            raise invoice_service.InvoiceError(
                "transaction_missing", "One of the transactions is no longer there"
            )
        legs = (moved, counterpart)
        if any(leg.transfer_pair_id is not None for leg in legs):
            # Paired since we asked, by a sync, a rule someone edited, or
            # the person doing it by hand. Re-pairing would overwrite a
            # link somebody may have chosen deliberately.
            raise invoice_service.InvoiceError(
                "already_paired", "One of these is already part of a transfer"
            )
        if not _transfer_still_holds(moved, counterpart, row):
            # **The rows are not the ones the question was about.** A
            # transaction stays editable while its suggestion waits, so
            # the amount, the account, the direction or the currency can
            # all have moved since we asked. Accepting anyway would bind
            # two rows on the strength of an answer to a different
            # question, and a paired row leaves income and expense, so
            # the damage is a total quietly changing.
            raise invoice_service.InvoiceError(
                "suggestion_stale",
                "These transactions changed since we asked. Check them again.",
            )
        pair_id = uuid.uuid4()
        for leg in legs:
            leg.transfer_pair_id = pair_id
        return

    if row.expectation_kind == "invoice":
        invoice = await session.get(Invoice, row.expectation_id)
        if invoice is None or invoice.workspace_id != ctx.workspace.id:
            raise invoice_service.InvoiceError(
                "invoice_missing", "The invoice is no longer there"
            )
        await invoice_service.allocate(
            session,
            invoice,
            row.transaction_id,
            amount=row.amount,
            # The rule that suggested it, not "manual": the person agreed
            # with a specific rule, and that is worth keeping.
            method=row.strategy_id,
        )
        return

    bill = await session.get(RecurringTransaction, row.expectation_id)
    if bill is None or bill.workspace_id != ctx.workspace.id:
        raise invoice_service.InvoiceError(
            "bill_missing", "The recurring bill is no longer there"
        )
    from app.services import recurring_match_service

    transaction = row.transaction
    transaction.recurring_transaction_id = bill.id
    recurring_match_service.advance_past(bill, transaction.date)


@router.post("/suggestions/{suggestion_id}/decline", response_model=SuggestionRead)
async def decline_suggestion(
    suggestion_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    """Say no. The rows stay, so this pair is never offered again.

    A grouped question is refused whole, for the same reason it is
    accepted whole: it was one question."""
    row = await suggestions.get(session, ctx.workspace.id, suggestion_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found"
        )
    if row.status != "pending":
        # The same guard accepting already has. Without it a decline
        # arriving after an accept would mark the row declined and write
        # a second history event while `_settle`'s allocation stayed
        # exactly where it was: a suggestion reading "dismissed" on top of
        # an invoice it had settled.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_answered",
                "message": "This one is already settled",
            },
        )
    members = await suggestions.members_of(session, row)
    for member in members:
        await suggestions.decline(session, member, ctx.user_id)
    await suggestions.answered(session, members, "declined", ctx.user_id)
    await session.commit()
    return await _with_label(session, row)


async def _invoice_name(session: AsyncSession, invoice: Invoice) -> Optional[str]:
    """The number as the client would recognise it.

    Mirrors what every invoice screen shows, and for the same reason: the
    snapshot taken at issue is authoritative: including when it recorded
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
    """Name the promises a suggestion points at.

    Resolved here rather than stored on the row: an invoice that gets
    renumbered or a bill that gets renamed should read correctly in the
    queue, not as it was called on the day we became unsure about it.
    """
    read = SuggestionRead.model_validate(row)
    read.covers = [
        SuggestionCovers(
            expectation_kind=member.expectation_kind,
            expectation_id=member.expectation_id,
            label=await _name_of(session, member),
            amount=member.amount,
        )
        for member in await suggestions.members_of(session, row)
    ]
    read.expectation_label = read.covers[0].label if read.covers else None
    # What the whole question is worth, so a grouped row shows the payment
    # rather than one slice of it.
    read.amount = sum((c.amount for c in read.covers), Decimal("0"))
    return read


async def _name_of(session: AsyncSession, row) -> Optional[str]:
    if row.expectation_kind == "transaction":
        # The account it came from or went to, not the statement text.
        # "Is this the other half of a transfer" is a question about two
        # accounts, and the description is usually a reference number
        # that answers nothing.
        leg = await session.get(Transaction, row.expectation_id)
        if leg is None:
            return None
        account = await session.get(Account, leg.account_id)
        return account.name if account else leg.description
    if row.expectation_kind == "invoice":
        invoice = await session.get(Invoice, row.expectation_id)
        return await _invoice_name(session, invoice) if invoice else None
    bill = await session.get(RecurringTransaction, row.expectation_id)
    return bill.description if bill else None


async def _currency_of(session: AsyncSession, row) -> Optional[str]:
    """What the recorded amount is denominated in.

    Taken from the promise rather than from the movement, because the
    settlement was written in the promise's currency and matching refuses
    a pair whose currencies disagree, so there is only ever one answer.
    """
    if row.expectation_kind == "transaction":
        leg = await session.get(Transaction, row.expectation_id)
        return leg.currency if leg else None
    if row.expectation_kind == "invoice":
        invoice = await session.get(Invoice, row.expectation_id)
        return invoice.currency if invoice else None
    bill = await session.get(RecurringTransaction, row.expectation_id)
    return bill.currency if bill else None


# ---------------------------------------------------------------------------
# What matching did
# ---------------------------------------------------------------------------
@router.get("/history", response_model=list[HistoryEventRead])
async def list_history(
    limit: int = 50,
    expectation_id: Optional[uuid.UUID] = None,
    ctx: WorkspaceContext = Depends(_read),
    session: AsyncSession = Depends(get_async_session),
):
    """The stream, newest first, or everything that happened to one promise.

    Newest first here and oldest first in the queue, deliberately: a queue
    is work to get through, so its oldest question is the most urgent,
    while a history is read to find out what just happened.
    """
    events = await history.recent(
        session, ctx.workspace.id, expectation_id=expectation_id, limit=limit
    )

    out: list[HistoryEventRead] = []
    for event in events:
        read = HistoryEventRead.model_validate(event)
        read.expectation_label = await _name_of(session, event)
        read.currency = await _currency_of(session, event)
        if event.transaction_id is not None:
            transaction = await session.get(Transaction, event.transaction_id)
            read.transaction_description = transaction.description if transaction else None
        out.append(read)
    return out
