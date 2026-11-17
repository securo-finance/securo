# 0017. Category lines split a transaction's category, the row stays as the bank reports it

- Status: Accepted
- Date: 2026-11-06
- Origin: #1383 (issue #425)

## Context

One purchase often covers several categories (groceries and household in one receipt). Splitting the transaction into several rows would break bank sync matching, imports and reconciliation, which all expect the row the bank sent.

## Decision

- A transaction keeps one row, exactly as the bank reports it. Its category can be divided into lines in `transaction_category_lines` (`backend/app/models/transaction_category_line.py`), each with an amount and a share type (`exact` or `percent`).
- `backend/app/services/category_line_service.py` is the only writer. Its invariants: at least two lines; line amounts are positive and sum exactly to the transaction's absolute amount (a -100.00 expense splits into 60.00 and 40.00; leftover cents go to the largest fractions); `Transaction.category_id` is NULL while the transaction has lines; every line's category is in the same workspace and is not hidden or ignored.
- Recurring transactions can carry the same template (`recurring_transaction_category_lines`), materialized on each occurrence.
- Category lines are not `TransactionSplit`: splits share a cost between people in a group; lines divide one person's expense between categories.

## Consequences

- Any query that aggregates by category has to decide between the line's category and the row's. `with_category_lines` in `backend/app/services/_query_filters.py` does it for reports.

## Enforced by

`tests/test_category_parts_structure.py` checks the aggregation call sites; `tests/test_category_lines_*.py` cover the invariants, including on Postgres.
