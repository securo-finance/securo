# 0022. Provider reserved balances are shown, never summed into a total

- Status: Accepted
- Date: 2026-11-17
- Origin: #1507, discussion in #1512

## Context

Pluggy reports Mercado Pago cofrinhos and other earmarked funds in
`bankData.reservedBalances`. Securo did not read or display that collection.

Pluggy does not document whether reserves are included in `balance` for every
institution. Reserves can also be judicial holds or scheduled Pix, not just
savings pockets. Assuming that every reserve is additional, spendable money
could double-count balances or overstate liquidity. The existing handling of
`automaticallyInvestedBalance` is a separate concern and remains unchanged.

## Decision

Store provider-owned reservations under their account and display a separate
breakdown. Never add them to account balances, net worth, dashboard totals,
goals, historical balance calculations, investment holdings or the cashflow
forecast. Any future inclusion rule needs evidence about the institution's
balance contract and a separate decision; #1512 tracks that discussion.

- **Storage**: `app/models/account_reserved_balance.py`, migration
  `alembic/versions/118_account_reserved_balances.py`. One row per
  `(account_id, external_id, currency)`, carrying the account's `workspace_id`.
  The provider's identification is the identity; a rename only updates `name`.
- **Provider contract**: `ReservedBalanceData` and
  `AccountData.reserved_balances` in `app/providers/base.py`.
  `None` means unreported or incomplete; `[]` means explicitly empty.
  A provider plugin reports the same collection in its account payload,
  through `ReservedBalance` and `Account.reserved_balances` in
  `app/plugins/contract.py`, with the same `None` and `[]` meanings and one
  record per `(external_id, currency)`.
- **Complete reads only**: `app/providers/pluggy.py::_reserved_balances` checks
  the collection, every identification and every remuneration band.
  `availableAmounts` must be a non-empty list of usable bands. If any entry or
  band is unreadable, return `None` and retain all stored rows. Only an explicit
  empty collection or `hasReservedBalance: false` can clear the collection.
  Valid bands are summed per currency; explicit zero amounts remain visible.
- **Idempotent sync**: `app/services/connection_service.py::_sync_reserved_balances`
  upserts by `(external_id, currency)`, including rows first seen earlier in the
  same response. Repeated keys reuse that row, with the last reported name and
  amount winning; they are not summed. A complete response removes missing
  keys in the same transaction. An incomplete response changes no reserve rows.
- **Display**: `app/services/account_service.py::serialize_account` returns
  `reserved_balances` and `reserved_total`; `app/schemas/account.py` exposes
  them read-only. `reserved_total` is only the panel's same-currency subtotal,
  never an input to another balance. `frontend/src/pages/account-detail.tsx`
  shows each reservation in its own currency, respecting privacy mode.
- **Backup**: workspace exports in `app/api/export.py` include the rows.

## Consequences

- `Account.balance`, `current_balance`, Available balance and net worth keep
  their existing meaning. No arithmetic is inferred from `closingBalance`,
  `automaticallyInvestedBalance` or the presence of reserves.
- Malformed optional provider data must not fail the account import or
  silently replace stored money with a partial snapshot.
- Reservations stay out of the holdings and auto-invested deduction paths.
- Different-currency reservations are stored and displayed, but excluded from
  the panel's account-currency subtotal. No reserve FX conversion is invented.
- Only current reservation values are stored. No historical value is inferred
  from them, and no history table is needed for this display-only feature.
- Deleting an account removes its reservations; closing it keeps them with
  the account.

## Enforced by

`backend/tests/test_providers_pluggy.py` (complete parsing, invalid bands and
scalar `availableAmounts`), `backend/tests/test_connection_service.py`
(duplicate keys, partial responses preserving stored rows, rename, deletion
and workspace stamping), `backend/tests/test_plugin_contract.py` (the plugin
payload's `None` and `[]`, duplicate identity and amount validation),
`backend/tests/test_provider_plugins.py` (a plugin payload synced through the
real pipeline stores, preserves and clears reserves without changing the
balance), `backend/tests/test_accounts_api.py` (display payload
with provider balances unchanged), `backend/tests/test_dashboard_service.py`
and `backend/tests/test_report_service.py` (reserves never change totals),
`backend/tests/test_cashflow_forecast.py` (forecast unchanged),
`frontend/src/pages/account-detail-reserved-balances.test.tsx` (visible panel
and rendered absence cases), `frontend/src/pages/dashboard.test.tsx`
(available balances exclude reserves), `frontend/src/locales/i18n.test.ts`,
`backend/tests/test_workspace_boundary_schema.py` and
`backend/tests/test_schema_matches_migrations_postgres.py`.
