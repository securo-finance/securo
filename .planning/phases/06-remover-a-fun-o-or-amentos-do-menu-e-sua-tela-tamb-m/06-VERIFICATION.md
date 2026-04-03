---
phase: 06-remover-a-fun-o-or-amentos-do-menu-e-sua-tela-tamb-m
verified: 2026-04-03T19:06:13Z
status: passed
score: 3/3 must-haves verified
---

# Phase 6: Remover a função "Orçamentos" do menu e sua tela também. Verification

## Goal-Backward Verification

**Phase Goal:** Users no longer see a standalone Budgets area in navigation and cannot reach the retired budgets screen through the supported app UI.

## Checks

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 1 | Users no longer see a Budgets entry in primary navigation | ✓ VERIFIED | `frontend/src/components/app-layout.tsx` no longer includes the `budgets` nav item. |
| 2 | `/budgets` no longer renders the retired standalone screen | ✓ VERIFIED | `frontend/src/App.tsx` now redirects `/budgets` to `/categories`, and `frontend/src/pages/budgets.tsx` has been removed. |
| 3 | Supported budget workflows remain available without dead UI references | ✓ VERIFIED | `frontend/src/locales/en.json` and `frontend/src/locales/pt-BR.json` retain shared budget terminology for active screens while removing page-only strings, and the frontend production build succeeded. |

## Result

Phase 6 passed verification. `npm --prefix frontend run build` completed successfully.
