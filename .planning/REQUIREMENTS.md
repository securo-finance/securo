# Requirements: Flux

**Defined:** 2026-04-03
**Core Value:** Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Branding

- [ ] **BRND-01**: User sees the product name `Flux` instead of `Securo` in the application shell, navigation, page titles, and primary user-facing copy
- [ ] **BRND-02**: User receives Flux-branded backup/export filenames and related user-facing operational labels
- [ ] **BRND-03**: Self-hosted setup and project documentation for this release use Flux naming consistently where compatibility does not require legacy identifiers

### Categories

- [ ] **CAT-01**: User can create a category without assigning it to a category group
- [ ] **CAT-02**: User can edit an existing category without any category-group dependency in the UI or API contract
- [ ] **CAT-03**: User can view and manage categories in a flat list that remains usable on mobile screens
- [ ] **CAT-04**: Existing categories, transactions, and rules remain linked correctly after category-group removal

### Budgeting

- [ ] **BUDG-01**: User can choose whether a category has a budget during category creation
- [ ] **BUDG-02**: User can enter a budget amount when budgeting is enabled for a category
- [ ] **BUDG-03**: User can disable budgeting for a category without treating that state as a zero-value budget
- [ ] **BUDG-04**: User can edit a category and change its budget enablement and budget amount from the same category flow
- [ ] **BUDG-05**: Budget-related screens and APIs read category-owned budget settings consistently instead of requiring separate standalone budget CRUD ownership

### Migration and Reporting

- [ ] **MIG-01**: Existing budgeted categories are migrated to the new category-owned budget model without losing their current effective budget state
- [ ] **MIG-02**: Historical transactions, reports, and budget-vs-actual views remain accurate for existing data after the migration
- [ ] **MIG-03**: Existing backups/exports remain recoverable through an explicit compatibility or versioning strategy during the v2 transition
- [ ] **MIG-04**: Legacy category-group and standalone budget paths are removed only after all active consumers have been migrated

### Mobile UX

- [ ] **MOB-01**: Category create/edit flows remain fully usable on mobile-width screens with budget controls included in the same responsive flow
- [ ] **MOB-02**: New v2 category and budget interactions do not depend on hover-only, drag-only, or desktop-only UI patterns

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Category Experience

- **CATX-01**: User can archive or hide categories to improve flat-list organization
- **CATX-02**: User can reorder categories with explicit mobile-friendly controls

### Budget Intelligence

- **BINT-01**: User receives suggested budget amounts based on recent spending history
- **BINT-02**: User can save a categorization rule directly after recategorizing a transaction from a budget-related workflow
- **BINT-03**: User can move money between categories with a dedicated reallocation workflow

## Out of Scope

| Feature | Reason |
|---------|--------|
| Native or hybrid mobile app | v2 remains the current responsive web application |
| Broad visual redesign | Current design language should be preserved |
| Reintroducing groups through a renamed or hidden grouping layer | Conflicts with the simplification goal of the v2 |
| Forcing every category to have a budget | Categories must support an explicit non-budgeted state |
| Long-term dual ownership between categories and standalone budgets | Categories become the source of truth for budget editing in v2 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BRND-01 | TBD | Pending |
| BRND-02 | TBD | Pending |
| BRND-03 | TBD | Pending |
| CAT-01 | TBD | Pending |
| CAT-02 | TBD | Pending |
| CAT-03 | TBD | Pending |
| CAT-04 | TBD | Pending |
| BUDG-01 | TBD | Pending |
| BUDG-02 | TBD | Pending |
| BUDG-03 | TBD | Pending |
| BUDG-04 | TBD | Pending |
| BUDG-05 | TBD | Pending |
| MIG-01 | TBD | Pending |
| MIG-02 | TBD | Pending |
| MIG-03 | TBD | Pending |
| MIG-04 | TBD | Pending |
| MOB-01 | TBD | Pending |
| MOB-02 | TBD | Pending |

**Coverage:**
- v1 requirements: 18 total
- Mapped to phases: 0
- Unmapped: 18 ⚠️

---
*Requirements defined: 2026-04-03*
*Last updated: 2026-04-03 after initial definition*
