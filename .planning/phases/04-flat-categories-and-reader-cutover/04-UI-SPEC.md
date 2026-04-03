# Phase 4 UI Spec: Flat Categories and Reader Cutover

## Intent

Keep the existing Flux visual language, but remove the grouped browsing pattern from category management so the page reads as one direct list of editable categories.

## UI Contract

- The categories page shows one flat list of categories inside the existing section card.
- The primary action remains `Add Category`.
- Group collapse, expand, and group editing controls are removed from the visible flow.
- Each category row still shows icon, name, budget state, and lightweight supporting metadata.
- The page remains tap-friendly on mobile and does not depend on hover or nested disclosure.

## Copy Contract

- Add helper copy explaining that categories are managed in one flat list.
- Preserve bilingual support in `frontend/src/locales/en.json` and `frontend/src/locales/pt-BR.json`.
