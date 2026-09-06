"""The rules matching follows, as data rather than as code.

Two nodes ship: one for money arriving against an invoice, one for money
moving against a recurring bill. They are separate documents because
their scopes genuinely differ (an invoice has no account and is settled
N:N, a recurring bill is anchored to one account and settled once), and
because a workspace should be able to loosen one without touching the
other.

**Defaults ship with the image; a workspace stores only what it changed.**
Copying the defaults into every workspace at creation would freeze them:
a better default six months from now would never reach anyone who had
already opened the module. An untouched strategy keeps improving with the
product, and the UI renders shipped ∪ override.

Nothing here is surfaced yet. It is written as a document from the first
line because the direction for automations is a node graph, and a
threshold that starts life as a constant inside an engine has to be
excavated later; see `recurring_match_service`, whose numbers were
excellent and unreachable.
"""
from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

#: Bumped when the shape changes in a way a stored override must be
#: migrated against. Overrides record the version they were written for.
POLICY_VERSION = 1

#: Money moving against an invoice, in either direction.
#:
#: Named for the object rather than the direction: a payable is settled
#: by money going out and by the same strategies, and the engine already
#: refuses a candidate whose direction disagrees with the movement. A
#: node called `match_receivable` that also matched bills would be a name
#: that lies.
#:
#: The withholding strategy is a `link` rather than a `suggest`, and that
#: is the most consequential line in this file. A R$3.000 invoice paid by
#: a Brazilian company lands as R$2.955 or R$2.860,50: withholding is
#: 1.5–11%, not cents. Sending those to a confirmation queue would send
#: *the best clients* to the queue, and the accountant interview is
#: explicit that import-then-make-the-user-confirm is what killed
#: adoption of the incumbent.
MATCH_INVOICE: dict[str, Any] = {
    "version": POLICY_VERSION,
    "node": "reconciliation.match_invoice",
    "scope": {
        "movement": "any",
        "candidate_states": ["open", "partial", "overdue"],
        # A generated placeholder is a promise, not the money that keeps
        # it. Matching one against an invoice would settle a debt with
        # another debt.
        "ignore_transaction_sources": ["recurring"],
    },
    "strategies": [
        {
            "id": "same_client_exact",
            "enabled": True,
            "outcome": "link",
            # Runs at both moments: a known client paying the exact amount
            # is as convincing before the nota as after it, and the
            # pay-then-invoice case is ordinary here.
            "trigger": "both",
            "when": {
                "counterparty": "same_payee",
                "amount": {"match": "exact"},
                # `before_days` counts back from the **issue date**, not
                # the due date: a client who pays a deposit, or pays on
                # the promise of a nota that follows, is the ordinary
                # case here and not an anomaly to be queued.
                "date": {"before_days": 10, "after_days": 60},
                "currency": {"conversion": "reject"},
                "unique_candidate": True,
            },
        },
        {
            "id": "same_client_net_of_withholding",
            "enabled": True,
            "outcome": "link",
            "trigger": "both",
            "when": {
                "counterparty": "same_payee",
                # Ratios come from the jurisdiction pack, never from
                # here: shapes in this file, vocabulary in the pack.
                "amount": {
                    "match": "ratio",
                    "ratios": "@jurisdiction.withholding_ratios",
                    "epsilon": "0.02",
                    "difference_kind": "withholding_tax",
                },
                "date": {"before_days": 10, "after_days": 60},
                "currency": {"conversion": "reject"},
                "unique_candidate": True,
            },
        },
        {
            "id": "exact_amount_any_client",
            "enabled": True,
            "outcome": "link",
            # Only when money arrives. Backwards, an exact amount from a
            # payer we cannot name is not enough: that money already had a
            # life of its own (a refund, a transfer, another job), and
            # claiming it for a document written afterwards is a guess.
            "trigger": "money_arrives",
            "when": {
                "counterparty": "any",
                "amount": {"match": "exact"},
                "date": {"before_days": 3, "after_days": 15},
                "currency": {"conversion": "reject"},
                "unique_candidate": True,
            },
        },
        {
            "id": "same_client_several_invoices",
            "enabled": True,
            # A link, because the arithmetic either works out or it does
            # not: the engine only gets here when exactly one combination
            # of this client's open invoices adds up to what arrived. Two
            # combinations is a question, and the engine downgrades it on
            # its own.
            "outcome": "link",
            "trigger": "money_arrives",
            "when": {
                "counterparty": "same_payee",
                # The sum of several promises rather than any one of them.
                # `percent` at zero means the total must be exact: a
                # payout net of a fee needs a tolerance, and guessing
                # which fee applies is how a wrong split gets written
                # confidently. Somebody who knows their gateway's cut can
                # set it here.
                "amount": {"match": "set", "max_invoices": 6, "percent": "0"},
                "date": {"before_days": 10, "after_days": 60},
                "currency": {"conversion": "reject"},
            },
        },
        {
            "id": "same_client_part_payment",
            "enabled": True,
            # A suggestion, not a link. Money that covers part of an
            # invoice is genuinely ambiguous (an instalment, a client
            # paying what they had, or a different job entirely), and the
            # difference matters enough to ask. Promoting it to a link is
            # one click for somebody whose clients always pay in parts.
            "outcome": "suggest",
            "trigger": "both",
            "when": {
                "counterparty": "same_payee",
                # The mode that makes two transactions on one invoice
                # reachable at all. Every other mode compares against the
                # whole outstanding balance, so nothing could ever propose
                # the first half of a payment made in two.
                # Between a twentieth and nineteen twentieths of what is
                # owed. Below that it is noise; above it, the gap is a fee
                # or a withholding rather than an instalment, and the
                # tolerance rules are the ones written for that.
                "amount": {
                    "match": "partial",
                    "min_ratio": "0.05",
                    "max_ratio": "0.95",
                },
                "date": {"before_days": 10, "after_days": 60},
                "currency": {"conversion": "reject"},
                "unique_candidate": True,
            },
        },
        {
            "id": "similar_description",
            "enabled": True,
            "outcome": "suggest",
            "trigger": "money_arrives",
            "when": {
                "counterparty": "any",
                "amount": {"match": "tolerance", "percent": "2"},
                "date": {"before_days": 10, "after_days": 45},
                "description_similarity": {"min": "0.6"},
            },
        },
    ],
    "on_ambiguity": "suggest",
}

#: Money moving against a recurring bill or income.
#:
#: **These numbers are not new.** They are what
#: `recurring_match_service` has run in production since issue #116,
#: lifted out of the code unchanged: same account, same direction, exact
#: amount, 3 days before / 5 after (2/2 weekly), description similarity
#: at 0.6, and only the exact tier auto-links. Reproducing today's
#: behaviour exactly is the point: a personal workspace should notice
#: nothing on the day this lands, and gain the ability to change it on
#: the day the automations screen ships.
MATCH_RECURRING: dict[str, Any] = {
    "version": POLICY_VERSION,
    "node": "reconciliation.match_recurring",
    "scope": {
        "movement": "any",
        # The placeholder is what we are matching *to*, so unlike the
        # receivable node this one has nothing to ignore.
        "ignore_transaction_sources": [],
    },
    "strategies": [
        {
            "id": "same_account_exact",
            "enabled": True,
            "outcome": "link",
            "trigger": "money_arrives",
            "when": {
                "counterparty": "any",
                "same_account": True,
                "amount": {"match": "exact"},
                "date": {"before_days": 3, "after_days": 5},
                "currency": {"conversion": "reject"},
                "description_similarity": {"min": "0.6"},
                # Deliberately **not** `unique_candidate`, unlike the
                # invoice node. Two placeholders of the same value on one
                # account are told apart by description, and production
                # has always taken the better-matching one rather than
                # refusing both. Refusing here would be a stricter product
                # than the one running today, on the day this lands, for
                # people who never asked for reconciliation at all.
            },
        },
    ],
    "on_ambiguity": "suggest",
}

#: A real charge arriving where a generated placeholder is already
#: standing.
#:
#: Separate from `match_recurring` because its window genuinely differs:
#: five days either side, the same on both, regardless of how often the
#: bill repeats. That is not an oversight in the code this replaces: the
#: placeholder was written *for* a specific occurrence, so there is no
#: neighbouring occurrence to be confused with, and the window can afford
#: to be symmetric where the bill-level one cannot.
MATCH_PLACEHOLDER: dict[str, Any] = {
    "version": POLICY_VERSION,
    "node": "reconciliation.match_placeholder",
    "scope": {
        "movement": "any",
        "ignore_transaction_sources": [],
    },
    "strategies": [
        {
            "id": "placeholder_same_account_exact",
            "enabled": True,
            "outcome": "link",
            "trigger": "money_arrives",
            "when": {
                "counterparty": "any",
                "same_account": True,
                "amount": {"match": "exact"},
                "date": {"before_days": 5, "after_days": 5},
                "currency": {"conversion": "reject"},
                "description_similarity": {"min": "0.6"},
            },
        },
    ],
    "on_ambiguity": "suggest",
}

#: A weekly bill sits closer to its neighbours, so its window narrows or
#: a charge could match the wrong occurrence. Carried as an override on
#: the strategy rather than as a second document.
RECURRING_WINDOW_BY_FREQUENCY: dict[str, dict[str, int]] = {
    "weekly": {"before_days": 2, "after_days": 2},
}

_DEFAULTS: dict[str, dict[str, Any]] = {
    MATCH_INVOICE["node"]: MATCH_INVOICE,
    MATCH_RECURRING["node"]: MATCH_RECURRING,
    MATCH_PLACEHOLDER["node"]: MATCH_PLACEHOLDER,
}


def default_policy(node: str) -> dict[str, Any]:
    """The shipped document for one node.

    Deep-copied, because callers adjust it per movement (the recurring
    window narrows for a weekly bill), and a caller must never be able to
    edit the defaults for everyone else in the process.
    """
    try:
        return copy.deepcopy(_DEFAULTS[node])
    except KeyError:
        raise ValueError(f"Unknown reconciliation node '{node}'") from None


def for_recurring(frequency: str) -> dict[str, Any]:
    """The recurring node, with the window this frequency needs."""
    policy = default_policy(MATCH_RECURRING["node"])
    window = RECURRING_WINDOW_BY_FREQUENCY.get(frequency)
    if window:
        for strategy in policy["strategies"]:
            strategy["when"]["date"] = dict(window)
    return policy


def withholding_ratios(jurisdiction: str | None) -> list[Decimal]:
    """What fraction of an invoice a client may actually pay.

    Placeholder until the jurisdiction pack carries these: the ratio
    strategy is disabled in practice while this returns nothing, which is
    the honest state: a wrong ratio would auto-link a wrong amount, and
    that is worse than asking.
    """
    return []
