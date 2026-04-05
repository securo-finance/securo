# Phase 3: Scheduled Bill Persistence & Categorization Baseline - Context

**Gathered:** 2026-04-05
**Status:** Executed autonomously
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

Imported bill rows should be persisted into Flux-owned month storage on an automatic daily cadence, and they must default to uncategorized unless a saved rule matches.

</domain>

<decisions>
## Implementation Decisions

### Daily sync uses the existing Celery/beat pipeline
The app already had background connection sync infrastructure. This phase narrows it to a daily scheduled pass across active/error connections rather than introducing a second scheduler.

### Imported bill rows belong to the active monthly period
Bill transactions are attached to the resolved `Mês Atual` monthly-period record instead of being bucketed by the raw provider transaction date month.

### Provider categories are no longer auto-mapped
Imported bill rows start uncategorized. Existing saved rules are still applied immediately after insert so category assignment remains deterministic and Flux-owned.

</decisions>

<code_context>
## Existing Code Insights

- Background connection sync already existed through Celery beat and `app.tasks.sync_tasks`.
- Sync imports were still auto-mapping Pluggy categories and assigning monthly periods by transaction date.

</code_context>
