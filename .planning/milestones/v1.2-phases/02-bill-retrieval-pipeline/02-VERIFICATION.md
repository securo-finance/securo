status: passed

# Phase 02 Verification

## Verified

- Pluggy provider now exposes bill retrieval alongside transactions.
- Connection creation and subsequent sync import only bill-linked credit-card rows for the supported runtime.
- Bill selection is based on `dueDate` in the month after the active period.

## Gaps

- Backend runtime tests were not executed in this shell because Python tooling is unavailable.

## Human Verification

- Connect a credit card with a closed bill and confirm only bill transactions land in Flux.
- Change `Mês Atual` and confirm the next sync targets the bill due in the following month.
