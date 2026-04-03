# Phase 6: Remover a função "Orçamentos" do menu e sua tela também. - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Retire the standalone Budgets surface from the shipped frontend now that category-owned budget management is the supported workflow. Users should manage limits from Categories, and any direct visit to the legacy `/budgets` route should be steered back into that supported flow.

</domain>

<decisions>
## Implementation Decisions

### Navigation and Routing
- Remove the `Budgets` / `Orçamentos` item from the primary navigation.
- Keep the `/budgets` URL from rendering the legacy screen by redirecting it to `/categories` rather than leaving a broken route.

### Supported Budget Flow
- Budget editing and review should continue through Categories and the dashboard/reporting surfaces that already consume category-owned budget data.
- Remove only the dedicated standalone budgets page and strings that exist solely for that page.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/App.tsx` owns the route table and is the single place to retire the standalone budgets screen.
- `frontend/src/components/app-layout.tsx` owns the main sidebar navigation entry for budgets.
- `frontend/src/locales/en.json` and `frontend/src/locales/pt-BR.json` still include labels used only by the retired budgets page.

### Integration Points
- `frontend/src/pages/categories.tsx` remains the active user-facing place for category budget management.
- `frontend/src/pages/dashboard.tsx` and `frontend/src/lib/api.ts` still rely on budget comparison data for analytics, so backend budget readers should remain untouched.

</code_context>

<specifics>
## Specific Ideas

- Preserve existing deep links gracefully by redirecting `/budgets` to `/categories`.
- Prefer a minimal frontend-only change set unless a broken dependency proves otherwise.

</specifics>

<deferred>
## Deferred Ideas

None.

</deferred>
