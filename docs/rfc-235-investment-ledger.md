# RFC #235 — Investment ledger: ticker grouping, average price, holdings vs. transactions

Status: in progress · Issue: [#235](https://github.com/securo-finance/securo/issues/235)

## Problem

BR investors (Status Invest / Investidor10 style) need their portfolio
**consolidated by ticker** with an **average price** (preço médio), and a
**separate list of buy/sell transactions** for income-tax (IR) reporting.
Today each `Asset` is a single position with one `purchase_price` — there is no
transaction ledger, no average across multiple buys, and no ticker grouping.

## Approach

Adopt the trades-are-source-of-truth model (inspired by Maybe/"sure"): a
market-priced `Asset` is the **consolidated holding for one ticker (per
wallet)**, and a new `asset_transactions` ledger sits underneath it. The
position (`units`, `average_price`, cost basis, realized gain) is **derived**
from the transactions and cached on the asset row so list views and the
portfolio chart stay cheap and unchanged.

### Average price — weighted average (preço médio, not FIFO)

Process a ticker's transactions in date order:

- **buy:** `total_cost += qty*price + fee`; `qty += quantity`
- **sell:** `avg = total_cost/qty`; `realized += (price - avg)*sell_qty - fee`;
  `total_cost -= avg*sell_qty`; `qty -= sell_qty` (avg per-share unchanged)
- Derived: `average_price = total_cost/qty`, cost basis = `total_cost`,
  `unrealized = units*last_price - total_cost`, `realized` accumulated above.

### Consolidation = find-or-create

Adding a buy for `PETR4` attaches a transaction to the existing PETR4 holding
in the chosen wallet instead of creating a duplicate asset. That is the ticker
grouping.

## Data model

New table `asset_transactions` (migration `059`, chained from `058`):
`id, asset_id, workspace_id, kind(buy|sell), quantity, price, fee, date,
source, external_id, notes, created_at`.

Two cached, additive columns on `assets`: `average_price`, `realized_gain`.
`units` is repurposed as the cached current quantity; `purchase_price` is
repurposed as the cached cost basis of the held position (keeps the existing
`gain_loss = current_value - purchase_price` math working as unrealized gain).

### No data loss

Purely additive: new table + two nullable columns. No column is dropped,
renamed, or overwritten; `AssetValue` history is untouched. The backfill only
reads existing `purchase_price`/`units`/`purchase_date`/`sell_*` to emit the
initial buy/sell rows and is idempotent. `downgrade()` drops the new table and
columns. Synced (Pluggy) holdings are unaffected in this PR.

## Scope

- **In:** manual + market-priced equity assets (stock/etf/crypto/fund) get the
  ledger, average price, realized/unrealized gains, a consolidated holdings
  view, and a transactions tab.
- **Out (this PR):** synced Pluggy holdings stay provider-owned single
  positions; ceiling-price suggestions (Graham/Lynch/Klarman/Bazin) are a
  follow-up (need fundamentals not yet plumbed).

## Surface

- Backend: `AssetTransaction` model, migration `059` (+ backfill),
  `asset_transaction_service` (recompute + CRUD + find-or-create buy), new
  fields on `AssetRead`, endpoints under `/api/assets/...`.
- Frontend: "Holdings" (consolidated, with preço médio) and "Transactions"
  tabs on the Assets page; add/edit/delete transactions; i18n (en/pt-BR/es/it/pl).
