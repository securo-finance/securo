# 0040. A row already in the viewer's currency reads as its own amount

- Status: Accepted
- Date: 2026-11-23
- Origin: #1821 and #1826 (board task #463: R$ 10,03 read back as R$ 10,06, and US$ 300 as US$ 312)
- Extends: [0032](0032-viewer-display-currency.md)

## Context

[0032](0032-viewer-display-currency.md) has a member of a shared workspace read another member's `amount_primary` through `ViewerCurrency`, which converts the owner's stamp into the viewer's currency at the row's date. When the row itself is already in the viewer's currency, that is a round trip, and the figure that was charged is already on the row.

A derived stamp only moves the cents on the way back: Bruno (EUR) enters R$ 10,03, the stamp is 2,01 €, and Ana (BRL) read it as R$ 10,06. A typed stamp ([0031](0031-typed-primary-amount.md)) moves more: Ana's US$ 300 with a typed R$ 1.560, read back into dollars at the market rate, showed US$ 312 to a member in dollars. What the owner typed is what the card charged in the owner's currency, not a second figure for the row's own currency.

## Decision

- A row whose `currency` is the viewer's display currency, owned by a member who stamps in another currency, reads as its own `amount`, whether its stamp was derived or typed. The currency code is compared upper-cased, so a row stored as `brl` matches too.
- A typed conversion holds in the owner's currency only. It stays stored as [0031](0031-typed-primary-amount.md) keeps it, and the owner's own numbers still read it.
- In SQL, `ViewerCurrency.amount()` in `backend/app/services/viewer_currency.py` adds this as the first branch of its `CASE`, before the conversion of foreign owners' stamps. Per row, `ViewerCurrency.convert()` does the same when given the row's `currency` and `amount`. The two agree, so a total and the rows it drills into match to the cent. The matched part of a split row takes the same rule as the row itself.
- Rows in a third currency are converted from the owner's stamp, as in 0032.

## Consequences

- Every caller of `ViewerCurrency.convert()` that shows a row passes its `currency` and `amount`, or the row falls back to the round trip.
- The stamp is not touched: the rule is a read. An edit by the viewer sends back the figure it was shown, the row's own amount in its own currency (US$ 300, not the owner's typed R$ 1.560). 0032's write guard takes a conversion sent back unchanged from what was shown as an echo and drops it, and that is what keeps the owner's typed stamp stored.

## Enforced by

`backend/tests/test_viewer_currency_shared_workspace.py`: `test_a_typed_row_in_the_members_currency_reads_as_its_own_amount` (300, not 312, in the list, footer, detail and calendar, and the typed stamp kept after the member's edit), `test_a_row_in_the_viewers_currency_reads_as_its_own_amount`, `test_a_split_row_in_the_viewers_currency_matches_its_own_part` and `test_a_lowercase_code_in_the_viewers_currency_reads_as_its_own_amount`.
