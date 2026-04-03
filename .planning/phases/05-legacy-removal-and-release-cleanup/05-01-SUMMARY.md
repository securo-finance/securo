---
phase: 05-legacy-removal-and-release-cleanup
plan: 01
subsystem: cleanup
tags:
  - migration
  - api
  - docs
  - branding
provides:
  - No public category-group route exposure
  - No standalone budget CRUD exposure
  - Flux-aligned user-facing setup/docs copy
affects: []
tech-stack:
  added: []
  patterns:
    - Category forms own budget editing; budget routes are reader-only
key-files:
  created:
    - .planning/phases/05-legacy-removal-and-release-cleanup/05-CONTEXT.md
    - .planning/phases/05-legacy-removal-and-release-cleanup/05-01-PLAN.md
  modified:
    - frontend/src/pages/categories.tsx
    - frontend/src/pages/budgets.tsx
    - frontend/src/lib/api.ts
    - backend/app/api/budgets.py
    - backend/app/main.py
    - backend/tests/test_budgets_api.py
    - README.md
    - CONTRIBUTING.md
    - SECURITY.md
    - install.sh
    - backend/app/core/config.py
    - backend/pyproject.toml
key-decisions:
  - Keep repository/container identifiers where they still serve compatibility
  - Remove only the public legacy paths that users could still reach
patterns-established:
  - Budget editing belongs exclusively to categories; budget routes are analytical readers
requirements-completed:
  - BRND-03
  - MIG-04
duration: "40min"
completed: 2026-04-03
---

# Phase 5: Remove Public Legacy Paths and Align Release Copy Summary

**The public product no longer exposes group-based or standalone budget editing paths, and the remaining user-facing release/setup copy now uses Flux**

## Accomplishments

- Removed frontend dependence on category-group APIs and standalone budget listing/editing.
- Reduced the public budgets API surface to the comparison reader and stopped exposing category-group routes.
- Updated user-facing setup and documentation copy from Securo to Flux within the compatibility boundary.

## Completion Note

With Phase 5 complete, the roadmap milestone is functionally delivered: category-owned budgets are the supported model, reader contracts are migrated, and legacy public edit paths are no longer part of the shipped workflow.
