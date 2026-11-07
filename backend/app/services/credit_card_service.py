import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from app.core.app_clock import app_today


def _clamp_day(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _next_day_occurrence(target_day: int, reference: date) -> date:
    candidate = _clamp_day(reference.year, reference.month, target_day)
    if candidate >= reference:
        return candidate
    if reference.month == 12:
        return _clamp_day(reference.year + 1, 1, target_day)
    return _clamp_day(reference.year, reference.month + 1, target_day)


def get_cycle_dates(
    statement_close_day: Optional[int],
    payment_due_day: Optional[int],
    reference: Optional[date] = None,
) -> dict:
    """Return the close + due dates of the nearest upcoming billing cycle.

    Both dates are from the SAME cycle: the due is anchored on the nearest upcoming
    occurrence of payment_due_day, and the close is the most recent occurrence of
    statement_close_day on or before that due date. This guarantees close <= due."""
    if reference is None:
        reference = app_today()

    next_due = _next_day_occurrence(payment_due_day, reference) if payment_due_day else None

    next_close: Optional[date] = None
    if statement_close_day:
        if next_due:
            # Pick the close that belongs to next_due's cycle: try same month first;
            # if it lands after the due date, walk back one month (with end-of-month clamping).
            anchor = next_due
            candidate = _clamp_day(anchor.year, anchor.month, statement_close_day)
            if candidate > anchor:
                if anchor.month == 1:
                    candidate = _clamp_day(anchor.year - 1, 12, statement_close_day)
                else:
                    candidate = _clamp_day(anchor.year, anchor.month - 1, statement_close_day)
            next_close = candidate
        else:
            next_close = _next_day_occurrence(statement_close_day, reference)

    return {
        "next_close_date": next_close,
        "next_due_date": next_due,
    }


def compute_available_credit(
    credit_limit: Optional[Decimal],
    current_balance: Decimal,
) -> Optional[Decimal]:
    """available = limit − utilized. current_balance for a credit card is negative when debt."""
    if credit_limit is None:
        return None
    utilized = -current_balance if current_balance < 0 else Decimal("0")
    return credit_limit - utilized


def apply_effective_date(transaction, account, *, bill_due_date: Optional[date] = None) -> None:
    """Populate `transaction.effective_date` based on the account type.

    Resolution order (highest priority first):
    1. `transaction.effective_bill_date` — manual user override; whatever
       the user set wins, since they're hand-correcting a bucketing they
       disagree with (issue #92's LucasFidelis suggestion).
    2. `bill_due_date` — bank-truth from Pluggy /bills (passed in by the
       sync layer when a bill is linked).
    3. Cycle math from `account.statement_close_day` / `payment_due_day`.

    For non-CC accounts, effective_date is always equal to `date`.

    Call this from every tx create/update path (manual, sync, import,
    transfers, opening balances). `effective_date` is stored on every row
    regardless of the user's reporting mode; the mode only affects which
    date the aggregation queries read from."""
    override = getattr(transaction, "effective_bill_date", None)
    if override is not None:
        transaction.effective_date = override
        return
    if account is not None and getattr(account, "type", None) == "credit_card":
        if bill_due_date is not None:
            transaction.effective_date = bill_due_date
        else:
            transaction.effective_date = compute_effective_date(
                transaction.date,
                getattr(account, "statement_close_day", None),
                getattr(account, "payment_due_day", None),
            )
    else:
        transaction.effective_date = transaction.date


def compute_effective_date(
    tx_date: date,
    statement_close_day: Optional[int],
    payment_due_day: Optional[int],
) -> date:
    """Return the *cash-flow* date for a credit card transaction.

    In accrual reporting mode, a credit card purchase doesn't impact cash flow
    on the purchase date — it impacts cash flow when the bill is paid. This
    helper computes that "effective" date:

      1. Find the cycle the transaction belongs to: the next statement close
         date *strictly after* tx_date. Per Brazilian banking convention
         (Nubank, Itaú, etc.), a transaction ON the close day belongs to the
         next invoice, not the one closing that day.
      2. The bill for that cycle is due on the next occurrence of payment_due_day
         strictly after the close.
      3. Return that bill's due date.

    Returns tx_date as-is when either close_day or due_day is not configured
    (nothing we can do without the cycle metadata)."""
    if not statement_close_day or not payment_due_day:
        return tx_date

    # Step 1: find the cycle end — the first close day strictly after tx_date.
    same_month_close = _clamp_day(tx_date.year, tx_date.month, statement_close_day)
    if same_month_close > tx_date:
        cycle_end = same_month_close
    else:
        if tx_date.month == 12:
            cycle_end = _clamp_day(tx_date.year + 1, 1, statement_close_day)
        else:
            cycle_end = _clamp_day(tx_date.year, tx_date.month + 1, statement_close_day)

    # Step 2: find the bill due date — the first due day strictly after cycle_end.
    same_month_due = _clamp_day(cycle_end.year, cycle_end.month, payment_due_day)
    if same_month_due > cycle_end:
        return same_month_due
    if cycle_end.month == 12:
        return _clamp_day(cycle_end.year + 1, 1, payment_due_day)
    return _clamp_day(cycle_end.year, cycle_end.month + 1, payment_due_day)


def cc_cycle_bill_due_dates(
    window_start: Optional[date],
    window_end: Optional[date],
    statement_close_day: Optional[int],
    payment_due_day: Optional[int],
) -> Optional[dict]:
    """Due dates of a single bill cycle, when a window is exactly one.

    Returns None unless ``[window_start, window_end]`` is exactly one
    billing cycle of a credit card account with both cycle days set: the
    start is a clamped close day and the day after the end is the next
    clamped close day. Callers should keep their plain date-window
    behavior for any other window shape.

    When the window is a cycle, returns a dict with the due dates of the
    neighboring bills and a ``degenerate`` flag:

    - ``this``: the bill this cycle closes to.
    - ``prev``: the bill that closed right before the window.
    - ``next``: the bill the following cycle closes to.
    - ``degenerate``: True when this bill shares a due date with the
      previous or the next one (month-length clamping, e.g. close 29 /
      due 30 around February). A manual ``effective_bill_date`` then
      cannot name exactly one bill, so callers keep the historical window
      behavior.

    A manual ``effective_bill_date`` belongs to this bill when it falls in
    ``(prev, this]``: after the previous bill's due date, up to and
    including this bill's due date. Every date then maps to exactly one
    bill, so an override that is not an exact due date still shows up in
    one bill view and only one.

    Why this exists: a manual ``effective_bill_date`` names a bill by that
    bill's DUE date, but bucketing a transaction by the purchase window
    containing that date lands it on the wrong bill whenever the due date
    falls inside the NEXT cycle's window. With close 24 / due 18, the Aug
    bill is due 08-18, which sits inside the Sep cycle's 07-24 to 08-23
    purchase window, so the Aug bill shows empty and the Sep bill picks up
    its charges (issue #563).
    """
    if window_start is None or window_end is None:
        return None
    if not statement_close_day or not payment_due_day:
        return None
    if (window_end - window_start).days > 31:
        return None
    next_day = window_end + timedelta(days=1)
    days_next = calendar.monthrange(next_day.year, next_day.month)[1]
    if next_day.day != min(statement_close_day, days_next):
        return None
    days_start = calendar.monthrange(window_start.year, window_start.month)[1]
    if window_start.day != min(statement_close_day, days_start):
        return None
    d_this = compute_effective_date(window_start, statement_close_day, payment_due_day)
    d_prev = compute_effective_date(
        window_start - timedelta(days=1), statement_close_day, payment_due_day
    )
    d_next = compute_effective_date(
        window_end + timedelta(days=1), statement_close_day, payment_due_day
    )
    return {
        "this": d_this,
        "prev": d_prev,
        "next": d_next,
        "degenerate": d_this == d_prev or d_this == d_next,
    }
