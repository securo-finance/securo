# 0039. A move of the date alone keeps the amount the owner typed

- Status: Accepted
- Date: 2026-11-23
- Origin: #444 (a member moving only the date lost the owner's typed figure)
- Amends: [0031](0031-typed-primary-amount.md), [0032](0032-viewer-display-currency.md)

## Context

ADR 0031 keeps a figure a person typed apart from the display-currency cache, and ADR 0032 says what a member in another currency may rewrite on a row they do not own. The amount they send back unchanged is an echo and is dropped; anything else is converted at the row's date into the owner's currency.

ADR 0032 did not say which edits invalidate the typed figure. The code decided the restamp with one flag that covered both money and date, so a member moving only the date dropped the typed copy and the row came back at the market rate of the new date: Ana (BRL) had R$ 1.560 typed at 5,2 on 20/11, Bruno (EUR) moved the row to 12/11, and the figure became R$ 1.500, the market value on that day. When Ana herself moved the date the figure stayed, so the same edit had two outcomes depending on who made it.

## Decision

- What the converter typed is a statement about the amount, not about the date, so only a change of amount, of currency or of account drops it (`_drop_typed_primary` in `backend/app/services/transaction_service.py`).
- A date move still restamps the account leg, because the value in the account's currency follows the date, and `_restore_typed_primary` then puts the owner's figure back.
- A row with nothing typed keeps following the date: a derived stamp is still recalculated on a date move.
- The restamp decision reads `money_inputs_changed` for the typed copy and keeps the date out of it.
- The counterpart of a transfer and a pending row posted to today still follow ADR 0031: a date change there drops the typed figure. This ADR covers the row the person edits; aligning those two paths with it is a separate decision, tracked in #1816.

## Consequences

- A member in another currency can move a row to another date without changing what its owner wrote down.
- A date move costs one account-leg conversion and one restore; nothing else in the update path changes.

## Enforced by

`backend/tests/test_viewer_currency_shared_workspace.py`: `test_member_moving_only_the_date_keeps_the_owners_typed_stamp`, `test_member_moving_only_the_date_restamps_a_derived_stamp` and `test_member_moving_only_the_date_keeps_the_typed_account_leg`.
