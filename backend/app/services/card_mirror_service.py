"""Setting aside a card purchase the bank also reports on the account it left.

A card with immediate debit takes each purchase from its account on the
spot. Some banks still list that card as an account of its own, so one
connection returns every purchase twice: on the card and on the account.
Neither copy is a transfer (both are debits), and per-account
deduplication never sees them because they sit in different accounts.

This module finds those pairs among the rows a sync just wrote and marks
the card's copy ignored, which is what people did by hand before. The
rules it applies live in `reconciliation_policy.MATCH_CARD_MIRROR`.
"""
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.models.account import Account
from app.models.transaction import Transaction
from app.services import reconciliation_policy
from app.services.reconciliation_engine import Expectation, Movement, evaluate

NODE = reconciliation_policy.MATCH_CARD_MIRROR["node"]


def _settles_on_the_spot(account: Account) -> bool:
    """A card that owes nothing once the sync has set its balance.

    A card with a bill carries the purchases until the bill is paid; a
    card debited immediately never does. A card that was just paid off
    and has not been used since also reads zero, but then it has no new
    purchase to compare either.
    """
    return account.type == "credit_card" and Decimal(account.balance or 0) == 0


def _as_movement(tx: Transaction, account: Account) -> Movement:
    return Movement(
        amount=abs(Decimal(tx.amount)),
        currency=tx.currency,
        direction=tx.type,
        when=tx.date,
        description=tx.description,
        account_id=tx.account_id,
        account_name=account.name,
        account_type=account.type,
        source=tx.source,
    )


def _as_expectation(tx: Transaction, account: Account) -> Expectation:
    """The other copy, in the same direction: both legs are the purchase."""
    return Expectation(
        kind="transaction",
        id=tx.id,
        amount=abs(Decimal(tx.amount)),
        currency=tx.currency,
        direction=tx.type,
        when=tx.date,
        description=tx.description,
        account_id=tx.account_id,
        account_name=account.name,
        account_type=account.type,
    )


async def ignore_mirrored_card_charges(
    session: AsyncSession,
    connection_id: uuid.UUID,
    candidate_ids: Optional[list[uuid.UUID]],
) -> set[uuid.UUID]:
    """Ignore the card copy of purchases this connection reports twice.

    Like transfer detection, a pair forms only when at least one copy is
    in `candidate_ids` (the rows this sync wrote), and only when each copy
    names the other as its single answer. Returns the ids of the card
    accounts that had a row ignored, so the caller can reconcile their
    opening balance.
    """
    if not candidate_ids:
        return set()
    new_ids = set(candidate_ids)

    accounts = {
        account.id: account
        for account in (
            await session.execute(
                select(Account).where(
                    Account.connection_id == connection_id,
                    Account.is_closed.is_(False),
                )
            )
        ).scalars()
    }
    cards = {aid for aid, acc in accounts.items() if _settles_on_the_spot(acc)}
    others = {aid for aid, acc in accounts.items() if acc.type != "credit_card"}
    if not cards or not others:
        return set()

    policy = reconciliation_policy.default_policy(NODE)
    window = max(
        max(int(s["when"]["date"]["before_days"]), int(s["when"]["date"]["after_days"]))
        for s in policy["strategies"]
        if s.get("enabled", True)
    )

    pool = list(
        (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.account_id.in_(cards | others),
                    Transaction.source == "sync",
                    Transaction.type == "debit",
                    Transaction.is_ignored.is_(False),
                    Transaction.transfer_pair_id.is_(None),
                    Transaction.has_category_lines.is_(False),
                )
                .options(noload(Transaction.category_lines))
            )
        ).scalars()
    )

    by_date: dict = defaultdict(list)
    for tx in pool:
        by_date[tx.date].append(tx)

    def across(tx: Transaction) -> list[Transaction]:
        """Same-amount debits on the other side (card against account)."""
        wanted = others if tx.account_id in cards else cards
        found = []
        for offset in range(-window, window + 1):
            for other in by_date.get(tx.date + timedelta(days=offset), ()):
                if other.account_id in wanted and other.amount == tx.amount:
                    found.append(other)
        return found

    def answer(tx: Transaction) -> Optional[uuid.UUID]:
        candidates = [_as_expectation(o, accounts[o.account_id]) for o in across(tx)]
        if not candidates:
            return None
        decision = evaluate(
            _as_movement(tx, accounts[tx.account_id]), candidates, policy
        )
        if decision.port != "linked" or decision.expectation is None:
            return None
        return decision.expectation.id

    by_id = {tx.id: tx for tx in pool}
    touched: set[uuid.UUID] = set()
    for card_tx in pool:
        if card_tx.account_id not in cards or card_tx.is_ignored:
            continue
        other_id = answer(card_tx)
        if other_id is None:
            continue
        if card_tx.id not in new_ids and other_id not in new_ids:
            continue
        # Both ends must agree: two card purchases of the same amount on
        # the same day leave the account's copy with two answers, and
        # then neither is set aside.
        if answer(by_id[other_id]) != card_tx.id:
            continue
        card_tx.is_ignored = True
        touched.add(card_tx.account_id)
    return touched
