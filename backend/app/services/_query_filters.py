"""Shared SQLAlchemy filter fragments for report/dashboard queries.

Centralizes the "what counts as real income/expense" definition so every
aggregation site agrees. Changes to the rule (e.g. adding a new exclusion
signal) only need to be made here.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, case, exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine


def is_confirmed():
    """SQL filter: the charge is settled rather than merely authorized.

    One of the two independent axes a transaction sits on. This one is about
    *confirmation*: a pending row is real money already committed, it just
    has not cleared yet. It says nothing about when the row is dated.
    """
    return Transaction.status == "posted"


def is_not_future(as_of: date):
    """SQL filter: the transaction has already happened by ``as_of``.

    The other axis, and a pure date question. A future-dated row is forecast
    no matter how confirmed it is; a past-dated row has happened no matter
    whether the bank has cleared it.
    """
    return Transaction.date <= as_of


def is_inside_provider_snapshot():
    """SQL filter: the provider's balance already accounts for this row.

    A connected account's current balance is the number the provider sends,
    not a sum of our rows, and providers net out the pending charges they
    report. A row typed by hand is ambiguous the same way, since the user is
    usually copying a charge the bank is already showing them.

    A recurring placeholder is the one case we can be sure about: we invented
    the row from a schedule, so no provider has ever seen it. Treating it as
    already counted makes it cancel itself out, leaving a charge that shows up
    in the forecast totals but moves no balance.
    """
    return Transaction.source != "recurring"


def counts_in_current_balance(as_of: date):
    """SQL filter: the row belongs in the balance labelled "current".

    Composed from the two axes above so the definition lives in one place and
    moving the line later is a change here rather than at every query site.

    Today the line sits at "confirmed and not future", with one exception:
    a credit card's balance is the debt owed, and an authorized purchase is
    already owed, so pending card rows stay in. Without that carve-out the
    card's balance understates the debt while its own bill total includes it.
    """
    return and_(
        is_not_future(as_of),
        or_(is_confirmed(), Account.type == "credit_card"),
    )


def reporting_date_col(accounting_mode: str):
    """The date column a transaction should be *bucketed by* in period
    aggregations (dashboard, reports, budgets).

    Honors the manual credit-card cycle override (`effective_bill_date`)
    FIRST — regardless of accounting mode — because that's the whole point
    of the override: the user hand-corrected which invoice a purchase
    belongs to (issue #92). When there's no override, fall back to
    `effective_date` in accrual mode or the raw purchase `date` in cash
    mode.

    This mirrors the ordering used by the transaction list and the credit
    card bill view, so a transaction lands in the same month everywhere the
    user looks. Aggregations that skipped the override summed credit-card
    spend under the purchase month instead of the invoice month (issue
    #232).
    """
    base = (
        Transaction.effective_date
        if accounting_mode == "accrual"
        else Transaction.date
    )
    return func.coalesce(Transaction.effective_bill_date, base)


def is_not_ignored():
    """SQL filter: the row is not one the user told us to disregard.

    Only the ignore signal, without the transfer/settlement family that
    `counts_as_pnl` folds in, because hiding rows from a *list* is a
    different question from leaving them out of a *total*: a transfer still
    belongs in the ledger the user is reading.

    Matches what the UI badges as ignored, which is the transaction flag or
    its category's — see `TransactionRead.reflect_ignored_category`. A list
    that hid one but not the other would leave visibly-ignored rows behind
    and look broken.
    """
    return and_(
        Transaction.is_ignored.is_(False),
        or_(
            Transaction.category_id.is_(None),
            Transaction.category_id.not_in(
                select(Category.id).where(Category.is_ignored.is_(True))
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Category parts
#
# A transaction split across categories keeps one row and its full amount;
# `transaction_category_lines` says how much of it belongs to each category.
# Anything that totals money *by category*, or that drops a category from a
# total (transfer-like and ignored categories), has to read those lines, or
# a split row would land as "uncategorized" or be counted whole.
#
# Aggregations do that with one LEFT JOIN: a plain row stays one row, a split
# row becomes one row per line. Every amount summed after the join must then
# be the line's share (`line_amount`, `line_amount_primary`), and every count
# must be `count(DISTINCT Transaction.id)`.
#
# Balances, bills, net worth and payee totals read the whole row and must
# never join the lines.
# ---------------------------------------------------------------------------

CATEGORY_LINE = aliased(TransactionCategoryLine, name="category_line")


def with_category_lines(stmt):
    """Join a statement over `transactions` to its category lines."""
    return stmt.outerjoin(CATEGORY_LINE, CATEGORY_LINE.transaction_id == Transaction.id)


def line_category_id():
    """The category of a joined part: the line's, or the row's own."""
    return func.coalesce(CATEGORY_LINE.category_id, Transaction.category_id)


def line_amount():
    """The joined part's amount, with the sign of its transaction."""
    return case(
        (CATEGORY_LINE.id.is_(None), Transaction.amount),
        (Transaction.amount < 0, -CATEGORY_LINE.amount),
        else_=CATEGORY_LINE.amount,
    )


def line_amount_primary():
    """The joined part's `amount_primary` (NULL when the row has none).

    Multiplying before dividing keeps the result exact whenever the part's
    share is (150 * 100 / 150 is 100, where 150 * (100 / 150) carries a
    rounded ratio into every total). The `* 1.0` keeps SQLite from dividing
    two integers.
    """
    return case(
        (CATEGORY_LINE.id.is_(None), Transaction.amount_primary),
        else_=(Transaction.amount_primary * CATEGORY_LINE.amount * literal_column("1.0"))
        / func.nullif(func.abs(Transaction.amount), 0),
    )


def line_primary_or_amount():
    """The joined part in primary currency, falling back to its own amount."""
    return func.coalesce(line_amount_primary(), line_amount())


def any_line_in(category_ids):
    """SQL filter: some category line of the row is in `category_ids`."""
    return exists().where(
        TransactionCategoryLine.transaction_id == Transaction.id,
        TransactionCategoryLine.category_id.in_(category_ids),
    )


def _transfer_category_ids():
    return select(Category.id).where(Category.treat_as_transfer.is_(True))


def _non_pnl_category_ids():
    return select(Category.id).where(
        or_(Category.treat_as_transfer.is_(True), Category.is_ignored.is_(True))
    )


def is_transfer(category_id):
    """SQL filter: the row (or part) is a transfer rather than income or expense.

    Either both legs were matched (`transfer_pair_id` set), or the category
    is flagged `treat_as_transfer` (one-sided movements such as an
    investment application). `category_id` is `line_category_id()` after
    `with_category_lines`, so a split row is judged part by part.
    """
    return or_(
        Transaction.transfer_pair_id.is_not(None),
        category_id.in_(_transfer_category_ids()),
    )


def is_not_transfer(category_id):
    """SQL filter: the negation of `is_transfer`, safe for uncategorized rows.

    `~is_transfer()` would also drop rows with no category: their
    `category_id IN (...)` is NULL, so the whole negation is NULL.
    """
    return and_(
        Transaction.transfer_pair_id.is_(None),
        or_(category_id.is_(None), category_id.not_in(_transfer_category_ids())),
    )


def is_transfer_row():
    """SQL filter for a transaction *list*: the row is a paired transfer, or
    its category or any of its category lines is transfer-like. A list shows
    whole rows, so it cannot join the lines."""
    return or_(
        is_transfer(Transaction.category_id),
        any_line_in(_transfer_category_ids()),
    )


def counts_as_pnl(category_id):
    """SQL filter: True when a transaction (or one part of a split
    transaction) should contribute to income/expense totals.

    `category_id` is required on purpose: pass `line_category_id()` after
    `with_category_lines`, so a split row's transfer-like or ignored parts
    drop out alone. Passing `Transaction.category_id` is only right where
    the rows cannot be split (group-split shares, for instance).

    Excludes:
      - paired transfers (both legs were matched; already cancel out),
      - parts in categories flagged `treat_as_transfer` (one-sided
        movements like investment applications where the counterpart is
        an Asset/Holding, not another Account),
      - transactions flagged `is_ignored=True` (user-marked as not to be reported),
      - transactions flagged `exclude_from_pnl=True` (kept in balance,
        omitted from income and expense calculations),
      - parts in categories flagged `is_ignored=True` (user-marked as not to be reported).

    Does NOT exclude `source='opening_balance'` — callers that already
    filter those keep doing so; this helper only handles the transfer-like
    exclusion family so both rules stay visible at each call site.
    """
    return and_(
        Transaction.transfer_pair_id.is_(None),
        Transaction.is_ignored.is_(False),
        Transaction.exclude_from_pnl.is_(False),
        # Settlement *debits* are repayments of debts that were already
        # booked as an expense via the share. Counting them would
        # double-count. Settlement *credits*, however, represent the
        # receiver actually getting the cash back — they offset the
        # over-recorded expense from when the receiver paid the full
        # parent transaction. So we keep credits, drop debits.
        ~and_(Transaction.source == "settlement", Transaction.type == "debit"),
        or_(category_id.is_(None), category_id.not_in(_non_pnl_category_ids())),
    )


def user_pnl_parts(tx: Transaction) -> list[tuple[Optional[uuid.UUID], Optional[Category], Decimal]]:
    """Python twin of `counts_as_user_pnl` for loops over loaded rows.

    Returns the parts of `tx` that count toward the user's P/L as
    `(category_id, category, ratio)`, where `ratio` is the part's share of
    the transaction: one entry with ratio 1 for a plain row, one per counted
    line for a split row, nothing when the row does not count. The row must
    have `category` loaded (lines load their category themselves).
    """
    if (
        tx.transfer_pair_id
        or tx.is_ignored
        or tx.exclude_from_pnl
        or tx.source == "settlement"
    ):
        return []

    def counts(category: Optional[Category]) -> bool:
        return not (category and (category.treat_as_transfer or category.is_ignored))

    if not tx.has_category_lines or not tx.category_lines:
        return [(tx.category_id, tx.category, Decimal("1"))] if counts(tx.category) else []
    total = abs(Decimal(str(tx.amount)))
    if not total:
        return []
    return [
        (line.category_id, line.category, Decimal(str(line.amount)) / total)
        for line in tx.category_lines
        if counts(line.category)
    ]


def user_pnl_ratio(tx: Transaction) -> Decimal:
    """Share of `tx` that counts toward the user's P/L (0 to 1)."""
    return sum((ratio for _, _, ratio in user_pnl_parts(tx)), Decimal("0"))


def counts_on_bill(net_payments: bool = False):
    """SQL filter: True when a transaction belongs on a credit-card bill.

    A bill total is an *amount owed*, not a reporting figure, and the two
    answer to different authorities: the bill has to match what the bank
    says you owe, while P/L answers to how the user chose to categorize
    their spending. So the card's cycle total cannot reuse
    `counts_as_pnl`. Every judgment that helper makes about what counts
    as *spending* is a judgment the bank never made.

    Kept out, because they are genuinely not charges on this bill:
      - paired transfers, by default (the bill *payment* is not a
        purchase). With ``net_payments=True`` the *credit* leg of a
        paired transfer counts instead: an intermediate payment into the
        card shrinks the amount owed, the way a refund does. Callers
        net only what is left after the payment settles any debt that
        was already outstanding (banks apply repayments oldest first),
        see `get_account_summary`.
      - settlement debits (a repayment of a share already booked),
      - rows the user flagged `is_ignored`, on the transaction or its
        category. Those leave the account balance too, so dropping them
        from the bill keeps the card's two numbers telling one story.

    `treat_as_transfer` categories are handled asymmetrically, and this is
    the whole point of the helper:
      - kept in for *debits*: buying an investment or paying a consortium
        installment with the card still lands on the statement, so a
        charge doesn't stop being owed to the bank because of how it was
        tagged afterwards (issue #647).
      - dropped for *credits*: an unpaired card payment (the payer's
        account isn't connected, the amount doesn't match exactly, or it
        was a partial payment) is normally filed under a transfer-like
        category, and letting it through here would net it against new
        debt instead of being a repayment of it.

    Neither reading is exactly right — there's no field today that tells
    a genuine merchant refund apart from an unpaired bill payment once
    both land as a credit in a transfer-like category, so this is a
    judgment call, not a derived fact. Dropping transfer-tagged credits
    errs toward the more common case (an unmatched payment silently
    shrinking the bill every cycle) over the rarer one (a refund of a
    transfer-tagged purchase failing to shrink it back).

    Deliberately spelled out rather than defined as "`counts_as_pnl`
    minus a clause": a filter for what a *report* excludes will keep
    growing as the product learns new ways to say "don't count this",
    and a bill total must not inherit those. Every clause here is one
    somebody chose for the bill.
    """
    ignored_category = Transaction.category_id.in_(
        select(Category.id).where(Category.is_ignored.is_(True))
    )
    transfer_category = Transaction.category_id.in_(
        select(Category.id).where(Category.treat_as_transfer.is_(True))
    )
    if net_payments:
        # The credit leg of a paired transfer is a payment by
        # construction: the pair proves the money moved between the
        # user's own accounts, so the pair itself and a transfer-like
        # category tag on it cannot keep it off the bill.
        paired_payment = and_(
            Transaction.type == "credit",
            Transaction.transfer_pair_id.is_not(None),
        )
        transfer_clause = or_(
            Transaction.transfer_pair_id.is_(None), paired_payment,
        )
        category_clause = or_(
            Transaction.type == "debit",
            Transaction.category_id.is_(None),
            ~transfer_category,
            paired_payment,
        )
    else:
        transfer_clause = Transaction.transfer_pair_id.is_(None)
        category_clause = or_(
            Transaction.type == "debit",
            Transaction.category_id.is_(None),
            ~transfer_category,
        )
    return and_(
        transfer_clause,
        Transaction.is_ignored.is_(False),
        ~and_(Transaction.source == "settlement", Transaction.type == "debit"),
        or_(Transaction.category_id.is_(None), ~ignored_category),
        category_clause,
    )


def counts_as_user_pnl(category_id):
    """SQL filter for *user-level* P/L (dashboard, reports, budgets).

    Stricter than `counts_as_pnl`: also drops settlement *credits*. Under
    the share-only model an owner's expense for a split tx is just their
    share, so the corresponding settlement credits would double-count.
    Per-account stats still use `counts_as_pnl` because account ledgers
    track real cash through the account, not user P/L.
    """
    return and_(
        counts_as_pnl(category_id),
        Transaction.source != "settlement",
    )


def counts_as_user_pnl_row():
    """SQL filter for a transaction *list*: the row counts toward the user's
    P/L, judging a split row by its parts (it counts when any part does).
    A list shows whole rows, so it cannot join the lines."""
    return and_(
        counts_as_user_pnl(Transaction.category_id),
        or_(
            Transaction.has_category_lines.is_(False),
            exists().where(
                TransactionCategoryLine.transaction_id == Transaction.id,
                TransactionCategoryLine.category_id.not_in(_non_pnl_category_ids()),
            ),
        ),
    )


async def owner_split_offset_pnl(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
    workspace_id: Optional[uuid.UUID] = None,
) -> tuple[float, float]:
    """Return (income_offset, expense_offset) — the totals to *subtract*
    from the owner's full-amount aggregations so only their own share
    remains. We sum every split share that belongs to a non-owner
    member; subtracting that from the parent's full amount leaves the
    owner's share."""
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    # The user's own member entries: linked_user_id matches OR is_self=true
    # in a group they own. The first form covers groups they're invited to;
    # the second covers their own groups (where is_self=true marks the owner).
    viewer_member_ids = (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(
            or_(
                GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self == True, Group.user_id == user_id),
            )
        )
    )
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if use_effective_date else Transaction.date,
    )

    result = await session.execute(
        select(
            Transaction.currency,
            func.sum(
                case(
                    (Transaction.type == "credit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (Transaction.type == "debit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            *(
                [Transaction.workspace_id == workspace_id]
                if workspace_id is not None
                else []
            ),
            TransactionSplit.group_member_id.notin_(viewer_member_ids),
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            date_col <= app_today(),
            Transaction.status == "posted",
            counts_as_user_pnl(Transaction.category_id),
        )
        .group_by(Transaction.currency)
    )

    if primary_currency is None:
        income_total = 0.0
        expense_total = 0.0
        for row in result.all():
            income_total += float(row[1] or 0)
            expense_total += float(row[2] or 0)
        return income_total, expense_total

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    income_total = 0.0
    expense_total = 0.0
    for row in result.all():
        cur, inc_raw, exp_raw = row[0], row[1] or 0, row[2] or 0
        if inc_raw:
            inc_pri, _ = await _convert(session, _Decimal(str(inc_raw)), cur, primary_currency)
            income_total += float(inc_pri)
        if exp_raw:
            exp_pri, _ = await _convert(session, _Decimal(str(exp_raw)), cur, primary_currency)
            expense_total += float(exp_pri)
    return income_total, expense_total


async def owner_split_offset_by_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
    workspace_id: Optional[uuid.UUID] = None,
) -> dict:
    """Per-category, sum of non-owner shares on owner-side debit splits —
    subtract from full owner debits to get the owner's category share."""
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    viewer_member_ids = (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(
            or_(
                GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self == True, Group.user_id == user_id),
            )
        )
    )
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if use_effective_date else Transaction.date,
    )

    result = await session.execute(
        select(
            Transaction.category_id,
            Transaction.currency,
            func.sum(TransactionSplit.share_amount),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            *(
                [Transaction.workspace_id == workspace_id]
                if workspace_id is not None
                else []
            ),
            Transaction.type == "debit",
            TransactionSplit.group_member_id.notin_(viewer_member_ids),
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            date_col <= app_today(),
            Transaction.status == "posted",
            counts_as_user_pnl(Transaction.category_id),
        )
        .group_by(Transaction.category_id, Transaction.currency)
    )

    out: dict = {}
    if primary_currency is None:
        for cat_id, _cur, total in result.all():
            out[cat_id] = out.get(cat_id, 0.0) + float(total or 0)
        return out

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    for cat_id, cur, total in result.all():
        if not total:
            continue
        converted, _ = await _convert(session, _Decimal(str(total)), cur, primary_currency)
        out[cat_id] = out.get(cat_id, 0.0) + float(converted)
    return out


async def forecast_owner_share_fractions(
    session: AsyncSession,
    user_id: uuid.UUID,
    transactions: Iterable[Transaction],
) -> dict[uuid.UUID, Decimal]:
    """Map the user's own split forecast rows to the fraction that is their cost.

    The `owner_split_offset_*` helpers only see posted rows dated up to today.
    Pending and future-dated rows reach the projections one by one through
    `_get_forecast_transactions`, so the share-only rule is applied per row
    instead. The fraction comes from the split amounts in the row's own
    currency, so multiplying it into `amount_primary` keeps the conversion
    already stored on the row and needs no FX lookup.

    Rows without non-owner shares are left out; callers treat a missing id
    as 1.
    """
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    own_rows = {tx.id: tx for tx in transactions if tx.user_id == user_id}
    if not own_rows:
        return {}

    viewer_member_ids = (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(
            or_(
                GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self == True, Group.user_id == user_id),
            )
        )
    )
    result = await session.execute(
        select(TransactionSplit.transaction_id, func.sum(TransactionSplit.share_amount))
        .where(
            TransactionSplit.transaction_id.in_(list(own_rows)),
            TransactionSplit.group_member_id.notin_(viewer_member_ids),
        )
        .group_by(TransactionSplit.transaction_id)
    )

    fractions: dict[uuid.UUID, Decimal] = {}
    for tx_id, others in result.all():
        full = abs(Decimal(str(own_rows[tx_id].amount)))
        if not full:
            continue
        # Signed and unclamped, like the posted offset: an exact split may
        # give a member a negative share, and the owner's share then exceeds
        # the row.
        fractions[tx_id] = 1 - Decimal(str(others or 0)) / full
    return fractions


async def viewer_shared_pnl(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> tuple[float, float]:
    """Return (income, expense) totals contributed by transactions the
    viewer doesn't own but participates in via a group split.

    Concert tickets paid by a friend show up as the viewer's share in
    their own spending picture — without inflating account balances.

    When `primary_currency` is given, mixed-currency shares are
    converted to that currency via the FX service. Otherwise, sums
    are taken in raw nominal terms (only safe when all shares are
    same-currency, e.g., during single-currency tests).
    """
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    # Cross-workspace Splitwise projection: include only invitations
    # (linked_user_id matches but is_self is False). Self-memberships
    # represent the user in their own group and are already counted via
    # the workspace-scoped Transaction filter at the caller.
    member_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id,
        GroupMember.is_self.is_(False),
    )
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if use_effective_date else Transaction.date,
    )

    result = await session.execute(
        select(
            Transaction.currency,
            func.sum(
                case(
                    (Transaction.type == "credit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (Transaction.type == "debit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            TransactionSplit.group_member_id.in_(member_ids),
            # Avoid double-counting if the viewer also owns the parent.
            Transaction.user_id != user_id,
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            date_col <= app_today(),
            Transaction.status == "posted",
            counts_as_pnl(Transaction.category_id),
        )
        .group_by(Transaction.currency)
    )

    if primary_currency is None:
        # Backward-compatible nominal sum (caller is responsible for
        # currency homogeneity). Acceptable when only one currency is
        # in play.
        income_total = 0.0
        expense_total = 0.0
        for row in result.all():
            income_total += float(row[1] or 0)
            expense_total += float(row[2] or 0)
        return income_total, expense_total

    # FX-aware: convert each currency bucket to primary.
    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    income_total = 0.0
    expense_total = 0.0
    for row in result.all():
        cur, inc_raw, exp_raw = row[0], row[1] or 0, row[2] or 0
        if inc_raw:
            inc_pri, _ = await _convert(session, _Decimal(str(inc_raw)), cur, primary_currency)
            income_total += float(inc_pri)
        if exp_raw:
            exp_pri, _ = await _convert(session, _Decimal(str(exp_raw)), cur, primary_currency)
            expense_total += float(exp_pri)
    return income_total, expense_total


async def viewer_shared_spending_by_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> dict:
    """Return {category_id (uuid|None): total_share_expense_float} for
    transactions where the viewer participates via a group split.

    With `primary_currency`, totals are FX-converted; otherwise raw
    nominal sums (single-currency only)."""
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    # Cross-workspace Splitwise projection: include only invitations
    # (linked_user_id matches but is_self is False). Self-memberships
    # represent the user in their own group and are already counted via
    # the workspace-scoped Transaction filter at the caller.
    member_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id,
        GroupMember.is_self.is_(False),
    )
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if use_effective_date else Transaction.date,
    )

    result = await session.execute(
        select(
            Transaction.category_id,
            Transaction.currency,
            func.sum(TransactionSplit.share_amount),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            TransactionSplit.group_member_id.in_(member_ids),
            Transaction.user_id != user_id,
            Transaction.type == "debit",
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            date_col <= app_today(),
            Transaction.status == "posted",
            counts_as_pnl(Transaction.category_id),
        )
        .group_by(Transaction.category_id, Transaction.currency)
    )

    out: dict = {}
    if primary_currency is None:
        for cat_id, _cur, total in result.all():
            out[cat_id] = out.get(cat_id, 0.0) + float(total or 0)
        return out

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    for cat_id, cur, total in result.all():
        if not total:
            continue
        converted, _ = await _convert(session, _Decimal(str(total)), cur, primary_currency)
        out[cat_id] = out.get(cat_id, 0.0) + float(converted)
    return out
