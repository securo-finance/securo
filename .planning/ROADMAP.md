# Roadmap: Flux

## Overview

This roadmap delivers Flux v2 as a controlled brownfield migration: establish the Flux brand in user-facing surfaces, migrate category and budget ownership safely, move category budgeting into one responsive flow, cut downstream readers over to the new model, and only then remove legacy group and standalone budget paths.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Flux Branding Foundation** - Establish Flux naming in the product shell and user-facing operational surfaces.
- [ ] **Phase 2: Category Budget Migration** - Migrate existing category and budget data to the category-owned model without breaking links.
- [ ] **Phase 3: Unified Category Budget Editing** - Move category creation and editing to a single mobile-usable flow with inline budget controls.
- [ ] **Phase 4: Flat Categories and Reader Cutover** - Deliver flat category management and migrate budget/report/export readers to category-owned settings.
- [ ] **Phase 5: Legacy Removal and Release Cleanup** - Remove deprecated group and standalone budget paths after all consumers are migrated.

## Phase Details

### Phase 1: Flux Branding Foundation
**Goal**: Users encounter Flux, not Securo, in the primary product shell and user-facing operational labels.
**Depends on**: Nothing (first phase)
**Requirements**: BRND-01, BRND-02
**Success Criteria** (what must be TRUE):
  1. User sees the product name `Flux` in the application shell, navigation, page titles, and primary user-facing copy.
  2. User receives Flux-branded backup/export filenames and related operational labels during normal app use.
  3. The visible product identity stays consistent across the main app surfaces touched in this release.
**Plans**: TBD
**UI hint**: yes

### Phase 2: Category Budget Migration
**Goal**: Existing data is safely moved to the category-owned budget model without losing category relationships or budget meaning.
**Depends on**: Phase 1
**Requirements**: CAT-04, MIG-01
**Success Criteria** (what must be TRUE):
  1. Existing categories, transactions, and rules remain linked correctly after category-group removal work begins.
  2. Previously budgeted categories retain their current effective budget state after migration to the category-owned model.
  3. Users do not need manual data repair to keep working with migrated categories and budgets.
**Plans**: TBD

### Phase 3: Unified Category Budget Editing
**Goal**: Users can create and edit categories, including optional budget settings, in one responsive flow.
**Depends on**: Phase 2
**Requirements**: CAT-01, CAT-02, BUDG-01, BUDG-02, BUDG-03, BUDG-04, MOB-01, MOB-02
**Success Criteria** (what must be TRUE):
  1. User can create a category without assigning it to a category group.
  2. User can choose during category creation whether the category has a budget and enter a budget amount when enabled.
  3. User can edit an existing category and change budget enablement or budget amount from the same category flow.
  4. User can leave a category explicitly unbudgeted without the system treating that state as a zero-value budget.
  5. Category create/edit works on mobile-width screens and does not depend on hover-only, drag-only, or desktop-only interactions.
**Plans**: TBD
**UI hint**: yes

### Phase 4: Flat Categories and Reader Cutover
**Goal**: Users manage categories in a flat mobile-usable list while budget, reporting, and export flows read from category-owned settings consistently.
**Depends on**: Phase 3
**Requirements**: CAT-03, BUDG-05, MIG-02, MIG-03
**Success Criteria** (what must be TRUE):
  1. User can view and manage categories in a flat list that remains usable on mobile screens.
  2. Budget-related screens and APIs read category-owned budget settings consistently instead of requiring separate standalone budget ownership.
  3. Historical transactions, reports, and budget-vs-actual views remain accurate for existing data after the migration.
  4. Existing backups/exports remain recoverable during the v2 transition through an explicit compatibility or versioning strategy.
**Plans**: TBD
**UI hint**: yes

### Phase 5: Legacy Removal and Release Cleanup
**Goal**: Flux v2 ships with consistent release/setup naming and no active dependence on legacy category-group or standalone budget paths.
**Depends on**: Phase 4
**Requirements**: BRND-03, MIG-04
**Success Criteria** (what must be TRUE):
  1. Self-hosted setup and project documentation for this release use Flux naming consistently where compatibility does not require legacy identifiers.
  2. All active consumers have been migrated before legacy category-group and standalone budget paths are removed.
  3. Users reach the supported category and budget workflows without encountering deprecated group-based or standalone-budget editing paths.
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Flux Branding Foundation | 0/TBD | Not started | - |
| 2. Category Budget Migration | 0/TBD | Not started | - |
| 3. Unified Category Budget Editing | 0/TBD | Not started | - |
| 4. Flat Categories and Reader Cutover | 0/TBD | Not started | - |
| 5. Legacy Removal and Release Cleanup | 0/TBD | Not started | - |
