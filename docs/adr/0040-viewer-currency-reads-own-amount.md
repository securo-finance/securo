# 0040. A row already in the viewer's currency reads as its own amount

- Status: Accepted
- Date: 2026-11-23
- Origin: #1821 (issue #463: R$ 10,03 read back as R$ 10,06)
- Extends: [0032](0032-viewer-display-currency.md)

## Context

[0032](0032-viewer-display-currency.md) has a member of a shared workspace read another member's `amount_primary` through `ViewerCurrency`, which converts the owner's stamp into the viewer's currency at the row's date. When the row itself is already in the viewer's currency, that is a round trip: Bruno (EUR) enters R$ 10,03, the stamp is 2,01 € after rounding to the cent, and Ana (BRL) reads it back as R$ 10,06. The figure that was charged is on the row, and the conversion only moves its cents.

A stamp the owner typed is different. [0031](0031-typed-primary-amount.md) keeps it apart (`amount_primary_typed`) because it is a figure of its own, such as what the card really charged in the owner's currency.

## Decision

- A row whose `currency` is the viewer's display currency, owned by a member who stamps in another currency, reads as its own `amount` when its stamp was derived (`amount_primary_typed` is NULL). The currency code is compared upper-cased, so a row stored as `brl` matches too.
- In SQL, `ViewerCurrency.amount()` in `backend/app/services/viewer_currency.py` adds this as the first branch of its `CASE`, before the conversion of foreign owners' stamps. Per row, `ViewerCurrency.convert()` does the same when given the row's `currency`, `amount` and `typed` value. The two agree, so a total and the rows it drills into match to the cent.
- The matched part of a split row takes the same rule as the row itself.
- A row with a typed stamp is still converted from the owner's stamp, as in 0032.

## Consequences

- Every caller of `ViewerCurrency.convert()` that shows a row must pass its `currency`, `amount` and `amount_primary_typed`, or the row falls back to the round trip.
- The stored stamp is not touched: the rule is a read, so the owner's own numbers stay as 0032 has them.
- Changing how a typed stamp reads for a viewer in the row's currency is a change to this decision and needs an ADR that supersedes it.

## Enforced by

`backend/tests/test_viewer_currency_shared_workspace.py`: `test_a_row_in_the_viewers_currency_reads_as_its_own_amount`, `test_a_split_row_in_the_viewers_currency_matches_its_own_part`, `test_a_lowercase_code_in_the_viewers_currency_reads_as_its_own_amount`, and `test_calendar_shows_the_owners_conversion_of_a_row_in_the_members_currency` for a typed stamp that is still converted.
