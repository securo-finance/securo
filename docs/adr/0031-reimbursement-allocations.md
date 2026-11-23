# 0031. Reimbursements net user P&L through allocations; the rows stay as the bank reports them

- Status: Proposed
- Date: 2026-11-22
- Origin: issue #1126 (related: #828)

## Context

Paying for others and being paid back, or buying something and returning part of it, moves money through the user's accounts that is neither spending nor income. Reports counted the full payment as an expense and every payback as income. Group splits already count only the owner's share, but only for splits and settlements created in the app: a payback that arrives through bank sync is an ordinary credit, and a refund has no group at all. Editing the amounts would lose the bank's figures and break sync matching and reconciliation, and transfers link only two rows of equal amounts.

## Decision

- A row in the other direction (the counterpart) can pay back part of an earlier one (the anchor). `reimbursement_allocations` (`backend/app/models/reimbursement_allocation.py`) holds one allocation per pair: a positive `amount` in the counterpart's currency. Links are many-to-many: an anchor can have many counterparts, and a counterpart can be split across anchors.
- `backend/app/services/reimbursement_service.py` is the only writer. Its invariants: opposite directions, one workspace, a row is never both an anchor and a counterpart (no chains), a counterpart is never allocated beyond its amount (an anchor has no cap), and neither side is a transfer, an ignored row or category, a transfer-like category, a settlement, an opening balance, a mortgage principal row or a row split among people. A manual edit that would shrink a counterpart below its allocations, flip a linked row, or split it among people is refused.
- Only user-level P&L nets: the dashboard, the income and expenses report, budgets and the category statement. `backend/app/services/reimbursement_netting.py` turns each allocation into parts that come off the full-row totals the way group-split offsets do, folded into `owner_split_offset_pnl`, `owner_split_offset_by_category` and `forecast_owner_share_fractions` in `backend/app/services/_query_filters.py`, plus the two places that compute split offsets themselves (`report_service`, `category_statement_service`).
  - The counterpart's allocated part leaves its own period, category and direction. The same amount comes off the anchor in the anchor's period and category, spread over its category lines in proportion.
  - An anchor paid back more than in full counts the excess in the opposite direction, still in its own category: 150 back for a 100 gift is 50 of income.
  - Amounts are converted at the counterpart's stored rate on both sides. An allocation with no rate at all nets nothing.
  - An allocation counts only while both rows count toward user P&L. When a sync shrinks a counterpart below its allocations, they are scaled down to fit at read time.
- The dashboard's balance evolution curve has a `netting` switch, on by default (`GET /api/dashboard/balance-history?netting=`, the switch on the widget). Netted, a linked row counts for its remaining share only, on its own date: a dinner paid for four dips the line once by the user's share and the paybacks do not bump it. `dashboard_service._netting_effects_by_date` applies it to the same rows as the real deltas (`_balance_row_filters`), the starting balance carries what netting moved before the month, and pending or future rows scale by `reimbursement_share_fractions`. A chart scoped to some accounts (a collection) stays real, because a subset cannot tell which side of a link it holds; the response's `netted` says what was applied.
- Balances, card bills, net worth, payee totals, per-account stats, the cash flow report, the accountant export and plugin reports keep reading whole rows on their own dates.
- Routes: `GET`/`PUT /api/transactions/{id}/reimbursements` and `GET /api/transactions/{id}/reimbursement-candidates` in `backend/app/api/reimbursements.py`, behind the transactions write gate and the `transactions` access-token scope. `GET /api/transactions` and its export take a `reimbursement` filter (`linked`, `anchor`, `counterpart`, `unlinked`) for rows by their place in a link.

## Consequences

- A month's totals change when a late payback is linked to one of its rows. That is the point: the month shows the user's share.
- Anything new that totals user P&L has to take the offsets from the shared helpers, or it counts paybacks as income.
- The link model is not shared with #828 (cash rows against asset movements): that one nets net worth, this one P&L, and a shared table would tie their rules together.
- A netted balance curve ends where the real balance does once every link has settled. While a payback is still on its way (a later month, or pending) the curve sits above the real balance by that amount, and the Total balance card, which is real, differs from it.
- A category whose net spending is already zero or below is left out of budgets before the offsets apply, so a credit an allocation takes out of such a category does not bring its spending back.

## Enforced by

`tests/test_reimbursement_service.py` covers the invariants and refused edits; `tests/test_reimbursement_netting.py` the netting rules; `tests/test_reimbursement_reports.py` each P&L view and the balances; `tests/test_reimbursement_balance.py` the netted balance curve; `tests/test_reimbursement_api.py` the routes, write gate and scopes; `tests/test_reimbursement_data.py` backups; `tests/test_reimbursement_postgres.py` the cascade, constraints and migration on PostgreSQL.
