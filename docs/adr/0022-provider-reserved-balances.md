# 0022. Provider reserved balances are counted on top of the account balance

- Status: Accepted
- Date: 2026-11-17
- Origin: the Pluggy `bankData.reservedBalances` pull request

## Context

Some institutions hold money *reserved* inside an account: Mercado Pago
cofrinhos, "caixinhas", judicial holds, a scheduled Pix. Pluggy reports them in
`bankData.reservedBalances`, and Securo never read that field, so those figures
reached no surface at all — they were not an account, not an `/investments`
holding, and not part of `Account.balance` either. The account page never showed
them and net worth understated them.

A real Mercado Pago item reports the shape the tests use: a `balance` of
`1000.00`, an `automaticallyInvestedBalance` equal to it, and reservations of
`2000.00` and `2000.00`. The reservations are money the user holds that
`balance` does not include, so the account's value is `1000 + 2000 + 2000 =
5000` — the balance plus the reservations, counted once.

Two things this ADR deliberately does **not** claim:

- **No arithmetic is derived from `closingBalance`.** It is never read. Nothing
  here says what it contains or how it relates to `balance`.
- **`automaticallyInvestedBalance` is not added.** On this payload it equals
  `balance`, i.e. it is already inside it; adding it would double count. Whether
  that holds for every connector is the existing concern of issue #843, whose
  behaviour (`_auto_invested_deductions`) is untouched by this ADR.

Two other shapes are ruled out:

- **Folding the reservations into `Account.balance`** breaks the ledger
  reconciliation, which assumes `SUM(transactions) == Account.balance`, and
  makes the account card disagree with the bank app's own figure. `balance`
  keeps the provider's number; the reservations are added by the value paths.
- **Mirroring each reservation as an `Asset`** puts earmarked money into
  portfolio and allocation reports, and feeds it to
  `_counted_holdings`/`_auto_invested_deductions`, where the 1%-tolerance match
  of issue #1459 would let a pocket subtract itself from the account balance.

## Decision

Reserved balances are stored per account, surfaced as a breakdown, and counted
once on top of the provider's balance.

- **Storage**: `account_reserved_balances` (model
  `app/models/account_reserved_balance.py`), one row per
  `(account, provider identification, currency)`, with `workspace_id` and
  `ON DELETE CASCADE` from `accounts`. `amount` is the sum of the reservation's
  remuneration bands in that currency. Provider-owned: `name` is a label, never
  an identity.
- **Provider contract**: `ReservedBalanceData` in `app/providers/base.py`, and
  `AccountData.reserved_balances`, where `None` means the provider did not report
  the collection (stored rows are left alone), `[]` means it reported none
  (authoritative — rows are deleted), and a present list is authoritative.
  Pluggy parses it in `app/providers/pluggy.py::_reserved_balances`; SimpleFIN
  and Enable Banking leave the field `None`.
- **Sync**: `app/services/connection_service.py::_sync_reserved_balances`
  upserts by `(external_id, currency)` and deletes rows the provider stopped
  reporting, in the same transaction as the account write.
- **Counting**: `Account.balance` keeps the provider's figure verbatim. The
  same-currency reserved total is added in exactly two places —
  `app/services/dashboard_service.py::_account_balance_at` (net worth, goals,
  shared-line strip) and `::_total_balance_by_currency` (dashboard, balance
  history) — through one helper,
  `app/services/account_service.py::reserved_totals_by_account`, which sums only
  rows whose currency matches the account's.
- **Excluded on purpose**: the transaction ledger walk (the invariant above),
  and the liquidity forecast
  (`app/services/cashflow_forecast_service.py`) — the runway starts from money
  that can pay a bill today, and a judicial hold or a scheduled Pix cannot.
- **Surfaced**: `serialize_account` adds `reserved_balances` and
  `reserved_total` beside `current_balance`, the read schema mirrors them, the
  account page renders a "Reserved balances" card, and workspace backups carry
  the rows.

## Consequences

- The dashboard's "Available balance", its account chips, net-worth breakdown
  and credit-card payment widget include same-currency reserves for every
  provider that reports them. This is a product rule, not an institution-specific
  inclusion heuristic. The account page's "Current balance" remains the
  provider's figure, with reserves shown separately.
- `reserved_total_primary` uses the same real FX rate as `balance_primary`,
  including when the provider balance is zero. Without a rate both remain
  unset and the dashboard keeps its existing native-currency fallback warning.
  The UI adds these separate components once; it does not add reserves again
  to the backend's already-inclusive net worth.
- Moving money between a pocket and the account's available balance is
  net-worth neutral: the balance rises by the amount the pocket falls.
- Reserved money is treated as constant across historical net-worth cutoffs —
  there is no per-pocket history table. A trend that needs it must add one.
- Foreign-currency reservations are stored and shown but not summed; a rate
  belongs to the caller, not to this sum.
- Every new consumer of an account's value goes through the two counting sites
  above; summing `account_reserved_balances` directly would count it twice.
- Deleting an account or connection removes its reservations with it; closing an
  account keeps them, hidden with the account.
- The parse treats `availableAmounts[].amount` as major units. If a connector
  ever reports minor units, the sum would overstate the account — the reason to
  check a new connector's payload before trusting it.

## Enforced by

`backend/tests/test_providers_pluggy.py` (parsing, band summing, absent vs empty
vs malformed), `backend/tests/test_connection_service.py` (upsert, rename,
delete, workspace stamping, the stored balance staying the provider's figure),
`backend/tests/test_dashboard_service.py` and
`backend/tests/test_report_service.py` (the account's value is the balance plus
the same-currency reservations, counted once, and a redemption is neutral),
`backend/tests/test_accounts_api.py` (the payload carries the rows and a total,
`current_balance` unchanged), `backend/tests/test_cashflow_forecast.py` (the
runway excludes reserved money),
`frontend/src/pages/account-detail-reserved-balances.test.tsx`,
`frontend/src/pages/dashboard.test.tsx` (available headline, chips, collection
scope and breakdown, including FX and zero-balance accounts),
`backend/tests/test_workspace_boundary_schema.py` and
`backend/tests/test_schema_matches_migrations_postgres.py`.
