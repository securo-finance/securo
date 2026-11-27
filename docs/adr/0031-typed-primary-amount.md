# 0031. A converted amount the user typed is kept apart from the display-currency cache

- Status: Accepted
- Date: 2026-11-19
- Origin: #1561 (restamp of stored primary amounts on a display currency change)
- Extends: [0027](0027-amount-in-account-currency.md)
- Extended by [0039](0039-date-move-keeps-typed-stamp.md): a move of the row's own date keeps the typed amount; a transfer leg's date and a pending row posted to today still drop it (#1816).

## Context

`amount_primary` is a cache of a row's value in its owner's display currency (ADR 0008), and `restamp_user_primary_amounts` rewrites it when that currency changes. The transaction dialog also lets the user type the converted amount of a foreign row: a USD 300 hotel paid as R$ 1,560.

ADR 0027 keeps that figure for a row in another currency than its account (`amount_account`), so a USD purchase on a BRL card comes back as typed. A USD row on a USD account has no such copy: the typed R$ 1,560 lived only in `amount_primary`, and a round trip BRL → USD → BRL replaced it with a market conversion (R$ 1,500), moving every expense total that reads it.

## Decision

- `transactions.amount_primary_typed` (`Numeric(15, 2)`) and `amount_primary_typed_currency` (`String(3)`) keep the converted amount the user typed and the display currency it was typed in. They are written only when a create, an installment series or an edit carries `amount_primary` or `fx_rate_used` for a row in another currency than the owner's display currency (`_keep_typed_primary` in `backend/app/services/transaction_service.py`).
- `restamp_user_primary_amounts` (`backend/app/services/primary_restamp_service.py`) uses the typed amount whenever the new display currency is the one it was typed in, before the account charge of ADR 0027 and before a market conversion. In any other currency the row is converted as before.
- The typed amount is dropped when the row's primary amount is derived again for a reason other than the display currency: clearing the conversion in the dialog, or changing the row's amount, currency, date or account (or its transfer leg's amount or date) without typing a new one. An edit that resends the same values keeps it.
- Migration 129 adds the columns empty. Nothing records which stored values were typed, so rows written before it convert at the market rate on a round trip, as they did since #1561.

## Consequences

- A display currency round trip gives back what the user typed, on any account.
- Recurring transactions and assets have no typed figure and keep converting at stored rates.

## Enforced by

`tests/test_primary_restamp.py` (a round trip on a same-currency account, a resave that keeps the typed amount, an edit that drops it).
