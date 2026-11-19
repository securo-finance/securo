"""Demo Bank: a made-up bank, to show how a provider plugin works.

Securo calls `plugin.sync(params, context)` in the plugin runner on the
schedule the manifest sets (`sync_minutes`) and when someone presses "sync
now". A provider plugin answers with the records it fetched; Securo checks
them and writes them as it does for any connected bank:

    {"state": {...}, "data": {"contract": 1, "accounts": [...], "holdings": [...]}}

This bank reaches nothing: every record is made up from the date, so the same
day always gives the same records. A real provider fetches them from its
service instead, through the hosts its manifest declares in `network`.

Only the standard library is available unless the plugin ships a lockfile.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

#: How far back the first sync reads.
HISTORY_DAYS = 30
CURRENCY = "USD"
CARD_CLOSE_DAY = 5
CARD_DUE_DAY = 15


class NeedsAuth(Exception):
    """The sign-in is wrong or expired. Securo marks the connection and asks the person to reconnect.

    Securo goes by the exception's class name, so any exception called
    `NeedsAuth` (or `AuthError`) does.
    """


class RateLimited(Exception):
    """The service asked us to slow down. Not a failure: Securo tries again on the next run."""


def _amount(value: Decimal) -> str:
    # Amounts travel as strings: a float would lose cents on the way.
    return str(value.quantize(Decimal("0.01")))


def _checking_lines(day: dt.date) -> list[tuple[str, str, Decimal, str]]:
    """What happened on the checking account that day: (slug, description, amount, type)."""
    lines = []
    if day.day == 1:
        lines.append(("salary", "Salary", Decimal("3200.00"), "credit"))
    if day.day == 5:
        lines.append(("rent", "Rent", Decimal("1200.00"), "debit"))
    if day.toordinal() % 3 == 0:
        cents = 1500 + day.toordinal() * 37 % 4000
        lines.append(("market", "Corner Market", Decimal(cents) / 100, "debit"))
    if day.toordinal() % 7 == 0:
        lines.append(("transit", "Transit card top-up", Decimal("20.00"), "debit"))
    return lines


def _card_lines(day: dt.date) -> list[tuple[str, str, Decimal, str]]:
    """What happened on the credit card that day."""
    lines = []
    if day.toordinal() % 2 == 0:
        lines.append(("coffee", "Coffee Shop", Decimal("4.50"), "debit"))
    if day.day == 10:
        lines.append(("streaming", "Streaming subscription", Decimal("15.99"), "debit"))
    return lines


def _transactions(prefix: str, lines_for, days: list[dt.date], today: dt.date, since: dt.date):
    """The account's transactions from `since` on, and what the whole history adds up to."""
    records, total = [], Decimal(0)
    for day in days:
        for slug, description, amount, kind in lines_for(day):
            total += amount if kind == "credit" else -amount
            if day < since:
                continue
            records.append({
                # Stable across syncs: the same record must keep the same id,
                # or Securo can't tell it already has it.
                "external_id": f"{prefix}-{day.isoformat()}-{slug}",
                "description": description,
                "payee": description,
                "amount": _amount(amount),
                "type": kind,
                "date": day.isoformat(),
                # Today's are still settling; the next sync reports them posted.
                "status": "pending" if day == today else "posted",
            })
    return records, total


def _next_due(today: dt.date) -> dt.date:
    due = today.replace(day=CARD_DUE_DAY)
    if due < today:
        due = (due.replace(day=1) + dt.timedelta(days=32)).replace(day=CARD_DUE_DAY)
    return due


def _holdings(today: dt.date) -> list[dict]:
    # The trades add up to the quantity, so Securo keeps them as the ledger.
    return [{
        "external_id": "fund-world",
        "name": "Demo World Index Fund",
        "type": "fund",
        "currency": CURRENCY,
        "quantity": "12",
        "unit_price": "107.50",
        "current_value": "1290.00",
        "account_external_id": "demo-checking",
        "trades": [
            {"external_id": "fund-world-buy-1", "kind": "buy", "date": (today - dt.timedelta(days=25)).isoformat(),
             "quantity": "10", "price": "100.00"},
            {"external_id": "fund-world-buy-2", "kind": "buy", "date": (today - dt.timedelta(days=10)).isoformat(),
             "quantity": "2", "price": "105.00", "fee": "1.00"},
        ],
    }]


class Plugin:
    def sync(self, params: dict, context) -> dict:
        """One sync.

        `params["since"]` is the first day Securo wants again (None on the
        first sync), `params["state"]` is what the last sync returned as
        `state`. `context.secrets` and `context.config` hold the connection
        form's values: secret fields in `secrets`, sealed by Securo, the rest
        in `config`.
        """
        code = context.secrets.get("access_code", "")
        if code == "expired":
            raise NeedsAuth("the access code expired; reconnect with a new one")
        if code == "busy":
            raise RateLimited("Demo Bank is busy; trying again later")

        today = dt.date.today()
        first = today - dt.timedelta(days=HISTORY_DAYS)
        since = max(dt.date.fromisoformat(params["since"]), first) if params.get("since") else first
        days = [first + dt.timedelta(days=n) for n in range(HISTORY_DAYS + 1)]
        owner = context.config.get("customer") or "Demo"

        checking, checking_total = _transactions("chk", _checking_lines, days, today, since)
        card, card_total = _transactions("card", _card_lines, days, today, since)
        card_debt = -card_total
        due = _next_due(today)

        accounts = [
            {
                "external_id": "demo-checking",
                "name": f"{owner} Checking",
                "type": "checking",
                "currency": CURRENCY,
                # What the bank says the account holds now, whatever window
                # the transactions cover.
                "balance": _amount(Decimal("1000.00") + checking_total),
                "institution_name": "Demo Bank",
                "masked_number": "0001",
                "transactions": checking,
            },
            {
                "external_id": "demo-card",
                "name": f"{owner} Card",
                "type": "credit_card",
                "currency": CURRENCY,
                # A card's balance is what is owed, as a positive number.
                "balance": _amount(card_debt),
                "credit_limit": "5000.00",
                "statement_close_day": CARD_CLOSE_DAY,
                "payment_due_day": CARD_DUE_DAY,
                "institution_name": "Demo Bank",
                "masked_number": "4242",
                "transactions": card,
                "bills": [{
                    "external_id": f"bill-{due.isoformat()[:7]}",
                    "due_date": due.isoformat(),
                    "total_amount": _amount(card_debt),
                    "minimum_payment": _amount(card_debt / 10),
                    "currency": CURRENCY,
                }],
            },
        ]
        return {
            # Kept by Securo once the records are saved, and handed back next time.
            "state": {"last_sync": today.isoformat()},
            "data": {"contract": 1, "accounts": accounts, "holdings": _holdings(today)},
        }


plugin = Plugin()
