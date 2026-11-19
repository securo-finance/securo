# 0027. A row's value in its account's currency has its own column

- Status: Accepted
- Date: 2026-11-19
- Origin: #1632 (issue #1381: stored primary amounts kept the old display currency)
- Extends: [0008](0008-money-and-currencies.md)

## Context

ADR 0008 gives every transaction an `amount_primary`: its value in its owner's primary (display) currency, for totals across currencies. The balance code also read that column for a second purpose: for a row in another currency than its account (a USD purchase on a BRL card), as the row's value in the account's currency. Balances, card bills, running balances and the balance history all summed it.

The two meanings agree only while the account is in the owner's display currency. They split in two cases:

- An account in another currency than the owner's (a EUR account for a BRL user) summed a BRL figure into a EUR balance.
- Changing the display currency, and restamping `amount_primary` to follow it as #1381 asks, would rewrite every balance holding a foreign row, and lose the amount the bank actually charged (spread and fees included) for good.

Other open source finance tools keep these apart too: the amount booked against the account is the source of truth for its balance, and a value in the display currency is a cache that can be rebuilt.

## Decision

- `transactions.amount_account` (`Numeric(15, 2)`) is the row's value in its account's currency. It is set only when the row's currency differs from its account's; a same-currency row keeps NULL and readers use `amount`.
- Balance readers use it: `amount_in_account_currency` and `signed_amount_expr` in `backend/app/services/_query_filters.py`, the account summary in `backend/app/services/account_service.py`, `_signed_transaction_amount` in `backend/app/services/dashboard_service.py` and `_signed_balance_delta_primary` in `backend/app/services/transaction_calendar_service.py`. An unconverted row still counts as zero in a balance (#353).
- `stamp_account_amount` in `backend/app/services/fx_rate_service.py` writes it, and `stamp_primary_amount` calls it for every transaction. In order: the amount the bank charged the account, when the provider reports one (`amount_in_account_currency`); `amount_primary`, when the account is in the owner's primary currency, so a converted amount typed in the transaction dialog keeps counting; otherwise the stored rate from the row's currency into the account's, for the row's date.
- Migration 123 fills it from `amount_primary` for every row in another currency than its account, which is the figure the balances read before; no balance moves on upgrade. A backup from before this column restores the same way.
- `amount_primary` keeps its ADR 0008 meaning, a reporting value in the owner's primary currency. Nothing that reads a balance may read it.

## Consequences

- Restamping `amount_primary` when the display currency changes no longer touches any balance.
- A new foreign row in an account outside the owner's currency now counts in the account's currency. Rows written before this change keep the figure they had; there is no way to tell which currency an old `amount_primary` was stamped in.
- Every new path that writes a transaction's amount, currency, date or account goes through `stamp_primary_amount`, or calls `stamp_account_amount` itself.
- The scheduled healer (`restamp_fallback_fx`) still selects rows by their primary stamp. A row whose account is in a third currency with no stored rate keeps counting as zero until it is written again, as it did before.

## Enforced by

`tests/test_amount_account.py` (balances across a display currency change, an account in another currency, moving a row between accounts, the provider's charge, a backup from before the column) and `tests/test_amount_account_migration_postgres.py` (the migration's backfill).
