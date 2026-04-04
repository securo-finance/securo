# Phase 8 UI Spec: Current Month Setup and Guards

## Intent

Preserve the current Flux shell and page layout while making the active monthly competency explicit. Users should understand when `Mês Atual` is undefined, how to define it, and why sync or manual entry actions are blocked until that setup is complete.

## UI Contract

- The dashboard remains the primary place to expose current-month status and month setup, using the existing header/card layout rather than a new standalone flow.
- Any month-status surface uses existing card, badge, button, and dialog patterns already present in Flux pages.
- The undefined-period state is visually explicit and paired with a clear primary action to define `Mês Atual`.
- Transactions, accounts, categories, and sync entry points show blocked states inline using the existing page structure instead of redirecting to a separate setup screen.
- Mobile behavior remains first-class: blocked actions and month setup controls must be reachable and understandable without hover-only affordances.

## Copy Contract

- Add concise bilingual copy explaining that `Mês Atual` must be defined before sync or manual financial entry is allowed.
- Use the `Mês Atual` terminology consistently across dashboard, transactions, accounts, categories, and any blocking dialogs or banners.
- Keep guard messaging practical and action-oriented: explain what is blocked and what the user must do next.
