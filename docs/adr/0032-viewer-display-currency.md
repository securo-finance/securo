# 0032. Members of a shared workspace read stamps in their own display currency

- Status: Accepted
- Date: 2026-11-23
- Origin: #1771 (issue #269: a member in EUR saw R$ 1.600 as 1.600 €)
- Extends: [0008](0008-money-and-currencies.md)
- Extended by [0039](0039-date-move-keeps-typed-stamp.md): a move of the date alone keeps the amount the owner typed.
- Extended by [0040](0040-viewer-currency-reads-own-amount.md): a row already in the viewer's currency reads as its own amount, typed conversion or not.

## Context

ADR 0008 stamps every transaction with `amount_primary`, its value in the primary (display) currency of the row's owner (`user_id`). In a workspace of one person, the owner and the viewer are the same and every stamp is in the viewer's currency.

A shared workspace breaks that. Members can display different currencies, and a row one member created is stamped in theirs. Totals, the list and reports summed those stamps for every viewer as they were stored, so a member in EUR read another member's BRL stamps as euros.

Rewriting the stamps for each viewer is not an option: the stored stamp belongs to the owner's own numbers, and two viewers in different currencies read the same row.

## Decision

- The stored `amount_primary` stays the owner's and is never rewritten for a viewer.
- A read that sums, filters, sorts or shows `amount_primary` to a viewer goes through `backend/app/services/viewer_currency.py`. `viewer_currency` lists the owners who stamp in another currency than the viewer; `ViewerCurrency.amount` converts their stamps in SQL at the row's date, with the stored rates and rounding to the cent, and leaves the rest as stored. A pair with no stored rate reads 1:1, like `fx_rate_service.convert`.
- The dashboard, the transactions list (its rows, footer, amount bounds and amount sort), reports, budgets and the viewer's shares of group splits (`viewer_shares_in_primary` in `backend/app/services/_query_filters.py`) all read through it, so a figure and its drill-down agree.
- A row already in the viewer's currency, whose owner's stamp was derived from its amount (`amount_primary_typed` is NULL), reads as its own `amount`: converting it into the owner's currency and back moved the cents (#463). A stamp the owner typed is a figure of its own, such as what the card really charged, so that one is still converted.
- A converted row is flagged as an FX fallback by its stored stamp, judged in its owner's currency, so a legacy 1:1 stamp keeps its warning after conversion.
- Every transaction a route returns reads like the list, not only the list: the detail, the transfer pair and candidates, the rows a create, edit or toggle returns, and the calendar's day items. The transaction dialog opens from any of them and sends `amount_primary` back as it got it.
- Writes go the other way. An edit by a member in another currency stamps the row in its owner's currency, whoever edits it (#440). A conversion that member sends back unchanged, either as it was shown or as the owner's stored stamp, is dropped, so the owner's stamp and the figure they typed stay as they were. A changed one is converted at the row's date into the owner's currency, and refused when no stored rate links the two. A rate synced after the client read the row moves what the server would show, so a client says what it was shown in `amount_primary_shown`, and a conversion equal to it is an echo too. Resending the same amount, currency and date does not restamp a row that already has a stamp.

## Consequences

- An owner in the viewer's currency costs nothing extra: `ViewerCurrency.amount` returns the column unchanged when no owner stamps in another currency.
- A row's value shifts with the rate stored for its date, not today's, so a past month does not move when rates do.
- New code that reads `amount_primary` for a viewer in a shared workspace must take it from `ViewerCurrency`, or it shows another member's stamp in the wrong currency.

## Enforced by

`tests/test_viewer_currency_shared_workspace.py` (dashboard, list rows and footer, amount bounds and sort, report, budgets and group shares for a member in another currency, edits by that member, and the owner's own numbers) and `tests/test_cross_workspace_group_shares.py` (the signed shares of rows with and without a stamp).
