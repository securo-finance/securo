# UI Spec: Phase 02 - Card Transaction Browsing

## Intent

Make the selected card's transactions feel familiar to users of `Transações` while keeping the selected card context obvious.

## Key Surfaces

- `/cards/:id` header with card name and return action
- Transaction table scoped to one card

## Design Notes

- Preserve the visual hierarchy of `Transações`: page header, compact filter bar, bordered table, fixed bulk-action bar.
- Keep the back-to-cards action visible without overwhelming the page header.
- Card metadata should be present near the top so the user does not lose context when paging or filtering.

## Mobile

- Header actions should wrap cleanly.
- The transaction table continues to collapse to the compact mobile layout already used by `Transações`.
