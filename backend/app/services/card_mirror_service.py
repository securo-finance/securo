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
    card debited immediately never does. A regular card that was just
    paid off also reads zero, so this alone is not enough: a pair is also
    refused when the card received a payment of the same amount (see
    `ignore_mirrored_card_charges`).
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
    refreshed_account_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Ignore the card copy of purchases this connection reports twice.

    Like transfer detection, a pair forms only when at least one copy is
    in `candidate_ids` (the rows this sync wrote), and only when each copy
    names the other as its single answer. Only accounts in
    `refreshed_account_ids` (the ones whose balance this sync set) are
    read: a card the provider skipped still holds an old balance, which
    says nothing about it now. Returns the ids of the card accounts that
    had a row ignored, so the caller can reconcile their opening balance.
    """
    if not candidate_ids or not refreshed_account_ids:
        return set()
    new_ids = set(candidate_ids)

    accounts = {
        account.id: account
        for account in (
            await session.execute(
                select(Account).where(
                    Account.connection_id == connection_id,
                    Account.id.in_(refreshed_account_ids),
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

    rows = (
        await session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(cards | others),
                Transaction.source == "sync",
                Transaction.transfer_pair_id.is_(None),
                Transaction.has_category_lines.is_(False),
            )
            .options(noload(Transaction.category_lines))
        )
    ).scalars()
    pool: list[Transaction] = []
    payments: dict = defaultdict(list)
    for tx in rows:
        if tx.type == "credit":
            if tx.account_id in cards and not tx.is_ignored:
                payments[tx.account_id].append(tx)
        # A card copy already set aside keeps its claim on the account's
        # copy, so that one account debit never sets aside two purchases.
        # An ignored account row is out of the money, so it claims nothing.
        elif not tx.is_ignored or tx.account_id in cards:
            pool.append(tx)

    def by_date(rows: list[Transaction]) -> dict:
        index: dict = defaultdict(list)
        for tx in rows:
            index[tx.date].append(tx)
        return index

    everything = by_date(pool)
    # The rows earlier syncs had already written: what they paired was
    # decided then.
    earlier = by_date([tx for tx in pool if tx.id not in new_ids])

    def across(tx: Transaction, index: dict) -> list[Transaction]:
        """Same-amount debits on the other side (card against account)."""
        wanted = others if tx.account_id in cards else cards
        found = []
        for offset in range(-window, window + 1):
            for other in index.get(tx.date + timedelta(days=offset), ()):
                if other.account_id in wanted and other.amount == tx.amount:
                    found.append(other)
        return found

    def answer(tx: Transaction, index: dict = everything) -> Optional[uuid.UUID]:
        candidates = [
            _as_expectation(o, accounts[o.account_id]) for o in across(tx, index)
        ]
        if not candidates:
            return None
        decision = evaluate(
            _as_movement(tx, accounts[tx.account_id]), candidates, policy
        )
        if decision.port != "linked" or decision.expectation is None:
            return None
        return decision.expectation.id

    def paired(tx: Transaction, index: dict) -> bool:
        other_id = answer(tx, index)
        return other_id is not None and answer(by_id[other_id], index) == tx.id

    def paid_with(card_id: uuid.UUID, account_tx: Transaction) -> bool:
        """The account's debit is a payment the card received, not a copy.

        A regular card paid off reads zero like an immediately debited
        one; when its bill held a single purchase, the payment leaving the
        account has that purchase's amount. The card shows the payment
        arriving; a card debited on the spot has no bill to pay.
        """
        return any(
            abs(Decimal(p.amount)) == abs(Decimal(account_tx.amount))
            and abs((p.date - account_tx.date).days) <= window
            for p in payments[card_id]
        )

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
        account_tx = by_id[other_id]
        # Both ends must agree: two card purchases of the same amount on
        # the same day leave the account's copy with two answers, and
        # then neither is set aside.
        if answer(account_tx) != card_tx.id:
            continue
        # The account's copy is already taken by a card copy set aside
        # earlier, even when the new card row sits closer to it.
        if any(
            o.is_ignored and answer(o) == account_tx.id
            for o in across(account_tx, everything)
        ):
            continue
        # A card row an earlier sync already paired, and that is not
        # ignored, was brought back by the person: a closer copy arriving
        # now does not undo that. A card row that had no copy yet can
        # still be paired late.
        if card_tx.id not in new_ids and paired(card_tx, earlier):
            continue
        if paid_with(card_tx.account_id, account_tx):
            continue
        card_tx.is_ignored = True
        touched.add(card_tx.account_id)
    return touched
