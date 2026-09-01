"""Bringing money and promises together, and writing the result.

The half that touches the database. `reconciliation_engine` decides;
this loads the candidates, hands them over, and applies what comes back.
Keeping the two apart is what lets the decision be dry-run later, and it
is the reason this module contains no thresholds.

## It runs in both directions, and that is not symmetry for its own sake

Money arrives and settles an invoice that already existed — the obvious
case. But the common Brazilian one runs the other way: the client pays,
*then* the nota is issued. An invoice created on Tuesday is often settled
by money that landed on Friday of the week before, and a matcher that
only looked forward would never see it. So issuing an invoice looks back
at money nobody has explained yet.

## Only `linked` is acted on here

The engine also returns `suggested`. Nothing stores those yet, and
dropping them costs nothing today because today nothing matches at all.
They need a row of their own — with `declined` and `expired` states, so a
suggestion somebody rejected does not come back on the next sync — and
that is its own slice.

What must stay true when it arrives: **the automatic tier carries the
traffic and the queue is the residue.** The accountant interview is
unambiguous that import-then-make-the-user-confirm is what killed
adoption of the incumbent — *"eles acharam muito trabalhoso"* — and a
product that turns every payment into a confirmation is that product
with a different logo.
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
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services import invoice_service, reconciliation_policy
from app.services.module_service import ModuleId, resolve_modules
from app.services.reconciliation_engine import (
    Decision,
    Expectation,
    Movement,
    evaluate,
)

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
        payee_id=transaction.payee_id,
        account_id=transaction.account_id,
        source=transaction.source,
    )


async def _module_is_on(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Whether this workspace has invoicing at all.

    The candidate query would return nothing for a personal workspace
    anyway, so this is not correctness — it is the modularity promise
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
    decision — draft, void, written off — so SQL can exclude it. Whether
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
    hand-made link — the invoice is open, the currency agrees, the amount
    fits — protects an automatic one too. **An automatic decision is not
    a trusted one.**
    """
    if decision.port != "linked" or decision.expectation is None:
        return None

    invoice = await session.get(Invoice, decision.expectation.id)
    if invoice is None:
        return None

    try:
        return await invoice_service.allocate(
            session,
            invoice,
            transaction.id,
            amount=decision.amount,
            # The strategy id, not a generic "auto": this is what lets the
            # screen answer "why is this linked" with the name of the rule
            # that fired.
            method=decision.strategy or "auto",
        )
    except invoice_service.InvoiceError:
        # A guard refused it. The money stays unexplained, which is the
        # honest outcome — the alternative is a link the ledger rejects.
        return None


async def match_incoming(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[Transaction],
) -> list[InvoiceAllocation]:
    """Bind newly arrived money to the invoices it settles.

    Called once per batch rather than once per row: the candidate list is
    fetched a single time and reused, so a sync importing three hundred
    transactions runs one query for invoices instead of three hundred.
    """
    if not transactions:
        return []

    if not await _module_is_on(session, workspace_id):
        return []

    policy = reconciliation_policy.default_policy("reconciliation.match_invoice")
    candidates = await _open_invoices(session, workspace_id, policy)
    if not candidates:
        return []

    applied: list[InvoiceAllocation] = []

    for transaction in transactions:
        if await _already_settles_something(session, transaction.id):
            continue

        expectations = [_as_expectation(inv) for inv in candidates]
        decision = evaluate(
            _as_movement(transaction),
            expectations,
            policy,
            withholding_ratios=reconciliation_policy.withholding_ratios(None),
        )
        allocation = await _apply(session, decision, transaction)
        if allocation is not None:
            applied.append(allocation)
            # Re-read on the next pass: this invoice's balance just
            # dropped, and the following transaction must see that rather
            # than settling the same debt twice.
            candidates = await _open_invoices(session, workspace_id, policy)

    return applied


async def match_for_invoice(
    session: AsyncSession, invoice: Invoice
) -> Optional[InvoiceAllocation]:
    """Look back at money that arrived before this invoice existed.

    The pay-then-invoice case: a client pays, and the nota follows days
    later. Nothing about the money changed, so nothing would ever
    re-examine it — this is the pass that does.

    **It is deliberately stricter than the forward direction, and only
    the known-client strategies run here.** The asymmetry is in the
    evidence, not in the code's convenience: when an invoice is already
    open and money of its exact value lands, the promise came first and
    the payment answers it. Backwards, the money already had a life of
    its own — it may have been a refund, a transfer, or another job — and
    an exact amount from a payer we cannot name is not enough to claim
    it. That case is a suggestion, and it waits for the queue.

    One allocation at most, and none at all if several movements fit.
    Picking the most recent of three identical payments would be
    inventing certainty exactly where the forward direction refuses to.
    """
    policy = reconciliation_policy.default_policy("reconciliation.match_invoice")
    wanted = set(policy.get("scope", {}).get("candidate_states", []))
    if invoice_service.derive_state(invoice, date.today()) not in wanted:
        return None

    policy["strategies"] = [
        strategy
        for strategy in policy["strategies"]
        if strategy.get("when", {}).get("counterparty") == "same_payee"
    ]
    if not invoice.payee_id or not policy["strategies"]:
        return None

    window_start = invoice.due_date - timedelta(days=LOOKBACK_DAYS)
    wanted_direction = "credit" if invoice.direction == "receivable" else "debit"

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.workspace_id == invoice.workspace_id,
            Transaction.payee_id == invoice.payee_id,
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
        decision = evaluate(
            _as_movement(transaction),
            expectation,
            policy,
            withholding_ratios=reconciliation_policy.withholding_ratios(None),
        )
        if decision.port == "linked":
            settles.append((transaction, decision))
            if len(settles) > 1:
                # Two payments answer this invoice equally well. Neither
                # is taken; a person decides which.
                return None

    if not settles:
        return None

    transaction, decision = settles[0]
    return await _apply(session, decision, transaction)
