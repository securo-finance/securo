# UI Spec: Phase 01 - Cards Navigation Surface

## Intent

Introduce `Cartões` as a natural sibling of `Transações`, using the existing shell language and card-based surfaces already present in the app.

## Key Surfaces

- Sidebar link directly below `Transações`
- `/cards` listing page with one tile per imported credit card

## Design Notes

- Reuse the existing shell spacing, rounded card containers, and subdued finance-product tone.
- Each card tile should make the card name the primary identifier, with currency and bill-import state as supporting metadata.
- Empty state should be calm and informative rather than promotional.

## Mobile

- Card tiles stack in a single column on narrow widths.
- Primary action remains the whole card tile tap target.
