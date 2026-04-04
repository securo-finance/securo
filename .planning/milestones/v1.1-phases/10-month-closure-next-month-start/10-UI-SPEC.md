# Phase 10 UI Spec: Month Closure and Next Month Start

## Intent

Keep the dashboard as the operational home for `Mês Atual`, while making month closure feel deliberate and safe. Users should understand which month is being closed, what snapshot will be created, and which next month becomes editable immediately after.

## UI Contract

- The `Fechar Mês` action lives inside the existing dashboard month-status/card area rather than in a new admin screen.
- Closing a month uses the current dialog, card, button, and form vocabulary already present in Flux.
- The close flow clearly states the period being closed and requires the next `Mês Atual` competency before submission.
- Success state keeps the user on the dashboard with the newly opened editable month visible immediately.
- Mobile layout remains intact: the close action, helper text, and next-month input must stay usable in narrow widths.

## Copy Contract

- Use `Fechar Mês` and `Mês Atual` terminology consistently in both languages.
- Closure copy should explain that the current month becomes a preserved snapshot and that the user will continue in the next month right away.
- Avoid technical database language such as "foreign key" or "snapshot record" in the user-facing flow.
