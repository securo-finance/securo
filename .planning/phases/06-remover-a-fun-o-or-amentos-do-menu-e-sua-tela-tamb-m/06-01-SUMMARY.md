---
phase: 06-remover-a-fun-o-or-amentos-do-menu-e-sua-tela-tamb-m
plan: 01
subsystem: frontend
tags:
  - navigation
  - routing
  - cleanup
provides:
  - No standalone budgets navigation entry
  - Legacy budgets route redirects into categories
  - Removed dedicated budgets page implementation
affects: []
tech-stack:
  added: []
  patterns:
    - Retired routes redirect to the supported replacement flow instead of failing hard
key-files:
  created:
    - .planning/phases/06-remover-a-fun-o-or-amentos-do-menu-e-sua-tela-tamb-m/06-CONTEXT.md
    - .planning/phases/06-remover-a-fun-o-or-amentos-do-menu-e-sua-tela-tamb-m/06-01-PLAN.md
  modified:
    - frontend/src/App.tsx
    - frontend/src/components/app-layout.tsx
    - frontend/src/locales/en.json
    - frontend/src/locales/pt-BR.json
  deleted:
    - frontend/src/pages/budgets.tsx
key-decisions:
  - Preserve `/budgets` as a redirect target to `/categories` for compatibility
  - Limit the phase to frontend retirement so shared budget analytics stay intact
patterns-established:
  - Deprecated standalone UI surfaces should redirect to the canonical replacement flow
requirements-completed:
  - none
duration: "10min"
completed: 2026-04-03
---

# Phase 6: Retire the Standalone Budgets Screen Summary

**The app no longer exposes a standalone Budgets screen, and legacy budget navigation now resolves into Categories**

## Accomplishments

- Removed the `Budgets` / `Orçamentos` entry from the primary sidebar navigation.
- Replaced the standalone `/budgets` page route with a redirect to `/categories`.
- Deleted the obsolete budgets page component and trimmed page-only locale strings.

## Completion Note

Budget management remains available through Categories and existing analytical readers, but users are no longer sent through a separate standalone budgets area.
