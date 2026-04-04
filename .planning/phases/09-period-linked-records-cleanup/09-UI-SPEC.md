# Phase 9 UI Spec: Period-Linked Records and Cleanup

## Intent

Preserve the existing Flux layout while making period-linked data feel invisible but reliable. Users should keep interacting with familiar dashboard, accounts, and transaction views, while the app silently resolves records against the active monthly period and no longer exposes category-group concepts.

## UI Contract

- Existing pages keep their current structure; this phase should not add a new period-management screen or a visible category-group replacement surface.
- If period-linked data changes require extra metadata in account or transaction flows, they must fit inside the current form/dialog layout patterns.
- Any cleanup of category-group traces removes UI elements rather than replacing them with a renamed grouping concept.

## Copy Contract

- Avoid surfacing technical period-linking terminology when the user does not need it.
- Preserve current bilingual support if any period-aware labels or helper text change as part of account or transaction flows.
