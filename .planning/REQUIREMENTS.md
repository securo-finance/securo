# Requirements: Flux

**Defined:** 2026-04-04
**Core Value:** Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## v1 Requirements

### Connected Accounts

- [ ] **CONN-01**: User can view `Contas` as a list of Pluggy-connected accounts only.
- [ ] **CONN-02**: User can rename a connected account without breaking its Pluggy association.
- [ ] **CONN-03**: User can enable or disable bill import for a connected account.
- [ ] **CONN-04**: User can delete a connected account connection from Flux.

### Bill Import

- [ ] **BILL-01**: User syncs only credit-card bill data from Pluggy instead of all account transactions.
- [ ] **BILL-02**: User has current-month bill imports fetched from `GET /accounts/{accountId}/bills` using `dueDate` filtered to the month after `Mês Atual`.
- [ ] **BILL-03**: User has enabled connected accounts checked for new bill data automatically on a daily schedule.
- [ ] **BILL-04**: User has imported bill transactions stored in app-owned persistence associated with the source card and `Mês Atual`.
- [ ] **BILL-05**: User sees imported bill transactions created without a category unless a saved categorization rule matches.

### Financial Model

- [ ] **MODEL-01**: User no longer manages wallets anywhere in the supported runtime.
- [ ] **MODEL-02**: User no longer sees account balances as part of account management or monthly calculations.
- [ ] **MODEL-03**: User has the monthly financial result derived from `receitas - despesas` within the active month context.

## v2 Requirements

### Broader Pluggy Coverage

- **SYNC-01**: User can import non-card banking transactions from Pluggy.
- **SYNC-02**: User can choose different sync scopes per institution or account type.

### Rules and Assistance

- **RULE-01**: User can create and edit categorization rules from imported bill transaction flows.
- **RULE-02**: User can review why an imported bill transaction matched a categorization rule.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Wallets as a supported bookkeeping surface | Conflicts with the milestone goal of simplifying the model around monthly totals. |
| Per-account balance tracking | Conflicts with the milestone goal that the month result is `receitas - despesas`. |
| Importing all Pluggy transactions | This milestone is intentionally limited to credit-card bills only. |
| Broad redesign of navigation or visual language | The milestone changes behavior and data flow, not product styling. |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONN-01 | Phase 1 | Pending |
| CONN-02 | Phase 1 | Pending |
| CONN-03 | Phase 1 | Pending |
| CONN-04 | Phase 1 | Pending |
| MODEL-01 | Phase 1 | Pending |
| MODEL-02 | Phase 1 | Pending |
| BILL-01 | Phase 2 | Pending |
| BILL-02 | Phase 2 | Pending |
| BILL-03 | Phase 3 | Pending |
| BILL-04 | Phase 3 | Pending |
| BILL-05 | Phase 3 | Pending |
| MODEL-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0

---
*Requirements defined: 2026-04-04*
*Last updated: 2026-04-04 after milestone v1.2 definition*
