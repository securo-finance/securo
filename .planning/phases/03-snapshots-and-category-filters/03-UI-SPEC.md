# UI Spec: Phase 03 - Snapshots And Category Filters

## Intent

Extend the card transaction page with snapshot and category controls that feel like the existing month-view model, not like a separate reporting mode.

## Key Surfaces

- Snapshot/current-month selector within the card detail summary area
- Category filter beside the existing search control
- Snapshot read-only notice when browsing a closed month

## Design Notes

- Reuse the dashboard's snapshot terminology and warning tone.
- Keep the selected card fixed while period and category controls change around it.
- Empty states should explain when filters or snapshot context are the reason no rows are shown.

## Mobile

- Snapshot selector stacks under the card metadata block.
- Filter controls collapse to a vertical layout without hiding the selected card context.
