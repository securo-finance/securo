# Phase 1: Flux Branding Foundation - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Rebrand the visible product shell from `Securo` to `Flux` across the current SPA shell, authentication/setup entry points, browser title, and user-facing export or backup labels that appear during normal product use. Preserve the existing visual system and avoid changing package names, database identifiers, container names, or broader self-hosted documentation in this phase.

</domain>

<decisions>
## Implementation Decisions

### Product Identity
- Replace user-facing product-name copy with `Flux` in the application shell, auth/setup surfaces, and localized welcome copy.
- Keep the existing logo mark and visual language for this phase; the branding change is name-first, not a full visual redesign.
- Update existing translation keys and browser metadata in place instead of introducing a second branding system.
- Treat package names, image names, compose project names, and database identifiers as compatibility identifiers for later release-cleanup work.

### Operational Labels
- Rename downloaded backup filenames from `securo-backup-*` to `flux-backup-*`.
- Rename downloaded transaction export filenames from `transactions-*` to `flux-transactions-*` so routine exports also carry the new product identity.
- Keep API routes stable in this phase; only the user-facing labels and downloaded filenames need to change.
- Preserve existing success and error toast flows while keeping copy aligned with the Flux name.

### the agent's Discretion
- If additional user-facing `Securo` strings are found in the touched product shell, update them to `Flux` as long as the change does not alter release/setup compatibility identifiers or widen scope into documentation cleanup.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/locales/en.json` and `frontend/src/locales/pt-BR.json` already centralize the shell brand string via `app.name` and setup/auth copy.
- `frontend/src/components/app-layout.tsx`, `frontend/src/pages/login.tsx`, `frontend/src/pages/register.tsx`, and `frontend/src/pages/setup.tsx` already read `t('app.name')`, so translation changes will propagate through the shell.
- `frontend/src/lib/api.ts` controls the client-side transaction export and backup download filenames.

### Established Patterns
- The frontend is a React SPA using i18n translation keys for user-facing copy.
- Visual styling is already defined through the current token set in `frontend/src/index.css`; the phase should preserve that system.
- Backend download responses use `Content-Disposition` filenames and frontend download helpers mirror those names for browser downloads.

### Integration Points
- `frontend/index.html` sets the base browser title.
- `frontend/src/locales/*.json` provide visible product-name copy across the shell and onboarding/auth flows.
- `frontend/src/lib/api.ts` and `backend/app/api/export.py` / `backend/app/api/transactions.py` define downloaded backup/export filenames.

</code_context>

<specifics>
## Specific Ideas

No specific visual redesign requests. Preserve the current responsive shell and simply replace visible Securo naming with Flux where users encounter it in the product.

</specifics>

<deferred>
## Deferred Ideas

- Rename container images, compose project names, package metadata, and backend defaults during the release-cleanup phase.
- Revisit the logo mark only if a later phase requires broader brand-system work.

</deferred>
