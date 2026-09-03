"""Bringing money and promises together, and writing the result.

The half that touches the database. `reconciliation_engine` decides;
this loads the candidates, hands them over, and applies what comes back.
Keeping the two apart is what lets the decision be dry-run later, and it
is the reason this module contains no thresholds.

## It runs in both directions, and that is not symmetry for its own sake

Money arrives and settles an invoice that already existed: the obvious
case. But the common Brazilian one runs the other way: the client pays,
*then* the nota is issued. An invoice created on Tuesday is often settled
by money that landed on Friday of the week before, and a matcher that
only looked forward would never see it. So issuing an invoice looks back
at money nobody has explained yet.

## Both ports are acted on, and they are not equals

`linked` writes an allocation. `suggested` goes to the queue in
`reconciliation_suggestion_service`, to be answered by a person.

**The automatic tier carries the traffic and the queue is the residue.**
That ordering is the product, not an implementation detail: the
accountant interview is unambiguous that import-then-make-the-user-confirm
is what killed adoption of the incumbent (*"eles acharam muito
trabalhoso"*), and a product that turns every payment into a confirmation
is that product with a different logo. If the queue is where the volume
goes, the rules are wrong, and the fix belongs in the rules.

## The rules are the workspace's, not ours

Which is why nothing here calls `default_policy` any more. The policy
comes from `reconciliation_rule_service.resolve`, which is what we ship
with whatever this workspace changed applied over it. A person can turn a
rule off, loosen it, demote it from linking to suggesting, or write one
of their own, and this module simply runs what comes back.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice, InvoiceAllocation
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services import (
    invoice_service,
    reconciliation_history_service,
    reconciliation_policy,
    reconciliation_rule_service,
    reconciliation_suggestion_service,
)
from app.services.module_service import ModuleId, resolve_modules
from app.services.reconciliation_engine import (
    Decision,
    Expectation,
    Movement,
    evaluate,
)

#: The policy document this module runs under.
NODE = reconciliation_policy.MATCH_INVOICE["node"]

#: The other kind of promise. A personal workspace has only these, and
#: the doubtful space has to serve it too.
RECURRING_NODE = reconciliation_policy.MATCH_RECURRING["node"]

#: How far back issuing an invoice looks for money that already arrived.
#: Wide enough for the pay-then-invoice case, bounded so issuing an
#: invoice never scans a year of a busy account.
LOOKBACK_DAYS = 90


def _as_expectation(invoice: Invoice) -> Expectation:
    """One open invoice, as the engine sees it.

    The amount is the **balance**, never the total: an invoice half paid
    expects the other half, and a market that pays by Pix pays in parts.
    """
    return Expectation(
        kind="invoice",
        id=invoice.id,
        amount=invoice_service.balance(invoice),
        currency=invoice.currency,
        # A receivable is settled by money coming in, a payable by money
        # going out. The engine refuses a candidate facing the wrong way.
        direction="credit" if invoice.direction == "receivable" else "debit",
        when=invoice.due_date,
        # Both dates: late is measured from the due date, early from the
        # day the document was written.
        issued=invoice.issue_date,
        description=invoice.notes or (invoice.payee.name if invoice.payee else None),
        payee_id=invoice.payee_id,
    )


def _as_movement(transaction: Transaction) -> Movement:
    return Movement(
        amount=Decimal(transaction.amount or 0),
        currency=transaction.currency or "",
        direction=transaction.type,
        when=transaction.date,
        description=transaction.description,
        # The raw string the bank sent, not the person we resolved it to.
        # A rule may need it before any mapping exists, which is the usual
        # state of a Pix from a client who has not been seen before.
        counterparty=transaction.payee,
        payee_id=transaction.payee_id,
        account_id=transaction.account_id,
        source=transaction.source,
    )


async def _base_currency(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[str]:
    """What this workspace normally deals in.

    Only rules that say "foreign" consult it, and they need it because
    foreign is relative: dollars are unremarkable to a workspace that
    keeps its books in dollars and worth a second look to one that does
    not.
    """
    workspace = await session.get(Workspace, workspace_id)
    return workspace.default_currency if workspace else None


async def _module_is_on(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Whether this workspace has invoicing at all.

    The candidate query would return nothing for a personal workspace
    anyway, so this is not correctness: it is the modularity promise
    kept literally: a workspace that never enabled the module pays one
    cheap lookup on an already-loaded row, not a join against a table it
    has no rows in, on every transaction of every sync.
    """
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return False
    return ModuleId.INVOICES.value in resolve_modules(workspace)


async def _open_invoices(
    session: AsyncSession, workspace_id: uuid.UUID, policy: dict
) -> list[Invoice]:
    """Every invoice the policy considers still waiting for money.

    Two filters, and the split is not incidental. `status` is a stored
    decision (draft, void, written off), so SQL can exclude it. Whether
    something is `partial` or `overdue` is **derived per read** from the
    allocations and the clock, so it is decided here, against the same
    function the screen uses. Reading `candidate_states` rather than
    hard-coding it is what keeps the policy a document instead of a
    comment: a workspace that stops auto-matching overdue invoices
    changes that list, not this file.

    Allocations are eager-loaded because the balance is computed from
    them: fetching them per invoice would turn one match into an N+1 on
    an ingest path that already handles hundreds of rows.
    """
    wanted = set(policy.get("scope", {}).get("candidate_states", []))
    result = await session.execute(
        select(Invoice)
        .where(Invoice.workspace_id == workspace_id, Invoice.status == "open")
        .options(
            selectinload(Invoice.allocations),
            selectinload(Invoice.payee),
        )
    )
    today = date.today()
    return [
        invoice
        for invoice in result.unique().scalars().all()
        if invoice_service.derive_state(invoice, today) in wanted
    ]


async def _already_settles_something(
    session: AsyncSession, transaction_id: uuid.UUID
) -> bool:
    """Whether this money is already accounted for against an invoice.

    Re-matching a transaction that already carries an allocation would
    let one payment settle two debts, which is how a ledger starts
    disagreeing with a bank."""
    result = await session.execute(
        select(InvoiceAllocation.id)
        .where(InvoiceAllocation.transaction_id == transaction_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _apply(
    session: AsyncSession, decision: Decision, transaction: Transaction
) -> Optional[InvoiceAllocation]:
    """Write what the engine decided.

    Applying is deliberately a separate step from deciding, and it goes
    through `allocate` rather than around it: every guard that protects a
    hand-made link (the invoice is open, the currency agrees, the amount
    fits) protects an automatic one too. **An automatic decision is not
    a trusted one.**

    **All of it or none of it.** One payment can settle several invoices,
    and a payout that lands against four of them and fails on the fifth
    would leave a ledger nobody can explain: money spread across some
    debts, the rest of it unaccounted for, and no record of what was
    attempted. The savepoint makes the whole set a single act.
    """
    if decision.port != "linked" or not decision.settlements:
        return None

    first: Optional[InvoiceAllocation] = None
    try:
        async with session.begin_nested():
            for settlement in decision.settlements:
                invoice = await session.get(Invoice, settlement.expectation.id)
                if invoice is None:
                    raise invoice_service.InvoiceError(
                        "invoice_missing", "The invoice is no longer there"
                    )
                allocation = await invoice_service.allocate(
                    session,
                    invoice,
                    transaction.id,
                    amount=settlement.amount,
                    # The strategy id, not a generic "auto": this is what
                    # lets the screen answer "why is this linked" with the
                    # name of the rule that fired.
                    method=decision.strategy or "auto",
                )
                if first is None:
                    first = allocation
                await reconciliation_history_service.record(
                    session,
                    invoice.workspace_id,
                    "linked",
                    expectation_kind=settlement.expectation.kind,
                    expectation_id=settlement.expectation.id,
                    amount=settlement.amount,
                    transaction_id=transaction.id,
                    strategy_id=decision.strategy,
                    # No user: the rules did this on their own, and that
                    # is the distinction a reader reaches for first.
                    detail={"of_set": len(decision.settlements)},
                )
    except invoice_service.InvoiceError:
        # A guard refused one of them, so none of them happened. The money
        # stays unexplained, which is the honest outcome: the alternative
        # is a half-settled payout.
        return None

    return first


async def match_incoming(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[Transaction],
) -> list[InvoiceAllocation]:
    """Bind newly arrived money to the promises it answers.

    Called once per batch rather than once per row: the candidate list is
    fetched a single time and reused, so a sync importing three hundred
    transactions runs one query for invoices instead of three hundred.

    Two passes, and the second is not an afterthought. A workspace that
    never issues an invoice still has promises (the rent leaving on the
    5th, the retainer arriving on the 20th), and the doubtful space has to
    exist for them too, or reconciliation would be a feature only
    businesses got.
    """
    if not transactions:
        return []

    applied: list[InvoiceAllocation] = []
    if await _module_is_on(session, workspace_id):
        applied = await _match_invoices(session, workspace_id, transactions)

    await _suggest_recurring(session, workspace_id, transactions)
    return applied


async def _match_invoices(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[Transaction],
) -> list[InvoiceAllocation]:
    # What the workspace normally deals in, so a rule can say "foreign".
    # Foreign is a fact about the workspace, not about the money.
    base_currency = await _base_currency(session, workspace_id)
    policy = await reconciliation_rule_service.resolve(session, workspace_id, NODE)
    candidates = await _open_invoices(session, workspace_id, policy)
    if not candidates:
        return []

    applied: list[InvoiceAllocation] = []

    for transaction in transactions:
        if await _already_settles_something(session, transaction.id):
            continue

        expectations = [_as_expectation(inv) for inv in candidates]
        movement = _as_movement(transaction)
        decision = evaluate(
            movement,
            expectations,
            policy,
            withholding_ratios=reconciliation_policy.withholding_ratios(None),
            base_currency=base_currency,
        )
        allocation = await _apply(session, decision, transaction)
        if allocation is not None:
            applied.append(allocation)
            # Re-read on the next pass: this invoice's balance just
            # dropped, and the following transaction must see that rather
            # than settling the same debt twice.
            candidates = await _open_invoices(session, workspace_id, policy)
        else:
            # Not confident enough to act. The pair goes to the queue,
            # where the service refuses to re-ask anything already
            # answered, including anything already refused.
            await reconciliation_suggestion_service.record(
                session, workspace_id, transaction.id, decision, movement, NODE
            )

    return applied


async def _suggest_recurring(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[Transaction],
) -> None:
    """Offer the recurring bills that money might answer.

    Nothing is linked here. A charge that the recurring rules were
    confident about was already bound during normalisation, in place, and
    carries a `recurring_transaction_id` by the time it reaches this
    function, so anything still unattached is by definition something
    that pass was *not* sure about.

    Which is exactly why this exists: it is what happens when somebody
    demotes the recurring rule from linking to suggesting. Without it that
    setting would be a switch that quietly does nothing, and a
    configuration screen whose switches do nothing is worse than no screen.
    """
    unexplained = [
        transaction
        for transaction in transactions
        if transaction.recurring_transaction_id is None
        and transaction.source != "recurring"
        and not transaction.is_ignored
    ]
    if not unexplained:
        return

    result = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active.is_(True),
        )
    )
    bills = list(result.scalars().all())
    if not bills:
        return

    from app.services.recurring_transaction_service import adjust_weekend_date

    composed = await reconciliation_rule_service.resolve(
        session, workspace_id, RECURRING_NODE
    )
    base_currency = await _base_currency(session, workspace_id)

    for transaction in unexplained:
        if await _already_settles_something(session, transaction.id):
            continue
        movement = _as_movement(transaction)
        for bill in bills:
            if bill.account_id != transaction.account_id:
                continue
            occurrence = adjust_weekend_date(
                bill.next_occurrence, bill.weekend_adjustment
            )
            decision = evaluate(
                movement,
                [
                    Expectation(
                        kind="recurring",
                        id=bill.id,
                        amount=Decimal(bill.amount or 0),
                        currency=bill.currency,
                        direction=bill.type,
                        when=occurrence,
                        description=bill.description,
                        account_id=bill.account_id,
                    )
                ],
                reconciliation_rule_service.narrow_for_frequency(
                    composed, bill.frequency
                ),
                base_currency=base_currency,
            )
            if decision.port == "suggested":
                await reconciliation_suggestion_service.record(
                    session,
                    workspace_id,
                    transaction.id,
                    decision,
                    movement,
                    RECURRING_NODE,
                )


async def match_for_invoice(
    session: AsyncSession, invoice: Invoice
) -> Optional[InvoiceAllocation]:
    """Look back at money that arrived before this invoice existed.

    The pay-then-invoice case: a client pays, and the nota follows days
    later. Nothing about the money changed, so nothing would ever
    re-examine it: this is the pass that does.

    **Which rules run here is the rules' own business, not this
    function's.** It used to be hardcoded: only known-client strategies,
    decided in this file, and that was a restriction nobody could see or
    change, on a page whose whole purpose is that matching is not a black
    box. Now a rule declares the moments it trusts, and this pass simply
    asks for the ones that trust this one.

    The asymmetry that motivated the old restriction is still real, and it
    lives in the shipped defaults instead: money of an exact value from a
    payer we cannot name links when an invoice was already open and
    waiting, and does not when the document came afterwards. The
    difference is that a workspace can now disagree.

    One allocation at most, and none at all if several movements fit.
    Picking the most recent of three identical payments would be
    inventing certainty exactly where the forward direction refuses to.
    """
    policy = await reconciliation_rule_service.resolve(
        session, invoice.workspace_id, NODE
    )
    wanted = set(policy.get("scope", {}).get("candidate_states", []))
    if invoice_service.derive_state(invoice, date.today()) not in wanted:
        return None

    policy["strategies"] = [
        strategy
        for strategy in policy["strategies"]
        if strategy.get("trigger", "money_arrives") in ("invoice_issued", "both")
    ]
    if not policy["strategies"]:
        return None

    window_start = invoice.due_date - timedelta(days=LOOKBACK_DAYS)
    wanted_direction = "credit" if invoice.direction == "receivable" else "debit"

    # Not narrowed by payee any more. The old query could afford it
    # because the only rules that ran here demanded a known client; now
    # that the rules decide, the query has to be able to see everything
    # they might legitimately match, or a workspace's own rule would fail
    # for a reason nothing on screen could explain.
    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.workspace_id == invoice.workspace_id,
            Transaction.type == wanted_direction,
            Transaction.currency == invoice.currency,
            Transaction.date >= window_start,
            Transaction.is_ignored.is_(False),
            # A generated placeholder is a promise, not money that
            # arrived. The policy says so too; saying it here as well
            # keeps the query from loading rows only to discard them.
            Transaction.source != "recurring",
        )
        .order_by(Transaction.date.desc())
        .limit(200)
    )

    expectation = [_as_expectation(invoice)]
    settles: list[tuple[Transaction, Decision]] = []

    for transaction in result.scalars().all():
        if await _already_settles_something(session, transaction.id):
            continue
        movement = _as_movement(transaction)
        decision = evaluate(
            movement,
            expectation,
            policy,
            withholding_ratios=reconciliation_policy.withholding_ratios(None),
            base_currency=await _base_currency(session, invoice.workspace_id),
            trigger="invoice_issued",
        )
        if decision.port == "linked":
            settles.append((transaction, decision))
            if len(settles) > 1:
                # Two payments answer this invoice equally well. Neither
                # is taken; a person decides which.
                return None
        elif decision.port == "suggested":
            # Dropping these would make a rule set to suggest do nothing
            # at this moment while doing something at the other; the same
            # silent switch the queue exists to prevent.
            await reconciliation_suggestion_service.record(
                session, invoice.workspace_id, transaction.id, decision, movement, NODE
            )

    if not settles:
        return None

    transaction, decision = settles[0]
    return await _apply(session, decision, transaction)
