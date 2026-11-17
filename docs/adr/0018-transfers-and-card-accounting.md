# 0018. Transfers are matched by a visible policy; card accounting mode moves reports, never balances

- Status: Accepted
- Date: 2026-09-08
- Origin: #65 (card accounting mode), #817 (payment matching)

## Context

Money moving between two of a person's own accounts is not income or expense, and has to be paired to stay out of reports. Credit card spending can be read on the day it happened or on the day the bill is paid. Both used to be hidden heuristics people couldn't see or change.

## Decision

- **Transfers.** Two transactions that are one transfer share a `transfer_pair_id`. `backend/app/services/transfer_detection_service.py` loads candidates, asks the reconciliation engine, and records the decision. The matching rules (amount, accounts, days apart) are a policy the workspace can see, change and turn off (`backend/app/services/reconciliation_policy.py`), not constants in code. Paired transfers are excluded from income and expense through `is_not_transfer` in `_query_filters.py`.
- **Card accounting mode.** An instance setting, `credit_card_accounting_mode`, is `cash` (default) or `accrual` (`get_credit_card_accounting_mode` in `backend/app/services/admin_service.py`). It changes which date dashboards, reports and budgets use for card spending (`Transaction.effective_date`, the bill cycle's date). It never changes an account's balance: balances always follow the ledger.

## Consequences

- New matching behavior is a new policy field, not a new heuristic.
- The accounting mode is per instance, not per workspace.

## Enforced by

`tests/test_transfer_detection.py`, `tests/test_transfer_matching_rules.py`, `tests/test_credit_card_accounting_mode.py`.
