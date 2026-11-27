# 0034. Plugins add dashboard widgets: a report sized for a card, over the month on screen

- Status: Accepted
- Date: 2026-11-23
- Origin: plugin widgets in the dashboard library
- Extends: [0028](0028-plugin-reports.md)

## Context

The dashboard is a library like the reports page: someone places widgets on a 12-column grid, in their order and at their widths (`useLayout('dashboard')`, `frontend/src/lib/library.ts`). Plugins could add reports (ADR 0028) but nothing to the dashboard, which is where most people look. A widget needs what a report has (a query or the plugin's own code, drawn by Securo) plus what a card needs: a starting width, and a period that follows the month the dashboard shows.

## Decision

- A manifest declares widgets under `contributes.widgets` (`Widget` in `backend/app/plugins/manifest.py`). A widget is a report with two more fields, so everything ADR 0028 says about query and code reports holds for it, rules and limits included:
  - `width`: `third`, `half` (the default), `two_thirds` or `full`, the columns it starts at until someone resizes it;
  - `period`: what it reads, counted back from the month on screen: `month` (the default), `year_to_date` or `last_12_months`. A query widget split by month needs more than one month.
- Widgets and reports are separate lists with separate keys, each looked up on its own surface: a widget is never a report tab, nor a report a widget.
- `GET /api/dashboard/plugins/{owner}/{name}/{key}` (`get_plugin_widget` in `backend/app/api/dashboard.py`) answers a widget as the report endpoint answers a report (`plugin_view_response` in `backend/app/api/reports.py`): the same 404, 422, 502 and 503, the same read by anyone who can see the workspace, the same `reports:read` for an access token, since the dashboard is under that scope. A code widget runs the plugin's `widget(params, context)` method, with `params["widget"]` naming it.
- The frontend lists the installed plugins' widgets in the dashboard library under `plugin:<owner/name>:<key>` (`pluginWidgetEntries`). A placed one is drawn by `PluginWidget` (`frontend/src/components/dashboard-widgets.tsx`) for the month and the accounts in view, inside the widget's card. A query widget shows its total and its chart, and its table only when the table is the chart. A code widget's view is drawn with `ReportView` without each block's frame, since the card is the frame.
- `ReportView` lays blocks out by the width of the space it has (container queries), not the screen's, so the same view stacks inside a narrow card and spreads across the reports page.

## Consequences

- A plugin can put its own figure or chart on the dashboard without any of its code reaching the interface.
- A code widget runs the plugin each time the dashboard loads it or its data changes, as a code report does when opened: a dashboard of several code widgets makes several runner calls.
- Removing the plugin takes its widgets out of the library; a placed one is left out of the dashboard, and the saved arrangement keeps it until the plugins are known to be read, so a failed read never drops it.

## Enforced by

`backend/tests/test_widget_plugins.py`: the manifest's rules (width, period, keys apart from reports), the listing, a query widget agreeing with the report it mirrors, a code widget run through the real runner by its `widget` method, and the 404, 422, 502 and 503 answers. `frontend/src/components/dashboard-widgets.test.tsx`, `frontend/src/components/library-dialog.test.tsx`, `frontend/src/pages/dashboard.test.tsx`, `frontend/src/lib/library.test.ts`, `frontend/src/lib/plugin-report-view.test.ts` and `frontend/src/components/reports/ReportView.test.tsx`: the period's dates, the card's content, adding a widget from the library, a placed widget drawn and one kept while the plugins can't be read.
