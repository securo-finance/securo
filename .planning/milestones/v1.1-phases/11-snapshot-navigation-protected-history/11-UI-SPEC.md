# Phase 11 UI Spec: Snapshot Navigation and Protected History

## Intent

Make historical month browsing feel explicit, calm, and safe. Users should always know whether they are in editable `Mês Atual` or in a closed monthly snapshot, and the app should steer them away from accidental changes while they review history.

## UI Contract

- The dashboard month-control area becomes the single app-wide switcher between `Mês Atual` and closed snapshots.
- Closed snapshot selection uses an explicit confirmation step before the app enters the read-only history context.
- When a closed snapshot is active, dashboard, accounts, transactions, and categories surface a visible read-only history state using existing card/badge/banner patterns.
- Mutation entry points that would edit financial data are disabled or redirected back to `Mês Atual`; the app should not present a closed snapshot as if it were editable.
- The visual language remains Flux-native: preserve current spacing, card framing, typography, and responsive behavior.

## Copy Contract

- Distinguish clearly between `Mês Atual` and `Histórico fechado` / `Closed snapshot`.
- Read-only messaging should be practical: explain that the user is viewing preserved history and must return to `Mês Atual` to make changes.
- Confirmation copy for entering snapshot view should mention that the selected month is preserved and that edit controls will be locked.
