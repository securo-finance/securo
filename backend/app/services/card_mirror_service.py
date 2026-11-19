"""Setting aside a card purchase the bank also reports on the account it left.

A card with immediate debit takes each purchase from its account on the
spot. Some banks still list that card as an account of its own, so one
connection returns every purchase twice: on the card and on the account.
Neither copy is a transfer (both are debits), and per-account
deduplication never sees them because they sit in different accounts.

This module finds those pairs and marks the card's copy ignored, which is
what people did by hand before. The rules it applies live in
`reconciliation_policy.MATCH_CARD_MIRROR`.

Each pair it makes is written to the reconciliation history as a
`linked` event (the card row against the account row it mirrors). That
event is the pair from then on: the account row it names is taken for
good, and the card row it names is never decided again, so a row a
person brings back stays back. Nothing is worked out again from whatever
rows happen to sit nearby on a later sync.
"""
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.reconciliation import ReconciliationEvent
from app.models.transaction import Transaction
from app.services import reconciliation_history_service, reconciliation_policy
from app.services.reconciliation_engine import Expectation, Movement, evaluate

NODE = reconciliation_policy.MATCH_CARD_MIRROR["node"]
STRATEGY = reconciliation_policy.MATCH_CARD_MIRROR["strategies"][0]["id"]

#: Kept in the connection's settings: rows a sync writes from this moment
#: on are candidates. Older history is left as it was, so turning this on
#: does not rewrite past reports; a recent row that could not be decided
#: when it arrived stays a candidate and is retried on later syncs.
SINCE_KEY = "card_mirror_since"


def _settles_on_the_spot(account: Account) -> bool:
    """A card that owes nothing once the sync has read it.

    A card with a bill carries the purchases until the bill is paid; a
    card debited immediately never does. A regular card that was just
    paid off also reads zero, so this alone is not enough (see
    `min_pairs` and the payment check in `ignore_mirrored_card_charges`).
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


def _utc(moment: datetime) -> datetime:
    # SQLite hands timestamps back without their zone; they are UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _since(connection: BankConnection, new_rows: Iterable[Transaction]) -> datetime:
    """When this connection's rows started being candidates, set once."""
    stored = (connection.settings or {}).get(SINCE_KEY)
    if stored:
        return datetime.fromisoformat(stored)
    stamps = [_utc(tx.created_at) for tx in new_rows if tx.created_at is not None]
    since = min(stamps) if stamps else datetime.now(timezone.utc)
    connection.settings = {**(connection.settings or {}), SINCE_KEY: since.isoformat()}
    return since


async def ignore_mirrored_card_charges(
    session: AsyncSession,
    connection: BankConnection,
    new_ids: Optional[list[uuid.UUID]],
    complete_account_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Ignore the card copy of purchases this connection reports twice.

    `complete_account_ids` are the accounts whose transaction feed came
    back whole in this run. A fresh balance is not enough: a card whose
    feed was throttled or cut short may be missing the payment that would
    show it is a regular card. Returns the ids of the card accounts that
    had a row ignored, so the caller can reconcile their opening balance.
    """
    new_ids = list(new_ids or [])
    policy = reconciliation_policy.default_policy(NODE)
    rules = policy["card"]
    # Nothing to do, now or later, for a connection without a card and an
    # account it could draw from.
    kinds = set(
        (
            await session.execute(
                select(Account.type).where(
                    Account.connection_id == connection.id,
                    Account.is_closed.is_(False),
                )
            )
        ).scalars()
    )
    if "credit_card" not in kinds or not kinds & set(rules["paid_from"]):
        return set()
    new_rows = (
        list(
            (
                await session.execute(
                    select(Transaction)
                    .where(Transaction.id.in_(new_ids))
                    .options(noload(Transaction.category_lines))
                )
            ).scalars()
        )
        if new_ids
        else []
    )
    # Set even when nothing can be compared yet, so the rows of this run
    # are retried once their accounts can be read.
    since = _since(connection, new_rows)
    if not complete_account_ids:
        return set()

    accounts = {
        account.id: account
        for account in (
            await session.execute(
                select(Account).where(
                    Account.connection_id == connection.id,
                    Account.id.in_(complete_account_ids),
                    Account.is_closed.is_(False),
                )
            )
        ).scalars()
    }
    cards = {aid for aid, acc in accounts.items() if _settles_on_the_spot(acc)}
    others = {aid for aid, acc in accounts.items() if acc.type in rules["paid_from"]}
    if not cards or not others:
        return set()

    window = max(
        max(int(s["when"]["date"]["before_days"]), int(s["when"]["date"]["after_days"]))
        for s in policy["strategies"]
        if s.get("enabled", True)
    )

    def comparable():
        return (
            select(Transaction)
            .where(
                Transaction.account_id.in_(cards | others),
                Transaction.source == "sync",
                Transaction.type == "debit",
                Transaction.transfer_pair_id.is_(None),
                Transaction.has_category_lines.is_(False),
            )
            .options(noload(Transaction.category_lines))
        )

    # What earlier runs paired: the card row is decided, the account row
    # is taken. Read from the history, never worked out again.
    pairs_made = (
        await session.execute(
            select(
                ReconciliationEvent.transaction_id,
                ReconciliationEvent.expectation_id,
                Transaction.account_id,
            )
            .join(Transaction, Transaction.id == ReconciliationEvent.transaction_id)
            .where(
                ReconciliationEvent.workspace_id == connection.workspace_id,
                ReconciliationEvent.action == "linked",
                ReconciliationEvent.strategy_id == STRATEGY,
                Transaction.account_id.in_(cards),
            )
        )
    ).all()
    decided = {card_tx_id for card_tx_id, _, _ in pairs_made}
    taken = {account_tx_id for _, account_tx_id, _ in pairs_made}
    evidence: dict = defaultdict(int)
    for _, _, card_id in pairs_made:
        evidence[card_id] += 1

    # The rows this run wrote, and recent rows earlier runs wrote but could
    # not decide (an account skipped, a feed cut short, a copy not in yet).
    # A copy lands within days, so a row older than `retry_days` that
    # still has no twin will not get one.
    retry_from = app_today() - timedelta(days=int(rules["retry_days"]))
    candidates = {
        tx.id: tx
        for tx in (
            await session.execute(
                comparable().where(
                    Transaction.is_ignored.is_(False),
                    or_(
                        Transaction.id.in_(new_ids),
                        and_(
                            Transaction.created_at >= since,
                            Transaction.date >= retry_from,
                        ),
                    ),
                )
            )
        ).scalars()
        if tx.id not in decided and tx.id not in taken
    }
    if not candidates:
        return set()

    # Every row a candidate could pair with, and every row those could
    # pair with in turn (each side must name the other).
    first = min(tx.date for tx in candidates.values()) - timedelta(days=2 * window)
    last = max(tx.date for tx in candidates.values()) + timedelta(days=2 * window)
    pool: list[Transaction] = []
    for tx in (
        await session.execute(
            comparable().where(Transaction.date >= first, Transaction.date <= last)
        )
    ).scalars():
        if tx.id in decided or tx.id in taken:
            continue
        # A card row a person ignored by hand still competes for its
        # account copy (it may well be that copy's twin); an ignored
        # account row is out of the money and pairs with nothing.
        if tx.is_ignored and tx.account_id not in cards:
            continue
        pool.append(tx)
    by_id = {tx.id: tx for tx in pool}

    index: dict = defaultdict(list)
    for tx in pool:
        index[tx.date].append(tx)

    def answer(tx: Transaction) -> Optional[uuid.UUID]:
        wanted = others if tx.account_id in cards else cards
        found = [
            _as_expectation(o, accounts[o.account_id])
            for offset in range(-window, window + 1)
            for o in index.get(tx.date + timedelta(days=offset), ())
            if o.account_id in wanted
            and o.amount == tx.amount
            and o.currency == tx.currency
        ]
        if not found:
            return None
        decision = evaluate(_as_movement(tx, accounts[tx.account_id]), found, policy)
        if decision.port != "linked" or decision.expectation is None:
            return None
        return decision.expectation.id

    # Payments the cards received, to tell a paid-off bill from a copy.
    payments: dict = defaultdict(list)
    for tx in (
        await session.execute(
            select(Transaction)
            .where(
                Transaction.account_id.in_(cards),
                Transaction.type == "credit",
                Transaction.is_ignored.is_(False),
                Transaction.date >= first,
            )
            .options(noload(Transaction.category_lines))
        )
    ).scalars():
        payments[tx.account_id].append(tx)
    reach = int(rules["payment_after_days"])

    def paid_with(card_id: uuid.UUID, account_tx: Transaction) -> bool:
        """The account's debit is a payment the card received, not a copy.

        A regular card paid off reads zero like an immediately debited
        one; when its bill held a single purchase, the payment leaving the
        account has that purchase's amount. The card shows the payment
        arriving, sometimes days after it left the account; a card debited
        on the spot has no bill to pay.
        """
        return any(
            p.currency == account_tx.currency
            and abs(Decimal(p.amount)) == abs(Decimal(account_tx.amount))
            and -window <= (p.date - account_tx.date).days <= reach
            for p in payments[card_id]
        )

    pairs: list[tuple[Transaction, Transaction]] = []
    for card_tx in pool:
        if card_tx.account_id not in cards or card_tx.is_ignored:
            continue
        other_id = answer(card_tx)
        if other_id is None:
            continue
        account_tx = by_id[other_id]
        # Both ends must agree: two card purchases of the same amount on
        # the same day leave the account's copy with two answers, and
        # then neither is set aside.
        if answer(account_tx) != card_tx.id:
            continue
        if paid_with(card_tx.account_id, account_tx):
            continue
        pairs.append((card_tx, account_tx))
        evidence[card_tx.account_id] += 1

    # One pair can be a coincidence: a regular card whose one purchase
    # was paid at once, with the payment missing from the card's feed. A
    # card debited on the spot mirrors every purchase, so it is treated
    # as one only after showing it more than once.
    touched: set[uuid.UUID] = set()
    for card_tx, account_tx in pairs:
        if card_tx.id not in candidates and account_tx.id not in candidates:
            continue
        if evidence[card_tx.account_id] < int(rules["min_pairs"]):
            continue
        card_tx.is_ignored = True
        await reconciliation_history_service.record(
            session,
            connection.workspace_id,
            "linked",
            expectation_kind="transaction",
            expectation_id=account_tx.id,
            transaction_id=card_tx.id,
            amount=abs(Decimal(card_tx.amount)),
            strategy_id=STRATEGY,
            detail={"node": NODE},
        )
        touched.add(card_tx.account_id)
    return touched
