# Milestones

## v1.2 Gestão de contas e faturas (Shipped: 2026-04-05)

**Phases completed:** 4 phases, 4 plans, 14 tasks

**Key accomplishments:**

- Reworked `Contas` into a connected-account management surface with persistent renames, per-account bill-import toggles, and no supported manual or wallet account path
- Reworked Pluggy sync to fetch only credit-card bill transactions tied to the active monthly workflow
- Shifted bill import persistence onto a daily automated schedule and made categorization rule-only by default
- Changed the supported dashboard and reports surface to speak in monthly result terms instead of account balance and net worth

**Known debt:**

- `v1.2` was archived without a checked-in `v1.2-MILESTONE-AUDIT.md` artifact, even though the milestone state indicates it passed readiness checks
- Backend automated tests were not executed in the recorded phase summaries because no local `pytest` runtime was available in the shell

---

## v1.0 Flux v2 (Shipped: 2026-04-03)

**Phases completed:** 7 phases, 7 plans, 9 tasks

**Key accomplishments:**

- Flux now appears in the SPA shell title, auth/setup copy, and downloaded backup or transaction export filenames without changing compatibility-facing infrastructure identifiers
- Categories now own the current budget state directly, legacy budget history is flattened to one active budget row per category, and the compatibility budget API keeps the app working against the simplified model
- Category create and edit now include explicit budget enablement and amount controls in one mobile-friendly form, while the budgets page shifts to a read-only companion surface
- Categories now render as a flat management list, budget comparison readers expose only category-owned state, and backup exports declare the v2 compatibility model explicitly
- The public product no longer exposes group-based or standalone budget editing paths, and the remaining user-facing release/setup copy now uses Flux
- The app no longer exposes a standalone Budgets screen, and legacy budget navigation now resolves into Categories
- The shipped category flow is now flat end to end: `/api/categories` no longer exposes `group_id`, and setup no longer creates category groups behind the scenes

---

## v1.1 Gestão Mensal (Shipped: 2026-04-04)

**Phases completed:** 5 phases, 5 plans, 21 tasks

**Key accomplishments:**

- Users must define `Mês Atual` explicitly before syncing or creating manual financial data
- Records now persist durable monthly-period identity across current-month and historical flows
- Closing the active month creates a preserved snapshot and immediately opens the next editable month
- The app now supports explicit closed-history navigation with read-only UI and backend mutation guards
- Closed-history browsing, remaining guard coverage, and dashboard month-selection UX were hardened after the milestone audit

**Known debt:**

- Nyquist validation artifacts are still missing for phases `08-12`

---
